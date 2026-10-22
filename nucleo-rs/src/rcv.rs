//! `core/rcv.py`: el Registro de Compras y Ventas del SII (la API JSON "facade" del portal
//! www4.sii.cl/consdcvinternetui), su normalización a filas de `rcv_documentos` y la
//! agregación al F29 (IVA mensual). Solo existe con datos en PRODUCCIÓN (Palena).
//! Transcripción de `nucleo/rcv` (Go).
//!
//! Contrato real capturado del navegador (2026-10-04): el detalle "Export" exige
//! accionRecaptcha + tokenRecaptcha (placeholder "t-o-k-e-n-web"), VENTA manda estadoContab
//! vacío y COMPRA "REGISTRO"; la respuesta del Export es {"data": ["cab;eza;ra", "fila;…"]}
//! (CSV con ';'), no una lista de dicts. getResumen sí devuelve dicts. `Accept:
//! application/json` provoca HTTP 500 en el facade: debe ser `*/*`.

use crate::almacen::{self, Almacen};
use crate::sii::http::{agente, ejecutar};
use rusqlite::params;
use serde_json::{json, Map, Value};
use std::collections::BTreeMap;
use std::time::Duration;

pub const BASE: &str = "https://www4.sii.cl/consdcvinternetui/services/data/facadeService/";
pub const COMPRA: &str = "COMPRA";
pub const VENTA: &str = "VENTA";
pub const TOKEN_RECAPTCHA: &str = "t-o-k-e-n-web";
pub const USER_AGENT: &str =
    "Mozilla/5.0 (compatible; PROG 1.0; +https://github.com/SebastianMoyano/dte-chile)";

pub fn metodo(op: &str) -> &'static str {
    match op {
        COMPRA => "getDetalleCompraExport",
        VENTA => "getDetalleVentaExport",
        _ => "",
    }
}

fn accion_recaptcha(op: &str) -> &'static str {
    match op {
        VENTA => "RCV_DDETV",
        COMPRA => "RCV_DDETC",
        _ => "",
    }
}

fn estado_export(op: &str) -> &'static str {
    match op {
        COMPRA => "REGISTRO",
        _ => "",
    }
}

pub fn es_nota_credito(tipo: i32) -> bool {
    tipo == 61
}

/// `RCV_ENDPOINTS` (COMPRA, VENTA y resumen).
pub fn endpoints() -> BTreeMap<String, String> {
    let mut e = BTreeMap::new();
    e.insert(COMPRA.into(), format!("{BASE}{}", metodo(COMPRA)));
    e.insert(VENTA.into(), format!("{BASE}{}", metodo(VENTA)));
    e.insert("resumen".into(), format!("{BASE}getResumen"));
    e
}

fn namespace(metodo: &str) -> String {
    format!("cl.sii.sdi.lob.diii.consdcv.data.api.interfaces.FacadeService/{metodo}")
}

/// +1 suma (facturas, ND); -1 notas de crédito.
pub fn signo_documento(tipo: i32) -> i64 {
    if es_nota_credito(tipo) {
        -1
    } else {
        1
    }
}

/// `DocumentoRCV` (una fila de rcv_documentos).
#[derive(Debug, Clone, Default, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct Documento {
    pub rut_empresa: String,
    pub tipo_operacion: String,
    pub periodo: String,
    pub tipo_dte: i32,
    pub folio: i64,
    pub fecha_doc: Option<String>,
    pub rut_contraparte: String,
    pub razon_social: String,
    pub monto_exento: i64,
    pub monto_neto: i64,
    pub monto_iva: i64,
    pub monto_iva_no_rec: i64,
    pub monto_iva_uso_comun: i64,
    pub monto_total: i64,
    pub estado_sii: String,
    pub clasificacion: Option<String>,
    pub origen: String,
}

// ---------------------------------------------------------------- normalización

/// `_num`: montos del RCV a entero (None, '', floats redondeados al par, "1.234").
pub fn num(v: Option<&Value>) -> i64 {
    match v {
        None | Some(Value::Null) => 0,
        Some(Value::Number(n)) => {
            if let Some(i) = n.as_i64() {
                i
            } else {
                n.as_f64().map(|f| f.round_ties_even() as i64).unwrap_or(0)
            }
        }
        Some(Value::String(s)) => {
            let s = s.trim().replace(['.', ','], "");
            if s.is_empty() {
                0
            } else {
                s.parse().unwrap_or(0)
            }
        }
        _ => 0,
    }
}

fn digitos(s: &str, n: usize) -> Option<u32> {
    if s.len() != n || !s.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    s.parse().ok()
}

/// Un parser estricto de los layouts de Go (`02/01/2006`, `2006-01-02`, `2006-01-02T15:04:05`,
/// `02-01-2006`): el texto debe calzar completo. Devuelve (año, mes, día).
fn parsear_layout(layout: &str, s: &str) -> Option<(u32, u32, u32)> {
    let partes: Vec<&str> = match layout {
        "02/01/2006" => {
            let p: Vec<&str> = s.split('/').collect();
            if p.len() != 3 {
                return None;
            }
            vec![p[2], p[1], p[0]]
        }
        "02-01-2006" => {
            let p: Vec<&str> = s.split('-').collect();
            if p.len() != 3 {
                return None;
            }
            vec![p[2], p[1], p[0]]
        }
        "2006-01-02" => {
            let p: Vec<&str> = s.split('-').collect();
            if p.len() != 3 {
                return None;
            }
            p
        }
        "2006-01-02T15:04:05" => {
            let (fecha, hora) = s.split_once('T')?;
            let h: Vec<&str> = hora.split(':').collect();
            if h.len() != 3 || h.iter().any(|x| digitos(x, 2).is_none()) {
                return None;
            }
            if digitos(h[0], 2)? > 23 || digitos(h[1], 2)? > 59 || digitos(h[2], 2)? > 59 {
                return None;
            }
            let p: Vec<&str> = fecha.split('-').collect();
            if p.len() != 3 {
                return None;
            }
            p
        }
        _ => return None,
    };
    let (y, m, d) = (
        digitos(partes[0], 4)?,
        digitos(partes[1], 2)?,
        digitos(partes[2], 2)?,
    );
    let dias = match m {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 => {
            if y % 4 == 0 && (y % 100 != 0 || y % 400 == 0) {
                29
            } else {
                28
            }
        }
        _ => return None,
    };
    if d < 1 || d > dias {
        return None;
    }
    Some((y, m, d))
}

/// `_fecha_iso`: dd/mm/yyyy, yyyy-mm-dd, …→ YYYY-MM-DD (None si vacío). Conserva la rareza
/// de Python: prueba cada formato sobre s[:len(fmt)+4] y, si ninguno calza, devuelve los 10
/// primeros caracteres tal cual (o None si hay menos de 10).
pub fn fecha_iso(v: Option<&Value>) -> Option<String> {
    let s = match v {
        None | Some(Value::Null) => return None,
        Some(Value::String(s)) => s.trim().to_string(),
        Some(x) => texto(Some(x)).trim().to_string(),
    };
    if s.is_empty() {
        return None;
    }
    for (py, layout) in [
        ("%d/%m/%Y", "02/01/2006"),
        ("%Y-%m-%d", "2006-01-02"),
        ("%Y-%m-%dT%H:%M:%S", "2006-01-02T15:04:05"),
        ("%d-%m-%Y", "02-01-2006"),
    ] {
        let n = py.len() + 4;
        let sub: String = s.chars().take(n).collect();
        if let Some((y, m, d)) = parsear_layout(layout, &sub) {
            return Some(format!("{y:04}-{m:02}-{d:02}"));
        }
    }
    if s.len() >= 10 {
        return Some(s[..10].to_string());
    }
    None
}

/// Acepta 'YYYY-MM', 'YYYYMM' o 'YYYY/MM' → 'YYYYMM'.
pub fn normalizar_periodo(p: &str) -> String {
    let s = p.replace(['-', '/'], "");
    let s = s.trim();
    s.chars().take(6).collect()
}

/// `core/rut.separar_rut`: ("12345678", "9") o ("", "") si no parece RUT.
pub fn separar_rut(rut: &str) -> (String, String) {
    let s = rut.trim().replace(['.', ' '], "").to_uppercase();
    if s.is_empty() {
        return (String::new(), String::new());
    }
    let (n, dv) = match s.split_once('-') {
        Some((n, dv)) => (n.to_string(), dv.to_string()),
        None => {
            if s.len() < 2 {
                return (String::new(), String::new());
            }
            (s[..s.len() - 1].to_string(), s[s.len() - 1..].to_string())
        }
    };
    if n.is_empty()
        || !n.bytes().all(|c| c.is_ascii_digit())
        || dv.len() != 1
        || !(dv == "K" || dv.bytes().all(|c| c.is_ascii_digit()))
    {
        return (String::new(), String::new());
    }
    (n, dv)
}

/// `_normalizar_rut`: NNNNNNNN-D o el texto tal cual si no se puede.
pub fn normalizar_rut(rut: &str) -> String {
    let (n, dv) = separar_rut(rut);
    if !n.is_empty() && !dv.is_empty() {
        format!("{n}-{dv}")
    } else {
        rut.to_string()
    }
}

fn get<'a>(f: &'a Map<String, Value>, claves: &[&str]) -> Option<&'a Value> {
    claves
        .iter()
        .find_map(|c| f.get(*c).filter(|v| !v.is_null()))
}

fn texto(v: Option<&Value>) -> String {
    match v {
        None | Some(Value::Null) => String::new(),
        Some(Value::String(s)) => s.clone(),
        Some(Value::Number(n)) => {
            if let Some(i) = n.as_i64() {
                i.to_string()
            } else {
                let f = n.as_f64().unwrap_or(0.0);
                if f == f.trunc() {
                    (f as i64).to_string()
                } else {
                    crate::totales::py_repr(f)
                }
            }
        }
        Some(x) => x.to_string(),
    }
}

/// `int(valor or 0)` de Python sobre lo que trae el facade (número o texto).
fn entero(v: Option<&Value>) -> i64 {
    match v {
        None | Some(Value::Null) => 0,
        Some(Value::Number(n)) => n.as_f64().unwrap_or(0.0) as i64,
        Some(Value::String(s)) => s.trim().parse().unwrap_or(0),
        _ => 0,
    }
}

/// Columnas del CSV del Export → campo del documento (orden de inserción = prioridad).
const COLUMNAS_CSV: [(&str, &str); 15] = [
    ("tipo doc", "tipo_dte"),
    ("folio", "folio"),
    ("fecha docto", "fecha_doc"),
    ("rut cliente", "rut_contraparte"),
    ("rut proveedor", "rut_contraparte"),
    ("razon social", "razon_social"),
    ("monto exento", "monto_exento"),
    ("monto neto", "monto_neto"),
    ("monto iva", "monto_iva"),
    ("monto iva recuperable", "monto_iva"),
    ("monto iva no recuperable", "monto_iva_no_rec"),
    ("monto iva no rec", "monto_iva_no_rec"),
    ("monto iva no rec.", "monto_iva_no_rec"),
    ("monto iva uso comun", "monto_iva_uso_comun"),
    ("monto total", "monto_total"),
];

fn campo_de_columna(col: &str) -> &'static str {
    if let Some((_, campo)) = COLUMNAS_CSV.iter().find(|(c, _)| *c == col) {
        return campo;
    }
    // "Monto Neto Activo Fijo" → monto_neto (prefijo largo)
    COLUMNAS_CSV
        .iter()
        .find(|(c, _)| col.starts_with(c) && c.len() > 9)
        .map(|(_, campo)| *campo)
        .unwrap_or("")
}

/// `_csv_a_dicts`: las líneas del Export a dicts con las llaves del facade.
fn csv_a_dicts(lineas: &[String]) -> Vec<Map<String, Value>> {
    let Some(primera) = lineas.first() else {
        return Vec::new();
    };
    let cab: Vec<String> = primera
        .split(';')
        .map(|c| c.trim().to_lowercase())
        .collect();
    let mut out = Vec::new();
    for ln in &lineas[1..] {
        if ln.trim().is_empty() {
            continue;
        }
        let vals: Vec<&str> = ln.split(';').collect();
        let mut fila: BTreeMap<&str, String> = BTreeMap::new();
        for (i, col) in cab.iter().enumerate() {
            let campo = campo_de_columna(col);
            if !campo.is_empty() && i < vals.len() {
                fila.entry(campo)
                    .or_insert_with(|| vals[i].trim().to_string()); // la primera columna que calza gana
            }
        }
        if fila.get("folio").is_none_or(|f| f.is_empty()) {
            continue;
        }
        let v = |k: &str| fila.get(k).cloned().unwrap_or_default();
        let (n, dv) = v("rut_contraparte")
            .split_once('-')
            .map(|(a, b)| (a.to_string(), b.to_string()))
            .unwrap_or((v("rut_contraparte"), String::new()));
        let mut m = Map::new();
        for (k, val) in [
            ("tipoDoc", v("tipo_dte")),
            ("folio", v("folio")),
            ("fecha", v("fecha_doc")),
            ("rut", n),
            ("dv", dv),
            ("rznSoc", v("razon_social")),
            ("mntExe", v("monto_exento")),
            ("mntNeto", v("monto_neto")),
            ("mntIVA", v("monto_iva")),
            ("mntIVANoRec", v("monto_iva_no_rec")),
            ("mntIVAUsoComun", v("monto_iva_uso_comun")),
            ("mntTotal", v("monto_total")),
        ] {
            m.insert(k.to_string(), Value::String(val));
        }
        out.push(m);
    }
    out
}

/// `normalizar_detalle`: la respuesta del facade (dict con data/detalle, lista de dicts o CSV
/// del Export) → documentos. Tolerante a las variantes de llaves.
pub fn normalizar_detalle(
    rut_empresa: &str,
    tipo_operacion: &str,
    periodo: &str,
    respuesta: &Value,
    origen: &str,
) -> Vec<Documento> {
    let origen = if origen.is_empty() { "rcv" } else { origen };
    let rut_empresa = normalizar_rut(rut_empresa);
    let periodo = normalizar_periodo(periodo);
    let filas: &Value = match respuesta {
        Value::Object(m) => get(m, &["data", "detalle", "detalles"]).unwrap_or(&Value::Null),
        x => x,
    };
    let Some(lista) = filas.as_array() else {
        return Vec::new();
    };
    let todas_texto = !lista.is_empty() && lista.iter().all(|x| x.is_string());
    let dicts: Vec<Map<String, Value>> = if todas_texto {
        csv_a_dicts(
            &lista
                .iter()
                .map(|x| x.as_str().unwrap_or("").to_string())
                .collect::<Vec<_>>(),
        )
    } else {
        lista
            .iter()
            .filter_map(|x| x.as_object().cloned())
            .collect()
    };
    dicts
        .iter()
        .map(|f| {
            let rut_c = texto(get(f, &["detRutDoc", "rutDoc", "rut"]));
            let dv_c = texto(get(f, &["detDvDoc", "dvDoc", "dv"]));
            let contraparte = if !rut_c.is_empty() && !dv_c.is_empty() {
                format!("{rut_c}-{dv_c}")
            } else {
                rut_c
            };
            let mut estado = texto(get(f, &["detEventoReceptor", "estado"]));
            if estado.is_empty() {
                estado = "REGISTRO".into();
            }
            Documento {
                rut_empresa: rut_empresa.clone(),
                tipo_operacion: tipo_operacion.to_string(),
                periodo: periodo.clone(),
                tipo_dte: entero(get(f, &["detTipoDoc", "tipoDoc", "tipoDte"])) as i32,
                folio: entero(get(f, &["detNroDoc", "nroDoc", "folio"])),
                fecha_doc: fecha_iso(get(f, &["detFchDoc", "fchDoc", "fecha"])),
                rut_contraparte: contraparte,
                razon_social: texto(get(f, &["detRznSoc", "rznSoc", "razonSocial"])),
                monto_exento: num(get(f, &["detMntExe", "mntExe", "montoExento"])),
                monto_neto: num(get(f, &["detMntNeto", "mntNeto", "montoNeto"])),
                monto_iva: num(get(f, &["detMntIVA", "mntIVA", "montoIva"])),
                monto_iva_no_rec: num(get(f, &["detMntIVANoRec", "detIVANoRec", "mntIVANoRec"])),
                monto_iva_uso_comun: num(get(f, &["detMntIVAUsoComun", "mntIVAUsoComun"])),
                monto_total: num(get(f, &["detMntTotal", "mntTotal", "montoTotal"])),
                estado_sii: estado,
                clasificacion: None,
                origen: origen.to_string(),
            }
        })
        .collect()
}

// ---------------------------------------------------------------- persistencia

/// `guardar_documentos`: upsert idempotente (UNIQUE rut, operación, tipo, folio, contraparte).
/// Devuelve cuántos se escribieron.
pub fn guardar_documentos(al: &Almacen, docs: &[Documento]) -> almacen::Resultado<usize> {
    if docs.is_empty() {
        return Ok(0);
    }
    let ahora = almacen::ahora_iso();
    let ahora = &ahora[..19];
    let mut c = al.conexion();
    let tx = c.transaction()?;
    {
        let mut st = tx.prepare(
            "INSERT INTO rcv_documentos (rut_empresa, tipo_operacion, periodo, tipo_dte, folio, fecha_doc, rut_contraparte, razon_social, monto_exento, monto_neto, monto_iva, monto_iva_no_rec, monto_iva_uso_comun, monto_total, estado_sii, clasificacion, origen, creado_en, actualizado_en) VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15,?16,?17,?18,?18) ON CONFLICT(rut_empresa, tipo_operacion, tipo_dte, folio, rut_contraparte) DO UPDATE SET periodo=excluded.periodo, fecha_doc=excluded.fecha_doc, razon_social=excluded.razon_social, monto_exento=excluded.monto_exento, monto_neto=excluded.monto_neto, monto_iva=excluded.monto_iva, monto_iva_no_rec=excluded.monto_iva_no_rec, monto_iva_uso_comun=excluded.monto_iva_uso_comun, monto_total=excluded.monto_total, estado_sii=excluded.estado_sii, origen=excluded.origen, actualizado_en=excluded.actualizado_en",
        )?;
        for d in docs {
            st.execute(params![
                d.rut_empresa,
                d.tipo_operacion,
                d.periodo,
                d.tipo_dte,
                d.folio,
                d.fecha_doc,
                d.rut_contraparte,
                d.razon_social,
                d.monto_exento,
                d.monto_neto,
                d.monto_iva,
                d.monto_iva_no_rec,
                d.monto_iva_uso_comun,
                d.monto_total,
                d.estado_sii,
                d.clasificacion,
                d.origen,
                ahora
            ])?;
        }
    }
    tx.commit()?;
    Ok(docs.len())
}

// ---------------------------------------------------------------- F29

/// `ResumenF29`.
#[derive(Debug, Clone, Default, serde::Serialize)]
pub struct Resumen {
    pub rut_empresa: String,
    pub periodo: String,
    pub ventas_netas: i64,
    pub ventas_exentas: i64,
    pub debito_fiscal: i64,
    pub n_docs_venta: i64,
    pub compras_netas: i64,
    pub compras_exentas: i64,
    pub credito_fiscal: i64,
    pub iva_no_recuperable: i64,
    pub n_docs_compra: i64,
    pub ppm: i64,
    pub remanente_anterior: i64,
}

impl Resumen {
    pub fn iva_determinado(&self) -> i64 {
        self.debito_fiscal - self.credito_fiscal - self.remanente_anterior
    }
    pub fn remanente_siguiente(&self) -> i64 {
        (-self.iva_determinado()).max(0)
    }
    pub fn total_a_pagar(&self) -> i64 {
        self.iva_determinado().max(0) + self.ppm
    }
}

struct FilaRcv {
    op: String,
    tipo: i32,
    neto: i64,
    exento: i64,
    iva: i64,
    iva_no_rec: i64,
}

fn filas_periodo(al: &Almacen, rut: &str, periodo: &str) -> almacen::Resultado<Vec<FilaRcv>> {
    let c = al.conexion();
    let mut st = c.prepare("SELECT tipo_operacion, tipo_dte, COALESCE(monto_neto,0), COALESCE(monto_exento,0), COALESCE(monto_iva,0), COALESCE(monto_iva_no_rec,0) FROM rcv_documentos WHERE rut_empresa=?1 AND periodo=?2")?;
    let filas = st
        .query_map(params![rut, periodo], |r| {
            Ok(FilaRcv {
                op: r.get(0)?,
                tipo: r.get(1)?,
                neto: r.get(2)?,
                exento: r.get(3)?,
                iva: r.get(4)?,
                iva_no_rec: r.get(5)?,
            })
        })?
        .collect::<Result<Vec<_>, _>>()?;
    Ok(filas)
}

/// `calcular_resumen_f29`: las NC (61) restan; el crédito excluye el IVA no recuperable; PPM y
/// remanente son insumos externos.
pub fn calcular_resumen(
    al: &Almacen,
    rut: &str,
    periodo: &str,
    ppm: i64,
    remanente: i64,
) -> almacen::Resultado<Resumen> {
    let mut r = Resumen {
        rut_empresa: normalizar_rut(rut),
        periodo: normalizar_periodo(periodo),
        ppm,
        remanente_anterior: remanente,
        ..Default::default()
    };
    for f in filas_periodo(al, &r.rut_empresa, &r.periodo)? {
        let s = signo_documento(f.tipo);
        if f.op == VENTA {
            r.ventas_netas += s * f.neto;
            r.ventas_exentas += s * f.exento;
            r.debito_fiscal += s * f.iva;
            r.n_docs_venta += 1;
        } else {
            r.compras_netas += s * f.neto;
            r.compras_exentas += s * f.exento;
            r.credito_fiscal += s * (f.iva - f.iva_no_rec);
            r.iva_no_recuperable += s * f.iva_no_rec;
            r.n_docs_compra += 1;
        }
    }
    Ok(r)
}

/// Códigos oficiales del F29 verificados contra el anverso del formulario.
pub const CODIGOS_F29: [(&str, &str, &str); 25] = [
    ("503", "Facturas emitidas — cantidad", "OK"),
    ("502", "Facturas emitidas — débito", "OK"),
    ("110", "Boletas — cantidad", "OK"),
    ("111", "Boletas — débito", "OK"),
    ("512", "Notas de débito emitidas — cantidad", "OK"),
    ("513", "Notas de débito emitidas — débito", "OK"),
    (
        "509",
        "Notas de crédito emitidas por facturas — cantidad",
        "OK",
    ),
    (
        "510",
        "Notas de crédito emitidas por facturas — rebaja al débito",
        "OK",
    ),
    (
        "586",
        "Ventas/servicios exentos o no gravados — cantidad",
        "OK",
    ),
    (
        "142",
        "Ventas/servicios exentos o no gravados — monto neto",
        "OK",
    ),
    ("538", "TOTAL DÉBITOS", "OK"),
    ("519", "Facturas recibidas del giro — cantidad", "OK"),
    (
        "520",
        "Facturas recibidas del giro — crédito recuperable",
        "OK",
    ),
    ("524", "Facturas activo fijo — cantidad", "OK"),
    ("525", "Facturas activo fijo — crédito", "OK"),
    ("527", "Notas de crédito recibidas — cantidad", "OK"),
    (
        "528",
        "Notas de crédito recibidas — rebaja al crédito",
        "OK",
    ),
    ("504", "Remanente crédito fiscal mes anterior", "OK"),
    ("537", "TOTAL CRÉDITOS", "OK"),
    ("89", "IVA determinado (a pagar)", "OK"),
    (
        "77",
        "Remanente crédito fiscal para el período siguiente",
        "OK",
    ),
    ("563", "Base imponible PPM (ingresos brutos del giro)", "OK"),
    ("62", "PPM neto determinado", "OK"),
    ("595", "Subtotal impuesto determinado anverso", "OK"),
    ("91", "TOTAL A PAGAR dentro del plazo legal", "OK"),
];

pub fn codigos_f29() -> BTreeMap<String, Vec<String>> {
    CODIGOS_F29
        .iter()
        .map(|(c, g, conf)| (c.to_string(), vec![g.to_string(), conf.to_string()]))
        .collect()
}

fn es_boleta(t: i32) -> bool {
    t == 39 || t == 41
}

/// `LineaF29`: un casillero.
#[derive(Debug, Clone, Default, serde::Serialize)]
pub struct Linea {
    pub codigo: String,
    pub glosa: String,
    pub valor: i64,
    pub confianza: String,
}

/// `DesgloseF29` (separa facturas, boletas y notas; pesos).
#[derive(Debug, Clone, Default, serde::Serialize)]
pub struct Desglose {
    pub rut_empresa: String,
    pub periodo: String,
    pub fact_emitidas_cant: i64,
    pub fact_emitidas_debito: i64,
    pub boletas_cant: i64,
    pub boletas_debito: i64,
    pub nc_emitidas_cant: i64,
    pub nc_emitidas_debito: i64,
    pub nd_emitidas_cant: i64,
    pub nd_emitidas_debito: i64,
    pub ventas_exentas: i64,
    pub exentas_cant: i64,
    pub total_debitos: i64,
    pub fact_recibidas_cant: i64,
    pub fact_recibidas_credito: i64,
    pub nc_recibidas_cant: i64,
    pub nc_recibidas_credito: i64,
    pub iva_no_recuperable: i64,
    pub total_creditos: i64,
    pub remanente_anterior: i64,
    pub ppm: i64,
    #[serde(skip)]
    pub base_ppm: i64,
}

impl Desglose {
    pub fn iva_determinado(&self) -> i64 {
        self.total_debitos - self.total_creditos - self.remanente_anterior
    }
    pub fn remanente_siguiente(&self) -> i64 {
        (-self.iva_determinado()).max(0)
    }
    pub fn total_a_pagar(&self) -> i64 {
        self.iva_determinado().max(0) + self.ppm
    }
}

/// `calcular_desglose_f29`.
pub fn calcular_desglose(
    al: &Almacen,
    rut: &str,
    periodo: &str,
    ppm: i64,
    remanente: i64,
) -> almacen::Resultado<Desglose> {
    let mut d = Desglose {
        rut_empresa: normalizar_rut(rut),
        periodo: normalizar_periodo(periodo),
        remanente_anterior: remanente,
        ppm,
        ..Default::default()
    };
    for f in filas_periodo(al, &d.rut_empresa, &d.periodo)? {
        if f.op == VENTA {
            d.base_ppm += f.neto + f.exento;
            if es_nota_credito(f.tipo) {
                d.nc_emitidas_cant += 1;
                d.nc_emitidas_debito += f.iva;
            } else if es_boleta(f.tipo) {
                d.boletas_cant += 1;
                d.boletas_debito += f.iva;
            } else if f.tipo == 56 {
                d.nd_emitidas_cant += 1;
                d.nd_emitidas_debito += f.iva;
            } else {
                d.fact_emitidas_cant += 1;
                d.fact_emitidas_debito += f.iva;
            }
            if f.exento != 0 {
                d.ventas_exentas += f.exento;
                d.exentas_cant += 1;
            }
        } else {
            let iva_rec = f.iva - f.iva_no_rec;
            d.iva_no_recuperable += f.iva_no_rec;
            if es_nota_credito(f.tipo) {
                d.nc_recibidas_cant += 1;
                d.nc_recibidas_credito += iva_rec;
            } else {
                d.fact_recibidas_cant += 1;
                d.fact_recibidas_credito += iva_rec;
            }
        }
    }
    d.total_debitos =
        d.fact_emitidas_debito + d.boletas_debito + d.nd_emitidas_debito - d.nc_emitidas_debito;
    d.total_creditos = d.fact_recibidas_credito - d.nc_recibidas_credito;
    Ok(d)
}

/// `mapear_a_f29`: casilleros en el orden del formulario, omitiendo los en cero.
pub fn mapear_a_f29(d: &Desglose) -> Vec<Linea> {
    let iva = d.iva_determinado().max(0);
    let cand: [(&str, i64); 22] = [
        ("503", d.fact_emitidas_cant),
        ("502", d.fact_emitidas_debito),
        ("110", d.boletas_cant),
        ("111", d.boletas_debito),
        ("512", d.nd_emitidas_cant),
        ("513", d.nd_emitidas_debito),
        ("509", d.nc_emitidas_cant),
        ("510", d.nc_emitidas_debito),
        ("586", d.exentas_cant),
        ("142", d.ventas_exentas),
        ("538", d.total_debitos),
        ("519", d.fact_recibidas_cant),
        ("520", d.fact_recibidas_credito),
        ("527", d.nc_recibidas_cant),
        ("528", d.nc_recibidas_credito),
        ("504", d.remanente_anterior),
        ("537", d.total_creditos),
        ("89", iva),
        ("77", d.remanente_siguiente()),
        ("563", d.base_ppm),
        ("62", d.ppm),
        ("91", d.total_a_pagar()),
    ];
    cand.iter()
        .filter(|(_, v)| *v != 0)
        .map(|(codigo, valor)| {
            let (glosa, conf) = CODIGOS_F29
                .iter()
                .find(|(c, _, _)| c == codigo)
                .map(|(_, g, c)| (g.to_string(), c.to_string()))
                .unwrap_or((codigo.to_string(), "CONF".into()));
            Linea {
                codigo: codigo.to_string(),
                glosa,
                valor: *valor,
                confianza: conf,
            }
        })
        .collect()
}

// ---------------------------------------------------------------- cliente

/// `RegistroCompraVenta`: con un token (semilla→token o la cookie TOKEN del portal) o con las
/// cookies de una sesión por clave (la forma VERIFICADA 2026-10-04).
pub struct Cliente {
    pub token: String,
    pub cookies: BTreeMap<String, String>,
    agente: ureq::Agent,
}

/// `_headers` (modo token).
pub fn headers(token: &str, num: &str, dv: &str) -> BTreeMap<String, String> {
    let mut h = BTreeMap::new();
    h.insert("User-Agent".into(), USER_AGENT.into());
    h.insert(
        "Cookie".into(),
        format!("TOKEN={token};RUT_NS={num};DV_NS={dv}"),
    );
    h.insert(
        "Content-Type".into(),
        "application/json;charset=UTF-8".into(),
    );
    h.insert("Accept".into(), "*/*".into());
    h
}

fn uuid4() -> String {
    let mut b = [0u8; 16];
    let t = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let mut x = (t as u64) ^ 0xA076_1D64_78BD_642F ^ (std::process::id() as u64).rotate_left(32);
    for chunk in b.chunks_mut(8) {
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        chunk.copy_from_slice(&x.to_le_bytes()[..chunk.len()]);
    }
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    let h: String = b.iter().map(|x| format!("{x:02x}")).collect();
    format!(
        "{}-{}-{}-{}-{}",
        &h[0..8],
        &h[8..12],
        &h[12..16],
        &h[16..20],
        &h[20..32]
    )
}

impl Cliente {
    pub fn nuevo(token: &str, cookies: BTreeMap<String, String>) -> Cliente {
        Cliente {
            token: token.to_string(),
            cookies,
            agente: agente(Duration::from_secs(60), true),
        }
    }

    fn token_de_sesion(&self) -> Result<String, String> {
        if !self.cookies.is_empty() {
            if let Some(t) = self.cookies.get("TOKEN").filter(|t| !t.is_empty()) {
                return Ok(t.clone());
            }
            return Ok(self.cookies.get("CSESSIONID").cloned().unwrap_or_default());
        }
        if !self.token.is_empty() {
            return Ok(self.token.clone());
        }
        Err("RegistroCompraVenta necesita un ClienteSII o un token.".into())
    }

    /// `_payload`: el body exacto del cliente web. `transaction_id` vacío → uuid nuevo.
    pub fn payload(
        &self,
        rut_empresa: &str,
        periodo: &str,
        tipo_operacion: &str,
        metodo_: &str,
        transaction_id: &str,
    ) -> Value {
        let (num, dv) = separar_rut(rut_empresa);
        let metodo_ = if metodo_.is_empty() {
            metodo(tipo_operacion)
        } else {
            metodo_
        };
        let mut conv = self
            .cookies
            .get("CSESSIONID")
            .cloned()
            .filter(|s| !s.is_empty())
            .or_else(|| self.cookies.get("TOKEN").cloned().filter(|s| !s.is_empty()))
            .unwrap_or_default();
        if conv.is_empty() {
            conv = self.token.clone();
        }
        if conv.is_empty() {
            conv = format!("{num}-{dv}");
        }
        let transaction_id = if transaction_id.is_empty() {
            uuid4()
        } else {
            transaction_id.to_string()
        };
        let mut data = json!({"rutEmisor": num, "dvEmisor": dv, "ptributario": normalizar_periodo(periodo), "estadoContab": "REGISTRO", "operacion": tipo_operacion});
        if metodo_.ends_with("Export") {
            data["codTipoDoc"] = json!(0);
            data["estadoContab"] = json!(estado_export(tipo_operacion));
            data["accionRecaptcha"] = json!(accion_recaptcha(tipo_operacion));
            data["tokenRecaptcha"] = json!(TOKEN_RECAPTCHA);
        } else {
            data["busquedaInicial"] = json!(true);
        }
        json!({"metaData": {"namespace": namespace(metodo_), "conversationId": conv, "transactionId": transaction_id, "page": Value::Null}, "data": data})
    }

    fn llamar(&self, u: &str, payload: &Value, rut_empresa: &str) -> Result<Value, String> {
        let (num, dv) = separar_rut(rut_empresa);
        let cuerpo = serde_json::to_vec(payload).map_err(|e| e.to_string())?;
        let mut req = self.agente.post(u);
        if !self.cookies.is_empty() {
            req = req
                .header("User-Agent", USER_AGENT)
                .header("Content-Type", "application/json")
                .header("Accept", "application/json, text/plain, */*")
                .header("Origin", "https://www4.sii.cl")
                .header("Referer", "https://www4.sii.cl/consdcvinternetui/");
            let partes: Vec<String> = self
                .cookies
                .iter()
                .map(|(k, v)| format!("{k}={v}"))
                .collect();
            req = req.header("Cookie", &partes.join("; "));
        } else {
            let tok = self.token_de_sesion()?;
            for (k, v) in headers(&tok, &num, &dv) {
                req = req.header(&k, &v);
            }
        }
        let r = ejecutar(req, &cuerpo)?;
        if r.status >= 400 {
            return Err(format!("RCV HTTP {}: {:.300}", r.status, r.texto()));
        }
        let data: Value = serde_json::from_slice(&r.cuerpo)
            .map_err(|_| format!("la respuesta del RCV no es JSON: {:.300}", r.texto()))?;
        if let Some(est) = data.get("respEstado").and_then(|v| v.as_object()) {
            if let Some(cod) = est.get("codRespuesta").filter(|c| !c.is_null()) {
                if num_v(cod) != 0 {
                    // codRespuesta 3 = período sin registro: no es error.
                    return Ok(json!({"data": [], "respEstado": est}));
                }
            }
        }
        Ok(data)
    }

    /// Baja el detalle del RCV de un período, normalizado (no persiste).
    pub fn descargar_periodo(
        &self,
        rut_empresa: &str,
        periodo: &str,
        tipo_operacion: &str,
    ) -> Result<Vec<Documento>, String> {
        let data = self.llamar(
            &endpoints()[tipo_operacion],
            &self.payload(rut_empresa, periodo, tipo_operacion, "", ""),
            rut_empresa,
        )?;
        Ok(normalizar_detalle(
            rut_empresa,
            tipo_operacion,
            periodo,
            &data,
            "rcv",
        ))
    }

    /// `descargar_resumen` (getResumen): totales por tipo de documento.
    pub fn descargar_resumen(
        &self,
        rut_empresa: &str,
        periodo: &str,
        tipo_operacion: &str,
    ) -> Result<Vec<Value>, String> {
        let data = self.llamar(
            &endpoints()["resumen"],
            &self.payload(rut_empresa, periodo, tipo_operacion, "getResumen", ""),
            rut_empresa,
        )?;
        Ok(data
            .get("data")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default())
    }

    /// Baja COMPRAS y VENTAS del período y las persiste → conteos.
    pub fn sincronizar_periodo(
        &self,
        al: &Almacen,
        rut_empresa: &str,
        periodo: &str,
    ) -> Result<BTreeMap<String, usize>, String> {
        let compras = self.descargar_periodo(rut_empresa, periodo, COMPRA)?;
        let ventas = self.descargar_periodo(rut_empresa, periodo, VENTA)?;
        let mut todos = compras.clone();
        todos.extend(ventas.iter().cloned());
        let n = guardar_documentos(al, &todos).map_err(|e| e.to_string())?;
        let mut out = BTreeMap::new();
        out.insert("compras".into(), compras.len());
        out.insert("ventas".into(), ventas.len());
        out.insert("guardados".into(), n);
        Ok(out)
    }
}

// ---------------------------------------------------------------- últimos movimientos

/// Lo que el SII tiene registrado del negocio, directo del RCV. Mismo comportamiento que
/// `periodos_por_defecto`/`parsear_periodos`/`ultimos_movimientos` de core/rcv.py.
pub const MAX_PERIODOS_MOVIMIENTOS: usize = 3;

pub fn mensaje_periodos() -> String {
    format!(
        "periodos debe ser una lista de meses YYYYMM separados por coma (máximo {MAX_PERIODOS_MOVIMIENTOS})."
    )
}

/// El mes en curso y el anterior (`hoy` = "YYYY-MM-DD…" local), del más nuevo al más viejo.
pub fn periodos_por_defecto(hoy: &str) -> Vec<String> {
    let a: i64 = hoy[..4].parse().unwrap_or(2000);
    let m: i64 = hoy[5..7].parse().unwrap_or(1);
    let (aa, ma) = if m == 1 { (a - 1, 12) } else { (a, m - 1) };
    vec![format!("{a:04}{m:02}"), format!("{aa:04}{ma:02}")]
}

/// "202610,2026-09" → ["202610", "202609"]; None si alguno no es un mes válido o son demasiados.
/// Vacío → `periodos_por_defecto` de hoy. Sin repetidos, en el orden pedido.
pub fn parsear_periodos(texto: &str) -> Option<Vec<String>> {
    let partes: Vec<String> = texto
        .split(',')
        .map(|p| p.trim().replace('-', ""))
        .filter(|p| !p.is_empty())
        .collect();
    if partes.is_empty() {
        return Some(periodos_por_defecto(&almacen::ahora_iso()));
    }
    let mut out: Vec<String> = Vec::new();
    for p in partes {
        let valido = p.len() == 6
            && p.bytes().all(|c| c.is_ascii_digit())
            && (1..=12).contains(&p[4..].parse::<u32>().unwrap_or(0));
        if !valido {
            return None;
        }
        if !out.contains(&p) {
            out.push(p);
        }
    }
    (out.len() <= MAX_PERIODOS_MOVIMIENTOS).then_some(out)
}

impl Cliente {
    /// Baja COMPRAS y VENTAS de cada período desde el SII, las persiste (como el F29) y devuelve
    /// los movimientos del más nuevo al más viejo: fecha del documento, período, operación, tipo
    /// y folio, todo descendente.
    pub fn ultimos_movimientos(
        &self,
        al: &Almacen,
        rut_empresa: &str,
        periodos: &[String],
        limite: usize,
    ) -> Result<Value, String> {
        let mut docs: Vec<Documento> = Vec::new();
        let mut conteos = Map::new();
        for periodo in periodos {
            let compras = self.descargar_periodo(rut_empresa, periodo, COMPRA)?;
            let ventas = self.descargar_periodo(rut_empresa, periodo, VENTA)?;
            conteos.insert(
                periodo.clone(),
                json!({"compras": compras.len(), "ventas": ventas.len()}),
            );
            let mut del = compras;
            del.extend(ventas);
            guardar_documentos(al, &del).map_err(|e| e.to_string())?;
            docs.extend(del);
        }
        docs.sort_by(|a, b| {
            let clave = |d: &Documento| {
                (
                    d.fecha_doc.clone().unwrap_or_default(),
                    d.periodo.clone(),
                    d.tipo_operacion.clone(),
                    d.tipo_dte,
                    d.folio,
                )
            };
            clave(b).cmp(&clave(a))
        });
        let movs: Vec<Value> = docs
            .iter()
            .take(limite)
            .map(|d| {
                json!({
                    "tipo_operacion": d.tipo_operacion, "periodo": d.periodo, "tipo_dte": d.tipo_dte,
                    "folio": d.folio, "fecha_doc": d.fecha_doc, "rut_contraparte": d.rut_contraparte,
                    "razon_social": d.razon_social, "monto_exento": d.monto_exento,
                    "monto_neto": d.monto_neto, "monto_iva": d.monto_iva,
                    "monto_total": d.monto_total, "estado_sii": d.estado_sii,
                })
            })
            .collect();
        Ok(json!({
            "periodos": periodos, "documentos": conteos, "total": docs.len(),
            "sin_datos": docs.is_empty(), "movimientos": movs,
        }))
    }
}

fn num_v(v: &Value) -> i64 {
    num(Some(v))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Normalización, payload, persistencia y F29 iguales a core/rcv.py sobre
    /// tests/dorado/rcv/ (datos ficticios). L15.
    #[test]
    fn paridad_con_python() {
        let base = format!("{}/../tests/dorado/rcv", env!("CARGO_MANIFEST_DIR"));
        let Ok(raw_in) = std::fs::read(format!("{base}/entrada.json")) else {
            return;
        };
        let entrada: Value = serde_json::from_slice(&raw_in).unwrap();
        let esp: Map<String, Value> =
            serde_json::from_slice(&std::fs::read(format!("{base}/esperado.json")).unwrap())
                .unwrap();
        let mut got: Map<String, Value> = Map::new();
        let ventas =
            normalizar_detalle("77.777.777-7", VENTA, "2026-01", &entrada["csv_ventas"], "");
        let compras = normalizar_detalle(
            "77777777-7",
            COMPRA,
            "202601",
            &entrada["dict_compras"],
            "csv",
        );
        got.insert("ventas".into(), json!(ventas));
        got.insert("compras".into(), json!(compras));
        got.insert(
            "lista_directa".into(),
            json!(normalizar_detalle(
                "77777777-7",
                VENTA,
                "202601",
                &json!([{"tipoDoc": 33.0, "folio": 1.0}]),
                ""
            )),
        );
        got.insert(
            "vacios".into(),
            json!([
                normalizar_detalle("77777777-7", VENTA, "202601", &json!({"data": null}), ""),
                normalizar_detalle("77777777-7", VENTA, "202601", &json!({"data": []}), "")
            ]),
        );
        let nums: Vec<i64> = [
            Value::Null,
            json!(""),
            json!(12.0),
            json!(12.6),
            json!("1.234"),
            json!("1,234"),
            json!("x"),
            json!(-3.5),
        ]
        .iter()
        .map(|v| num(Some(v)))
        .collect();
        got.insert("num".into(), json!(nums));
        let fechas: Vec<Option<String>> = [
            Value::Null,
            json!(""),
            json!("15/01/2026"),
            json!("2026-01-15"),
            json!("2026-01-15T10:00:00"),
            json!("15-01-2026"),
            json!("garbage!!!"),
            json!("2026"),
        ]
        .iter()
        .map(|v| fecha_iso(Some(v)))
        .collect();
        got.insert("fecha".into(), json!(fechas));
        got.insert(
            "periodo".into(),
            json!([
                normalizar_periodo("2026-01"),
                normalizar_periodo("202601"),
                normalizar_periodo("2026/01"),
                normalizar_periodo(" 202601 ")
            ]),
        );
        let cli = Cliente::nuevo("TOK", BTreeMap::new());
        got.insert(
            "payload".into(),
            json!({"venta": cli.payload("77777777-7", "2026-01", VENTA, "", "<uuid>"), "compra": cli.payload("77777777-7", "202601", COMPRA, "", "<uuid>"), "resumen": cli.payload("77777777-7", "202601", VENTA, "getResumen", "<uuid>")}),
        );
        let mut ck = BTreeMap::new();
        ck.insert("CSESSIONID".to_string(), "CS".to_string());
        ck.insert("TOKEN".to_string(), "T2".to_string());
        got.insert(
            "payload_cookies".into(),
            Cliente::nuevo("", ck).payload("77777777-7", "202601", VENTA, "", "<uuid>"),
        );
        got.insert("headers".into(), json!(headers("TOK", "77777777", "7")));
        got.insert("endpoints".into(), json!(endpoints()));

        let dir = std::env::temp_dir().join(format!("rcv-{}", std::process::id()));
        let _ = std::fs::create_dir_all(&dir);
        let al = Almacen::abrir_y_migrar(dir.join("rcv.db").to_str().unwrap()).unwrap();
        let mut todos = ventas.clone();
        todos.extend(compras.iter().cloned());
        let n1 = guardar_documentos(&al, &todos).unwrap();
        let n2 = guardar_documentos(&al, &todos).unwrap();
        got.insert("guardados".into(), json!(n1));
        got.insert("guardados_otra_vez".into(), json!(n2));
        let filas: i64 = al
            .conexion()
            .query_row("SELECT COUNT(*) FROM rcv_documentos", [], |r| r.get(0))
            .unwrap();
        got.insert("filas_en_bd".into(), json!(filas));
        let d = calcular_desglose(&al, "77777777-7", "202601", 1234, 500).unwrap();
        let mut dm = serde_json::to_value(&d).unwrap();
        dm["iva_determinado"] = json!(d.iva_determinado());
        dm["remanente_siguiente"] = json!(d.remanente_siguiente());
        dm["base_ppm"] = json!(d.base_ppm);
        dm["total_a_pagar"] = json!(d.total_a_pagar());
        got.insert("desglose".into(), dm);
        got.insert("casilleros".into(), json!(mapear_a_f29(&d)));
        let d2 = calcular_desglose(&al, "77777777-7", "202601", 0, 900000).unwrap();
        got.insert("casilleros_remanente".into(), json!(mapear_a_f29(&d2)));
        let r = calcular_resumen(&al, "77777777-7", "202601", 10, 0).unwrap();
        let mut rm = serde_json::to_value(&r).unwrap();
        rm["iva_determinado"] = json!(r.iva_determinado());
        rm["remanente_siguiente"] = json!(r.remanente_siguiente());
        rm["total_a_pagar"] = json!(r.total_a_pagar());
        got.insert("resumen".into(), rm);
        got.insert("codigos_f29".into(), json!(codigos_f29()));
        let _ = std::fs::remove_dir_all(&dir);

        let mut fallos = Vec::new();
        for (k, esperado) in &esp {
            let g = got.get(k).cloned().unwrap_or(Value::Null);
            if &g != esperado {
                fallos.push(format!("{k} difiere\n  python: {esperado}\n  rust:   {g}"));
            }
        }
        assert!(fallos.is_empty(), "{}", fallos.join("\n"));
    }
}

#[cfg(test)]
mod tests_movimientos {
    use super::*;

    #[test]
    fn periodos_de_movimientos() {
        assert_eq!(periodos_por_defecto("2026-10-19"), ["202610", "202609"]);
        assert_eq!(periodos_por_defecto("2026-01-05T10:00"), ["202601", "202512"]);
        assert_eq!(
            parsear_periodos("202610, 2026-09,202610").unwrap(),
            ["202610", "202609"]
        );
        assert_eq!(parsear_periodos("").unwrap().len(), 2);
        for malo in ["202613", "2026", "abcdef", "202600", "202601,202602,202603,202604"] {
            assert!(parsear_periodos(malo).is_none(), "{malo}");
        }
    }
}
