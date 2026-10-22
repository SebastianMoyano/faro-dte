package servidor

import (
	"crypto/x509"
	"database/sql"
	"encoding/pem"
	"fmt"
	"net/http"
	"strconv"

	"dtechile/nucleo/firma"
	"dtechile/nucleo/portal"
)

// Rutas del portal del SII con certificado (api/routes/keystore.py y onboarding.py):
// negocios (alta con auto-relleno, estado de habilitación, panel de timbraje), empresas del
// certificado y el diagnóstico del onboarding. Todo solo lectura en el SII.
func (s *Servidor) rutasPortal(mux *http.ServeMux) {
	mux.HandleFunc("GET /api/v1/keystore/empresa", s.protegido(s.previsualizarEmpresa))
	mux.HandleFunc("GET /api/v1/keystore/certificados/{id}/empresas", s.protegido(s.empresasDelCertificado))
	mux.HandleFunc("POST /api/v1/keystore/negocios", s.protegido(s.agregarNegocio))
	mux.HandleFunc("GET /api/v1/keystore/negocios/{id}/estado", s.protegido(s.estadoNegocio))
	mux.HandleFunc("GET /api/v1/keystore/negocios/{id}/timbraje", s.protegido(s.timbrajeNegocio))
	mux.HandleFunc("GET /api/v1/keystore/negocios/{id}/f29", s.protegido(s.f29Negocio))
	mux.HandleFunc("GET /api/v1/keystore/negocios/{id}/movimientos", s.protegido(s.movimientosNegocio))
	mux.HandleFunc("GET /api/v1/onboarding/diagnostico", s.protegido(s.onboardingDiagnostico))
	mux.HandleFunc("GET /api/v1/onboarding/cartera", s.protegido(s.onboardingCartera))
}

// pemDeCertificado es keystore.pem_transitorio: el certificado del keystore como PEM (en memoria).
func (s *Servidor) pemDeCertificado(certID, cuenta int64) (certPEM, keyPEM []byte, err error) {
	p12, clave, err := s.descifrarCertificado(certID, cuenta)
	if err != nil {
		return nil, nil, fmt.Errorf("Certificado %d no encontrado", certID)
	}
	cert, err := firma.CargarP12(p12, clave)
	if err != nil {
		return nil, nil, err
	}
	return pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: cert.Cert.Raw}),
		pem.EncodeToMemory(&pem.Block{Type: "RSA PRIVATE KEY", Bytes: x509.MarshalPKCS1PrivateKey(cert.Clave)}), nil
}

// infoEmpresa es negocios.info_empresa: auto-relleno por consulta pública (nil si no está autorizada).
func infoEmpresa(rut, ambiente string) (map[string]any, error) {
	p, _ := portal.Nuevo(nil, nil, portal.BaseDe(ambiente))
	emp, err := p.ConsultarEmpresaAutorizada(rut, "")
	if err != nil {
		return nil, err
	}
	if emp == nil {
		return nil, nil
	}
	tipos := make([]map[string]any, 0, len(emp.Documentos))
	for _, d := range emp.Documentos {
		tipos = append(tipos, map[string]any{"codigo": d.Codigo, "descripcion": d.Descripcion})
	}
	return map[string]any{"rut": emp.Rut, "razon_social": emp.RazonSocial, "resolucion": map[string]any{"numero": emp.NroResolucion, "fecha": emp.FechaResolucion},
		"autorizado": len(emp.Documentos) > 0, "tipos": tipos}, nil
}

func (s *Servidor) previsualizarEmpresa(w http.ResponseWriter, r *http.Request) {
	rut := r.URL.Query().Get("rut")
	if rut == "" {
		errorQuery(w, r, "rut", "Field required", "missing")
		return
	}
	ambiente := r.URL.Query().Get("ambiente")
	if ambiente == "" {
		ambiente = "certificacion"
	}
	info, err := infoEmpresa(rut, ambiente)
	if err != nil { // Python no captura el error de red aquí → 500
		responderError(w, r, 500, "error_interno", "Ocurrió un error interno. Reporta el request_id al soporte.", nil)
		return
	}
	if info == nil {
		responderError(w, r, 404, "no_encontrado", fmt.Sprintf("El RUT %s no aparece autorizado a emitir DTE.", rut), nil)
		return
	}
	responderJSON(w, 200, info)
}

// empresasDelCertificado: todas las empresas asociadas al certificado (selector del SII).
// Python envuelve TODO lo que no sea SIIError/ValueError en 502 (incluido "no encontrado").
func (s *Servidor) empresasDelCertificado(w http.ResponseWriter, r *http.Request) {
	id, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
	if err != nil {
		errorPath(w, r, "cert_id")
		return
	}
	cuenta, _ := s.cuentaID(w, r)
	certPEM, keyPEM, err := s.pemDeCertificado(id, cuenta)
	if err != nil {
		responderError(w, r, 502, "sii_comunicacion", "No se pudo consultar el SII: "+err.Error(), nil)
		return
	}
	p, err := portal.Nuevo(certPEM, keyPEM, portal.BaseCertificacion)
	if err != nil {
		responderError(w, r, 404, "no_encontrado", err.Error(), nil)
		return
	}
	if _, err := p.Autenticar(""); err != nil {
		responderError(w, r, 502, "sii_comunicacion", "No se pudo consultar el SII: "+err.Error(), nil)
		return
	}
	emps, err := p.EmpresasAsociadas()
	if err != nil {
		responderError(w, r, 502, "sii_comunicacion", "No se pudo consultar el SII: "+err.Error(), nil)
		return
	}
	responderJSON(w, 200, emps)
}

// agregarNegocio es negocios.agregar_negocio (multipart/form: rut, cert_id, ambiente) → 201.
func (s *Servidor) agregarNegocio(w http.ResponseWriter, r *http.Request) {
	_ = r.ParseMultipartForm(1 << 20)
	rut := r.FormValue("rut")
	if rut == "" {
		responderError(w, r, 422, "validacion", "Los datos enviados no son válidos.", map[string]any{"errores": []map[string]any{{"loc": []any{"body", "rut"}, "msg": "Field required", "type": "missing"}}})
		return
	}
	var certID sql.NullInt64
	if v := r.FormValue("cert_id"); v != "" {
		n, err := strconv.ParseInt(v, 10, 64)
		if err != nil {
			responderError(w, r, 422, "validacion", "Los datos enviados no son válidos.", map[string]any{"errores": []map[string]any{{"loc": []any{"body", "cert_id"}, "msg": "Input should be a valid integer, unable to parse string as an integer", "type": "int_parsing"}}})
			return
		}
		certID = sql.NullInt64{Int64: n, Valid: true}
	}
	ambiente := r.FormValue("ambiente")
	if ambiente == "" {
		ambiente = "certificacion"
	}
	cuenta, _ := s.cuentaID(w, r)
	info, err := infoEmpresa(rut, ambiente)
	if err != nil {
		responderError(w, r, 500, "error_interno", "Ocurrió un error interno. Reporta el request_id al soporte.", nil)
		return
	}
	if info == nil {
		responderError(w, r, 422, "validacion", fmt.Sprintf("El RUT %s no aparece autorizado a emitir DTE en el SII.", rut), nil)
		return
	}
	rutN, razon := info["rut"].(string), info["razon_social"].(string)
	var id int64
	err = s.db.QueryRow(`SELECT id FROM negocios WHERE cuenta_id=? AND rut=?`, cuenta, rutN).Scan(&id)
	if err == nil {
		_, err = s.db.Exec(`UPDATE negocios SET razon_social=?, cert_id=?, ambiente=? WHERE id=?`, razon, certID, ambiente, id)
	} else {
		var res sql.Result
		res, err = s.db.Exec(`INSERT INTO negocios (cuenta_id, rut, razon_social, cert_id, ambiente, creado) VALUES (?,?,?,?,?,?)`, cuenta, rutN, razon, certID, ambiente, ahoraUTC())
		if err == nil {
			id, _ = res.LastInsertId()
		}
	}
	if err != nil {
		responderError(w, r, 500, "error_interno", "Ocurrió un error interno. Reporta el request_id al soporte.", nil)
		return
	}
	var certOut any
	if certID.Valid {
		certOut = certID.Int64
	}
	responderJSON(w, 201, map[string]any{"id": id, "rut": rutN, "razon_social": razon, "cert_id": certOut, "ambiente": ambiente})
}

// negocioO404 es _negocio_o_404: el negocio de la cuenta, con certificado asociado.
func (s *Servidor) negocioO404(w http.ResponseWriter, r *http.Request) (*filaNegocio, int64, bool) {
	id, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
	if err != nil {
		errorPath(w, r, "negocio_id")
		return nil, 0, false
	}
	cuenta, _ := s.cuentaID(w, r)
	negs, err := s.negociosDe(cuenta)
	if err != nil {
		responderError(w, r, 500, "error_interno", "Ocurrió un error interno. Reporta el request_id al soporte.", nil)
		return nil, 0, false
	}
	for i := range negs {
		if negs[i].ID == id {
			if !negs[i].CertID.Valid || negs[i].CertID.Int64 == 0 {
				responderError(w, r, 422, "validacion", "El negocio no tiene un certificado asociado", nil)
				return nil, 0, false
			}
			return &negs[i], cuenta, true
		}
	}
	responderError(w, r, 404, "no_encontrado", "Negocio no encontrado", nil)
	return nil, 0, false
}

func (s *Servidor) softwareRegistrado(rut string) string {
	var v sql.NullString
	_ = s.db.QueryRow(`SELECT software_registrado FROM negocios WHERE rut=? AND software_registrado IS NOT NULL LIMIT 1`, rut).Scan(&v)
	return v.String
}

func (s *Servidor) guardarEstadoNegocio(cuenta, id int64, estado, etiqueta string, usaPropio bool) {
	usa := 0
	if usaPropio {
		usa = 1
	}
	_, _ = s.db.Exec(`UPDATE negocios SET estado=?, estado_etiqueta=?, usa_sw_propio=?, estado_ts=? WHERE id=? AND cuenta_id=?`, estado, etiqueta, usa, ahoraUTC(), id, cuenta)
}

// portalDelNegocio abre y autentica el portal con el certificado del negocio.
func (s *Servidor) portalDelNegocio(neg *filaNegocio, cuenta int64, base string) (*portal.Cliente, error) {
	certPEM, keyPEM, err := s.pemDeCertificado(neg.CertID.Int64, cuenta)
	if err != nil {
		return nil, err
	}
	p, err := portal.Nuevo(certPEM, keyPEM, base)
	if err != nil {
		return nil, err
	}
	if _, err := p.Autenticar(""); err != nil {
		return nil, err
	}
	return p, nil
}

type pasoNegocio = map[string]any

// estadoNegocio es estado_negocio: autorización en PRODUCCIÓN, software con que emite hoy,
// certificación del software propio y los próximos pasos.
func (s *Servidor) estadoNegocio(w http.ResponseWriter, r *http.Request) {
	neg, cuenta, ok := s.negocioO404(w, r)
	if !ok {
		return
	}
	p, err := s.portalDelNegocio(neg, cuenta, portal.BaseCertificacion)
	if err != nil {
		responderError(w, r, 502, "sii_comunicacion", "No se pudo consultar el SII: "+err.Error(), nil)
		return
	}
	emp, err := p.ConsultarEmpresaAutorizada(neg.Rut, portal.BaseProduccion)
	var swProd, swCert portal.DatosSoftware
	var sit map[int]portal.SituacionTipo
	if err == nil {
		swProd, err = p.DatosSoftwareDe(neg.Rut, portal.BaseProduccion)
	}
	if err == nil {
		swCert, err = p.DatosSoftwareDe(neg.Rut, portal.BaseCertificacion)
	}
	if err == nil {
		sit, err = p.SituacionFolios(neg.Rut, []int{33, 61})
	}
	if err != nil {
		responderError(w, r, 502, "sii_comunicacion", "No se pudo consultar el SII: "+err.Error(), nil)
		return
	}
	autorizados := []map[string]any{}
	if emp != nil {
		for _, d := range emp.Documentos {
			if d.DesautorizadoDesde == "" {
				autorizados = append(autorizados, map[string]any{"tipo": d.Codigo, "nombre": d.Descripcion, "desde": d.AutorizadoDesde})
			}
		}
	}
	estado, etiqueta := portal.ClasificarSoftware(swProd, swCert, s.softwareRegistrado(neg.Rut), s.cfg.NombreSoftware)
	swA, swB := swProd.Software, swCert.Software
	if swA == "" {
		swA = "el sistema del SII"
	}
	if swB == "" {
		swB = "tu software propio"
	}
	pasos := []pasoNegocio{}
	switch estado {
	case "emitiendo":
		pasos = append(pasos, pasoNegocio{"titulo": "Todo listo", "detalle": fmt.Sprintf("Ya emites en producción con %s. No hay pasos pendientes.", swB), "hecho": true})
	case "externo":
		pasos = append(pasos,
			pasoNegocio{"titulo": "Emite con otro proveedor", "urgente": true,
				"detalle": fmt.Sprintf("El SII tiene registrado «%s» como software de esta empresa. No emitas facturas con este motor mientras siga así: los folios del CAF los administra ese proveedor y se chocarían. La autorización es de la empresa, así que cambiarse NO exige volver a certificar.", swA)},
			pasoNegocio{"titulo": "1. Sacar el historial del proveedor",
				"detalle": "PDF y XML de todo lo emitido y recibido, antes de cortar el contrato: después ese acceso desaparece y no se puede importar desde aquí."},
			pasoNegocio{"titulo": "2. Cerrar los folios que le quedan",
				"detalle": "Pedirle el último folio usado por tipo y los CAF sin usar (se cargan aquí), o anular esos rangos en el SII con la firma de quien los timbró. Folios sin usar acumulados bloquean el timbraje nuevo."},
			pasoNegocio{"titulo": "3. Fecha de corte",
				"detalle": "Un cierre de mes: hasta ese día emite el proveedor, desde el siguiente este motor. La numeración sigue igual (es por empresa, no por software)."},
			pasoNegocio{"titulo": "4. Timbrar folios con este motor",
				"detalle": "Con el certificado cargado, pedir los rangos por tipo desde «Folios»."},
			pasoNegocio{"titulo": "5. Actualizar datos en el SII",
				"detalle": "Cambiar el nombre del software y, sobre todo, la casilla de intercambio (adonde llegan las facturas de tus proveedores), en «Actualización de datos del contribuyente». Todavía es manual."},
			pasoNegocio{"titulo": "6. Primer documento",
				"detalle": "Emitir uno real y comprobar el veredicto del SII (aceptado), no solo el estado del envío."})
	case "certificando":
		var bloq []string
		for _, t := range []int{33, 61} {
			if sit[t].Bloqueado {
				if t == 33 {
					bloq = append(bloq, "Factura (33)")
				} else {
					bloq = append(bloq, "Nota de crédito (61)")
				}
			}
		}
		if len(bloq) > 0 {
			nb := bloq[0]
			for _, x := range bloq[1:] {
				nb += ", " + x
			}
			pasos = append(pasos, pasoNegocio{"titulo": "Destrabar folios de prueba", "urgente": true,
				"detalle": fmt.Sprintf("El timbraje de %s está bloqueado en certificación. Pídelo a la Mesa de Ayuda del SII (600 330 3000) para poder emitir los documentos del set de pruebas.", nb)})
		}
		pasos = append(pasos, pasoNegocio{"titulo": "Completar el set de pruebas", "detalle": "Emitir y validar los documentos del set de certificación en el ambiente de pruebas."},
			pasoNegocio{"titulo": "Obtener la resolución del SII", "detalle": fmt.Sprintf("Al terminar el set, el SII emite la resolución que autoriza a %s como tu software de emisión.", swB)},
			pasoNegocio{"titulo": "Activar en producción", "detalle": fmt.Sprintf("Cambiar el software registrado en producción de «%s» a «%s» (Actualización de datos del contribuyente).", swA, swB)})
	case "certificado":
		pasos = append(pasos, pasoNegocio{"titulo": "Activar en producción", "detalle": fmt.Sprintf("Cambiar el software registrado de «%s» a «%s» en el SII (Actualización de datos del contribuyente).", swA, swB)},
			pasoNegocio{"titulo": "Emitir tu primer documento", "detalle": "Probar la emisión real en producción con tu software propio."})
	default: // sin_propio
		pasos = append(pasos, pasoNegocio{"titulo": "Registrar tu software", "detalle": "Declarar tu software propio en el SII (Actualización de datos del contribuyente)."},
			pasoNegocio{"titulo": "Certificar el software", "detalle": "Realizar el set de pruebas en el ambiente de certificación."})
	}
	s.guardarEstadoNegocio(cuenta, neg.ID, estado, etiqueta, swProd.Propio)
	o := func(s string) any {
		if s == "" {
			return "—"
		}
		return s
	}
	responderJSON(w, 200, map[string]any{
		"rut": neg.Rut, "razon_social": neg.RazonSocial.valor(), "estado": estado, "etiqueta": etiqueta, "pasos": pasos,
		"produccion":    map[string]any{"software_actual": o(swProd.Software), "usa_software_propio": swProd.Propio, "autorizado_a_emitir": autorizados},
		"certificacion": map[string]any{"software": o(swCert.Software), "es_propio": swCert.Propio, "finalizada": swCert.Certificado, "fecha": swCert.FechaResolucion},
	})
}

var tiposPanel = []struct {
	tipo   int
	nombre string
}{{33, "Factura"}, {34, "Factura exenta"}, {39, "Boleta"}, {52, "Guía despacho"}, {56, "Nota débito"}, {61, "Nota crédito"}}

// timbrajeNegocio es timbraje_negocio: por tipo, autorizado y si el timbraje está bloqueado.
func (s *Servidor) timbrajeNegocio(w http.ResponseWriter, r *http.Request) {
	neg, cuenta, ok := s.negocioO404(w, r)
	if !ok {
		return
	}
	base := portal.BaseCertificacion
	if neg.Ambiente.Valid && neg.Ambiente.String == "produccion" {
		base = portal.BaseProduccion
	}
	tipos := make([]int, 0, len(tiposPanel))
	for _, t := range tiposPanel {
		tipos = append(tipos, t.tipo)
	}
	p, err := s.portalDelNegocio(neg, cuenta, base)
	if err != nil {
		responderError(w, r, 502, "sii_comunicacion", "No se pudo consultar el SII: "+err.Error(), nil)
		return
	}
	emp, err := p.ConsultarEmpresaAutorizada(neg.Rut, "")
	var sit map[int]portal.SituacionTipo
	if err == nil {
		sit, err = p.SituacionFolios(neg.Rut, tipos)
	}
	if err != nil {
		responderError(w, r, 502, "sii_comunicacion", "No se pudo consultar el SII: "+err.Error(), nil)
		return
	}
	autorizados := map[int]bool{}
	if emp != nil {
		for _, d := range emp.Documentos {
			autorizados[d.Codigo] = true
		}
	}
	docs := []map[string]any{}
	for _, t := range tiposPanel {
		docs = append(docs, map[string]any{"tipo": t.tipo, "nombre": t.nombre, "autorizado": autorizados[t.tipo], "bloqueado": sit[t.tipo].Bloqueado})
	}
	responderJSON(w, 200, map[string]any{"rut": neg.Rut, "razon_social": neg.RazonSocial.valor(), "documentos": docs})
}

// portalDeQuery abre el portal (certificación) con el cert_id de la query, para el onboarding.
// Cualquier fallo (incluido "no encontrado") es 502, como el `except Exception` de Python.
func (s *Servidor) portalDeQuery(w http.ResponseWriter, r *http.Request, prefijo string) (*portal.Cliente, bool) {
	v := r.URL.Query().Get("cert_id")
	if v == "" {
		errorQuery(w, r, "cert_id", "Field required", "missing")
		return nil, false
	}
	certID, err := strconv.ParseInt(v, 10, 64)
	if err != nil {
		errorQuery(w, r, "cert_id", "Input should be a valid integer, unable to parse string as an integer", "int_parsing")
		return nil, false
	}
	cuenta, _ := s.cuentaID(w, r)
	certPEM, keyPEM, err := s.pemDeCertificado(certID, cuenta)
	if err != nil {
		responderError(w, r, 502, "sii_comunicacion", prefijo+err.Error(), nil)
		return nil, false
	}
	p, err := portal.Nuevo(certPEM, keyPEM, portal.BaseCertificacion)
	if err == nil {
		_, err = p.Autenticar("")
	}
	if err != nil {
		responderError(w, r, 502, "sii_comunicacion", prefijo+err.Error(), nil)
		return nil, false
	}
	return p, true
}

func (s *Servidor) onboardingDiagnostico(w http.ResponseWriter, r *http.Request) {
	rut := r.URL.Query().Get("rut")
	if r.URL.Query().Get("cert_id") != "" && rut == "" {
		errorQuery(w, r, "rut", "Field required", "missing")
		return
	}
	p, ok := s.portalDeQuery(w, r, "No se pudo diagnosticar la empresa en el SII: ")
	if !ok {
		return
	}
	nombre := r.URL.Query().Get("nombre_sistema")
	d, err := portal.Diagnosticar(p, rut, nombre, s.softwareRegistrado(rut), s.cfg.NombreSoftware)
	if err != nil {
		responderError(w, r, 502, "sii_comunicacion", "No se pudo diagnosticar la empresa en el SII: "+err.Error(), nil)
		return
	}
	responderJSON(w, 200, d)
}

func (s *Servidor) onboardingCartera(w http.ResponseWriter, r *http.Request) {
	p, ok := s.portalDeQuery(w, r, "No se pudo diagnosticar la cartera en el SII: ")
	if !ok {
		return
	}
	nombre := r.URL.Query().Get("nombre_sistema")
	ds, err := portal.DiagnosticarCartera(p, nil, nombre, s.softwareRegistrado, s.cfg.NombreSoftware)
	if err != nil {
		responderError(w, r, 502, "sii_comunicacion", "No se pudo diagnosticar la cartera en el SII: "+err.Error(), nil)
		return
	}
	out := []map[string]any{}
	for _, d := range ds {
		pend := 0
		for _, a := range d.Acciones {
			if !a.Hecho {
				pend++
			}
		}
		out = append(out, map[string]any{"rut": d.Rut, "razon_social": d.RazonSocial, "estado": d.Estado, "etiqueta": d.Etiqueta, "listo_para_emitir": d.ListoParaEmitir, "acciones_pendientes": pend})
	}
	responderJSON(w, 200, out)
}
