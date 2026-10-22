package sii

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/textproto"
	"regexp"
	"strings"
	"time"

	"dtechile/nucleo/firma"
	"dtechile/nucleo/salaespera"
)

// ClienteBoleta es core/sii_boleta.py::ClienteBoletaSII: las boletas (39/41) NO comparten
// infraestructura con las facturas (Res. Ex. SII N° 74 de 2020): servicios REST, servidores
// propios y token propio (el de factura no sirve aquí).
//
// ⚠️ LOS HOSTS SON ASIMÉTRICOS — es el error que más tiempo cuesta:
//
//	paso                        certificación      producción
//	semilla · token · consulta  apicert.sii.cl     api.sii.cl
//	ENVÍO                       pangal.sii.cl      rahue.sii.cl
//
// Mandar el ENVÍO a apicert/api devuelve un SOAP Fault "Acceso Denegado" con HTTP 500 que
// parece un problema de permisos; pangal/rahue responden 400 "No trae TOKEN" → ésa es la ruta.
type ClienteBoleta struct {
	Ambiente string
	Cert     *firma.Certificado
	HTTP     *http.Client
	token    string
	expira   time.Time
}

var urlsBoleta = map[string]map[string]string{
	"certificacion": {
		"semilla": "https://apicert.sii.cl/recursos/v1/boleta.electronica.semilla",
		"token":   "https://apicert.sii.cl/recursos/v1/boleta.electronica.token",
		"envio":   "https://pangal.sii.cl/recursos/v1/boleta.electronica.envio",
		"estado":  "https://apicert.sii.cl/recursos/v1/boleta.electronica.envio",
	},
	"produccion": {
		"semilla": "https://api.sii.cl/recursos/v1/boleta.electronica.semilla",
		"token":   "https://api.sii.cl/recursos/v1/boleta.electronica.token",
		"envio":   "https://rahue.sii.cl/recursos/v1/boleta.electronica.envio",
		"estado":  "https://api.sii.cl/recursos/v1/boleta.electronica.envio",
	},
}

// El SII responde el ESTADO de semilla/token como DOS dígitos ("00"), no "0".
const estadoBoletaOK = "00"

// Estados del ENVÍO. ⚠️ EPR = "Envío Procesado", NO "aceptado": el veredicto por documento está
// en `estadistica` (aceptados/rechazados) y `detalle_rep_rech`.
const (
	EstadoBoletaProcesado = "EPR"
	EstadoBoletaRechazado = "RCT"
)

// Tope de boletas por sobre. NO está en el XSD (el 1000 del esquema es de Detalle): viene del
// Instructivo Técnico y no está verificado contra el SII vivo; es un tope conservador nuestro.
const MaxBoletasPorEnvio = 500

func NuevoBoleta(ambiente string, cert *firma.Certificado) (*ClienteBoleta, error) {
	if _, ok := urlsBoleta[ambiente]; !ok {
		return nil, fmt.Errorf("ambiente desconocido: %q", ambiente)
	}
	return &ClienteBoleta{Ambiente: ambiente, Cert: cert, HTTP: &http.Client{Transport: salaespera.Transporte(nil), Timeout: 60 * time.Second}}, nil
}

// FirmarSemillaBoleta es ClienteBoletaSII._firmar_semilla: el mismo <getToken> firmado del flujo
// SOAP pero compacto (sin saltos de línea) y mandado DIRECTO como body, sin escapar.
func FirmarSemillaBoleta(cert *firma.Certificado, semilla string) (string, error) {
	doc := "<getToken><item><Semilla>" + semilla + "</Semilla></item></getToken>"
	sig, err := firmaGetToken(cert, doc)
	if err != nil {
		return "", err
	}
	return "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n" +
		"<getToken><item><Semilla>" + semilla + "</Semilla></item>" + sig + "</getToken>", nil
}

var reElemento = regexp.MustCompile(`<([A-Za-z_]+)>([^<]*)</[A-Za-z_]+>`)

// textoDe devuelve el texto del primer elemento llamado `tag` (sin namespaces), como
// core/sii_boleta.py::_texto.
func textoDe(xml, tag string) string {
	for _, m := range reElemento.FindAllStringSubmatch(xml, -1) {
		if m[1] == tag {
			return strings.TrimSpace(m[2])
		}
	}
	return ""
}

func (c *ClienteBoleta) pedir(req *http.Request) (int, []byte, error) {
	var ultimo error
	for intento := 0; intento < 3; intento++ {
		r, err := c.HTTP.Do(req)
		if err != nil {
			ultimo = err
		} else {
			b, _ := io.ReadAll(r.Body)
			r.Body.Close()
			if r.StatusCode < 500 {
				return r.StatusCode, b, nil
			}
			ultimo = fmt.Errorf("HTTP %d: %.300s", r.StatusCode, string(b))
		}
		if req.GetBody != nil {
			req.Body, _ = req.GetBody()
		}
		time.Sleep(time.Duration(intento+1) * 500 * time.Millisecond)
	}
	return 0, nil, ultimo
}

// Semilla pide una semilla (REST, sin autenticación).
func (c *ClienteBoleta) Semilla() (string, error) {
	req, _ := http.NewRequest("GET", urlsBoleta[c.Ambiente]["semilla"], nil)
	req.Header.Set("accept", "application/xml")
	st, b, err := c.pedir(req)
	if err != nil {
		return "", fmt.Errorf("no se pudo pedir la semilla de boleta al SII: %w", err)
	}
	if st >= 400 {
		return "", fmt.Errorf("no se pudo pedir la semilla de boleta al SII: HTTP %d", st)
	}
	resp := string(b)
	estado, semilla := textoDe(resp, "ESTADO"), textoDe(resp, "SEMILLA")
	if estado != estadoBoletaOK || semilla == "" {
		return "", fmt.Errorf("el SII no entregó semilla de boleta (ESTADO=%s, GLOSA=%s)", estado, textoDe(resp, "GLOSA"))
	}
	return semilla, nil
}

// Token: semilla → firma → token de boleta (cacheado 50 min).
func (c *ClienteBoleta) Token() (string, error) {
	if c.token != "" && time.Now().Before(c.expira) {
		return c.token, nil
	}
	sem, err := c.Semilla()
	if err != nil {
		return "", err
	}
	cuerpo, err := FirmarSemillaBoleta(c.Cert, sem)
	if err != nil {
		return "", err
	}
	req, _ := http.NewRequest("POST", urlsBoleta[c.Ambiente]["token"], strings.NewReader(cuerpo))
	req.Header.Set("Content-Type", "application/xml")
	req.Header.Set("accept", "application/xml")
	st, b, err := c.pedir(req)
	if err != nil {
		return "", fmt.Errorf("no se pudo obtener el token de boleta: %w", err)
	}
	if st >= 400 {
		return "", fmt.Errorf("no se pudo obtener el token de boleta: HTTP %d", st)
	}
	resp := string(b)
	tok := textoDe(resp, "TOKEN")
	if tok == "" {
		// El SII responde 200 aunque falle: el error viene en ESTADO/GLOSA.
		return "", fmt.Errorf("el SII no entregó token de boleta (ESTADO=%s, GLOSA=%s)", textoDe(resp, "ESTADO"), textoDe(resp, "GLOSA"))
	}
	c.token, c.expira = tok, time.Now().Add(50*time.Minute)
	return tok, nil
}

// ErrRechazoBoleta: el SII respondió pero no recibió el envío (estado != REC).
type ErrRechazoBoleta struct {
	Estado  string
	Detalle map[string]any
}

func (e *ErrRechazoBoleta) Error() string {
	return fmt.Sprintf("el SII no recibió el envío de boletas (estado=%s)", e.Estado)
}

// Enviar sube un EnvioBOLETA firmado (ISO-8859-1) y devuelve el TrackID (15 dígitos: string).
// El RUT va PARTIDO y sin guion en cuatro campos: quien envía (dueño del certificado) y la
// empresa emisora.
func (c *ClienteBoleta) Enviar(xmlEnvio []byte, rutEmpresa, dvEmpresa string) (string, map[string]any, error) {
	tok, err := c.Token()
	if err != nil {
		return "", nil, err
	}
	rutS, dvS := rutEmpresa, dvEmpresa
	if r, err := c.Cert.RutEmisor(); err == nil && strings.Contains(r, "-") {
		p := strings.SplitN(r, "-", 2)
		rutS, dvS = p[0], p[1]
	}
	var buf bytes.Buffer
	w := multipart.NewWriter(&buf)
	for _, kv := range [][2]string{{"rutSender", rutS}, {"dvSender", dvS}, {"rutCompany", rutEmpresa}, {"dvCompany", dvEmpresa}} {
		_ = w.WriteField(kv[0], kv[1])
	}
	h := make(textproto.MIMEHeader)
	h.Set("Content-Disposition", fmt.Sprintf(`form-data; name="archivo"; filename="%s-%s_boletas.xml"`, rutEmpresa, dvEmpresa))
	h.Set("Content-Type", "text/xml")
	fw, _ := w.CreatePart(h)
	_, _ = fw.Write(xmlEnvio)
	_ = w.Close()
	cuerpo := buf.Bytes()
	req, _ := http.NewRequest("POST", urlsBoleta[c.Ambiente]["envio"], bytes.NewReader(cuerpo))
	req.GetBody = func() (io.ReadCloser, error) { return io.NopCloser(bytes.NewReader(cuerpo)), nil }
	req.Header.Set("Content-Type", w.FormDataContentType())
	req.Header.Set("Cookie", "TOKEN="+tok)
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", UserAgent) // sin esto: 401 "NO ESTA AUTENTICADO" engañoso
	st, b, err := c.pedir(req)
	if err != nil {
		return "", nil, fmt.Errorf("falló el envío de boletas al SII: %w", err)
	}
	if st >= 400 {
		return "", nil, fmt.Errorf("falló el envío de boletas al SII: HTTP %d: %.300s", st, string(b))
	}
	var resp map[string]any
	if err := json.Unmarshal(b, &resp); err != nil {
		return "", nil, fmt.Errorf("la respuesta del envío de boletas no es JSON (¿endpoint equivocado? el envío va a pangal/rahue): %.300s", string(b))
	}
	estado := strings.ToUpper(fmt.Sprint(resp["estado"]))
	if estado != "REC" {
		det := map[string]any{}
		for k, v := range resp {
			if k != "archivo" {
				det[k] = v
			}
		}
		return "", resp, &ErrRechazoBoleta{Estado: estado, Detalle: det}
	}
	track := strings.TrimSpace(fmt.Sprint(resp["trackid"]))
	if track == "" || track == "<nil>" {
		return "", resp, errors.New("el SII aceptó el envío pero no devolvió TrackID")
	}
	return track, resp, nil
}

// EstadoBoleta es el resultado de la consulta por TrackID (core/sii_boleta.py::consultar_estado).
type EstadoBoleta struct {
	TrackID      string         `json:"track_id"`
	Estado       string         `json:"estado"`
	Glosa        string         `json:"glosa"`
	Procesado    bool           `json:"procesado"`
	Informados   int            `json:"informados"`
	Aceptados    int            `json:"aceptados"`
	Rechazados   int            `json:"rechazados"`
	Reparos      int            `json:"reparos"`
	TodoAceptado bool           `json:"todo_aceptado"`
	Resuelto     bool           `json:"resuelto"`
	Detalle      any            `json:"detalle"`
	RespuestaRaw map[string]any `json:"respuesta_raw"`
}

// Estado consulta un envío: el TrackID va EN EL PATH pegado al RUT ({RUT}-{DV}-{TRACKID}).
func (c *ClienteBoleta) Estado(trackID, rutEmpresa, dvEmpresa string) (*EstadoBoleta, error) {
	tok, err := c.Token()
	if err != nil {
		return nil, err
	}
	req, _ := http.NewRequest("GET", fmt.Sprintf("%s/%s-%s-%s", urlsBoleta[c.Ambiente]["estado"], rutEmpresa, dvEmpresa, trackID), nil)
	req.Header.Set("Cookie", "TOKEN="+tok)
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", UserAgent)
	st, b, err := c.pedir(req)
	if err != nil {
		return nil, fmt.Errorf("no se pudo consultar el estado del envío %s: %w", trackID, err)
	}
	if st >= 400 {
		return nil, fmt.Errorf("no se pudo consultar el estado del envío %s: HTTP %d", trackID, st)
	}
	var resp map[string]any
	if err := json.Unmarshal(b, &resp); err != nil {
		return nil, fmt.Errorf("la respuesta de estado del envío %s no es JSON: %.300s", trackID, string(b))
	}
	e := &EstadoBoleta{TrackID: trackID, Estado: strings.ToUpper(fmt.Sprint(resp["estado"])), RespuestaRaw: resp, Detalle: resp["detalle_rep_rech"]}
	if e.Estado == "<nil>" {
		e.Estado = ""
	}
	for _, k := range []string{"glosa", "descripcion"} {
		if v, ok := resp[k].(string); ok && v != "" {
			e.Glosa = v
			break
		}
	}
	if lista, ok := resp["estadistica"].([]any); ok {
		for _, x := range lista {
			m, _ := x.(map[string]any)
			e.Informados += entero(m["informados"])
			e.Aceptados += entero(m["aceptados"])
			e.Rechazados += entero(m["rechazados"])
			e.Reparos += entero(m["reparos"])
		}
	}
	e.Procesado = e.Estado == EstadoBoletaProcesado
	e.TodoAceptado = e.Procesado && e.Informados > 0 && e.Rechazados == 0 && e.Reparos == 0
	e.Resuelto = e.Estado == EstadoBoletaProcesado || e.Estado == EstadoBoletaRechazado || e.Estado == "RLV" || e.Estado == "RPR"
	return e, nil
}

func entero(v any) int {
	switch x := v.(type) {
	case float64:
		return int(x)
	case string:
		var n int
		fmt.Sscan(x, &n)
		return n
	}
	return 0
}
