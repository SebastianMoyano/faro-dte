//! Rutas del portal del SII con certificado (api/routes/keystore.py y onboarding.py):
//! negocios (alta con auto-relleno, estado de habilitación, panel de timbraje), empresas del
//! certificado y el diagnóstico del onboarding. Todo solo lectura en el SII.

use super::errores::{json, ok, ErrorHTTP, Res};
use super::http::{parsear_formulario, Peticion};
use super::keystore::{cert_id_de, negocios_de, Negocio};
use super::{ahora_utc, entero, texto, Prot, Servidor};
use crate::portal::{self, onboarding, Cliente};
use rusqlite::types::Value as Sql;
use serde_json::{json as j, Value};

pub fn rutas(s: &mut Servidor) {
    s.ruta(
        "GET /api/v1/keystore/empresa",
        Prot::Protegida,
        previsualizar_empresa,
    );
    s.ruta(
        "GET /api/v1/keystore/certificados/{id}/empresas",
        Prot::Protegida,
        empresas_del_certificado,
    );
    s.ruta(
        "POST /api/v1/keystore/negocios",
        Prot::Protegida,
        agregar_negocio,
    );
    s.ruta(
        "GET /api/v1/keystore/negocios/{id}/estado",
        Prot::Protegida,
        estado_negocio,
    );
    s.ruta(
        "GET /api/v1/keystore/negocios/{id}/timbraje",
        Prot::Protegida,
        timbraje_negocio,
    );
    s.ruta(
        "GET /api/v1/keystore/negocios/{id}/f29",
        Prot::Protegida,
        super::f29::f29_negocio,
    );
    s.ruta(
        "GET /api/v1/keystore/negocios/{id}/movimientos",
        Prot::Protegida,
        super::f29::movimientos_negocio,
    );
    s.ruta(
        "GET /api/v1/onboarding/diagnostico",
        Prot::Protegida,
        onboarding_diagnostico,
    );
    s.ruta(
        "GET /api/v1/onboarding/cartera",
        Prot::Protegida,
        onboarding_cartera,
    );
}

/// `negocios.info_empresa`: auto-relleno por consulta pública (None si no está autorizada).
fn info_empresa(rut: &str, ambiente: &str) -> Result<Option<Value>, String> {
    let mut p = Cliente::nuevo(&[], &[], portal::base_de(ambiente))?;
    let Some(emp) = p.consultar_empresa_autorizada(rut, "")? else {
        return Ok(None);
    };
    let tipos: Vec<Value> = emp
        .documentos
        .iter()
        .map(|d| j!({"codigo": d.codigo, "descripcion": d.descripcion}))
        .collect();
    Ok(Some(
        j!({"rut": emp.rut, "razon_social": emp.razon_social, "resolucion": {"numero": emp.nro_resolucion, "fecha": emp.fecha_resolucion},
        "autorizado": !emp.documentos.is_empty(), "tipos": tipos}),
    ))
}

fn previsualizar_empresa(_s: &Servidor, p: &mut Peticion) -> Res {
    let rut = p.q("rut");
    if rut.is_empty() {
        return Err(ErrorHTTP::query("rut", "Field required", "missing"));
    }
    let mut ambiente = p.q("ambiente");
    if ambiente.is_empty() {
        ambiente = "certificacion".into();
    }
    // Python no captura el error de red aquí → 500
    let info = info_empresa(&rut, &ambiente).map_err(|_| ErrorHTTP::interno())?;
    match info {
        Some(i) => ok(i),
        None => Err(ErrorHTTP::no_encontrado(format!(
            "El RUT {rut} no aparece autorizado a emitir DTE."
        ))),
    }
}

/// Python envuelve TODO lo que no sea SIIError/ValueError en 502 (incluido "no encontrado").
fn empresas_del_certificado(s: &Servidor, p: &mut Peticion) -> Res {
    let id: i64 = p
        .param("id")
        .parse()
        .map_err(|_| ErrorHTTP::path("cert_id"))?;
    let cuenta = s.cuenta_id(p);
    let (cert_pem, key_pem) = s
        .pem_del_keystore(id, cuenta)
        .map_err(|e| ErrorHTTP::sii_comunicacion(format!("No se pudo consultar el SII: {e}")))?;
    let mut cli = Cliente::nuevo(
        cert_pem.as_bytes(),
        key_pem.as_bytes(),
        portal::BASE_CERTIFICACION,
    )
    .map_err(ErrorHTTP::no_encontrado)?;
    cli.autenticar("")
        .map_err(|e| ErrorHTTP::sii_comunicacion(format!("No se pudo consultar el SII: {e}")))?;
    let emps = cli
        .empresas_asociadas()
        .map_err(|e| ErrorHTTP::sii_comunicacion(format!("No se pudo consultar el SII: {e}")))?;
    ok(serde_json::to_value(emps).unwrap_or(Value::Null))
}

/// `negocios.agregar_negocio` (multipart/form: rut, cert_id, ambiente) → 201.
fn agregar_negocio(s: &Servidor, p: &mut Peticion) -> Res {
    let f = parsear_formulario(p).unwrap_or_default();
    let rut = f.valor("rut");
    if rut.is_empty() {
        return Err(ErrorHTTP::pydantic(
            "body",
            "rut",
            "Field required",
            "missing",
        ));
    }
    let mut cert_id: Option<i64> = None;
    let cid = f.valor("cert_id");
    if !cid.is_empty() {
        cert_id = Some(cid.parse().map_err(|_| {
            ErrorHTTP::pydantic(
                "body",
                "cert_id",
                "Input should be a valid integer, unable to parse string as an integer",
                "int_parsing",
            )
        })?);
    }
    let mut ambiente = f.valor("ambiente");
    if ambiente.is_empty() {
        ambiente = "certificacion".into();
    }
    let cuenta = s.cuenta_id(p);
    let info = info_empresa(&rut, &ambiente).map_err(|_| ErrorHTTP::interno())?;
    let Some(info) = info else {
        return Err(ErrorHTTP::validacion(format!(
            "El RUT {rut} no aparece autorizado a emitir DTE en el SII."
        )));
    };
    let (rut_n, razon) = (texto(&info["rut"]), texto(&info["razon_social"]));
    let cert_sql: Sql = cert_id.map(Into::into).unwrap_or(Sql::Null);
    let id = match s.escalar::<i64>("SELECT id FROM negocios WHERE cuenta_id=? AND rut=?", &sql![cuenta, rut_n.clone()])? {
        Some(id) => {
            s.ejecutar("UPDATE negocios SET razon_social=?, cert_id=?, ambiente=? WHERE id=?", &sql![razon.clone(), cert_sql, ambiente.clone(), id])?;
            id
        }
        None => s.ejecutar_id(
            "INSERT INTO negocios (cuenta_id, rut, razon_social, cert_id, ambiente, creado) VALUES (?,?,?,?,?,?)",
            &sql![cuenta, rut_n.clone(), razon.clone(), cert_sql, ambiente.clone(), ahora_utc()],
        )?,
    };
    Ok(json(
        201,
        &j!({"id": id, "rut": rut_n, "razon_social": razon, "cert_id": cert_id, "ambiente": ambiente}),
    ))
}

impl Servidor {
    /// `_negocio_o_404`: el negocio de la cuenta, con certificado asociado.
    pub fn negocio_o_404(&self, p: &Peticion) -> Result<(Negocio, i64), ErrorHTTP> {
        let id: i64 = p
            .param("id")
            .parse()
            .map_err(|_| ErrorHTTP::path("negocio_id"))?;
        let cuenta = self.cuenta_id(p);
        for n in negocios_de(self, cuenta)? {
            if entero(&n["id"]) == id {
                if cert_id_de(&n).is_none() {
                    return Err(ErrorHTTP::validacion(
                        "El negocio no tiene un certificado asociado",
                    ));
                }
                return Ok((n, cuenta));
            }
        }
        Err(ErrorHTTP::no_encontrado("Negocio no encontrado"))
    }

    pub fn software_registrado(&self, rut: &str) -> String {
        self.escalar::<String>("SELECT software_registrado FROM negocios WHERE rut=? AND software_registrado IS NOT NULL LIMIT 1", &sql![rut])
            .ok()
            .flatten()
            .unwrap_or_default()
    }

    fn guardar_estado_negocio(
        &self,
        cuenta: i64,
        id: i64,
        estado: &str,
        etiqueta: &str,
        usa_propio: bool,
    ) {
        let _ = self.ejecutar(
            "UPDATE negocios SET estado=?, estado_etiqueta=?, usa_sw_propio=?, estado_ts=? WHERE id=? AND cuenta_id=?",
            &sql![estado, etiqueta, usa_propio as i64, ahora_utc(), id, cuenta],
        );
    }

    /// `portalDelNegocio`: abre y autentica el portal con el certificado del negocio.
    pub fn portal_del_negocio(
        &self,
        neg: &Negocio,
        cuenta: i64,
        base: &str,
    ) -> Result<Cliente, String> {
        let (cert_pem, key_pem) = self.pem_del_keystore(cert_id_de(neg).unwrap_or(0), cuenta)?;
        let mut p = Cliente::nuevo(cert_pem.as_bytes(), key_pem.as_bytes(), base)?;
        p.autenticar("")?;
        Ok(p)
    }
}

fn sii502(e: String) -> ErrorHTTP {
    ErrorHTTP::sii_comunicacion(format!("No se pudo consultar el SII: {e}"))
}

/// `estado_negocio`: autorización en PRODUCCIÓN, software con que emite hoy, certificación del
/// software propio y los próximos pasos.
fn estado_negocio(s: &Servidor, p: &mut Peticion) -> Res {
    let (neg, cuenta) = s.negocio_o_404(p)?;
    let rut = texto(&neg["rut"]);
    let mut cli = s
        .portal_del_negocio(&neg, cuenta, portal::BASE_CERTIFICACION)
        .map_err(sii502)?;
    let emp = cli
        .consultar_empresa_autorizada(&rut, portal::BASE_PRODUCCION)
        .map_err(sii502)?;
    let sw_prod = cli
        .datos_software_de(&rut, portal::BASE_PRODUCCION)
        .map_err(sii502)?;
    let sw_cert = cli
        .datos_software_de(&rut, portal::BASE_CERTIFICACION)
        .map_err(sii502)?;
    let sit = cli.situacion_folios(&rut, &[33, 61]).map_err(sii502)?;
    let autorizados: Vec<Value> = emp
        .as_ref()
        .map(|e| e.documentos.iter().filter(|d| d.desautorizado_desde.is_empty()).map(|d| j!({"tipo": d.codigo, "nombre": d.descripcion, "desde": d.autorizado_desde})).collect())
        .unwrap_or_default();
    let (estado, etiqueta) = onboarding::clasificar_software(
        &sw_prod,
        &sw_cert,
        &s.software_registrado(&rut),
        &s.cfg.nombre_software,
    );
    let sw_a = if sw_prod.software.is_empty() {
        "el sistema del SII".to_string()
    } else {
        sw_prod.software.clone()
    };
    let sw_b = if sw_cert.software.is_empty() {
        "tu software propio".to_string()
    } else {
        sw_cert.software.clone()
    };
    let paso = |titulo: &str, detalle: String| j!({"titulo": titulo, "detalle": detalle});
    let mut pasos: Vec<Value> = Vec::new();
    match estado.as_str() {
        "emitiendo" => pasos.push(j!({"titulo": "Todo listo", "detalle": format!("Ya emites en producción con {sw_b}. No hay pasos pendientes."), "hecho": true})),
        "externo" => {
            pasos.push(j!({"titulo": "Emite con otro proveedor", "urgente": true,
                "detalle": format!("El SII tiene registrado «{sw_a}» como software de esta empresa. No emitas facturas con este motor mientras siga así: los folios del CAF los administra ese proveedor y se chocarían. La autorización es de la empresa, así que cambiarse NO exige volver a certificar.")}));
            pasos.push(paso("1. Sacar el historial del proveedor", "PDF y XML de todo lo emitido y recibido, antes de cortar el contrato: después ese acceso desaparece y no se puede importar desde aquí.".into()));
            pasos.push(paso("2. Cerrar los folios que le quedan", "Pedirle el último folio usado por tipo y los CAF sin usar (se cargan aquí), o anular esos rangos en el SII con la firma de quien los timbró. Folios sin usar acumulados bloquean el timbraje nuevo.".into()));
            pasos.push(paso("3. Fecha de corte", "Un cierre de mes: hasta ese día emite el proveedor, desde el siguiente este motor. La numeración sigue igual (es por empresa, no por software).".into()));
            pasos.push(paso("4. Timbrar folios con este motor", "Con el certificado cargado, pedir los rangos por tipo desde «Folios».".into()));
            pasos.push(paso("5. Actualizar datos en el SII", "Cambiar el nombre del software y, sobre todo, la casilla de intercambio (adonde llegan las facturas de tus proveedores), en «Actualización de datos del contribuyente». Todavía es manual.".into()));
            pasos.push(paso("6. Primer documento", "Emitir uno real y comprobar el veredicto del SII (aceptado), no solo el estado del envío.".into()));
        }
        "certificando" => {
            let bloq: Vec<&str> = [33, 61]
                .iter()
                .filter(|t| sit.get(t).map(|x| x.bloqueado).unwrap_or(false))
                .map(|t| if *t == 33 { "Factura (33)" } else { "Nota de crédito (61)" })
                .collect();
            if !bloq.is_empty() {
                pasos.push(j!({"titulo": "Destrabar folios de prueba", "urgente": true,
                    "detalle": format!("El timbraje de {} está bloqueado en certificación. Pídelo a la Mesa de Ayuda del SII (600 330 3000) para poder emitir los documentos del set de pruebas.", bloq.join(", "))}));
            }
            pasos.push(paso("Completar el set de pruebas", "Emitir y validar los documentos del set de certificación en el ambiente de pruebas.".into()));
            pasos.push(paso("Obtener la resolución del SII", format!("Al terminar el set, el SII emite la resolución que autoriza a {sw_b} como tu software de emisión.")));
            pasos.push(paso("Activar en producción", format!("Cambiar el software registrado en producción de «{sw_a}» a «{sw_b}» (Actualización de datos del contribuyente).")));
        }
        "certificado" => {
            pasos.push(paso("Activar en producción", format!("Cambiar el software registrado de «{sw_a}» a «{sw_b}» en el SII (Actualización de datos del contribuyente).")));
            pasos.push(paso("Emitir tu primer documento", "Probar la emisión real en producción con tu software propio.".into()));
        }
        _ => {
            pasos.push(paso("Registrar tu software", "Declarar tu software propio en el SII (Actualización de datos del contribuyente).".into()));
            pasos.push(paso("Certificar el software", "Realizar el set de pruebas en el ambiente de certificación.".into()));
        }
    }
    s.guardar_estado_negocio(
        cuenta,
        entero(&neg["id"]),
        &estado,
        &etiqueta,
        sw_prod.propio,
    );
    let o = |x: &str| {
        if x.is_empty() {
            "—".to_string()
        } else {
            x.to_string()
        }
    };
    ok(j!({
        "rut": rut, "razon_social": neg["razon_social"], "estado": estado, "etiqueta": etiqueta, "pasos": pasos,
        "produccion": {"software_actual": o(&sw_prod.software), "usa_software_propio": sw_prod.propio, "autorizado_a_emitir": autorizados},
        "certificacion": {"software": o(&sw_cert.software), "es_propio": sw_cert.propio, "finalizada": sw_cert.certificado, "fecha": sw_cert.fecha_resolucion},
    }))
}

const TIPOS_PANEL: [(i32, &str); 6] = [
    (33, "Factura"),
    (34, "Factura exenta"),
    (39, "Boleta"),
    (52, "Guía despacho"),
    (56, "Nota débito"),
    (61, "Nota crédito"),
];

/// `timbraje_negocio`: por tipo, autorizado y si el timbraje está bloqueado.
fn timbraje_negocio(s: &Servidor, p: &mut Peticion) -> Res {
    let (neg, cuenta) = s.negocio_o_404(p)?;
    let rut = texto(&neg["rut"]);
    let base = if texto(&neg["ambiente"]) == "produccion" {
        portal::BASE_PRODUCCION
    } else {
        portal::BASE_CERTIFICACION
    };
    let tipos: Vec<i32> = TIPOS_PANEL.iter().map(|(t, _)| *t).collect();
    let mut cli = s.portal_del_negocio(&neg, cuenta, base).map_err(sii502)?;
    let emp = cli.consultar_empresa_autorizada(&rut, "").map_err(sii502)?;
    let sit = cli.situacion_folios(&rut, &tipos).map_err(sii502)?;
    let autorizados: Vec<i32> = emp
        .map(|e| e.documentos.iter().map(|d| d.codigo).collect())
        .unwrap_or_default();
    let docs: Vec<Value> = TIPOS_PANEL
        .iter()
        .map(|(t, n)| j!({"tipo": t, "nombre": n, "autorizado": autorizados.contains(t), "bloqueado": sit.get(t).map(|x| x.bloqueado).unwrap_or(false)}))
        .collect();
    ok(j!({"rut": rut, "razon_social": neg["razon_social"], "documentos": docs}))
}

/// `portalDeQuery`: el portal (certificación) con el cert_id de la query. Cualquier fallo
/// (incluido "no encontrado") es 502, como el `except Exception` de Python.
fn portal_de_query(s: &Servidor, p: &Peticion, prefijo: &str) -> Result<Cliente, ErrorHTTP> {
    let v = p.q("cert_id");
    if v.is_empty() {
        return Err(ErrorHTTP::query("cert_id", "Field required", "missing"));
    }
    let cert_id: i64 = v.parse().map_err(|_| {
        ErrorHTTP::query(
            "cert_id",
            "Input should be a valid integer, unable to parse string as an integer",
            "int_parsing",
        )
    })?;
    let cuenta = s.cuenta_id(p);
    let err = |e: String| ErrorHTTP::sii_comunicacion(format!("{prefijo}{e}"));
    let (cert_pem, key_pem) = s.pem_del_keystore(cert_id, cuenta).map_err(err)?;
    let mut cli = Cliente::nuevo(
        cert_pem.as_bytes(),
        key_pem.as_bytes(),
        portal::BASE_CERTIFICACION,
    )
    .map_err(err)?;
    cli.autenticar("").map_err(err)?;
    Ok(cli)
}

fn onboarding_diagnostico(s: &Servidor, p: &mut Peticion) -> Res {
    let rut = p.q("rut");
    if !p.q("cert_id").is_empty() && rut.is_empty() {
        return Err(ErrorHTTP::query("rut", "Field required", "missing"));
    }
    let prefijo = "No se pudo diagnosticar la empresa en el SII: ";
    let mut cli = portal_de_query(s, p, prefijo)?;
    let d = onboarding::diagnosticar(
        &mut cli,
        &rut,
        &p.q("nombre_sistema"),
        &s.software_registrado(&rut),
        &s.cfg.nombre_software,
    )
    .map_err(|e| ErrorHTTP::sii_comunicacion(format!("{prefijo}{e}")))?;
    ok(serde_json::to_value(d).unwrap_or(Value::Null))
}

fn onboarding_cartera(s: &Servidor, p: &mut Peticion) -> Res {
    let prefijo = "No se pudo diagnosticar la cartera en el SII: ";
    let mut cli = portal_de_query(s, p, prefijo)?;
    let registrado = |r: &str| s.software_registrado(r);
    let ds = onboarding::diagnosticar_cartera(
        &mut cli,
        None,
        &p.q("nombre_sistema"),
        Some(&registrado),
        &s.cfg.nombre_software,
    )
    .map_err(|e| ErrorHTTP::sii_comunicacion(format!("{prefijo}{e}")))?;
    let out: Vec<Value> = ds
        .iter()
        .map(|d| {
            let pend = d.acciones.iter().filter(|a| !a.hecho).count();
            j!({"rut": d.rut, "razon_social": d.razon_social, "estado": d.estado, "etiqueta": d.etiqueta, "listo_para_emitir": d.listo_para_emitir, "acciones_pendientes": pend})
        })
        .collect();
    ok(Value::Array(out))
}
