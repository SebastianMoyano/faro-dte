"""
api/routes/keystore.py

Endpoints REST para la gestión de certificados por cuenta (almacén cifrado).
Backend de la "página para subir el certificado y su clave". Una cuenta (usuario
autenticado) puede tener VARIOS certificados. Todo protegido con JWT.

Los .p12 y sus claves se guardan CIFRADOS (core/keystore.py); estos endpoints
nunca devuelven secretos, sólo metadatos.
"""
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from api.util import leer_upload

from pydantic import BaseModel, Field

from core import keystore, negocios
from core.auth import requerir_autenticacion
from core.errors import CertificadoError, RecursoNoEncontrado, SIIError, ValidacionError

router = APIRouter(prefix="/api/v1/keystore", tags=["Certificados (Keystore)"])


@router.post("/certificados", status_code=status.HTTP_201_CREATED)
async def subir_certificado(
    archivo: UploadFile = File(..., description="Certificado .p12 / .pfx"),
    password: str = Form(..., description="Contraseña del certificado"),
    nombre: str = Form(None, description="Nombre descriptivo (opcional)"),
    alias: str = Form(None, description="Alias (opcional)"),
    usuario: dict = Depends(requerir_autenticacion),
) -> dict:
    """Sube y guarda (cifrado) un certificado para la cuenta autenticada.

    Valida el .p12 con la contraseña (rechaza si es incorrecta), extrae RUT y
    vencimiento, y lo almacena cifrado. Devuelve metadatos (sin secretos).
    """
    contenido = await leer_upload(archivo)
    try:
        info = keystore.guardar_certificado(
            usuario["id"], contenido, password, nombre=nombre, alias=alias)
    except Exception as e:
        raise CertificadoError(f"Certificado inválido o contraseña incorrecta: {e}")
    return info


@router.get("/certificados")
async def listar_certificados(usuario: dict = Depends(requerir_autenticacion)) -> list[dict]:
    """Lista los certificados de la cuenta (metadatos, sin secretos)."""
    keystore.init_keystore()
    return keystore.listar_certificados(usuario["id"])


@router.delete("/certificados/{cert_id}")
async def eliminar_certificado(
    cert_id: int, usuario: dict = Depends(requerir_autenticacion),
) -> dict:
    """Elimina un certificado de la cuenta."""
    if not keystore.eliminar_certificado(usuario["id"], cert_id):
        raise RecursoNoEncontrado("Certificado no encontrado")
    return {"eliminado": True, "id": cert_id}


@router.get("/certificados/{cert_id}/empresas")
async def empresas_del_certificado(
    cert_id: int, usuario: dict = Depends(requerir_autenticacion),
) -> list[dict]:
    """DESCUBRE todas las empresas asociadas a un certificado (donde el titular es
    representante / usuario autorizado) — vía el selector de empresas del SII. No hay
    que escribir RUTs: devuelve `[{rut, razon_social}]` listo para agregar."""
    from core.sii_portal import PortalSII
    try:
        with keystore.pem_transitorio(cert_id, usuario["id"]) as (cert_pem, key_pem):
            portal = PortalSII(cert_pem, key_pem)
            portal.autenticar()
            return portal.empresas_asociadas()
    except ValueError as e:
        raise RecursoNoEncontrado(str(e))
    except SIIError:
        raise
    except Exception as e:
        raise SIIError(f"No se pudo consultar el SII: {e}")


# ----------------------------------------------------------------- Negocios
@router.get("/empresa")
async def previsualizar_empresa(
    rut: str = Query(..., description="RUT del negocio, ej: 76111111-6"),
    ambiente: str = Query("certificacion", description="certificacion | produccion"),
    usuario: dict = Depends(requerir_autenticacion),
) -> dict:
    """Auto-relleno: razón social + tipos DTE autorizados de un RUT (consulta pública).
    Para mostrar el negocio ANTES de agregarlo. 404 si el RUT no está autorizado."""
    info = negocios.info_empresa(rut, ambiente)
    if not info:
        raise RecursoNoEncontrado(f"El RUT {rut} no aparece autorizado a emitir DTE.")
    return info


@router.post("/negocios", status_code=status.HTTP_201_CREATED)
async def agregar_negocio(
    rut: str = Form(...),
    cert_id: int = Form(None, description="Certificado que opera el negocio (opcional)"),
    ambiente: str = Form("certificacion"),
    usuario: dict = Depends(requerir_autenticacion),
) -> dict:
    """Agrega un negocio a la cuenta (auto-completa la razón social desde el SII)."""
    try:
        return negocios.agregar_negocio(usuario["id"], rut, cert_id, ambiente)
    except ValueError as e:
        raise ValidacionError(str(e))


@router.get("/negocios")
async def listar_negocios(usuario: dict = Depends(requerir_autenticacion)) -> list[dict]:
    """Lista los negocios de la cuenta (con los datos del emisor: giro, dirección…)."""
    negocios.init_negocios()
    return negocios.listar_negocios(usuario["id"])


class DatosNegocioIn(BaseModel):
    """Los datos fijos que van en la carátula de cada documento. Los campos ausentes (o null)
    no se tocan; un texto vacío borra el dato. Largos: los del XSD del SII (RznSoc 100,
    GiroEmis 80, DirOrigen 70, CmnaOrigen/CiudadOrigen 20)."""
    razon_social: Optional[str] = Field(None, max_length=100)
    giro: Optional[str] = Field(None, max_length=80)
    codigo_actividad: Optional[int] = None
    direccion: Optional[str] = Field(None, max_length=70)
    comuna: Optional[str] = Field(None, max_length=20)
    ciudad: Optional[str] = Field(None, max_length=20)
    email: Optional[str] = Field(None, max_length=80)
    telefono: Optional[str] = Field(None, max_length=20)


@router.put("/negocios/{negocio_id}", summary="Guardar los datos del emisor de un negocio (giro, dirección, código de actividad…)")
async def actualizar_negocio(
    negocio_id: int, body: DatosNegocioIn, usuario: dict = Depends(requerir_autenticacion),
) -> dict:
    """Lo que antes se configuraba en `EMPRESA_*` del `.env` para todo el motor, ahora por
    negocio y desde la app ("Mi empresa"). Devuelve el negocio con sus datos."""
    neg = negocios.actualizar_datos(usuario["id"], negocio_id, body.model_dump(exclude_unset=True))
    if not neg:
        raise RecursoNoEncontrado("Negocio no encontrado")
    return neg


# Tipos DTE más usados que se muestran en el panel de timbraje.
_TIPOS_PANEL = {33: "Factura", 34: "Factura exenta", 39: "Boleta",
                52: "Guía despacho", 56: "Nota débito", 61: "Nota crédito"}


def _negocio_o_404(cuenta_id: int, negocio_id: int) -> dict:
    negocios.init_negocios()
    neg = next((n for n in negocios.listar_negocios(cuenta_id) if n["id"] == negocio_id), None)
    if not neg:
        raise RecursoNoEncontrado("Negocio no encontrado")
    if not neg.get("cert_id"):
        raise ValidacionError("El negocio no tiene un certificado asociado")
    return neg


@router.get("/negocios/{negocio_id}/timbraje")
async def timbraje_negocio(
    negocio_id: int, usuario: dict = Depends(requerir_autenticacion),
) -> dict:
    """Panel de timbraje del negocio: por cada tipo de DTE, si está autorizado y si el
    timbraje está habilitado o bloqueado (anti-acaparamiento). Opera con el certificado
    asociado al negocio."""
    from core.sii_portal import BASE_CERTIFICACION, BASE_PRODUCCION, PortalSII
    neg = _negocio_o_404(usuario["id"], negocio_id)
    base = BASE_PRODUCCION if neg.get("ambiente") == "produccion" else BASE_CERTIFICACION
    tipos = list(_TIPOS_PANEL)
    try:
        with keystore.pem_transitorio(neg["cert_id"], usuario["id"]) as (cert_pem, key_pem):
            portal = PortalSII(cert_pem, key_pem, base=base)
            portal.autenticar()
            emp = portal.consultar_empresa_autorizada(neg["rut"])
            sit = portal.situacion_folios(neg["rut"], tipos)
    except Exception as e:
        raise SIIError(f"No se pudo consultar el SII: {e}")
    autorizados = {d.codigo for d in emp.documentos} if emp else set()
    docs = [{"tipo": t, "nombre": _TIPOS_PANEL[t], "autorizado": t in autorizados,
             "bloqueado": bool(sit.get(t, {}).get("bloqueado"))} for t in tipos]
    return {"rut": neg["rut"], "razon_social": neg.get("razon_social"), "documentos": docs}


class ClavesSIIIn(BaseModel):
    rut_empresa: str
    rut_usuario: Optional[str] = Field(None, description="RUT (con DV) de la persona autorizada que entra al SII")
    clave_tributaria: Optional[str] = Field(None, description="Clave tributaria de esa persona (vacía = no cambiar)")
    clave_certificado: Optional[str] = Field(None, description="Clave del certificado para la firma central (vacía = no cambiar)")


@router.put("/claves-sii", summary="Recordar (cifradas) las claves del SII de una empresa del gratuito")
async def guardar_claves_sii(body: ClavesSIIIn, usuario: dict = Depends(requerir_autenticacion)) -> dict:
    """Solo cuando la persona marcó 'recordar en este equipo'. Se cifran con la clave maestra
    del keystore (la misma del certificado). Nunca se devuelven en claro: la respuesta dice
    solo qué hay guardado. Por defecto las claves viven en memoria de la app y mueren con ella."""
    return keystore.guardar_claves_sii(usuario["id"], body.rut_empresa, body.rut_usuario,
                                       body.clave_tributaria or None, body.clave_certificado or None)


@router.get("/claves-sii/{rut_empresa}", summary="Qué claves del SII hay recordadas para la empresa (sin valores)")
async def estado_claves_sii(rut_empresa: str, usuario: dict = Depends(requerir_autenticacion)) -> dict:
    return keystore.estado_claves_sii(usuario["id"], rut_empresa)


@router.delete("/claves-sii/{rut_empresa}", summary="Olvidar las claves del SII de la empresa")
async def olvidar_claves_sii(rut_empresa: str, usuario: dict = Depends(requerir_autenticacion)) -> dict:
    return {"olvidadas": keystore.eliminar_claves_sii(usuario["id"], rut_empresa)}


@router.get("/negocios/{negocio_id}/contexto-emision",
            summary="Lo que el núcleo (Go) necesita para emitir por este negocio: certificado en PEM, resolución, rutas")
async def contexto_emision(
    negocio_id: int, usuario: dict = Depends(requerir_autenticacion),
) -> dict:
    """Entrega al cascarón de escritorio (app/) todo lo que el núcleo en Go necesita para
    emitir SIN sacar el certificado del keystore a disco: el par cert/clave en PEM (en
    memoria, por la conexión local), la resolución del emisor para su ambiente, y las rutas
    absolutas de la base y del storage. Solo por localhost y con la clave de la app; la
    clave privada viaja una vez por emisión y no se persiste en ningún lado."""
    from pathlib import Path
    from core.config import settings
    from core.database import DATABASE_PATH
    from core.resolucion import resolucion_emisor
    neg = _negocio_o_404(usuario["id"], negocio_id)
    p12, pwd = keystore._descifrar(neg["cert_id"], usuario["id"])
    cert_pem, key_pem = keystore._p12_a_pem(p12, pwd)
    ambiente = neg.get("ambiente") or settings.sii_ambiente
    fecha, numero = resolucion_emisor(neg["rut"], ambiente)
    return {
        "negocio_id": neg["id"], "rut": neg["rut"], "razon_social": neg.get("razon_social"),
        "ambiente": ambiente, "resolucion": {"fecha": fecha, "numero": numero},
        "cert_pem": cert_pem.decode("ascii"), "key_pem": key_pem.decode("ascii"),
        "database_path": str(Path(DATABASE_PATH).resolve()),
        "storage_path": str(Path(settings.storage_path).resolve()),
    }


@router.get("/negocios/{negocio_id}/estado")
async def estado_negocio(
    negocio_id: int, usuario: dict = Depends(requerir_autenticacion),
) -> dict:
    """Estado de habilitación del negocio para facturar con software propio:
    autorización en PRODUCCIÓN (por tipo + fecha), software con que emite hoy, y estado
    de la CERTIFICACIÓN del software propio (Maullín → producción)."""
    from core.sii_portal import BASE_CERTIFICACION, BASE_PRODUCCION, PortalSII
    neg = _negocio_o_404(usuario["id"], negocio_id)
    try:
        with keystore.pem_transitorio(neg["cert_id"], usuario["id"]) as (cert_pem, key_pem):
            portal = PortalSII(cert_pem, key_pem)
            portal.autenticar()
            emp = portal.consultar_empresa_autorizada(neg["rut"], base=BASE_PRODUCCION)
            sw_prod = portal.datos_software(neg["rut"], base=BASE_PRODUCCION)
            sw_cert = portal.datos_software(neg["rut"], base=BASE_CERTIFICACION)
            # ¿folios de factura/NC bloqueados en certificación? (freno del set de pruebas)
            sit_cert = portal.situacion_folios(neg["rut"], [33, 61])
    except Exception as e:
        raise SIIError(f"No se pudo consultar el SII: {e}")

    autorizados = [{"tipo": d.codigo, "nombre": d.descripcion, "desde": d.autorizado_desde}
                   for d in (emp.documentos if emp else [])
                   if not d.desautorizado_desde]

    # Estado de migración a software propio (misma lógica que el asistente).
    from core.onboarding import clasificar_software
    estado, etiqueta = clasificar_software(sw_prod, sw_cert, negocios.software_registrado(neg["rut"]))

    # Próximos pasos concretos según el punto de la migración.
    sw_a = sw_prod["software"] or "el sistema del SII"
    sw_b = sw_cert["software"] or "tu software propio"
    pasos = []
    if estado == "emitiendo":
        pasos = [{"titulo": "Todo listo", "detalle": f"Ya emites en producción con {sw_b}. No hay pasos pendientes.", "hecho": True}]
    elif estado == "externo":
        pasos = [
            {"titulo": "Emite con otro proveedor", "urgente": True,
             "detalle": f"El SII tiene registrado «{sw_a}» como software de esta empresa. No emitas facturas con este motor mientras siga así: los folios del CAF los administra ese proveedor y se chocarían. La autorización es de la empresa, así que cambiarse NO exige volver a certificar."},
            {"titulo": "1. Sacar el historial del proveedor",
             "detalle": "PDF y XML de todo lo emitido y recibido, antes de cortar el contrato: después ese acceso desaparece y no se puede importar desde aquí."},
            {"titulo": "2. Cerrar los folios que le quedan",
             "detalle": "Pedirle el último folio usado por tipo y los CAF sin usar (se cargan aquí), o anular esos rangos en el SII con la firma de quien los timbró. Folios sin usar acumulados bloquean el timbraje nuevo."},
            {"titulo": "3. Fecha de corte",
             "detalle": "Un cierre de mes: hasta ese día emite el proveedor, desde el siguiente este motor. La numeración sigue igual (es por empresa, no por software)."},
            {"titulo": "4. Timbrar folios con este motor",
             "detalle": "Con el certificado cargado, pedir los rangos por tipo desde «Folios»."},
            {"titulo": "5. Actualizar datos en el SII",
             "detalle": "Cambiar el nombre del software y, sobre todo, la casilla de intercambio (adonde llegan las facturas de tus proveedores), en «Actualización de datos del contribuyente». Todavía es manual."},
            {"titulo": "6. Primer documento",
             "detalle": "Emitir uno real y comprobar el veredicto del SII (aceptado), no solo el estado del envío."},
        ]
    elif estado == "certificando":
        bloq = [t for t in (33, 61) if sit_cert.get(t, {}).get("bloqueado")]
        if bloq:
            nb = ", ".join("Factura (33)" if t == 33 else "Nota de crédito (61)" for t in bloq)
            pasos.append({"titulo": "Destrabar folios de prueba", "urgente": True,
                          "detalle": f"El timbraje de {nb} está bloqueado en certificación. Pídelo a la Mesa de Ayuda del SII (600 330 3000) para poder emitir los documentos del set de pruebas."})
        pasos.append({"titulo": "Completar el set de pruebas",
                      "detalle": "Emitir y validar los documentos del set de certificación en el ambiente de pruebas."})
        pasos.append({"titulo": "Obtener la resolución del SII",
                      "detalle": f"Al terminar el set, el SII emite la resolución que autoriza a {sw_b} como tu software de emisión."})
        pasos.append({"titulo": "Activar en producción",
                      "detalle": f"Cambiar el software registrado en producción de «{sw_a}» a «{sw_b}» (Actualización de datos del contribuyente)."})
    elif estado == "certificado":
        pasos.append({"titulo": "Activar en producción",
                      "detalle": f"Cambiar el software registrado de «{sw_a}» a «{sw_b}» en el SII (Actualización de datos del contribuyente)."})
        pasos.append({"titulo": "Emitir tu primer documento",
                      "detalle": "Probar la emisión real en producción con tu software propio."})
    else:  # sin_propio
        pasos.append({"titulo": "Registrar tu software",
                      "detalle": "Declarar tu software propio en el SII (Actualización de datos del contribuyente)."})
        pasos.append({"titulo": "Certificar el software",
                      "detalle": "Realizar el set de pruebas en el ambiente de certificación."})

    negocios.guardar_estado(usuario["id"], negocio_id, estado, etiqueta, sw_prod["propio"])
    return {
        "rut": neg["rut"], "razon_social": neg.get("razon_social"),
        "estado": estado, "etiqueta": etiqueta, "pasos": pasos,
        "produccion": {
            "software_actual": sw_prod["software"] or "—",
            "usa_software_propio": sw_prod["propio"],
            "autorizado_a_emitir": autorizados,
        },
        "certificacion": {
            "software": sw_cert["software"] or "—",
            "es_propio": sw_cert["propio"],
            "finalizada": sw_cert["certificado"],
            "fecha": sw_cert["fecha_resolucion"],
        },
    }


@router.delete("/negocios/{negocio_id}")
async def eliminar_negocio(
    negocio_id: int, usuario: dict = Depends(requerir_autenticacion),
) -> dict:
    """Elimina un negocio de la cuenta."""
    if not negocios.eliminar_negocio(usuario["id"], negocio_id):
        raise RecursoNoEncontrado("Negocio no encontrado")
    return {"eliminado": True, "id": negocio_id}


@router.get("/negocios/{negocio_id}/movimientos")
async def movimientos_negocio(
    negocio_id: int,
    periodos: str = Query("", description="Meses YYYYMM separados por coma (máx. 3); vacío = este mes y el anterior"),
    limite: int = Query(50, ge=1, le=500),
    usuario: dict = Depends(requerir_autenticacion),
) -> dict:
    """Últimos movimientos del negocio tal como los tiene el SII (RCV de PRODUCCIÓN, compras y
    ventas), bajados en el momento con la sesión del certificado del negocio. Solo lectura."""
    from core.sii_portal import BASE_PRODUCCION, PortalSII
    from core import rcv as rcv_mod
    lista = rcv_mod.parsear_periodos(periodos)
    if lista is None:
        raise ValidacionError(rcv_mod.MENSAJE_PERIODOS)
    neg = _negocio_o_404(usuario["id"], negocio_id)
    rcv_mod.init_rcv_db()
    try:
        with keystore.pem_transitorio(neg["cert_id"], usuario["id"]) as (cert_pem, key_pem):
            portal = PortalSII(cert_pem, key_pem, base=BASE_PRODUCCION)
            portal.autenticar()
            cli = rcv_mod.RegistroCompraVenta(token=portal.cookies.get("TOKEN"))
            res = rcv_mod.ultimos_movimientos(cli, neg["rut"], lista, limite)
    except Exception as e:
        raise SIIError(f"No se pudo bajar el RCV del SII: {e}")
    return {"rut": neg["rut"], "razon_social": neg.get("razon_social"), **res}


@router.get("/negocios/{negocio_id}/f29")
async def f29_negocio(
    negocio_id: int, periodo: str = Query(..., description="Período tributario YYYYMM"),
    ppm: int = Query(0, description="PPM del período (opcional)"),
    remanente_anterior: int = Query(0, description="Remanente de crédito fiscal del mes anterior (opcional)"),
    usuario: dict = Depends(requerir_autenticacion),
) -> dict:
    """Propuesta de Formulario 29 (IVA mensual) del negocio para un período.

    Baja el RCV (Registro de Compras y Ventas) desde PRODUCCIÓN con la sesión del
    certificado del negocio, agrega los totales y los mapea a los casilleros del F29.
    Es sólo lectura: NO declara nada al SII.
    """
    from core.sii_portal import BASE_PRODUCCION, PortalSII
    from core import rcv as rcv_mod
    neg = _negocio_o_404(usuario["id"], negocio_id)
    rcv_mod.init_rcv_db()
    try:
        with keystore.pem_transitorio(neg["cert_id"], usuario["id"]) as (cert_pem, key_pem):
            portal = PortalSII(cert_pem, key_pem, base=BASE_PRODUCCION)
            portal.autenticar()
            cli = rcv_mod.RegistroCompraVenta(token=portal.cookies.get("TOKEN"))
            sync = cli.sincronizar_periodo(neg["rut"], periodo)
    except Exception as e:
        raise SIIError(f"No se pudo bajar el RCV del SII: {e}")
    desglose = rcv_mod.calcular_desglose_f29(
        neg["rut"], periodo, ppm=ppm, remanente_anterior=remanente_anterior)
    lineas = rcv_mod.mapear_a_f29(desglose)
    return {
        "rut": neg["rut"], "razon_social": neg.get("razon_social"), "periodo": periodo,
        "documentos": sync,
        "sin_datos": (sync.get("compras", 0) + sync.get("ventas", 0)) == 0,
        "ppm": ppm,
        "remanente_anterior": remanente_anterior,
        "total_debitos": desglose.total_debitos,
        "total_creditos": desglose.total_creditos,
        "iva_determinado": desglose.iva_determinado,
        "remanente_siguiente": desglose.remanente_siguiente,
        "total_a_pagar": desglose.total_a_pagar,
        "casilleros": [{"codigo": l.codigo, "glosa": l.glosa, "valor": l.valor} for l in lineas],
    }
