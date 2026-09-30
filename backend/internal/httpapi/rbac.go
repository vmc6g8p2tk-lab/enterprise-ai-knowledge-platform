package httpapi

import (
	"errors"
	"net/http"
	"os"

	"github.com/jackc/pgx/v5"
)

func (a *API) listMembers(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if !a.authorizeKB(w, r, id, "admin") {
		return
	}
	rows, err := a.db.Query(r.Context(), `SELECT u.id,u.email,m.role FROM knowledge_base_members m
		JOIN users u ON u.id=m.user_id WHERE m.knowledge_base_id=$1 ORDER BY u.email`, id)
	if err != nil {
		writeError(w, 500, "database error")
		return
	}
	defer rows.Close()
	result := make([]map[string]string, 0)
	for rows.Next() {
		var uid, email, role string
		if rows.Scan(&uid, &email, &role) != nil {
			writeError(w, 500, "database error")
			return
		}
		result = append(result, map[string]string{"user_id": uid, "email": email, "role": role})
	}
	if rows.Err() != nil {
		writeError(w, 500, "database error")
		return
	}
	writeJSON(w, 200, result)
}

func (a *API) putMember(w http.ResponseWriter, r *http.Request) {
	id, target := r.PathValue("id"), r.PathValue("userID")
	if !a.authorizeKB(w, r, id, "admin") {
		return
	}
	if !validUUID(target) {
		writeError(w, 400, "invalid user id")
		return
	}
	var input struct {
		Role string `json:"role"`
	}
	if err := decodeJSON(w, r, &input); err != nil {
		writeError(w, 400, err.Error())
		return
	}
	if roleLevel(input.Role) == 0 {
		writeError(w, 400, "invalid role")
		return
	}
	tx, err := a.db.Begin(r.Context())
	if err != nil {
		writeError(w, 500, "database error")
		return
	}
	defer tx.Rollback(r.Context())
	// Lock the KB row so concurrent role edits cannot remove its last admin.
	var locked string
	if err = tx.QueryRow(r.Context(), "SELECT id FROM knowledge_bases WHERE id=$1 FOR UPDATE", id).Scan(&locked); err != nil {
		writeError(w, 500, "database error")
		return
	}
	var actingRole string
	if err = tx.QueryRow(r.Context(), "SELECT role FROM knowledge_base_members WHERE knowledge_base_id=$1 AND user_id=$2", id, userID(r)).Scan(&actingRole); err != nil || actingRole != "admin" {
		writeError(w, 403, "insufficient permission")
		return
	}
	var exists bool
	if err = tx.QueryRow(r.Context(), "SELECT EXISTS(SELECT 1 FROM users WHERE id=$1)", target).Scan(&exists); err != nil {
		writeError(w, 500, "database error")
		return
	}
	if !exists {
		writeError(w, 404, "user not found")
		return
	}
	var previous string
	err = tx.QueryRow(r.Context(), "SELECT role FROM knowledge_base_members WHERE knowledge_base_id=$1 AND user_id=$2", id, target).Scan(&previous)
	if err != nil && !errors.Is(err, pgx.ErrNoRows) {
		writeError(w, 500, "database error")
		return
	}
	if previous == "admin" && input.Role != "admin" {
		var count int
		if err = tx.QueryRow(r.Context(), "SELECT count(*) FROM knowledge_base_members WHERE knowledge_base_id=$1 AND role='admin'", id).Scan(&count); err != nil {
			writeError(w, 500, "database error")
			return
		}
		if count <= 1 {
			writeError(w, 409, "knowledge base needs an admin")
			return
		}
	}
	_, err = tx.Exec(r.Context(), `INSERT INTO knowledge_base_members(knowledge_base_id,user_id,role) VALUES($1,$2,$3)
		ON CONFLICT(knowledge_base_id,user_id) DO UPDATE SET role=excluded.role`, id, target, input.Role)
	if err != nil || tx.Commit(r.Context()) != nil {
		writeError(w, 500, "database error")
		return
	}
	writeJSON(w, 200, map[string]string{"knowledge_base_id": id, "user_id": target, "role": input.Role})
}

func (a *API) deleteKnowledgeBase(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if !a.authorizeKB(w, r, id, "admin") {
		return
	}
	rows, err := a.db.Query(r.Context(), "SELECT storage_path FROM documents WHERE knowledge_base_id=$1", id)
	if err != nil {
		writeError(w, 500, "database error")
		return
	}
	paths := make([]string, 0)
	for rows.Next() {
		var path string
		if rows.Scan(&path) != nil {
			rows.Close()
			writeError(w, 500, "database error")
			return
		}
		paths = append(paths, path)
	}
	rowErr := rows.Err()
	rows.Close()
	if rowErr != nil {
		writeError(w, 500, "database error")
		return
	}
	deleted, err := a.db.Exec(r.Context(), `DELETE FROM knowledge_bases WHERE id=$1 AND EXISTS(
		SELECT 1 FROM knowledge_base_members WHERE knowledge_base_id=$1 AND user_id=$2 AND role='admin')`, id, userID(r))
	if err != nil {
		writeError(w, 500, "database error")
		return
	}
	if deleted.RowsAffected() == 0 {
		writeError(w, 403, "insufficient permission")
		return
	}
	for _, path := range paths {
		_ = os.Remove(path)
	}
	w.WriteHeader(http.StatusNoContent)
}

func (a *API) deleteDocument(w http.ResponseWriter, r *http.Request) {
	id, docID := r.PathValue("id"), r.PathValue("documentID")
	if !a.authorizeKB(w, r, id, "admin") {
		return
	}
	if !validUUID(docID) {
		writeError(w, 400, "invalid document id")
		return
	}
	var path string
	err := a.db.QueryRow(r.Context(), `DELETE FROM documents WHERE id=$1 AND knowledge_base_id=$2 AND EXISTS(
		SELECT 1 FROM knowledge_base_members WHERE knowledge_base_id=$2 AND user_id=$3 AND role='admin') RETURNING storage_path`, docID, id, userID(r)).Scan(&path)
	if errors.Is(err, pgx.ErrNoRows) {
		writeError(w, 404, "document not found")
		return
	}
	if err != nil {
		writeError(w, 500, "database error")
		return
	}
	_ = os.Remove(path)
	w.WriteHeader(http.StatusNoContent)
}
