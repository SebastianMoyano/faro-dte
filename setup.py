#!/usr/bin/env python3
"""
setup.py — Script de configuración inicial del DTE Chile API.

Ejecutar después de instalar dependencias:
    python setup.py

Este script:
1. Verifica que todas las dependencias estén instaladas
2. Crea el archivo .env si no existe
3. Inicializa la base de datos
4. Crea el usuario admin por defecto
5. Verifica la conexión con el SII (opcional)
"""

import os
import sys
import shutil
from pathlib import Path

# Colores
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
CYAN = "\033[96m"
RESET = "\033[0m"
BOLD = "\033[1m"


def ok(msg):
    print(f"  {GREEN}✓{RESET} {msg}")


def error(msg):
    print(f"  {RED}✗{RESET} {msg}")


def warn(msg):
    print(f"  {YELLOW}⚠{RESET} {msg}")


def info(msg):
    print(f"  {BLUE}ℹ{RESET} {msg}")


def titulo(msg):
    print(f"\n{BOLD}{CYAN}{'='*60}{RESET}")
    print(f"{BOLD}{CYAN}  {msg}{RESET}")
    print(f"{BOLD}{CYAN}{'='*60}{RESET}")


def paso(msg):
    print(f"\n{BOLD}{YELLOW}▶ {msg}{RESET}")


def verificar_dependencias():
    """Verifica que todas las dependencias estén instaladas."""
    paso("Verificando dependencias...")

    deps = {
        "fastapi": "Framework API REST",
        "uvicorn": "Servidor ASGI",
        "pydantic": "Validación de datos",
        "pydantic_settings": "Configuración por entorno",
        "lxml": "Manipulación de XML",
        "cryptography": "Criptografía RSA",
        "signxml": "Firma XMLDSig",
        "reportlab": "Generación de PDF",
        "pdf417gen": "Código de barras PDF417",
        "httpx": "Cliente HTTP",
        "jwt": "Tokens JWT",
        "passlib": "Hash de contraseñas",
        "bcrypt": "Algoritmo bcrypt",
    }

    faltantes = []
    for lib, desc in deps.items():
        try:
            __import__(lib)
            ok(f"{lib:<20} — {desc}")
        except ImportError:
            error(f"{lib:<20} — {desc} (NO INSTALADA)")
            faltantes.append(lib)

    if faltantes:
        error(f"Faltan {len(faltantes)} dependencias. Ejecuta: pip install -r requirements.txt")
        return False

    ok("Todas las dependencias están instaladas")
    return True


def crear_env():
    """Crea el archivo .env si no existe."""
    paso("Configurando archivo .env...")

    env_path = Path(".env")
    env_example = Path(".env.example")

    if env_path.exists():
        ok("El archivo .env ya existe")
        return True

    if not env_example.exists():
        error("No se encuentra .env.example")
        return False

    shutil.copy(env_example, env_path)

    # Inyectar secretos FUERTES generados (no dejar los placeholders inseguros del ejemplo).
    import re as _re
    import secrets as _secrets
    from cryptography.fernet import Fernet

    contenido = env_path.read_text(encoding="utf-8")
    jwt = _secrets.token_urlsafe(48)
    master = Fernet.generate_key().decode()
    for clave, valor in (("JWT_SECRET_KEY", jwt), ("DTE_MASTER_KEY", master)):
        if _re.search(rf"(?m)^{clave}=", contenido):
            contenido = _re.sub(rf"(?m)^{clave}=.*$", f"{clave}={valor}", contenido)
        else:
            contenido += f"\n{clave}={valor}\n"
    env_path.write_text(contenido, encoding="utf-8")

    ok("Archivo .env creado con secretos FUERTES generados (JWT + master key del keystore)")
    warn("Edita el archivo .env con los datos de tu empresa")
    return True


def inicializar_bd():
    """Inicializa la base de datos."""
    paso("Inicializando base de datos...")

    try:
        from core.database import init_db
        from core.models import listar_usuarios, crear_usuario
        from core.auth import hash_password

        init_db()
        ok("Base de datos inicializada correctamente")

        usuarios = listar_usuarios()
        if not usuarios:
            import os as _os
            import secrets as _secrets
            pwd = _os.environ.get("ADMIN_PASSWORD") or _secrets.token_urlsafe(12)
            crear_usuario(
                username="admin",
                hashed_password=hash_password(pwd),
                nombre_completo="Administrador",
                email="admin@localhost",
            )
            ok("Usuario admin creado")
            warn(f"Credenciales: admin / {pwd}")
            warn("Guarda esta contraseña (se muestra solo aquí) y cámbiala tras el primer login.")
        else:
            ok(f"Ya existen {len(usuarios)} usuario(s) registrado(s)")

        return True
    except Exception as e:
        error(f"Error al inicializar la base de datos: {e}")
        return False


def crear_directorios():
    """Crea los directorios necesarios."""
    paso("Creando directorios...")

    dirs = ["storage", "logs", "storage/dtes", "storage/pdfs", "storage/cafs"]
    for d in dirs:
        Path(d).mkdir(parents=True, exist_ok=True)
        ok(f"Directorio '{d}' listo")

    return True


def verificar_config():
    """Verifica la configuración actual."""
    paso("Verificando configuración...")

    try:
        from core.config import settings

        info(f"App: {settings.app_name} v{settings.app_version}")
        info(f"Ambiente SII: {settings.sii_ambiente}")
        info(f"Base de datos: {settings.database_url}")
        info(f"Storage: {settings.storage_path}")

        if settings.empresa_configurada:
            ok(f"Empresa configurada: {settings.empresa_rut}")
        else:
            warn("Empresa NO configurada. Edita el archivo .env")

        if settings.certificado_configurado:
            ok("Certificado digital configurado")
        else:
            warn("Certificado digital NO configurado. Se puede enviar por API")

        return True
    except Exception as e:
        error(f"Error al verificar configuración: {e}")
        return False


def mostrar_resumen():
    """Muestra el resumen final."""
    titulo("CONFIGURACIÓN COMPLETA")
    print(f"""
  {GREEN}{BOLD}¡DTE Chile API está listo para usar!{RESET}

  {BOLD}Próximos pasos:{RESET}

  1. {YELLOW}Edita el archivo .env{RESET} con los datos de tu empresa:
     - EMPRESA_RUT, EMPRESA_RAZON_SOCIAL, etc.
     - SII_AMBIENTE (certificacion o produccion)

  2. {YELLOW}Inicia el servidor:{RESET}
     {BOLD}uvicorn main:app --reload --port 8000{RESET}

  3. {YELLOW}Accede a la documentación:{RESET}
     {BLUE}http://localhost:8000/docs{RESET}  (Swagger UI)
     {BLUE}http://localhost:8000/redoc{RESET} (ReDoc)

  4. {YELLOW}Autentícate:{RESET}
     POST /api/v1/auth/login
     {{
       "username": "admin",
       "password": "<tu-password>"
     }}

  {BOLD}Para producción con Docker:{RESET}
     {BOLD}docker-compose up -d{RESET}

  {BOLD}Archivos importantes:{RESET}
     .env          → Configuración (editar)
     dte_database.db → Base de datos (se crea automáticamente)
     storage/      → DTEs y PDFs generados
     logs/         → Logs de la aplicación
""")


def main():
    """Ejecuta el setup completo."""
    titulo("DTE Chile API - Configuración Inicial")

    pasos = [
        ("Dependencias", verificar_dependencias),
        ("Archivo .env", crear_env),
        ("Directorios", crear_directorios),
        ("Base de datos", inicializar_bd),
        ("Configuración", verificar_config),
    ]

    resultados = {}
    for nombre, func in pasos:
        try:
            resultados[nombre] = func()
        except Exception as e:
            error(f"Error en {nombre}: {e}")
            resultados[nombre] = False

    # Resumen
    paso("Resumen")
    for nombre, resultado in resultados.items():
        if resultado:
            ok(f"{nombre}: OK")
        else:
            error(f"{nombre}: FALLÓ")

    todos_ok = all(resultados.values())
    if todos_ok:
        mostrar_resumen()
    else:
        error("Algunos pasos fallaron. Revisa los errores arriba.")
        sys.exit(1)


if __name__ == "__main__":
    main()
