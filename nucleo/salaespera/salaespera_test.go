package salaespera

import (
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestEs(t *testing.T) {
	si := []string{
		"https://salaespera.sii.cl/?c=sii&e=consultaterceros&t=https%3A%2F%2Fwww2.sii.cl%2Fstc%2Fnoauthz&cid=es-CL",
		"HTTPS://SalaEspera.SII.cl", "//salaespera.sii.cl/?c=sii", "https://sii.queue-it.net/?c=sii&e=renta",
		"https://salaespera.sii.cl:443/x",
	}
	no := []string{
		"https://www2.sii.cl/stc/noauthz?t=salaespera.sii.cl", "https://misiir.sii.cl/cgi_misii/siihome.cgi",
		"/salaespera.sii.cl", "https://notqueue-it.net/", "",
	}
	for _, u := range si {
		if !Es(u) {
			t.Errorf("debió reconocer %q", u)
		}
	}
	for _, u := range no {
		if Es(u) {
			t.Errorf("no debió reconocer %q", u)
		}
	}
}

func TestRedireccionALaFila(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Location", "https://salaespera.sii.cl/?c=sii&e=autenticacionmisii")
		w.WriteHeader(302)
	}))
	defer srv.Close()
	for _, seguir := range []bool{true, false} {
		cli := &http.Client{Transport: Transporte(nil)}
		if !seguir {
			cli.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
		}
		_, err := cli.Get(srv.URL + "/cgi_misii/siihome.cgi")
		if !errors.Is(err, ErrSalaEspera) {
			t.Fatalf("seguir=%v: esperaba ErrSalaEspera, vino %v", seguir, err)
		}
		if Limpiar("No se pudo consultar el SII: "+err.Error()) != Mensaje {
			t.Fatalf("Limpiar no dejó solo el mensaje: %v", err)
		}
	}
}
