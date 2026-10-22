// Package siiengine es el SII engine del núcleo: la app operando los sistemas del propio SII
// por la empresa (hoy el portal MIPYME de facturas, `www1.sii.cl/cgi-bin/Portal001`; después
// eboleta). Réplica de core/sii_mipyme.py, por segmentos (docs/PLAN-PORT.md, fase 6).
//
// Segmento 1 (este archivo y lectura.go/importar.go): sesión por clave tributaria, selección
// de empresa, cierre real de la sesión, historial de emitidos, PDF, XML por firma central,
// form pre-llenado e importación al almacén. Todo de SOLO LECTURA en el SII: no gasta folios.
//
// PRODUCCIÓN: no hay sandbox. Reglas que se heredan de Python: una sesión por corrida y cerrar
// siempre (tope ~10 por RUT, memoria `sii-cerrar-sesion`); nada se reintenta; las claves nunca
// viajan por argumento ni se escriben en logs.
package siiengine

import (
	"bytes"
	"compress/flate"
	"compress/gzip"
	"compress/zlib"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"regexp"
	"sort"
	"strings"
	"time"

	"dtechile/nucleo/salaespera"
)

const (
	Base          = "https://www1.sii.cl/cgi-bin/Portal001"
	urlLogin      = "https://zeusr.sii.cl/cgi_AUT2000/CAutInicio.cgi"
	urlLogout     = "https://zeusr.sii.cl/cgi_AUT2000/autTermino.cgi" // seguir el 302, si no la sesión sigue viva
	urlSIIHome    = "https://misiir.sii.cl/cgi_misii/siihome.cgi"
	refererLogin  = "https://zeusr.sii.cl/AUT2000/InicioAutenticacion/IngresoRutClave.html"
	opcionEmision = "OPCION=34&TIPO=4"

	TipoFacturaExenta = 34
	TipoNotaCredito   = 61
	TipoNotaDebito    = 56

	// Lo que el JS del portal (signXmlCompatible) inserta antes de <Documento al firmar.
	ComentarioFirma = "<!-- O MacIntel Chrome 154 central VERSION: v20170209 -->"

	ua     = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
	accept = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
)

// Errores por clase (los de core/errors.py que usa el módulo Python).
var (
	ErrValidacion    = errors.New("validación")
	ErrAutenticacion = errors.New("autenticación")
	ErrRechazo       = errors.New("el portal rechazó la petición")
	ErrSII           = errors.New("portal MIPYME")
)

// Cliente es una sesión contra el portal. Patrón: Nuevo → IniciarSesion → SeleccionarEmpresa →
// (lecturas) → Cerrar, siempre con `defer cli.Cerrar()`.
type Cliente struct {
	RutUsuario string // sin puntos, con DV
	Empresa    string // sin puntos, con DV; vacío hasta SeleccionarEmpresa
	Timeout    time.Duration
	Pausa      time.Duration // entre páginas del historial y descargas (no martillar el portal)
	// RaizStorage es la carpeta base de la evidencia (mipyme_fallidos/); "" = "storage".
	RaizStorage string
	// Log recibe los avisos que Python manda al logger (nil = silencio). Nunca lleva claves.
	Log func(format string, a ...any)
	// Trazas, si no es nil, recibe las páginas crudas de los dry-runs (form.html, preview.html,
	// nc_anulacion.html, corr_form.html, corr_preview.html): sirven de fixtures de paridad.
	Trazas map[string]string

	claveTrib, claveCert string
	jar                  *cookiejar.Jar
	http                 *http.Client // sin seguir redirects (como httpx follow_redirects=False)
	autenticado          bool
	menuHTML             string
}

// Nuevo valida el RUT y prepara la sesión. La clave del certificado solo hace falta para la
// firma central (XML de lo emitido); puede ir vacía para listar e importar PDF.
func Nuevo(rutUsuario, claveTributaria, claveCertificado string) (*Cliente, error) {
	if !ValidarRut(rutUsuario) {
		return nil, fmt.Errorf("%w: RUT de usuario inválido: %s", ErrValidacion, rutUsuario)
	}
	jar, _ := cookiejar.New(nil)
	c := &Cliente{
		RutUsuario: FormatearRut(rutUsuario, false),
		Timeout:    40 * time.Second, Pausa: 700 * time.Millisecond,
		claveTrib: claveTributaria, claveCert: claveCertificado, jar: jar,
	}
	c.http = &http.Client{
		Transport:     salaespera.Transporte(nil),
		Jar:           jar,
		Timeout:       c.Timeout,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
	return c, nil
}

// --------------------------------------------------------------------------- plumbing

type respuesta struct {
	Status int
	Cuerpo []byte
	URL    string
	Header http.Header
}

func (r *respuesta) Texto() string { return Latin1(r.Cuerpo) }

func (r *respuesta) rechazado() bool {
	return r.Status != 200 || bytes.Contains(r.Cuerpo, []byte("Request Rejected"))
}

type opciones struct {
	data       []Par  // POST form, codificado latin-1 en orden
	content    []byte // POST crudo (gana sobre data)
	params     []Par  // query string
	referer    string
	origin     string
	navegacion bool // true = navegación de documento; false = XHR
	extra      map[string]string
}

func (c *Cliente) req(metodo, u string, o opciones) (*respuesta, error) {
	if len(o.params) > 0 {
		q, err := CodificarLatin1(o.params)
		if err != nil {
			return nil, fmt.Errorf("%w: %v", ErrValidacion, err)
		}
		u += "?" + string(q)
	}
	cuerpo := o.content
	if cuerpo == nil && o.data != nil {
		b, err := CodificarLatin1(o.data)
		if err != nil {
			return nil, fmt.Errorf("%w: %v", ErrValidacion, err)
		}
		cuerpo = b
	}
	var body io.Reader
	if cuerpo != nil {
		body = bytes.NewReader(cuerpo)
	}
	req, err := http.NewRequest(metodo, u, body)
	if err != nil {
		return nil, err
	}
	h := req.Header
	h.Set("User-Agent", ua)
	h.Set("Accept", accept)
	h.Set("Accept-Encoding", "gzip, deflate")
	h.Set("Accept-Language", "es-CL,es;q=0.9,en;q=0.8")
	h.Set("sec-ch-ua", `"Chromium";v="152", "Not?A_Brand";v="24", "Google Chrome";v="152"`)
	h.Set("sec-ch-ua-mobile", "?0")
	h.Set("sec-ch-ua-platform", `"macOS"`)
	h.Set("Upgrade-Insecure-Requests", "1")
	h.Set("Sec-Fetch-Site", "same-origin")
	if o.navegacion {
		h.Set("Sec-Fetch-Mode", "navigate")
		h.Set("Sec-Fetch-Dest", "document")
	} else {
		h.Set("Sec-Fetch-Mode", "cors")
		h.Set("Sec-Fetch-Dest", "empty")
	}
	if o.origin != "" {
		h.Set("Origin", o.origin)
	}
	if o.referer != "" {
		h.Set("Referer", o.referer)
	}
	if metodo == "POST" {
		h.Set("Content-Type", "application/x-www-form-urlencoded")
		if o.navegacion {
			h.Set("Cache-Control", "max-age=0")
			h.Set("Sec-Fetch-User", "?1")
		}
	}
	for k, v := range o.extra {
		h.Set(k, v)
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("%w: sin respuesta (%v)", ErrSII, err)
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("%w: leyendo la respuesta (%v)", ErrSII, err)
	}
	raw = descomprimir(raw, resp.Header.Get("Content-Encoding"))
	c.limpiarCookies(req.URL)
	return &respuesta{Status: resp.StatusCode, Cuerpo: raw, URL: u, Header: resp.Header}, nil
}

func descomprimir(raw []byte, enc string) []byte {
	switch strings.ToLower(strings.TrimSpace(enc)) {
	case "gzip":
		if zr, err := gzip.NewReader(bytes.NewReader(raw)); err == nil {
			if out, err := io.ReadAll(zr); err == nil {
				return out
			}
		}
	case "deflate":
		if zr, err := zlib.NewReader(bytes.NewReader(raw)); err == nil {
			if out, err := io.ReadAll(zr); err == nil {
				return out
			}
		}
		if out, err := io.ReadAll(flate.NewReader(bytes.NewReader(raw))); err == nil {
			return out
		}
	}
	return raw
}

// limpiarCookies es _limpiar_cookies: el SII manda cookies con valor "None"; reenviarlas
// malforma el request.
func (c *Cliente) limpiarCookies(u *url.URL) {
	var muertas []*http.Cookie
	for _, ck := range c.jar.Cookies(u) {
		if ck.Value == "None" || ck.Value == "" {
			muertas = append(muertas, &http.Cookie{Name: ck.Name, Value: "", Path: "/", MaxAge: -1})
		}
	}
	if len(muertas) > 0 {
		c.jar.SetCookies(u, muertas)
	}
}

func (c *Cliente) tieneSesion() bool {
	for _, raw := range []string{urlLogin, Base + "/", urlSIIHome} {
		u, _ := url.Parse(raw)
		for _, ck := range c.jar.Cookies(u) {
			if strings.HasPrefix(ck.Name, "NETSCAPE_LIVEWIRE") {
				return true
			}
		}
	}
	return false
}

// ------------------------------------------------------------------------------- sesión

// IniciarSesion es el login por clave tributaria + entrada al menú de emisión. PRODUCCIÓN.
func (c *Cliente) IniciarSesion() error {
	n, dv := SepararRut(c.RutUsuario)
	r, err := c.req("POST", urlLogin, opciones{navegacion: true, referer: refererLogin, origin: "https://zeusr.sii.cl",
		data: []Par{{"rut", n}, {"dv", dv}, {"referencia", urlSIIHome}, {"411", ""},
			{"rutcntr", FormatearRut(c.RutUsuario, true)}, {"clave", c.claveTrib}}})
	if err != nil {
		return err
	}
	// El SII devuelve 200 con HTML de error, no un 401: mirar las cookies es lo fiable.
	if !c.tieneSesion() {
		return fmt.Errorf("%w: el SII no abrió sesión (clave tributaria incorrecta o tope de sesiones concurrentes; http %d)", ErrAutenticacion, r.Status)
	}
	c.autenticado = true
	if _, err := c.req("GET", urlSIIHome, opciones{navegacion: true, referer: refererLogin}); err != nil {
		return err
	}
	if _, err := c.req("GET", Base+"/mipeLaunchPage.cgi", opciones{navegacion: true, referer: urlSIIHome,
		params: []Par{{"OPCION", "34"}, {"TIPO", "4"}}}); err != nil {
		return err
	}
	_, err = c.req("GET", Base+"/mipeSelEmpresa.cgi", opciones{navegacion: true, referer: urlSIIHome,
		params: []Par{{"DESDE_DONDE_URL", opcionEmision}}})
	return err
}

// SeleccionarEmpresa elige la empresa a operar. El RUT va COMPLETO con DV: sin él, el WAF
// responde `Request Rejected` y toda la cadena hereda el rechazo.
func (c *Cliente) SeleccionarEmpresa(rutEmpresa string) error {
	if !ValidarRut(rutEmpresa) {
		return fmt.Errorf("%w: RUT de empresa inválido: %s", ErrValidacion, rutEmpresa)
	}
	rut := FormatearRut(rutEmpresa, false)
	r, err := c.req("POST", Base+"/mipeSelEmpresa.cgi", opciones{navegacion: true, origin: "https://www1.sii.cl",
		referer: Base + "/mipeSelEmpresa.cgi?DESDE_DONDE_URL=OPCION%3D34%26TIPO%3D4",
		data:    []Par{{"DESDE_DONDE_URL", opcionEmision}, {"RUT_EMP", rut}}})
	if err != nil {
		return err
	}
	if r.rechazado() || len(r.Cuerpo) < 400 {
		return fmt.Errorf("%w: el portal no aceptó la selección de empresa (http %d, rut %s)", ErrRechazo, r.Status, rut)
	}
	c.Empresa = rut
	c.menuHTML = r.Texto()
	return nil
}

func (c *Cliente) requiereEmpresa() error {
	if c.Empresa == "" {
		return fmt.Errorf("%w: primero IniciarSesion() y SeleccionarEmpresa()", ErrValidacion)
	}
	return nil
}

// Enlace es un `(texto, href)` del menú de la empresa.
type Enlace struct {
	Texto string `json:"texto"`
	Href  string `json:"href"`
}

var (
	reAnchor   = regexp.MustCompile(`(?is)<a\b[^>]*href=["']([^"']+)["'][^>]*>(.*?)</a>`)
	reFrame    = regexp.MustCompile(`(?i)<i?frame\b[^>]*src=["']([^"']+)`)
	reFormAct  = regexp.MustCompile(`(?i)<form\b[^>]*action=["']([^"']+)`)
	reOptionCG = regexp.MustCompile(`(?is)<option\b[^>]*value=["']([^"']*\.cgi[^"']*)["'][^>]*>(.*?)</option>`)
	reJSCGI    = regexp.MustCompile(`(?i)(?:location(?:\.href)?\s*=|window\.open\(|\.submit\(|href=)\s*["']([^"']*\.cgi[^"']*)`)
	reOnclick  = regexp.MustCompile(`(?is)onclick=["'][^"']*?["']([^"']*\.cgi[^"']*)["'][^"']*["'][^>]*>(.*?)<`)
	reTagsHTML = regexp.MustCompile(`<[^>]+>`)
	reTitle    = regexp.MustCompile(`(?is)<title>(.*?)</title>`)
	reCGI      = regexp.MustCompile(`([A-Za-z0-9_]+\.cgi)`)
	reIFrame   = regexp.MustCompile(`(?i)<i?frame\b`)
	reForm     = regexp.MustCompile(`(?i)<form\b`)
	reA        = regexp.MustCompile(`(?i)<a\b`)
	reScript   = regexp.MustCompile(`(?i)<script\b`)
)

func sinTags(s string) string { return strings.TrimSpace(reTagsHTML.ReplaceAllString(s, "")) }

// EnlacesMenu es enlaces_menu: los CGI que el menú de la empresa expone (anclas, frames,
// forms, options y JS), sin duplicados. Solo lectura; sirve para descubrir sin adivinar.
func (c *Cliente) EnlacesMenu() []Enlace {
	h := c.menuHTML
	var enlaces []Enlace
	for _, m := range reAnchor.FindAllStringSubmatch(h, -1) {
		enlaces = append(enlaces, Enlace{sinTags(m[2]), m[1]})
	}
	for _, m := range reFrame.FindAllStringSubmatch(h, -1) {
		enlaces = append(enlaces, Enlace{"[frame]", m[1]})
	}
	for _, m := range reFormAct.FindAllStringSubmatch(h, -1) {
		enlaces = append(enlaces, Enlace{"[form]", m[1]})
	}
	for _, m := range reOptionCG.FindAllStringSubmatch(h, -1) {
		enlaces = append(enlaces, Enlace{"[option] " + sinTags(m[2]), m[1]})
	}
	for _, m := range reJSCGI.FindAllStringSubmatch(h, -1) {
		enlaces = append(enlaces, Enlace{"[js]", m[1]})
	}
	for _, m := range reOnclick.FindAllStringSubmatch(h, -1) {
		enlaces = append(enlaces, Enlace{"[onclick] " + sinTags(m[2]), m[1]})
	}
	vistos := map[Enlace]bool{}
	var unicos []Enlace
	for _, e := range enlaces {
		if !vistos[e] {
			vistos[e] = true
			unicos = append(unicos, e)
		}
	}
	return unicos
}

// ResumenMenu es resumen_menu: qué contiene la página del menú, sin exponer su HTML.
type ResumenMenu struct {
	Bytes           int      `json:"bytes"`
	Titulo          string   `json:"titulo"`
	Frames          int      `json:"frames"`
	Forms           int      `json:"forms"`
	Anchors         int      `json:"anchors"`
	Scripts         int      `json:"scripts"`
	CGIsMencionados []string `json:"cgis_mencionados"`
}

func (c *Cliente) ResumenMenu() ResumenMenu {
	h := c.menuHTML
	r := ResumenMenu{Bytes: len(h), Frames: len(reIFrame.FindAllString(h, -1)), Forms: len(reForm.FindAllString(h, -1)),
		Anchors: len(reA.FindAllString(h, -1)), Scripts: len(reScript.FindAllString(h, -1)), CGIsMencionados: []string{}}
	if m := reTitle.FindStringSubmatch(h); m != nil {
		r.Titulo = strings.TrimSpace(m[1])
	}
	set := map[string]bool{}
	for _, m := range reCGI.FindAllStringSubmatch(h, -1) {
		set[m[1]] = true
	}
	for k := range set {
		r.CGIsMencionados = append(r.CGIsMencionados, k)
	}
	sort.Strings(r.CGIsMencionados)
	return r
}

// Cerrar cierra la sesión en el SII (best-effort). Sigue el 302 de autTermino: el GET a secas
// deja la sesión viva y así se llegó al tope el 2026-10-08. Devuelve error solo si el SII no
// borró las cookies de sesión (aviso, no fallo).
func (c *Cliente) Cerrar() error {
	if !c.autenticado {
		return nil
	}
	c.autenticado = false
	cli := &http.Client{Transport: salaespera.Transporte(nil), Jar: c.jar, Timeout: 20 * time.Second}
	req, err := http.NewRequest("GET", urlLogout, nil)
	if err != nil {
		return nil
	}
	req.Header.Set("User-Agent", ua)
	req.Header.Set("Referer", Base+"/mipeSelEmpresa.cgi")
	resp, err := cli.Do(req)
	if err != nil {
		return fmt.Errorf("%w: autTermino sin respuesta (%v)", ErrSII, err)
	}
	io.Copy(io.Discard, resp.Body)
	resp.Body.Close()
	if c.tieneSesion() {
		return fmt.Errorf("%w: autTermino no cerró la sesión (cookies de sesión siguen vivas)", ErrSII)
	}
	return nil
}
