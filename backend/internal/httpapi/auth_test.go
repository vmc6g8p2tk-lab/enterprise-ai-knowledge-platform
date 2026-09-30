package httpapi

import (
	"encoding/base64"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/VMC6G8P2TK-Lab/enterprise-ai-knowledge-platform/backend/internal/config"
)

func TestSignedTokenValidation(t *testing.T) {
	a := &API{cfg: config.Config{JWTSecret: strings.Repeat("s", 48)}}
	id := "8e6b2de8-079d-4f6d-bcb7-73068cc05a40"
	token, err := a.signToken(id)
	if err != nil {
		t.Fatal(err)
	}
	verified, err := a.verifyToken(token)
	if err != nil || verified != id {
		t.Fatalf("valid token: %q %v", verified, err)
	}
	parts := strings.Split(token, ".")
	parts[0] = base64.RawURLEncoding.EncodeToString([]byte(`{"alg":"none","typ":"JWT"}`))
	if _, err = a.verifyToken(strings.Join(parts, ".")); err == nil {
		t.Fatal("accepted algorithm substitution")
	}
	parts = strings.Split(token, ".")
	parts[1] = base64.RawURLEncoding.EncodeToString([]byte(`{"sub":"8e6b2de8-079d-4f6d-bcb7-73068cc05a40","exp":9999999999}`))
	if _, err = a.verifyToken(strings.Join(parts, ".")); err == nil {
		t.Fatal("accepted payload tampering")
	}
	other := &API{cfg: config.Config{JWTSecret: strings.Repeat("x", 48)}}
	if _, err = other.verifyToken(token); err == nil {
		t.Fatal("accepted wrong secret")
	}
	if _, err = a.verifyToken(token + strings.Repeat("x", 5000)); err == nil {
		t.Fatal("accepted oversized token")
	}
}

func TestRoleHierarchy(t *testing.T) {
	if !(roleLevel("admin") > roleLevel("editor") && roleLevel("editor") > roleLevel("viewer")) {
		t.Fatal("role order")
	}
	if roleLevel("unknown") != 0 {
		t.Fatal("unknown role accepted")
	}
}

func TestUnauthenticatedRequestReturns401(t *testing.T) {
	handler := New(nil, config.Config{JWTSecret: strings.Repeat("s", 48)})
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/knowledge-bases", nil))
	if response.Code != 401 {
		t.Fatalf("status=%d", response.Code)
	}
}
