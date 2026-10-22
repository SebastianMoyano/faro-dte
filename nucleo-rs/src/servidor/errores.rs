//! Envoltorio de error y request-id, idénticos a `api/errors.py` y `nucleo/servidor/errores.go`:
//! `{"error": {"codigo", "mensaje", "detalle"?, "request_id"}}` + cabecera `X-Request-ID`.

use super::http::Respuesta;
use serde_json::{json, Value};

pub const INTERNO: &str = "Ocurrió un error interno. Reporta el request_id al soporte.";
pub const DATOS_INVALIDOS: &str = "Los datos enviados no son válidos.";

/// Un error con status, código y mensaje; se vuelve el envoltorio al responder.
#[derive(Debug, Clone)]
pub struct ErrorHTTP {
    pub status: u16,
    pub codigo: String,
    pub mensaje: String,
    pub detalle: Option<Value>,
}

pub type Res = Result<Respuesta, ErrorHTTP>;

impl ErrorHTTP {
    pub fn nuevo(status: u16, codigo: &str, mensaje: impl Into<String>) -> ErrorHTTP {
        ErrorHTTP {
            status,
            codigo: codigo.into(),
            mensaje: mensaje.into(),
            detalle: None,
        }
    }
    pub fn detalle(mut self, d: Value) -> ErrorHTTP {
        self.detalle = Some(d);
        self
    }
    pub fn interno() -> ErrorHTTP {
        ErrorHTTP::nuevo(500, "error_interno", INTERNO)
    }
    pub fn validacion(m: impl Into<String>) -> ErrorHTTP {
        ErrorHTTP::nuevo(422, "validacion", m)
    }
    pub fn datos_invalidos() -> ErrorHTTP {
        ErrorHTTP::validacion(DATOS_INVALIDOS)
    }
    pub fn no_encontrado(m: impl Into<String>) -> ErrorHTTP {
        ErrorHTTP::nuevo(404, "no_encontrado", m)
    }
    pub fn conflicto(m: impl Into<String>) -> ErrorHTTP {
        ErrorHTTP::nuevo(409, "conflicto", m)
    }
    pub fn folio(m: impl Into<String>) -> ErrorHTTP {
        ErrorHTTP::nuevo(409, "folio", m)
    }
    pub fn caf(m: impl Into<String>) -> ErrorHTTP {
        ErrorHTTP::nuevo(422, "caf", m)
    }
    pub fn certificado(m: impl Into<String>) -> ErrorHTTP {
        ErrorHTTP::nuevo(422, "certificado", m)
    }
    /// 423: la bóveda con PIN está cerrada (`boveda.rs`).
    pub fn boveda_bloqueada() -> ErrorHTTP {
        ErrorHTTP::nuevo(423, "boveda_bloqueada", super::boveda::MENSAJE_BLOQUEADA)
    }
    pub fn autenticacion(m: impl Into<String>) -> ErrorHTTP {
        ErrorHTTP::nuevo(401, "autenticacion", m)
    }
    pub fn sii_comunicacion(m: impl Into<String>) -> ErrorHTTP {
        ErrorHTTP::nuevo(502, "sii_comunicacion", m)
    }
    pub fn sii_rechazo(m: impl Into<String>) -> ErrorHTTP {
        ErrorHTTP::nuevo(422, "sii_rechazo", m)
    }

    /// El 422 de pydantic con un error por campo: `{"errores": [{"loc": [where, campo], ...}]}`.
    pub fn pydantic(donde: &str, campo: &str, msg: &str, tipo: &str) -> ErrorHTTP {
        let loc = if campo.is_empty() {
            json!([donde])
        } else {
            json!([donde, campo])
        };
        ErrorHTTP::datos_invalidos()
            .detalle(json!({"errores": [{"loc": loc, "msg": msg, "type": tipo}]}))
    }
    /// `faltan(...)`: "Field required" por cada campo del cuerpo.
    pub fn faltan(campos: &[&str]) -> ErrorHTTP {
        let errs: Vec<Value> = campos
            .iter()
            .map(|c| json!({"loc": ["body", c], "msg": "Field required", "type": "missing"}))
            .collect();
        ErrorHTTP::datos_invalidos().detalle(json!({"errores": errs}))
    }
    pub fn json_invalido() -> ErrorHTTP {
        ErrorHTTP::pydantic("body", "", "JSON inválido", "json_invalid")
    }
    pub fn query(campo: &str, msg: &str, tipo: &str) -> ErrorHTTP {
        ErrorHTTP::pydantic("query", campo, msg, tipo)
    }
    pub fn path(campo: &str) -> ErrorHTTP {
        ErrorHTTP::pydantic(
            "path",
            campo,
            "Input should be a valid integer, unable to parse string as an integer",
            "int_parsing",
        )
    }

    /// La respuesta con el envoltorio (`responderError`).
    pub fn respuesta(&self, request_id: &str) -> Respuesta {
        let mut e = serde_json::Map::new();
        e.insert("codigo".into(), Value::String(self.codigo.clone()));
        // La sala de espera del SII llega envuelta en prefijos ("No se pudo consultar el SII: …"):
        // se responde solo su mensaje, igual que `salaespera.Limpiar` en Go y `api/errors.py`.
        let mensaje = if self.mensaje.contains(crate::sii::http::MENSAJE_SALA_ESPERA) {
            crate::sii::http::MENSAJE_SALA_ESPERA.to_string()
        } else {
            self.mensaje.clone()
        };
        e.insert("mensaje".into(), Value::String(mensaje));
        if let Some(d) = &self.detalle {
            e.insert("detalle".into(), d.clone());
        }
        if !request_id.is_empty() {
            e.insert("request_id".into(), Value::String(request_id.to_string()));
        }
        let mut r = json(self.status, &json!({ "error": e }));
        if self.status == 401 {
            r.poner("WWW-Authenticate", "Bearer");
        }
        r
    }
}

impl From<rusqlite::Error> for ErrorHTTP {
    fn from(_: rusqlite::Error) -> Self {
        ErrorHTTP::interno()
    }
}

impl From<crate::almacen::Error> for ErrorHTTP {
    fn from(_: crate::almacen::Error) -> Self {
        ErrorHTTP::interno()
    }
}

impl From<std::io::Error> for ErrorHTTP {
    fn from(_: std::io::Error) -> Self {
        ErrorHTTP::interno()
    }
}

/// `responderJSON`: el cuerpo JSON (sin escapar HTML, con salto final como el codificador de Go).
pub fn json(status: u16, v: &Value) -> Respuesta {
    let mut b = serde_json::to_vec(v).unwrap_or_default();
    b.push(b'\n');
    Respuesta::binario(status, "application/json", b)
}

pub fn ok(v: Value) -> Res {
    Ok(json(200, &v))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn la_sala_de_espera_se_responde_sin_prefijos() {
        let m = crate::sii::http::MENSAJE_SALA_ESPERA;
        let r = ErrorHTTP::sii_comunicacion(format!("No se pudo consultar el SII: {m}")).respuesta("");
        let v: Value = serde_json::from_slice(&r.cuerpo).unwrap();
        assert_eq!(v["error"]["mensaje"], m);
        assert_eq!(v["error"]["codigo"], "sii_comunicacion");
    }
}
