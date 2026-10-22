"""
core/config.py

Módulo de configuración centralizado.
Lee variables de entorno desde archivo .env usando pydantic-settings.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Valor del secreto JWT que viene en el repo/.env.example: es PÚBLICO → inseguro.
# Si `jwt_secret_key` sigue en este valor, cualquiera puede forjar tokens.
JWT_DEFAULT_INSEGURO = "cambiar_esto_por_una_clave_segura_de_al_menos_32_caracteres"


class Settings(BaseSettings):
    """Configuración global de la aplicación DTE Chile API."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- Servidor ----
    app_name: str = Field(default="DTE Chile API", description="Nombre de la aplicación")
    app_version: str = Field(default="1.2.0", description="Versión de la aplicación")
    host: str = Field(default="0.0.0.0", description="Host del servidor")
    port: int = Field(default=8000, description="Puerto del servidor")
    debug: bool = Field(default=False, description="Modo debug")

    # ---- SII Chile ----
    sii_ambiente: str = Field(default="certificacion", description="Ambiente SII: certificacion o produccion")
    # Resolución de PRODUCCIÓN (la que autoriza a emitir de verdad).
    sii_fecha_resolucion: str = Field(default="2014-08-22", description="Fecha resolución SII (producción)")
    sii_numero_resolucion: int = Field(default=80, description="Número resolución SII (producción)")
    # Resolución de CERTIFICACIÓN: es OTRA. El SII rechaza la carátula con
    # `CRT-3-19 "Fecha/Numero Resolucion Invalido"` si se le manda la de producción.
    # NroResol siempre 0 en certificación; la fecha es la del inicio del proceso.
    sii_fecha_resolucion_cert: str = Field(default="2026-07-08", description="Fecha resolución en certificación")
    sii_numero_resolucion_cert: int = Field(default=0, description="Número resolución en certificación (siempre 0)")

    # User-Agent de las requests al SII. ⚠️ El **envío de boletas** (rahue/pangal) y **DTEUpload**
    # exigen que el UA contenga el token **`PROG 1.0`** (identificador de "programa" de la impl.
    # de referencia del SII, documentado como `Mozilla/4.0 (compatible; PROG 1.0; Windows NT)`).
    # Verificado en vivo: un UA CON `PROG 1.0` funciona; Chrome, Mozilla genérico y cualquier UA
    # SIN `PROG 1.0` dan un `401` engañoso. El resto del string (la URL) es libre. El portal de
    # folios NO es picky. Configurable por `SII_USER_AGENT`. Ver docs/LECCIONES-SII.md.
    sii_user_agent: str = Field(
        default="Mozilla/5.0 (compatible; PROG 1.0; +https://github.com/SebastianMoyano/dte-chile)",
        description="User-Agent para requests al SII (DEBE contener 'PROG 1.0'; el SII lo valida)")

    # ---- Lectura de correos del SII (skill `correos-sii`) ----
    # El detalle de un rechazo de FACTURA llega SOLO por correo: el SOAP (QueryEstUp) da
    # únicamente conteos, sin el código de error. Este token abre el endpoint propio del
    # usuario (Apps Script, solo lectura, últimos 20 correos). Rotarlo = cambiarlo en .env.
    # ⚠️ Es una credencial: nunca loguearlo ni devolverlo por la API.
    # Nombre con que ESTE motor está (o estará) registrado como software de facturación en el
    # SII ("Nombre del Software"). Sirve para distinguir, por empresa, si emite con este motor,
    # con OTRO proveedor (Nubox, Bsale, Haulmer…) o por el gratuito del SII. Varios: separados
    # por coma. Vacío = no se distingue proveedor (cualquier software propio cuenta como nuestro).
    nombre_software: str = Field(default="", description="Nombre(s) del software registrado en el SII para este motor, separados por coma")
    sii_mail_token: str = Field(default="", description="Token del endpoint de correos del SII")

    # ---- RVD (Registro de Ventas Diario de boletas) ----
    # El programador corre DENTRO del proceso (sin cron/launchd) para que el servidor sea
    # portable a Windows/macOS/Linux. Ver core/scheduler.py.
    rvd_scheduler_activo: bool = Field(default=True, description="Generar el RVD diario automáticamente")
    rvd_intervalo_seg: int = Field(default=1800, description="Cada cuánto revisa RVD pendientes (segundos)")

    # ---- Gestión AUTOMÁTICA de folios (estilo TUU/Haulmer) ----
    # Cuando los folios disponibles de un (rut, tipo) caen bajo el umbral, el motor pide un CAF
    # nuevo al SII solo (con el cert del mandatario) y lo carga, para que la emisión no se
    # detenga. Notifica el resultado por webhook GENÉRICO (el usuario lo cablea a lo que quiera;
    # el motor NO asume canal) + log. NUNCA correo (regla del proyecto).
    folios_auto_activo: bool = Field(default=True, description="Reponer folios automáticamente cuando bajan")
    folios_auto_intervalo_seg: int = Field(default=1800, description="Cada cuánto revisa folios (segundos)")
    folios_auto_umbral: int = Field(default=10, description="Reponer cuando los folios disponibles bajan de este número")
    folios_auto_cantidad: int = Field(default=50, description="Cuántos folios pedir al reponer")
    folios_auto_tipos: str = Field(default="39,41", description="Tipos de DTE a gestionar (coma-separado)")
    folios_auto_cooldown_seg: int = Field(default=21600, description="Espera mínima entre pedidos del mismo (rut,tipo) — anti-acaparamiento")
    folios_auto_cert_id: int = Field(default=0, description="cert_id de respaldo para pedir folios si el negocio no lo tiene registrado (0 = solo usar negocios)")
    notif_webhook_url: str = Field(default="", description="URL genérica para notificar eventos de folios (repuesto/bloqueado/error). Opcional; el usuario la cablea a su canal")

    # ---- Empresa (Emisor por defecto) ----
    empresa_rut: str = Field(default="", description="RUT de la empresa")
    empresa_razon_social: str = Field(default="", description="Razón social")
    empresa_giro: str = Field(default="", description="Giro comercial")
    empresa_codigo_actividad: Optional[int] = Field(default=None, description="Código actividad SII")
    empresa_direccion: str = Field(default="", description="Dirección")
    empresa_comuna: str = Field(default="", description="Comuna")
    empresa_ciudad: str = Field(default="", description="Ciudad")
    empresa_email: str = Field(default="", description="Email")
    empresa_telefono: str = Field(default="", description="Teléfono")

    @field_validator("empresa_codigo_actividad", mode="before")
    @classmethod
    def validate_codigo_actividad(cls, v: Any) -> Optional[int]:
        if v is None or v == "" or v == "None":
            return None
        try:
            return int(v)
        except (ValueError, TypeError):
            return None

    # ---- Certificado Digital ----
    certificado_path: str = Field(default="", description="Ruta al certificado .p12")
    certificado_password: str = Field(default="", description="Contraseña del certificado")

    # ---- Base de Datos ----
    database_url: str = Field(default="sqlite:///./dte_database.db", description="URL de conexión a BD")

    # ---- Seguridad / JWT ----
    jwt_secret_key: str = Field(
        default="cambiar_esto_por_una_clave_segura_de_al_menos_32_caracteres",
        description="Clave secreta JWT",
    )
    jwt_algorithm: str = Field(default="HS256", description="Algoritmo JWT")
    jwt_access_token_expire_minutes: int = Field(default=60, description="Expiración token JWT")

    # ---- Almacén de credenciales (keystore) ----
    # Clave Fernet (urlsafe-base64, 32 bytes) para cifrar los .p12 y claves en reposo.
    # Genera una con: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # DEFÍNELA en producción (env DTE_MASTER_KEY); si queda vacía se deriva del jwt_secret_key (menos seguro).
    master_key: str = Field(default="", alias="DTE_MASTER_KEY", description="Clave maestra Fernet del keystore")

    # ---- CORS ----
    cors_origins: str = Field(default="*", description="Orígenes CORS (separados por coma)")
    # Clave efímera que la app de escritorio (app/) inyecta en el entorno del motor que ella
    # misma arranca. Autentica como el usuario dueño de la cuenta. NO va en .env: la genera
    # la app en cada arranque y muere con el proceso. Ver docs/APP.md.
    app_shell_key: Optional[str] = Field(default=None, description="Clave efímera de la app de escritorio (solo por entorno)")
    # Hora fija para salida reproducible byte a byte (corpus dorado). NUNCA en producción.
    reloj_fijo: Optional[str] = Field(default=None, description="Hora fija ISO para pruebas reproducibles (RELOJ_FIJO)")

    # ---- Exposición pública acotada ----
    # Host público que SOLO puede ver el buscador de boletas (ej. "boletas.tu-dominio.cl").
    # Cuando el Host de la request coincide, la app redirige "/" → "/consulta" y responde 404
    # a todo lo que no sea "/consulta*" — así el subdominio público no expone /docs ni la API
    # (varios endpoints no piden JWT). Vacío = sin restricción (acceso interno completo).
    dominio_publico_boletas: str = Field(default="", description="Host público restringido al buscador de boletas")

    # ---- Logs ----
    log_level: str = Field(default="INFO", description="Nivel de log")
    log_file: str = Field(default="logs/dte_api.log", description="Archivo de log")

    # ---- Almacenamiento ----
    storage_path: str = Field(default="./storage", description="Directorio de almacenamiento")

    @property
    def cors_origins_list(self) -> List[str]:
        """Retorna la lista de orígenes CORS."""
        if self.cors_origins == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def empresa_configurada(self) -> bool:
        """Verifica si la empresa está configurada."""
        return bool(self.empresa_rut and self.empresa_razon_social)

    @property
    def certificado_configurado(self) -> bool:
        """Verifica si hay un certificado configurado."""
        return bool(self.certificado_path and self.certificado_password)

    @property
    def es_produccion(self) -> bool:
        """Postura de producción (emite DTE reales al SII de producción)."""
        return self.sii_ambiente == "produccion"

    @property
    def resolucion(self) -> tuple[str, int]:
        """`(fecha_resolucion, numero_resolucion)` **default** del ambiente activo.

        ⚠️ Para la carátula usa `core.resolucion.resolucion_emisor(rut)`, NO esto: la
        resolución la valida el SII **por RUT del emisor** (cada empresa tiene la suya) y
        mandar la de otra empresa/ambiente hace que rechace el sobre entero con
        `CRT-3-19 "Fecha/Numero Resolucion Invalido"`. Esta propiedad es solo el **fallback**
        (cuando no se puede consultar el registro del emisor). Nunca usar
        `sii_fecha_resolucion`/`sii_numero_resolucion` a pelo.
        """
        if self.es_produccion:
            return self.sii_fecha_resolucion, self.sii_numero_resolucion
        return self.sii_fecha_resolucion_cert, self.sii_numero_resolucion_cert

    def problemas_seguridad(self) -> List[str]:
        """Lista los defaults inseguros detectados (para avisar/abortar al arrancar).

        No incluye secretos en el texto — solo describe QUÉ corregir. La usa
        `main.py` al iniciar: avisa siempre, y en producción aborta si hay alguno.
        """
        p: List[str] = []
        if self.jwt_secret_key in ("", JWT_DEFAULT_INSEGURO) or len(self.jwt_secret_key) < 32:
            p.append("JWT_SECRET_KEY vacía, por defecto o < 32 caracteres "
                     "(genera una fuerte y ponla en el .env).")
        if not (self.master_key or os.environ.get("DTE_MASTER_KEY")):
            p.append("DTE_MASTER_KEY no definida: el keystore se cifra con una clave "
                     "derivada del JWT (define una clave Fernet propia).")
        if self.cors_origins.strip() == "*":
            p.append("CORS abierto a '*' (restríngelo a tus orígenes).")
        return p

    @property
    def storage_dir(self) -> Path:
        """Retorna el directorio de almacenamiento como Path."""
        return Path(self.storage_path)

    def ensure_directories(self) -> None:
        """Crea los directorios necesarios si no existen."""
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        Path(self.log_file).parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
