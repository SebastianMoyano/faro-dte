"""
core/auth.py

Módulo de autenticación JWT para proteger los endpoints de la API.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt
from jwt import PyJWTError
from passlib.context import CryptContext

from core.config import settings
from core.models import obtener_usuario_por_username, actualizar_ultimo_acceso


pwd_context = CryptContext(schemes=["sha256_crypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    """Genera el hash de una contraseña."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifica si una contraseña coincide con su hash."""
    return pwd_context.verify(plain_password, hashed_password)


def crear_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Crea un token JWT de acceso.

    Args:
        data: Datos a incluir en el token (ej: {"sub": "username"}).
        expires_delta: Tiempo de expiración personalizado.

    Returns:
        Token JWT como string.
    """
    to_encode = data.copy()
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=settings.jwt_access_token_expire_minutes)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decodificar_token(token: str) -> Optional[dict]:
    """
    Decodifica y valida un token JWT.

    Args:
        token: Token JWT a decodificar.

    Returns:
        Payload del token si es válido, None si no.
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        return payload
    except PyJWTError:
        return None


async def obtener_usuario_actual(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[dict]:
    """
    Dependencia de FastAPI para obtener el usuario actual desde el token JWT.

    Uso:
        @router.get("/protegido")
        async def endpoint(usuario=Depends(obtener_usuario_actual)):
            ...
    """
    if not credentials:
        return None

    token = credentials.credentials
    payload = decodificar_token(token)

    if not payload:
        return None

    username = payload.get("sub")
    if not username:
        return None

    usuario = obtener_usuario_por_username(username)
    return usuario


def _usuario_dueno() -> Optional[dict]:
    """El usuario dueño de la instalación: el de menor id (la cuenta es única, ver
    docs/PLATAFORMA.md). Es con quien actúa la app de escritorio."""
    from core.models import listar_usuarios
    usuarios = sorted(listar_usuarios(), key=lambda u: u["id"])
    return usuarios[0] if usuarios else None


async def requerir_autenticacion(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    """
    Dependencia de FastAPI que REQUIERE autenticación: acepta un **JWT** (usuario) o una
    **API key** (`dte_...`, para integraciones/agentes). Lanza HTTP 401 si no hay ninguno válido.
    """
    usuario = await obtener_usuario_actual(credentials)
    if usuario:
        actualizar_ultimo_acceso(usuario["id"])
        return usuario
    # Clave efímera de la app de escritorio (APP_SHELL_KEY, solo por entorno): actúa como el
    # usuario dueño de la cuenta (el primero creado), así los recursos por cuenta (negocios,
    # certificados) son los suyos. Comparación en tiempo constante.
    if credentials and settings.app_shell_key and len(settings.app_shell_key) >= 24 \
            and secrets.compare_digest(credentials.credentials, settings.app_shell_key):
        dueno = _usuario_dueno()
        if dueno:
            actualizar_ultimo_acceso(dueno["id"])
            return dueno
    # No es un JWT válido → probar como API key.
    if credentials:
        from core.apikeys import verificar_api_key
        rec = verificar_api_key(credentials.credentials)
        if rec:
            return {"id": None, "username": f"apikey:{rec['nombre']}",
                    "tipo": "apikey", "api_key_id": rec["id"]}
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Se requiere un JWT o una API key válidos (Authorization: Bearer).",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def requerir_usuario(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    """Como `requerir_autenticacion` pero SOLO acepta un usuario real (JWT), no API keys. Para
    acciones sensibles como gestionar las propias API keys (que una key no cree más keys)."""
    usuario = await obtener_usuario_actual(credentials)
    if not usuario:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Esta acción requiere un usuario autenticado (JWT), no una API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    actualizar_ultimo_acceso(usuario["id"])
    return usuario
