// Package rcv es core/rcv.py: el Registro de Compras y Ventas del SII (la API JSON "facade"
// del portal www4.sii.cl/consdcvinternetui), su normalización a filas de `rcv_documentos` y
// la agregación al F29 (IVA mensual). Solo existe con datos en PRODUCCIÓN (Palena).
//
// Contrato real capturado del navegador (2026-10-04): el detalle "Export" exige
// accionRecaptcha + tokenRecaptcha (placeholder "t-o-k-e-n-web"), VENTA manda estadoContab
// vacío y COMPRA "REGISTRO"; la respuesta del Export es {"data": ["cab;eza;ra", "fila;…"]}
// (CSV con ';'), no una lista de dicts. getResumen sí devuelve dicts. `Accept:
// application/json` provoca HTTP 500 en el facade: debe ser `*/*`.
package rcv

import (
	"bytes"
	"crypto/rand"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"time"

	"dtechile/nucleo/salaespera"
)

const (
	Base           = "https://www4.sii.cl/consdcvinternetui/services/data/facadeService/"
	Compra         = "COMPRA"
	Venta          = "VENTA"
	TokenRecaptcha = "t-o-k-e-n-web"
	UserAgent      = "Mozilla/5.0 (compatible; PROG 1.0; +https://github.com/SebastianMoyano/dte-chile)"
)

var (
	Metodos          = map[string]string{Compra: "getDetalleCompraExport", Venta: "getDetalleVentaExport"}
	AccionRecaptcha  = map[string]string{Venta: "RCV_DDETV", Compra: "RCV_DDETC"}
	EstadoExport     = map[string]string{Venta: "", Compra: "REGISTRO"}
	TiposNotaCredito = map[int]bool{61: true}
)

// Endpoints es RCV_ENDPOINTS (COMPRA, VENTA y resumen).
func Endpoints() map[string]string {
	e := map[string]string{}
	for op, m := range Metodos {
		e[op] = Base + m
	}
	e["resumen"] = Base + "getResumen"
	return e
}

func namespace(metodo string) string {
	return "cl.sii.sdi.lob.diii.consdcv.data.api.interfaces.FacadeService/" + metodo
}

// SignoDocumento: +1 suma (facturas, ND); -1 notas de crédito.
func SignoDocumento(tipo int) int {
	if TiposNotaCredito[tipo] {
		return -1
	}
	return 1
}

// Documento es DocumentoRCV (una fila de rcv_documentos).
type Documento struct {
	RutEmpresa       string  `json:"rut_empresa"`
	TipoOperacion    string  `json:"tipo_operacion"`
	Periodo          string  `json:"periodo"`
	TipoDTE          int     `json:"tipo_dte"`
	Folio            int     `json:"folio"`
	FechaDoc         *string `json:"fecha_doc"`
	RutContraparte   string  `json:"rut_contraparte"`
	RazonSocial      string  `json:"razon_social"`
	MontoExento      int64   `json:"monto_exento"`
	MontoNeto        int64   `json:"monto_neto"`
	MontoIVA         int64   `json:"monto_iva"`
	MontoIVANoRec    int64   `json:"monto_iva_no_rec"`
	MontoIVAUsoComun int64   `json:"monto_iva_uso_comun"`
	MontoTotal       int64   `json:"monto_total"`
	EstadoSII        string  `json:"estado_sii"`
	Clasificacion    *string `json:"clasificacion"`
	Origen           string  `json:"origen"`
}

// ---------------------------------------------------------------- normalización

// Num es _num: montos del RCV a entero (None, ”, floats redondeados al par, "1.234").
func Num(v any) int64 {
	switch x := v.(type) {
	case nil:
		return 0
	case float64:
		return int64(math.RoundToEven(x))
	case int:
		return int64(x)
	case int64:
		return x
	case json.Number:
		if f, err := x.Float64(); err == nil {
			return int64(math.RoundToEven(f))
		}
		return 0
	case string:
		s := strings.ReplaceAll(strings.ReplaceAll(strings.TrimSpace(x), ".", ""), ",", "")
		if s == "" {
			return 0
		}
		n, err := strconv.ParseInt(s, 10, 64)
		if err != nil {
			return 0
		}
		return n
	}
	return 0
}

var formatosFecha = []struct{ py, go_ string }{{"%d/%m/%Y", "02/01/2006"}, {"%Y-%m-%d", "2006-01-02"}, {"%Y-%m-%dT%H:%M:%S", "2006-01-02T15:04:05"}, {"%d-%m-%Y", "02-01-2006"}}

// FechaISO es _fecha_iso: dd/mm/yyyy, yyyy-mm-dd, …→ YYYY-MM-DD (nil si vacío). Conserva la
// rareza de Python: prueba cada formato sobre s[:len(fmt)+4] y, si ninguno calza, devuelve
// los 10 primeros caracteres tal cual (o nil si hay menos de 10).
func FechaISO(v any) *string {
	if v == nil {
		return nil
	}
	s := strings.TrimSpace(fmt.Sprint(v))
	if s == "" {
		return nil
	}
	for _, f := range formatosFecha {
		n := len(f.py) + 4
		sub := s
		if len(sub) > n {
			sub = sub[:n]
		}
		if t, err := time.Parse(f.go_, sub); err == nil {
			r := t.Format("2006-01-02")
			return &r
		}
	}
	if len(s) >= 10 {
		r := s[:10]
		return &r
	}
	return nil
}

// NormalizarPeriodo acepta 'YYYY-MM', 'YYYYMM' o 'YYYY/MM' → 'YYYYMM'.
func NormalizarPeriodo(p string) string {
	s := strings.TrimSpace(strings.ReplaceAll(strings.ReplaceAll(p, "-", ""), "/", ""))
	if len(s) > 6 {
		s = s[:6]
	}
	return s
}

// SepararRut es core/rut.separar_rut: ("12345678", "9") o ("", "") si no parece RUT.
func SepararRut(rut string) (string, string) {
	s := strings.ToUpper(strings.ReplaceAll(strings.ReplaceAll(strings.TrimSpace(rut), ".", ""), " ", ""))
	if s == "" {
		return "", ""
	}
	n, dv, ok := strings.Cut(s, "-")
	if !ok {
		if len(s) < 2 {
			return "", ""
		}
		n, dv = s[:len(s)-1], s[len(s)-1:]
	}
	for _, c := range n {
		if c < '0' || c > '9' {
			return "", ""
		}
	}
	if n == "" || len(dv) != 1 || !(dv == "K" || (dv[0] >= '0' && dv[0] <= '9')) {
		return "", ""
	}
	return n, dv
}

// NormalizarRut es _normalizar_rut: NNNNNNNN-D o el texto tal cual si no se puede.
func NormalizarRut(rut string) string {
	n, dv := SepararRut(rut)
	if n != "" && dv != "" {
		return n + "-" + dv
	}
	return rut
}

func get(f map[string]any, claves ...string) any {
	for _, c := range claves {
		if v, ok := f[c]; ok && v != nil {
			return v
		}
	}
	return nil
}

func texto(v any) string {
	if v == nil {
		return ""
	}
	switch x := v.(type) {
	case string:
		return x
	case float64:
		if x == math.Trunc(x) {
			return strconv.FormatInt(int64(x), 10)
		}
		return strconv.FormatFloat(x, 'f', -1, 64)
	}
	return fmt.Sprint(v)
}

// entero es int(valor or 0) de Python sobre lo que trae el facade (número o texto).
func entero(v any) int {
	switch x := v.(type) {
	case nil:
		return 0
	case float64:
		return int(x)
	case string:
		n, err := strconv.Atoi(strings.TrimSpace(x))
		if err != nil {
			return 0
		}
		return n
	}
	return 0
}

// Columnas del CSV del Export → campo del documento (orden de inserción = prioridad).
var columnasCSV = []struct{ col, campo string }{
	{"tipo doc", "tipo_dte"}, {"folio", "folio"}, {"fecha docto", "fecha_doc"}, {"rut cliente", "rut_contraparte"},
	{"rut proveedor", "rut_contraparte"}, {"razon social", "razon_social"}, {"monto exento", "monto_exento"},
	{"monto neto", "monto_neto"}, {"monto iva", "monto_iva"}, {"monto iva recuperable", "monto_iva"},
	{"monto iva no recuperable", "monto_iva_no_rec"}, {"monto iva no rec", "monto_iva_no_rec"}, {"monto iva no rec.", "monto_iva_no_rec"},
	{"monto iva uso comun", "monto_iva_uso_comun"}, {"monto total", "monto_total"},
}

func campoDeColumna(col string) string {
	for _, c := range columnasCSV {
		if c.col == col {
			return c.campo
		}
	}
	for _, c := range columnasCSV { // "Monto Neto Activo Fijo" → monto_neto (prefijo largo)
		if strings.HasPrefix(col, c.col) && len(c.col) > 9 {
			return c.campo
		}
	}
	return ""
}

// csvADicts es _csv_a_dicts: las líneas del Export a dicts con las llaves del facade.
func csvADicts(lineas []string) []map[string]any {
	if len(lineas) == 0 {
		return nil
	}
	var cab []string
	for _, c := range strings.Split(lineas[0], ";") {
		cab = append(cab, strings.ToLower(strings.TrimSpace(c)))
	}
	var out []map[string]any
	for _, ln := range lineas[1:] {
		if strings.TrimSpace(ln) == "" {
			continue
		}
		vals := strings.Split(ln, ";")
		fila := map[string]string{}
		for i, col := range cab {
			campo := campoDeColumna(col)
			if campo != "" && i < len(vals) {
				if _, ya := fila[campo]; !ya { // la primera columna que calza gana
					fila[campo] = strings.TrimSpace(vals[i])
				}
			}
		}
		if fila["folio"] == "" {
			continue
		}
		n, dv, _ := strings.Cut(fila["rut_contraparte"], "-")
		out = append(out, map[string]any{"tipoDoc": fila["tipo_dte"], "folio": fila["folio"], "fecha": fila["fecha_doc"], "rut": n, "dv": dv,
			"rznSoc": fila["razon_social"], "mntExe": fila["monto_exento"], "mntNeto": fila["monto_neto"], "mntIVA": fila["monto_iva"],
			"mntIVANoRec": fila["monto_iva_no_rec"], "mntIVAUsoComun": fila["monto_iva_uso_comun"], "mntTotal": fila["monto_total"]})
	}
	return out
}

// NormalizarDetalle es normalizar_detalle: la respuesta del facade (dict con data/detalle,
// lista de dicts o CSV del Export) → documentos. Tolerante a las variantes de llaves.
func NormalizarDetalle(rutEmpresa, tipoOperacion, periodo string, respuesta any, origen string) []Documento {
	if origen == "" {
		origen = "rcv"
	}
	rutEmpresa = NormalizarRut(rutEmpresa)
	periodo = NormalizarPeriodo(periodo)
	filas := respuesta
	if m, ok := respuesta.(map[string]any); ok {
		filas = get(m, "data", "detalle", "detalles")
		if filas == nil {
			filas = []any{}
		}
	}
	lista, ok := filas.([]any)
	if !ok {
		return []Documento{}
	}
	todasTexto := len(lista) > 0
	var lineas []string
	for _, x := range lista {
		s, es := x.(string)
		if !es {
			todasTexto = false
			break
		}
		lineas = append(lineas, s)
	}
	var dicts []map[string]any
	if todasTexto {
		dicts = csvADicts(lineas)
	} else {
		for _, x := range lista {
			if d, es := x.(map[string]any); es {
				dicts = append(dicts, d)
			}
		}
	}
	docs := []Documento{}
	for _, f := range dicts {
		rutC := texto(get(f, "detRutDoc", "rutDoc", "rut"))
		dvC := texto(get(f, "detDvDoc", "dvDoc", "dv"))
		contraparte := rutC
		if rutC != "" && dvC != "" {
			contraparte = rutC + "-" + dvC
		}
		estado := texto(get(f, "detEventoReceptor", "estado"))
		if estado == "" {
			estado = "REGISTRO"
		}
		docs = append(docs, Documento{RutEmpresa: rutEmpresa, TipoOperacion: tipoOperacion, Periodo: periodo,
			TipoDTE: entero(get(f, "detTipoDoc", "tipoDoc", "tipoDte")), Folio: entero(get(f, "detNroDoc", "nroDoc", "folio")),
			FechaDoc: FechaISO(get(f, "detFchDoc", "fchDoc", "fecha")), RutContraparte: contraparte,
			RazonSocial: texto(get(f, "detRznSoc", "rznSoc", "razonSocial")),
			MontoExento: Num(get(f, "detMntExe", "mntExe", "montoExento")), MontoNeto: Num(get(f, "detMntNeto", "mntNeto", "montoNeto")),
			MontoIVA: Num(get(f, "detMntIVA", "mntIVA", "montoIva")), MontoIVANoRec: Num(get(f, "detMntIVANoRec", "detIVANoRec", "mntIVANoRec")),
			MontoIVAUsoComun: Num(get(f, "detMntIVAUsoComun", "mntIVAUsoComun")), MontoTotal: Num(get(f, "detMntTotal", "mntTotal", "montoTotal")),
			EstadoSII: estado, Origen: origen})
	}
	return docs
}

// ---------------------------------------------------------------- persistencia

// GuardarDocumentos es guardar_documentos: upsert idempotente (UNIQUE rut, operación, tipo,
// folio, contraparte). Devuelve cuántos se escribieron.
func GuardarDocumentos(db *sql.DB, docs []Documento) (int, error) {
	if len(docs) == 0 {
		return 0, nil
	}
	ahora := time.Now().Format("2006-01-02T15:04:05")
	tx, err := db.Begin()
	if err != nil {
		return 0, err
	}
	defer tx.Rollback()
	st, err := tx.Prepare(`INSERT INTO rcv_documentos (rut_empresa, tipo_operacion, periodo, tipo_dte, folio, fecha_doc, rut_contraparte, razon_social,
		monto_exento, monto_neto, monto_iva, monto_iva_no_rec, monto_iva_uso_comun, monto_total, estado_sii, clasificacion, origen, creado_en, actualizado_en)
		VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
		ON CONFLICT(rut_empresa, tipo_operacion, tipo_dte, folio, rut_contraparte) DO UPDATE SET periodo=excluded.periodo, fecha_doc=excluded.fecha_doc,
		razon_social=excluded.razon_social, monto_exento=excluded.monto_exento, monto_neto=excluded.monto_neto, monto_iva=excluded.monto_iva,
		monto_iva_no_rec=excluded.monto_iva_no_rec, monto_iva_uso_comun=excluded.monto_iva_uso_comun, monto_total=excluded.monto_total,
		estado_sii=excluded.estado_sii, origen=excluded.origen, actualizado_en=excluded.actualizado_en`)
	if err != nil {
		return 0, err
	}
	defer st.Close()
	for _, d := range docs {
		var fecha, clas any
		if d.FechaDoc != nil {
			fecha = *d.FechaDoc
		}
		if d.Clasificacion != nil {
			clas = *d.Clasificacion
		}
		if _, err := st.Exec(d.RutEmpresa, d.TipoOperacion, d.Periodo, d.TipoDTE, d.Folio, fecha, d.RutContraparte, d.RazonSocial,
			d.MontoExento, d.MontoNeto, d.MontoIVA, d.MontoIVANoRec, d.MontoIVAUsoComun, d.MontoTotal, d.EstadoSII, clas, d.Origen, ahora, ahora); err != nil {
			return 0, err
		}
	}
	return len(docs), tx.Commit()
}

// ---------------------------------------------------------------- cliente

// Cliente es RegistroCompraVenta: con un token (semilla→token o la cookie TOKEN del portal) o
// con las cookies de una sesión por clave (la forma VERIFICADA 2026-10-04).
type Cliente struct {
	Token   string
	Cookies map[string]string
	HTTP    *http.Client
}

func Nuevo(token string, cookies map[string]string) *Cliente {
	return &Cliente{Token: token, Cookies: cookies, HTTP: &http.Client{Transport: salaespera.Transporte(nil), Timeout: 60 * time.Second}}
}

func (c *Cliente) token() (string, error) {
	if len(c.Cookies) > 0 {
		if t := c.Cookies["TOKEN"]; t != "" {
			return t, nil
		}
		return c.Cookies["CSESSIONID"], nil
	}
	if c.Token != "" {
		return c.Token, nil
	}
	return "", errors.New("RegistroCompraVenta necesita un ClienteSII o un token.")
}

// Headers es _headers (modo token).
func Headers(token, num, dv string) map[string]string {
	return map[string]string{"User-Agent": UserAgent, "Cookie": "TOKEN=" + token + ";RUT_NS=" + num + ";DV_NS=" + dv,
		"Content-Type": "application/json;charset=UTF-8", "Accept": "*/*"}
}

func uuid4() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:16])
}

// Payload es _payload: el body exacto del cliente web. `transactionID` vacío → uuid nuevo.
func (c *Cliente) Payload(rutEmpresa, periodo, tipoOperacion, metodo, transactionID string) map[string]any {
	num, dv := SepararRut(rutEmpresa)
	if metodo == "" {
		metodo = Metodos[tipoOperacion]
	}
	conv := ""
	if c.Cookies != nil {
		conv = c.Cookies["CSESSIONID"]
		if conv == "" {
			conv = c.Cookies["TOKEN"]
		}
	}
	if conv == "" {
		conv = c.Token
	}
	if conv == "" {
		conv = num + "-" + dv
	}
	if transactionID == "" {
		transactionID = uuid4()
	}
	data := map[string]any{"rutEmisor": num, "dvEmisor": dv, "ptributario": NormalizarPeriodo(periodo), "estadoContab": "REGISTRO", "operacion": tipoOperacion}
	if strings.HasSuffix(metodo, "Export") {
		data["codTipoDoc"] = 0
		data["estadoContab"] = EstadoExport[tipoOperacion]
		data["accionRecaptcha"] = AccionRecaptcha[tipoOperacion]
		data["tokenRecaptcha"] = TokenRecaptcha
	} else {
		data["busquedaInicial"] = true
	}
	return map[string]any{"metaData": map[string]any{"namespace": namespace(metodo), "conversationId": conv, "transactionId": transactionID, "page": nil}, "data": data}
}

func (c *Cliente) llamar(u string, payload map[string]any, rutEmpresa string) (map[string]any, error) {
	num, dv := SepararRut(rutEmpresa)
	cuerpo, _ := json.Marshal(payload)
	req, err := http.NewRequest("POST", u, bytes.NewReader(cuerpo))
	if err != nil {
		return nil, err
	}
	if len(c.Cookies) > 0 {
		req.Header.Set("User-Agent", UserAgent)
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Accept", "application/json, text/plain, */*")
		req.Header.Set("Origin", "https://www4.sii.cl")
		req.Header.Set("Referer", "https://www4.sii.cl/consdcvinternetui/")
		var ks []string
		for k := range c.Cookies {
			ks = append(ks, k)
		}
		sort.Strings(ks)
		var partes []string
		for _, k := range ks {
			partes = append(partes, k+"="+c.Cookies[k])
		}
		req.Header.Set("Cookie", strings.Join(partes, "; "))
	} else {
		tok, err := c.token()
		if err != nil {
			return nil, err
		}
		for k, v := range Headers(tok, num, dv) {
			req.Header.Set(k, v)
		}
	}
	r, err := c.HTTP.Do(req)
	if err != nil {
		return nil, err
	}
	defer r.Body.Close()
	b, _ := io.ReadAll(r.Body)
	if r.StatusCode >= 400 {
		return nil, fmt.Errorf("RCV HTTP %d: %.300s", r.StatusCode, string(b))
	}
	var data map[string]any
	if err := json.Unmarshal(b, &data); err != nil {
		return nil, fmt.Errorf("la respuesta del RCV no es JSON: %.300s", string(b))
	}
	if est, ok := data["respEstado"].(map[string]any); ok {
		if cod, hay := est["codRespuesta"]; hay && cod != nil && Num(cod) != 0 {
			// codRespuesta 3 = período sin registro: no es error.
			return map[string]any{"data": []any{}, "respEstado": est}, nil
		}
	}
	return data, nil
}

// DescargarPeriodo baja el detalle del RCV de un período, normalizado (no persiste).
func (c *Cliente) DescargarPeriodo(rutEmpresa, periodo, tipoOperacion string) ([]Documento, error) {
	data, err := c.llamar(Endpoints()[tipoOperacion], c.Payload(rutEmpresa, periodo, tipoOperacion, "", ""), rutEmpresa)
	if err != nil {
		return nil, err
	}
	return NormalizarDetalle(rutEmpresa, tipoOperacion, periodo, data, "rcv"), nil
}

// DescargarResumen es descargar_resumen (getResumen): totales por tipo de documento.
func (c *Cliente) DescargarResumen(rutEmpresa, periodo, tipoOperacion string) ([]any, error) {
	data, err := c.llamar(Endpoints()["resumen"], c.Payload(rutEmpresa, periodo, tipoOperacion, "getResumen", ""), rutEmpresa)
	if err != nil {
		return nil, err
	}
	lista, _ := data["data"].([]any)
	if lista == nil {
		lista = []any{}
	}
	return lista, nil
}

// SincronizarPeriodo baja COMPRAS y VENTAS del período y las persiste → conteos.
func (c *Cliente) SincronizarPeriodo(db *sql.DB, rutEmpresa, periodo string) (map[string]int, error) {
	compras, err := c.DescargarPeriodo(rutEmpresa, periodo, Compra)
	if err != nil {
		return nil, err
	}
	ventas, err := c.DescargarPeriodo(rutEmpresa, periodo, Venta)
	if err != nil {
		return nil, err
	}
	n, err := GuardarDocumentos(db, append(append([]Documento{}, compras...), ventas...))
	if err != nil {
		return nil, err
	}
	return map[string]int{"compras": len(compras), "ventas": len(ventas), "guardados": n}, nil
}
