"""
core/sala_espera.py — Detecta la sala de espera (Queue-it) del SII.

El SII pone una fila virtual delante de varios servicios: `salaespera.sii.cl` o `*.queue-it.net`
(ver docs/SII-ENDPOINTS.md §5). Hoy se activa con JS en el navegador, así que `httpx` no la ve;
si el SII la pasa al servidor, las respuestas terminarían en la fila y el motor parsearía su
HTML como si fuera el servicio (igual que `ayudas/900.html`). Este hook corta antes con un
`SIIError` claro.

Uso: `httpx.Client(..., event_hooks=HOOKS)`. `ClienteReintentos` ya lo trae. El hook ve cada
respuesta, también los 302 intermedios, así que sirve con y sin `follow_redirects`.

Hermanos con el mismo texto: `nucleo/salaespera` (Go) y `es_sala_espera` en
`nucleo-rs/src/sii/http.rs` (Rust).
"""
from __future__ import annotations

from urllib.parse import urlsplit

import httpx

from core.errors import SIIError

MENSAJE = ("El SII tiene una fila de espera en este servicio (sala de espera): "
           "intenta de nuevo en unos minutos.")


def es_sala_espera(url: str) -> bool:
    """¿La URL es de la sala de espera del SII? Una ruta relativa nunca lo es."""
    if "://" not in url and not url.startswith("//"):
        return False
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return host in ("salaespera.sii.cl", "queue-it.net") or host.endswith(".queue-it.net")


def verificar_respuesta(resp: httpx.Response) -> None:
    """Hook de respuesta de httpx: levanta `SIIError` si el SII mandó a la fila."""
    if es_sala_espera(str(resp.url)) or es_sala_espera(resp.headers.get("location", "")):
        resp.close()
        raise SIIError(MENSAJE)


def limpiar(mensaje: str) -> str:
    """Deja solo `MENSAJE` si viene envuelto en prefijos ("No se pudo consultar el SII: …")."""
    return MENSAJE if MENSAJE in mensaje else mensaje


HOOKS = {"response": [verificar_respuesta]}
