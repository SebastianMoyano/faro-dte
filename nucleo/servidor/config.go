// Package servidor es el motor HTTP en Go (fase 7 del port): atiende /api/v1 con el mismo
// contrato que el motor Python y reenvía a Python lo que todavía no está portado (patrón
// estrangulador). La app y el navegador no cambian: mismo puerto, mismas rutas, mismos JSON.
// Cada ruta portada se compara contra Python con tools/contrato_api.py antes de apagarla ahí.
package servidor

import (
	"bufio"
	"net/http"
	"os"
	"strconv"
	"strings"
)

// Config es lo que el servidor necesita del `.env` del motor (mismos nombres que
// core/config.py; las variables de entorno ganan sobre el archivo, como en pydantic-settings).
type Config struct {
	AppName, AppVersion   string
	DatabasePath          string // DATABASE_URL sin el prefijo sqlite:///
	StoragePath           string
	JWTSecret             string
	JWTAlgoritmo          string
	AppShellKey           string // solo por entorno (APP_SHELL_KEY)
	SIIAmbiente           string
	EmpresaRut            string
	EmpresaRazonSocial    string
	CORSOrigins           string
	JWTExpiraMin          int
	MasterKey             string // DTE_MASTER_KEY (Fernet); vacía = derivada del secreto JWT, como Python
	ResolucionFecha       string // SII_FECHA_RESOLUCION (producción)
	ResolucionNumero      int
	ResolucionFechaCert   string // SII_FECHA_RESOLUCION_CERT
	ResolucionNumeroCert  int
	MotorPython           string          // URL del motor Python para el reenvío ("" = sin reenvío)
	CertificadoPath       string          // CERTIFICADO_PATH (.p12 del emisor, respaldo cuando la petición no trae uno)
	CertificadoPassword   string          // CERTIFICADO_PASSWORD
	RelojFijo             string          // RELOJ_FIJO: hora fija ISO para salidas reproducibles (nunca en producción)
	RVDSchedulerActivo    bool            // RVD_SCHEDULER_ACTIVO
	RVDIntervaloSeg       int             // RVD_INTERVALO_SEG
	NombreSoftware        string          // NOMBRE_SOFTWARE: nombre(s) con que este motor se registra en el SII (coma)
	AdminPassword         string          // ADMIN_PASSWORD: clave del usuario admin al crearlo (vacía = aleatoria, se registra una vez)
	DominioPublicoBoletas string          // DOMINIO_PUBLICO_BOLETAS: Host público acotado al buscador /consulta
	AppFS                 http.FileSystem // la interfaz (/app) desde un FS embebido; si es nil se usa CarpetaApp
	CarpetaApp            string          // app/frontend/dist, servida en /app
	Valores               map[string]string
}

// CargarConfig lee el .env (si existe) y el entorno. Los secretos nunca se registran en el log.
func CargarConfig(rutaEnv string) (*Config, error) {
	vals := map[string]string{}
	if rutaEnv != "" {
		f, err := os.Open(rutaEnv)
		if err == nil {
			sc := bufio.NewScanner(f)
			for sc.Scan() {
				l := strings.TrimSpace(sc.Text())
				if l == "" || strings.HasPrefix(l, "#") {
					continue
				}
				k, v, ok := strings.Cut(l, "=")
				if !ok {
					continue
				}
				k = strings.TrimSpace(k)
				v = strings.TrimSpace(v)
				if len(v) >= 2 && ((v[0] == '"' && v[len(v)-1] == '"') || (v[0] == '\'' && v[len(v)-1] == '\'')) {
					v = v[1 : len(v)-1]
				}
				vals[k] = v
			}
			f.Close()
		}
	}
	for _, kv := range os.Environ() { // el entorno gana
		if k, v, ok := strings.Cut(kv, "="); ok {
			vals[k] = v
		}
	}
	get := func(k, def string) string {
		if v, ok := vals[k]; ok && v != "" {
			return v
		}
		return def
	}
	c := &Config{
		AppName: get("APP_NAME", "DTE Chile API"), AppVersion: get("APP_VERSION", "1.2.0"),
		DatabasePath:          strings.TrimPrefix(get("DATABASE_URL", "sqlite:///./dte_database.db"), "sqlite:///"),
		StoragePath:           get("STORAGE_PATH", "./storage"),
		JWTSecret:             get("JWT_SECRET_KEY", ""),
		JWTAlgoritmo:          get("JWT_ALGORITHM", "HS256"),
		AppShellKey:           os.Getenv("APP_SHELL_KEY"),
		SIIAmbiente:           get("SII_AMBIENTE", "certificacion"),
		EmpresaRut:            get("EMPRESA_RUT", ""),
		EmpresaRazonSocial:    get("EMPRESA_RAZON_SOCIAL", ""),
		CORSOrigins:           get("CORS_ORIGINS", "*"),
		JWTExpiraMin:          entero(get("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "60"), 60),
		MasterKey:             get("DTE_MASTER_KEY", ""),
		ResolucionFecha:       get("SII_FECHA_RESOLUCION", "2014-08-22"),
		ResolucionNumero:      entero(get("SII_NUMERO_RESOLUCION", "80"), 80),
		ResolucionFechaCert:   get("SII_FECHA_RESOLUCION_CERT", "2026-07-08"),
		ResolucionNumeroCert:  entero(get("SII_NUMERO_RESOLUCION_CERT", "0"), 0),
		CertificadoPath:       get("CERTIFICADO_PATH", ""),
		CertificadoPassword:   get("CERTIFICADO_PASSWORD", ""),
		RelojFijo:             get("RELOJ_FIJO", ""),
		RVDSchedulerActivo:    !strings.EqualFold(get("RVD_SCHEDULER_ACTIVO", "true"), "false") && get("RVD_SCHEDULER_ACTIVO", "true") != "0",
		RVDIntervaloSeg:       entero(get("RVD_INTERVALO_SEG", "1800"), 1800),
		NombreSoftware:        get("NOMBRE_SOFTWARE", ""),
		AdminPassword:         get("ADMIN_PASSWORD", ""),
		DominioPublicoBoletas: get("DOMINIO_PUBLICO_BOLETAS", ""),
		Valores:               vals,
	}
	if c.JWTSecret == "" {
		c.JWTSecret = "cambiar_esto_por_una_clave_segura_de_al_menos_32_caracteres" // default de core/config.py
	}
	return c, nil
}

func entero(s string, def int) int {
	n, err := strconv.Atoi(strings.TrimSpace(s))
	if err != nil {
		return def
	}
	return n
}

// EmpresaConfigurada es settings.empresa_configurada.
func (c *Config) EmpresaConfigurada() bool { return c.EmpresaRut != "" && c.EmpresaRazonSocial != "" }
