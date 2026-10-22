"""
api/errors.py — Manejo de errores y observabilidad transversal de la API REST.

Aporta:
  1. Un **esquema de error uniforme** (`ErrorEnvelope`) para TODA respuesta de error:
        {"error": {"codigo", "mensaje", "detalle", "request_id"}}
  2. **Handlers globales** que traducen:
        - `DTEChileError` (dominio)      → status del error + envelope
        - `RequestValidationError`       → 422 + envelope (codigo="validacion")
        - `HTTPException`                → status + envelope
        - `Exception` (no manejada)      → 500 + envelope (traza al log, NO al cliente)
  3. Middleware de **request-id + logging de acceso** (método, ruta, status, ms).

Se instala con `registrar_manejo_errores(app)` desde `main.py`. Con esto, TODAS las
rutas — incluso las que aún levantan excepciones crudas — devuelven un error
consistente, sin filtrar trazas internas.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.errors import DTEChileError
from core.sala_espera import limpiar

logger = logging.getLogger("dte.api")
_acceso = logging.getLogger("dte.acceso")


class ErrorBody(BaseModel):
    """Cuerpo del error (dentro de la clave ``error``)."""

    codigo: str = Field(..., description="Identificador estable del error (para máquinas).",
                        examples=["sin_folios"])
    mensaje: str = Field(..., description="Descripción legible en español.")
    detalle: Optional[dict[str, Any]] = Field(
        None, description="Contexto estructurado del error (opcional).")
    request_id: Optional[str] = Field(
        None, description="ID de la petición, para correlacionar con los logs.")


class ErrorEnvelope(BaseModel):
    """Envoltorio uniforme de todas las respuestas de error de la API."""

    error: ErrorBody


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or "-"


def _json_error(status_code: int, codigo: str, mensaje: str, *,
                request: Request, detalle: Optional[dict] = None) -> JSONResponse:
    envelope = ErrorEnvelope(error=ErrorBody(
        codigo=codigo, mensaje=limpiar(mensaje), detalle=detalle, request_id=_request_id(request)))
    return JSONResponse(status_code=status_code, content=envelope.model_dump(exclude_none=True))


def registrar_manejo_errores(app: FastAPI) -> None:
    """Instala middleware de request-id/logging y los handlers de error globales."""

    @app.middleware("http")
    async def _request_context(request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        inicio = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # Lo capturan los exception_handlers de abajo; aquí solo medimos/relanzamos.
            ms = (time.perf_counter() - inicio) * 1000
            _acceso.info("%s %s → EXC (%.0f ms) [rid=%s]",
                         request.method, request.url.path, ms, request.state.request_id)
            raise
        ms = (time.perf_counter() - inicio) * 1000
        response.headers["X-Request-ID"] = request.state.request_id
        _acceso.info("%s %s → %s (%.0f ms) [rid=%s]",
                     request.method, request.url.path, response.status_code, ms,
                     request.state.request_id)
        return response

    @app.exception_handler(DTEChileError)
    async def _dominio(request: Request, exc: DTEChileError):
        # Errores de dominio esperados: log a nivel info/warning, sin traza de estrés.
        logger.info("dominio [%s] %s [rid=%s]", exc.codigo, exc.mensaje, _request_id(request))
        return _json_error(exc.http_status, exc.codigo, exc.mensaje,
                           request=request, detalle=exc.detalle or None)

    @app.exception_handler(RequestValidationError)
    async def _validacion(request: Request, exc: RequestValidationError):
        # Sin `input`: el body puede traer credenciales (p.ej. clave_tributaria del canal MIPYME)
        # y no deben volver en la respuesta ni quedar en logs de proxy/navegador.
        errores = [{k: v for k, v in e.items() if k != "input"} for e in exc.errors()]
        return _json_error(422, "validacion", "Los datos enviados no son válidos.",
                           request=request, detalle={"errores": errores})

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        codigo = {401: "autenticacion", 403: "autorizacion", 404: "no_encontrado",
                  409: "conflicto", 429: "demasiadas_solicitudes"}.get(exc.status_code, "http_error")
        mensaje = exc.detail if isinstance(exc.detail, str) else "Error de solicitud."
        return _json_error(exc.status_code, codigo, mensaje, request=request)

    @app.exception_handler(Exception)
    async def _no_manejada(request: Request, exc: Exception):
        # Bug o fallo inesperado: traza COMPLETA al log, mensaje genérico al cliente.
        logger.exception("no manejada: %s [rid=%s]", exc, _request_id(request))
        return _json_error(500, "error_interno",
                           "Ocurrió un error interno. Reporta el request_id al soporte.",
                           request=request)
