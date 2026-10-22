package servidor

import (
	"net/http"

	"dtechile/nucleo/portal"
	"dtechile/nucleo/rcv"
)

// movimientosNegocio es GET /api/v1/keystore/negocios/{id}/movimientos?periodos=YYYYMM,…&limite=:
// los últimos movimientos del negocio tal como los tiene el SII (RCV de PRODUCCIÓN, compras y
// ventas), bajados en el momento con la sesión del certificado del negocio. Solo lectura.
func (s *Servidor) movimientosNegocio(w http.ResponseWriter, r *http.Request) {
	limite, ok := enteroQuery(w, r, "limite", 50, 1, 500)
	if !ok {
		return
	}
	periodos := rcv.ParsearPeriodos(r.URL.Query().Get("periodos"))
	if periodos == nil {
		responderError(w, r, 422, "validacion", rcv.MensajePeriodos, nil)
		return
	}
	neg, cuenta, ok := s.negocioO404(w, r)
	if !ok {
		return
	}
	p, err := s.portalDelNegocio(neg, cuenta, portal.BaseProduccion)
	if err != nil {
		responderError(w, r, 502, "sii_comunicacion", "No se pudo bajar el RCV del SII: "+err.Error(), nil)
		return
	}
	res, err := rcv.Nuevo(p.Cookies["TOKEN"], nil).UltimosMovimientos(s.db, neg.Rut, periodos, limite)
	if err != nil {
		responderError(w, r, 502, "sii_comunicacion", "No se pudo bajar el RCV del SII: "+err.Error(), nil)
		return
	}
	res["rut"], res["razon_social"] = neg.Rut, neg.RazonSocial.valor()
	responderJSON(w, 200, res)
}
