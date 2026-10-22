package rcv

import (
	"database/sql"
	"fmt"
	"sort"
	"strconv"
	"strings"
	"time"
)

// Últimos movimientos: lo que el SII tiene registrado del negocio, directo del RCV. Mismo
// comportamiento que `periodos_por_defecto`/`parsear_periodos`/`ultimos_movimientos` de core/rcv.py.

// MaxPeriodosMovimientos es el tope de meses por consulta.
const MaxPeriodosMovimientos = 3

// MensajePeriodos es el 422 cuando `periodos` no se entiende.
var MensajePeriodos = fmt.Sprintf("periodos debe ser una lista de meses YYYYMM separados por coma (máximo %d).", MaxPeriodosMovimientos)

// PeriodosPorDefecto: el mes en curso y el anterior (hora local), del más nuevo al más viejo.
func PeriodosPorDefecto(hoy time.Time) []string {
	ant := hoy.AddDate(0, 0, -hoy.Day()+1).AddDate(0, -1, 0)
	return []string{hoy.Format("200601"), ant.Format("200601")}
}

// ParsearPeriodos: "202610,2026-09" → [202610 202609]; nil si alguno no es un mes válido o son
// demasiados. Vacío → PeriodosPorDefecto(ahora). Sin repetidos, en el orden pedido.
func ParsearPeriodos(texto string) []string {
	var partes []string
	for _, p := range strings.Split(texto, ",") {
		if p = strings.ReplaceAll(strings.TrimSpace(p), "-", ""); p != "" {
			partes = append(partes, p)
		}
	}
	if len(partes) == 0 {
		return PeriodosPorDefecto(time.Now())
	}
	out := []string{}
	for _, p := range partes {
		mes, err := strconv.Atoi(p[min(4, len(p)):])
		if len(p) != 6 || !soloDigitos(p) || err != nil || mes < 1 || mes > 12 {
			return nil
		}
		repetido := false
		for _, o := range out {
			repetido = repetido || o == p
		}
		if !repetido {
			out = append(out, p)
		}
	}
	if len(out) > MaxPeriodosMovimientos {
		return nil
	}
	return out
}

func soloDigitos(s string) bool {
	for _, c := range s {
		if c < '0' || c > '9' {
			return false
		}
	}
	return true
}

// UltimosMovimientos baja COMPRAS y VENTAS de cada período desde el SII, las persiste (como el
// F29) y devuelve los movimientos del más nuevo al más viejo: fecha del documento, período,
// operación, tipo y folio, todo descendente.
func (c *Cliente) UltimosMovimientos(db *sql.DB, rutEmpresa string, periodos []string, limite int) (map[string]any, error) {
	var docs []Documento
	conteos := map[string]map[string]int{}
	for _, periodo := range periodos {
		compras, err := c.DescargarPeriodo(rutEmpresa, periodo, Compra)
		if err != nil {
			return nil, err
		}
		ventas, err := c.DescargarPeriodo(rutEmpresa, periodo, Venta)
		if err != nil {
			return nil, err
		}
		del := append(append([]Documento{}, compras...), ventas...)
		if _, err := GuardarDocumentos(db, del); err != nil {
			return nil, err
		}
		conteos[periodo] = map[string]int{"compras": len(compras), "ventas": len(ventas)}
		docs = append(docs, del...)
	}
	fecha := func(d Documento) string {
		if d.FechaDoc == nil {
			return ""
		}
		return *d.FechaDoc
	}
	sort.SliceStable(docs, func(i, j int) bool {
		a, b := docs[i], docs[j]
		switch {
		case fecha(a) != fecha(b):
			return fecha(a) > fecha(b)
		case a.Periodo != b.Periodo:
			return a.Periodo > b.Periodo
		case a.TipoOperacion != b.TipoOperacion:
			return a.TipoOperacion > b.TipoOperacion
		case a.TipoDTE != b.TipoDTE:
			return a.TipoDTE > b.TipoDTE
		}
		return a.Folio > b.Folio
	})
	movs := []map[string]any{}
	for i, d := range docs {
		if i >= limite {
			break
		}
		var f any
		if d.FechaDoc != nil {
			f = *d.FechaDoc
		}
		movs = append(movs, map[string]any{
			"tipo_operacion": d.TipoOperacion, "periodo": d.Periodo, "tipo_dte": d.TipoDTE, "folio": d.Folio,
			"fecha_doc": f, "rut_contraparte": d.RutContraparte, "razon_social": d.RazonSocial,
			"monto_exento": d.MontoExento, "monto_neto": d.MontoNeto, "monto_iva": d.MontoIVA,
			"monto_total": d.MontoTotal, "estado_sii": d.EstadoSII,
		})
	}
	return map[string]any{
		"periodos": periodos, "documentos": conteos, "total": len(docs), "sin_datos": len(docs) == 0,
		"movimientos": movs,
	}, nil
}
