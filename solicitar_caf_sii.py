#!/usr/bin/env python3
"""
solicitar_caf_sii.py — Solicita un CAF al SII para un tipo DTE y lo carga en BD.

Flujo:
  1. Lee .env para configuración
  2. Carga certificado digital .p12
  3. Obtiene semilla → firma → token del SII (vía CrSeed + GetTokenFromSeed)
  4. Llama a CrFolio.jws del SII para solicitar folios (tipo DTE)
  5. Guarda el XML en storage/cafs/
  6. Parsea con ManejadorCAF y registra en BD

Uso:
  .venv/bin/python solicitar_caf_sii.py [--tipo 61] [--cantidad 100]

Requiere:
  - .env configurado
  - Certificado .p12 válido para SII
  - Postulación SII aprobada
"""

import argparse
import base64
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_PROJECT_ROOT))

import traceback
from datetime import datetime

import httpx
from lxml import etree

from core import sala_espera
from core.caf import ManejadorCAF
from core.config import settings
from core.crypto import CertificadoDigital, canonicalizar_elemento
from core.database import get_db
from core.models import registrar_caf
from core.sii import AmbienteSII

# ============================================================
# URLs de los Web Services del SII
# ============================================================
URLS = {
    AmbienteSII.CERTIFICACION: {
        "semilla": "https://maullin.sii.cl/DTEWS/CrSeed.jws",
        "token":   "https://maullin.sii.cl/DTEWS/GetTokenFromSeed.jws",
        "caf":     "https://maullin.sii.cl/DTEWS/CrFolio.jws",
    },
    AmbienteSII.PRODUCCION: {
        "semilla": "https://palena.sii.cl/DTEWS/CrSeed.jws",
        "token":   "https://palena.sii.cl/DTEWS/GetTokenFromSeed.jws",
        "caf":     "https://palena.sii.cl/DTEWS/CrFolio.jws",
    },
}

# ============================================================
# UTILIDADES
# ============================================================

def split_rut(rut_completo: str) -> tuple[str, str]:
    """Divide '76111111-6' → ('76111111', '6')."""
    r = rut_completo.strip().replace(".", "")
    if "-" not in r:
        return (r, "")
    parts = r.rsplit("-", 1)
    return (parts[0], parts[1])


def caf_existe(rut_emisor: str, tipo_dte: int, folio_desde: int) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM cafs WHERE rut_emisor=? AND tipo_dte=? AND folio_desde=?",
            (rut_emisor, tipo_dte, folio_desde),
        ).fetchone()
    return row is not None


def mostrar_cafs():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, rut_emisor, tipo_dte, folio_desde, folio_hasta, "
            "folio_siguiente, activo FROM cafs ORDER BY id"
        ).fetchall()
    print()
    print("=" * 72)
    print("CAFs EN BASE DE DATOS")
    print("=" * 72)
    if not rows:
        print("  (vacío)")
        return
    hdr = f"{'ID':>4}  {'RUT':<12} {'TIPO':>5}  {'DESDE':>6}  {'HASTA':>6}  {'SIG':>6}  {'ACT':>4}"
    print(hdr)
    print("-" * 72)
    for r in rows:
        print(
            f"{r['id']:>4}  {r['rut_emisor']:<12} {r['tipo_dte']:>5}  "
            f"{r['folio_desde']:>6}  {r['folio_hasta']:>6}  "
            f"{r['folio_siguiente']:>6}  {r['activo']:>4}"
        )
    print("=" * 72)


# ============================================================
# CLIENTE SOAP SII
# ============================================================

class ClienteSOAP:
    """Cliente SOAP simple para Web Services del SII."""

    def __init__(self, ambiente: AmbienteSII, timeout: float = 60.0):
        self.ambiente = ambiente
        self.urls = URLS[ambiente]
        self.timeout = timeout

    def _post(self, url: str, soap_body: str) -> str:
        headers = {
            "Content-Type": "text/xml; charset=UTF-8",
            "SOAPAction": "",
        }
        with httpx.Client(timeout=self.timeout, verify=True, event_hooks=sala_espera.HOOKS) as client:
            resp = client.post(url, content=soap_body.encode("utf-8"), headers=headers)
            if resp.status_code == 302:
                raise RuntimeError(
                    f"Endpoint no disponible en este ambiente. "
                    f"URL: {url} → Redirect: {resp.headers.get('location', 'N/A')}"
                )
            resp.raise_for_status()
            return resp.text

    def obtener_semilla(self) -> str:
        """
        Obtiene la semilla del SII.
        Maneja formato RPC/encoded (getSeedReturn con XML escapado).
        """
        import html as _html
        soap = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
            '<soapenv:Body><getSeed/></soapenv:Body></soapenv:Envelope>'
        )
        respuesta = self._post(self.urls["semilla"], soap)
        root = etree.fromstring(respuesta.encode("utf-8"))

        # 1. Elemento directo
        for ns in ("http://www.sii.cl/XMLSchema", ""):
            q = f".//{{{ns}}}SEMILLA" if ns else ".//SEMILLA"
            s = root.find(q)
            if s is not None and s.text:
                return s.text.strip()

        # 2. getSeedReturn (RPC/encoded)
        ret = root.find(".//getSeedReturn")
        if ret is not None and ret.text:
            for t in (ret.text.strip(), _html.unescape(ret.text.strip())):
                try:
                    inner = etree.fromstring(t.encode("utf-8"))
                except Exception:
                    continue
                for ns in ("http://www.sii.cl/XMLSchema", ""):
                    q = f".//{{{ns}}}SEMILLA" if ns else ".//SEMILLA"
                    s = inner.find(q)
                    if s is not None and s.text:
                        return s.text.strip()

        raise ValueError(f"No se pudo obtener semilla. Respuesta: {respuesta[:600]}")

    def obtener_token(self, cert: CertificadoDigital) -> str:
        """Flujo semilla → firma → token."""
        import html as _html

        # 1. Semilla
        semilla = self.obtener_semilla()
        print(f"    → Semilla: {semilla}")

        # 2. Digest del documento semilla
        xml_doc = f"<getToken><item><Semilla>{semilla}</Semilla></item></getToken>"
        doc_elem = etree.fromstring(xml_doc)
        digest_b64 = cert.hash_sha1_b64(canonicalizar_elemento(doc_elem))

        # 3. SignedInfo (canonicalizado para firma)
        si_str = (
            '<SignedInfo xmlns="http://www.w3.org/2000/09/xmldsig#">'
            '<CanonicalizationMethod Algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315"/>'
            '<SignatureMethod Algorithm="http://www.w3.org/2000/09/xmldsig#rsa-sha1"/>'
            '<Reference URI="">'
            '<Transforms><Transform Algorithm="http://www.w3.org/2000/09/xmldsig#enveloped-signature"/></Transforms>'
            f'<DigestMethod Algorithm="http://www.w3.org/2000/09/xmldsig#sha1"/>'
            f'<DigestValue>{digest_b64}</DigestValue>'
            '</Reference>'
            '</SignedInfo>'
        )
        si_elem = etree.fromstring(si_str)
        firma_b64 = base64.b64encode(
            cert.firmar_datos(canonicalizar_elemento(si_elem))
        ).decode("ascii")

        # 4. XML firmado con prefijo ds: (SII acepta mejor este formato)
        semilla_firmada = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<getToken>\n'
            '<item>\n'
            f'<Semilla>{semilla}</Semilla>\n'
            '</item>\n'
            '<ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#">\n'
            '<ds:SignedInfo>\n'
            '<ds:CanonicalizationMethod Algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315"/>\n'
            '<ds:SignatureMethod Algorithm="http://www.w3.org/2000/09/xmldsig#rsa-sha1"/>\n'
            '<ds:Reference URI="">\n'
            '<ds:Transforms>\n'
            '<ds:Transform Algorithm="http://www.w3.org/2000/09/xmldsig#enveloped-signature"/>\n'
            '</ds:Transforms>\n'
            '<ds:DigestMethod Algorithm="http://www.w3.org/2000/09/xmldsig#sha1"/>\n'
            f'<ds:DigestValue>{digest_b64}</ds:DigestValue>\n'
            '</ds:Reference>\n'
            '</ds:SignedInfo>\n'
            f'<ds:SignatureValue>{firma_b64}</ds:SignatureValue>\n'
            '<ds:KeyInfo>\n'
            '<ds:X509Data>\n'
            f'<ds:X509Certificate>{cert.certificado_b64}</ds:X509Certificate>\n'
            '</ds:X509Data>\n'
            '</ds:KeyInfo>\n'
            '</ds:Signature>\n'
            '</getToken>'
        )

        print(f"    → XML firmado: {len(semilla_firmada)} chars")

        # 5. Enviar al SII
        escaped = (
            semilla_firmada.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        soap = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">\n'
            '  <soapenv:Body>\n'
            '    <getToken>\n'
            f'      <pszXml>{escaped}</pszXml>\n'
            '    </getToken>\n'
            '  </soapenv:Body>\n'
            '</soapenv:Envelope>'
        )

        respuesta = self._post(self.urls["token"], soap)
        root = etree.fromstring(respuesta.encode("utf-8"))

        # Verificar SOAP Fault
        fault = root.find(".//{http://schemas.xmlsoap.org/soap/envelope/}Fault")
        if fault is not None:
            fs = fault.find("faultstring")
            raise RuntimeError(
                f"SOAP Fault del SII: {fs.text if fs is not None else 'Unknown'}"
            )

        # Buscar token en RPC/encoded response
        ret = root.find(".//getTokenReturn")
        if ret is not None and ret.text:
            for t in (ret.text.strip(), _html.unescape(ret.text.strip())):
                try:
                    inner = etree.fromstring(t.encode("utf-8"))
                except Exception:
                    continue
                # Estado
                estado = None
                for ns in ("http://www.sii.cl/XMLSchema", ""):
                    q = f".//{{{ns}}}ESTADO" if ns else ".//ESTADO"
                    e = inner.find(q)
                    if e is not None and e.text:
                        estado = e.text.strip()
                        break
                # Glosa
                glosa = None
                for ns in ("http://www.sii.cl/XMLSchema", ""):
                    q = f".//{{{ns}}}GLOSA" if ns else ".//GLOSA"
                    g = inner.find(q)
                    if g is not None and g.text:
                        glosa = g.text.strip()
                        break
                # Token
                for ns in ("http://www.sii.cl/XMLSchema", ""):
                    q = f".//{{{ns}}}TOKEN" if ns else ".//TOKEN"
                    tok = inner.find(q)
                    if tok is not None and tok.text:
                        return tok.text.strip()
                # Error check
                if estado is not None and estado != "00":
                    raise RuntimeError(
                        f"SII rechazó token. Estado={estado}, "
                        f"Glosa='{glosa or 'N/A'}'. "
                        f"Respuesta completa:\n{ret.text.strip()[:500]}"
                    )

        raise ValueError(
            f"No se pudo obtener token. Respuesta: {respuesta[:500]}"
        )

    def solicitar_caf(self, token: str, rut_num: str, dv: str,
                      tipo_dte: int, cantidad: int) -> str:
        """Llama a CrFolio.jws y extrae el XML <AUTORIZACION>."""
        import html as _html

        soap = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">\n'
            '  <soapenv:Body>\n'
            '    <getFolio>\n'
            f'      <RutContratante>{rut_num}</RutContratante>\n'
            f'      <DvContratante>{dv}</DvContratante>\n'
            f'      <RutEmpresa>{rut_num}</RutEmpresa>\n'
            f'      <DvEmpresa>{dv}</DvEmpresa>\n'
            f'      <RutEnvia>{rut_num}</RutEnvia>\n'
            f'      <DvEnvia>{dv}</DvEnvia>\n'
            f'      <TipoDte>{tipo_dte}</TipoDte>\n'
            f'      <CantFolios>{cantidad}</CantFolios>\n'
            f'      <Token>{token}</Token>\n'
            '    </getFolio>\n'
            '  </soapenv:Body>\n'
            '</soapenv:Envelope>'
        )

        print(f"  → URL: {self.urls['caf']}")
        print(f"  → RUT: {rut_num}-{dv}, Tipo DTE: {tipo_dte}, Cantidad: {cantidad}")

        respuesta_xml = self._post(self.urls["caf"], soap)
        print(f"  → Respuesta: {len(respuesta_xml)} chars")

        root = etree.fromstring(respuesta_xml.encode("utf-8"))
        body = root.find("{http://schemas.xmlsoap.org/soap/envelope/}Body")

        # Verificar Fault
        fault = root.find(".//{http://schemas.xmlsoap.org/soap/envelope/}Fault")
        if fault is not None:
            fs = fault.find("faultstring")
            fd = fault.find("detail")
            msg = f"SII Fault: {fs.text if fs is not None else 'Unknown'}"
            if fd is not None and fd.text:
                msg += f"\n  Detalle: {fd.text}"
            raise RuntimeError(msg)

        caf_xml = None

        # Estrategia A: texto dentro de elemento (escapado o no)
        for elem in body.iter():
            for t in (elem.text or "", _html.unescape(elem.text or "")):
                if "<AUTORIZACION" in t:
                    start = t.index("<AUTORIZACION")
                    end = t.rindex("</AUTORIZACION>") + len("</AUTORIZACION>")
                    caf_xml = t[start:end]
                    break
            if caf_xml:
                break

        # Estrategia B: elemento AUTORIZACION parseado
        if caf_xml is None:
            for elem in body.iter():
                local = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                if local == "AUTORIZACION":
                    caf_xml = etree.tostring(elem, encoding="unicode")
                    break

        # Estrategia C: raw
        if caf_xml is None:
            for t in (respuesta_xml, _html.unescape(respuesta_xml)):
                if "<AUTORIZACION" in t:
                    start = t.index("<AUTORIZACION")
                    end = t.rindex("</AUTORIZACION>") + len("</AUTORIZACION>")
                    caf_xml = t[start:end]
                    break

        if caf_xml is None:
            raise RuntimeError(
                "No se pudo extraer <AUTORIZACION> de la respuesta.\n"
                f"Respuesta (primeros 1000 chars):\n{respuesta_xml[:1000]}"
            )

        return caf_xml.strip()


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Solicitar CAF al SII y cargar en BD")
    parser.add_argument("--tipo", type=int, default=61,
                        help="Tipo DTE (default: 61 Nota de Crédito)")
    parser.add_argument("--cantidad", type=int, default=100,
                        help="Cantidad de folios (default: 100)")
    args = parser.parse_args()
    tipo_dte = args.tipo
    cantidad = args.cantidad

    print("=" * 72)
    print("SOLICITAR CAF AL SII - DTE Chile API")
    print("Fecha/Hora:", datetime.now().isoformat())
    print("=" * 72)

    # 1. Config
    print("\n📋 Configuración:")
    ambiente_str = settings.sii_ambiente
    empresa_rut = settings.empresa_rut
    cert_path = settings.certificado_path
    cert_pass = settings.certificado_password
    print(f"  Ambiente      : {ambiente_str}")
    print(f"  Empresa RUT   : {empresa_rut}")
    print(f"  Certificado   : {cert_path}")
    print(f"  Tipo DTE      : {tipo_dte}")
    print(f"  Cantidad      : {cantidad}")

    if not empresa_rut:
        sys.exit("❌ EMPRESA_RUT no configurado en .env")
    if not cert_path or not cert_pass:
        sys.exit("❌ Certificado no configurado en .env")
    cert_file = _PROJECT_ROOT / cert_path
    if not cert_file.exists():
        sys.exit(f"❌ Certificado no encontrado: {cert_file}")

    rut_num, dv = split_rut(empresa_rut)
    ambiente = AmbienteSII(ambiente_str)
    print(f"  RUT num/DV    : {rut_num}/{dv}")

    # 2. Certificado
    print("\n🔑 Cargando certificado...")
    try:
        cert = CertificadoDigital.desde_archivo(str(cert_file), cert_pass)
        print(f"  ✅ {cert.rut_emisor}")
    except Exception as e:
        sys.exit(f"❌ {e}")

    # 3. Token
    # Usa el cliente canónico core/sii.py::ClienteSII para la autenticación.
    # La firma de semilla hecha a mano en ClienteSOAP construía un <SignedInfo>
    # con prefijo ds: distinto del que firmaba -> el SII devolvía Estado=10
    # "Error Interno". ClienteSII firma de forma consistente y sí obtiene token.
    from core.sii import ClienteSII
    print("\n🎫 Obteniendo token SII (semilla → firma → token)...")
    cliente = ClienteSOAP(ambiente)  # se sigue usando para CrFolio
    try:
        token = ClienteSII(cert, ambiente).obtener_token()
        print(f"  ✅ Token: {token[:40]}...")
    except Exception as e:
        print(f"  ❌ Error al obtener token: {e}")
        traceback.print_exc()
        mostrar_cafs()
        print("\n⚠️  El script no pudo obtener token del SII.")
        print("   (El servicio de token puede dar 503 transitorios; reintentar.)")
        print("\n   Alternativa: descargue el CAF desde")
        print("   https://maullin.sii.cl/ y use load_cafs.py")
        sys.exit(1)

    # 4. CAF
    print(f"\n📄 Solicitando CAF tipo {tipo_dte} ({cantidad} folios)...")
    try:
        caf_xml = cliente.solicitar_caf(token, rut_num, dv, tipo_dte, cantidad)
        print(f"  ✅ CAF obtenido ({len(caf_xml)} chars)")
        print(f"  Primeros 500 chars:\n  {caf_xml[:500]}")
    except Exception as e:
        print(f"  ❌ Error al solicitar CAF: {e}")
        traceback.print_exc()
        print("\n⚠️  CrFolio.jws no está disponible en ambiente de certificación")
        print("   (Maullin). El endpoint redirige a página de error 404.")
        print("   En producción (Palena) el endpoint sí existe.")
        mostrar_cafs()
        sys.exit(1)

    # 5. Parsear
    print("\n🔍 Parseando CAF...")
    try:
        manejador = ManejadorCAF(caf_xml.encode("iso-8859-1"))
        datos = manejador.datos
    except Exception:
        try:
            manejador = ManejadorCAF(caf_xml.encode("utf-8"))
            datos = manejador.datos
        except Exception as e:
            sys.exit(f"❌ No se pudo parsear CAF: {e}")

    print(f"  Tipo DTE      : {datos.tipo_dte}")
    print(f"  RUT Emisor    : {datos.rut_emisor}")
    print(f"  Folios        : {datos.folio_desde} - {datos.folio_hasta}")
    print(f"  Fecha Aut     : {datos.fecha_autorizacion}")

    # 6. Guardar
    print("\n💾 Guardando XML...")
    rut_clean = datos.rut_emisor.replace("-", "").replace(".", "")
    filename = (
        f"FoliosSII{rut_clean}{datos.tipo_dte}"
        f"{datos.folio_desde}{datos.folio_hasta}.xml"
    )
    cafs_dir = _PROJECT_ROOT / "storage" / "cafs"
    cafs_dir.mkdir(parents=True, exist_ok=True)
    filepath = cafs_dir / filename
    with open(filepath, "w", encoding="iso-8859-1") as f:
        f.write(caf_xml)
    print(f"  ✅ {filepath}")

    # 7. BD
    print("\n🗄️  Cargando en BD...")
    if caf_existe(datos.rut_emisor, datos.tipo_dte, datos.folio_desde):
        print("  ⏭️  Ya existe. Saltando.")
    else:
        try:
            caf_id = registrar_caf(
                tipo_dte=datos.tipo_dte,
                rut_emisor=datos.rut_emisor,
                folio_desde=datos.folio_desde,
                folio_hasta=datos.folio_hasta,
                fecha_autorizacion=datos.fecha_autorizacion.isoformat(),
                caf_xml=caf_xml,
            )
            print(f"  ✅ Insertado ID={caf_id}")
        except Exception as e:
            sys.exit(f"❌ Error BD: {e}")

    # 8. Resultado
    mostrar_cafs()
    print("\n✅ Proceso completado.")


if __name__ == "__main__":
    main()
