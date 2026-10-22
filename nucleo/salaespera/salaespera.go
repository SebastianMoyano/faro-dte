// Package salaespera detecta la sala de espera (Queue-it) del SII: salaespera.sii.cl o
// *.queue-it.net (docs/SII-ENDPOINTS.md §5). Si el SII manda a la fila, el HTML que vuelve no
// es del servicio pedido; se corta con un error claro en vez de parsearlo. Hermano de
// core/sala_espera.py y de es_sala_espera en nucleo-rs/src/sii/http.rs (mismo texto).
package salaespera

import (
	"errors"
	"net/http"
	"net/url"
	"strings"
)

// Mensaje es lo que se le dice a la persona; idéntico en los tres motores.
const Mensaje = "El SII tiene una fila de espera en este servicio (sala de espera): intenta de nuevo en unos minutos."

// ErrSalaEspera se devuelve cuando una respuesta del SII terminó en la sala de espera.
var ErrSalaEspera = errors.New(Mensaje)

// Es dice si la URL es de la sala de espera. Una ruta relativa nunca lo es.
func Es(u string) bool {
	if !strings.Contains(u, "://") && !strings.HasPrefix(u, "//") {
		return false
	}
	p, err := url.Parse(u)
	if err != nil {
		return false
	}
	h := strings.ToLower(p.Hostname())
	return h == "salaespera.sii.cl" || h == "queue-it.net" || strings.HasSuffix(h, ".queue-it.net")
}

// Transporte envuelve un RoundTripper (nil = http.DefaultTransport). Ve cada salto de una
// redirección, así que corta tanto si se sigue el 302 como si no (CheckRedirect).
func Transporte(rt http.RoundTripper) http.RoundTripper {
	if rt == nil {
		rt = http.DefaultTransport
	}
	return transporte{rt}
}

type transporte struct{ rt http.RoundTripper }

func (t transporte) RoundTrip(req *http.Request) (*http.Response, error) {
	if Es(req.URL.String()) {
		return nil, ErrSalaEspera
	}
	resp, err := t.rt.RoundTrip(req)
	if err != nil {
		return nil, err
	}
	if loc := resp.Header.Get("Location"); loc != "" && Es(loc) {
		resp.Body.Close()
		return nil, ErrSalaEspera
	}
	return resp, nil
}

// Limpiar deja solo Mensaje si el texto de un error lo contiene (lo envuelven prefijos como
// `Get "url": ...` o "No se pudo consultar el SII: ..."), para que los tres motores respondan igual.
func Limpiar(mensaje string) string {
	if strings.Contains(mensaje, Mensaje) {
		return Mensaje
	}
	return mensaje
}
