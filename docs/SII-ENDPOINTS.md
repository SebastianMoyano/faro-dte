# Endpoints del portal SII (observados en captura)

> **Qué es.** Inventario de los servicios internos que usan los módulos web del SII, sacado de
> capturas reales con `tools/captura-sii`. Sirve para entender a qué se le habla cuando se
> automatiza un trámite por `httpx` (como ya hace `core/sii_mipyme.py` y `core/rcv.py`).
>
> **Confianza.** Esto es **observado en captura, no verificado contra código ni estable**: el
> SII cambia estas rutas sin avisar. Trátalo como pista, no como contrato. Lo verificado contra
> el SII vivo (firma, envío) vive en [`LECCIONES-SII.md`](LECCIONES-SII.md), no aquí.
>
> Captura base: `www.sii.cl`, 2026-10-08, sesión de una empresa real entrando con **RUT empresa +
> clave tributaria** (qué empresa: en la memoria del proyecto, no aquí; ley L14). Los RUT,
> correos y celulares de los ejemplos son **ficticios** (reemplazo de datos personales de la
> extensión) y los números de verificación, folio o trámite se muestran como `<n>`.

## 1. Dos estilos de API conviven

| Estilo | Quién lo usa | Forma |
|---|---|---|
| **SDI/AA facade** (JSON) | módulos nuevos (Angular): verificación de actividades, MIA, TIBVS… | POST JSON con sobre `metaData`/`data` |
| **GWT-RPC** (pipe) | módulos viejos: `inicioactividades`, y el F29 (ver [`modulo RCV`]) | POST texto `5\|0\|4\|...\|`, respuesta `//OK[...]` |

### Sobre SDI/AA (el patrón reutilizable)
Request:
```json
{"metaData":{"namespace":"cl.sii.sdi.lob.<area>...FacadeService/<metodo>",
             "conversationId":"<id>","transactionId":"<uuid>"},
 "data":{ ... }}
```
Response: el mismo sobre, con `data` (el resultado), `errors` (null si ok) y `page`. El
`conversationId` se mantiene entre llamadas del mismo módulo; el `transactionId` cambia por
llamada. El `namespace` es literal: `<FacadeService>/<metodo>` dice exactamente qué se invoca.

## 2. Autenticación

`POST /common-1.0/services/autConfDataService/obtieneConf` devuelve las URLs de login:
```json
{"data":{"autCc":"https://zeusr.sii.cl/AUT2000/InicioAutenticacion/IngresoCertificado.html",
         "autCp":"https://zeusr.sii.cl/AUT2000/InicioAutenticacion/IngresoRutClave.html",
         "autFc":"http://vulcano.sii.cl/AUTFUN/IngresoRutClaveOItoken.html",
         "autFp":"http://vulcano.sii.cl/AUTFUN/IngresoRutClaveOItoken.html"}}
```
- `Cc`/`Cp` = contribuyente por **C**ertificado / por **C**lave (zeus). `Fc`/`Fp` = funcionario (vulcano).
- La sesión queda en **zeus/AUT2000**; de ahí cuelgan los módulos. `zeus.sii.cl/cgi_AUT2000/autTokenData.cgi` sin `sid` válido da 404.

`GET /common-1.0/services/aaSessionService/load` dice quién está autenticado:
```json
{"data":{"usuario":"<id>","rut":"<n>","dv":"7","perfiles":[],
         "funcionario":false,"contribuyente":true,
         "hayRepresentacion":false,"rutDvRepresentado":null,"subrogados":null}}
```
Útil para saber, con una sesión por clave, el RUT, si es funcionario y si hay representación.

**Ojo:** no todo hereda la sesión de clave tributaria. `mandatario.w.sii.cl/mandatarios-digitales`
pidió sesión aparte: `GET /app/session/status?originalUrl=.../mandatarios-digitales/home` → **401**.

## 3. Módulos observados

| Módulo | Namespace raíz del facade | Auth | Qué entrega |
|---|---|---|---|
| Verificación de Actividades | `cl.sii.sdi.lob.did.verificacionactividades.data.api.interfaces.FacadeService` | clave | estado de la verificación de actividad, contribuyente, domicilio, docs a acreditar |
| TIBVS (**timbraje virtual**) | `cl.sii.sdi.lob.ar.tibvs.ui.api.interfaces.FacadeService` | clave | datos del contribuyente para timbrar, últimos timbrajes, tipos de documento |
| MIA (modificaciones) | `cl.sii.sdi.lob.riac.modificacionesia.data.api.interfaces.ContribuyenteService` | clave | datos del contribuyente para modificar |
| Inicio de Actividades | `cl.sii.sdi.oim.inicioactividades...` (GWT-RPC) | clave | catálogos (calidad ocupación, etc.) |

### Verificación de Actividades — métodos vistos
- `recuperarContribuyenteByRutDvV2` — `data:{rut,dv}` → razón social, giro, fecha constitución, `tctbCodigo` (2 = persona jurídica).
- `recuperarDireccionPrincipalByRutDvV2` — domicilio principal.
- `recuperarDocAcreditar` — catálogo de documentos para acreditar actividad.
- `consultarVerificacionInternet` — **el estado de la(s) verificación(es)**. Ejemplo real (recortado):
  ```json
  {"data":{"codigoError":0,"domicilio":"LOS MORROS BUCHUPUREO",
    "converiBO":[{"verNumVerificacion":<n>,"tipo":"VIA",
      "motivo":"INGRESO DE UNA SOLICITUD DE VERIFICACION","resultadoVia":"NEGATIVA",
      "fechaSolicitud":"25-04-2025","verEstadoVerificacion":"ANU",
      "descEstadoVerificacion":"ANULADO","resultadoFinal":"NO AUTORIZADO",
      "comentarios":"...antecedentes tributarios (ingresos 100% exentos)... se procede a
                     rechazar la verificacion, dado que no requiere emitir d[ocumentos]...",
      "cabeceraTramite":{"ttra_codigo":"4001","descripcion":"Verificación Actividad general",
                         "triac_folio":"<n>"}}]}}
  ```
  → para este RUT la verificación general salió **ANULADA / NO AUTORIZADO** por ingresos 100%
  exentos. Ver la nota de memoria del caso (verificación de actividades no autorizada) y [`CERTIFICACION.md`](CERTIFICACION.md).

### TIBVS — `recuperarInicialTIBVS`
`data:{rut,dv}` → `{razonSocial, glosaActividad, domicilio, actividades:[{codigo,descripcion,afectoIva,...}]}`.
Es el camino **web** del timbraje (folios/CAF); territorio del bloqueo T33/T61.

## 4. Cómo se automatiza (si se decide)
Un cliente fino `core/sii_sdi.py` por `httpx` que arme el sobre `metaData/data` y reuse la
sesión por clave (molde de `core/sii_mipyme.py` + `core/rcv.py`). Encima, helpers por módulo.
**No está hecho**: solo se abre cuando haya un trámite concreto que valga la pena automatizar.

## 5. Sala de espera (Queue-it) — `salaespera.sii.cl`

El SII pone una **fila virtual** delante de varios servicios. Ejemplo:
`https://salaespera.sii.cl/?c=sii&e=consultaterceros&t=https%3A%2F%2Fwww2.sii.cl%2Fstc%2Fnoauthz&cid=es-CL`
→ `c` = cliente Queue-it (`sii`), `e` = la fila (evento), `t` = adónde vuelve al salir de la fila.

**Cómo funciona hoy (observado 2026-10-19).** La integración es **del lado del navegador**: las
páginas cargan `static.queue-it.net/script/queueconfigloader.min.js` (`data-queueit-c='sii'`), que
baja `https://assets.queue-it.net/sii/integrationconfig/javascript/queueclientConfig.js` y, si la
URL de la página calza con un disparador, manda al navegador a `salaespera.sii.cl`. Las mismas
URLs pedidas sin JS (curl/`httpx`) responden directo, sin 302 a la fila. Por eso **al motor por
`httpx` no le afecta hoy**; sí afecta a todo lo que corre en un navegador (capturas con
`tools/captura-sii`, Playwright, enlaces que la app abre para la persona).

**Filas configuradas** (`eventId` → disparador "URL contiene"; lista viva en el JS de arriba):

| fila | URL que la dispara | ¿la toca el motor? |
|---|---|---|
| `autenticacionmisii` | `misiir.sii.cl/cgi_misii/siihome.cg` | **sí** (login por clave, `sii_portal`/`sii_mipyme`); hoy sin efecto en `httpx` |
| `feregcompraventa` / `certferegcompraventa` | `www4.sii.cl` / `www4c.sii.cl` `/consdcvinternetui/#/index` | RCV usa `…/services/data/facadeService/`, no la página |
| `declaracionivaf29` | `www4.sii.cl/propuestaf29ui/index.html#/default` | F29 (envío GWT-RPC pendiente) |
| `consultaterceros` | `www2.sii.cl/stc/noauthz` | no (situación tributaria de terceros) |
| `consultasesiones` / `certconsultasesiones` | `/RTCConsultaCesionesHtml.cgi` | no (cesiones) |
| `rentaingreso`, `rentaingresow`, `renta` | `navegacionf22/`, `consultaestado/`, `consultaestadof22ui` | no (F22) |
| `iniciodeactividades`, `autenticabifurcacion`, `obtenerclavetributia`, `recuperarclavetrib` | `www2.sii.cl/inicio-de-actividades/`, `/bifurcacion/`, `admin-cntr/noauthz/…` | no |
| `ctributariaregular`, `ctribpersonalizada` | `carpetatributaria/generarcte…` | no |
| otras | bienes raíces (`vica/Menu/…`), F24, F50, giros, mapas, `gestortramitesui`, `sctiInternet`, `bcm2`, clase media, `sispad` | no |

**Reglas para el código y la app:**
- Si alguna vez una respuesta del SII termina en `salaespera.sii.cl` o `*.queue-it.net` (porque el
  SII pase a una integración del lado del servidor), **no es un error de credenciales ni de
  parseo**: es "el SII tiene fila, intenta en unos minutos". Nunca seguir parseando el HTML de la
  fila (mismo patrón que `ayudas/900.html`).
- **Ya está hecho en los tres motores** (2026-10-19), mirando el host de cada salto y del `Location`:
  Python `core/sala_espera.py` (hook de `httpx`; `ClienteReintentos` lo trae, y lo usan además
  `sii_mipyme`, `rcv` y `solicitar_caf_sii`), Go `nucleo/salaespera` (`Transporte` en todos los
  `http.Client` hacia el SII), Rust `es_sala_espera` en `nucleo-rs/src/sii/http.rs` (dentro de
  `ejecutar`/`ejecutar_get`, por donde pasa todo). La API responde **502 `sii_comunicacion`** con el
  mensaje fijo de la fila, sin prefijos (`limpiar` / `salaespera.Limpiar` / `errores.rs`), igual en
  los tres. Pruebas: `test_robustez.py` [5b], `go test ./salaespera`, `cargo test sii::http errores`.
  Un código cliente nuevo hacia el SII **tiene que pasar por esos puntos**.
- No reintentar en loop contra la fila: la cookie `Queue-it-<c>___<e>` y `Queue-it-visitorsession`
  son las que dan el paso; un cliente sin JS no avanza en la fila.
- En la app, los enlaces al portal pueden caer en la fila: decirle a la persona que espere ahí,
  no que algo falló.

[`modulo RCV`]: ../core/rcv.py
