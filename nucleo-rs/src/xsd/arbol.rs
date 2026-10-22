//! El árbol del documento (o del XSD): nombres resueltos a (namespace, local), atributos sin
//! los xmlns, texto concatenado, hijos y línea de inicio. Transcripción de `nucleo/xsd/arbol.go`.

use crate::c14n::{resolver_referencia, valor_atributo};
use quick_xml::events::Event;
use quick_xml::Reader;
use std::collections::BTreeMap;
use std::fmt;
use std::sync::OnceLock;

pub const NS_XSD: &str = "http://www.w3.org/2001/XMLSchema";
pub const NS_XSI: &str = "http://www.w3.org/2001/XMLSchema-instance";
pub const NS_XML: &str = "http://www.w3.org/XML/1998/namespace";

/// Un nombre con namespace (URI, no prefijo).
#[derive(Debug, Clone, Default, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct QName {
    pub ns: String,
    pub local: String,
}

impl QName {
    pub fn new(ns: &str, local: &str) -> QName {
        QName {
            ns: ns.to_string(),
            local: local.to_string(),
        }
    }
}

impl fmt::Display for QName {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        if self.ns.is_empty() {
            write!(f, "{}", self.local)
        } else {
            write!(f, "{{{}}}{}", self.ns, self.local)
        }
    }
}

#[derive(Debug, Clone)]
pub struct Atributo {
    pub nombre: QName,
    pub valor: String,
}

#[derive(Debug, Clone, Default)]
pub struct Nodo {
    pub nombre: QName,
    pub attrs: Vec<Atributo>,
    pub texto: String,
    pub hijos: Vec<Nodo>,
    pub linea: usize,
    /// prefijos en alcance en ESTE nodo (heredados + propios): "" = default
    ns: BTreeMap<String, String>,
}

impl Nodo {
    /// Resuelve un prefijo declarado en el nodo o en un ancestro ("" = default).
    pub fn prefijo(&self, p: &str) -> Option<&str> {
        if let Some(u) = self.ns.get(p) {
            return Some(u);
        }
        if p == "xml" {
            return Some(NS_XML);
        }
        None
    }

    /// Resuelve un valor QName ("SiiDte:RUTType") con los prefijos en alcance del nodo.
    pub fn qname(&self, v: &str) -> Result<QName, String> {
        match v.split_once(':') {
            None => Ok(QName {
                ns: self.prefijo("").unwrap_or("").to_string(),
                local: v.to_string(),
            }),
            Some((pref, local)) => match self.prefijo(pref) {
                Some(u) => Ok(QName {
                    ns: u.to_string(),
                    local: local.to_string(),
                }),
                None => Err(format!("prefijo no declarado en {v:?}")),
            },
        }
    }

    pub fn attr(&self, local: &str) -> Option<&str> {
        self.attrs
            .iter()
            .find(|a| a.nombre.ns.is_empty() && a.nombre.local == local)
            .map(|a| a.valor.as_str())
    }
}

pub fn es_blanco(s: &str) -> bool {
    s.trim().is_empty()
}

static RE_DECL: OnceLock<regex::bytes::Regex> = OnceLock::new();

/// Decodifica los bytes a texto como lo haría el decoder de Go con `CharsetReader`: BOM UTF-8,
/// UTF-16 con BOM, y la codificación de la declaración (ISO-8859-1 es la del SII; windows-1252
/// y el resto de la familia latina se leen como ISO-8859-1: un byte = un carácter, que es lo que
/// importa para largos y patrones). Sin declaración, UTF-8.
fn decodificar(raw: &[u8]) -> Result<String, String> {
    if let Some(r) = raw.strip_prefix(&[0xEF, 0xBB, 0xBF]) {
        return String::from_utf8(r.to_vec()).map_err(|_| "UTF-8 inválido".to_string());
    }
    let (be, le) = (
        raw.starts_with(&[0xFE, 0xFF]),
        raw.starts_with(&[0xFF, 0xFE]),
    );
    if be || le {
        let b = &raw[2..];
        let u: Vec<u16> = b
            .chunks(2)
            .filter(|c| c.len() == 2)
            .map(|c| {
                if be {
                    u16::from_be_bytes([c[0], c[1]])
                } else {
                    u16::from_le_bytes([c[0], c[1]])
                }
            })
            .collect();
        return String::from_utf16(&u).map_err(|_| "UTF-16 inválido".to_string());
    }
    let re = RE_DECL.get_or_init(|| {
        regex::bytes::Regex::new(r#"(?is)^\s*<\?xml[^>]*?encoding\s*=\s*["']([^"']+)["']"#)
            .expect("regex")
    });
    let charset = re
        .captures(raw)
        .map(|c| String::from_utf8_lossy(&c[1]).to_lowercase());
    match charset.as_deref() {
        None | Some("utf-8") | Some("utf8") | Some("us-ascii") | Some("ascii") | Some("utf-16")
        | Some("utf-16le") | Some("utf-16be") => {
            String::from_utf8(raw.to_vec()).map_err(|e| format!("UTF-8 inválido: {e}"))
        }
        Some("iso-8859-1") | Some("iso8859-1") | Some("latin1") | Some("latin-1") | Some("l1")
        | Some("iso-8859-15") | Some("windows-1252") | Some("cp1252") | Some("iso-8859-2") => {
            Ok(raw.iter().map(|&b| b as char).collect())
        }
        Some(otro) => Err(format!("codificación no soportada: {otro}")),
    }
}

/// Construye el árbol del documento. Cualquier error es de sintaxis (mal formado, codificación
/// inválida, entidades no definidas, contenido fuera de la raíz). No se resuelven entidades
/// externas ni DTD: no hay XXE ni "billion laughs".
pub fn parsear_arbol(raw: &[u8]) -> Result<Nodo, String> {
    let texto = decodificar(raw)?;
    if texto.trim().is_empty() {
        return Err("el documento está vacío".into());
    }
    if let Some(c) = texto.chars().find(|&c| !caracter_xml(c)) {
        return Err(format!("carácter no permitido en XML: U+{:04X}", c as u32));
    }
    // offsets de inicio de línea, para numerar los elementos como el decoder de Go
    let mut lineas: Vec<usize> = vec![0];
    for (i, b) in texto.bytes().enumerate() {
        if b == b'\n' {
            lineas.push(i + 1);
        }
    }
    let linea_de = |pos: usize| -> usize { lineas.partition_point(|&l| l <= pos) };

    let mut lector = Reader::from_str(&texto);
    lector.config_mut().expand_empty_elements = true;
    lector.config_mut().check_end_names = true;
    lector.config_mut().allow_unmatched_ends = false;
    // pila de nodos abiertos; el último es el actual
    let mut pila: Vec<Nodo> = Vec::new();
    let mut raiz: Option<Nodo> = None;
    loop {
        let ev = lector.read_event().map_err(|e| e.to_string())?;
        match ev {
            Event::Start(e) => {
                if raiz.is_some() && pila.is_empty() {
                    return Err("contenido extra al final del documento".into());
                }
                let linea = linea_de(lector.buffer_position() as usize);
                let mut ns: BTreeMap<String, String> =
                    pila.last().map(|p| p.ns.clone()).unwrap_or_default();
                let mut crudos: Vec<(String, String)> = Vec::new();
                for a in e.attributes() {
                    let a = a.map_err(|e| e.to_string())?;
                    let clave = String::from_utf8_lossy(a.key.as_ref()).into_owned();
                    let valor = valor_atributo(&a)?;
                    if clave == "xmlns" {
                        ns.insert(String::new(), valor);
                    } else if let Some(p) = clave.strip_prefix("xmlns:") {
                        ns.insert(p.to_string(), valor);
                    } else {
                        crudos.push((clave, valor));
                    }
                }
                let nombre_crudo = String::from_utf8_lossy(e.name().as_ref()).into_owned();
                let mut n = Nodo {
                    linea,
                    ns,
                    ..Default::default()
                };
                n.nombre = resolver_nombre(&n, &nombre_crudo, true)?;
                for (k, v) in crudos {
                    let q = resolver_nombre(&n, &k, false)?;
                    n.attrs.push(Atributo {
                        nombre: q,
                        valor: v,
                    });
                }
                pila.push(n);
            }
            Event::End(_) => {
                let n = pila.pop().ok_or("cierre sin apertura")?;
                match pila.last_mut() {
                    Some(p) => p.hijos.push(n),
                    None => raiz = Some(n),
                }
            }
            Event::Text(t) => {
                let s = t.decode().map_err(|e| e.to_string())?;
                match pila.last_mut() {
                    Some(p) => p.texto.push_str(&s),
                    None => {
                        if !es_blanco(&s) {
                            return Err("contenido extra al final del documento".into());
                        }
                    }
                }
            }
            Event::GeneralRef(r) => {
                let s = resolver_referencia(&r)?;
                match pila.last_mut() {
                    Some(p) => p.texto.push_str(&s),
                    None => return Err("contenido extra al final del documento".into()),
                }
            }
            Event::CData(c) => {
                let s = String::from_utf8_lossy(c.as_ref()).into_owned();
                match pila.last_mut() {
                    Some(p) => p.texto.push_str(&s),
                    None => return Err("contenido extra al final del documento".into()),
                }
            }
            // Un DOCTYPE se tolera (lxml también), pero no se resuelve: las entidades que declare
            // quedan sin definir y `&x;` es error de sintaxis (ni XXE ni "billion laughs").
            Event::DocType(_) => {
                if raiz.is_some() || !pila.is_empty() {
                    return Err("DOCTYPE fuera de lugar".into());
                }
            }
            Event::Decl(_) | Event::PI(_) | Event::Comment(_) | Event::Empty(_) => {}
            Event::Eof => break,
        }
    }
    if !pila.is_empty() {
        return Err("el documento termina con elementos sin cerrar".into());
    }
    raiz.ok_or_else(|| "no hay elemento raíz".to_string())
}

/// Caracteres permitidos por XML 1.0 (lo que Go rechaza con "illegal character code").
fn caracter_xml(c: char) -> bool {
    matches!(c, '\u{9}' | '\u{A}' | '\u{D}' | '\u{20}'..='\u{D7FF}' | '\u{E000}'..='\u{FFFD}' | '\u{10000}'..='\u{10FFFF}')
}

/// Nombre de elemento (`es_elemento`: el default aplica) o de atributo (sin prefijo = sin
/// namespace). Un prefijo no declarado es error de sintaxis, como en el decoder de Go.
fn resolver_nombre(n: &Nodo, crudo: &str, es_elemento: bool) -> Result<QName, String> {
    match crudo.split_once(':') {
        None => Ok(QName {
            ns: if es_elemento {
                n.prefijo("").unwrap_or("").to_string()
            } else {
                String::new()
            },
            local: crudo.to_string(),
        }),
        Some((pref, local)) => match n.prefijo(pref) {
            Some(u) => Ok(QName {
                ns: u.to_string(),
                local: local.to_string(),
            }),
            None => Err(format!("prefijo no declarado: {pref}")),
        },
    }
}
