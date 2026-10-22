"""
core/sii_mipyme.py — Canal MIPYME: el Sistema de Facturación Gratuito del SII manejado por httpx.

**Es un canal DISTINTO al motor**, no un tipo de DTE más. Aquí el SII pone el folio, el CAF
y la firma (firma central con la clave del certificado que el SII guarda). Nada de esto pasa
por `core/sobre.py`, `core/caf.py` ni por los CAF locales: la Ley L2 no aplica porque no
firmamos nosotros.

¿Para qué sirve? Para dos cosas que el motor NO puede hacer:
  1. Facturas de una empresa que está en el gratuito para facturas (el SII no le timbra CAF
     a software externo → el motor no puede emitirle ni una T34).
  2. A futuro, la NC de anulación de boletas que `docs/ANULAR-BOLETA.md` manda hacer por el
     portal (hacerla con el motor cuesta el gratuito). **NO implementada**: la entrada de boletas
     (`mipeSelEmpresaBol.cgi`) ni siquiera está cableada.

⚠️ **Solo existe en producción** (Ley L1). No hay sandbox del portal MIPYME. Cada corrida que
llega a `mipeGenXMLFirma` gasta un folio real aunque después no se envíe. Por eso:
  - `preparar()` NO toca el SII más allá de leer el form (dry-run gratis).
  - `emitir()` exige `confirmar=True` y avisa que va a producción.
  - Este cliente NO usa `ClienteReintentos`: reintentar `GenXMLFirma` en un 502 gastaría dos folios.

La cadena (la que hace el navegador; captura completa de una factura emitida el 2026-10-08):

    login   POST zeusr.sii.cl/cgi_AUT2000/CAutInicio.cgi  → cookies NETSCAPE_LIVEWIRE.*
    1. mipeLaunchPage / mipeSelEmpresa (GET) / mipeSelEmpresa (POST RUT_EMP **con DV**)
    2. mipeGenFacEx?IGUAL=ULT_FAC&PTDC_CODIGO=34     → form pre-llenado "igual al último"
    2b. mipeDisplayPreView (POST, 54 campos)        → página PreViewDTE (243 campos)  ← OBLIGATORIO, gratis
    3. mipeGenXMLFirma                (243 campos)   → página de firma (frmSign)  ⚠️ gasta el folio
    4. postFirmaDigital               (clave del CERTIFICADO, no la tributaria) → XML firmado
    5. mipeSendXML                    (todo el frmSign + firma, orden ASCII, XML con CRLF)
    6. mipeDisplayPDF?DHDR_CODIGO=…
    logout  GET zeusr.sii.cl/cgi_AUT2000/autTermino.cgi siguiendo la redirección (si no, la sesión
            sigue viva ~2 h y se llega al tope de ~10 por RUT)

Estado de verificación DESDE ESTE REPO (`CADENAS_VERIFICADAS` sigue vacía; detalle en
`docs/LECCIONES-SII.md` → "Portal MIPYME" y `docs/MIPYME.md`):
  - Lectura, importación (PDF + XML por "Obtener Envío") y firma central: verificadas.
  - Emisión: tres folios perdidos en `mipeSendXML` (NC 57 y 58 el 2026-10-02; factura 61 el
    2026-10-08). En el 61 todo lo anterior funcionó (previsualización, GenXMLFirma, firma). La
    comparación fiel contra el envío real del navegador (factura 62, a mano) mostró solo
    diferencias de forma, ya corregidas: orden ASCII de los campos, el input duplicado
    `EFXP_FMA_PAGO`, saltos CRLF en los XML. Sin corrida real después de esa corrección.
  - Las facturas 48/57/59 de septiembre y la NC 56 salieron de la herramienta del repo de la empresa del gratuito
    (sesión del navegador), no de aquí. La NC 59 y la ND 1 se emitieron a mano en el portal.

Dos claves distintas (Ley L6: son credenciales, nunca a logs ni a disco en claro):
  - clave tributaria → login (`CAutInicio`)
  - clave del certificado → firma central (`postFirmaDigital` y `mipeSendXML`)

Sobre el `Request Rejected` del WAF F5: no es fingerprinting. En 2026-09 fue `RUT_EMP` sin dígito
verificador; en los envíos rechazados de octubre la única diferencia frente al navegador era la
forma del cuerpo (ver arriba). Los headers de navegador de abajo son los que el diff byte a byte
contra las capturas mostró como diferencia; se replican para no volver a investigar lo mismo.
"""
from __future__ import annotations

import logging
import html as html_mod
import re
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from typing import Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlencode

import httpx

from core import sala_espera
from core.errors import AutenticacionError, SIIError, SIIRechazoError, ValidacionError
from core.rut import formatear_rut, separar_rut, validar_rut

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------
BASE = "https://www1.sii.cl/cgi-bin/Portal001"
URL_LOGIN = "https://zeusr.sii.cl/cgi_AUT2000/CAutInicio.cgi"
# Cierre de sesión: el que invoca "Cerrar Sesión" de la barra del SII (zeus.sii.cl/admin/barranav.js).
# OJO: hay que SEGUIR la redirección; el GET a secas devuelve 302 y la sesión sigue viva en el
# servidor (así se acumularon sesiones hasta el tope el 2026-10-08). La variante herculesr sin
# seguir redirects no cerraba nada.
URL_LOGOUT = "https://zeusr.sii.cl/cgi_AUT2000/autTermino.cgi"
URL_SIIHOME = "https://misiir.sii.cl/cgi_misii/siihome.cgi"
REFERER_LOGIN = "https://zeusr.sii.cl/AUT2000/InicioAutenticacion/IngresoRutClave.html"
OPCION_EMISION = "OPCION=34&TIPO=4"   # menú "emisión de DTE" del portal (el tipo va en PTDC_CODIGO)

TIPO_FACTURA_EXENTA = 34
TIPO_NOTA_CREDITO = 61
TIPO_NOTA_DEBITO = 56
# Comentario que el JS del portal (`signXmlCompatible`) inserta antes de <Documento al firmar:
# navigator.platform, navegador, versión mayor, "central", VERSION. Capturado tal cual de un
# navegador real el 2026-10-04 (flujo "Obtener Envío").
COMENTARIO_FIRMA = "<!-- O MacIntel Chrome 154 central VERSION: v20170209 -->"


def preparar_dato_firma(xml: str) -> str:
    """Lo que el navegador manda en `dato` a `postFirmaDigital` (port de `signXmlCompatible`):
    escapa `&` y antepone el comentario al primer <Documento / <Liquidacion / <Exportaciones."""
    dato = xml.replace("&", "&amp;")
    if "<!-- O " in dato:
        return dato
    for tag in ("<Documento ", "<Liquidacion ", "<Exportaciones "):
        if tag in dato:
            return dato.replace(tag, COMENTARIO_FIRMA + "\n" + tag, 1)
    return dato

# Cadenas de emisión VERIFICADAS de punta a punta contra el SII DESDE ESTE REPO, con evidencia
# (tipo → DHDR/folio/fecha). Antes de la primera entrada se perdieron tres folios en `mipeSendXML`
# (NC 57 y 58 el 2026-10-02, factura 61 el 2026-10-08); la corrección del cuerpo del envío (orden
# ASCII, duplicados, CRLF; calcada de la captura de la factura 62) pasó el 2026-10-08 con la
# factura 63 y la NC 60 que la anuló. Emitir un tipo que NO esté aquí (p. ej. la nota de débito 56
# o la NC de corrección de montos, que es otro form) exige `aceptar_riesgo_folio=True`.
CADENAS_VERIFICADAS: dict = {
    TIPO_FACTURA_EXENTA: "factura 63 del 2026-10-08, DHDR 2881744181 (prueba la empresa del gratuito → la de software propio, $1.000; "
                         "BD id 118); cadena DisplayPreView → PreView → GenXMLFirma → SendXML",
    TIPO_NOTA_CREDITO: "NC 60 del 2026-10-08, DHDR 2881747811, anula (código 1) la factura 63 "
                       "(BD id 119); misma cadena vía preparar_nc_anulacion",
}
CODIGO_REF_ANULA = "1"
RAZON_REF_ANULA = "ANULA DOCUMENTO DE LA REFERENCIA"
CODIGO_REF_CORRIGE_MONTO = "3"
RAZON_REF_CORRIGE_MONTO = "CORRIGE MONTO"

# "Validar y visualizar" del formulario de NC para corregir montos (`mipeGenFacEx.cgi?IGUAL=
# CORR_MNT&VALOR=<DHDR>&PTDC_CODIGO=61`): el navegador manda EXACTAMENTE estos 60 campos a
# `mipeDisplayPreView.cgi` (capturado 2026-10-04 con la extensión, NC 59 emitida con éxito).
# POST "Validar y visualizar" de una FACTURA (`mipeDisplayPreView.cgi`): los 54 campos que manda
# el navegador, en su orden (captura con la extensión, 2026-10-04, formulario `VIEW_EFXP`).
# Este paso es OBLIGATORIO antes de `mipeGenXMLFirma`: sin él el portal responde "No se pudo
# generar XML para firmar" (código de mesa de ayuda 02.35.209.44…). Costó el intento del
# 2026-10-08 (sin folio perdido: el folio se asigna recién en GenXMLFirma).
CAMPOS_VALIDAR_FACTURA: Tuple[str, ...] = (
    "CANT_DET", "DESCRIP_01", "EFXP_ACTECO", "EFXP_ACTECO_SELECT", "EFXP_CDG_SII_SUCUR",
    "EFXP_CIUDAD_ORIGEN", "EFXP_CIUDAD_RECEP", "EFXP_CMNA_ORIGEN", "EFXP_CMNA_RECEP", "EFXP_CONTACTO",
    "EFXP_DIR_ORIGEN", "EFXP_DIR_ORIGEN_DEFUALT", "EFXP_DIR_RECEP", "EFXP_DIR_RECEP_DEFUALT",
    "EFXP_DSC_ITEM_01", "EFXP_DV_CHOFER", "EFXP_DV_RECEP", "EFXP_DV_SOLICITA", "EFXP_DV_TRANSPORTE",
    "EFXP_EMAIL_EMISOR", "EFXP_FCH_EMIS", "EFXP_FMA_PAGO", "EFXP_FONO_EMISOR", "EFXP_GIRO_EMIS",
    "EFXP_GIRO_RECEP", "EFXP_GIRO_RECEP_DEFUALT", "EFXP_MNT_DESC", "EFXP_MNT_NETO", "EFXP_MNT_TOTAL",
    "EFXP_NMB_01", "EFXP_NOMBRE_CHOFER", "EFXP_PATENTE", "EFXP_PCTD_01", "EFXP_PCT_DESC", "EFXP_PRC_01",
    "EFXP_QTY_01", "EFXP_RUT_CHOFER", "EFXP_RUT_RECEP", "EFXP_RUT_SOLICITA", "EFXP_RUT_TRANSPORTE",
    "EFXP_RZN_SOC", "EFXP_RZN_SOC_RECEP", "EFXP_SUBTOTAL", "EFXP_SUBT_01", "EFXP_TIPOCOMPRA_SELECT",
    "EFXP_TIPOVENTA_SELECT", "EFXP_UNMD_01", "EHDR_CODIGO", "ES_BORR", "IVA_TEMP", "MNT_NETO_TEMP",
    "PTDC_CODIGO", "esCRED_EC", "esFACT_TUR",
)

CAMPOS_VALIDAR_CORR_MNT: Tuple[str, ...] = (
    "CANT_DET", "DESCRIP_01", "EFXP_ACTECO", "EFXP_CDG_SII_SUCUR", "EFXP_CIUDAD_ORIGEN", "EFXP_CIUDAD_RECEP",
    "EFXP_CMNA_ORIGEN", "EFXP_CMNA_RECEP", "EFXP_CODIGO_REF_001", "EFXP_CONTACTO", "EFXP_DIR_ORIGEN", "EFXP_DIR_RECEP",
    "EFXP_DSC_ITEM_01", "EFXP_DV_RECEP", "EFXP_DV_SOLICITA", "EFXP_EMAIL_EMISOR", "EFXP_FCH_EMIS", "EFXP_FCH_REF_001",
    "EFXP_FOLIO_REF_001", "EFXP_FONO_EMISOR", "EFXP_GIRO_EMIS", "EFXP_GIRO_RECEP", "EFXP_IND_GLOBAL_001", "EFXP_MNT_DESC",
    "EFXP_MNT_NETO", "EFXP_MNT_TOTAL", "EFXP_NMB_01", "EFXP_NMB_02", "EFXP_NMB_03", "EFXP_PCTD_01", "EFXP_PCTD_02",
    "EFXP_PCTD_03", "EFXP_PCT_DESC", "EFXP_PRC_01", "EFXP_PRC_02", "EFXP_PRC_03", "EFXP_QTY_01", "EFXP_QTY_02", "EFXP_QTY_03",
    "EFXP_RAZON_REF_001", "EFXP_RUT_RECEP", "EFXP_RUT_SOLICITA", "EFXP_RZN_SOC", "EFXP_RZN_SOC_RECEP", "EFXP_SUBTOTAL",
    "EFXP_SUBT_01", "EFXP_SUBT_02", "EFXP_SUBT_03", "EFXP_TPO_DOC_REF_001", "EFXP_UNMD_01", "EFXP_UNMD_02", "EFXP_UNMD_03",
    "EHDR_CODIGO", "IVA_TEMP", "MNT_NETO_TEMP", "MNT_TOTAL_ORI", "PTDC_CODIGO", "REF_SI_NO", "TIPO_PLANTILLA", "esCRED_EC",
)

# ---------------------------------------------------------------------------
# Headers de navegador. Lo que el diff contra la captura de mitmproxy mostró que faltaba:
# Accept exacto de Chrome, Accept-Encoding con br/zstd, Cache-Control en los POST.
# ---------------------------------------------------------------------------
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36")
_ACCEPT = ("text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,"
           "image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7")


def _accept_encoding() -> str:
    """Anuncia br/zstd solo si podemos decodificarlos (si no, httpx revienta al leer)."""
    enc = ["gzip", "deflate"]
    try:
        import brotli  # noqa: F401
        enc.append("br")
    except ImportError:
        pass
    try:
        import zstandard  # noqa: F401
        enc.append("zstd")
    except ImportError:
        pass
    return ", ".join(enc)


def _headers_base() -> dict:
    return {
        "User-Agent": _UA,
        "Accept": _ACCEPT,
        "Accept-Encoding": _accept_encoding(),
        "Accept-Language": "es-CL,es;q=0.9,en;q=0.8",
        "sec-ch-ua": '"Chromium";v="152", "Not?A_Brand";v="24", "Google Chrome";v="152"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "Upgrade-Insecure-Requests": "1",
    }


# ---------------------------------------------------------------------------
# Schema del form: los 243 nombres que el JS del portal manda a mipeGenXMLFirma, EN ORDEN.
# El HTML solo trae ~40 inputs; el resto lo genera el JS al enviar. Mandar menos campos da
# "requerimiento no bien recepcionado". Contrato capturado 2026-09-06.
# ---------------------------------------------------------------------------
SCHEMA_CAMPOS: Tuple[str, ...] = (
    "INDICA_PRIMERA_EJECUCION", "ES_BORR", "EHDR_CODIGO", "AGENTE_RETENEDOR", "PTES_ESTADO",
    "TIPO_PLANTILLA", "MNT_TOTAL_ORI", "PTDC_CODIGO", "EFXP_FMA_PAGO", "EFXP_FCH_PAGO_001",
    "EFXP_FCH_PAGO_002", "EFXP_FCH_PAGO_003", "EFXP_MNT_PAGO_001", "EFXP_MNT_PAGO_002",
    "EFXP_MNT_PAGO_003", "EFXP_GLOSA_PAGOS_001", "EFXP_GLOSA_PAGOS_002",
    "EFXP_GLOSA_PAGOS_003", "EFXP_FCH_VENC", "EFXP_IND_VENTA", "EFXP_RZN_SOC",
    "EFXP_GIRO_EMIS", "EFXP_ACTECO", "EFXP_CDG_SII_SUCUR", "EFXP_DIR_ORIGEN",
    "EFXP_CMNA_ORIGEN", "EFXP_CIUDAD_ORIGEN", "EFXP_TIPOVENTA_SELECT",
    "EFXP_TIPOCOMPRA_SELECT", "EFXP_DOC_REF_RAIZ", "EFXP_COMUNA_ROL_ORIGEN",
    "EFXP_MAN_ROL_ORIGEN", "EFXP_PRED_ROL_ORIGEN", "EFXP_SIST_REF_GEO",
    "EFXP_LAT_ORIG_MADERA", "EFXP_LONG_ORIG_MADERA", "EFXP_COD_PLAN_CONAF",
    "UBIC_MADERA_SI_NO", "EFXP_EMAIL_EMISOR", "EFXP_FONO_EMISOR", "EFXP_RUT_RECEP",
    "EFXP_DV_RECEP", "EFXP_FCH_EMIS", "EFXP_RZN_SOC_RECEP", "EFXP_GIRO_RECEP",
    "EFXP_DIR_RECEP", "EFXP_CIUDAD_RECEP", "EFXP_CMNA_RECEP", "EFXP_CONTACTO",
    "EFXP_RUT_SOLICITA", "EFXP_DV_SOLICITA",
    # 10 ítems × 10 campos
    *[f"EFXP_{c}_{i:02d}" for i in range(1, 11)
      for c in ("TPO_COD", "COD", "NMB", "QTY", "UNMD", "PRC", "OTRO_IMP", "PCTD", "SUBT", "DSC_ITEM")],
    "EFXP_SUBTOTAL", "EFXP_PCT_DESC", "EFXP_MNT_DESC",
    "EFXP_MNT_NETO", "EFXP_TASA_IVA", "EFXP_IVA", "EFXP_MNT_TOTAL", "EFXP_OTRO_IMP_TOTAL",
    # 3 referencias × 6 campos
    *[f"EFXP_{c}_{i:03d}" for i in range(1, 4)
      for c in ("TPO_DOC_REF", "IND_GLOBAL", "FOLIO_REF", "FCH_REF", "CODIGO_REF", "RAZON_REF")],
    "EMP_MENOR_TAMANO",
    *[f"EDL_PRC_EXE_{i:02d}" for i in range(1, 11)],
    *[f"EDL_TPO_DOC_LIQ_{i:02d}" for i in range(1, 11)],
    "EDL_MNT_NET_TRAN", "EDL_MNT_EXE_TRAN", "EDL_TASA_IVA_TRAN", "EDL_IVA_TRAN", "EDL_SUBTOTAL_TRAN",
    *[f"EDL_COM_{c}_{i:02d}" for i in range(1, 4)
      for c in ("TIP_MOV", "GLOSA", "ITM_EXENTO", "VAL")],
    "EDL_MNT_NET_COM", "EDL_MNT_EXE_COM", "EDL_TASA_IVA_COM", "EDL_IVA_COM", "EDL_SUBTOTAL_COM",
    "EFXP_CRED_EC", "esCRED_EC", "esFACT_TUR", "EFXP_TPO_DOC_TURISTA", "EFXP_NUM_ID",
    "EFXP_NACIONALIDAD", "EDFE_PAIS_DEST", "EDFE_TPO_MON", "EDFE_PTO_EMBAR", "EDFE_PTO_DESEM",
    "EDFE_TOT_BOLT", "EDFE_MOD_VTA", "EDFE_TPO_EXPO", "EDFE_PTO_EMBAR_TXT", "EDFE_PTO_DESEM_TXT",
    "EDFE_TAX_ID", "EDFE_TPO_CAMBIO", "EFXP_RUT_TRANSPORTE", "EFXP_DV_TRANSPORTE",
    "EFXP_PATENTE", "EFXP_RUT_CHOFER", "EFXP_DV_CHOFER", "EFXP_NOMBRE_CHOFER",
)

# Inputs "helper" que el HTML trae pero el JS del SII NO manda. No son drift. Verificado en
# vivo 2026-09-07: el HTML trae 43 campos; los `*_DEFUALT` (sic, el typo es del SII) son
# EFXP_DIR_ORIGEN_DEFUALT, EFXP_DIR_RECEP_DEFUALT y EFXP_GIRO_RECEP_DEFUALT.
CAMPOS_HELPER = frozenset({
    "CANT_DET", "COD_SI_NO", "REF_SI_NO", "PAGO_SI_NO", "IVA_TEMP", "MNT_NETO_TEMP",
    "btnSign", "btnVolver",   # botones de la página de revisión de notas (2026-10-02)
    "btnCorregir",            # botón del form de factura, apareció el 2026-10-08 (factura 63)
})


def es_campo_helper(nombre: str) -> bool:
    return nombre in CAMPOS_HELPER or nombre.endswith("_DEFUALT")

# Los 5 montos que deben coincidir SIEMPRE (con 1 ítem). Sustituir solo 2 de ellos dio el
# reparo HED-2-220 (ítem ≠ total). Con N ítems: PRC/SUBT por ítem + los 3 totales.
CAMPOS_MONTO_TOTAL = ("EFXP_SUBTOTAL", "EFXP_MNT_NETO", "EFXP_MNT_TOTAL")


# ---------------------------------------------------------------------------
# Claves desde el Llavero de macOS (mismo patrón que test_sobre.py con el .pfx). Así el
# operador nunca escribe la clave en un chat, un argumento ni un .env (L6).
#   security add-generic-password -s sii-clave-tributaria -a 12345678-9      (sin -w: la pide)
#   security add-generic-password -s sii-clave-certificado -a 12345678-9
# ---------------------------------------------------------------------------
LLAVERO_CLAVE_TRIBUTARIA = "sii-clave-tributaria"
LLAVERO_CLAVE_CERTIFICADO = "sii-clave-certificado"


def clave_desde_llavero(servicio: str, rut: str) -> Optional[str]:
    """La clave guardada en el Llavero para (servicio, rut), o None si no está / no es macOS."""
    import shutil
    import subprocess
    if not shutil.which("security"):
        return None
    cuenta = formatear_rut(rut, con_puntos=False)
    r = subprocess.run(["security", "find-generic-password", "-s", servicio, "-a", cuenta, "-w"],
                       capture_output=True, text=True)
    return r.stdout.strip() or None if r.returncode == 0 else None


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------
@dataclass
class ReceptorMipyme:
    rut: str
    razon_social: str
    giro: str
    direccion: str
    ciudad: str
    comuna: str

    def __post_init__(self):
        if not validar_rut(self.rut):
            raise ValidacionError(f"RUT de receptor inválido: {self.rut}")


@dataclass
class ItemMipyme:
    nombre: str
    precio: int
    cantidad: int = 1
    descripcion: str = ""

    @property
    def subtotal(self) -> int:
        return self.precio * self.cantidad


@dataclass
class PayloadMipyme:
    """Payload listo para `mipeGenXMLFirma`, en el orden del schema. `campos_nuevos` es el
    drift: inputs que el HTML trae y el schema no conoce → el form del SII cambió."""
    tipo: int
    pares: List[Tuple[str, str]]
    campos_nuevos: List[str] = field(default_factory=list)

    def valor(self, nombre: str) -> str:
        return dict(self.pares).get(nombre, "")

    @property
    def monto_total(self) -> int:
        return int(self.valor("EFXP_MNT_TOTAL") or 0)


@dataclass
class ResultadoMipyme:
    tipo: int
    folio: int
    dhdr: str                 # DHDR_CODIGO: id del documento en el portal (sirve para el PDF y la NC)
    xml_firmado: str          # XML con la <Signature> puesta por el SII
    monto_total: int
    pdf: Optional[bytes] = None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
class _FormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.campos: dict = {}
        self._select: Optional[str] = None
        self._select_marcado = False      # ya vimos una <option selected>
        self._select_vio_opcion = False   # ya vimos la primera <option>
        self._textarea: Optional[str] = None

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        nombre = d.get("name")
        if tag == "input" and nombre:
            self.campos.setdefault(nombre, d.get("value") or "")
        elif tag == "select" and nombre:
            self._select = nombre
            self._select_marcado = False
            self._select_vio_opcion = False
            self.campos.setdefault(nombre, "")
        elif tag == "option" and self._select is not None:
            # la opción marcada gana; si ninguna está marcada, la primera (como el navegador)
            if "selected" in d:
                self.campos[self._select] = d.get("value") or ""
                self._select_marcado = True
            elif not self._select_marcado and not self._select_vio_opcion:
                self.campos[self._select] = d.get("value") or ""
            self._select_vio_opcion = True
        elif tag == "textarea" and nombre:
            self._textarea = nombre
            self.campos.setdefault(nombre, "")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        if self._textarea:
            self.campos[self._textarea] = self.campos.get(self._textarea, "") + data

    def handle_endtag(self, tag):
        if tag == "select":
            self._select = None
        elif tag == "textarea":
            self._textarea = None


def parsear_form(html: str) -> dict:
    """`{name: value}` de todos los input/select/textarea del HTML del portal."""
    p = _FormParser()
    p.feed(html)
    return p.campos


def extraer_xml_dte(html: str) -> Optional[str]:
    """El XML del DTE que `mipeGenXMLFirma` devuelve embebido en la página."""
    m = re.search(r"<\?xml.*?</DTE>", html, re.S)
    if m:
        return m.group(0)
    m = re.search(r"var\s+\w*[Xx][Mm][Ll]\w*\s*=\s*['\"](.*?)['\"];", html, re.S)
    return m.group(1) if m else None


def extraer_form_firma(html: str) -> Optional[dict]:
    """El `<form name="frmSign">` que `mipeGenXMLFirma` devuelve: `{"action": ..., "campos": {...}}`.
    Es lo que el navegador envía a `mipeSendXML` (más txtSignText y los datos de firma). La
    implementación que SÍ emitió una NC por httpx (app/sii/service_http.py del repo de la empresa del gratuito,
    port de un Apps Script) manda ESTOS campos, no una reconstrucción del schema."""
    for m in re.finditer(r"<form\b([^>]*)>([\s\S]*?)</form>", html, re.I):
        attrs, inner = m.group(1), m.group(2)
        nombre = re.search(r'name=["\']([^"\']+)', attrs, re.I)
        if not nombre or nombre.group(1).lower() != "frmsign":
            continue
        accion = re.search(r'action=["\']([^"\']*)', attrs, re.I)
        campos = parsear_form("<form>" + inner + "</form>")
        # Lista cruda (orden del HTML, con duplicados): el frmSign real trae EFXP_FMA_PAGO dos
        # veces y el navegador manda las dos (captura 2026-10-08, factura 62).
        # inputs y textareas por separado (un solo regex con el contenido opcional del textarea
        # se tragaba el textarea siguiente a un input: perdía txtPlainText), fusionados por posición.
        encontrados = []
        for m2 in re.finditer(r"<input\b([^>]*)>", inner, re.I):
            n = re.search(r'name=["\']([^"\']+)', m2.group(1), re.I)
            vm = re.search(r'value=["\']([^"\']*)', m2.group(1), re.I)
            if n:
                encontrados.append((m2.start(), n.group(1), html_mod.unescape(vm.group(1)) if vm else ""))
        for m2 in re.finditer(r"<textarea\b([^>]*)>([\s\S]*?)</textarea>", inner, re.I):
            n = re.search(r'name=["\']([^"\']+)', m2.group(1), re.I)
            if n:
                encontrados.append((m2.start(), n.group(1), html_mod.unescape(m2.group(2))))
        crudos = [(k, v) for _, k, v in sorted(encontrados)]
        return {"action": accion.group(1) if accion else "", "campos": campos, "crudos": crudos}
    return None


def parsear_respuesta_firma(cruda: str) -> Tuple[int, str]:
    """`postFirmaDigital` responde en más de un formato (port del repo de la empresa del gratuito, que sí emitió):
    JSON `{"status"|"statusCode": n, "text": "<xml firmado>"}` (a veces con prefijo anti-JSON
    `while(1);`), un objeto estilo JS, o el XML firmado directo. Devuelve `(status, xml)`;
    status 0 = OK, 2 + "clave no corresponde" = clave del certificado incorrecta.
    Usar el JSON entero como txtSignText manda al SII un body que el navegador jamás manda."""
    import json
    t = (cruda or "").strip()
    t = re.sub(r"^while\s*\(\s*1\s*\)\s*;?", "", t).strip()
    if re.match(r"^\s*<\?xml|^\s*<DTE\b", t, re.I):
        return 0, t
    try:
        obj = json.loads(t)
        if isinstance(obj, dict) and ("status" in obj or "statusCode" in obj):
            return int(obj.get("statusCode", obj.get("status"))), str(obj.get("text", ""))
    except ValueError:
        pass
    m = re.search(r"\{[\s\S]*\}", t)
    if m:
        bloque = m.group(0)
        try:
            obj = json.loads(bloque)
            if isinstance(obj, dict) and ("status" in obj or "statusCode" in obj):
                return int(obj.get("statusCode", obj.get("status"))), str(obj.get("text", ""))
        except ValueError:
            pass
        ms = re.search(r"\b(?:statusCode|status)\b\s*:\s*(\d+)", bloque, re.I)
        mt = re.search(r'\btext\b\s*:\s*["\']([\s\S]*?)["\']\s*(?:,|\})', bloque, re.I)
        if ms and mt:
            return int(ms.group(1)), mt.group(1)
    return -1, ""


def _folio_de(xml: str) -> int:
    m = re.search(r"<Folio>(\d+)</Folio>", xml)
    if not m:
        raise SIIError("El XML del portal no trae <Folio>", detalle={"inicio": xml[:200]})
    return int(m.group(1))


def _esta_rechazado(r: httpx.Response) -> bool:
    return r.status_code != 200 or "Request Rejected" in r.text


# ---------------------------------------------------------------------------
# Historial de emitidos (mipeAdminDocsEmi.cgi). Contrato del tool externo sii_descargar_pdfs.py
# (2026-09-07): tabla paginada "Página N de M"; cada fila trae un enlace con `CODIGO=<DHDR>` y
# las celdas [RUT receptor, razón social, tipo (texto), folio, fecha, monto, estado].
# ---------------------------------------------------------------------------
@dataclass
class DocumentoEmitido:
    dhdr: str
    tipo_texto: str
    folio: int
    fecha: str            # YYYY-MM-DD
    monto: int
    estado: str
    receptor_rut: str
    receptor_razon: str

    @property
    def tipo(self) -> Optional[int]:
        return tipo_desde_texto(self.tipo_texto)


_TIPOS_TEXTO = (
    (r"NOTA\s+DE\s+CR[EÉ]DITO", 61), (r"NOTA\s+DE\s+D[EÉ]BITO", 56),
    (r"GU[IÍ]A\s+DE\s+DESPACHO", 52), (r"FACTURA\s+DE\s+COMPRA", 46),
    (r"EXENTA|NO\s+AFECTA", 34), (r"FACTURA", 33),
)


def tipo_desde_texto(texto: str) -> Optional[int]:
    """El listado muestra el tipo como texto ("FACTURA NO AFECTA O EXENTA ELECTRONICA")."""
    t = texto.upper()
    m = re.search(r"\b(33|34|39|41|46|52|56|61)\b", t)
    if m:
        return int(m.group(1))
    for patron, tipo in _TIPOS_TEXTO:
        if re.search(patron, t):
            return tipo
    return None


def _monto_desde_texto(texto: str) -> int:
    digitos = re.sub(r"[^\d]", "", texto.split(",")[0])
    return int(digitos) if digitos else 0


def _fecha_iso(texto: str) -> str:
    t = texto.strip()
    m = re.match(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", t)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", t)
    return m.group(0) if m else t


class _FilasParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.filas: List[dict] = []
        self._fila: Optional[dict] = None
        self._celda: Optional[int] = None

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "tr":
            self._fila = {"celdas": [], "hrefs": []}
        elif self._fila is not None and tag in ("td", "th"):
            self._fila["celdas"].append("")
            self._celda = len(self._fila["celdas"]) - 1
        elif self._fila is not None and tag == "a" and d.get("href"):
            self._fila["hrefs"].append(d["href"])

    def handle_data(self, data):
        if self._fila is not None and self._celda is not None:
            self._fila["celdas"][self._celda] += data

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self._celda = None
        elif tag == "tr" and self._fila is not None:
            if any(c.strip() for c in self._fila["celdas"]):
                self.filas.append(self._fila)
            self._fila = None


def parsear_listado(html: str) -> Tuple[List[DocumentoEmitido], int]:
    """`(documentos, última_página)` de una página de `mipeAdminDocsEmi.cgi`."""
    p = _FilasParser()
    p.feed(html)
    docs: List[DocumentoEmitido] = []
    for fila in p.filas:
        dhdr = next((m.group(1) for h in fila["hrefs"] for m in [re.search(r"[?&]CODIGO=(\d+)", h)] if m), None)
        celdas = [re.sub(r"\s+", " ", c).strip() for c in fila["celdas"]]
        celdas = [c for c in celdas if c]
        if not dhdr or len(celdas) < 7:
            continue
        folio = re.sub(r"[^\d]", "", celdas[3])
        if not folio:
            continue
        docs.append(DocumentoEmitido(dhdr=dhdr, tipo_texto=celdas[2], folio=int(folio),
                                     fecha=_fecha_iso(celdas[4]), monto=_monto_desde_texto(celdas[5]),
                                     estado=celdas[6], receptor_rut=celdas[0].replace(".", ""),
                                     receptor_razon=celdas[1]))
    descartadas = sum(1 for f in p.filas if any(re.search(r"[?&]CODIGO=(\d+)", h) for h in f["hrefs"])) - len(docs)
    if descartadas > 0:
        logger.warning("MIPYME: %d fila(s) del historial con DHDR pero sin las 7 celdas esperadas (descartadas)", descartadas)
    m = re.search(r"[Pp][áa]gina\s+\d+\s+(?:de|/)\s+(\d+)", html)
    return docs, (int(m.group(1)) if m else 1)


def total_desde_pdf(pdf: bytes) -> int:
    """`TOTAL $ 3.068.005` del PDF que genera el portal. Lanza SIIError si no lo encuentra."""
    import io
    try:
        from pypdf import PdfReader
    except ImportError as e:  # pragma: no cover
        raise SIIError("Falta la librería pypdf para leer el total del documento") from e
    texto = "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(pdf)).pages)
    coincidencias = re.findall(r"(?<![A-Z])TOTAL\s*\$\s*([\d.]+)", texto)   # excluye SUBTOTAL
    if not coincidencias:
        raise SIIError("No se encontró el TOTAL en el PDF del documento")
    return int(coincidencias[-1].replace(".", ""))


# ---------------------------------------------------------------------------
# Armado del payload (puro, sin red)
# ---------------------------------------------------------------------------
def armar_payload(campos_html: dict, tipo: int, receptor: ReceptorMipyme,
                  items: Sequence[ItemMipyme], fecha: Optional[str] = None,
                  forma_pago: str = "2", referencias: Iterable[dict] = (),
                  respetar_html: Iterable[str] = ()) -> PayloadMipyme:
    """Payload = schema (orden) + valores del HTML fresco + sustituciones nuestras.

    Los montos van SIEMPRE alineados: `PRC`/`SUBT` de cada ítem y los 3 totales salen del
    mismo número (HED-2-220 si no). `referencias`: dicts con `tipo`, `folio`, `fecha`,
    `codigo`, `razon` (para la NC de anulación).
    """
    if not items:
        raise ValidacionError("La factura necesita al menos un ítem")
    if len(items) > 10:
        raise ValidacionError("El form del portal admite hasta 10 ítems")
    rut_n, dv = separar_rut(receptor.rut)
    total = sum(i.subtotal for i in items)
    subs = {
        "INDICA_PRIMERA_EJECUCION": "1", "ES_BORR": "FALSE",
        "PTDC_CODIGO": str(tipo), "EFXP_FMA_PAGO": forma_pago,
        "EFXP_FCH_EMIS": fecha or date.today().isoformat(),
        "EFXP_RUT_RECEP": rut_n, "EFXP_DV_RECEP": dv,
        "EFXP_RZN_SOC_RECEP": receptor.razon_social, "EFXP_GIRO_RECEP": receptor.giro,
        "EFXP_DIR_RECEP": receptor.direccion, "EFXP_CIUDAD_RECEP": receptor.ciudad,
        "EFXP_CMNA_RECEP": receptor.comuna,
    }
    # Limpiar los 10 ítems que el form pre-llena "igual al último" y poner los nuestros.
    for i in range(1, 11):
        for c in ("TPO_COD", "COD", "NMB", "QTY", "UNMD", "PRC", "OTRO_IMP", "PCTD", "SUBT", "DSC_ITEM"):
            subs[f"EFXP_{c}_{i:02d}"] = ""
    if tipo not in (TIPO_NOTA_CREDITO, TIPO_NOTA_DEBITO):
        for i in range(1, 4):   # una factura nueva no hereda referencias del último documento
            for c in ("TPO_DOC_REF", "IND_GLOBAL", "FOLIO_REF", "FCH_REF", "CODIGO_REF", "RAZON_REF"):
                subs[f"EFXP_{c}_{i:03d}"] = ""
        subs["EFXP_PCT_DESC"] = "0"; subs["EFXP_MNT_DESC"] = "0"
    for n, it in enumerate(items, start=1):
        subs[f"EFXP_NMB_{n:02d}"] = it.nombre
        subs[f"EFXP_QTY_{n:02d}"] = str(it.cantidad)
        subs[f"EFXP_PRC_{n:02d}"] = str(it.precio)
        subs[f"EFXP_SUBT_{n:02d}"] = str(it.subtotal)
        subs[f"EFXP_DSC_ITEM_{n:02d}"] = it.descripcion
    for c in CAMPOS_MONTO_TOTAL:
        subs[c] = str(total)
    for n, ref in enumerate(referencias, start=1):
        subs[f"EFXP_TPO_DOC_REF_{n:03d}"] = str(ref["tipo"])
        subs[f"EFXP_FOLIO_REF_{n:03d}"] = str(ref["folio"])
        subs[f"EFXP_FCH_REF_{n:03d}"] = ref["fecha"]
        subs[f"EFXP_CODIGO_REF_{n:03d}"] = str(ref.get("codigo", CODIGO_REF_ANULA))
        subs[f"EFXP_RAZON_REF_{n:03d}"] = ref.get("razon", RAZON_REF_ANULA)

    # `respetar_html`: campos que NO se sobreescriben (en una nota el portal deja ES_BORR y
    # EFXP_FMA_PAGO vacíos, y INDICA_PRIMERA_EJECUCION ya viene en 1; capturado 2026-10-02).
    for n in respetar_html:
        if n in campos_html:
            subs[n] = campos_html[n]
    if tipo in (TIPO_NOTA_CREDITO, TIPO_NOTA_DEBITO):
        # Nota: el navegador manda el form PreViewDTE TAL CUAL (orden y campos del HTML, incluidos
        # los que el schema no conoce), con nuestras sustituciones encima. No reordenar por schema.
        pares = [(n, subs.get(n, v)) for n, v in campos_html.items() if n not in ("btnSign", "btnVolver")]
        for n in SCHEMA_CAMPOS:   # lo que el HTML no trae pero el schema exige, al final
            if n not in campos_html:
                pares.append((n, subs.get(n, "")))
    else:
        pares = [(n, subs.get(n, campos_html.get(n, ""))) for n in SCHEMA_CAMPOS]
    nuevos = sorted(n for n in campos_html if n not in SCHEMA_CAMPOS and not es_campo_helper(n))
    if nuevos:
        logger.warning("Portal MIPYME: el form trae %d campo(s) que el schema no conoce: %s",
                       len(nuevos), nuevos)
    return PayloadMipyme(tipo=tipo, pares=pares, campos_nuevos=nuevos)


def armar_send_xml_desde_form(form_firma: dict, xml_firmado: str, clave_cert: str,
                              tmst_firma: str) -> List[Tuple[str, str]]:
    """Body de `mipeSendXML` tal como lo manda el navegador (captura completa del 2026-10-08,
    factura 62 emitida): TODOS los inputs del `frmSign` que devolvió `mipeGenXMLFirma`
    (incluidos los duplicados, p. ej. `EFXP_FMA_PAGO` dos veces) más lo que el JS rellena
    (txtSignText, nombre, clave, nodo, nodoId, nameSpace; TMST_SIGN viaja como venga en la
    página), y el conjunto **ordenado por nombre en orden ASCII** (mayúsculas antes que
    minúsculas): así quedan `clave`, `nameSpace`, `nodo`, `nodoId`, `nombre`, `txtPlainText`,
    `txtSignText` al final. El orden anterior (firma antes de txtPlainText, resto en orden del
    HTML) fue la única diferencia estructural frente al navegador en el envío rechazado del
    folio 61; `txtPlainText` ya viene en el form."""
    crudos = list(form_firma.get("crudos") or form_firma["campos"].items())
    omitir = {"btnSign", "btnVolver", "txtSignText", "nombre", "clave", "nodo", "nodoId", "nameSpace"}
    pares: List[Tuple[str, str]] = [(k, v) for k, v in crudos if k not in omitir]
    if not any(k == "TMST_SIGN" and v for k, v in pares):
        pares = [(k, v) for k, v in pares if k != "TMST_SIGN"] + [("TMST_SIGN", tmst_firma)]
    campos = dict(form_firma["campos"])
    pares += [("txtSignText", xml_firmado), ("nombre", campos.get("nombre") or "1"), ("clave", clave_cert),
              ("nodo", campos.get("nodo") or "dte:DTE"), ("nodoId", campos.get("nodoId") or "dte:Documento"),
              ("nameSpace", campos.get("nameSpace") or "http://www.sii.cl/SiiDte")]
    # Los dos XML viajan como valores de <textarea>: el navegador normaliza sus saltos de línea a
    # CRLF al enviar el formulario (captura 2026-10-08: 0 LF sueltos). El módulo mandaba LF.
    crlf = lambda t: t.replace("\r\n", "\n").replace("\n", "\r\n")
    pares = [(k, crlf(v) if k in ("txtPlainText", "txtSignText") else v) for k, v in pares]
    pares.sort(key=lambda kv: kv[0])   # sort es estable: los duplicados conservan su orden relativo
    return pares


def _body(pares: Sequence[Tuple[str, str]]) -> bytes:
    # El portal es ISO-8859-1 (como todo el SII, Ley L8): el percent-encoding tiene que ser de los
    # bytes latin-1 (`ñ` → %F1), y `urlencode` sin `encoding=` los haría UTF-8 (%C3%B1). Sin
    # quote() previo: un doble encoding deja %2520 en el body.
    try:
        return urlencode(list(pares), doseq=True, encoding="latin-1", errors="strict").encode("ascii")
    except UnicodeEncodeError as e:
        raise ValidacionError("Hay un carácter que el portal (ISO-8859-1) no admite: revisa comillas "
                              "tipográficas, guiones largos o símbolos como €",
                              detalle={"caracter": e.object[e.start:e.end]}) from e


def _guardar_evidencia(rut_empresa: Optional[str], tipo: int, folio: int, paso: str, respuesta: str,
                       xml_firmado: Optional[str], pares: Sequence[Tuple[str, str]],
                       secretos: Sequence[str], extras: Optional[dict] = None) -> Optional[str]:
    """Deja en `storage/mipyme_fallidos/<rut>/T<tipo>_F<folio>_<ts>/` lo necesario para
    diagnosticar un fallo DESPUÉS de gastar el folio: respuesta del portal, XML firmado (vale
    oro: es el documento con la firma del SII) y el body enviado. Los secretos (claves) se
    reemplazan por *** antes de escribir (L6). Best-effort: nunca lanza."""
    try:
        from datetime import datetime
        from pathlib import Path
        from html import escape as _esc
        from urllib.parse import quote, quote_plus
        def limpiar(t: str) -> str:
            for sec in secretos:
                if not sec or len(sec) < 4:
                    continue
                for variante in {sec, quote(sec), quote_plus(sec), _esc(sec),
                                 quote(sec, encoding="latin-1", errors="replace")}:
                    t = t.replace(variante, "***")
            return t
        carpeta = Path("storage") / "mipyme_fallidos" / (rut_empresa or "sin_rut").replace("-", "") \
            / f"T{tipo}_F{folio}_{datetime.now().strftime('%Y%m%dT%H%M%S')}"
        carpeta.mkdir(parents=True, exist_ok=True)
        (carpeta / f"respuesta_{paso}.html").write_text(limpiar(respuesta), encoding="iso-8859-1", errors="replace")
        if xml_firmado:
            (carpeta / "dte_firmado.xml").write_text(xml_firmado, encoding="iso-8859-1", errors="replace")
        # Una sola línea x-www-form-urlencoded (como viaja), parseable con parse_qsl. El formato
        # anterior ("k=v" por línea) partía los valores multilínea y el comparador leía nombres
        # falsos sacados del base64 de la firma.
        (carpeta / f"body_{paso}.txt").write_bytes(
            _body([(k, limpiar(v) if isinstance(v, str) else v) for k, v in pares]))
        for nombre, contenido in (extras or {}).items():
            (carpeta / nombre).write_text(limpiar(contenido or ""), encoding="iso-8859-1", errors="replace")
        return str(carpeta)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Cliente
# ---------------------------------------------------------------------------
class ClienteMipyme:
    """Sesión contra el portal MIPYME. Úsalo como context manager: el `__exit__` cierra la
    sesión en el SII (tope de ~10 concurrentes por RUT; ver memoria `sii-cerrar-sesion`).

        with ClienteMipyme(rut_usuario, clave_tributaria, clave_certificado) as cli:
            cli.iniciar_sesion()
            cli.seleccionar_empresa("77.777.777-7")
            payload = cli.preparar(TIPO_FACTURA_EXENTA, receptor, [item])   # gratis
            res = cli.emitir(payload, confirmar=True)                       # gasta folio, PRODUCCIÓN
    """

    def __init__(self, rut_usuario: str, clave_tributaria: str, clave_certificado: str,
                 timeout: float = 40.0):
        if not validar_rut(rut_usuario):
            raise ValidacionError(f"RUT de usuario inválido: {rut_usuario}")
        self.rut_usuario = formatear_rut(rut_usuario, con_puntos=False)
        self._clave_trib = clave_tributaria
        self._clave_cert = clave_certificado
        # httpx.Client a secas, NO ClienteReintentos: un reintento de GenXMLFirma gasta 2 folios.
        self._s = httpx.Client(timeout=timeout, follow_redirects=False, headers=_headers_base(),
                               event_hooks=sala_espera.HOOKS)
        self.empresa: Optional[str] = None
        self._autenticado = False
        self._menu_html = ""   # menú de la empresa (respuesta de mipeSelEmpresa): de aquí salen los enlaces
        self._referer_emision: Optional[str] = None   # la página desde la que el navegador firmaría
        self._evidencia_extra: dict = {}              # página de firma + respuesta cruda de postFirma

    # --------------------------------------------------------------- plumbing
    def __enter__(self) -> "ClienteMipyme":
        return self

    def __exit__(self, *exc) -> None:
        self.cerrar()

    def _limpiar_cookies(self) -> None:
        """El SII manda Set-Cookie con valor `None`; reenviarlos así malforma el request."""
        for c in list(self._s.cookies.jar):
            if c.value is None or c.value == "None":
                self._s.cookies.jar.clear(c.domain, c.path, c.name)

    def _req(self, metodo: str, url: str, *, data=None, content=None, params=None,
             referer: Optional[str] = None, origin: Optional[str] = None,
             navegacion: bool = True, extra: Optional[dict] = None) -> httpx.Response:
        h = {"Sec-Fetch-Site": "same-origin"}
        if extra:
            h.update(extra)
        if navegacion:
            h.update({"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"})
        else:
            h.update({"Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"})
        if origin:
            h["Origin"] = origin
        if referer:
            h["Referer"] = referer
        if metodo == "POST":
            h["Content-Type"] = "application/x-www-form-urlencoded"
            if navegacion:
                h["Cache-Control"] = "max-age=0"
                h["Sec-Fetch-User"] = "?1"
        if data is not None:
            # httpx codifica `data=` en UTF-8; el portal es ISO-8859-1 (L8). Todo body pasa por _body().
            content, data = _body(list(data.items())), None
        try:
            r = self._s.request(metodo, url, content=content, params=params, headers=h)
        except httpx.TransportError as e:
            raise SIIError(f"Sin respuesta del portal MIPYME ({type(e).__name__})") from e
        self._limpiar_cookies()
        logger.debug("MIPYME %s %s → %d (%dB)", metodo, url, r.status_code, len(r.content))
        return r

    @staticmethod
    def _texto(r: httpx.Response) -> str:
        return r.content.decode("latin-1", "replace")

    # ------------------------------------------------------------------ sesión
    def iniciar_sesion(self) -> None:
        """Login por clave tributaria + entrada al menú de emisión. PRODUCCIÓN."""
        n, dv = separar_rut(self.rut_usuario)
        r = self._req("POST", URL_LOGIN, referer=REFERER_LOGIN, origin="https://zeusr.sii.cl",
                      data={"rut": n, "dv": dv, "referencia": URL_SIIHOME, "411": "",
                            "rutcntr": formatear_rut(self.rut_usuario, con_puntos=True),
                            "clave": self._clave_trib})
        if not any(c.name.startswith("NETSCAPE_LIVEWIRE") for c in self._s.cookies.jar):
            # El SII devuelve 200 con HTML de error, no un 401: mirar las cookies es lo fiable.
            raise AutenticacionError("El SII no abrió sesión: clave tributaria incorrecta o "
                                     "tope de sesiones concurrentes alcanzado",
                                     detalle={"http": r.status_code})
        self._autenticado = True
        self._req("GET", URL_SIIHOME, referer=REFERER_LOGIN)
        self._req("GET", f"{BASE}/mipeLaunchPage.cgi", params={"OPCION": "34", "TIPO": "4"},
                  referer=URL_SIIHOME)
        self._req("GET", f"{BASE}/mipeSelEmpresa.cgi", params={"DESDE_DONDE_URL": OPCION_EMISION},
                  referer=URL_SIIHOME)

    def seleccionar_empresa(self, rut_empresa: str) -> None:
        """Elige la empresa a operar. El RUT va COMPLETO con DV: sin él, el WAF F5 responde
        `Request Rejected` y toda la cadena hereda el rechazo."""
        if not validar_rut(rut_empresa):
            raise ValidacionError(f"RUT de empresa inválido: {rut_empresa}")
        rut = formatear_rut(rut_empresa, con_puntos=False)
        r = self._req("POST", f"{BASE}/mipeSelEmpresa.cgi", origin="https://www1.sii.cl",
                      referer=f"{BASE}/mipeSelEmpresa.cgi?DESDE_DONDE_URL=OPCION%3D34%26TIPO%3D4",
                      data={"DESDE_DONDE_URL": OPCION_EMISION, "RUT_EMP": rut})
        if _esta_rechazado(r) or len(r.content) < 400:
            raise SIIRechazoError("El portal no aceptó la selección de empresa",
                                  detalle={"http": r.status_code, "rut": rut,
                                           "inicio": self._texto(r)[:200]})
        self.empresa = rut
        self._menu_html = self._texto(r)

    def enlaces_menu(self) -> List[Tuple[str, str]]:
        """`[(texto, href)]` del menú de la empresa. Sirve para descubrir sin adivinar los CGI
        que todavía no capturamos (historial de emitidos, XML, etc.). Solo lectura."""
        h = self._menu_html
        enlaces: List[Tuple[str, str]] = []
        for href, texto in re.findall(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', h, re.S | re.I):
            enlaces.append((re.sub(r"<[^>]+>", "", texto).strip(), href))
        # Los portales CGI del SII navegan también con frames, forms, selects y JS.
        for src in re.findall(r'<i?frame\b[^>]*src=["\']([^"\']+)', h, re.I):
            enlaces.append(("[frame]", src))
        for action in re.findall(r'<form\b[^>]*action=["\']([^"\']+)', h, re.I):
            enlaces.append(("[form]", action))
        for val, texto in re.findall(r'<option\b[^>]*value=["\']([^"\']*\.cgi[^"\']*)["\'][^>]*>(.*?)</option>', h, re.S | re.I):
            enlaces.append(("[option] " + re.sub(r"<[^>]+>", "", texto).strip(), val))
        for url in re.findall(r'(?:location(?:\.href)?\s*=|window\.open\(|\.submit\(|href=)\s*["\']([^"\']*\.cgi[^"\']*)', h, re.I):
            enlaces.append(("[js]", url))
        for url, texto in re.findall(r'onclick=["\'][^"\']*?["\']([^"\']*\.cgi[^"\']*)["\'][^"\']*["\'][^>]*>(.*?)<', h, re.S | re.I):
            enlaces.append(("[onclick] " + re.sub(r"<[^>]+>", "", texto).strip(), url))
        vistos, unicos = set(), []
        for t, u in enlaces:
            if (t, u) not in vistos:
                vistos.add((t, u)); unicos.append((t, u))
        return unicos

    def resumen_menu(self) -> dict:
        """Qué contiene la página del menú (para depurar sin exponer su HTML completo)."""
        h = self._menu_html
        titulo = re.search(r"<title>(.*?)</title>", h, re.S | re.I)
        return {"bytes": len(h), "titulo": (titulo.group(1).strip() if titulo else ""),
                "frames": len(re.findall(r"<i?frame\b", h, re.I)), "forms": len(re.findall(r"<form\b", h, re.I)),
                "anchors": len(re.findall(r"<a\b", h, re.I)), "scripts": len(re.findall(r"<script\b", h, re.I)),
                "cgis_mencionados": sorted(set(re.findall(r"([A-Za-z0-9_]+\.cgi)", h)))}

    def cerrar(self) -> None:
        """Cierra la sesión en el SII (best-effort, nunca lanza). Sin esto se acumulan
        sesiones y el SII niega el siguiente login."""
        try:
            if self._autenticado:
                self._s.get(URL_LOGOUT, timeout=20, follow_redirects=True,
                            headers={"Referer": f"{BASE}/mipeSelEmpresa.cgi"})
                # Si el SII no borró las cookies de sesión, el cierre no surtió efecto: avisar.
                if any(c.name.startswith("NETSCAPE_LIVEWIRE") for c in self._s.cookies.jar):
                    logger.warning("Portal MIPYME: autTermino no cerró la sesión (cookies de sesión siguen vivas)")
        except Exception:
            pass
        finally:
            self._autenticado = False
            self._s.close()

    # ------------------------------------------------------------- lectura (gratis)
    def leer_form(self, tipo: int = TIPO_FACTURA_EXENTA) -> dict:
        """El form pre-llenado "igual al último emitido". NO gasta folio."""
        self._requiere_empresa()
        r = self._req("GET", f"{BASE}/mipeGenFacEx.cgi", referer=f"{BASE}/mipeSelEmpresa.cgi",
                      params={"IGUAL": "ULT_FAC", "PTDC_CODIGO": str(tipo), "ES_BORR": "FALSE",
                              "esCRED_EC": "FALSE", "esFACT_TUR": "FALSE"})
        if _esta_rechazado(r) or len(r.content) < 5000:
            raise SIIRechazoError("El portal no entregó el formulario de emisión "
                                  "(¿sesión expirada, empresa sin documentos previos?)",
                                  detalle={"http": r.status_code, "inicio": self._texto(r)[:200]})
        return parsear_form(self._texto(r))

    def preparar(self, tipo: int, receptor: ReceptorMipyme, items: Sequence[ItemMipyme],
                 fecha: Optional[str] = None, forma_pago: str = "1") -> PayloadMipyme:
        """Dry-run de una factura: lee el form, hace el POST "Validar y visualizar"
        (`mipeDisplayPreView`, 54 campos, como el navegador) y arma el payload desde la página
        PreViewDTE que devuelve. NO gasta folio: el folio se asigna recién en GenXMLFirma.

        Por qué el paso previo: la cadena verificada en septiembre (contrato de la empresa del gratuito) fue
        PreView → DisplayPreView → GenXMLFirma; la sesión del navegador ya traía la
        previsualización hecha. Ir directo a GenXMLFirma desde una sesión httpx limpia devuelve
        "No se pudo generar XML para firmar" (2026-10-08). Mismo esquema que
        `preparar_nc_correccion_montos`."""
        if tipo in (TIPO_NOTA_CREDITO, 56):
            return armar_payload(self.leer_form(tipo), tipo, receptor, items, fecha, forma_pago)
        if len(items) != 1:
            raise ValidacionError("El paso 'Validar y visualizar' de factura está calcado para 1 línea "
                                  "(el navegador manda EFXP_*_01); con más líneas hay que capturar ese flujo")
        self._requiere_empresa()
        base = self.leer_form(tipo)
        it = items[0]
        total = it.subtotal
        rut_n, dv = separar_rut(receptor.rut)
        nuestros = {
            "CANT_DET": "1", "DESCRIP_01": "DESCRIP_01", "EHDR_CODIGO": "", "ES_BORR": "FALSE",
            "PTDC_CODIGO": str(tipo), "esCRED_EC": "FALSE", "esFACT_TUR": "FALSE",
            "EFXP_ACTECO_SELECT": base.get("EFXP_ACTECO", ""), "EFXP_TIPOCOMPRA_SELECT": "1", "EFXP_TIPOVENTA_SELECT": "1",
            "EFXP_FCH_EMIS": fecha or date.today().isoformat(), "EFXP_FMA_PAGO": str(forma_pago),
            "EFXP_RUT_RECEP": rut_n, "EFXP_DV_RECEP": dv, "EFXP_RZN_SOC_RECEP": receptor.razon_social,
            "EFXP_GIRO_RECEP": receptor.giro, "EFXP_GIRO_RECEP_DEFUALT": receptor.giro,
            # El JS del portal copia la dirección del campo auxiliar (_DEFUALT) al real y le deja
            # cuatro espacios al final (captura 2026-10-04); vacío → "Debe ingresar el campo:
            # Direccion del contribuyente emisor" (2026-10-08).
            "EFXP_DIR_ORIGEN": (base.get("EFXP_DIR_ORIGEN") or base.get("EFXP_DIR_ORIGEN_DEFUALT", "")).rstrip() + "    ",
            "EFXP_DIR_RECEP": receptor.direccion.rstrip() + "    ", "EFXP_DIR_RECEP_DEFUALT": receptor.direccion,
            "EFXP_CIUDAD_RECEP": receptor.ciudad, "EFXP_CMNA_RECEP": receptor.comuna, "EFXP_CONTACTO": "",
            "EFXP_NMB_01": it.nombre, "EFXP_QTY_01": str(it.cantidad), "EFXP_PRC_01": str(it.precio),
            "EFXP_SUBT_01": str(it.subtotal), "EFXP_DSC_ITEM_01": it.descripcion, "EFXP_UNMD_01": "", "EFXP_PCTD_01": "",
            "EFXP_PCT_DESC": "0", "EFXP_MNT_DESC": "0", "IVA_TEMP": "", "MNT_NETO_TEMP": str(total),
            "EFXP_SUBTOTAL": str(total), "EFXP_MNT_NETO": str(total), "EFXP_MNT_TOTAL": str(total),
        }
        pares = [(k, nuestros.get(k, base.get(k, ""))) for k in CAMPOS_VALIDAR_FACTURA]
        url_form = f"{BASE}/mipeGenFacEx.cgi?IGUAL=ULT_FAC&PTDC_CODIGO={tipo}&ES_BORR=FALSE&esCRED_EC=FALSE&esFACT_TUR=FALSE"
        r = self._req("POST", f"{BASE}/mipeDisplayPreView.cgi", content=_body(pares), referer=url_form,
                      origin="https://www1.sii.cl")
        vista = self._texto(r)
        if _esta_rechazado(r) or "PreViewDTE" not in vista:
            _guardar_evidencia(self.empresa, tipo, 0, "validar", vista, None, pares,
                               (self._clave_cert, self._clave_trib))
            raise SIIRechazoError("El portal no aceptó 'Validar y visualizar' de la factura",
                                  detalle={"http": r.status_code, "largo": len(vista),
                                           "error_sii": (re.search(r"CODIGO:\s*([\d.]+)", vista) or [None, None])[1]})
        self._referer_emision = f"{BASE}/mipeDisplayPreView.cgi"
        return armar_payload(parsear_form(vista), tipo, receptor, items, fecha, forma_pago,
                             respetar_html=("ES_BORR", "INDICA_PRIMERA_EJECUCION"))

    def total_registrado(self, dhdr: str) -> int:
        """El TOTAL del documento tal como el SII lo registró: se lee del PDF que el propio SII
        genera desde el XML firmado. ⚠️ NO usar el monto del listado (`listar_emitidos`): en un
        documento inconsistente el listado muestra la línea, no el total (caso factura 57:
        listado 3.270.323, total firmado 3.068.005). NO gasta folio."""
        return total_desde_pdf(self.descargar_pdf(dhdr))

    def preparar_nc_anulacion(self, dhdr: str, tipo_ref: int, folio_ref: int, fecha_ref: str,
                              receptor: ReceptorMipyme, items: Sequence[ItemMipyme],
                              fecha: Optional[str] = None, verificar_monto: bool = True) -> PayloadMipyme:
        """NC de anulación (T61) del documento `dhdr`. El portal pre-construye la NC en
        `mipeDisplayPreView?EHDR_CODIGO&TIPO_NOTA=61`; luego es la misma cadena con
        `PTDC_CODIGO=61` y la referencia `CODIGO_REF=1`.

        **Guardarraíl (`verificar_monto`)**: la suma de `items` debe ser EXACTAMENTE el total
        registrado del documento anulado (leído de su PDF). Si no, `ValidacionError` ANTES de
        gastar folio. Costó real: la NC 56 salió por 3.270.323 contra una factura 57 registrada
        en 3.068.005 → reparo `REF-2-780`, un abono de más y una corrección con dos documentos.

        ⚠️ Estado (2026-10-02): la NC por httpx desde este repo se corrió UNA vez y FALLÓ en
        `mipeSendXML` (WAF). El envío se reescribió con el `frmSign` real y no se ha vuelto a
        correr. Esta función NO gasta folio; `emitir()` sí.
        """
        return self._preparar_nota_anulacion(TIPO_NOTA_CREDITO, dhdr, tipo_ref, folio_ref, fecha_ref,
                                             receptor, items, fecha, verificar_monto)

    def preparar_nd_anulacion(self, dhdr: str, folio_ref: int, fecha_ref: str,
                              receptor: ReceptorMipyme, items: Sequence[ItemMipyme],
                              fecha: Optional[str] = None, verificar_monto: bool = True) -> PayloadMipyme:
        """Nota de DÉBITO (T56) que deja sin efecto la nota de crédito `dhdr` (código 1): el mismo
        formulario que la NC, pre-construido por el portal con `TIPO_NOTA=56` (capturado el
        2026-10-02 al emitir a mano la ND 1 que anuló la NC 56). El documento que esa NC anulaba
        vuelve a valer. Mismo guardarraíl: `items` debe sumar el total registrado de la NC.
        NO gasta folio; la cadena 56 no se ha corrido desde el módulo, así que `emitir()` exige
        `aceptar_riesgo_folio=True`."""
        return self._preparar_nota_anulacion(TIPO_NOTA_DEBITO, dhdr, TIPO_NOTA_CREDITO, folio_ref, fecha_ref,
                                             receptor, items, fecha, verificar_monto)

    def _preparar_nota_anulacion(self, tipo_nota: int, dhdr: str, tipo_ref: int, folio_ref: int, fecha_ref: str,
                                 receptor: ReceptorMipyme, items: Sequence[ItemMipyme],
                                 fecha: Optional[str], verificar_monto: bool) -> PayloadMipyme:
        """Lo común a la NC (61) y la ND (56) de anulación: `mipeDisplayPreView?EHDR_CODIGO&TIPO_NOTA=<tipo_nota>`."""
        self._requiere_empresa()
        sigla = "NC" if tipo_nota == TIPO_NOTA_CREDITO else "ND"
        if verificar_monto:
            total_ref = self.total_registrado(dhdr)
            total_nc = sum(i.subtotal for i in items)
            if total_nc != total_ref:
                raise ValidacionError(
                    f"La {sigla} sumaría {total_nc:,} pero el documento {tipo_ref}/{folio_ref} está "
                    f"registrado por {total_ref:,}: el SII la aceptaría con reparo REF-2-780 y el "
                    f"abono quedaría mal. Usa el total registrado.".replace(",", "."),
                    detalle={"monto_nc": total_nc, "total_registrado": total_ref, "dhdr": dhdr})
        r = self._req("GET", f"{BASE}/mipeDisplayPreView.cgi",
                      params={"EHDR_CODIGO": dhdr, "TIPO_NOTA": str(tipo_nota)},
                      referer=f"{BASE}/mipeGesDocEmi.cgi?CODIGO={dhdr}")
        if _esta_rechazado(r) or len(r.content) < 1000:
            raise SIIRechazoError(f"El portal no pre-construyó la {sigla} de anulación",
                                  detalle={"http": r.status_code, "dhdr": dhdr,
                                           "inicio": self._texto(r)[:200]})
        ref = {"tipo": tipo_ref, "folio": folio_ref, "fecha": fecha_ref,
               "codigo": CODIGO_REF_ANULA, "razon": RAZON_REF_ANULA}
        # El navegador firma desde la página de revisión: usarla como Referer en GenXMLFirma.
        self._referer_emision = f"{BASE}/mipeDisplayPreView.cgi?EHDR_CODIGO={dhdr}&TIPO_NOTA={tipo_nota}"
        return armar_payload(parsear_form(self._texto(r)), tipo_nota, receptor, items,
                             fecha, forma_pago="", referencias=[ref],
                             respetar_html=("ES_BORR", "EFXP_FMA_PAGO", "INDICA_PRIMERA_EJECUCION"))

    def preparar_nc_correccion_montos(self, dhdr: str, tipo_ref: int, folio_ref: int, fecha_ref: str,
                                      receptor: ReceptorMipyme, items: Sequence[ItemMipyme],
                                      fecha: Optional[str] = None, verificar_monto: bool = True) -> PayloadMipyme:
        """NC (T61) para CORREGIR MONTOS del documento `dhdr` (código de referencia 3). Replica el
        flujo real capturado el 2026-10-04 (NC 59 de la empresa del gratuito, emitida con éxito en el navegador):
        GET del form `IGUAL=CORR_MNT` → POST "Validar y visualizar" a `mipeDisplayPreView.cgi` con
        los 60 campos exactos → página PreViewDTE (243 campos) → de ahí `emitir()` sigue igual que
        el navegador (GenXMLFirma → firma central → frmSign → mipeSendXML).

        Una NC de corrección por el TOTAL registrado deja el documento en cero (lo que se hizo con
        la factura 57). `verificar_monto`: la NC no puede superar el total registrado. NO gasta folio.
        """
        self._requiere_empresa()
        url_form = f"{BASE}/mipeGenFacEx.cgi?IGUAL=CORR_MNT&VALOR={dhdr}&PTDC_CODIGO={TIPO_NOTA_CREDITO}"
        r = self._req("GET", f"{BASE}/mipeGenFacEx.cgi", referer=f"{BASE}/mipeGesDocEmi.cgi?ALL_PAGE_ANT=2&CODIGO={dhdr}",
                      params={"IGUAL": "CORR_MNT", "VALOR": dhdr, "PTDC_CODIGO": str(TIPO_NOTA_CREDITO)})
        html = self._texto(r)
        if _esta_rechazado(r) or len(html) < 5000:
            raise SIIRechazoError("El portal no entregó el formulario de corrección de montos",
                                  detalle={"dhdr": dhdr, "http": r.status_code})
        base = parsear_form(html)
        total_ref = int(re.sub(r"[^\d]", "", base.get("EFXP_MNT_TOTAL") or base.get("MNT_TOTAL_ORI") or "0") or 0)
        total_nc = sum(i.subtotal for i in items)
        if verificar_monto and (total_ref <= 0 or total_nc > total_ref):
            raise ValidacionError(
                f"La NC de corrección sumaría {total_nc:,} y el documento está registrado por {total_ref:,}".replace(",", "."),
                detalle={"monto_nc": total_nc, "total_registrado": total_ref, "dhdr": dhdr})
        rut_n, dv = separar_rut(receptor.rut)
        nuestros = {
            "CANT_DET": "3", "DESCRIP_01": "DESCRIP_01", "REF_SI_NO": "SiChecked", "TIPO_PLANTILLA": "CORR_MNT_V_NC",
            "esCRED_EC": "FALSE", "IVA_TEMP": "", "MNT_NETO_TEMP": str(total_ref), "MNT_TOTAL_ORI": str(total_ref),
            "EHDR_CODIGO": dhdr, "PTDC_CODIGO": str(TIPO_NOTA_CREDITO), "EFXP_FCH_EMIS": fecha or date.today().isoformat(),
            "EFXP_RUT_RECEP": rut_n, "EFXP_DV_RECEP": dv, "EFXP_RZN_SOC_RECEP": receptor.razon_social,
            "EFXP_GIRO_RECEP": receptor.giro, "EFXP_DIR_RECEP": receptor.direccion, "EFXP_CIUDAD_RECEP": receptor.ciudad,
            "EFXP_CMNA_RECEP": receptor.comuna, "EFXP_PCT_DESC": "0", "EFXP_MNT_DESC": "0",
            "EFXP_SUBTOTAL": str(total_nc), "EFXP_MNT_NETO": str(total_nc), "EFXP_MNT_TOTAL": str(total_nc),
            "EFXP_TPO_DOC_REF_001": str(tipo_ref), "EFXP_FOLIO_REF_001": str(folio_ref), "EFXP_FCH_REF_001": fecha_ref,
            "EFXP_CODIGO_REF_001": CODIGO_REF_CORRIGE_MONTO, "EFXP_RAZON_REF_001": RAZON_REF_CORRIGE_MONTO, "EFXP_IND_GLOBAL_001": "",
        }
        for i in range(1, 4):
            for c in ("NMB", "QTY", "PRC", "SUBT", "UNMD", "PCTD"):
                nuestros[f"EFXP_{c}_{i:02d}"] = ""
        nuestros["EFXP_DSC_ITEM_01"] = ""
        if len(items) > 3:
            raise ValidacionError("El formulario de corrección admite hasta 3 líneas")
        for n, it in enumerate(items, start=1):
            nuestros[f"EFXP_NMB_{n:02d}"] = it.nombre; nuestros[f"EFXP_QTY_{n:02d}"] = str(it.cantidad)
            nuestros[f"EFXP_PRC_{n:02d}"] = str(it.precio); nuestros[f"EFXP_SUBT_{n:02d}"] = str(it.subtotal)
            if n == 1:
                nuestros["EFXP_DSC_ITEM_01"] = it.descripcion
        pares = [(k, nuestros.get(k, base.get(k, ""))) for k in CAMPOS_VALIDAR_CORR_MNT]
        r = self._req("POST", f"{BASE}/mipeDisplayPreView.cgi", content=_body(pares), referer=url_form,
                      origin="https://www1.sii.cl")
        vista = self._texto(r)
        if _esta_rechazado(r) or "PreViewDTE" not in vista:
            raise SIIRechazoError("El portal no aceptó 'Validar y visualizar' de la NC de corrección",
                                  detalle={"dhdr": dhdr, "http": r.status_code, "largo": len(vista),
                                           "error_sii": (re.search(r"CODIGO:\s*([\d.]+)", vista) or [None, None])[1]})
        ref = {"tipo": tipo_ref, "folio": folio_ref, "fecha": fecha_ref,
               "codigo": CODIGO_REF_CORRIGE_MONTO, "razon": RAZON_REF_CORRIGE_MONTO}
        self._referer_emision = f"{BASE}/mipeDisplayPreView.cgi"
        return armar_payload(parsear_form(vista), TIPO_NOTA_CREDITO, receptor, items, fecha, forma_pago="",
                             referencias=[ref], respetar_html=("ES_BORR", "EFXP_FMA_PAGO", "INDICA_PRIMERA_EJECUCION"))

    # ------------------------------------------------------------- emisión (PAGA)
    def emitir(self, payload: PayloadMipyme, confirmar: bool = False,
               con_pdf: bool = True, aceptar_riesgo_folio: bool = False) -> ResultadoMipyme:
        """Emite en PRODUCCIÓN: GenXMLFirma (gasta el folio) → firma central → envío → PDF.
        Sin `confirmar=True` no hace nada: no hay sandbox del portal MIPYME (Ley L1).
        Si el tipo no está en `CADENAS_VERIFICADAS`, exige además `aceptar_riesgo_folio=True`:
        un fallo después de GenXMLFirma deja un folio gastado sin documento."""
        if not confirmar:
            raise ValidacionError("emitir() gasta un folio real en PRODUCCIÓN: "
                                  "llámalo con confirmar=True")
        if payload.tipo not in CADENAS_VERIFICADAS and not aceptar_riesgo_folio:
            raise ValidacionError(
                f"La cadena de emisión del tipo {payload.tipo} NO está verificada contra el SII desde "
                f"este repo (verificadas: {sorted(CADENAS_VERIFICADAS)}). Si falla después de "
                f"GenXMLFirma se pierde un folio. Para correrla igual: aceptar_riesgo_folio=True.",
                detalle={"tipo": payload.tipo, "verificadas": sorted(CADENAS_VERIFICADAS)})
        if payload.tipo not in CADENAS_VERIFICADAS:
            logger.warning("Portal MIPYME: emitiendo tipo %d con cadena NO verificada (riesgo de folio aceptado)", payload.tipo)
        self._requiere_empresa()
        if payload.campos_nuevos:
            logger.warning("Emitiendo con drift del form (campos nuevos: %s)", payload.campos_nuevos)
        logger.warning("Portal MIPYME → PRODUCCIÓN: emitiendo tipo %d por $%d para la empresa %s",
                       payload.tipo, payload.monto_total, self.empresa)

        # 3. GenXMLFirma — desde aquí el folio ya está gastado
        r = self._req("POST", f"{BASE}/mipeGenXMLFirma.cgi", content=_body(payload.pares),
                      navegacion=False,
                      referer=self._referer_emision or f"{BASE}/mipeGenFacEx.cgi?IGUAL=ULT_FAC&PTDC_CODIGO={payload.tipo}")
        html = self._texto(r)
        if _esta_rechazado(r):
            raise SIIRechazoError("mipeGenXMLFirma rechazado (¿payload incompleto?)",
                                  detalle={"http": r.status_code, "inicio": html[:300]})
        form_firma = extraer_form_firma(html)   # lo que el navegador mandaría a mipeSendXML
        # La página de firma trae el JS que arma el envío real: se guarda SIEMPRE (es el artefacto
        # que faltó para diagnosticar las NC 57 y 58 perdidas el 2026-10-02).
        self._evidencia_extra = {"respuesta_genxmlfirma.html": html}
        # El XML a firmar: el navegador lee `txtPlainText` del frmSign (entidades ya decodificadas
        # por el parser); la regex sobre la página es el respaldo.
        xml = (form_firma or {}).get("campos", {}).get("txtPlainText") or extraer_xml_dte(html)
        if not xml or "<DTE" not in xml:
            # Antes del folio, pero igual hay que poder diagnosticarlo: respuesta + payload.
            _guardar_evidencia(self.empresa, payload.tipo, 0, "genxml", html, None, payload.pares,
                               (self._clave_cert, self._clave_trib))
            raise SIIError("mipeGenXMLFirma no devolvió el XML del DTE",
                           detalle={"inicio": html[:300],
                                    "error_sii": (re.search(r"CODIGO:\s*([\d.]+)", html) or [None, None])[1]})
        folio = _folio_de(xml)
        logger.info("Portal MIPYME: folio %d asignado (tipo %d); frmSign %s", folio, payload.tipo,
                    "encontrado" if form_firma else "NO encontrado (reconstrucción)")

        # 4. Firma central — clave del CERTIFICADO. La cookie la pone el JS del portal.
        self._s.cookies.set("usa_firma_central", "true", domain="www1.sii.cl")
        n, dv = separar_rut(self.rut_usuario)
        # El JS del portal (pluginsii-1.2.js::signXmlCompatible) NO firma txtPlainText tal cual:
        # escapa `&` y antepone a <Documento un comentario con SO/navegador/"central". El tool
        # externo emitió la factura 59 sin esto; para notas se replica al navegador.
        dato = preparar_dato_firma(xml) if payload.tipo in (TIPO_NOTA_CREDITO, TIPO_NOTA_DEBITO) else xml
        r, cruda = self._firma_central(dato, "dte:DTE", "dte:Documento", referer=f"{BASE}/mipeGenXMLFirma.cgi")
        self._evidencia_extra["respuesta_postfirma_cruda.txt"] = cruda
        estado_firma, firmado = parsear_respuesta_firma(cruda)
        if estado_firma == 2 or re.search(r"clave no corresponde", cruda, re.I):
            _guardar_evidencia(self.empresa, payload.tipo, folio, "firma", cruda, None, payload.pares,
                               (self._clave_cert, self._clave_trib), extras=getattr(self, "_evidencia_extra", None))
            raise AutenticacionError(
                f"La firma central rechazó la clave del certificado (folio {folio} gastado sin documento). "
                f"Es la clave del .p12, no la tributaria.", detalle={"folio": folio})
        if r.status_code != 200 or estado_firma != 0 or "<Signature" not in firmado:
            # Sin fragmento de la respuesta en el error: el CGI recibió la clave del certificado y no
            # sabemos si la refleja (L6). La evidencia va a disco con los secretos tachados.
            carpeta = _guardar_evidencia(self.empresa, payload.tipo, folio, "firma", firmado, None,
                                         payload.pares, (self._clave_cert, self._clave_trib),
                                         extras=getattr(self, "_evidencia_extra", None))
            raise SIIRechazoError(
                f"La firma central falló para el folio {folio} (¿clave del certificado "
                f"incorrecta? no es la tributaria). El folio quedó gastado sin documento.",
                detalle={"folio": folio, "http": r.status_code, "largo_respuesta": len(firmado),
                         "evidencia": carpeta})
        m = re.search(r"<TmstFirma>([^<]+)</TmstFirma>", firmado)
        tmst = m.group(1) if m else ""

        # 5. Envío, para TODOS los tipos, SOLO con el frmSign real que devolvió la página de firma:
        # es lo único que el navegador manda (captura completa de la factura 62, 2026-10-08). La
        # reconstrucción por schema que se usaba para facturas fue el envío rechazado del folio 61
        # y se eliminó. Sin frmSign no hay camino alternativo: evidencia y abortar.
        if not (form_firma and form_firma["campos"].get("txtPlainText")):
            carpeta = _guardar_evidencia(self.empresa, payload.tipo, folio, "sin_frmsign", html, firmado,
                                         payload.pares, (self._clave_cert, self._clave_trib),
                                         extras=getattr(self, "_evidencia_extra", None))
            raise SIIError(f"mipeGenXMLFirma no devolvió el formulario de firma (frmSign); "
                           f"folio {folio} firmado y NO enviado. Evidencia guardada.",
                           detalle={"folio": folio, "evidencia": carpeta})
        pares = armar_send_xml_desde_form(form_firma, firmado, self._clave_cert, tmst)
        from html import unescape as _unescape
        from urllib.parse import urljoin
        url_envio = urljoin(f"{BASE}/mipeGenXMLFirma.cgi", _unescape(form_firma["action"] or "mipeSendXML.cgi"))
        logger.info("Portal MIPYME: envío con frmSign (%d campos) → %s", len(pares), url_envio)
        r = self._req("POST", url_envio, content=_body(pares),
                      referer=f"{BASE}/mipeGenXMLFirma.cgi", origin="https://www1.sii.cl")
        out = self._texto(r)
        # Un 302 también puede ser éxito: el DHDR puede venir en Location.
        destino = r.headers.get("location", "")
        m = re.search(r"DHDR_CODIGO=(\d+)", out) or re.search(r"DHDR_CODIGO=(\d+)", destino)
        exito = bool(m) or bool(re.search(r"Firmado y Enviado|ENVIADO EXITOSAMENTE|Documento\s+enviado", out, re.I))
        if re.search(r"Firma\s+no\s+reconocida", out, re.I):
            exito = False
        rechazado = ("Request Rejected" in out) or (r.status_code not in (200, 302))
        if rechazado or not exito:
            # El body llevó la clave del certificado: no reflejar la respuesta en el error (L6).
            # La evidencia (respuesta, XML FIRMADO y body con la clave tachada) va a disco.
            carpeta = _guardar_evidencia(self.empresa, payload.tipo, folio, "envio", out, firmado,
                                         pares, (self._clave_cert, self._clave_trib),
                                         extras=getattr(self, "_evidencia_extra", None))
            raise SIIRechazoError(
                f"mipeSendXML no confirmó el envío del folio {folio} "
                f"({'WAF: Request Rejected' if 'Request Rejected' in out else 'respuesta inesperada'}). "
                f"El folio ya está gastado y firmado: revisar en el portal si el documento quedó "
                f"emitido antes de reintentar.",
                detalle={"folio": folio, "http": r.status_code, "largo_respuesta": len(out),
                         "evidencia": carpeta})
        dhdr = m.group(1) if m else ""
        _guardar_evidencia(self.empresa, payload.tipo, folio, "ok", out, firmado, pares,
                           (self._clave_cert, self._clave_trib), extras=getattr(self, "_evidencia_extra", None))
        res = ResultadoMipyme(tipo=payload.tipo, folio=folio, dhdr=dhdr, xml_firmado=firmado,
                              monto_total=payload.monto_total)
        logger.info("Portal MIPYME: tipo %d folio %d emitido, DHDR=%s", payload.tipo, folio, dhdr)
        if con_pdf and dhdr:
            try:
                res.pdf = self.descargar_pdf(dhdr)
            except SIIError as e:  # el documento ya está emitido: el PDF no debe tumbar el resultado
                logger.warning("PDF del DHDR %s no disponible: %s", dhdr, e)
        return res

    def listar_emitidos(self, tipo: str = "", desde: str = "", hasta: str = "",
                        pag_max: int = 20, pausa: float = 0.7) -> List[DocumentoEmitido]:
        """Historial de documentos emitidos por el gratuito (todas las páginas). Solo lectura."""
        import time
        self._requiere_empresa()
        todos: dict = {}
        ultima = 1
        for pag in range(1, pag_max + 1):
            r = self._req("GET", f"{BASE}/mipeAdminDocsEmi.cgi", referer=f"{BASE}/mipeSelEmpresa.cgi",
                          params={"RUT_RECP": "", "FOLIO": "", "RZN_SOC": "", "FEC_DESDE": desde,
                                  "FEC_HASTA": hasta, "TPO_DOC": tipo, "ESTADO": "", "ORDEN": "",
                                  "NUM_PAG": str(pag)})
            if _esta_rechazado(r):
                raise SIIRechazoError("El portal no entregó el historial de emitidos",
                                      detalle={"http": r.status_code, "pagina": pag})
            docs, ultima = parsear_listado(self._texto(r))
            nuevos = [d for d in docs if d.dhdr not in todos]
            for d in nuevos:
                todos[d.dhdr] = d
            if pag >= max(ultima, 1) or not nuevos:
                break
            time.sleep(pausa)
        else:
            logger.warning("MIPYME: historial truncado en %d páginas de %d", pag_max, ultima)
        return list(todos.values())

    def _firma_central(self, dato: str, nodo: str, nodo_id: str, referer: str):
        """`postFirmaDigital` exactamente como el XHR del navegador (capturado 2026-10-04):
        form UTF-8 con charset declarado, X-Requested-With, Accept text/plain; responde el XML
        firmado en texto plano ISO-8859-1. Devuelve (respuesta, texto)."""
        n, dv = separar_rut(self.rut_usuario)
        pares = [("nombre", "1"), ("dato", dato), ("rut", n), ("dv", dv), ("clave", self._clave_cert),
                 ("nodo", nodo), ("nodoId", nodo_id), ("nameSpace", "http://www.sii.cl/SiiDte")]
        cuerpo = urlencode(pares, doseq=True, encoding="utf-8").encode("ascii")
        r = self._req("POST", f"{BASE}/postFirmaDigital.cgi", navegacion=False, content=cuerpo,
                      referer=referer, origin="https://www1.sii.cl",
                      extra={"X-Requested-With": "XMLHttpRequest", "Accept": "text/plain, */*; q=0.01",
                             "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"})
        return r, self._texto(r)

    def obtener_envio_xml(self, dhdr: str, tipo: int, rut_receptor: str) -> bytes:
        """El XML (EnvioDTE con el DTE firmado adentro) de un documento emitido: botón "Obtener
        Envío" del portal. Flujo capturado 2026-10-04: GET `mipeGenDLNewEnvio.cgi` → textarea
        `txtPlainText` → firma central del EnvioDTE (nodo dte:EnvioDTE / dte:SetDTE, con la clave
        del certificado) → POST `mipeDownLoadNewEnvio.cgi` txtSignText → adjunto .xml.
        NO gasta folio. Necesita `clave_certificado`."""
        self._requiere_empresa()
        if not self._clave_cert:
            raise ValidacionError("Obtener el XML exige la clave del certificado (firma central)")
        rn, rdv = separar_rut(rut_receptor)
        r = self._req("GET", f"{BASE}/mipeGenDLNewEnvio.cgi", referer=f"{BASE}/mipeGesDocEmi.cgi?ALL_PAGE_ANT=2&CODIGO={dhdr}",
                      params={"CODIGO": dhdr, "TPO_DOC": str(tipo), "RUT_RCP": rn, "DV_RCP": rdv})
        html = self._texto(r)
        m = re.search(r'<textarea\b[^>]*id=["\']txtPlainText["\'][^>]*>([\s\S]*?)</textarea>', html, re.I)
        if _esta_rechazado(r) or not m:
            raise SIIRechazoError("El portal no entregó el envío para firmar", detalle={"dhdr": dhdr, "http": r.status_code})
        from html import unescape
        plano = unescape(m.group(1))
        r, firmado = self._firma_central(preparar_dato_firma(plano), "dte:EnvioDTE", "dte:SetDTE",
                                         referer=f"{BASE}/mipeGenDLNewEnvio.cgi")
        estado_firma, firmado = parsear_respuesta_firma(firmado)
        if estado_firma != 0 or "<Signature" not in firmado:
            raise SIIRechazoError("La firma central del envío falló (¿clave del certificado?)", detalle={"dhdr": dhdr, "estado": estado_firma})
        r = self._req("POST", f"{BASE}/mipeDownLoadNewEnvio.cgi", referer=f"{BASE}/mipeGenDLNewEnvio.cgi",
                      origin="https://www1.sii.cl", content=_body([("txtSignText", firmado)]))
        if r.status_code != 200 or b"<EnvioDTE" not in r.content:
            raise SIIRechazoError("El portal no devolvió el XML del envío", detalle={"dhdr": dhdr, "http": r.status_code})
        return r.content

    def descargar_pdf(self, dhdr: str) -> bytes:
        r = self._req("GET", f"{BASE}/mipeDisplayPDF.cgi", params={"DHDR_CODIGO": dhdr},
                      referer=f"{BASE}/mipeGesDocEmi.cgi?ALL_PAGE_ANT=2&CODIGO={dhdr}")
        if r.content[:4] != b"%PDF":
            raise SIIError("El portal no devolvió un PDF", detalle={"dhdr": dhdr, "http": r.status_code})
        return r.content

    def _requiere_empresa(self) -> None:
        if not self.empresa:
            raise ValidacionError("Primero iniciar_sesion() y seleccionar_empresa()")


# ---------------------------------------------------------------------------
# Persistencia (opcional): que el buscador vea también lo emitido por este canal
# ---------------------------------------------------------------------------
def registrar_en_bd(res: ResultadoMipyme, rut_emisor: str, receptor: ReceptorMipyme,
                    fecha_emision: str) -> int:
    """Guarda el documento en `dtes` con ambiente `produccion` y estado `emitido_mipyme`.
    El `track_id` NO existe en este canal: guardamos el DHDR_CODIGO ahí (es el id que el
    portal usa para el PDF y para la NC)."""
    from core.models import actualizar_estado_dte, crear_dte, obtener_dte_por_folio
    # RUT canónico del motor (sin puntos, con guión): así lo filtra `listar_dtes` y así aplica
    # el UNIQUE(rut_emisor, tipo_dte, folio). Idempotente: si ya existe, solo actualiza estado.
    existente = obtener_dte_por_folio(formatear_rut(rut_emisor, con_puntos=False), res.tipo, res.folio)
    if existente:
        actualizar_estado_dte(existente["id"], "emitido_mipyme", track_id=int(res.dhdr) if res.dhdr.isdigit() else None)
        return existente["id"]
    dte_id = crear_dte(tipo_dte=res.tipo, folio=res.folio,
                       rut_emisor=formatear_rut(rut_emisor, con_puntos=False),
                       rut_receptor=formatear_rut(receptor.rut, con_puntos=False),
                       razon_social_receptor=receptor.razon_social,
                       fecha_emision=fecha_emision, monto_neto=0, monto_exento=res.monto_total,
                       iva=0, monto_total=res.monto_total, xml_firmado=res.xml_firmado,
                       ambiente="produccion")
    actualizar_estado_dte(dte_id, "emitido_mipyme", track_id=int(res.dhdr) if res.dhdr.isdigit() else None)
    return dte_id


def importar_emitidos(cli: ClienteMipyme, rut_emisor: str, docs: Sequence[DocumentoEmitido],
                      con_pdf: bool = True, carpeta: str = "storage/pdfs/mipyme",
                      pausa: float = 0.5, con_xml: bool = False) -> dict:
    """Mete el historial del gratuito en la tabla `dtes` (canal MIPYME) y baja los PDF.
    Idempotente: lo que ya existe por (rut, tipo, folio) se salta. Devuelve el resumen."""
    import time
    from pathlib import Path
    from core.models import actualizar_estado_dte, crear_dte, guardar_pdf_dte, obtener_dte_por_folio
    rut = formatear_rut(rut_emisor, con_puntos=False)
    destino = Path(carpeta) / rut.replace("-", "")
    resumen = {"listados": len(docs), "importados": 0, "ya_existian": 0, "sin_tipo": 0,
               "pdf_ok": 0, "pdf_falta": 0, "xml_ok": 0, "xml_falta": 0, "documentos": []}
    for d in docs:
        tipo = d.tipo
        if tipo is None:
            resumen["sin_tipo"] += 1
            logger.warning("MIPYME: tipo no reconocido en el historial: %r (folio %s)", d.tipo_texto, d.folio)
            continue
        fila = obtener_dte_por_folio(rut, tipo, d.folio)
        if fila:
            resumen["ya_existian"] += 1
            dte_id = fila["id"]
        else:
            dte_id = crear_dte(tipo_dte=tipo, folio=d.folio, rut_emisor=rut,
                               rut_receptor=formatear_rut(d.receptor_rut, con_puntos=False) if validar_rut(d.receptor_rut) else d.receptor_rut,
                               razon_social_receptor=d.receptor_razon, fecha_emision=d.fecha,
                               monto_neto=0, monto_exento=d.monto if tipo in (34, 61) else 0, iva=0,
                               monto_total=d.monto, xml_firmado=None, ambiente="produccion")
            actualizar_estado_dte(dte_id, "emitido_mipyme", track_id=int(d.dhdr) if d.dhdr.isdigit() else None)
            resumen["importados"] += 1
        if con_pdf and not (fila and fila.get("pdf_path") and Path(fila["pdf_path"]).exists()):
            try:
                pdf = cli.descargar_pdf(d.dhdr)
                destino.mkdir(parents=True, exist_ok=True)
                fecha_segura = re.sub(r"[^0-9-]", "_", d.fecha)
                ruta = destino / f"T{tipo}_F{d.folio}_{fecha_segura}_D{d.dhdr}.pdf"
                ruta.write_bytes(pdf)
                guardar_pdf_dte(dte_id, str(ruta))
                resumen["pdf_ok"] += 1
                time.sleep(pausa)
            except SIIError as e:
                resumen["pdf_falta"] += 1
                logger.warning("MIPYME: sin PDF para DHDR %s: %s", d.dhdr, e)
        if con_xml and not (fila and fila.get("xml_firmado")):
            try:
                xml = cli.obtener_envio_xml(d.dhdr, tipo, d.receptor_rut).decode("iso-8859-1", "replace")
                from core.database import ejecutar
                ejecutar("UPDATE dtes SET xml_firmado=?, actualizado_en=datetime('now') WHERE id=?", (xml, dte_id))
                carpeta_xml = Path("storage") / "dtes" / "mipyme" / rut.replace("-", "")
                carpeta_xml.mkdir(parents=True, exist_ok=True)
                (carpeta_xml / f"T{tipo}_F{d.folio}_D{d.dhdr}.xml").write_bytes(xml.encode("iso-8859-1", "replace"))
                resumen["xml_ok"] += 1
                time.sleep(pausa)
            except (SIIError, ValidacionError) as e:
                resumen["xml_falta"] += 1
                logger.warning("MIPYME: sin XML para DHDR %s: %s", d.dhdr, e)
        resumen["documentos"].append({"tipo": tipo, "folio": d.folio, "fecha": d.fecha, "monto": d.monto,
                                      "estado": d.estado, "dhdr": d.dhdr, "receptor": d.receptor_razon})
    return resumen
