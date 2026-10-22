"""
core/reintentos.py — Cliente HTTP con reintentos/backoff para el SII.

El SII devuelve fallos TRANSITORIOS bajo carga: HTTP 429 (rate-limit por IP), 502/503/504
(servidor saturado) y cortes de red. `ClienteReintentos` es un `httpx.Client` que
reintenta SOLO esos casos, con **backoff exponencial + jitter completo** y respetando el
header `Retry-After` cuando viene. No reintenta 4xx (salvo 429) ni respuestas de negocio.

Uso: reemplaza `httpx.Client(...)` por `ClienteReintentos(...)`; acepta los mismos
kwargs (verify, timeout, cookies, headers, follow_redirects, …) más:
    max_reintentos (default 3), backoff_base (0.5s), backoff_tope (8s).

Nota sobre POST: el SII es idempotente por folio (un reenvío del mismo DTE se rechaza con
DTE-3-101, no se duplica) y las consultas son de solo lectura, así que reintentar POST es
seguro aquí.
"""
from __future__ import annotations

import logging
import random
import time

import httpx

from core.sala_espera import verificar_respuesta

logger = logging.getLogger("dte.http")

# Fallos que vale la pena reintentar: rate-limit + 5xx de saturación.
STATUS_TRANSITORIOS = frozenset({429, 502, 503, 504})


class ClienteReintentos(httpx.Client):
    """`httpx.Client` que reintenta fallos transitorios del SII con backoff + jitter."""

    def __init__(self, *args, max_reintentos: int = 3, backoff_base: float = 0.5,
                 backoff_tope: float = 8.0, **kwargs):
        self._max = max(0, max_reintentos)
        self._base = backoff_base
        self._tope = backoff_tope
        # Toda respuesta pasa por la detección de la sala de espera del SII (core/sala_espera.py).
        hooks = dict(kwargs.pop("event_hooks", None) or {})
        hooks["response"] = [verificar_respuesta, *hooks.get("response", [])]
        super().__init__(*args, event_hooks=hooks, **kwargs)

    def _espera(self, intento: int, retry_after: str | None) -> float:
        """Segundos a esperar antes del siguiente intento (0-based)."""
        if retry_after:
            try:
                return min(float(retry_after), self._tope)
            except ValueError:
                pass  # Retry-After en formato fecha: caemos al backoff normal
        # Full jitter (AWS): uniform(0, min(tope, base * 2**intento)).
        return random.uniform(0, min(self._tope, self._base * (2 ** intento)))

    def send(self, request: httpx.Request, **kwargs) -> httpx.Response:  # type: ignore[override]
        ultimo_exc: Exception | None = None
        for intento in range(self._max + 1):
            try:
                resp = super().send(request, **kwargs)
            except httpx.TransportError as e:
                ultimo_exc = e
                if intento >= self._max:
                    raise
                espera = self._espera(intento, None)
                logger.warning("SII red %s en %s %s — reintento %d/%d en %.1fs",
                               type(e).__name__, request.method, request.url,
                               intento + 1, self._max, espera)
                time.sleep(espera)
                continue

            if resp.status_code in STATUS_TRANSITORIOS and intento < self._max:
                espera = self._espera(intento, resp.headers.get("Retry-After"))
                logger.warning("SII HTTP %d en %s %s — reintento %d/%d en %.1fs",
                               resp.status_code, request.method, request.url,
                               intento + 1, self._max, espera)
                resp.close()
                time.sleep(espera)
                continue

            return resp

        # Solo se llega aquí si el último intento fue una excepción de red.
        raise ultimo_exc  # pragma: no cover
