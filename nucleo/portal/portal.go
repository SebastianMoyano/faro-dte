// Package portal es core/sii_portal.py: el PORTAL web del SII (cvc_cgi/dte) para timbraje y
// folios, autenticado por certificado digital (mutual-TLS contra herculesr CAutInicio.cgi) o
// por RUT + clave. Scraping por HTTP puro, sin navegador. Las páginas se parsean con
// funciones puras (parsear*), medidas contra Python con tests/dorado/portal/.
//
// Verificado contra Maullín en Python: autenticar, consultar_timbrajes, listar_anulables,
// anular_folios, situacion_folios, solicitar_folios (incluido el paso oculto CON_CREDITO=0 /
// CON_AJUSTE=0 que el navegador agrega y el form no expone).
package portal

import (
	"crypto/tls"
	"errors"
	"fmt"
	"html"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"dtechile/nucleo/salaespera"
)

const (
	UserAgent         = "Mozilla/5.0 (compatible; DTE-Portal/1.0)"
	urlAuthCert       = "https://herculesr.sii.cl/cgi_AUT2000/CAutInicio.cgi"
	urlAuthClave      = "https://zeusr.sii.cl/cgi_AUT2000/CAutInicio.cgi"
	BaseCertificacion = "https://maullin.sii.cl/cvc_cgi/dte"
	BaseProduccion    = "https://palena.sii.cl/cvc_cgi/dte"
	urlSelEmpresa     = "https://www1.sii.cl/cgi-bin/Portal001/mipeSelEmpresa.cgi"
)

// BaseDe devuelve la base del portal para un ambiente.
func BaseDe(ambiente string) string {
	if ambiente == "produccion" {
		return BaseProduccion
	}
	return BaseCertificacion
}

var reTags, reBlancos = regexp.MustCompile(`<[^>]+>`), regexp.MustCompile(`\s+`)

// texto es _texto: HTML → texto plano colapsado.
func texto(h string) string { return reBlancos.ReplaceAllString(reTags.ReplaceAllString(h, " "), " ") }

// RangoFolios es un rango de folios autorizado (timbraje) para un tipo.
type RangoFolios struct {
	TipoDTE    int    `json:"tipo_dte"`
	FolioDesde int    `json:"folio_desde"`
	FolioHasta int    `json:"folio_hasta"`
	Cantidad   int    `json:"cantidad"`
	Dia        string `json:"dia"`
	Mes        string `json:"mes"`
	Ano        string `json:"ano"`
	Mandatario string `json:"mandatario"`
}

func (r RangoFolios) Fecha() string { return r.Dia + "-" + r.Mes + "-" + r.Ano }

type ResultadoAnulacion struct {
	OK         bool   `json:"ok"`
	Mensaje    string `json:"mensaje"`
	FolioDesde int    `json:"folio_desde"`
	FolioHasta int    `json:"folio_hasta"`
}

// DocumentoAutorizado: un tipo de DTE que un contribuyente está autorizado a emitir.
type DocumentoAutorizado struct {
	Codigo             int    `json:"codigo"`
	Descripcion        string `json:"descripcion"`
	AutorizadoDesde    string `json:"autorizado_desde"`
	DesautorizadoDesde string `json:"desautorizado_desde"`
}

// EmpresaAutorizadaDTE: resumen de autorización DTE (consulta pública ee_empresa_rut).
type EmpresaAutorizadaDTE struct {
	Rut               string                `json:"rut"`
	RazonSocial       string                `json:"razon_social"`
	NroResolucion     string                `json:"nro_resolucion"`
	FechaResolucion   string                `json:"fecha_resolucion"`
	DireccionRegional string                `json:"direccion_regional"`
	Documentos        []DocumentoAutorizado `json:"documentos"`
}

func (e *EmpresaAutorizadaDTE) Autoriza(tipo int) bool {
	for _, d := range e.Documentos {
		if d.Codigo == tipo {
			return d.DesautorizadoDesde == ""
		}
	}
	return false
}

// DatosSoftware es lo que ad_empresa2 muestra del software de emisión registrado.
type DatosSoftware struct {
	Software        string `json:"software"`
	Resolucion      string `json:"resolucion"`
	FechaResolucion string `json:"fecha_resolucion"`
	MailAdmin       string `json:"mail_admin"`
	Propio          bool   `json:"propio"`      // SII = sistema gratuito
	Certificado     bool   `json:"certificado"` // resolución "0" = certificación sin finalizar
}

type Empresa struct {
	Rut         string `json:"rut"`
	RazonSocial string `json:"razon_social"`
}

// SituacionTipo es situacion_folios[tipo].
type SituacionTipo struct {
	Bloqueado    bool `json:"bloqueado"`
	PuedeTimbrar bool `json:"puede_timbrar"`
}

// Cliente es PortalSII: sesión por cookies (se mandan a todos los hosts, como Python).
type Cliente struct {
	Base             string
	MaxFoliosPorTipo int
	Cookies          map[string]string
	RutSesion        string
	HTTP             *http.Client
	tlsCert          *tls.Certificate
	solicitudes      map[string]int
}

// Nuevo crea el cliente; certPEM/keyPEM pueden ir vacíos si solo se usa clave.
func Nuevo(certPEM, keyPEM []byte, base string) (*Cliente, error) {
	c := &Cliente{Base: base, MaxFoliosPorTipo: 3, solicitudes: map[string]int{}}
	if c.Base == "" {
		c.Base = BaseCertificacion
	}
	tr := &http.Transport{TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12}}
	if len(certPEM) > 0 && len(keyPEM) > 0 {
		cert, err := tls.X509KeyPair(certPEM, keyPEM)
		if err != nil {
			return nil, fmt.Errorf("certificado PEM: %w", err)
		}
		c.tlsCert = &cert
		tr.TLSClientConfig.Certificates = []tls.Certificate{cert}
	}
	c.HTTP = &http.Client{Transport: salaespera.Transporte(tr), Timeout: 40 * time.Second}
	return c, nil
}

// pedir hace la petición con reintentos ante 429/5xx o red (ClienteReintentos) y absorbe las
// cookies que lleguen (de cualquier host: las de .sii.cl sirven en maullin, palena y www1).
func (c *Cliente) pedir(metodo, u string, form url.Values) (string, []byte, error) {
	var ultimo error
	for intento := 0; intento < 4; intento++ {
		var cuerpo io.Reader
		if form != nil {
			cuerpo = strings.NewReader(form.Encode())
		}
		req, err := http.NewRequest(metodo, u, cuerpo)
		if err != nil {
			return "", nil, err
		}
		req.Header.Set("User-Agent", UserAgent)
		if form != nil {
			req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
		}
		if len(c.Cookies) > 0 {
			var partes []string
			for k, v := range c.Cookies {
				partes = append(partes, k+"="+v)
			}
			sort.Strings(partes)
			req.Header.Set("Cookie", strings.Join(partes, "; "))
		}
		r, err := c.HTTP.Do(req)
		if err != nil {
			ultimo = err
		} else {
			b, _ := io.ReadAll(r.Body)
			r.Body.Close()
			b = decodificar(b, r.Header.Get("Content-Type"))
			if c.Cookies == nil {
				c.Cookies = map[string]string{}
			}
			for _, ck := range r.Cookies() {
				c.Cookies[ck.Name] = ck.Value
			}
			if r.StatusCode != 429 && r.StatusCode < 500 {
				return r.Status, b, nil
			}
			ultimo = fmt.Errorf("HTTP %d en %s", r.StatusCode, u)
		}
		if intento < 3 {
			time.Sleep(time.Duration(500*(1<<intento)) * time.Millisecond)
		}
	}
	return "", nil, ultimo
}

// decodificar es lo que hace `resp.text` de httpx: el portal responde en ISO-8859-1 (lo diga
// o no el Content-Type), así que los bytes que no sean UTF-8 válido se leen como Latin-1.
// Sin esto, `N°` y las tildes de las páginas no calzan con las expresiones regulares.
func decodificar(b []byte, contentType string) []byte {
	ct := strings.ToLower(contentType)
	latin := strings.Contains(ct, "iso-8859-1") || strings.Contains(ct, "windows-1252") || strings.Contains(ct, "latin")
	if !latin && (strings.Contains(ct, "utf-8") || utf8.Valid(b)) {
		return b
	}
	out := make([]byte, 0, len(b)+len(b)/8)
	for _, c := range b {
		out = utf8.AppendRune(out, rune(c))
	}
	return out
}

func (c *Cliente) post(u string, datos map[string]string) (string, error) {
	f := url.Values{}
	for k, v := range datos {
		f.Set(k, v)
	}
	_, b, err := c.pedir("POST", u, f)
	return string(b), err
}

// Autenticar es autenticar(): login por certificado (mutual-TLS) → cookies de sesión.
func (c *Cliente) Autenticar(referencia string) (map[string]string, error) {
	if c.tlsCert == nil {
		return nil, errors.New("Se requiere certificado (cert_pem/key_pem) para autenticar por firma. Usa AutenticarConClave() si sólo tienes RUT+clave.")
	}
	if referencia == "" {
		referencia = c.Base + "/of_solicita_folios"
	}
	if _, err := c.post(urlAuthCert, map[string]string{"referencia": referencia}); err != nil {
		return nil, err
	}
	c.RutSesion = c.Cookies["RUT_NS"]
	return c.Cookies, nil
}

// AutenticarConClave es autenticar_con_clave(): RUT + clave tributaria (Mi SII).
func (c *Cliente) AutenticarConClave(rut, clave, referencia string) (map[string]string, error) {
	rut = strings.ReplaceAll(rut, ".", "")
	n, dv, ok := strings.Cut(rut, "-")
	if !ok {
		return nil, fmt.Errorf("RUT sin DV: %q", rut)
	}
	if referencia == "" {
		referencia = "https://misiir.sii.cl/cgi_misii/siihome.cgi"
	}
	if _, err := c.post(urlAuthClave, map[string]string{"rut": n, "dv": dv, "rutcntr": rut, "clave": clave, "referencia": referencia, "411": ""}); err != nil {
		return nil, err
	}
	c.RutSesion = c.Cookies["RUT_NS"]
	if c.RutSesion == "" {
		c.RutSesion = n
	}
	return c.Cookies, nil
}

func (c *Cliente) sesion() error {
	if len(c.Cookies) == 0 {
		_, err := c.Autenticar("")
		return err
	}
	return nil
}

func SepararRut(rut string) (string, string) {
	rut = strings.ReplaceAll(rut, ".", "")
	n, dv, _ := strings.Cut(rut, "-")
	return n, dv
}

// ---------------------------------------------------------------- timbrajes

var reTimbraje = regexp.MustCompile(`(\d{2})-(\d{2})-(\d{4})\s+(\d+)\s+(\d+)\s+(\d+)\s+([A-ZÑÁÉÍÓÚ ]+?)(\d{2}-\d{2}-\d{4}|Anterior|Siguiente|$)`)

// ParsearTimbrajes es el parseo de consultar_timbrajes sobre el texto plano. (Python usa un
// lookahead; aquí el terminador se captura y la búsqueda sigue desde él.)
func ParsearTimbrajes(h string, tipo int) []RangoFolios {
	p := texto(h)
	var rangos []RangoFolios
	pos := 0
	for pos <= len(p) {
		m := reTimbraje.FindStringSubmatchIndex(p[pos:])
		if m == nil {
			break
		}
		g := func(i int) string { return p[pos+m[2*i] : pos+m[2*i+1]] }
		cant, _ := strconv.Atoi(g(4))
		desde, _ := strconv.Atoi(g(5))
		hasta, _ := strconv.Atoi(g(6))
		rangos = append(rangos, RangoFolios{TipoDTE: tipo, FolioDesde: desde, FolioHasta: hasta, Cantidad: cant,
			Dia: g(1), Mes: g(2), Ano: g(3), Mandatario: strings.TrimSpace(g(7))})
		// seguir desde el terminador (no consumido, como el lookahead)
		if m[16] == m[17] { // $
			break
		}
		pos += m[16]
	}
	vistos := map[[2]int]bool{}
	out := []RangoFolios{}
	for _, r := range rangos {
		k := [2]int{r.FolioDesde, r.FolioHasta}
		if !vistos[k] {
			vistos[k] = true
			out = append(out, r)
		}
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i].FolioDesde < out[j].FolioDesde })
	return out
}

// ConsultarTimbrajes es consultar_timbrajes: historial de rangos por tipo (of_consulta2_folio).
func (c *Cliente) ConsultarTimbrajes(rut string, tipo int) ([]RangoFolios, error) {
	if err := c.sesion(); err != nil {
		return nil, err
	}
	n, dv := SepararRut(rut)
	h, err := c.post(c.Base+"/of_consulta2_folio", map[string]string{"RUT_EMP": n, "DV_EMP": dv, "PAGINA": "1", "COD_DOCTO": strconv.Itoa(tipo), "ACEPTAR": "Consultar"})
	if err != nil {
		return nil, err
	}
	return ParsearTimbrajes(h, tipo), nil
}

// ------------------------------------------------------ empresa autorizada

var reDocAutorizado = regexp.MustCompile(`\b(\d{2,3})\s+([A-ZÑ][A-ZÑ0-9 ./'"]+?)\s+(\d{2}-\d{2}-\d{4})\s*(\d{2}-\d{2}-\d{4})?`)

// ParsearEmpresaAutorizada es el parseo de consultar_empresa_autorizada (nil si no está
// autorizada). Los rótulos se buscan con las mismas expresiones (y sus alternancias) que Python.
func ParsearEmpresaAutorizada(h, rut string) *EmpresaAutorizadaDTE {
	n, dv := SepararRut(rut)
	p := html.UnescapeString(texto(h))
	if !strings.Contains(strings.ToLower(p), "autorizada la emisi") {
		return nil
	}
	campo := func(rotulo, sig string) string {
		re := regexp.MustCompile(rotulo + `\s+(.+?)\s+` + sig)
		m := re.FindStringSubmatch(p)
		if m == nil || len(m) < 2 {
			return ""
		}
		return strings.TrimSpace(m[1])
	}
	razon := campo("Nombres", `N°|Nro|Resoluci`)
	nrores := campo(`N°?\s*Resoluci[oó]n`, `Fecha`)
	fchres := campo(`Fecha\s+Resoluci[oó]n`, `Direcci`)
	dirreg := campo(`Direcci[oó]n\s+Regional`, `El contribuyente|autorizada`)
	docs := []DocumentoAutorizado{}
	seg := p
	if i := strings.Index(strings.ToLower(p), "siguientes documentos"); i >= 0 {
		seg = p[i:]
	} else {
		seg = p[len(p)-1:] // find() == -1 → p[-1:] (el último carácter)
	}
	for _, m := range reDocAutorizado.FindAllStringSubmatch(seg, -1) {
		cod, _ := strconv.Atoi(m[1])
		docs = append(docs, DocumentoAutorizado{Codigo: cod, Descripcion: strings.TrimSpace(m[2]), AutorizadoDesde: m[3], DesautorizadoDesde: m[4]})
	}
	return &EmpresaAutorizadaDTE{Rut: n + "-" + dv, RazonSocial: razon, NroResolucion: nrores, FechaResolucion: fchres, DireccionRegional: dirreg, Documentos: docs}
}

// ConsultarEmpresaAutorizada es consultar_empresa_autorizada (PÚBLICA, sin sesión).
func (c *Cliente) ConsultarEmpresaAutorizada(rut, base string) (*EmpresaAutorizadaDTE, error) {
	if base == "" {
		base = c.Base
	}
	n, dv := SepararRut(rut)
	publico := &Cliente{HTTP: &http.Client{Transport: salaespera.Transporte(nil), Timeout: 25 * time.Second}}
	h, err := publico.post(base+"/ee_empresa_rut", map[string]string{"CNSLT": "R", "RUT_EMP": n, "DV_EMP": dv, "ACEPTAR": "Consultar"})
	if err != nil {
		return nil, err
	}
	return ParsearEmpresaAutorizada(h, rut), nil
}

// ------------------------------------------------------------- software

const reResol = `Resoluci(?:&oacute;|[oó])n`

func valorEtiqueta(h, label string) string {
	re := regexp.MustCompile(`(?i)<font[^>]*>\s*` + label + `[^<]*</font>\s*</td>\s*<td[^>]*>\s*<font[^>]*>\s*(?:&nbsp;)?\s*([^<]*?)\s*</font>`)
	m := re.FindStringSubmatch(h)
	if m == nil {
		return ""
	}
	return html.UnescapeString(strings.TrimSpace(m[1]))
}

// ParsearDatosSoftware es el parseo de datos_software (ad_empresa2).
func ParsearDatosSoftware(h string) DatosSoftware {
	sw := valorEtiqueta(h, `Nombre del Software \(\*\)`)
	res := valorEtiqueta(h, reResol)
	return DatosSoftware{Software: sw, Resolucion: res, FechaResolucion: valorEtiqueta(h, `Fecha `+reResol), MailAdmin: valorEtiqueta(h, `Mail Contacto`),
		Propio: sw != "" && strings.ToUpper(sw) != "SII", Certificado: res != "" && res != "0"}
}

// DatosSoftwareDe es datos_software: requiere sesión; `base` permite otro ambiente con las
// mismas cookies.
func (c *Cliente) DatosSoftwareDe(rut, base string) (DatosSoftware, error) {
	if err := c.sesion(); err != nil {
		return DatosSoftware{}, err
	}
	if base == "" {
		base = c.Base
	}
	n, dv := SepararRut(rut)
	h, err := c.post(base+"/ad_empresa2", map[string]string{"RUT_EMP": n, "DV_EMP": dv})
	if err != nil {
		return DatosSoftware{}, err
	}
	return ParsearDatosSoftware(h), nil
}

// ------------------------------------------------------ empresas asociadas

var reOption, reRutExacto, reColaRut = regexp.MustCompile(`(?i)<option[^>]*value="?([\dkK.\-]+)"?[^>]*>([^<]+)`), regexp.MustCompile(`^\d{7,8}-[\dkK]$`), regexp.MustCompile(`[\s,]*\d[\d.\-kK]*\s*$`)

// ParsearEmpresasAsociadas es el parseo de empresas_asociadas (mipeSelEmpresa).
func ParsearEmpresasAsociadas(h string) []Empresa {
	out := []Empresa{}
	for _, m := range reOption.FindAllStringSubmatch(h, -1) {
		rut := strings.TrimSpace(m[1])
		if !reRutExacto.MatchString(rut) {
			continue
		}
		razon := strings.TrimSpace(strings.TrimRight(strings.TrimSpace(reColaRut.ReplaceAllString(m[2], "")), ","))
		out = append(out, Empresa{Rut: rut, RazonSocial: razon})
	}
	return out
}

// EmpresasAsociadas: todas las empresas que el titular de la sesión puede operar.
func (c *Cliente) EmpresasAsociadas() ([]Empresa, error) {
	if err := c.sesion(); err != nil {
		return nil, err
	}
	_, b, err := c.pedir("GET", urlSelEmpresa, nil)
	if err != nil {
		return nil, err
	}
	return ParsearEmpresasAsociadas(string(b)), nil
}

// ------------------------------------------------------------- anulables

var reForms, reInputNV = regexp.MustCompile(`(?is)<form\b.*?</form>`), regexp.MustCompile(`(?i)<input[^>]*name=["']?(\w+)["']?[^>]*?value=["']?([^"'>]*)`)

// ParsearAnulables es el parseo de listar_anulables (af_anular2).
func ParsearAnulables(h string, tipo int) []RangoFolios {
	out := []RangoFolios{}
	for _, f := range reForms.FindAllString(h, -1) {
		d := map[string]string{}
		for _, m := range reInputNV.FindAllStringSubmatch(f, -1) {
			d[m[1]] = m[2]
		}
		ini, okI := d["FOLIO_INI"]
		fin, okF := d["FOLIO_FIN"]
		if okI && okF {
			desde, _ := strconv.Atoi(ini)
			hasta, _ := strconv.Atoi(fin)
			cant, _ := strconv.Atoi(d["CANT_DOCTOS"])
			out = append(out, RangoFolios{TipoDTE: tipo, FolioDesde: desde, FolioHasta: hasta, Cantidad: cant, Dia: d["DIA"], Mes: d["MES"], Ano: d["ANO"]})
		}
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i].FolioDesde < out[j].FolioDesde })
	return out
}

func (c *Cliente) ListarAnulables(rut string, tipo int) ([]RangoFolios, error) {
	if err := c.sesion(); err != nil {
		return nil, err
	}
	n, dv := SepararRut(rut)
	h, err := c.post(c.Base+"/af_anular2", map[string]string{"RUT_EMP": n, "DV_EMP": dv, "PAGINA": "1", "COD_DOCTO": strconv.Itoa(tipo), "ACEPTAR": "Consultar"})
	if err != nil {
		return nil, err
	}
	return ParsearAnulables(h, tipo), nil
}

// InterpretarAnulacion es la lectura de la respuesta de af_anular.
func InterpretarAnulacion(h string, desde, hasta int) ResultadoAnulacion {
	p := strings.ToLower(texto(h))
	switch {
	case strings.Contains(p, "ha autorizado la anulaci"):
		return ResultadoAnulacion{true, "Anulación autorizada por el SII", desde, hasta}
	case strings.Contains(p, "recepcionado"):
		return ResultadoAnulacion{false, "Al menos un folio fue recepcionado (no anulable)", desde, hasta}
	case strings.Contains(p, "anulado anteriormente"):
		return ResultadoAnulacion{false, "El rango ya fue anulado anteriormente", desde, hasta}
	}
	return ResultadoAnulacion{false, "Respuesta no reconocida del SII", desde, hasta}
}

// AnularFolios anula el sub-rango [desde, hasta] (todos NO recepcionados; sesión del mandatario).
func (c *Cliente) AnularFolios(rut string, tipo, desde, hasta int, motivo string) (ResultadoAnulacion, error) {
	if motivo == "" {
		motivo = "Folios no utilizados"
	}
	rangos, err := c.ListarAnulables(rut, tipo)
	if err != nil {
		return ResultadoAnulacion{}, err
	}
	var padre *RangoFolios
	for i := range rangos {
		if rangos[i].FolioDesde <= desde && hasta <= rangos[i].FolioHasta {
			padre = &rangos[i]
			break
		}
	}
	if padre == nil {
		return ResultadoAnulacion{false, "No hay rango anulable que contenga esos folios", desde, hasta}, nil
	}
	n, dv := SepararRut(rut)
	if _, err := c.post(c.Base+"/af_anular3", map[string]string{"RUT_EMP": n, "DV_EMP": dv, "DIA": padre.Dia, "MES": padre.Mes, "ANO": padre.Ano,
		"COD_DOCTO": strconv.Itoa(tipo), "FOLIO_INI": strconv.Itoa(padre.FolioDesde), "FOLIO_FIN": strconv.Itoa(padre.FolioHasta), "CANT_DOCTOS": strconv.Itoa(padre.Cantidad)}); err != nil {
		return ResultadoAnulacion{}, err
	}
	h, err := c.post(c.Base+"/af_anular", map[string]string{"FOLIO_INI_A": strconv.Itoa(desde), "FOLIO_FIN_A": strconv.Itoa(hasta), "MOTIVO": motivo, "RUT_EMP": n, "DV_EMP": dv,
		"COD_DOCTO": strconv.Itoa(tipo), "FOLIO_INI": strconv.Itoa(padre.FolioDesde), "FOLIO_FIN": strconv.Itoa(padre.FolioHasta)})
	if err != nil {
		return ResultadoAnulacion{}, err
	}
	return InterpretarAnulacion(h, desde, hasta), nil
}

// ------------------------------------------------------ situación de folios

var TiposSituacion = []int{33, 34, 39, 41, 43, 46, 52, 56, 61}

// Bloqueado: ¿la página dice NO SE AUTORIZA?
func Bloqueado(h string) bool { return strings.Contains(strings.ToUpper(texto(h)), "NO SE AUTORIZA") }

// SituacionFolios usa el paso de PROCESAMIENTO real (of_solicita_folios_dcto), donde el SII
// aplica el control de stock (of_confirma_folio es preview y engaña).
func (c *Cliente) SituacionFolios(rut string, tipos []int) (map[int]SituacionTipo, error) {
	if err := c.sesion(); err != nil {
		return nil, err
	}
	if len(tipos) == 0 {
		tipos = TiposSituacion
	}
	n, dv := SepararRut(rut)
	out := map[int]SituacionTipo{}
	for _, tp := range tipos {
		h, err := c.post(c.Base+"/of_solicita_folios_dcto", map[string]string{"RUT_EMP": n, "DV_EMP": dv, "COD_DOCTO": strconv.Itoa(tp), "FOLIO_INICIAL": "", "CANT_DOCTOS": ""})
		if err != nil {
			return nil, err
		}
		b := Bloqueado(h)
		out[tp] = SituacionTipo{Bloqueado: b, PuedeTimbrar: !b}
	}
	return out, nil
}

// ------------------------------------------------------ solicitar/generar CAF

var reInputAttrs, reNombre, reValor = regexp.MustCompile(`(?i)<input\b([^>]*)>`), regexp.MustCompile(`name=["']?(\w+)`), regexp.MustCompile(`value=["']?([^"'>]*)`)

// CamposForm es _campos_form: {name: value} de los <input> del <form> cuyo action contiene
// el texto (nil si no hay tal form).
func CamposForm(h, actionContiene string) map[string]string {
	re := regexp.MustCompile(`(?is)<form\b[^>]*` + regexp.QuoteMeta(actionContiene) + `[^>]*>(.*?)</form>`)
	m := re.FindStringSubmatch(h)
	if m == nil {
		return nil
	}
	campos := map[string]string{}
	for _, im := range reInputAttrs.FindAllStringSubmatch(m[1], -1) {
		nm := reNombre.FindStringSubmatch(im[1])
		if nm == nil {
			continue
		}
		v := ""
		if vl := reValor.FindStringSubmatch(im[1]); vl != nil {
			v = vl[1]
		}
		campos[nm[1]] = v
	}
	return campos
}

// InfoSolicitud es el `info` de solicitar_folios.
type InfoSolicitud struct {
	RateLimited         bool   `json:"rate_limited,omitempty"`
	SolicitadosEnSesion int    `json:"solicitados_en_sesion,omitempty"`
	Max                 int    `json:"max,omitempty"`
	Bloqueado           bool   `json:"bloqueado"`
	Mensaje             string `json:"mensaje,omitempty"`
	Error               string `json:"error,omitempty"`
	Folio               string `json:"folio,omitempty"`
	FolioDesde          int    `json:"folio_desde,omitempty"`
	FolioHasta          int    `json:"folio_hasta,omitempty"`
	CAFBytes            int    `json:"caf_bytes,omitempty"`
}

// SolicitarFolios solicita, genera y descarga un CAF (100% HTTP). Devuelve (caf, info); caf
// nil con la razón en info. Guardarraíles anti-acaparamiento: tope blando por (rut, tipo) por
// sesión (forzar lo salta) y chequeo previo de bloqueo (verificarBloqueo).
func (c *Cliente) SolicitarFolios(rut string, tipo, cantidad int, verificarBloqueo, forzar bool) ([]byte, InfoSolicitud, error) {
	if cantidad <= 0 {
		cantidad = 1
	}
	clave := rut + "/" + strconv.Itoa(tipo)
	ya := c.solicitudes[clave]
	if !forzar && ya+cantidad > c.MaxFoliosPorTipo {
		return nil, InfoSolicitud{RateLimited: true, SolicitadosEnSesion: ya, Max: c.MaxFoliosPorTipo,
			Mensaje: fmt.Sprintf("Tope de %d folios T%d/sesión alcanzado. El SII bloquea el timbraje si acumulas folios sin usar: emítelos o anúlalos antes de pedir más (o usa forzar=True bajo tu responsabilidad).", c.MaxFoliosPorTipo, tipo)}, nil
	}
	if verificarBloqueo {
		sit, err := c.SituacionFolios(rut, []int{tipo})
		if err != nil {
			return nil, InfoSolicitud{}, err
		}
		if sit[tipo].Bloqueado {
			return nil, InfoSolicitud{Bloqueado: true, Mensaje: fmt.Sprintf("El SII ya bloquea el timbraje T%d (anti-acaparamiento). Emite o anula folios sin usar de ese tipo antes de reintentar.", tipo)}, nil
		}
	}
	if err := c.sesion(); err != nil {
		return nil, InfoSolicitud{}, err
	}
	n, dv := SepararRut(rut)
	conf, err := c.post(c.Base+"/of_confirma_folio", map[string]string{"RUT_EMP": n, "DV_EMP": dv, "COD_DOCTO": strconv.Itoa(tipo), "CANT_DOCTOS": strconv.Itoa(cantidad),
		"FOLIO_INICIAL": "", "AFECTO_IVA": "", "ANOTACION": "", "CON_CREDITO": "", "CON_AJUSTE": "", "FACTOR": ""})
	if err != nil {
		return nil, InfoSolicitud{}, err
	}
	campos := CamposForm(conf, "of_genera_folio")
	// BOLETAS (y a veces facturas): paso intermedio "Confirmar Folio Inicial" que postea de
	// nuevo a of_confirma_folio; recién ese responde el form de generación.
	if _, ok := campos["FOLIO_INI"]; !ok {
		inter := CamposForm(conf, "of_confirma_folio")
		if inter != nil && inter["FOLIO_INICIAL"] != "" && strings.Contains(inter["ACEPTAR"], "Confirmar") {
			if _, ok := inter["CON_CREDITO"]; !ok {
				inter["CON_CREDITO"] = "0"
			}
			if _, ok := inter["CON_AJUSTE"]; !ok {
				inter["CON_AJUSTE"] = "0"
			}
			conf, err = c.post(c.Base+"/of_confirma_folio", inter)
			if err != nil {
				return nil, InfoSolicitud{}, err
			}
			campos = CamposForm(conf, "of_genera_folio")
		}
	}
	if _, ok := campos["FOLIO_INI"]; !ok {
		return nil, InfoSolicitud{Bloqueado: Bloqueado(conf), Error: "of_confirma_folio no ofreció folio"}, nil
	}
	// el navegador agrega estos dos; el form del confirma no los expone.
	if _, ok := campos["CON_CREDITO"]; !ok {
		campos["CON_CREDITO"] = "0"
	}
	if _, ok := campos["CON_AJUSTE"]; !ok {
		campos["CON_AJUSTE"] = "0"
	}
	gen, err := c.post(c.Base+"/of_genera_folio", campos)
	if err != nil {
		return nil, InfoSolicitud{}, err
	}
	if Bloqueado(gen) {
		return nil, InfoSolicitud{Bloqueado: true, Folio: campos["FOLIO_INI"]}, nil
	}
	arch := CamposForm(gen, "of_genera_archivo")
	if arch == nil {
		return nil, InfoSolicitud{Error: "of_genera_folio no devolvió el form de descarga", Folio: campos["FOLIO_INI"]}, nil
	}
	f := url.Values{}
	for k, v := range arch {
		f.Set(k, v)
	}
	_, caf, err := c.pedir("POST", c.Base+"/of_genera_archivo", f)
	if err != nil {
		return nil, InfoSolicitud{}, err
	}
	c.solicitudes[clave] = ya + cantidad
	desde, _ := strconv.Atoi(arch["FOLIO_INI"])
	hasta, _ := strconv.Atoi(arch["FOLIO_FIN"])
	info := InfoSolicitud{Bloqueado: false, FolioDesde: desde, FolioHasta: hasta, CAFBytes: len(caf)}
	if !strings.Contains(string(caf), "<CAF") {
		return nil, info, nil
	}
	return caf, info, nil
}
