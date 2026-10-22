"""
core/rcv.py

Registro de Compras y Ventas (RCV) del SII — ingesta y normalización.

Módulo NUEVO y puramente ADITIVO: no modifica el pipeline de emisión/firma/
certificación. Se apoya en la autenticación que ya existe (`core/sii.py::ClienteSII`)
y agrega su propia tabla `rcv_documentos` sin tocar `dtes`/`cafs`.

Propósito
---------
El RCV es lo que el SII ya sabe de la empresa: el registro de **ventas** (lo que
la empresa emitió = débito fiscal) y de **compras** (lo que terceros le emitieron
= crédito fiscal). Es la fuente de datos que permite armar el **F29** (IVA mensual)
y alimentar el **F22** (renta anual). Las ventas propias también viven en la tabla
`dtes`, pero el RCV es la única fuente para las COMPRAS.

Cómo consume el SII
-------------------
El RCV NO es un web service SOAP como los de DTE: es la API JSON "facade" que usa
el portal (``www4.sii.cl/consdcvinternetui``). La autenticación es la misma
(semilla → token), y el token se envía como cookie ``TOKEN=<token>``. Por eso el
cliente de aquí recibe un ``ClienteSII`` ya construido y reutiliza su
``obtener_token()``.

    Los endpoints (``getDetalleCompraExport``/``getDetalleVentaExport``) y las
    llaves del payload (``operacion``, ``ptributario``, ``codTipoDoc``,
    ``estadoContab``) están verificados contra una implementación .NET en
    producción y aislados en las constantes ``RCV_*`` de abajo. Falta únicamente
    el smoke-test en vivo con credenciales reales (el RCV solo tiene datos en
    PRODUCCIÓN/Palena; Maullín no sirve). Por eso el módulo también ingiere un
    JSON ya descargado (``cargar_desde_json``): toda la lógica de normalización +
    BD + agregación F29 funciona sin depender de la bajada en vivo.

El JSON va y viene en UTF-8 (esta API, a diferencia del XML DTE, no es ISO-8859-1).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import httpx

from core import sala_espera
from core.database import get_db
from core.rut import separar_rut
from core.sii import ClienteSII

# ---------------------------------------------------------------------------
# Wire-format del facade RCV (AISLADO — confirmar/ajustar contra el SII vivo)
# ---------------------------------------------------------------------------
RCV_BASE = "https://www4.sii.cl/consdcvinternetui/services/data/facadeService/"
# Endpoints del facade RCV. Los de DETALLE usan el sufijo "Export" y entregan el
# documento a documento; getResumen entrega los totales del período. Verificado
# contra una implementación .NET en producción (lenguajedemaquinas.blogspot.com).
RCV_METODOS = {
    "COMPRA": "getDetalleCompraExport",
    "VENTA": "getDetalleVentaExport",
}
RCV_ENDPOINTS = {op: RCV_BASE + metodo for op, metodo in RCV_METODOS.items()}
RCV_ENDPOINTS["resumen"] = RCV_BASE + "getResumen"

# Contrato REAL capturado del navegador (extensión Captura SII, 2026-10-04, la empresa del gratuito):
#   - el detalle "Export" exige `accionRecaptcha` + `tokenRecaptcha`; el cliente web manda el
#     placeholder "t-o-k-e-n-web" (no hay reCAPTCHA real) y `estadoContab` VACÍO;
#   - `metaData.conversationId` = cookie CSESSIONID/TOKEN de la sesión; `transactionId` = uuid;
#   - la respuesta del Export es `{"data": ["cabecera;con;columnas", "fila;..."]}` (CSV con ';'),
#     NO una lista de dicts. getResumen sí devuelve dicts (rsmn*).
RCV_TOKEN_RECAPTCHA = "t-o-k-e-n-web"
RCV_ACCION_RECAPTCHA = {"VENTA": "RCV_DDETV", "COMPRA": "RCV_DDETC"}   # ambas verificadas en vivo 2026-10-04
# estadoContab del Export: VENTA lo manda vacío; COMPRA exige "REGISTRO" (vacío → codError cdvc17.05.03).
RCV_ESTADO_EXPORT = {"VENTA": "", "COMPRA": "REGISTRO"}

# Columnas del CSV del Export (ventas, verificado) → campos del DocumentoRCV.
_COLUMNAS_CSV = {
    "tipo doc": "tipo_dte", "folio": "folio", "fecha docto": "fecha_doc", "rut cliente": "rut_contraparte",
    "rut proveedor": "rut_contraparte", "razon social": "razon_social", "monto exento": "monto_exento",
    "monto neto": "monto_neto", "monto iva": "monto_iva", "monto iva recuperable": "monto_iva",
    "monto iva no recuperable": "monto_iva_no_rec", "monto iva no rec": "monto_iva_no_rec", "monto iva no rec.": "monto_iva_no_rec",
    "monto iva uso comun": "monto_iva_uso_comun", "monto total": "monto_total",
}


def _rcv_namespace(metodo: str) -> str:
    """El namespace del metaData debe coincidir con el método invocado."""
    return f"cl.sii.sdi.lob.diii.consdcv.data.api.interfaces.FacadeService/{metodo}"


# El servlet exige User-Agent tipo navegador, igual que DTEUpload (ver core/sii.py).
from core.config import settings
RCV_USER_AGENT = settings.sii_user_agent

# Tipos de operación del RCV.
COMPRA = "COMPRA"
VENTA = "VENTA"

# Nota de crédito (61) resta base/IVA en cualquiera de los dos lados del RCV.
TIPOS_NOTA_CREDITO = {61}


def signo_documento(tipo_dte: int) -> int:
    """+1 para documentos que suman (facturas, ND); -1 para notas de crédito."""
    return -1 if int(tipo_dte) in TIPOS_NOTA_CREDITO else 1


# ---------------------------------------------------------------------------
# Modelo normalizado
# ---------------------------------------------------------------------------
@dataclass
class DocumentoRCV:
    """Un documento del RCV, ya normalizado (una fila de `rcv_documentos`)."""

    rut_empresa: str            # dueño del registro (el contribuyente), formato NNNNNNNN-D
    tipo_operacion: str         # COMPRA | VENTA
    periodo: str                # YYYYMM
    tipo_dte: int
    folio: int
    fecha_doc: Optional[str] = None      # ISO YYYY-MM-DD
    rut_contraparte: str = ""            # proveedor (compra) o cliente (venta)
    razon_social: str = ""
    monto_exento: int = 0
    monto_neto: int = 0
    monto_iva: int = 0
    monto_iva_no_rec: int = 0            # IVA no recuperable (compras)
    monto_iva_uso_comun: int = 0         # IVA de uso común (compras)
    monto_total: int = 0
    estado_sii: str = "REGISTRO"         # REGISTRO | PENDIENTE | NO_INCLUIR | RECLAMADO
    clasificacion: Optional[str] = None  # reservado para la capa IA (recuperable/activo_fijo/…)
    origen: str = "rcv"                  # rcv | csv | manual


# ---------------------------------------------------------------------------
# Persistencia (tabla propia, aditiva)
# ---------------------------------------------------------------------------
_SCHEMA_RCV = """
CREATE TABLE IF NOT EXISTS rcv_documentos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rut_empresa TEXT NOT NULL,
    tipo_operacion TEXT NOT NULL,
    periodo TEXT NOT NULL,
    tipo_dte INTEGER NOT NULL,
    folio INTEGER NOT NULL,
    fecha_doc TEXT,
    rut_contraparte TEXT DEFAULT '',
    razon_social TEXT DEFAULT '',
    monto_exento INTEGER DEFAULT 0,
    monto_neto INTEGER DEFAULT 0,
    monto_iva INTEGER DEFAULT 0,
    monto_iva_no_rec INTEGER DEFAULT 0,
    monto_iva_uso_comun INTEGER DEFAULT 0,
    monto_total INTEGER DEFAULT 0,
    estado_sii TEXT DEFAULT 'REGISTRO',
    clasificacion TEXT,
    origen TEXT DEFAULT 'rcv',
    creado_en TEXT NOT NULL,
    actualizado_en TEXT NOT NULL,
    UNIQUE(rut_empresa, tipo_operacion, tipo_dte, folio, rut_contraparte)
);
CREATE INDEX IF NOT EXISTS idx_rcv_empresa_periodo ON rcv_documentos(rut_empresa, periodo);
CREATE INDEX IF NOT EXISTS idx_rcv_operacion ON rcv_documentos(tipo_operacion);
"""


def init_rcv_db() -> None:
    """Crea la tabla `rcv_documentos` si no existe. Idempotente y aditivo."""
    with get_db() as conn:
        conn.executescript(_SCHEMA_RCV)


def guardar_documentos(docs: list[DocumentoRCV]) -> int:
    """
    Inserta/actualiza (upsert) documentos del RCV. Idempotente: re-bajar un
    período actualiza las filas en vez de duplicarlas (clave UNIQUE).

    Returns:
        Cantidad de documentos escritos.
    """
    if not docs:
        return 0
    init_rcv_db()
    ahora = datetime.now().isoformat(timespec="seconds")
    filas = [
        (
            d.rut_empresa, d.tipo_operacion, d.periodo, int(d.tipo_dte), int(d.folio),
            d.fecha_doc, d.rut_contraparte, d.razon_social,
            int(d.monto_exento), int(d.monto_neto), int(d.monto_iva),
            int(d.monto_iva_no_rec), int(d.monto_iva_uso_comun), int(d.monto_total),
            d.estado_sii, d.clasificacion, d.origen, ahora, ahora,
        )
        for d in docs
    ]
    sql = """
    INSERT INTO rcv_documentos (
        rut_empresa, tipo_operacion, periodo, tipo_dte, folio,
        fecha_doc, rut_contraparte, razon_social,
        monto_exento, monto_neto, monto_iva,
        monto_iva_no_rec, monto_iva_uso_comun, monto_total,
        estado_sii, clasificacion, origen, creado_en, actualizado_en
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT(rut_empresa, tipo_operacion, tipo_dte, folio, rut_contraparte)
    DO UPDATE SET
        periodo=excluded.periodo,
        fecha_doc=excluded.fecha_doc,
        razon_social=excluded.razon_social,
        monto_exento=excluded.monto_exento,
        monto_neto=excluded.monto_neto,
        monto_iva=excluded.monto_iva,
        monto_iva_no_rec=excluded.monto_iva_no_rec,
        monto_iva_uso_comun=excluded.monto_iva_uso_comun,
        monto_total=excluded.monto_total,
        estado_sii=excluded.estado_sii,
        origen=excluded.origen,
        actualizado_en=excluded.actualizado_en
    """
    with get_db() as conn:
        conn.executemany(sql, filas)
    return len(filas)


# ---------------------------------------------------------------------------
# Normalización del JSON facade → DocumentoRCV
# ---------------------------------------------------------------------------
def _num(valor: Any) -> int:
    """Convierte un monto del RCV a int, tolerando None, '', floats y strings."""
    if valor is None or valor == "":
        return 0
    if isinstance(valor, (int, float)):
        return int(round(valor))
    s = str(valor).strip().replace(".", "").replace(",", "")
    try:
        return int(s)
    except ValueError:
        return 0


def _fecha_iso(valor: Any) -> Optional[str]:
    """Normaliza fechas del RCV (dd/mm/yyyy o yyyy-mm-dd) a ISO YYYY-MM-DD."""
    if not valor:
        return None
    s = str(valor).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d-%m-%Y"):
        try:
            return datetime.strptime(s[: len(fmt) + 4], fmt).date().isoformat()
        except ValueError:
            continue
    return s[:10] if len(s) >= 10 else None


def _get(fila: dict, *claves: str, default: Any = None) -> Any:
    """Primer valor no-None entre varias posibles llaves (el facade varía)."""
    for c in claves:
        if c in fila and fila[c] is not None:
            return fila[c]
    return default


def normalizar_detalle(
    rut_empresa: str,
    tipo_operacion: str,
    periodo: str,
    respuesta_json: dict,
    origen: str = "rcv",
) -> list[DocumentoRCV]:
    """
    Convierte la respuesta de detalle del facade RCV a una lista de DocumentoRCV.

    Tolerante a variaciones de forma: acepta las filas bajo ``data``, ``detalle``
    o directamente una lista, y prueba varias llaves por campo (con prefijo
    ``det`` o sin él), porque el facade no es estable entre endpoints.
    """
    rut_empresa = _normalizar_rut(rut_empresa)
    periodo = _normalizar_periodo(periodo)

    filas = respuesta_json
    if isinstance(respuesta_json, dict):
        filas = _get(respuesta_json, "data", "detalle", "detalles", default=[])
    if not isinstance(filas, list):
        return []
    # Forma real del Export (2026-10-04): lista de strings CSV con ';', la primera es la cabecera.
    if filas and all(isinstance(x, str) for x in filas):
        filas = _csv_a_dicts(filas)

    docs: list[DocumentoRCV] = []
    for f in filas:
        if not isinstance(f, dict):
            continue
        rut_c = _get(f, "detRutDoc", "rutDoc", "rut", default="")
        dv_c = _get(f, "detDvDoc", "dvDoc", "dv", default="")
        rut_contraparte = f"{rut_c}-{dv_c}" if rut_c and dv_c else str(rut_c or "")

        docs.append(
            DocumentoRCV(
                rut_empresa=rut_empresa,
                tipo_operacion=tipo_operacion,
                periodo=periodo,
                tipo_dte=int(_get(f, "detTipoDoc", "tipoDoc", "tipoDte", default=0) or 0),
                folio=int(_get(f, "detNroDoc", "nroDoc", "folio", default=0) or 0),
                fecha_doc=_fecha_iso(_get(f, "detFchDoc", "fchDoc", "fecha")),
                rut_contraparte=rut_contraparte,
                razon_social=str(_get(f, "detRznSoc", "rznSoc", "razonSocial", default="") or ""),
                monto_exento=_num(_get(f, "detMntExe", "mntExe", "montoExento")),
                monto_neto=_num(_get(f, "detMntNeto", "mntNeto", "montoNeto")),
                monto_iva=_num(_get(f, "detMntIVA", "mntIVA", "montoIva")),
                monto_iva_no_rec=_num(_get(f, "detMntIVANoRec", "detIVANoRec", "mntIVANoRec")),
                monto_iva_uso_comun=_num(_get(f, "detMntIVAUsoComun", "mntIVAUsoComun")),
                monto_total=_num(_get(f, "detMntTotal", "mntTotal", "montoTotal")),
                estado_sii=str(_get(f, "detEventoReceptor", "estado", default="REGISTRO") or "REGISTRO"),
                origen=origen,
            )
        )
    return docs


def _csv_a_dicts(lineas: list[str]) -> list[dict]:
    """Convierte las líneas CSV del Export a dicts con las llaves que entiende `normalizar_detalle`."""
    if not lineas:
        return []
    cab = [c.strip().lower() for c in lineas[0].split(";")]
    out = []
    for ln in lineas[1:]:
        if not ln.strip():
            continue
        vals = ln.split(";")
        fila = {}
        for i, col in enumerate(cab):
            campo = _COLUMNAS_CSV.get(col) or next((v for k, v in _COLUMNAS_CSV.items() if col.startswith(k) and len(k) > 9), None)
            # La primera columna que calza gana: "Monto Neto Activo Fijo" (vacía) no debe pisar "Monto Neto".
            if campo and i < len(vals) and campo not in fila:
                fila[campo] = vals[i].strip()
        if not fila.get("folio"):
            continue
        rut = fila.get("rut_contraparte", "")
        n, _, dv = rut.partition("-")
        out.append({"tipoDoc": fila.get("tipo_dte"), "folio": fila.get("folio"), "fecha": fila.get("fecha_doc"),
                    "rut": n, "dv": dv, "rznSoc": fila.get("razon_social", ""), "mntExe": fila.get("monto_exento"),
                    "mntNeto": fila.get("monto_neto"), "mntIVA": fila.get("monto_iva"), "mntIVANoRec": fila.get("monto_iva_no_rec"),
                    "mntIVAUsoComun": fila.get("monto_iva_uso_comun"), "mntTotal": fila.get("monto_total")})
    return out


def _normalizar_periodo(periodo: str) -> str:
    """Acepta 'YYYY-MM', 'YYYYMM' o date/datetime y devuelve 'YYYYMM'."""
    if isinstance(periodo, (date, datetime)):
        return periodo.strftime("%Y%m")
    s = str(periodo).replace("-", "").replace("/", "").strip()
    return s[:6]


def _normalizar_rut(rut: str) -> str:
    """Devuelve el RUT en formato NNNNNNNN-D (sin puntos)."""
    num, dv = separar_rut(rut)
    return f"{num}-{dv}" if num and dv else str(rut)


# ---------------------------------------------------------------------------
# Cliente de bajada (en vivo) e ingesta (desde archivo)
# ---------------------------------------------------------------------------
class RegistroCompraVenta:
    """
    Cliente del RCV. Reutiliza la autenticación de ``ClienteSII`` (semilla→token).

    El ``ClienteSII`` debe apuntar a PRODUCCIÓN, porque el RCV real solo existe en
    Palena. Uso típico::

        from core.crypto import CertificadoDigital
        from core.sii import ClienteSII, AmbienteSII
        from core.rcv import RegistroCompraVenta

        cert = CertificadoDigital.desde_archivo(ruta, password)
        cliente = ClienteSII(cert, ambiente=AmbienteSII.PRODUCCION)
        rcv = RegistroCompraVenta(cliente)
        docs = rcv.descargar_periodo("76111111-6", "202406", COMPRA)
    """

    def __init__(self, cliente_sii: Optional[ClienteSII] = None,
                 token: Optional[str] = None, timeout: float = 60.0,
                 cookies: Optional[dict] = None):
        """Se puede construir con un `ClienteSII` (obtiene el token por semilla→token)
        o directamente con un `token` ya obtenido — p.ej. el de la sesión por
        certificado de `PortalSII` (cookie TOKEN), para unificar la auth con la
        plataforma. El RCV real sólo existe en PRODUCCIÓN (Palena)."""
        self.cliente = cliente_sii
        self._token = token
        self.timeout = timeout
        # Sesión por clave tributaria (la del navegador / `ClienteMipyme`): cookies .sii.cl.
        # Es la forma VERIFICADA (2026-10-04); el token por semilla sigue como alternativa.
        self._cookies = dict(cookies) if cookies else None

    def _obtener_token(self) -> str:
        if self._cookies:
            return self._cookies.get("TOKEN") or self._cookies.get("CSESSIONID") or ""
        if self._token:
            return self._token
        if self.cliente is None:
            raise ValueError("RegistroCompraVenta necesita un ClienteSII o un token.")
        return self.cliente.obtener_token()

    def _headers(self, token: str, num: str, dv: str) -> dict:
        return {
            "User-Agent": RCV_USER_AGENT,
            "Cookie": f"TOKEN={token};RUT_NS={num};DV_NS={dv}",
            "Content-Type": "application/json;charset=UTF-8",
            # `Accept: application/json` provoca HTTP 500 "RESTEASY001530: No match for
            # accept header" en el facade RCV. Debe ser `*/*` (verificado en vivo).
            "Accept": "*/*",
        }

    def _payload(self, rut_empresa: str, periodo: str, tipo_operacion: str,
                 metodo: Optional[str] = None) -> dict:
        """Body exacto del cliente web (capturado 2026-10-04)."""
        num, dv = separar_rut(rut_empresa)
        metodo = metodo or RCV_METODOS[tipo_operacion]
        conv = (self._cookies or {}).get("CSESSIONID") or (self._cookies or {}).get("TOKEN") or self._token or f"{num}-{dv}"
        data = {
            "rutEmisor": num,
            "dvEmisor": dv,
            "ptributario": _normalizar_periodo(periodo),
            "estadoContab": "REGISTRO",
            "operacion": tipo_operacion,  # COMPRA | VENTA
        }
        if metodo.endswith("Export"):
            data.update({"codTipoDoc": 0, "estadoContab": RCV_ESTADO_EXPORT[tipo_operacion],
                         "accionRecaptcha": RCV_ACCION_RECAPTCHA[tipo_operacion],
                         "tokenRecaptcha": RCV_TOKEN_RECAPTCHA})
        else:
            data["busquedaInicial"] = True
        return {
            "metaData": {"namespace": _rcv_namespace(metodo), "conversationId": conv,
                         "transactionId": str(uuid.uuid4()), "page": None},
            "data": data,
        }

    def descargar_periodo(
        self, rut_empresa: str, periodo: str, tipo_operacion: str
    ) -> list[DocumentoRCV]:
        """
        Baja el detalle del RCV de un período y lo devuelve normalizado.
        NO persiste (usa `guardar_documentos` para eso).

        Args:
            rut_empresa: RUT del contribuyente dueño del registro.
            periodo: 'YYYYMM' o 'YYYY-MM'.
            tipo_operacion: COMPRA o VENTA.
        """
        data = self._llamar(RCV_ENDPOINTS[tipo_operacion], self._payload(rut_empresa, periodo, tipo_operacion), rut_empresa)
        return normalizar_detalle(rut_empresa, tipo_operacion, periodo, data, origen="rcv")

    def _llamar(self, url: str, payload: dict, rut_empresa: str) -> dict:
        num, dv = separar_rut(rut_empresa)
        with httpx.Client(timeout=self.timeout, verify=True, event_hooks=sala_espera.HOOKS) as client:
            if self._cookies:
                headers = {"User-Agent": RCV_USER_AGENT, "Content-Type": "application/json",
                           "Accept": "application/json, text/plain, */*", "Origin": "https://www4.sii.cl",
                           "Referer": "https://www4.sii.cl/consdcvinternetui/"}
                resp = client.post(url, headers=headers, cookies=self._cookies, json=payload)
            else:
                resp = client.post(url, headers=self._headers(self._obtener_token(), num, dv), json=payload)
            resp.raise_for_status()
            data = resp.json()
        est = (data.get("respEstado") or {}) if isinstance(data, dict) else {}
        if est.get("codRespuesta") not in (None, 0):
            # codRespuesta 3 = período sin registro (visto en vivo en 202610 compras): no es error.
            return {"data": [], "respEstado": est}
        return data

    def descargar_resumen(self, rut_empresa: str, periodo: str, tipo_operacion: str) -> list[dict]:
        """Totales por tipo de documento del período (getResumen): `rsmnTipoDocInteger`,
        `rsmnTotDoc`, `rsmnMntExe/Neto/IVA/Total`. Verificado 2026-10-04."""
        data = self._llamar(RCV_ENDPOINTS["resumen"], self._payload(rut_empresa, periodo, tipo_operacion, metodo="getResumen"), rut_empresa)
        return list(data.get("data") or [])

    def sincronizar_periodo(
        self, rut_empresa: str, periodo: str
    ) -> dict[str, int]:
        """
        Baja COMPRAS y VENTAS del período y las persiste. Devuelve conteos.
        """
        compras = self.descargar_periodo(rut_empresa, periodo, COMPRA)
        ventas = self.descargar_periodo(rut_empresa, periodo, VENTA)
        n = guardar_documentos(compras + ventas)
        return {"compras": len(compras), "ventas": len(ventas), "guardados": n}


# ---------------------------------------------------------------------------
# Últimos movimientos: lo que el SII tiene registrado del negocio, directo del RCV
# ---------------------------------------------------------------------------
MAX_PERIODOS_MOVIMIENTOS = 3
MENSAJE_PERIODOS = ("periodos debe ser una lista de meses YYYYMM separados por coma "
                    f"(máximo {MAX_PERIODOS_MOVIMIENTOS}).")
CAMPOS_MOVIMIENTO = ("tipo_operacion", "periodo", "tipo_dte", "folio", "fecha_doc", "rut_contraparte",
                     "razon_social", "monto_exento", "monto_neto", "monto_iva", "monto_total", "estado_sii")


def periodos_por_defecto(hoy: Optional[date] = None) -> list[str]:
    """El mes en curso y el anterior (hora local), del más nuevo al más viejo."""
    hoy = hoy or date.today()
    anterior = (hoy.year - 1, 12) if hoy.month == 1 else (hoy.year, hoy.month - 1)
    return [f"{hoy.year:04d}{hoy.month:02d}", f"{anterior[0]:04d}{anterior[1]:02d}"]


def parsear_periodos(texto: str) -> Optional[list[str]]:
    """'202610,2026-09' → ['202610', '202609']; None si alguno no es un mes válido o son
    demasiados. Vacío → los de `periodos_por_defecto`. Sin repetidos, en el orden pedido."""
    partes = [p.strip().replace("-", "") for p in (texto or "").split(",") if p.strip()]
    if not partes:
        return periodos_por_defecto()
    out: list[str] = []
    for p in partes:
        if len(p) != 6 or not p.isdigit() or not 1 <= int(p[4:]) <= 12:
            return None
        if p not in out:
            out.append(p)
    return out if len(out) <= MAX_PERIODOS_MOVIMIENTOS else None


def ultimos_movimientos(cli: "RegistroCompraVenta", rut_empresa: str, periodos: list[str],
                        limite: int) -> dict[str, Any]:
    """Baja COMPRAS y VENTAS de cada período desde el SII, las persiste (como el F29) y devuelve
    los movimientos del más nuevo al más viejo: fecha del documento, período, operación, tipo y
    folio, todo descendente. `codRespuesta` 3 (período sin registro) no es error: cuenta 0."""
    docs: list[DocumentoRCV] = []
    conteos: dict[str, dict[str, int]] = {}
    for periodo in periodos:
        compras = cli.descargar_periodo(rut_empresa, periodo, COMPRA)
        ventas = cli.descargar_periodo(rut_empresa, periodo, VENTA)
        guardar_documentos(compras + ventas)
        conteos[periodo] = {"compras": len(compras), "ventas": len(ventas)}
        docs += compras + ventas
    docs.sort(key=lambda d: (d.fecha_doc or "", d.periodo, d.tipo_operacion, int(d.tipo_dte), int(d.folio)),
              reverse=True)
    return {
        "periodos": periodos, "documentos": conteos, "total": len(docs), "sin_datos": not docs,
        "movimientos": [{k: getattr(d, k) for k in CAMPOS_MOVIMIENTO} for d in docs[:limite]],
    }


def cargar_desde_json(
    rut_empresa: str,
    tipo_operacion: str,
    periodo: str,
    ruta_o_dict: str | Path | dict,
    persistir: bool = True,
) -> list[DocumentoRCV]:
    """
    Ingiere un RCV ya descargado (respuesta JSON del facade guardada o pegada).
    Permite trabajar toda la lógica sin depender de la bajada en vivo.

    Args:
        ruta_o_dict: ruta a un .json, o el dict ya parseado.
        persistir: si True, guarda en la BD además de devolver los documentos.
    """
    if isinstance(ruta_o_dict, dict):
        data = ruta_o_dict
    else:
        data = json.loads(Path(ruta_o_dict).read_text(encoding="utf-8"))
    docs = normalizar_detalle(rut_empresa, tipo_operacion, periodo, data, origen="csv")
    if persistir:
        guardar_documentos(docs)
    return docs


# ---------------------------------------------------------------------------
# Agregación para el F29 (IVA mensual) — sobre lo persistido
# ---------------------------------------------------------------------------
@dataclass
class ResumenF29:
    """Totales económicos de un período, base para el F29. Montos en pesos."""

    rut_empresa: str
    periodo: str
    # --- Ventas (débito fiscal) ---
    ventas_netas: int = 0
    ventas_exentas: int = 0
    debito_fiscal: int = 0        # IVA de ventas (NC restan)
    n_docs_venta: int = 0
    # --- Compras (crédito fiscal) ---
    compras_netas: int = 0
    compras_exentas: int = 0
    credito_fiscal: int = 0       # IVA recuperable de compras (NC restan)
    iva_no_recuperable: int = 0
    n_docs_compra: int = 0
    # --- Resultado del período (sin PPM/remanente, que son insumos externos) ---
    ppm: int = 0
    remanente_anterior: int = 0

    @property
    def iva_determinado(self) -> int:
        """Débito − Crédito − remanente anterior. Positivo = a pagar."""
        return self.debito_fiscal - self.credito_fiscal - self.remanente_anterior

    @property
    def remanente_siguiente(self) -> int:
        """Si el crédito supera al débito, queda remanente para el mes siguiente."""
        neto = self.iva_determinado
        return -neto if neto < 0 else 0

    @property
    def total_a_pagar(self) -> int:
        """IVA determinado a pagar (si es positivo) más PPM. 0 si hay remanente."""
        base = self.iva_determinado
        return (base if base > 0 else 0) + self.ppm


def calcular_resumen_f29(
    rut_empresa: str,
    periodo: str,
    ppm: int = 0,
    remanente_anterior: int = 0,
) -> ResumenF29:
    """
    Agrega los documentos del RCV persistidos de un período en los totales del
    F29. Las notas de crédito (61) restan mediante ``signo_documento``.

    El IVA recuperable de compras excluye el IVA no recuperable. PPM y remanente
    del mes anterior son insumos externos (no vienen del RCV).
    """
    rut_empresa = _normalizar_rut(rut_empresa)
    periodo = _normalizar_periodo(periodo)
    resumen = ResumenF29(
        rut_empresa=rut_empresa, periodo=periodo,
        ppm=int(ppm), remanente_anterior=int(remanente_anterior),
    )

    with get_db() as conn:
        filas = conn.execute(
            """SELECT tipo_operacion, tipo_dte, monto_neto, monto_exento,
                      monto_iva, monto_iva_no_rec
               FROM rcv_documentos
               WHERE rut_empresa=? AND periodo=?""",
            (rut_empresa, periodo),
        ).fetchall()

    for r in filas:
        s = signo_documento(r["tipo_dte"])
        if r["tipo_operacion"] == VENTA:
            resumen.ventas_netas += s * r["monto_neto"]
            resumen.ventas_exentas += s * r["monto_exento"]
            resumen.debito_fiscal += s * r["monto_iva"]
            resumen.n_docs_venta += 1
        else:  # COMPRA
            resumen.compras_netas += s * r["monto_neto"]
            resumen.compras_exentas += s * r["monto_exento"]
            iva_rec = r["monto_iva"] - (r["monto_iva_no_rec"] or 0)
            resumen.credito_fiscal += s * iva_rec
            resumen.iva_no_recuperable += s * (r["monto_iva_no_rec"] or 0)
            resumen.n_docs_compra += 1

    return resumen


# ---------------------------------------------------------------------------
# Mapeo a los casilleros (códigos) del Formulario 29
# ---------------------------------------------------------------------------
# Códigos oficiales del F29 (Declaración Mensual y Pago Simultáneo). Verificados
# contra el anverso oficial del formulario del SII (anverso_f29.pdf), línea por
# línea. Centralizados aquí por si el SII reordena el formulario a futuro.
#   [OK]   = confirmado contra el formulario oficial.
CODIGOS_F29 = {
    # --- DÉBITOS y VENTAS (líneas 1-13 del anverso) ---
    "503": ("Facturas emitidas — cantidad", "OK"),                      # línea 4
    "502": ("Facturas emitidas — débito", "OK"),
    "110": ("Boletas — cantidad", "OK"),                                # línea 5
    "111": ("Boletas — débito", "OK"),
    "512": ("Notas de débito emitidas — cantidad", "OK"),               # línea 6
    "513": ("Notas de débito emitidas — débito", "OK"),
    "509": ("Notas de crédito emitidas por facturas — cantidad", "OK"), # línea 7 (resta)
    "510": ("Notas de crédito emitidas por facturas — rebaja al débito", "OK"),
    "586": ("Ventas/servicios exentos o no gravados — cantidad", "OK"), # línea 2
    "142": ("Ventas/servicios exentos o no gravados — monto neto", "OK"),
    "538": ("TOTAL DÉBITOS", "OK"),                                     # línea 13
    # --- CRÉDITOS y COMPRAS (líneas 14-33) ---
    "519": ("Facturas recibidas del giro — cantidad", "OK"),            # línea 18
    "520": ("Facturas recibidas del giro — crédito recuperable", "OK"),
    "524": ("Facturas activo fijo — cantidad", "OK"),                   # línea 19
    "525": ("Facturas activo fijo — crédito", "OK"),
    "527": ("Notas de crédito recibidas — cantidad", "OK"),             # línea 20 (resta)
    "528": ("Notas de crédito recibidas — rebaja al crédito", "OK"),
    "504": ("Remanente crédito fiscal mes anterior", "OK"),            # línea 24
    "537": ("TOTAL CRÉDITOS", "OK"),                                    # línea 33
    # --- IMPUESTO DETERMINADO (línea 34) ---
    "89": ("IVA determinado (a pagar)", "OK"),
    "77": ("Remanente crédito fiscal para el período siguiente", "OK"),
    # --- PPM 1a Categoría (línea 43) ---
    "563": ("Base imponible PPM (ingresos brutos del giro)", "OK"),
    "62": ("PPM neto determinado", "OK"),
    # --- TOTALES ---
    "595": ("Subtotal impuesto determinado anverso", "OK"),            # línea 49
    "91": ("TOTAL A PAGAR dentro del plazo legal", "OK"),              # línea 98
}

# Clasificación de tipos de DTE por línea del F29.
_BOLETAS = {39, 41}
_NOTA_DEBITO = {56}
# 61 = nota de crédito (ver TIPOS_NOTA_CREDITO).


@dataclass
class LineaF29:
    """Un casillero del F29: código, glosa, valor y nivel de confianza del código."""
    codigo: str
    glosa: str
    valor: int
    confianza: str = "OK"          # OK | CONF (confirmar contra F29 real)


@dataclass
class DesgloseF29:
    """Desglose por línea del F29 (separa facturas, boletas y notas). Pesos."""
    rut_empresa: str
    periodo: str
    # débitos
    fact_emitidas_cant: int = 0
    fact_emitidas_debito: int = 0
    boletas_cant: int = 0
    boletas_debito: int = 0
    nc_emitidas_cant: int = 0
    nc_emitidas_debito: int = 0
    nd_emitidas_cant: int = 0
    nd_emitidas_debito: int = 0
    ventas_exentas: int = 0
    exentas_cant: int = 0
    total_debitos: int = 0
    # créditos
    fact_recibidas_cant: int = 0
    fact_recibidas_credito: int = 0
    nc_recibidas_cant: int = 0
    nc_recibidas_credito: int = 0
    iva_no_recuperable: int = 0
    total_creditos: int = 0
    remanente_anterior: int = 0
    # determinación
    ppm: int = 0

    @property
    def iva_determinado(self) -> int:
        return self.total_debitos - self.total_creditos - self.remanente_anterior

    @property
    def remanente_siguiente(self) -> int:
        return -self.iva_determinado if self.iva_determinado < 0 else 0

    @property
    def base_ppm(self) -> int:
        """Base del PPM = ingresos brutos del giro (netos afectos + exentos).
        La llena `calcular_desglose_f29` en el atributo interno `_base_ppm`."""
        return getattr(self, "_base_ppm", 0)

    @property
    def total_a_pagar(self) -> int:
        base = self.iva_determinado
        return (base if base > 0 else 0) + self.ppm


def calcular_desglose_f29(
    rut_empresa: str,
    periodo: str,
    ppm: int = 0,
    remanente_anterior: int = 0,
) -> DesgloseF29:
    """
    Desglose por línea del F29 a partir del RCV persistido, separando facturas,
    boletas, notas de crédito y notas de débito (cada una es una línea distinta
    del formulario). Las NC rebajan; el IVA no recuperable se excluye del crédito.
    """
    rut_empresa = _normalizar_rut(rut_empresa)
    periodo = _normalizar_periodo(periodo)
    d = DesgloseF29(rut_empresa=rut_empresa, periodo=periodo,
                    remanente_anterior=int(remanente_anterior), ppm=int(ppm))
    base_ppm = 0

    with get_db() as conn:
        filas = conn.execute(
            """SELECT tipo_operacion, tipo_dte, monto_neto, monto_exento,
                      monto_iva, monto_iva_no_rec
               FROM rcv_documentos WHERE rut_empresa=? AND periodo=?""",
            (rut_empresa, periodo),
        ).fetchall()

    for r in filas:
        t = int(r["tipo_dte"])
        neto, exento, iva = r["monto_neto"], r["monto_exento"], r["monto_iva"]
        if r["tipo_operacion"] == VENTA:
            base_ppm += neto + exento
            if t in TIPOS_NOTA_CREDITO:
                d.nc_emitidas_cant += 1
                d.nc_emitidas_debito += iva
            elif t in _BOLETAS:
                d.boletas_cant += 1
                d.boletas_debito += iva
            elif t in _NOTA_DEBITO:
                d.nd_emitidas_cant += 1
                d.nd_emitidas_debito += iva
            else:  # facturas afectas/exentas
                d.fact_emitidas_cant += 1
                d.fact_emitidas_debito += iva
            if exento:
                d.ventas_exentas += exento
                d.exentas_cant += 1
        else:  # COMPRA
            iva_rec = iva - (r["monto_iva_no_rec"] or 0)
            d.iva_no_recuperable += (r["monto_iva_no_rec"] or 0)
            if t in TIPOS_NOTA_CREDITO:
                d.nc_recibidas_cant += 1
                d.nc_recibidas_credito += iva_rec
            else:
                d.fact_recibidas_cant += 1
                d.fact_recibidas_credito += iva_rec

    d.total_debitos = (d.fact_emitidas_debito + d.boletas_debito
                       + d.nd_emitidas_debito - d.nc_emitidas_debito)
    d.total_creditos = d.fact_recibidas_credito - d.nc_recibidas_credito
    # base del PPM se guarda aparte (la property no puede calcularla sola)
    d._base_ppm = base_ppm  # type: ignore[attr-defined]
    return d


def mapear_a_f29(desglose: DesgloseF29) -> list[LineaF29]:
    """
    Traduce un DesgloseF29 a la lista de casilleros del F29 (código→valor), en
    el orden visual del formulario. Omite líneas en cero para no ensuciar.
    """
    base_ppm = getattr(desglose, "_base_ppm", 0)

    iva = desglose.iva_determinado
    candidatos = [
        # --- DÉBITOS (líneas 4-13) ---
        ("503", desglose.fact_emitidas_cant),
        ("502", desglose.fact_emitidas_debito),
        ("110", desglose.boletas_cant),
        ("111", desglose.boletas_debito),
        ("512", desglose.nd_emitidas_cant),      # notas de débito emitidas
        ("513", desglose.nd_emitidas_debito),
        ("509", desglose.nc_emitidas_cant),      # notas de crédito emitidas (resta)
        ("510", desglose.nc_emitidas_debito),
        ("586", desglose.exentas_cant),
        ("142", desglose.ventas_exentas),
        ("538", desglose.total_debitos),
        # --- CRÉDITOS (líneas 18-33) ---
        ("519", desglose.fact_recibidas_cant),
        ("520", desglose.fact_recibidas_credito),
        ("527", desglose.nc_recibidas_cant),     # notas de crédito recibidas (resta)
        ("528", desglose.nc_recibidas_credito),
        ("504", desglose.remanente_anterior),
        ("537", desglose.total_creditos),
        # --- IMPUESTO DETERMINADO (línea 34) ---
        ("89", iva if iva > 0 else 0),           # IVA a pagar
        ("77", desglose.remanente_siguiente),    # remanente período siguiente
        # --- PPM (línea 43) ---
        ("563", base_ppm),
        ("62", desglose.ppm),
        # --- TOTAL (línea 98) ---
        ("91", desglose.total_a_pagar),
    ]

    lineas: list[LineaF29] = []
    for codigo, valor in candidatos:
        if not valor:
            continue
        glosa, confianza = CODIGOS_F29.get(codigo, (codigo, "CONF"))
        lineas.append(LineaF29(codigo=codigo, glosa=glosa, valor=int(valor), confianza=confianza))
    return lineas
