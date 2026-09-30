package httpapi

import (
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/VMC6G8P2TK-Lab/enterprise-ai-knowledge-platform/backend/internal/config"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

const maxUploadBytes = 10 << 20

var uuidPattern = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)

type API struct {
	db          *pgxpool.Pool
	cfg         config.Config
	ai          *http.Client
	metricsData *httpMetrics
}
type KnowledgeBase struct {
	ID          string    `json:"id"`
	Name        string    `json:"name"`
	Description string    `json:"description"`
	CreatedAt   time.Time `json:"created_at"`
}
type Document struct {
	ID               string    `json:"id"`
	KnowledgeBaseID  string    `json:"knowledge_base_id"`
	OriginalFilename string    `json:"original_filename"`
	MediaType        string    `json:"media_type"`
	Status           string    `json:"status"`
	ChunkCount       int       `json:"chunk_count"`
	FailureReason    string    `json:"failure_reason"`
	CreatedAt        time.Time `json:"created_at"`
	UpdatedAt        time.Time `json:"updated_at"`
}

func New(db *pgxpool.Pool, cfg config.Config) http.Handler {
	a := &API{db: db, cfg: cfg, ai: &http.Client{Timeout: 5 * time.Minute}, metricsData: newHTTPMetrics()}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", a.health)
	mux.HandleFunc("GET /metrics", a.metrics)
	mux.HandleFunc("POST /auth/register", a.register)
	mux.HandleFunc("POST /auth/login", a.login)
	mux.HandleFunc("GET /knowledge-bases", a.listKnowledgeBases)
	mux.HandleFunc("POST /knowledge-bases", a.createKnowledgeBase)
	mux.HandleFunc("GET /knowledge-bases/{id}", a.getKnowledgeBase)
	mux.HandleFunc("DELETE /knowledge-bases/{id}", a.deleteKnowledgeBase)
	mux.HandleFunc("GET /knowledge-bases/{id}/members", a.listMembers)
	mux.HandleFunc("PUT /knowledge-bases/{id}/members/{userID}", a.putMember)
	mux.HandleFunc("GET /knowledge-bases/{id}/documents", a.listDocuments)
	mux.HandleFunc("POST /knowledge-bases/{id}/documents", a.uploadDocument)
	mux.HandleFunc("DELETE /knowledge-bases/{id}/documents/{documentID}", a.deleteDocument)
	mux.HandleFunc("POST /knowledge-bases/{id}/chat", a.chat)
	mux.HandleFunc("POST /knowledge-bases/{id}/agent", a.agent)
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		started := time.Now()
		method, path := r.Method, r.URL.Path
		recorded := &responseRecorder{ResponseWriter: w}
		defer func() {
			status := recorded.status
			if status == 0 {
				status = 200
			}
			a.metricsData.observe(method, path, status, time.Since(started))
		}()
		w = recorded
		w.Header().Set("Access-Control-Allow-Origin", cfg.CORSOrigin)
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization")
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		if r.URL.Path != "/healthz" && r.URL.Path != "/metrics" && r.URL.Path != "/auth/register" && r.URL.Path != "/auth/login" {
			r = a.authenticate(w, r)
			if r == nil {
				return
			}
		}
		mux.ServeHTTP(w, r)
	})
}

func (a *API) health(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
	defer cancel()
	if err := a.db.Ping(ctx); err != nil {
		writeError(w, http.StatusServiceUnavailable, "database unavailable")
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func (a *API) createKnowledgeBase(w http.ResponseWriter, r *http.Request) {
	var input struct {
		Name        string `json:"name"`
		Description string `json:"description"`
	}
	if err := decodeJSON(w, r, &input); err != nil {
		writeError(w, 400, err.Error())
		return
	}
	input.Name = strings.TrimSpace(input.Name)
	if len(input.Name) == 0 || len(input.Name) > 120 || len(input.Description) > 2000 {
		writeError(w, 400, "invalid name or description")
		return
	}
	id, err := newUUID()
	if err != nil {
		writeError(w, 500, "unable to create id")
		return
	}
	tx, err := a.db.Begin(r.Context())
	if err != nil {
		writeError(w, 500, "database error")
		return
	}
	defer tx.Rollback(r.Context())
	var kb KnowledgeBase
	err = tx.QueryRow(r.Context(), "INSERT INTO knowledge_bases(id,name,description) VALUES($1,$2,$3) RETURNING id,name,description,created_at", id, input.Name, input.Description).Scan(&kb.ID, &kb.Name, &kb.Description, &kb.CreatedAt)
	if err == nil {
		_, err = tx.Exec(r.Context(), "INSERT INTO knowledge_base_members(knowledge_base_id,user_id,role) VALUES($1,$2,'admin')", id, userID(r))
	}
	if err == nil {
		err = tx.Commit(r.Context())
	}
	if err != nil {
		writeError(w, 500, "database error")
		return
	}
	writeJSON(w, 201, kb)
}

func (a *API) listKnowledgeBases(w http.ResponseWriter, r *http.Request) {
	rows, err := a.db.Query(r.Context(), `SELECT k.id,k.name,k.description,k.created_at FROM knowledge_bases k
		JOIN knowledge_base_members m ON m.knowledge_base_id=k.id WHERE m.user_id=$1 ORDER BY k.created_at DESC LIMIT 100`, userID(r))
	if err != nil {
		writeError(w, 500, "database error")
		return
	}
	defer rows.Close()
	items := make([]KnowledgeBase, 0)
	for rows.Next() {
		var kb KnowledgeBase
		if err := rows.Scan(&kb.ID, &kb.Name, &kb.Description, &kb.CreatedAt); err != nil {
			writeError(w, 500, "database error")
			return
		}
		items = append(items, kb)
	}
	if err := rows.Err(); err != nil {
		writeError(w, 500, "database error")
		return
	}
	writeJSON(w, 200, items)
}

func (a *API) getKnowledgeBase(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if !a.authorizeKB(w, r, id, "viewer") {
		return
	}
	var kb KnowledgeBase
	err := a.db.QueryRow(r.Context(), "SELECT id,name,description,created_at FROM knowledge_bases WHERE id=$1", id).Scan(&kb.ID, &kb.Name, &kb.Description, &kb.CreatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		writeError(w, 404, "knowledge base not found")
		return
	}
	if err != nil {
		writeError(w, 500, "database error")
		return
	}
	writeJSON(w, 200, kb)
}

func (a *API) listDocuments(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if !a.authorizeKB(w, r, id, "viewer") {
		return
	}
	rows, err := a.db.Query(r.Context(), "SELECT id,knowledge_base_id,original_filename,media_type,status,chunk_count,failure_reason,created_at,updated_at FROM documents WHERE knowledge_base_id=$1 ORDER BY created_at DESC LIMIT 100", id)
	if err != nil {
		writeError(w, 500, "database error")
		return
	}
	defer rows.Close()
	items := make([]Document, 0)
	for rows.Next() {
		var d Document
		if err := scanDocument(rows, &d); err != nil {
			writeError(w, 500, "database error")
			return
		}
		items = append(items, d)
	}
	if err := rows.Err(); err != nil {
		writeError(w, 500, "database error")
		return
	}
	writeJSON(w, 200, items)
}

type scanner interface{ Scan(...any) error }

func scanDocument(s scanner, d *Document) error {
	return s.Scan(&d.ID, &d.KnowledgeBaseID, &d.OriginalFilename, &d.MediaType, &d.Status, &d.ChunkCount, &d.FailureReason, &d.CreatedAt, &d.UpdatedAt)
}
func (a *API) getDocument(ctx context.Context, id string) (Document, error) {
	var d Document
	err := scanDocument(a.db.QueryRow(ctx, "SELECT id,knowledge_base_id,original_filename,media_type,status,chunk_count,failure_reason,created_at,updated_at FROM documents WHERE id=$1", id), &d)
	return d, err
}

func (a *API) uploadDocument(w http.ResponseWriter, r *http.Request) {
	kbID := r.PathValue("id")
	if !a.authorizeKB(w, r, kbID, "editor") {
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, maxUploadBytes+1024*1024)
	if err := r.ParseMultipartForm(2 << 20); err != nil {
		writeError(w, 400, "invalid or oversized multipart upload")
		return
	}
	if r.MultipartForm != nil {
		defer r.MultipartForm.RemoveAll()
	}
	file, header, err := r.FormFile("file")
	if err != nil {
		writeError(w, 400, "file is required")
		return
	}
	defer file.Close()
	name := filepath.Base(strings.ReplaceAll(header.Filename, "\\", "/"))
	ext := strings.ToLower(filepath.Ext(name))
	media := map[string]string{".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".txt": "text/plain", ".md": "text/markdown", ".markdown": "text/markdown"}[ext]
	if media == "" || name == "" {
		writeError(w, 400, "supported files: PDF, DOCX, TXT, Markdown")
		return
	}
	id, err := newUUID()
	if err != nil {
		writeError(w, 500, "unable to create id")
		return
	}
	storagePath := filepath.Join(a.cfg.UploadDir, id+ext)
	// The AI service runs with the same container group and reads this volume read-only.
	output, err := os.OpenFile(storagePath, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0640)
	if err != nil {
		writeError(w, 500, "unable to store upload")
		return
	}
	n, copyErr := io.Copy(output, io.LimitReader(file, maxUploadBytes+1))
	closeErr := output.Close()
	if copyErr != nil || closeErr != nil || n > maxUploadBytes || n == 0 {
		_ = os.Remove(storagePath)
		writeError(w, 400, "invalid or oversized file")
		return
	}
	jobID, err := newUUID()
	if err != nil {
		_ = os.Remove(storagePath)
		writeError(w, 500, "unable to create job id")
		return
	}
	tx, err := a.db.Begin(r.Context())
	if err != nil {
		_ = os.Remove(storagePath)
		writeError(w, 500, "database error")
		return
	}
	defer tx.Rollback(r.Context())
	_, err = tx.Exec(r.Context(), "INSERT INTO documents(id,knowledge_base_id,original_filename,media_type,storage_path) VALUES($1,$2,$3,$4,$5)", id, kbID, name, media, storagePath)
	if err == nil {
		_, err = tx.Exec(r.Context(), "INSERT INTO processing_jobs(id,document_id) VALUES($1,$2)", jobID, id)
	}
	if err == nil {
		err = tx.Commit(r.Context())
	}
	if err != nil {
		_ = os.Remove(storagePath)
		writeError(w, 500, "database error")
		return
	}
	d, err := a.getDocument(r.Context(), id)
	if err != nil {
		writeError(w, 500, "database error")
		return
	}
	writeJSON(w, http.StatusAccepted, d)
}

func (a *API) chat(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if !a.authorizeKB(w, r, id, "viewer") {
		return
	}
	var input struct {
		Question string `json:"question"`
	}
	if err := decodeJSON(w, r, &input); err != nil {
		writeError(w, 400, err.Error())
		return
	}
	input.Question = strings.TrimSpace(input.Question)
	if input.Question == "" || len(input.Question) > 2000 {
		writeError(w, 400, "question must have 1-2000 characters")
		return
	}
	body, _ := json.Marshal(input)
	request, err := http.NewRequestWithContext(r.Context(), http.MethodPost, strings.TrimRight(a.cfg.AIServiceURL, "/")+"/internal/knowledge-bases/"+id+"/chat", strings.NewReader(string(body)))
	if err != nil {
		writeError(w, 500, "unable to prepare request")
		return
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("X-Internal-Token", a.cfg.InternalToken)
	response, err := a.ai.Do(request)
	if err != nil {
		writeError(w, 502, "AI service unavailable")
		return
	}
	defer response.Body.Close()
	data, err := io.ReadAll(io.LimitReader(response.Body, 2<<20))
	if err != nil {
		writeError(w, 502, "AI service response error")
		return
	}
	if response.StatusCode != 200 {
		writeError(w, 502, "AI service error")
		return
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(200)
	_, _ = w.Write(data)
}

func (a *API) agent(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if !a.authorizeKB(w, r, id, "viewer") {
		return
	}
	var input struct {
		Question string `json:"question"`
	}
	if err := decodeJSON(w, r, &input); err != nil {
		writeError(w, 400, err.Error())
		return
	}
	input.Question = strings.TrimSpace(input.Question)
	if input.Question == "" || len(input.Question) > 2000 {
		writeError(w, 400, "question must have 1-2000 characters")
		return
	}
	requestID, err := newUUID()
	if err != nil {
		writeError(w, 500, "unable to create request id")
		return
	}
	body, _ := json.Marshal(map[string]string{"question": input.Question, "user_id": userID(r), "request_id": requestID})
	request, err := http.NewRequestWithContext(r.Context(), http.MethodPost,
		strings.TrimRight(a.cfg.AIServiceURL, "/")+"/internal/knowledge-bases/"+id+"/agent", strings.NewReader(string(body)))
	if err != nil {
		writeError(w, 500, "unable to prepare request")
		return
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("X-Internal-Token", a.cfg.InternalToken)
	response, err := a.ai.Do(request)
	if err != nil {
		writeError(w, 502, "AI service unavailable")
		return
	}
	defer response.Body.Close()
	data, err := io.ReadAll(io.LimitReader(response.Body, 2<<20))
	if err != nil {
		writeError(w, 502, "AI service response error")
		return
	}
	if response.StatusCode == 403 {
		writeError(w, 403, "agent tool access denied")
		return
	}
	if response.StatusCode != 200 {
		writeError(w, 502, "agent request failed")
		return
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(200)
	_, _ = w.Write(data)
}

func (a *API) knowledgeBaseExists(ctx context.Context, id string) bool {
	var exists bool
	return a.db.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM knowledge_bases WHERE id=$1)", id).Scan(&exists) == nil && exists
}
func validUUID(value string) bool { return uuidPattern.MatchString(value) }
func newUUID() (string, error) {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", err
	}
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[:4], b[4:6], b[6:8], b[8:10], b[10:]), nil
}
func decodeJSON(w http.ResponseWriter, r *http.Request, target any) error {
	r.Body = http.MaxBytesReader(w, r.Body, 1<<20)
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return errors.New("invalid JSON body")
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return errors.New("only one JSON object is allowed")
	}
	return nil
}
func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}
func writeError(w http.ResponseWriter, status int, message string) {
	writeJSON(w, status, map[string]string{"error": message})
}
