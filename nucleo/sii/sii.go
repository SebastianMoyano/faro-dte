// Package sii es el cliente de los web services del SII (semilla → token → envío → estado),
// réplica de core/sii.py. La semilla se firma con XMLDSig "enveloped" (el formato de
// `core/crypto.py::firmar_documento_xml`, distinto del de los DTE): se prueba byte a byte
// contra tests/dorado/sii/semilla_firmada.xml.
package sii

import (
	"bytes"
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha1"
	"encoding/base64"
	"errors"
	"fmt"
	"html"
	"io"
	"mime/multipart"
	"net/http"
	"net/textproto"
	"regexp"
	"strings"
	"time"

	"dtechile/nucleo/c14n"
	"dtechile/nucleo/firma"
	"dtechile/nucleo/salaespera"
)

const (
	Certificacion = "certificacion"
	Produccion    = "produccion"
	// Lo que el SII valida en el User-Agent es el token "PROG 1.0" (core/config.py).
	UserAgent = "Mozilla/5.0 (compatible; PROG 1.0; +https://github.com/SebastianMoyano/dte-chile)"
	nsDsig    = "http://www.w3.org/2000/09/xmldsig#"
)

var hosts = map[string]string{Certificacion: "https://maullin.sii.cl", Produccion: "https://palena.sii.cl"}

type Cliente struct {
	Ambiente string
	Cert     *firma.Certificado
	HTTP     *http.Client
	token    string
	expira   time.Time
}

func Nuevo(ambiente string, cert *firma.Certificado) (*Cliente, error) {
	if _, ok := hosts[ambiente]; !ok {
		return nil, fmt.Errorf("ambiente desconocido %q (certificacion | produccion)", ambiente)
	}
	return &Cliente{Ambiente: ambiente, Cert: cert, HTTP: &http.Client{Transport: salaespera.Transporte(nil), Timeout: 90 * time.Second}}, nil
}

func (c *Cliente) url(ruta string) string { return hosts[c.Ambiente] + ruta }

// FirmarSemilla replica _firmar_semilla + firmar_documento_xml: el <getToken> con la semilla,
// digest del elemento canonicalizado (con sus saltos de línea), SignedInfo con transform
// enveloped-signature, firma RSA-SHA1, KeyInfo sin envolver a 64.
func FirmarSemilla(cert *firma.Certificado, semilla string) (string, error) {
	doc := "<getToken>\n<item>\n<Semilla>" + semilla + "</Semilla>\n</item>\n</getToken>"
	firmaXML, err := firmaGetToken(cert, doc)
	if err != nil {
		return "", err
	}
	// lxml anexa la firma tras el último hijo: el "\n" (cola de <item>) queda antes de <Signature>
	// y no hay nada entre </Signature> y </getToken>.
	return "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n" +
		"<getToken>\n<item>\n<Semilla>" + semilla + "</Semilla>\n</item>\n" + firmaXML + "</getToken>", nil
}

// firmaGetToken es firmar_documento_xml sobre un <getToken>: digest del documento canonicalizado
// (tal cual, con o sin saltos de línea), SignedInfo con transform enveloped-signature, firma
// RSA-SHA1, KeyInfo sin envolver a 64. Devuelve solo el <Signature>.
func firmaGetToken(cert *firma.Certificado, doc string) (string, error) {
	can, err := c14n.Canonicalizar(doc, nil)
	if err != nil {
		return "", err
	}
	dig := sha1.Sum(can)
	signedInfo := "<SignedInfo xmlns=\"" + nsDsig + "\">\n" +
		"<CanonicalizationMethod Algorithm=\"http://www.w3.org/TR/2001/REC-xml-c14n-20010315\"/>\n" +
		"<SignatureMethod Algorithm=\"http://www.w3.org/2000/09/xmldsig#rsa-sha1\"/>\n" +
		"<Reference URI=\"\">\n<Transforms>\n" +
		"<Transform Algorithm=\"http://www.w3.org/2000/09/xmldsig#enveloped-signature\"/>\n" +
		"</Transforms>\n<DigestMethod Algorithm=\"http://www.w3.org/2000/09/xmldsig#sha1\"/>\n" +
		"<DigestValue>" + base64.StdEncoding.EncodeToString(dig[:]) + "</DigestValue>\n" +
		"</Reference>\n</SignedInfo>"
	canSI, err := c14n.Canonicalizar(signedInfo, nil)
	if err != nil {
		return "", err
	}
	h := sha1.Sum(canSI)
	sig, err := rsa.SignPKCS1v15(rand.Reader, cert.Clave, crypto.SHA1, h[:])
	if err != nil {
		return "", err
	}
	pub := cert.Clave.PublicKey
	exp := []byte{}
	for e := pub.E; e > 0; e >>= 8 {
		exp = append([]byte{byte(e & 0xff)}, exp...)
	}
	// Dentro de <Signature> el xmlns del SignedInfo es redundante y lxml no lo serializa
	// (sí está en lo canonicalizado y firmado, arriba).
	signedInfoEmbebido := strings.Replace(signedInfo, " xmlns=\""+nsDsig+"\"", "", 1)
	return "<Signature xmlns=\"" + nsDsig + "\">\n" + signedInfoEmbebido + "\n" +
		"<SignatureValue>" + base64.StdEncoding.EncodeToString(sig) + "</SignatureValue>\n" +
		"<KeyInfo>\n<KeyValue>\n<RSAKeyValue>\n" +
		"<Modulus>" + base64.StdEncoding.EncodeToString(pub.N.Bytes()) + "</Modulus>\n" +
		"<Exponent>" + base64.StdEncoding.EncodeToString(exp) + "</Exponent>\n" +
		"</RSAKeyValue>\n</KeyValue>\n<X509Data>\n" +
		"<X509Certificate>" + base64.StdEncoding.EncodeToString(cert.Cert.Raw) + "</X509Certificate>\n" +
		"</X509Data>\n</KeyInfo>\n</Signature>", nil
}

// soap hace el POST con hasta 3 intentos ante 5xx o error de red (Maullín devuelve 503
// intermitentes; core/sii.py usa ClienteReintentos por lo mismo).
func (c *Cliente) soap(ruta, cuerpo string) (string, error) {
	var ultimo error
	for intento := 0; intento < 3; intento++ {
		if intento > 0 {
			time.Sleep(time.Duration(2*intento) * time.Second)
		}
		req, err := http.NewRequest("POST", c.url(ruta), strings.NewReader(cuerpo))
		if err != nil {
			return "", err
		}
		req.Header.Set("Content-Type", "text/xml; charset=UTF-8")
		req.Header.Set("SOAPAction", "")
		req.Header.Set("User-Agent", UserAgent)
		r, err := c.HTTP.Do(req)
		if err != nil {
			ultimo = err
			continue
		}
		b, err := io.ReadAll(r.Body)
		r.Body.Close()
		if err != nil {
			ultimo = err
			continue
		}
		if r.StatusCode >= 500 {
			ultimo = fmt.Errorf("SII %s → HTTP %d: %.200s", ruta, r.StatusCode, string(b))
			continue
		}
		if r.StatusCode >= 400 {
			return "", fmt.Errorf("SII %s → HTTP %d: %.300s", ruta, r.StatusCode, string(b))
		}
		return string(b), nil
	}
	return "", fmt.Errorf("tras 3 intentos: %w", ultimo)
}

var reTag = func(tag string) *regexp.Regexp {
	return regexp.MustCompile(`(?s)<(?:[A-Za-z0-9]+:)?` + tag + `[^>]*>([^<]*)</(?:[A-Za-z0-9]+:)?` + tag + `>`)
}

// valor extrae <TAG>…</TAG> de la respuesta SOAP, que suele venir con el XML interno escapado
// dentro de <getSeedReturn>/<getTokenReturn> (por eso se des-escapa antes).
func valor(respuesta, tag string) string {
	des := html.UnescapeString(respuesta)
	if m := reTag(tag).FindStringSubmatch(des); m != nil {
		return strings.TrimSpace(m[1])
	}
	return ""
}

func (c *Cliente) Semilla() (string, error) {
	resp, err := c.soap("/DTEWS/CrSeed.jws", `<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
  <soapenv:Body>
    <getSeed/>
  </soapenv:Body>
</soapenv:Envelope>`)
	if err != nil {
		return "", err
	}
	s := valor(resp, "SEMILLA")
	if s == "" {
		return "", &ErrRespuesta{fmt.Sprintf("No se pudo obtener la semilla del SII. Respuesta: %.300s", resp)}
	}
	return s, nil
}

// Token hace semilla → firma → token y lo cachea 50 minutos (como Python).
func (c *Cliente) Token() (string, error) {
	if c.token != "" && time.Now().Before(c.expira) {
		return c.token, nil
	}
	sem, err := c.Semilla()
	if err != nil {
		return "", err
	}
	firmada, err := FirmarSemilla(c.Cert, sem)
	if err != nil {
		return "", err
	}
	esc := strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;").Replace(firmada)
	resp, err := c.soap("/DTEWS/GetTokenFromSeed.jws", `<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
  <soapenv:Body>
    <getToken>
      <pszXml>`+esc+`</pszXml>
    </getToken>
  </soapenv:Body>
</soapenv:Envelope>`)
	if err != nil {
		return "", err
	}
	t := valor(resp, "TOKEN")
	if t == "" {
		return "", &ErrRespuesta{fmt.Sprintf("No se pudo obtener el token del SII. Respuesta: %.300s", resp)}
	}
	c.token, c.expira = t, time.Now().Add(50*time.Minute)
	return t, nil
}

// EnviarDTE sube el sobre (EnvioDTE) por DTEUpload. Boletas NO van por aquí (otra API).
func (c *Cliente) EnviarDTE(xmlEnvio []byte, rutEmpresa, dvEmpresa string) (int64, string, error) {
	if bytes.Contains(xmlEnvio[:min(len(xmlEnvio), 200)], []byte("EnvioBOLETA")) {
		return 0, "", errors.New("las boletas no se envían por DTEUpload")
	}
	tok, err := c.Token()
	if err != nil {
		return 0, "", err
	}
	rutS, dvS := rutEmpresa, dvEmpresa
	if r, err := c.Cert.RutEmisor(); err == nil && strings.Contains(r, "-") {
		p := strings.SplitN(r, "-", 2)
		rutS, dvS = p[0], p[1]
	}
	var buf bytes.Buffer
	w := multipart.NewWriter(&buf)
	for k, v := range map[string]string{"rutSender": rutS, "dvSender": dvS, "rutCompany": rutEmpresa, "dvCompany": dvEmpresa} {
		_ = w.WriteField(k, v)
	}
	h := make(textproto.MIMEHeader)
	h.Set("Content-Disposition", fmt.Sprintf(`form-data; name="archivo"; filename="%s-%s_envio.xml"`, rutEmpresa, dvEmpresa))
	h.Set("Content-Type", "application/xml")
	fw, _ := w.CreatePart(h)
	_, _ = fw.Write(xmlEnvio)
	_ = w.Close()
	req, err := http.NewRequest("POST", c.url("/cgi_dte/UPL/DTEUpload"), &buf)
	if err != nil {
		return 0, "", err
	}
	req.Header.Set("Content-Type", w.FormDataContentType())
	req.Header.Set("User-Agent", UserAgent)
	req.Header.Set("Cookie", "TOKEN="+tok)
	r, err := c.HTTP.Do(req)
	if err != nil {
		return 0, "", err
	}
	defer r.Body.Close()
	b, _ := io.ReadAll(r.Body)
	resp := string(b)
	if r.StatusCode >= 400 {
		return 0, resp, fmt.Errorf("DTEUpload HTTP %d", r.StatusCode)
	}
	estado := valor(resp, "STATUS")
	var track int64
	fmt.Sscan(valor(resp, "TRACKID"), &track)
	if estado != "0" {
		return track, resp, &ErrRespuesta{fmt.Sprintf("El SII rechazó el envío. Estado: %s. Respuesta: %.300s", estado, resp)}
	}
	return track, resp, nil
}

// Estado del envío por TrackID. OJO: EPR = "Envío Procesado", NO "aceptado": el veredicto son
// Aceptados/Rechazados/Reparos.
type Estado struct {
	TrackID    int64  `json:"track_id"`
	Estado     string `json:"estado"`
	Glosa      string `json:"glosa"`
	Informados int    `json:"informados"`
	Aceptados  int    `json:"aceptados"`
	Rechazados int    `json:"rechazados"`
	Reparos    int    `json:"reparos"`
	Crudo      string `json:"-"`
}

func (c *Cliente) EstadoTrack(track int64, rutEmpresa, dvEmpresa string) (*Estado, error) {
	tok, err := c.Token()
	if err != nil {
		return nil, err
	}
	resp, err := c.soap("/DTEWS/QueryEstUp.jws", fmt.Sprintf(`<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
  <soapenv:Body>
    <getEstUp>
      <Rut>%s</Rut>
      <Dv>%s</Dv>
      <TrackId>%d</TrackId>
      <Token>%s</Token>
    </getEstUp>
  </soapenv:Body>
</soapenv:Envelope>`, rutEmpresa, dvEmpresa, track, tok))
	if err != nil {
		return nil, err
	}
	e := &Estado{TrackID: track, Estado: valor(resp, "ESTADO"), Glosa: valor(resp, "GLOSA"), Crudo: resp}
	fmt.Sscan(valor(resp, "INFORMADOS"), &e.Informados)
	fmt.Sscan(valor(resp, "ACEPTADOS"), &e.Aceptados)
	fmt.Sscan(valor(resp, "RECHAZADOS"), &e.Rechazados)
	fmt.Sscan(valor(resp, "REPAROS"), &e.Reparos)
	if e.Estado == "" {
		e.Estado = "DESCONOCIDO"
	}
	return e, nil
}

// EstadoDTE es consultar_estado_dte: estado de un documento por sus datos (getEstDte).
// fechaEmision va como YYYYMMDD.
func (c *Cliente) EstadoDTE(rutEmisor, dvEmisor string, tipo, folio int, fechaEmision string, montoTotal int64, rutReceptor, dvReceptor string) (*Estado, error) {
	tok, err := c.Token()
	if err != nil {
		return nil, err
	}
	resp, err := c.soap("/DTEWS/services/wsDTECorreo", fmt.Sprintf(`<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
  <soapenv:Body>
    <getEstDte>
      <RutEmisor>%s</RutEmisor>
      <DvEmisor>%s</DvEmisor>
      <TipoDte>%d</TipoDte>
      <FolioDte>%d</FolioDte>
      <FechaEmisionDte>%s</FechaEmisionDte>
      <MontoDte>%d</MontoDte>
      <RutReceptor>%s</RutReceptor>
      <DvReceptor>%s</DvReceptor>
      <Token>%s</Token>
    </getEstDte>
  </soapenv:Body>
</soapenv:Envelope>`, rutEmisor, dvEmisor, tipo, folio, fechaEmision, montoTotal, rutReceptor, dvReceptor, tok))
	if err != nil {
		return nil, err
	}
	e := &Estado{Estado: valor(resp, "ESTADO"), Glosa: valor(resp, "GLOSA"), Crudo: resp}
	if e.Estado == "" {
		e.Estado = valor(resp, "estado")
		e.Glosa = valor(resp, "glosa")
	}
	return e, nil
}

// ErrRespuesta: el SII respondió, pero sin lo esperado (sin semilla/token, STATUS distinto de 0).
// Es el ValueError de core/sii.py, que la API traduce a 422 sii_rechazo; los errores de red o
// HTTP son 502 sii_comunicacion.
type ErrRespuesta struct{ Msg string }

func (e *ErrRespuesta) Error() string { return e.Msg }
