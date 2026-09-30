package httpapi

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/http"
	"net/mail"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"golang.org/x/crypto/bcrypt"
)

type userKey struct{}

type tokenClaims struct {
	Subject   string `json:"sub"`
	Issuer    string `json:"iss"`
	Audience  string `json:"aud"`
	IssuedAt  int64  `json:"iat"`
	ExpiresAt int64  `json:"exp"`
}

func (a *API) register(w http.ResponseWriter, r *http.Request) {
	var input struct {
		Email    string `json:"email"`
		Password string `json:"password"`
	}
	if err := decodeJSON(w, r, &input); err != nil {
		writeError(w, 400, err.Error())
		return
	}
	input.Email = strings.ToLower(strings.TrimSpace(input.Email))
	address, err := mail.ParseAddress(input.Email)
	if err != nil || address.Address != input.Email || len(input.Email) > 254 ||
		len(input.Password) < 12 || len(input.Password) > 72 {
		writeError(w, 400, "valid email and 12-72 byte password required")
		return
	}
	hash, err := bcrypt.GenerateFromPassword([]byte(input.Password), 12)
	if err != nil {
		writeError(w, 500, "unable to hash password")
		return
	}
	id, err := newUUID()
	if err != nil {
		writeError(w, 500, "unable to create id")
		return
	}
	_, err = a.db.Exec(r.Context(), "INSERT INTO users(id,email,password_hash) VALUES($1,$2,$3)", id, input.Email, string(hash))
	if err != nil {
		var databaseError *pgconn.PgError
		if errors.As(err, &databaseError) && databaseError.Code == "23505" {
			writeError(w, 409, "email already registered")
		} else {
			writeError(w, 500, "database error")
		}
		return
	}
	writeJSON(w, 201, map[string]string{"id": id, "email": input.Email})
}

func (a *API) login(w http.ResponseWriter, r *http.Request) {
	var input struct {
		Email    string `json:"email"`
		Password string `json:"password"`
	}
	if err := decodeJSON(w, r, &input); err != nil {
		writeError(w, 400, err.Error())
		return
	}
	var id, hash string
	err := a.db.QueryRow(r.Context(), "SELECT id,password_hash FROM users WHERE lower(email)=lower($1)", strings.TrimSpace(input.Email)).Scan(&id, &hash)
	if err != nil || bcrypt.CompareHashAndPassword([]byte(hash), []byte(input.Password)) != nil {
		writeError(w, 401, "invalid credentials")
		return
	}
	token, err := a.signToken(id)
	if err != nil {
		writeError(w, 500, "unable to issue token")
		return
	}
	writeJSON(w, 200, map[string]any{"access_token": token, "token_type": "Bearer", "expires_in": 7200, "user_id": id})
}

func (a *API) signToken(userID string) (string, error) {
	header := base64.RawURLEncoding.EncodeToString([]byte(`{"alg":"HS256","typ":"JWT"}`))
	now := time.Now().Unix()
	data, err := json.Marshal(tokenClaims{userID, "enterprise-ai-knowledge-platform", "enterprise-ai-api", now, now + 7200})
	if err != nil {
		return "", err
	}
	body := base64.RawURLEncoding.EncodeToString(data)
	message := header + "." + body
	mac := hmac.New(sha256.New, []byte(a.cfg.JWTSecret))
	_, _ = mac.Write([]byte(message))
	return message + "." + base64.RawURLEncoding.EncodeToString(mac.Sum(nil)), nil
}

func (a *API) verifyToken(token string) (string, error) {
	if len(token) > 4096 {
		return "", errors.New("token too large")
	}
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return "", errors.New("malformed token")
	}
	header, err := base64.RawURLEncoding.DecodeString(parts[0])
	if err != nil || string(header) != `{"alg":"HS256","typ":"JWT"}` {
		return "", errors.New("invalid algorithm")
	}
	message := parts[0] + "." + parts[1]
	mac := hmac.New(sha256.New, []byte(a.cfg.JWTSecret))
	_, _ = mac.Write([]byte(message))
	signature, err := base64.RawURLEncoding.DecodeString(parts[2])
	if err != nil || !hmac.Equal(signature, mac.Sum(nil)) {
		return "", errors.New("invalid signature")
	}
	data, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return "", errors.New("invalid payload")
	}
	var claims tokenClaims
	if json.Unmarshal(data, &claims) != nil || !validUUID(claims.Subject) ||
		claims.Issuer != "enterprise-ai-knowledge-platform" || claims.Audience != "enterprise-ai-api" ||
		claims.IssuedAt > time.Now().Unix()+30 || claims.ExpiresAt <= time.Now().Unix() ||
		claims.ExpiresAt-claims.IssuedAt > 7200 || claims.IssuedAt <= 0 {
		return "", errors.New("invalid claims")
	}
	return claims.Subject, nil
}

func (a *API) authenticate(w http.ResponseWriter, r *http.Request) *http.Request {
	header := r.Header.Get("Authorization")
	if !strings.HasPrefix(header, "Bearer ") {
		writeError(w, 401, "authentication required")
		return nil
	}
	id, err := a.verifyToken(strings.TrimPrefix(header, "Bearer "))
	if err != nil {
		writeError(w, 401, "invalid token")
		return nil
	}
	// Deleting a user revokes their account even before the short token expires.
	var exists bool
	if err := a.db.QueryRow(r.Context(), "SELECT EXISTS(SELECT 1 FROM users WHERE id=$1)", id).Scan(&exists); err != nil {
		writeError(w, 500, "database error")
		return nil
	}
	if !exists {
		writeError(w, 401, "invalid token")
		return nil
	}
	return r.WithContext(context.WithValue(r.Context(), userKey{}, id))
}

func userID(r *http.Request) string { id, _ := r.Context().Value(userKey{}).(string); return id }

func roleLevel(role string) int {
	switch role {
	case "admin":
		return 3
	case "editor":
		return 2
	case "viewer":
		return 1
	}
	return 0
}

func (a *API) authorizeKB(w http.ResponseWriter, r *http.Request, kbID, minimum string) bool {
	if !validUUID(kbID) {
		writeError(w, 400, "invalid knowledge base id")
		return false
	}
	var role string
	err := a.db.QueryRow(r.Context(),
		"SELECT role FROM knowledge_base_members WHERE knowledge_base_id=$1 AND user_id=$2", kbID, userID(r)).Scan(&role)
	if errors.Is(err, pgx.ErrNoRows) {
		writeError(w, 404, "knowledge base not found")
		return false
	}
	if err != nil {
		writeError(w, 500, "database error")
		return false
	}
	if roleLevel(role) < roleLevel(minimum) {
		writeError(w, 403, "insufficient permission")
		return false
	}
	return true
}
