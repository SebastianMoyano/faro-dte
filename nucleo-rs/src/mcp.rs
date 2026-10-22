//! El servidor MCP del motor (`mcp_server.py`, `nucleo/mcp`): expone el toolkit del SII como
//! herramientas para una IA (Claude, Cursor, Codex, Gemini u otro cliente MCP). Corre el motor
//! EN EL MISMO PROCESO y llama a sus rutas sin red; las credenciales nunca viajan por el
//! protocolo: cada herramienta recibe un cert_id del keystore.
//!
//! Transportes: `servir_stdio` (JSON-RPC por líneas) y `atender_http` (Streamable HTTP de la
//! spec 2025-06-18, sin estado: POST /mcp con Authorization: Bearer).

use crate::servidor::http::{Peticion, Respuesta};
use crate::servidor::Servidor;
use serde_json::{json, Map, Value};
use std::io::{BufRead, Write};
use std::sync::Arc;

pub const VERSION: &str = "1.2.0";
pub const PROTOCOL_VERSION: &str = "2025-06-18";
const VERSIONES_SOPORTADAS: [&str; 3] = ["2025-06-18", "2025-03-26", "2024-11-05"];

pub const INSTRUCCIONES: &str = "Motor de facturación electrónica DTE para el SII de Chile. Usa estas herramientas para consultar autorización de empresas, gestionar folios (timbrar/anular), y consultar el estado de envíos. Los tipos de DTE son: 33 Factura, 34 Factura Exenta, 39/41 Boletas, 52 Guía, 56 Nota Débito, 61 Nota Crédito. Los RUT van con guión y dígito verificador (ej. 76111111-6). El 'ambiente' es 'certificacion' (pruebas, Maullín) o 'produccion' (Palena).";

type Ejecutor = Box<dyn Fn(&Mcp, &Map<String, Value>) -> Result<Value, String> + Send + Sync>;

struct Herramienta {
    nombre: &'static str,
    descripcion: &'static str,
    esquema: Value,
    ejecutar: Ejecutor,
}

/// El servidor MCP sobre un motor en proceso.
pub struct Mcp {
    pub s: Arc<Servidor>,
    pub cuenta: i64,
    pub ambiente: String,
    herramientas: Vec<Herramienta>,
}

fn prop(tipo: &str, desc: &str) -> Value {
    json!({"type": tipo, "description": desc})
}

fn esquema(req: &[&str], props: Value) -> Value {
    let mut e = json!({"type": "object", "properties": props});
    if !req.is_empty() {
        e["required"] = json!(req);
    }
    e
}

/// `texto(a, k, def)`: el valor como texto (un número se imprime), o `def` si falta.
pub fn texto(a: &Map<String, Value>, k: &str, def: &str) -> String {
    match a.get(k) {
        None | Some(Value::Null) => def.to_string(),
        Some(Value::String(s)) if !s.is_empty() => s.clone(),
        Some(Value::String(_)) => String::new(),
        Some(v) => v.to_string(),
    }
}

pub fn entero(a: &Map<String, Value>, k: &str, def: i64) -> i64 {
    match a.get(k) {
        Some(Value::Number(n)) => n
            .as_i64()
            .or_else(|| n.as_f64().map(|f| f as i64))
            .unwrap_or(def),
        Some(Value::String(s)) => s.trim().parse().unwrap_or(0),
        _ => def,
    }
}

/// `structuredContent` debe ser un objeto: una lista va en {"items": …} y un escalar en {"valor": …}.
pub fn estructurado(v: &Value) -> Value {
    match v {
        Value::Object(_) => v.clone(),
        Value::Array(_) => json!({"items": v}),
        _ => json!({"valor": v}),
    }
}

impl Mcp {
    /// `Nuevo`: cuenta = usuario dueño de los negocios (1 = admin); ambiente = el del .env.
    pub fn nuevo(s: Arc<Servidor>, cuenta: i64, ambiente: &str) -> Mcp {
        let mut m = Mcp {
            s,
            cuenta,
            ambiente: ambiente.to_string(),
            herramientas: Vec::new(),
        };
        m.registrar();
        m
    }

    /// Ejecuta una ruta del motor en proceso; los errores del envoltorio salen como error de tool.
    pub fn llamar(&self, metodo: &str, ruta: &str, cuerpo: Option<Value>) -> Result<Value, String> {
        let b = cuerpo.map(|c| serde_json::to_vec(&c).unwrap_or_default());
        let (st, resp) = self.s.llamar(metodo, ruta, b.as_deref());
        let v: Value = serde_json::from_slice(&resp).map_err(|_| {
            format!(
                "respuesta no JSON ({st}): {:.300}",
                String::from_utf8_lossy(&resp)
            )
        })?;
        if st >= 400 {
            if let Some(e) = v.get("error").and_then(|e| e.as_object()) {
                return Err(format!(
                    "[{}] {}",
                    texto(e, "codigo", ""),
                    texto(e, "mensaje", "")
                ));
            }
            return Err(format!(
                "HTTP {st}: {:.300}",
                String::from_utf8_lossy(&resp)
            ));
        }
        Ok(v)
    }

    pub fn herramientas(&self) -> Vec<&'static str> {
        self.herramientas.iter().map(|h| h.nombre).collect()
    }

    fn h(
        &mut self,
        nombre: &'static str,
        descripcion: &'static str,
        esquema: Value,
        f: impl Fn(&Mcp, &Map<String, Value>) -> Result<Value, String> + Send + Sync + 'static,
    ) {
        self.herramientas.push(Herramienta {
            nombre,
            descripcion,
            esquema,
            ejecutar: Box::new(f),
        });
    }

    fn registrar(&mut self) {
        let cert_id = || {
            prop(
                "integer",
                "id del certificado en el keystore (ver listar_certificados)",
            )
        };
        let ambiente = || {
            prop(
                "string",
                "certificacion | produccion (default: el del .env)",
            )
        };
        let amb = |m: &Mcp, a: &Map<String, Value>| texto(a, "ambiente", &m.ambiente);
        self.h("salud", "Estado del servidor MCP: ambiente por defecto, cuenta y nº de certificados.", esquema(&[], json!({})), |m, _| {
            let v = m.llamar("GET", "/api/v1/keystore/certificados", None)?;
            let n = v.as_array().map(|l| l.len()).unwrap_or(0);
            Ok(json!({"servicio": "dte-chile-mcp", "motor": "rust", "ambiente_defecto": m.ambiente, "cuenta_id": m.cuenta, "certificados": n}))
        });
        self.h("listar_certificados", "Lista los certificados cargados en el keystore (id, RUT, nombre, vencimiento). Usa el id como cert_id en las demás herramientas. NUNCA expone la clave privada.", esquema(&[], json!({})), |m, _| m.llamar("GET", "/api/v1/keystore/certificados", None));
        self.h(
            "listar_negocios",
            "Lista los negocios (empresas) registrados en la cuenta con su cert asociado.",
            esquema(&[], json!({})),
            |m, _| m.llamar("GET", "/api/v1/keystore/negocios", None),
        );
        self.h("empresa_autorizada", "¿Qué DTE está autorizada a emitir una empresa? (consulta PÚBLICA, sin cert). Devuelve razón social, nº/fecha de resolución y los tipos autorizados; o null si no aparece autorizada.", esquema(&["rut"], json!({"rut": prop("string", "RUT con guión y DV"), "ambiente": ambiente()})), move |m, a| {
            match m.llamar("GET", &format!("/api/v1/keystore/empresa?rut={}&ambiente={}", texto(a, "rut", ""), amb(m, a)), None) {
                Err(e) if e.starts_with("[no_encontrado]") => Ok(Value::Null),
                r => r,
            }
        });
        self.h("diagnostico", "Investigación automática (SOLO LECTURA) del estado de una empresa en el SII + plan de acciones (modo auto/consentimiento/humano). Primer paso del onboarding.", esquema(&["rut", "cert_id"], json!({"rut": prop("string", "RUT con guión"), "cert_id": cert_id(), "nombre_sistema": prop("string", "cómo llamar al software propio")})), |m, a| {
            m.llamar("GET", &format!("/api/v1/onboarding/diagnostico?cert_id={}&rut={}&nombre_sistema={}", entero(a, "cert_id", 0), texto(a, "rut", ""), texto(a, "nombre_sistema", "tu software propio")), None)
        });
        self.h("diagnostico_cartera", "Diagnostica TODAS las empresas asociadas a un certificado (resumen por empresa). Solo lectura.", esquema(&["cert_id"], json!({"cert_id": cert_id(), "nombre_sistema": prop("string", "cómo llamar al software propio")})), |m, a| {
            m.llamar("GET", &format!("/api/v1/onboarding/cartera?cert_id={}&nombre_sistema={}", entero(a, "cert_id", 0), texto(a, "nombre_sistema", "tu software propio")), None)
        });
        self.h("salud_folios", "Salud de folios/CAF de una empresa — LOCAL, sin tocar el SII: CAF vencidos (6 meses, CAF-3-517) y folios agotándose, con veredicto ok/atencion/critico.", esquema(&["rut"], json!({"rut": prop("string", "RUT con guión")})), |m, a| {
            m.llamar("GET", &format!("/api/v1/monitoreo/folios?rut={}", texto(a, "rut", "")), None)
        });
        self.h("salud_folios_cartera", "Salud de folios/CAF de TODAS las empresas cargadas — LOCAL, sin SII, ordenadas por urgencia.", esquema(&[], json!({})), |m, _| m.llamar("GET", "/api/v1/monitoreo/folios/cartera", None));
        self.h("situacion_folios", "Situación de folios por tipo: si puede timbrar y si está bloqueado (anti-acaparamiento). Requiere cert_id.", esquema(&["rut", "cert_id"], json!({"rut": prop("string", ""), "cert_id": cert_id(), "tipos": {"type": "array", "items": {"type": "integer"}, "description": "tipos de DTE (ej. [33, 61]); si se omite, los habituales"}, "ambiente": ambiente()})), move |m, a| {
            m.llamar("POST", "/api/v1/portal/situacion", Some(json!({"cert_id": entero(a, "cert_id", 0), "rut": texto(a, "rut", ""), "tipos": a.get("tipos").cloned().unwrap_or(Value::Null), "ambiente": amb(m, a)})))
        });
        self.h("timbrajes", "Rangos de folios ya autorizados (timbrados) para un RUT y tipo de DTE.", esquema(&["rut", "tipo_dte", "cert_id"], json!({"rut": prop("string", ""), "tipo_dte": prop("integer", ""), "cert_id": cert_id(), "ambiente": ambiente()})), move |m, a| {
            m.llamar("POST", "/api/v1/portal/timbrajes", Some(json!({"cert_id": entero(a, "cert_id", 0), "rut": texto(a, "rut", ""), "tipo_dte": entero(a, "tipo_dte", 33), "ambiente": amb(m, a)})))
        });
        self.h("datos_software", "Software de emisión registrado y resolución del contribuyente en el SII.", esquema(&["rut", "cert_id"], json!({"rut": prop("string", ""), "cert_id": cert_id(), "ambiente": ambiente()})), move |m, a| {
            m.llamar("POST", "/api/v1/portal/software", Some(json!({"cert_id": entero(a, "cert_id", 0), "rut": texto(a, "rut", ""), "ambiente": amb(m, a)})))
        });
        self.h(
            "empresas_del_certificado",
            "Empresas a las que está asociado el titular de un certificado (mandatario).",
            esquema(
                &["cert_id"],
                json!({"cert_id": cert_id(), "ambiente": ambiente()}),
            ),
            move |m, a| {
                m.llamar(
                    "POST",
                    "/api/v1/portal/empresas",
                    Some(json!({"cert_id": entero(a, "cert_id", 0), "ambiente": amb(m, a)})),
                )
            },
        );
        self.h("folios_anulables", "Rangos de folios que se pueden ANULAR (no recepcionados) para un tipo de DTE.", esquema(&["rut", "tipo_dte", "cert_id"], json!({"rut": prop("string", ""), "tipo_dte": prop("integer", ""), "cert_id": cert_id(), "ambiente": ambiente()})), move |m, a| {
            m.llamar("POST", "/api/v1/portal/anulables", Some(json!({"cert_id": entero(a, "cert_id", 0), "rut": texto(a, "rut", ""), "tipo_dte": entero(a, "tipo_dte", 33), "ambiente": amb(m, a)})))
        });
        self.h("estado_envio", "Estado de un envío al SII por su TrackID (EPR/aceptado/rechazado + glosa).", esquema(&["track_id", "rut", "cert_id"], json!({"track_id": prop("integer", ""), "rut": prop("string", "RUT con guión"), "cert_id": cert_id(), "ambiente": ambiente()})), move |m, a| {
            let v = m.llamar("POST", "/api/v1/estado/lote", Some(json!({"track_ids": [entero(a, "track_id", 0)], "rut_empresa": texto(a, "rut", ""), "cert_id": entero(a, "cert_id", 0), "ambiente": amb(m, a)})))?;
            if let Some(d) = v.get("detalles").and_then(|d| d.as_array()) {
                if d.len() == 1 {
                    return Ok(d[0].clone());
                }
            }
            Ok(v)
        });
        self.h("estado_envios", "Estado de VARIOS envíos al SII de una vez (por TrackID) + resumen accionable (aceptados/rechazados/pendientes/todos_resueltos).", esquema(&["track_ids", "rut", "cert_id"], json!({"track_ids": {"type": "array", "items": {"type": "integer"}}, "rut": prop("string", ""), "cert_id": cert_id(), "ambiente": ambiente()})), move |m, a| {
            m.llamar("POST", "/api/v1/estado/lote", Some(json!({"track_ids": a.get("track_ids").cloned().unwrap_or(Value::Null), "rut_empresa": texto(a, "rut", ""), "cert_id": entero(a, "cert_id", 0), "ambiente": amb(m, a)})))
        });
        self.h("solicitar_folios", "TIMBRA (solicita) folios nuevos al SII. ACCIÓN REAL: consume cupo de timbraje. Verifica bloqueo antes; forzar=true solo si sabes lo que haces. Devuelve {obtenido, info, caf_xml}.", esquema(&["rut", "tipo_dte", "cert_id"], json!({"rut": prop("string", ""), "tipo_dte": prop("integer", ""), "cert_id": cert_id(), "cantidad": prop("integer", "default 1"), "ambiente": ambiente(), "forzar": prop("boolean", "saltar el tope por sesión")})), move |m, a| {
            let f = a.get("forzar").and_then(Value::as_bool).unwrap_or(false);
            m.llamar("POST", "/api/v1/portal/solicitar", Some(json!({"cert_id": entero(a, "cert_id", 0), "rut": texto(a, "rut", ""), "tipo_dte": entero(a, "tipo_dte", 33), "cantidad": entero(a, "cantidad", 1), "ambiente": amb(m, a), "forzar": f})))
        });
        self.h("anular_folios", "ANULA un rango de folios NO recepcionados. ACCIÓN REAL E IRREVERSIBLE. Solo con el mandatario que los timbró.", esquema(&["rut", "tipo_dte", "folio_desde", "folio_hasta", "cert_id"], json!({"rut": prop("string", ""), "tipo_dte": prop("integer", ""), "folio_desde": prop("integer", ""), "folio_hasta": prop("integer", ""), "cert_id": cert_id(), "motivo": prop("string", ""), "ambiente": ambiente()})), move |m, a| {
            m.llamar("POST", "/api/v1/portal/anular", Some(json!({"cert_id": entero(a, "cert_id", 0), "rut": texto(a, "rut", ""), "tipo_dte": entero(a, "tipo_dte", 33), "folio_desde": entero(a, "folio_desde", 0), "folio_hasta": entero(a, "folio_hasta", 0), "motivo": texto(a, "motivo", ""), "ambiente": amb(m, a)})))
        });
        self.h("enviar_dte", "ENVÍA un EnvioDTE (o EnvioBOLETA con tipo_dte 39/41) firmado al SII. ACCIÓN REAL. Devuelve el TrackID; luego consulta estado_envio.", esquema(&["xml_envio_b64", "rut_empresa", "cert_id"], json!({"xml_envio_b64": prop("string", "el sobre en base64 (el xml_envio_b64 de emitir_dte)"), "rut_empresa": prop("string", "RUT con guión"), "cert_id": cert_id(), "tipo_dte": prop("integer", "default 33"), "ambiente": ambiente()})), move |m, a| {
            m.llamar("POST", "/api/v1/dte/enviar-keystore", Some(json!({"xml_envio_b64": texto(a, "xml_envio_b64", ""), "rut_empresa": texto(a, "rut_empresa", ""), "cert_id": entero(a, "cert_id", 0), "tipo_dte": entero(a, "tipo_dte", 33), "ambiente": amb(m, a)})))
        });
        self.h("previsualizar_dte", "Previsualiza un DTE: FIRMADO + timbrado + XSD + PDF, SIN enviarlo ni consumir folios. Devuelve totales, XSD y XML/PDF en base64.", esquema(&["dte", "cert_id"], json!({"dte": {"type": "object", "description": "DTEInput: tipo_dte, folio (0 = el primero del CAF), fecha_emision, emisor, receptor, items, referencias"}, "cert_id": cert_id()})), |m, a| {
            m.llamar("POST", "/api/v1/dte/previsualizar", Some(json!({"dte": a.get("dte").cloned().unwrap_or(Value::Null), "cert_id": entero(a, "cert_id", 0)})))
        });
        self.h("emitir_dte", "EMITE un DTE o BOLETA completo. ACCIÓN REAL: consume un folio, firma, genera PDF y persiste. NO envía al SII (usa enviar_dte con el xml_envio_b64 que devuelve). cert_id del keystore; si se omite, el certificado del .env.", esquema(&["dte"], json!({"dte": {"type": "object", "description": "DTEInput (folio 0 = automático)"}, "cert_id": cert_id()})), |m, a| {
            let mut cuerpo = json!({"dte": a.get("dte").cloned().unwrap_or(Value::Null)});
            if a.contains_key("cert_id") {
                cuerpo["cert_id"] = json!(entero(a, "cert_id", 0));
            }
            m.llamar("POST", "/api/v1/dte/emitir", Some(cuerpo))
        });
    }

    /// Resuelve un mensaje JSON-RPC del protocolo MCP (None = notificación sin respuesta).
    pub fn atender(&self, raw: &[u8]) -> Option<Value> {
        let req: Value = match serde_json::from_slice(raw) {
            Ok(v) => v,
            Err(_) => return Some(error_rpc(Value::Null, -32700, "JSON inválido")),
        };
        let id = req.get("id").cloned().unwrap_or(Value::Null);
        let metodo = req.get("method").and_then(Value::as_str).unwrap_or("");
        match metodo {
            "initialize" => Some(respuesta(
                id,
                json!({"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}},
                "serverInfo": {"name": "dte-chile", "version": VERSION}, "instructions": INSTRUCCIONES}),
            )),
            "notifications/initialized" | "notifications/cancelled" => None,
            "ping" => Some(respuesta(id, json!({}))),
            "tools/list" => {
                let lista: Vec<Value> = self.herramientas.iter().map(|h| json!({"name": h.nombre, "description": h.descripcion, "inputSchema": h.esquema})).collect();
                Some(respuesta(id, json!({"tools": lista})))
            }
            "tools/call" => {
                let p = req.get("params").cloned().unwrap_or(Value::Null);
                let nombre = p
                    .get("name")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string();
                let args: Map<String, Value> = p
                    .get("arguments")
                    .and_then(|a| a.as_object())
                    .cloned()
                    .unwrap_or_default();
                let Some(h) = self.herramientas.iter().find(|h| h.nombre == nombre) else {
                    return Some(error_rpc(
                        id,
                        -32602,
                        &format!("herramienta desconocida: {nombre}"),
                    ));
                };
                Some(match (h.ejecutar)(self, &args) {
                    Err(e) => respuesta(
                        id,
                        json!({"content": [{"type": "text", "text": e}], "isError": true}),
                    ),
                    Ok(v) => respuesta(
                        id,
                        json!({"content": [{"type": "text", "text": v.to_string()}], "structuredContent": estructurado(&v)}),
                    ),
                })
            }
            _ => {
                if id.is_null() {
                    return None;
                }
                Some(error_rpc(
                    id,
                    -32601,
                    &format!("método no soportado: {metodo}"),
                ))
            }
        }
    }

    /// El transporte stdio: un mensaje JSON por línea, respuestas por línea. Vuelve al cerrarse la entrada.
    pub fn servir_stdio(&self, r: impl BufRead, mut w: impl Write) {
        for linea in r.lines() {
            let Ok(l) = linea else { return };
            if l.trim().is_empty() {
                continue;
            }
            if let Some(resp) = self.atender(l.as_bytes()) {
                let _ = writeln!(w, "{resp}");
                let _ = w.flush();
            }
        }
    }

    /// El transporte Streamable HTTP (spec 2025-06-18) sin estado: un solo endpoint /mcp; POST
    /// lleva un mensaje JSON-RPC y responde JSON; una notificación responde 202 sin cuerpo; GET
    /// y DELETE responden 405. Valida Origin (DNS rebinding), negocia MCP-Protocol-Version y
    /// exige Authorization: Bearer con el token fijo o un JWT del motor.
    pub fn atender_http(&self, token: &str, p: &Peticion) -> Respuesta {
        if p.ruta != "/mcp" {
            return Respuesta::texto(404, "text/plain; charset=utf-8", "404 page not found\n");
        }
        if !origen_permitido(p) {
            return http_error(
                403,
                "origen",
                "Origin no permitido (protección contra DNS rebinding).",
            );
        }
        if !self.autorizado(token, p) {
            let mut r = http_error(
                401,
                "autenticacion",
                "Token MCP invalido o ausente (Authorization: Bearer).",
            );
            r.poner("WWW-Authenticate", "Bearer realm=\"mcp\"");
            return r;
        }
        let v = p.cabecera("MCP-Protocol-Version");
        if !v.is_empty() && !VERSIONES_SOPORTADAS.contains(&v) {
            return http_error(
                400,
                "version",
                &format!("MCP-Protocol-Version no soportada: {v}"),
            );
        }
        let con_version = |mut r: Respuesta| {
            r.poner("MCP-Protocol-Version", PROTOCOL_VERSION);
            r
        };
        if p.metodo != "POST" {
            return con_version(Respuesta::nueva(405).con("Allow", "POST"));
        }
        if !acepta_json(p.cabecera("Accept")) {
            return con_version(http_error(
                406,
                "accept",
                "El cliente debe aceptar application/json (o text/event-stream).",
            ));
        }
        let ct = p.cabecera("Content-Type");
        if !ct.is_empty() && !ct.to_ascii_lowercase().starts_with("application/json") {
            return con_version(http_error(
                415,
                "content_type",
                "El cuerpo debe ser application/json.",
            ));
        }
        if p.cuerpo.len() > 4 << 20 {
            return con_version(http_error(
                413,
                "tamano",
                "Mensaje demasiado grande (máximo 4 MB).",
            ));
        }
        match self.atender(&p.cuerpo) {
            None => con_version(Respuesta::nueva(202)),
            Some(resp) => con_version(Respuesta::binario(
                200,
                "application/json",
                format!("{resp}\n").into_bytes(),
            )),
        }
    }

    fn autorizado(&self, token: &str, p: &Peticion) -> bool {
        let auth = p.cabecera("Authorization");
        if !auth.to_ascii_lowercase().starts_with("bearer ") {
            return false;
        }
        let t = auth[7..].trim();
        if t.is_empty() {
            return false;
        }
        if !token.is_empty()
            && t.len() == token.len()
            && t.bytes()
                .zip(token.bytes())
                .fold(0u8, |a, (x, y)| a | (x ^ y))
                == 0
        {
            return true;
        }
        self.s.token_valido(t)
    }

    /// Sirve el transporte HTTP por red hasta que el proceso muera.
    pub fn servir_http(self: Arc<Self>, direccion: &str, token: String) -> Result<(), String> {
        crate::servidor::http::servir(direccion, move |p| self.atender_http(&token, &p))
    }
}

fn respuesta(id: Value, result: Value) -> Value {
    json!({"jsonrpc": "2.0", "id": id, "result": result})
}

fn error_rpc(id: Value, code: i64, msg: &str) -> Value {
    json!({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": msg}})
}

fn http_error(code: u16, codigo: &str, msg: &str) -> Respuesta {
    Respuesta::binario(
        code,
        "application/json",
        format!("{}\n", json!({"error": {"codigo": codigo, "mensaje": msg}})).into_bytes(),
    )
}

/// Sin Origin (clientes que no son navegador) pasa; con Origin, solo loopback o el mismo host.
fn origen_permitido(p: &Peticion) -> bool {
    let o = p.cabecera("Origin");
    if o.is_empty() {
        return true;
    }
    if o == "null" {
        return false;
    }
    let sin_esquema = o.split("://").nth(1).unwrap_or("");
    let host_puerto = sin_esquema.split('/').next().unwrap_or("");
    if host_puerto.is_empty() {
        return false;
    }
    let h = host_puerto
        .rsplit_once(':')
        .map(|(h, _)| h)
        .unwrap_or(host_puerto)
        .trim_matches(|c| c == '[' || c == ']')
        .to_ascii_lowercase();
    if h == "localhost" || h == "127.0.0.1" || h == "::1" {
        return true;
    }
    let rh = p.host.rsplit_once(':').map(|(h, _)| h).unwrap_or(&p.host);
    h.eq_ignore_ascii_case(rh)
}

fn acepta_json(accept: &str) -> bool {
    if accept.trim().is_empty() {
        return true;
    }
    accept.split(',').any(|p| {
        let mt = p
            .split(';')
            .next()
            .unwrap_or("")
            .trim()
            .to_ascii_lowercase();
        matches!(
            mt.as_str(),
            "application/json" | "text/event-stream" | "*/*" | "application/*"
        )
    })
}

/// `Arrancar`: levanta el motor en proceso y el servidor MCP encima, con la misma regla de
/// carpeta que la app (`cliutil::resolver_raiz`). Sin RVD automático y con clave de la app efímera.
pub fn arrancar(env: &str, db: &str) -> Result<Mcp, String> {
    let (raiz, _) = crate::cliutil::resolver_raiz(env)?;
    let mut cfg = crate::cliutil::config_de_raiz(&raiz);
    if !db.is_empty() {
        cfg.database_path = db.to_string();
    }
    if cfg.app_shell_key.is_empty() {
        cfg.app_shell_key = format!(
            "mcp-{}",
            crate::servidor::auth::hex(&crate::servidor::local::bytes_azar(32))
        );
    }
    cfg.rvd_scheduler_activo = false;
    let ambiente = cfg.sii_ambiente.clone();
    let s = Servidor::nuevo(cfg).map_err(|e| format!("no pude arrancar el motor: {e}"))?;
    let cuenta = std::env::var("MCP_CUENTA_ID")
        .ok()
        .and_then(|v| v.trim().parse().ok())
        .unwrap_or(1);
    Ok(Mcp::nuevo(s, cuenta, &ambiente))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::servidor::config;

    fn motor_de_prueba(dir: &std::path::Path) -> Arc<Servidor> {
        let mut cfg = config::cargar("");
        cfg.database_path = dir.join("mcp.db").to_string_lossy().into_owned();
        cfg.storage_path = dir.join("storage").to_string_lossy().into_owned();
        cfg.jwt_secret = "secreto-de-prueba-largo-para-el-jwt".into();
        cfg.app_shell_key = "clave-de-prueba-muy-larga-123456".into();
        cfg.rvd_scheduler_activo = false;
        cfg.admin_password = "admin-de-prueba".into();
        Servidor::nuevo(cfg).unwrap()
    }

    fn tmp(n: &str) -> std::path::PathBuf {
        let d = std::env::temp_dir().join(format!("mcp-{n}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    /// stdio: initialize, tools/list con las 20 herramientas, una notificación sin respuesta y
    /// un tools/call que llega al motor.
    #[test]
    fn stdio() {
        let d = tmp("stdio");
        let m = Mcp::nuevo(motor_de_prueba(&d), 1, "certificacion");
        let entrada = [
            r#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}"#,
            r#"{"jsonrpc":"2.0","method":"notifications/initialized"}"#,
            r#"{"jsonrpc":"2.0","id":2,"method":"tools/list"}"#,
            r#"{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"salud","arguments":{}}}"#,
            r#"{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"no_existe"}}"#,
        ]
        .join("\n")
            + "\n";
        let mut out = Vec::new();
        m.servir_stdio(std::io::Cursor::new(entrada), &mut out);
        let r: Vec<Value> = String::from_utf8(out)
            .unwrap()
            .lines()
            .map(|l| serde_json::from_str(l).unwrap())
            .collect();
        assert_eq!(r.len(), 4);
        assert_eq!(r[0]["result"]["serverInfo"]["name"], "dte-chile");
        assert_eq!(r[1]["result"]["tools"].as_array().unwrap().len(), 20);
        assert_eq!(m.herramientas().len(), 20);
        assert_eq!(r[2]["result"]["structuredContent"]["motor"], "rust");
        assert_eq!(
            r[2]["result"]["structuredContent"]["ambiente_defecto"],
            "certificacion"
        );
        assert!(r[3].get("error").is_some());
        // structuredContent siempre objeto: una lista va en items y el texto lleva la lista tal cual
        for tool in ["listar_negocios", "listar_certificados"] {
            let r = m.atender(format!(r#"{{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{{"name":"{tool}","arguments":{{}}}}}}"#).as_bytes()).unwrap();
            assert!(r["result"]["isError"].is_null(), "{tool}: {r}");
            assert!(r["result"]["structuredContent"]["items"].is_array());
            assert!(r["result"]["content"][0]["text"]
                .as_str()
                .unwrap()
                .starts_with('['));
        }
        assert_eq!(estructurado(&json!(3.0))["valor"], 3.0);
        let _ = std::fs::remove_dir_all(&d);
    }

    /// La app y un cliente MCP abren la misma base a la vez (dos motores): emitir en uno
    /// mientras el otro consulta no puede romper ni bloquear.
    #[test]
    fn dos_motores_misma_base() {
        let d = tmp("dos");
        let app = motor_de_prueba(&d);
        let otro = motor_de_prueba(&d);
        let m = Arc::new(Mcp::nuevo(otro.clone(), 1, "certificacion"));
        let a2 = app.clone();
        let h1 = std::thread::spawn(move || {
            for _ in 0..15 {
                let (st, b) = a2.llamar(
                    "POST",
                    "/api/v1/simulacion/emitir",
                    Some(
                        r#"{"tipo_dte":39,"items":[{"nombre":"Café","precio_unitario":1190}]}"#
                            .as_bytes(),
                    ),
                );
                assert!(
                    st == 200 || st == 201,
                    "emitir: {st} {}",
                    String::from_utf8_lossy(&b)
                );
            }
        });
        let m2 = m.clone();
        let h2 = std::thread::spawn(move || {
            for _ in 0..15 {
                let r = m2.atender(br#"{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"listar_negocios","arguments":{}}}"#).unwrap();
                assert!(r["result"]["isError"].is_null(), "tool: {r}");
            }
        });
        h1.join().unwrap();
        h2.join().unwrap();
        let (st, b) = otro.llamar("GET", "/api/v1/simulacion/estado", None);
        assert!(
            st == 200 && String::from_utf8_lossy(&b).contains("\"documentos\":15"),
            "{st} {}",
            String::from_utf8_lossy(&b)
        );
        let _ = std::fs::remove_dir_all(&d);
    }

    /// El transporte HTTP cumple la forma sin estado de Streamable HTTP.
    #[test]
    fn http_streamable() {
        let d = tmp("http");
        let m = Mcp::nuevo(motor_de_prueba(&d), 1, "certificacion");
        let pide = |metodo: &str, cuerpo: &str, cab: &[(&str, &str)]| -> Respuesta {
            let mut p = Peticion::nueva(metodo, "/mcp", cuerpo.as_bytes().to_vec());
            p.host = "127.0.0.1:8090".into();
            let mut cabs = vec![
                ("Authorization", "Bearer token-de-prueba"),
                ("Content-Type", "application/json"),
                ("Accept", "application/json, text/event-stream"),
            ];
            for (k, v) in cab {
                cabs.retain(|(n, _)| !n.eq_ignore_ascii_case(k));
                cabs.push((k, v));
            }
            p.cabeceras = cabs
                .iter()
                .map(|(k, v)| (k.to_string(), v.to_string()))
                .collect();
            m.atender_http("token-de-prueba", &p)
        };
        let init = r#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}"#;
        let r = pide("POST", init, &[]);
        assert!(
            r.status == 200
                && r.cabecera("Content-Type") == "application/json"
                && r.cabecera("MCP-Protocol-Version") == PROTOCOL_VERSION,
            "{r:?}"
        );
        let v: Value = serde_json::from_slice(&r.cuerpo).unwrap();
        assert_eq!(v["result"]["protocolVersion"], PROTOCOL_VERSION);
        let r = pide(
            "POST",
            r#"{"jsonrpc":"2.0","method":"notifications/initialized"}"#,
            &[],
        );
        assert!(r.status == 202 && r.cuerpo.is_empty());
        let r = pide(
            "POST",
            r#"{"jsonrpc":"2.0","id":2,"method":"tools/list"}"#,
            &[("MCP-Protocol-Version", "2025-06-18")],
        );
        assert!(r.status == 200 && String::from_utf8_lossy(&r.cuerpo).contains("\"tools\""));
        assert_eq!(
            pide("POST", init, &[("MCP-Protocol-Version", "1999-01-01")]).status,
            400
        );
        let r = pide("GET", "", &[]);
        assert!(r.status == 405 && r.cabecera("Allow") == "POST");
        assert_eq!(pide("DELETE", "", &[]).status, 405);
        assert_eq!(
            pide("POST", init, &[("Origin", "http://localhost:3000")]).status,
            200
        );
        assert_eq!(
            pide("POST", init, &[("Origin", "http://127.0.0.1:8090")]).status,
            200
        );
        assert_eq!(
            pide("POST", init, &[("Origin", "http://malo.example")]).status,
            403
        );
        assert_eq!(pide("POST", init, &[("Accept", "text/html")]).status, 406);
        let r = pide("POST", init, &[("Authorization", "Bearer otro")]);
        assert!(r.status == 401 && !r.cabecera("WWW-Authenticate").is_empty());
        let _ = std::fs::remove_dir_all(&d);
    }

    /// Arrancar sin --env ni ./.env usa la carpeta de datos (DTE_DATOS) y le crea su .env.
    #[test]
    fn arrancar_carpeta_datos() {
        let datos = tmp("datos");
        std::env::set_var("DTE_DATOS", &datos);
        let wd = std::env::current_dir().unwrap();
        let otro = tmp("cwd");
        std::env::set_current_dir(&otro).unwrap();
        let r = arrancar("", "");
        std::env::set_current_dir(wd).unwrap();
        std::env::remove_var("DTE_DATOS");
        let m = r.unwrap();
        assert!(
            datos.join(".env").exists(),
            "no creó el .env de la carpeta de datos"
        );
        assert!(
            datos.join("dte_database.db").exists(),
            "la base no quedó en la carpeta de datos"
        );
        assert_eq!(m.herramientas().len(), 20);
        let _ = std::fs::remove_dir_all(&datos);
        let _ = std::fs::remove_dir_all(&otro);
    }
}
