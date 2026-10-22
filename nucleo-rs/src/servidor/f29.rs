//! GET /api/v1/keystore/negocios/{id}/f29?periodo=YYYYMM&ppm=&remanente_anterior=: baja el RCV
//! del período desde PRODUCCIÓN con la sesión del certificado del negocio, agrega y mapea a los
//! casilleros del F29. Solo lectura: NO declara nada al SII. Aquí también `movimientos` (mismo RCV).

use super::db::entero_query;
use super::errores::{ok, ErrorHTTP, Res};
use super::http::Peticion;
use super::{texto, Servidor};
use crate::portal;
use crate::rcv;
use serde_json::{json, Value};

pub fn f29_negocio(s: &Servidor, p: &mut Peticion) -> Res {
    let periodo = p.q("periodo");
    if periodo.is_empty() {
        return Err(ErrorHTTP::query("periodo", "Field required", "missing"));
    }
    let ppm = entero_query(p, "ppm", 0, i64::MIN / 2, 0)?;
    let rem = entero_query(p, "remanente_anterior", 0, i64::MIN / 2, 0)?;
    let (neg, cuenta) = s.negocio_o_404(p)?;
    let rut = texto(&neg["rut"]);
    let e502 =
        |e: String| ErrorHTTP::sii_comunicacion(format!("No se pudo bajar el RCV del SII: {e}"));
    let cli_portal = s
        .portal_del_negocio(&neg, cuenta, portal::BASE_PRODUCCION)
        .map_err(e502)?;
    let token = cli_portal.cookies.get("TOKEN").cloned().unwrap_or_default();
    let cli = rcv::Cliente::nuevo(&token, Default::default());
    let sync = cli
        .sincronizar_periodo(&s.alm, &rut, &periodo)
        .map_err(e502)?;
    let d = rcv::calcular_desglose(&s.alm, &rut, &periodo, ppm, rem)
        .map_err(|_| ErrorHTTP::interno())?;
    let cas: Vec<Value> = rcv::mapear_a_f29(&d)
        .iter()
        .map(|l| json!({"codigo": l.codigo, "glosa": l.glosa, "valor": l.valor}))
        .collect();
    let (compras, ventas) = (
        sync.get("compras").copied().unwrap_or(0),
        sync.get("ventas").copied().unwrap_or(0),
    );
    ok(json!({
        "rut": rut, "razon_social": neg["razon_social"], "periodo": periodo, "documentos": sync,
        "sin_datos": compras + ventas == 0, "ppm": ppm, "remanente_anterior": rem,
        "total_debitos": d.total_debitos, "total_creditos": d.total_creditos, "iva_determinado": d.iva_determinado(),
        "remanente_siguiente": d.remanente_siguiente(), "total_a_pagar": d.total_a_pagar(), "casilleros": cas,
    }))
}

/// GET /api/v1/keystore/negocios/{id}/movimientos?periodos=YYYYMM,…&limite=: los últimos
/// movimientos del negocio tal como los tiene el SII (RCV de PRODUCCIÓN, compras y ventas),
/// bajados en el momento con la sesión del certificado del negocio. Solo lectura.
pub fn movimientos_negocio(s: &Servidor, p: &mut Peticion) -> Res {
    let limite = entero_query(p, "limite", 50, 1, 500)?;
    let periodos = rcv::parsear_periodos(&p.q("periodos"))
        .ok_or_else(|| ErrorHTTP::validacion(rcv::mensaje_periodos()))?;
    let (neg, cuenta) = s.negocio_o_404(p)?;
    let rut = texto(&neg["rut"]);
    let e502 =
        |e: String| ErrorHTTP::sii_comunicacion(format!("No se pudo bajar el RCV del SII: {e}"));
    let cli_portal = s
        .portal_del_negocio(&neg, cuenta, portal::BASE_PRODUCCION)
        .map_err(e502)?;
    let token = cli_portal.cookies.get("TOKEN").cloned().unwrap_or_default();
    let mut res = rcv::Cliente::nuevo(&token, Default::default())
        .ultimos_movimientos(&s.alm, &rut, &periodos, limite as usize)
        .map_err(e502)?;
    res["rut"] = json!(rut);
    res["razon_social"] = neg["razon_social"].clone();
    ok(res)
}
