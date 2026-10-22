"""
test_robustez.py — Verifica la capa de robustez transversal (API + MCP).

Es un script plano (como el resto de los tests del repo), no pytest. Comprueba:
  1. Errores de dominio (`core/errors`) con su código y status.
  2. La API devuelve el **envelope de error uniforme** y el header `X-Request-ID`:
       - 422 en validación de body.
       - 404 con codigo="no_encontrado".
       - 500 SIN filtrar la traza interna (mensaje genérico).
  3. El servidor MCP registra sus herramientas.

Uso:  .venv/bin/python test_robustez.py
"""
from __future__ import annotations

import asyncio
import sys

from fastapi import APIRouter
from fastapi.testclient import TestClient

import main
from core.errors import DTEChileError, SinFoliosError, SIIError, ValidacionError


def _check(cond: bool, msg: str) -> None:
    print(f"  {'✅' if cond else '❌'} {msg}")
    if not cond:
        _check.fallos += 1
_check.fallos = 0


def test_errores_dominio() -> None:
    print("\n[1] Errores de dominio")
    e = SinFoliosError("No quedan folios T61", detalle={"tipo_dte": 61})
    _check(e.codigo == "sin_folios" and e.http_status == 409, "SinFoliosError → sin_folios/409")
    _check(isinstance(e, DTEChileError), "hereda de DTEChileError")
    _check(SIIError("timeout").http_status == 502, "SIIError → 502 (servicio externo)")
    _check(ValidacionError("rut malo").http_status == 422, "ValidacionError → 422")
    _check(e.as_dict()["detalle"] == {"tipo_dte": 61}, "as_dict incluye detalle")


def test_api_envelope() -> None:
    print("\n[2] Envelope de error de la API")
    # Rutas de prueba que levantan cada tipo de error.
    r = APIRouter()

    @r.get("/_t/dominio")
    def _dom():
        raise SinFoliosError("No quedan folios T61", detalle={"tipo_dte": 61})

    @r.get("/_t/boom")
    def _boom():
        raise RuntimeError("secreto interno que NO debe filtrarse")

    main.app.include_router(r)
    c = TestClient(main.app, raise_server_exceptions=False)

    # 404 nativo
    r404 = c.get("/no-existe-nunca")
    _check(r404.status_code == 404 and r404.json()["error"]["codigo"] == "no_encontrado",
           "404 → envelope codigo=no_encontrado")
    _check("X-Request-ID" in r404.headers, "respuesta trae header X-Request-ID")

    # error de dominio → status + codigo del dominio
    rd = c.get("/_t/dominio")
    body = rd.json()["error"]
    _check(rd.status_code == 409, "dominio → 409")
    _check(body["codigo"] == "sin_folios" and body["detalle"]["tipo_dte"] == 61,
           "dominio → codigo=sin_folios + detalle")
    _check(bool(body.get("request_id")), "envelope incluye request_id")

    # excepción no manejada → 500 genérico, SIN filtrar el mensaje interno
    rb = c.get("/_t/boom")
    _check(rb.status_code == 500, "no manejada → 500")
    _check("secreto interno" not in rb.text, "500 NO filtra la traza/mensaje interno")
    _check(rb.json()["error"]["codigo"] == "error_interno", "500 → codigo=error_interno")

    # validación de body (422)
    rv = c.post("/api/v1/auth/login", json={"solo_un_campo": "x"})
    _check(rv.status_code == 422 and rv.json()["error"]["codigo"] == "validacion",
           "body inválido → 422 codigo=validacion")


def test_mcp() -> None:
    print("\n[3] Servidor MCP")
    import mcp_server
    tools = asyncio.run(mcp_server.mcp.list_tools())
    nombres = {t.name for t in tools}
    _check(len(tools) >= 10, f"registra {len(tools)} herramientas")
    for req in ("situacion_folios", "solicitar_folios", "estado_envio", "empresa_autorizada",
                "emitir_dte", "enviar_dte"):
        _check(req in nombres, f"expone '{req}'")
    _check(all(t.description for t in tools), "todas las herramientas tienen descripción")


def test_mcp_auth() -> None:
    print("\n[4] Auth del servidor MCP (HTTP)")
    import mcp_server
    from core.auth import crear_access_token

    # token válido: JWT del proyecto o el secreto compartido
    _check(mcp_server._token_valido(crear_access_token({"sub": "admin"})),
           "acepta un JWT válido del proyecto")
    _check(not mcp_server._token_valido(""), "rechaza token vacío")
    _check(not mcp_server._token_valido("basura.no.jwt"), "rechaza token inválido")

    # el gate ASGI responde 401 sin Authorization, sin tocar la app protegida
    async def _app_protegida(scope, receive, send):
        raise AssertionError("la app protegida NO debe ejecutarse sin auth")

    gate = mcp_server._AuthASGI(_app_protegida)
    enviados = []

    async def _send(msg):
        enviados.append(msg)

    async def _receive():
        return {"type": "http.request", "body": b""}

    scope = {"type": "http", "headers": []}  # sin Authorization
    asyncio.run(gate(scope, _receive, _send))
    status_ = next((m["status"] for m in enviados if m["type"] == "http.response.start"), None)
    _check(status_ == 401, "gate ASGI → 401 sin bearer token (no ejecuta la app)")


def test_reintentos() -> None:
    print("\n[5] Reintentos/backoff HTTP del SII")
    import httpx
    from core.reintentos import ClienteReintentos

    def _cliente(handler, **kw):
        return ClienteReintentos(transport=httpx.MockTransport(handler),
                                 backoff_base=0.001, backoff_tope=0.005, **kw)

    # 503 dos veces y luego 200 → reintenta y termina OK
    n = {"c": 0}
    def h_503(_):
        n["c"] += 1
        return httpx.Response(200, text="ok") if n["c"] >= 3 else httpx.Response(503)
    with _cliente(h_503, max_reintentos=3) as c:
        r = c.get("https://maullin.sii.cl/x")
    _check(r.status_code == 200 and n["c"] == 3, "503×2 → reintenta y termina en 200 (3 llamadas)")

    # agota los reintentos → devuelve el último 503
    m = {"c": 0}
    def h_siempre503(_):
        m["c"] += 1
        return httpx.Response(503)
    with _cliente(h_siempre503, max_reintentos=2) as c:
        r = c.get("https://x/y")
    _check(r.status_code == 503 and m["c"] == 3, "agota reintentos (1+2) y devuelve 503")

    # 4xx NO se reintenta
    k = {"c": 0}
    def h_400(_):
        k["c"] += 1
        return httpx.Response(400)
    with _cliente(h_400, max_reintentos=3) as c:
        r = c.get("https://x/y")
    _check(r.status_code == 400 and k["c"] == 1, "no reintenta 4xx (400)")

    # error de red: reintenta y al agotar propaga la excepción
    j = {"c": 0}
    def h_red(_):
        j["c"] += 1
        raise httpx.ConnectError("boom")
    try:
        with _cliente(h_red, max_reintentos=2) as c:
            c.get("https://x/y")
        propago = False
    except httpx.ConnectError:
        propago = True
    _check(propago and j["c"] == 3, "error de red: reintenta (1+2) y propaga")

    # respeta Retry-After
    _check(ClienteReintentos(backoff_tope=8.0)._espera(0, "3") == 3.0, "respeta Retry-After (3s)")


def test_sala_espera() -> None:
    print("\n[5b] Sala de espera (Queue-it) del SII")
    import httpx
    from core.errors import SIIError
    from core.reintentos import ClienteReintentos
    from core.sala_espera import MENSAJE, HOOKS, es_sala_espera, limpiar

    _check(all(es_sala_espera(u) for u in (
        "https://salaespera.sii.cl/?c=sii&e=consultaterceros&t=https%3A%2F%2Fwww2.sii.cl%2Fstc%2Fnoauthz&cid=es-CL",
        "HTTPS://SalaEspera.SII.cl", "//salaespera.sii.cl/?c=sii", "https://sii.queue-it.net/?c=sii&e=renta",
        "https://salaespera.sii.cl:443/x")), "reconoce salaespera.sii.cl y *.queue-it.net")
    _check(not any(es_sala_espera(u) for u in (
        "https://www2.sii.cl/stc/noauthz?t=salaespera.sii.cl", "https://misiir.sii.cl/cgi_misii/siihome.cgi",
        "/salaespera.sii.cl", "https://notqueue-it.net/", "")), "no confunde URLs normales del SII")

    def h(req):
        if req.url.host == "salaespera.sii.cl":
            return httpx.Response(200, text="<title>SII-SalaEspera</title>")
        return httpx.Response(302, headers={"Location": "https://salaespera.sii.cl/?c=sii&e=autenticacionmisii"})

    for nombre, cli in (
        ("ClienteReintentos siguiendo el 302", ClienteReintentos(transport=httpx.MockTransport(h), follow_redirects=True)),
        ("httpx sin seguir el 302 (mipyme)", httpx.Client(transport=httpx.MockTransport(h), event_hooks=HOOKS)),
    ):
        try:
            cli.get("https://misiir.sii.cl/cgi_misii/siihome.cgi")
            _check(False, f"{nombre}: debió cortar")
        except SIIError as e:
            _check(e.mensaje == MENSAJE and e.http_status == 502, f"{nombre}: SIIError con el mensaje de la fila")
    _check(limpiar(f"No se pudo consultar el SII: {MENSAJE}") == MENSAJE, "limpiar quita prefijos")

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.errors import registrar_manejo_errores
    app = FastAPI()
    registrar_manejo_errores(app)

    @app.get("/fila")
    def _fila():
        raise SIIError(f"No se pudo consultar el SII: {MENSAJE}")

    r = TestClient(app).get("/fila")
    _check(r.status_code == 502 and r.json()["error"] == {**r.json()["error"], "codigo": "sii_comunicacion", "mensaje": MENSAJE},
           "API: 502 sii_comunicacion con el mensaje de la fila, sin prefijos")


def test_movimientos_periodos() -> None:
    print("\n[5c] Movimientos del SII: períodos")
    from datetime import date
    from core.rcv import parsear_periodos, periodos_por_defecto
    _check(periodos_por_defecto(date(2026, 10, 31)) == ["202610", "202609"], "este mes y el anterior")
    _check(periodos_por_defecto(date(2026, 1, 5)) == ["202601", "202512"], "enero → diciembre del año anterior")
    _check(parsear_periodos("202610, 2026-09,202610") == ["202610", "202609"], "acepta YYYY-MM y saca repetidos")
    _check(len(parsear_periodos("")) == 2, "vacío → dos meses")
    _check(all(parsear_periodos(m) is None for m in ("202613", "2026", "abcdef", "202600", "202601,202602,202603,202604")),
           "rechaza meses inválidos y más de 3")


def test_seguridad() -> None:
    print("\n[6] Hardening / configuración")
    import os
    from core.config import JWT_DEFAULT_INSEGURO, Settings

    os.environ.pop("DTE_MASTER_KEY", None)
    ins = Settings(jwt_secret_key=JWT_DEFAULT_INSEGURO, cors_origins="*")
    probs = ins.problemas_seguridad()
    _check(any("JWT" in p for p in probs), "detecta JWT_SECRET_KEY por defecto")
    _check(any("CORS" in p for p in probs), "detecta CORS '*'")

    ins2 = Settings(jwt_secret_key="corta")
    _check(any("JWT" in p for p in ins2.problemas_seguridad()), "detecta JWT < 32 chars")

    # producción + inseguro = postura de abortar
    prod = Settings(sii_ambiente="produccion", jwt_secret_key=JWT_DEFAULT_INSEGURO)
    _check(prod.es_produccion and bool(prod.problemas_seguridad()),
           "producción + inseguro → hay problemas (main.py aborta)")

    # config fuerte → sin problemas
    os.environ["DTE_MASTER_KEY"] = "k" * 44
    try:
        seg = Settings(jwt_secret_key="x" * 48, cors_origins="http://localhost:8000")
        _check(seg.problemas_seguridad() == [], "config fuerte → sin problemas")
    finally:
        os.environ.pop("DTE_MASTER_KEY", None)


def test_seguridad_recursos() -> None:
    print("\n[7] XXE / límite de uploads")
    import asyncio
    import io

    from starlette.datastructures import UploadFile

    from api.util import MAX_UPLOAD_BYTES, leer_upload
    from core.errors import ValidacionError
    from core.xml_seguro import parse_seguro

    # una entidad interna NO se expande (anti billion-laughs / XXE)
    root = parse_seguro(b'<!DOCTYPE r [<!ENTITY x "EXPANDIDO">]><r>&x;</r>')
    _check(root.text != "EXPANDIDO", "parse_seguro NO expande entidades (anti-XXE)")
    _check(parse_seguro(b"<a><b>1</b></a>").find("b").text == "1",
           "parse_seguro parsea XML normal")

    # upload que declara tamaño > límite → rechazado (no lo lee entero)
    grande = UploadFile(file=io.BytesIO(b"x"), size=MAX_UPLOAD_BYTES + 1, filename="big.xml")
    try:
        asyncio.run(leer_upload(grande)); rechazo = False
    except ValidacionError:
        rechazo = True
    _check(rechazo, "leer_upload rechaza archivo > límite (OOM/DoS)")

    chico = UploadFile(file=io.BytesIO(b"hola"), size=4, filename="ok.xml")
    _check(asyncio.run(leer_upload(chico)) == b"hola", "leer_upload devuelve archivo chico")


def test_diagnostico() -> None:
    print("\n[8] Diagnóstico de onboarding (sin red)")
    from types import SimpleNamespace

    from core.onboarding import AUTO, CONSENTIMIENTO, HUMANO, diagnosticar, diagnosticar_cartera
    from core.sii_portal import BASE_PRODUCCION

    class FakePortal:
        def __init__(self, docs, swp, swc, folios):
            self._d, self._swp, self._swc, self._f = docs, swp, swc, folios

        def consultar_empresa_autorizada(self, rut, base=None):
            return SimpleNamespace(razon_social="EMPRESA TEST", rut=rut, documentos=[
                SimpleNamespace(codigo=c, descripcion="", autorizado_desde="",
                                desautorizado_desde=None) for c in self._d])

        def datos_software(self, rut, base=None):
            return self._swp if base == BASE_PRODUCCION else self._swc

        def situacion_folios(self, rut, tipos):
            return self._f

        def empresas_asociadas(self):
            return [{"rut": "76111111-6"}, {"rut": "76444444-2"}]

    propio = {"propio": True, "software": "MISW", "certificado": True, "fecha_resolucion": ""}
    d = diagnosticar(FakePortal([33, 61], propio, propio, {}), "76111111-6")
    _check(d.estado == "emitiendo" and d.listo_para_emitir, "empresa que emite propio → listo")

    cert = FakePortal([33, 61],
                      {"propio": False, "software": "SII", "certificado": False, "fecha_resolucion": ""},
                      {"propio": True, "software": "MISW", "certificado": False, "fecha_resolucion": ""},
                      {33: {"bloqueado": True}, 61: {"bloqueado": True}})
    d2 = diagnosticar(cert, "76111111-6")
    _check(d2.estado == "certificando", "en certificación detectado")
    _check(any(a.modo == HUMANO and a.urgente for a in d2.acciones),
           "folios bloqueados → acción humana urgente")
    _check(any(a.modo == CONSENTIMIENTO for a in d2.acciones),
           "activar en producción → requiere consentimiento")
    _check(any(a.modo == AUTO for a in d2.acciones), "set de pruebas → auto")
    _check(any(c.id == "software" and c.estado == "atencion" for c in d2.chequeos),
           "chequeo: software 'SII' marcado para cambiar")

    # caso ejemplo: autorizada a 34 (exenta) pero NO a 33 (afecta), en sistema gratuito
    exenta = FakePortal([34, 39, 61],
                        {"propio": False, "software": "SII", "certificado": False, "fecha_resolucion": ""},
                        {"propio": False, "software": "", "certificado": False, "fecha_resolucion": ""}, {})
    d3 = diagnosticar(exenta, "76444444-2")
    _check(any("33" in n and "Exenta" in n for n in d3.notas),
           "exenta sin afecta → nota explicativa del 33")

    # cartera: diagnostica todas las empresas del cert (aquí, 2 por empresas_asociadas)
    cart = diagnosticar_cartera(exenta)
    _check(len(cart) == 2, "cartera diagnostica todas las empresas del cert")


def test_monitoreo() -> None:
    print("\n[9] Monitoreo de folios/CAF (local)")
    from datetime import date

    from core.monitoreo import resumen_salud

    r = resumen_salud("76111111-6", hoy=date(2026, 7, 15))
    _check(r["verdict"] in ("ok", "atencion", "critico"), "veredicto válido")
    _check("cafs" in r and "por_estado" in r and "total_cafs" in r, "estructura correcta")
    if r["cafs"]:  # empresa demo tiene CAF de 2024/2025 → vencidos a 2026
        _check(any(c["estado"] == "vencido" for c in r["cafs"]),
               "detecta CAF vencido (regla 6 meses / CAF-3-517)")
        _check(all("detalle" in c and "restantes" in c for c in r["cafs"]),
               "cada CAF trae detalle + folios restantes")


def test_seguimiento() -> None:
    print("\n[10] Seguimiento de envíos por lote (sin red)")
    from core.seguimiento import estados_lote

    def body(td, ace, rch):
        return {"estado": "EPR", "glosa": "Envio Procesado", "respuesta_raw":
                f"<SII:RESP_BODY><TIPO_DOCTO>{td}</TIPO_DOCTO><INFORMADOS>1</INFORMADOS>"
                f"<ACEPTADOS>{ace}</ACEPTADOS><RECHAZADOS>{rch}</RECHAZADOS>"
                f"<REPAROS>0</REPAROS></SII:RESP_BODY>"}

    respuestas = {
        1: body(34, 1, 0),                                  # aceptado
        2: body(33, 0, 1),                                  # rechazado
        3: {"estado": "REC", "glosa": "Recibido", "respuesta_raw": "<ESTADO>REC</ESTADO>"},  # pendiente
    }

    class FakeCli:
        def consultar_estado_track(self, tid, rut, dv):
            return respuestas[int(tid)]

    r = estados_lote(FakeCli(), [1, 2, 3], "76111111", "6")
    _check(r["aceptados"] == 1 and r["rechazados"] == 1, "cuenta aceptados/rechazados del lote")
    _check(r["resueltos"] == 2 and r["pendientes"] == 1, "REC = pendiente; EPR con body = resuelto")
    _check(not r["todos_resueltos"], "todos_resueltos=False con un envío pendiente")
    _check(len(r["detalles"]) == 3, "detalle por cada TrackID")


def main_() -> int:
    print("=" * 60)
    print("  TEST ROBUSTEZ — API + MCP")
    print("=" * 60)
    test_errores_dominio()
    test_api_envelope()
    test_mcp()
    test_mcp_auth()
    test_reintentos()
    test_sala_espera()
    test_movimientos_periodos()
    test_seguridad()
    test_seguridad_recursos()
    test_diagnostico()
    test_monitoreo()
    test_seguimiento()
    print("\n" + "=" * 60)
    if _check.fallos:
        print(f"❌ {_check.fallos} comprobación(es) fallaron")
        return 1
    print("✅ Todo OK")
    return 0


if __name__ == "__main__":
    sys.exit(main_())
