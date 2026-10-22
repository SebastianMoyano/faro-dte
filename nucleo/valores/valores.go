// Package valores trae los indicadores públicos del SII (UF, UTM, dólar observado) desde las
// páginas oficiales de "Valores y Fechas" (www.sii.cl/valores_y_fechas/...), con mindicador.cl
// como fallback, y los cachea en el MISMO SQLite del motor (ver cache.go).
//
// Fuente PRIMARIA = oficial (SII). Esas páginas NO están tras el WAF Incapsula que frena otras
// (verificado 2026-10-08: HTTP 200 por httpx/curl), así que se bajan sin navegador. El HTML es
// ISO-8859-1. Dos formatos de tabla:
//   - UF y DÓLAR: una tabla por mes (<h2>Mes</h2>), pares <th>día</th><td>valor</td>. Diario.
//     OJO: la UF usa coma decimal ("41.065,38"); el dólar usa punto ("972.6").
//   - UTM: una sola tabla, una fila por mes (<th>Mes</th><td>UTM</td><td>UTA</td>...). Mensual
//     (se guarda en el día 1 del mes; Cache.Vigente devuelve la del mes en curso).
//
// mindicador.cl (JSON) se usa solo si la oficial falla.
package valores

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"time"

	"dtechile/nucleo/salaespera"
)

// Valor es un indicador en una fecha. Valor en pesos (UF, UTM, dólar observado).
type Valor struct {
	Indicador string  `json:"indicador"` // "uf" | "utm" | "dolar"
	Fecha     string  `json:"fecha"`     // YYYY-MM-DD
	Valor     float64 `json:"valor"`
}

const uaNavegador = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) " +
	"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0 Safari/537.36"

var meses = map[string]int{
	"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
	"julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}

var (
	// UF usa <h2>Mes</h2>; dólar usa <h3>Mes</h3> (con o sin atributos). El título "Dólar
	// Observado" no matchea: el captador es una sola palabra y "Dólar Observado" lleva espacio.
	reMes     = regexp.MustCompile(`(?i)<h[23][^>]*>\s*([a-záéíóúñ]+)\s*</h[23]>`)
	rePar     = regexp.MustCompile(`(?is)<th[^>]*>\s*(?:<strong>)?\s*(\d{1,2})\s*(?:</strong>)?\s*</th>\s*<td[^>]*>([^<]*)</td>`)
	reFilaMes = regexp.MustCompile(`(?is)<tr>\s*<th[^>]*>\s*([a-záéíóúñ]+)\s*</th>\s*<td[^>]*>([^<]*)</td>`)
)

// latin1 decodifica ISO-8859-1 a string: cada byte 0x00-0xFF es su propio code point Unicode.
func latin1(b []byte) string {
	r := make([]rune, len(b))
	for i, c := range b {
		r[i] = rune(c)
	}
	return string(r)
}

// numeroCL parsea un número con coma decimal chilena ("41.065,38" → 41065.38). "" → (0,false).
func numeroCL(s string) (float64, bool) {
	s = strings.TrimSpace(s)
	if s == "" {
		return 0, false
	}
	s = strings.ReplaceAll(s, ".", "")
	s = strings.ReplaceAll(s, ",", ".")
	return parseFloat(s)
}

// numeroDolar parsea el formato de la página dólar: punto decimal ("972.6" → 972.6). Si trae coma
// (por si el SII cambia a formato chileno cuando el dólar pasa de 1000), cae a numeroCL.
func numeroDolar(s string) (float64, bool) {
	s = strings.TrimSpace(s)
	if s == "" {
		return 0, false
	}
	if strings.Contains(s, ",") {
		return numeroCL(s)
	}
	return parseFloat(s)
}

func parseFloat(s string) (float64, bool) {
	v, err := strconv.ParseFloat(s, 64)
	if err != nil {
		return 0, false
	}
	return v, true
}

// parsearDiario recorre una página de tabla-por-mes (UF, dólar): cada bloque <h2>Mes</h2> seguido
// de pares <th>día</th><td>valor</td>. num parsea el valor según el formato del indicador.
func parsearDiario(htmlBytes []byte, anio int, indicador string, num func(string) (float64, bool)) ([]Valor, error) {
	doc := latin1(htmlBytes)
	enc := reMes.FindAllStringSubmatchIndex(doc, -1)
	if len(enc) == 0 {
		return nil, fmt.Errorf("no se encontraron meses en la página %s %d", indicador, anio)
	}
	var out []Valor
	for i, h := range enc {
		mes, ok := meses[strings.ToLower(doc[h[2]:h[3]])]
		if !ok {
			continue
		}
		fin := len(doc)
		if i+1 < len(enc) {
			fin = enc[i+1][0]
		}
		for _, p := range rePar.FindAllStringSubmatch(doc[h[1]:fin], -1) {
			dia, err := strconv.Atoi(strings.TrimSpace(p[1]))
			if err != nil || dia < 1 || dia > 31 {
				continue
			}
			if v, ok := num(p[2]); ok {
				out = append(out, Valor{indicador, fmt.Sprintf("%04d-%02d-%02d", anio, mes, dia), v})
			}
		}
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("página %s %d sin valores parseables", indicador, anio)
	}
	return out, nil
}

// ParsearUF: página UF (coma decimal, diario).
func ParsearUF(htmlBytes []byte, anio int) ([]Valor, error) {
	return parsearDiario(htmlBytes, anio, "uf", numeroCL)
}

// ParsearDolar: página dólar observado (punto decimal, diario).
func ParsearDolar(htmlBytes []byte, anio int) ([]Valor, error) {
	return parsearDiario(htmlBytes, anio, "dolar", numeroDolar)
}

// ParsearUTM: una tabla con una fila por mes; la UTM es el PRIMER <td> tras el <th>Mes</th>.
// Mensual: se guarda en el día 1 del mes.
func ParsearUTM(htmlBytes []byte, anio int) ([]Valor, error) {
	doc := latin1(htmlBytes)
	var out []Valor
	for _, m := range reFilaMes.FindAllStringSubmatch(doc, -1) {
		mes, ok := meses[strings.ToLower(strings.TrimSpace(m[1]))]
		if !ok {
			continue // filas de encabezado (2026, UTM(1)...) no son meses
		}
		if v, ok := numeroCL(m[2]); ok {
			out = append(out, Valor{"utm", fmt.Sprintf("%04d-%02d-01", anio, mes), v})
		}
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("página UTM %d sin valores parseables", anio)
	}
	return out, nil
}

func bajar(url string) ([]byte, error) {
	req, _ := http.NewRequest("GET", url, nil)
	req.Header.Set("User-Agent", uaNavegador)
	req.Header.Set("Accept-Language", "es-CL,es;q=0.9")
	resp, err := (&http.Client{Transport: salaespera.Transporte(nil), Timeout: 20 * time.Second}).Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("%s → HTTP %d", url, resp.StatusCode)
	}
	return io.ReadAll(resp.Body)
}

// Fetch trae un indicador ("uf"|"utm"|"dolar") del año: primero la página oficial del SII; si
// falla (red, HTTP, parseo), mindicador.cl. Devuelve los valores y la fuente ("sii"|"mindicador").
func Fetch(indicador string, anio int) ([]Valor, string, error) {
	var url string
	var parse func([]byte, int) ([]Valor, error)
	switch indicador {
	case "uf":
		url, parse = fmt.Sprintf("https://www.sii.cl/valores_y_fechas/uf/uf%d.htm", anio), ParsearUF
	case "dolar":
		url, parse = fmt.Sprintf("https://www.sii.cl/valores_y_fechas/dolar/dolar%d.htm", anio), ParsearDolar
	case "utm":
		url, parse = fmt.Sprintf("https://www.sii.cl/valores_y_fechas/utm/utm%d.htm", anio), ParsearUTM
	default:
		return nil, "", fmt.Errorf("indicador desconocido: %s", indicador)
	}
	if b, err := bajar(url); err == nil {
		if vs, err := parse(b, anio); err == nil {
			return vs, "sii", nil
		}
	}
	vs, err := fetchMindicador(indicador, anio)
	if err != nil {
		return nil, "", fmt.Errorf("%s %d: falló SII y mindicador: %w", indicador, anio, err)
	}
	return vs, "mindicador", nil
}

func fetchMindicador(indicador string, anio int) ([]Valor, error) {
	b, err := bajar(fmt.Sprintf("https://mindicador.cl/api/%s/%d", indicador, anio))
	if err != nil {
		return nil, err
	}
	var payload struct {
		Serie []struct {
			Fecha string  `json:"fecha"`
			Valor float64 `json:"valor"`
		} `json:"serie"`
	}
	if err := json.Unmarshal(b, &payload); err != nil {
		return nil, err
	}
	var out []Valor
	for _, s := range payload.Serie {
		if len(s.Fecha) >= 10 {
			out = append(out, Valor{indicador, s.Fecha[:10], s.Valor})
		}
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("mindicador %s %d sin serie", indicador, anio)
	}
	return out, nil
}
