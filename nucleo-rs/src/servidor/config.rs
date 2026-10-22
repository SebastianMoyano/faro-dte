//! `Config`: lo que el servidor necesita del `.env` del motor (mismos nombres que
//! `core/config.py`; las variables de entorno ganan sobre el archivo, como pydantic-settings).

use std::collections::BTreeMap;

#[derive(Debug, Clone, Default)]
pub struct Config {
    pub app_name: String,
    pub app_version: String,
    /// DATABASE_URL sin el prefijo sqlite:///
    pub database_path: String,
    pub storage_path: String,
    pub jwt_secret: String,
    pub jwt_algoritmo: String,
    /// solo por entorno (APP_SHELL_KEY)
    pub app_shell_key: String,
    pub sii_ambiente: String,
    pub empresa_rut: String,
    pub empresa_razon_social: String,
    pub cors_origins: String,
    pub jwt_expira_min: i64,
    /// DTE_MASTER_KEY (Fernet); vacía = derivada del secreto JWT, como Python
    pub master_key: String,
    /// `boveda.json` junto al .env (bóveda con PIN de la app); vacío = sin bóveda
    pub boveda_path: String,
    pub resolucion_fecha: String,
    pub resolucion_numero: i64,
    pub resolucion_fecha_cert: String,
    pub resolucion_numero_cert: i64,
    /// URL del motor Python para el reenvío ("" = sin reenvío)
    pub motor_python: String,
    pub certificado_path: String,
    pub certificado_password: String,
    /// RELOJ_FIJO: hora fija ISO para salidas reproducibles (nunca en producción)
    pub reloj_fijo: String,
    pub rvd_scheduler_activo: bool,
    pub rvd_intervalo_seg: i64,
    pub nombre_software: String,
    pub admin_password: String,
    pub dominio_publico_boletas: String,
    /// app/frontend/dist, servida en /app
    pub carpeta_app: String,
    pub valores: BTreeMap<String, String>,
}

/// `.env` → pares (comillas simples/dobles fuera; `#` comentarios).
pub fn leer_env(texto: &str) -> BTreeMap<String, String> {
    let mut vals = BTreeMap::new();
    for l in texto.lines() {
        let l = l.trim();
        if l.is_empty() || l.starts_with('#') {
            continue;
        }
        let Some((k, v)) = l.split_once('=') else {
            continue;
        };
        let (k, mut v) = (k.trim(), v.trim());
        if v.len() >= 2
            && ((v.starts_with('"') && v.ends_with('"'))
                || (v.starts_with('\'') && v.ends_with('\'')))
        {
            v = &v[1..v.len() - 1];
        }
        vals.insert(k.to_string(), v.to_string());
    }
    vals
}

/// `CargarConfig`: lee el .env (si existe) y el entorno. Los secretos nunca van al log.
pub fn cargar(ruta_env: &str) -> Config {
    let mut vals = BTreeMap::new();
    if !ruta_env.is_empty() {
        if let Ok(t) = std::fs::read_to_string(ruta_env) {
            vals = leer_env(&t);
        }
    }
    for (k, v) in std::env::vars() {
        vals.insert(k, v);
    }
    let get = |k: &str, def: &str| -> String {
        match vals.get(k) {
            Some(v) if !v.is_empty() => v.clone(),
            _ => def.to_string(),
        }
    };
    let entero = |k: &str, def: i64| -> i64 { get(k, "").trim().parse().unwrap_or(def) };
    let rvd = get("RVD_SCHEDULER_ACTIVO", "true");
    let mut c = Config {
        app_name: get("APP_NAME", "DTE Chile API"),
        app_version: get("APP_VERSION", "1.2.0"),
        database_path: get("DATABASE_URL", "sqlite:///./dte_database.db")
            .trim_start_matches("sqlite:///")
            .to_string(),
        storage_path: get("STORAGE_PATH", "./storage"),
        jwt_secret: get("JWT_SECRET_KEY", ""),
        jwt_algoritmo: get("JWT_ALGORITHM", "HS256"),
        app_shell_key: std::env::var("APP_SHELL_KEY").unwrap_or_default(),
        sii_ambiente: get("SII_AMBIENTE", "certificacion"),
        empresa_rut: get("EMPRESA_RUT", ""),
        empresa_razon_social: get("EMPRESA_RAZON_SOCIAL", ""),
        cors_origins: get("CORS_ORIGINS", "*"),
        jwt_expira_min: entero("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", 60),
        master_key: get("DTE_MASTER_KEY", ""),
        boveda_path: if ruta_env.is_empty() {
            String::new()
        } else {
            std::path::Path::new(ruta_env)
                .parent()
                .map(|p| p.join("boveda.json").to_string_lossy().into_owned())
                .unwrap_or_default()
        },
        resolucion_fecha: get("SII_FECHA_RESOLUCION", "2014-08-22"),
        resolucion_numero: entero("SII_NUMERO_RESOLUCION", 80),
        resolucion_fecha_cert: get("SII_FECHA_RESOLUCION_CERT", "2026-07-08"),
        resolucion_numero_cert: entero("SII_NUMERO_RESOLUCION_CERT", 0),
        motor_python: String::new(),
        certificado_path: get("CERTIFICADO_PATH", ""),
        certificado_password: get("CERTIFICADO_PASSWORD", ""),
        reloj_fijo: get("RELOJ_FIJO", ""),
        rvd_scheduler_activo: !rvd.eq_ignore_ascii_case("false") && rvd != "0",
        rvd_intervalo_seg: entero("RVD_INTERVALO_SEG", 1800),
        nombre_software: get("NOMBRE_SOFTWARE", ""),
        admin_password: get("ADMIN_PASSWORD", ""),
        dominio_publico_boletas: get("DOMINIO_PUBLICO_BOLETAS", ""),
        carpeta_app: String::new(),
        valores: vals,
    };
    if c.jwt_secret.is_empty() {
        c.jwt_secret = "cambiar_esto_por_una_clave_segura_de_al_menos_32_caracteres".into();
    }
    c
}

impl Config {
    /// `settings.empresa_configurada`.
    pub fn empresa_configurada(&self) -> bool {
        !self.empresa_rut.is_empty() && !self.empresa_razon_social.is_empty()
    }
    pub fn valor(&self, k: &str) -> String {
        self.valores.get(k).cloned().unwrap_or_default()
    }
}

/// `RutaApp`: app/frontend/dist a partir de la raíz del repo (desarrollo).
pub fn ruta_app(raiz: &str) -> String {
    let p = std::path::Path::new(raiz)
        .join("app")
        .join("frontend")
        .join("dist");
    if p.is_dir() {
        p.to_string_lossy().into_owned()
    } else {
        String::new()
    }
}
