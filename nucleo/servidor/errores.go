package servidor

// Envoltorio de error y request-id, idénticos a api/errors.py:
//   {"error": {"codigo", "mensaje", "detalle"?, "request_id"}}  +  cabecera X-Request-ID.

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"log"
	"net/http"
	"time"

	"dtechile/nucleo/salaespera"
)

type cuerpoError struct {
	Codigo    string         `json:"codigo"`
	Mensaje   string         `json:"mensaje"`
	Detalle   map[string]any `json:"detalle,omitempty"`
	RequestID string         `json:"request_id,omitempty"`
}

type claveCtx string

const ctxRequestID claveCtx = "rid"

func requestID(r *http.Request) string {
	if v, ok := r.Context().Value(ctxRequestID).(string); ok {
		return v
	}
	return "-"
}

// responderError escribe el envoltorio con el status dado.
func responderError(w http.ResponseWriter, r *http.Request, status int, codigo, mensaje string, detalle map[string]any) {
	w.Header().Set("Content-Type", "application/json")
	if status == 401 {
		w.Header().Set("WWW-Authenticate", "Bearer")
	}
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(map[string]any{"error": cuerpoError{Codigo: codigo, Mensaje: salaespera.Limpiar(mensaje), Detalle: detalle, RequestID: requestID(r)}})
}

// responderJSON escribe un cuerpo JSON (sin escapar HTML, como FastAPI).
func responderJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	enc := json.NewEncoder(w)
	enc.SetEscapeHTML(false)
	enc.Encode(v)
}

type escritor struct {
	http.ResponseWriter
	status int
}

func (e *escritor) WriteHeader(s int) { e.status = s; e.ResponseWriter.WriteHeader(s) }

// conRequestID es el middleware de request-id + log de acceso (api/errors.py::_request_context).
func conRequestID(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		rid := r.Header.Get("X-Request-ID")
		if rid == "" {
			b := make([]byte, 6)
			rand.Read(b)
			rid = hex.EncodeToString(b)
		}
		ctx := contextoCon(r.Context(), ctxRequestID, rid)
		w.Header().Set("X-Request-ID", rid)
		ew := &escritor{ResponseWriter: w, status: 200}
		inicio := time.Now()
		next.ServeHTTP(ew, r.WithContext(ctx))
		log.Printf("%s %s → %d (%d ms) [rid=%s]", r.Method, r.URL.Path, ew.status, time.Since(inicio).Milliseconds(), rid)
	})
}
