// DTE Chile — interfaz de la app de escritorio. Sin framework: una función por vista.
// Todo lo que toca al motor pasa por `api()`, que llama a Go (App.API) y Go firma la petición.
// La clave del motor nunca llega aquí.
//
// La empresa activa (selector de arriba) decide "en qué mundo estás":
//   demo (EMPRESA DEMO SPA)      → SIMULACIÓN: nada sale del computador (core/simulacion.py)
//   negocio en certificación     → PRUEBAS DEL SII: responde el SII, sin efecto tributario
//   negocio en producción        → REAL: cada documento cuenta

const $ = (id) => document.getElementById(id);
// Nombre del producto (antes "DTE Chile"). También en tauri.conf.json, wails.json y la carpeta de datos.
const MARCA = "Faro";
document.title = MARCA; document.querySelectorAll(".marca-nombre").forEach((e) => (e.textContent = MARCA));
// El haz del faro: un cono de luz que gira en horizontal, proyectado como se ve desde la costa.
// La punta recorre una elipse alrededor de la linterna. De costado se ve el cono largo; al girar
// hacia ti se acorta pero conserva su ancho (ves su sección) hasta volverse un disco de luz
// alrededor de la linterna y un resplandor cálido sobre toda la escena; hacia atrás se acorta
// hasta esconderse detrás de la torre. Se detiene al entrar y con "reducir movimiento".
(() => {
  const haz = document.querySelector(".haz"), luz = document.querySelector(".luz"), disco = document.querySelector(".disco"), resplandor = document.querySelector(".resplandor");
  if (!haz || !luz) return;
  const quieto = () => document.documentElement.classList.contains("dentro") || matchMedia("(prefers-reduced-motion: reduce)").matches;
  const VUELTA = 24000, R = 1500, RY = 110, BAJO = 70;   // ms por vuelta, alcance, semieje vertical, centro bajo la linterna
  const suave = (x) => x * x * (3 - 2 * x);
  function paso(t) {
    if (!quieto()) {
      const th = ((t % VUELTA) / VUELTA) * 2 * Math.PI;   // 0 = derecha, π/2 = hacia ti, π = izquierda, 3π/2 = atrás
      const dx = R * Math.cos(th), dy = BAJO + RY * Math.sin(th);
      const largo = Math.hypot(dx, dy), ang = (Math.atan2(dy, dx) * 180) / Math.PI;
      const cerca = (Math.sin(th) + 1) / 2;                 // 1 adelante, 0 atrás
      const frente = suave(Math.max(0, 1 - Math.abs(Math.cos(th)) / 0.45));   // 1 cuando apunta hacia ti o hacia atrás
      haz.style.transform = `translate(430px,372px) rotate(${ang}deg) scale(${largo / R},1)`;   // se acorta, no se adelgaza
      haz.style.opacity = ((0.3 + 0.65 * cerca) * (1 - 0.6 * frente)).toFixed(3);
      const d = frente * (0.15 + 0.85 * cerca);             // el disco: fuerte adelante, apenas atrás (queda tras la torre)
      if (disco) { disco.style.opacity = d.toFixed(3); disco.setAttribute("cy", String(372 + 40 * Math.sin(th))); }
      luz.style.opacity = (0.55 + 0.45 * d).toFixed(3);
      luz.style.transform = `scale(${1 + 1.2 * d})`;
      if (resplandor) resplandor.style.opacity = (0.8 * frente * cerca).toFixed(3);   // la luz te da en la cara
    }
    requestAnimationFrame(paso);
  }
  requestAnimationFrame(paso);
})();
// Tema: automático (el del sistema), claro u oscuro. Se recuerda en este equipo.
const TEMAS = { "": "Automático", light: "Claro", dark: "Oscuro" };
function aplicarTema(t) { if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; try { localStorage.setItem("tema", t); } catch {} }
try { aplicarTema(localStorage.getItem("tema") || ""); } catch {}
const clp = (n) => (n == null ? "—" : "$ " + Math.round(n).toLocaleString("es-CL"));
// Fechas en castellano: la persona lee "1 de octubre de 2026", no "2026-10-01".
const MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];
const fechaLarga = (iso) => { const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || ""); return m ? `${Number(m[3])} de ${MESES[Number(m[2]) - 1]} de ${m[1]}` : (iso || "—"); };
const fechaCorta = (iso) => { const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || ""); return m ? `${m[3]}/${m[2]}/${m[1]}` : (iso || ""); };
const mesLargo = (ym) => { const m = /^(\d{4})-(\d{2})/.exec(ym || ""); return m ? `${MESES[Number(m[2]) - 1]} de ${m[1]}` : (ym || ""); };
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const TIPOS = { 33: "Factura", 34: "Factura exenta", 39: "Boleta", 41: "Boleta exenta", 52: "Guía de despacho", 56: "Nota de débito", 61: "Nota de crédito" };
const IVA = 0.19;
// Íconos propios ilustrados (24×24, relleno en capas: .s sombra, .f forma, .a acento): los mismos en
// Mac, Windows y Linux. En index.html el dock lleva los suyos inline; el color lo pone app.css.
const ICONOS = {
  inicio: '<path class="s" d="M4.5 12.5 12 6l7.5 6.5V20a1 1 0 0 1-1 1H5.5a1 1 0 0 1-1-1z"/><path class="f" d="M3.5 11.5 12 4.5l8.5 7V19a1 1 0 0 1-1 1h-5v-5h-5v5h-5a1 1 0 0 1-1-1z"/><path class="a" d="M10 15h4v5h-4z"/><path class="a" d="M3 12.2 12 4.5l9 7.7-1.2 1.2L12 7 4.2 13.4z"/>',
  cobrar: '<path class="s" d="M7 4h11v17l-1.8-1.4L14.4 21l-1.8-1.4L10.8 21 9 19.6 7.2 21z"/><path class="f" d="M6 3h11v17l-1.8-1.4L13.4 20l-1.8-1.4L9.8 20 8 18.6 6.2 20z"/><path class="a" d="M8.5 7h6v1.6h-6zM8.5 10.5h6v1.6h-6zM8.5 14h3.5v1.6H8.5z"/>',
  documentos: '<path class="s" d="M7.5 4H15l4.5 4.5V21a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1z"/><path class="f" d="M6 3h8l5 5v12a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path class="a" d="M14 3v5h5zM8 12h8v1.6H8zM8 15.5h8v1.6H8z"/>',
  aprender: '<path class="s" d="M3.5 7.5 12 4l8.5 3.5L12 11z"/><path class="f" d="M2.5 8.5 12 4l9.5 4.5L12 13z"/><path class="f" d="M6.5 11.3v4.2c0 1.6 2.6 2.9 5.5 2.9s5.5-1.3 5.5-2.9v-4.2L12 14z"/><path class="a" d="M12 13 2.5 8.5v1.2L12 14.2l9.5-4.5V8.5zM20.3 9.4h1.4v5.2h-1.4z"/>',
  ajustes: '<path class="s" d="M5 4h2v17H5zM11 4h2v17h-2zM17 4h2v17h-2z"/><path class="f" d="M5.2 3h1.6v18H5.2zM11.2 3h1.6v18h-1.6zM17.2 3h1.6v18h-1.6z"/><circle class="f" cx="6" cy="14" r="2.6"/><circle class="f" cx="12" cy="8" r="2.6"/><circle class="f" cx="18" cy="16" r="2.6"/><circle class="a" cx="6" cy="14" r="1.1"/><circle class="a" cx="12" cy="8" r="1.1"/><circle class="a" cx="18" cy="16" r="1.1"/>',
  mes: '<rect class="s" x="4.5" y="5.5" width="16" height="16" rx="2.5"/><rect class="f" x="3.5" y="4.5" width="16" height="16" rx="2.5"/><path class="a" d="M3.5 9h16v1.6h-16zM7 2.5h1.8v4H7zM14.2 2.5H16v4h-1.8z"/><rect class="a" x="6.5" y="12.5" width="3.2" height="3.2" rx=".8"/><rect class="a" x="10.4" y="12.5" width="3.2" height="3.2" rx=".8" opacity=".5"/>',
  ia: '<rect class="s" x="5.5" y="5.5" width="14" height="14" rx="3"/><rect class="f" x="4.5" y="4.5" width="14" height="14" rx="3"/><path class="a" d="M11.5 7.5l1.1 2.9 2.9 1.1-2.9 1.1-1.1 2.9-1.1-2.9-2.9-1.1 2.9-1.1z"/><path class="a" d="M8 2h1.6v2.5H8zM13.4 2H15v2.5h-1.6zM8 18.5h1.6V21H8zM13.4 18.5H15V21h-1.6zM2 8h2.5v1.6H2zM2 13.4h2.5V15H2zM18.5 8H21v1.6h-2.5zM18.5 13.4H21V15h-2.5z"/>',
  valores: '<path class="s" d="M5 19h2.6v-5H5zM10 19h2.6v-9H10zM15 19h2.6v-13H15z"/><path class="f" d="M4 18h2.6v-5H4zM9 18h2.6v-9H9zM14 18h2.6V5H14z"/><path class="a" d="M3 20h18v1.6H3zM19.5 3l2 3.6h-1.3v3.4h-1.4V6.6h-1.3z"/>',
  panel: '<path class="s" d="M5 11.5h15V21H5z"/><path class="f" d="M4 11h15v9.5H4z"/><path class="f" d="M3 5h17l1.5 4.2c0 1.4-1.2 2.3-2.5 2.3S16.5 10.6 16.5 9.2c0 1.4-1.2 2.3-2.5 2.3S11.5 10.6 11.5 9.2c0 1.4-1.2 2.3-2.5 2.3S6.5 10.6 6.5 9.2c0 1.4-1.2 2.3-2.5 2.3S1.5 10.6 1.5 9.2z"/><path class="a" d="M6.5 9.2V5h5v4.2zM16.5 9.2V5h3.5l1.5 4.2z"/><path class="a" d="M8.5 14h3v6.5h-3zM13.5 14h3.5v3h-3.5z"/>',
  empresa: '<path class="s" d="M5 11.5h15V21H5z"/><path class="f" d="M4 11h15v9.5H4z"/><path class="f" d="M3 5h17l1.5 4.2c0 1.4-1.2 2.3-2.5 2.3S16.5 10.6 16.5 9.2c0 1.4-1.2 2.3-2.5 2.3S11.5 10.6 11.5 9.2c0 1.4-1.2 2.3-2.5 2.3S6.5 10.6 6.5 9.2c0 1.4-1.2 2.3-2.5 2.3S1.5 10.6 1.5 9.2z"/><path class="a" d="M6.5 9.2V5h5v4.2zM16.5 9.2V5h3.5l1.5 4.2z"/><path class="a" d="M8.5 14h3v6.5h-3zM13.5 14h3.5v3h-3.5z"/>',
  sincronizar: '<path class="s" d="M13 5.5a7 7 0 0 1 6.2 3.8l1.4-.8 .3 4.4-4-1.9 1.3-.7A5 5 0 0 0 13 7.5z"/><path class="f" d="M12 4a8 8 0 0 1 7.1 4.3l1.9-1 .3 5.3-4.8-2.3 1.6-.9A6 6 0 0 0 12 6zM12 20a8 8 0 0 1-7.1-4.3l-1.9 1-.3-5.3 4.8 2.3-1.6.9A6 6 0 0 0 12 18z"/>',
  vacio: '<path class="f" d="M3 9h18l-1.5 11H4.5z" opacity=".55"/><path class="a" d="M5 4h14l2 5H3z"/><path class="a" d="M9 12h6v1.6H9z"/>',
};
const icono = (k) => `<div class="ico"><svg viewBox="0 0 24 24" aria-hidden="true">${ICONOS[k]}</svg></div>`;
const RUT_DEMO = "77777777-7";
const DEMO = { rut: RUT_DEMO, razon_social: "EMPRESA DEMO SPA", demo: true, ambiente: "simulacion", estado_etiqueta: "Empresa de práctica", usa_sw_propio: 1 };

let estado = { listo: false };
let negocios = [];          // reales, del motor
let empresaRut = (() => { try { return localStorage.getItem("empresa") || RUT_DEMO; } catch { return RUT_DEMO; } })();

// ---------- motor ----------
async function api(metodo, ruta, cuerpo) {
  const r = await window.go.main.App.API(metodo, ruta, cuerpo ? JSON.stringify(cuerpo) : "");
  let datos = null;
  try { datos = JSON.parse(r.cuerpo); } catch { datos = r.cuerpo; }
  if (r.status >= 400) {
    if (r.status === 423) bovedaBloqueada();   // la bóveda con PIN se cerró: pedir el PIN
    const msg = (datos && (datos.error?.mensaje || datos.detail || datos.mensaje)) || `Error ${r.status}`;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return datos;
}
// Diálogo propio (en vez del confirm() del sistema): título, cuerpo en HTML y botones con su
// valor. Resuelve con el valor del botón apretado; Esc o "Volver" resuelven false.
// `leer`: si el cuerpo trae campos, devuelve lo escrito al confirmar (se llama antes de cerrar).
function dialogo({ titulo, cuerpo, botones, ancho = 520, leer }) {
  if (window.Tour && !window.Tour.enDemo()) window.Tour.cerrar();   // un diálogo es un momento de atención: ninguna pista encima (salvo que lo abra la propia demo)
  return new Promise((resolve) => {
    const m = document.createElement("div");
    m.className = "modal";
    m.innerHTML = `<div class="card" style="max-width:${ancho}px;width:92%"><h2 style="margin-top:0">${titulo}</h2>${cuerpo}
      <div class="row" style="justify-content:flex-end">${botones.map((b, i) => `<button class="${b.clase || ""}" data-i="${i}" style="flex:none">${b.texto}</button>`).join("")}</div></div>`;
    const fin = (v) => { document.removeEventListener("keydown", tecla); m.remove(); resolve(v); };
    const tecla = (ev) => { if (ev.key === "Escape") { ev.preventDefault(); fin(false); } };
    document.addEventListener("keydown", tecla);
    document.body.appendChild(m);
    m.querySelectorAll("button[data-i]").forEach((b) => (b.onclick = () => { const v = botones[Number(b.dataset.i)].valor; fin(v && leer ? leer() : v); }));
    setTimeout(() => (m.querySelector("input") || m.querySelector("button[data-i]:last-child"))?.focus(), 30);
  });
}
// La revisión antes de emitir: quién, a quién, qué, los tres números y en qué mundo. Es la
// pantalla que promete el botón "Revisar y emitir".
async function revisar(args) {
  const ok = await revisarBase(args);
  if (!ok) return false;
  return args.w === "real" ? pinParaReal() : true;   // el primer envío real de la sesión pide el PIN
}
function revisarBase({ tipo, de, para, que, neto, iva, total, w, extra = "" }) {
  const mundoTxt = w === "real" ? `<div class="banner rojo"><b>Esto es real.</b> Un folio gastado no vuelve: revisa el RUT, el monto y el tipo.</div>`
    : w === "pruebas" ? `<div class="banner">Ambiente de <b>pruebas del SII</b>: el documento no tiene efecto tributario.</div>`
    : `<div class="banner verde"><b>Práctica.</b> Se arma, se firma y se timbra como uno real, pero no sale de tu computador.</div>`;
  const filas = [["Documento", TIPOS[tipo] || tipo], ["De", de], ["Para", para], ["Qué", que]]
    .map(([k, v]) => `<div class="rev"><span>${k}</span><b>${esc(v)}</b></div>`).join("");
  const totales = `<div class="totales"><div><span>Neto</span><b>${clp(neto)}</b></div><div><span>IVA</span><b>${clp(iva)}</b></div><div><span>Total</span><b>${clp(total)}</b></div></div>`;
  return dialogo({ titulo: "Revisa antes de emitir", cuerpo: filas + totales + extra + mundoTxt,
    botones: [{ texto: "Volver", clase: "sec", valor: false }, { texto: `Emitir ${(TIPOS[tipo] || "").toLowerCase()} por ${clp(total)}`, clase: w === "real" ? "peligro" : "", valor: true }] });
}
function aviso(txt, ms = 3500) { const a = $("aviso"); a.textContent = txt; a.style.display = "block"; clearTimeout(a._t); a._t = setTimeout(() => (a.style.display = "none"), ms); }

// ---------- empresa activa y mundo ----------
const todas = () => [DEMO, ...negocios];
const empresa = () => todas().find((n) => n.rut === empresaRut) || DEMO;
const mundo = () => { const e = empresa(); return e.demo ? "sim" : e.ambiente === "produccion" ? "real" : "pruebas"; };

function pintarMundo() {
  const m = $("mundo");
  if (!estado.listo) { m.className = "mundo " + (estado.error ? "m-error" : "m-cargando"); m.textContent = estado.error ? "motor con error" : "arrancando motor…"; return; }
  const w = mundo();
  m.className = "mundo m-" + w;
  m.textContent = w === "sim" ? "Práctica · nada sale de aquí" : w === "real" ? "Real · cada documento cuenta" : "Pruebas del SII · sin efecto";
}
function pintarSelector() {
  const s = $("empresa");
  // Con una sola empresa (la de práctica) no hay nada que elegir: el selector aparece recién con la primera real.
  const w = s.closest(".sel-wrap"); if (w) w.style.display = negocios.length ? "" : "none";
  s.innerHTML = todas().map((n) => `<option value="${esc(n.rut)}" ${n.rut === empresaRut ? "selected" : ""}>${esc(n.razon_social || n.rut)}${n.demo ? " (práctica)" : ""}</option>`).join("");
  s.onchange = () => { empresaRut = s.value; try { localStorage.setItem("empresa", empresaRut); } catch {} pintarMundo(); render(); };
}
async function cargarNegocios() {
  try { negocios = await api("GET", "/api/v1/keystore/negocios"); } catch { negocios = []; }
  if (!todas().some((n) => n.rut === empresaRut)) empresaRut = RUT_DEMO;
  pintarSelector(); pintarMundo();
  return negocios;
}

// ---------- router ----------
const vistas = {};
function ir(v) { location.hash = "#" + v; }
function param(k) { const q = (location.hash.split("?")[1] || ""); return new URLSearchParams(q).get(k); }
async function render() {
  await revisarBoveda();
  const v = (location.hash || "#inicio").slice(1).split("?")[0];
  const grupo = { ver: "documentos", mes: "documentos", corregir: "documentos", ia: "ajustes", valores: "ajustes", empresa: "ajustes", leccion: "aprender" }[v] || v;
  document.querySelectorAll("[data-v]").forEach((a) => a.classList.toggle("on", a.dataset.v === grupo));
  const f = vistas[v] || vistas.inicio;
  window.scrollTo(0, 0);
  $("vista").innerHTML = '<p class="mut">Cargando…</p>';
  try { await f($("vista")); } catch (e) { $("vista").innerHTML = `<div class="banner rojo">${esc(e.message)}</div>`; }
  document.dispatchEvent(new CustomEvent("vista", { detail: v }));   // el tour escucha (tour.js)
}
window.addEventListener("hashchange", render);

// ---------- bóveda con PIN ----------
// El motor Rust guarda la clave maestra cifrada con el PIN (boveda.json). Mientras está
// bloqueado responde 423 a todo lo que necesite el certificado o las claves del SII. Si el
// motor no tiene bóveda (Python, Go), GET /api/v1/boveda da 404 y nada de esto aparece.
let boveda = { disponible: false, configurada: false, bloqueada: false, autobloqueo_min: 15, pin_minimo: 6, tiene_secretos: false, ofrecida: false };
let pinReal = false;    // ya escribió el PIN para un envío "de verdad" en esta sesión
let modalPIN = null;    // la pantalla de bloqueo, si está abierta
const AUTOBLOQUEOS = [[5, "5 minutos"], [15, "15 minutos"], [60, "1 hora"], [0, "nunca (no recomendado)"]];
const bovedaErr = (r) => { try { return JSON.parse(r.cuerpo).error?.mensaje || `Error ${r.status}`; } catch { return `Error ${r.status}`; } };
async function estadoBoveda() {
  try {
    const r = await window.go.main.App.API("GET", "/api/v1/boveda", "");
    if (r.status === 404) { boveda.disponible = false; return boveda; }
    if (r.status >= 400) return boveda;
    Object.assign(boveda, JSON.parse(r.cuerpo), { disponible: true });
    if (boveda.bloqueada) pinReal = false;
  } catch { /* el motor todavía no está listo */ }
  return boveda;
}
// Un modal propio (no `dialogo`): cuando es obligatorio no se cierra con Esc ni con "Volver".
// `al(m)` hace el trabajo; si lanza un error, se muestra adentro y el modal sigue abierto.
function modalPin({ titulo, cuerpo, boton, obligatorio, al }) {
  if (window.Tour && !window.Tour.enDemo()) window.Tour.cerrar();
  return new Promise((resolve) => {
    const m = document.createElement("div"); m.className = "modal";
    m.innerHTML = `<div class="card" style="max-width:480px;width:92%"><h2 style="margin-top:0">${titulo}</h2>${cuerpo}<div class="pin-err"></div>
      <div class="row" style="justify-content:flex-end">${obligatorio ? "" : `<button class="sec" data-v="no" style="flex:none">Volver</button>`}<button data-v="si" style="flex:none">${boton}</button></div></div>`;
    document.body.appendChild(m);
    const fin = (v) => { document.removeEventListener("keydown", tecla); m.remove(); resolve(v); };
    const tecla = (ev) => {
      if (!m.isConnected) return document.removeEventListener("keydown", tecla);
      if (ev.key === "Escape" && !obligatorio) { ev.preventDefault(); fin(null); }
      if (ev.key === "Enter" && m.contains(document.activeElement) && document.activeElement.tagName === "INPUT") { ev.preventDefault(); m.querySelector('[data-v="si"]').click(); }
    };
    document.addEventListener("keydown", tecla);
    m.querySelector('[data-v="no"]')?.addEventListener("click", () => fin(null));
    m.querySelector('[data-v="si"]').onclick = async () => {
      const b = m.querySelector('[data-v="si"]'); b.disabled = true; m.querySelector(".pin-err").innerHTML = "";
      try { const v = await al(m); if (v !== undefined) return fin(v); }
      catch (e) { m.querySelector(".pin-err").innerHTML = `<div class="banner rojo">${esc(e.message || e)}</div>`; }
      b.disabled = false;
      const campos = m.querySelectorAll("input.pin");
      if (campos.length === 1) campos[0].value = "";   // un solo campo (abrir, confirmar): se limpia; con varios, se corrige lo escrito
      campos[0]?.focus();
    };
    setTimeout(() => m.querySelector("input")?.focus(), 30);
  });
}
// El campo del PIN es type=text enmascarado con CSS (no type=password): así el gestor de
// contraseñas del sistema no lo autollena ni ofrece "contraseña segura", y "Mostrar" deja ver
// lo escrito para que nadie guarde un PIN con un error de tipeo.
const campoPin = (id, etiqueta, ayuda) => `<label style="display:flex;justify-content:space-between;align-items:center">${etiqueta}<span class="lnk mut" style="font-weight:400" onclick="const i=document.getElementById('${id}');i.classList.toggle('ver');this.textContent=i.classList.contains('ver')?'Ocultar':'Mostrar'">Mostrar</span></label><input id="${id}" class="pin" type="text" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" placeholder="Al menos ${boveda.pin_minimo} caracteres">${ayuda ? `<p class="mut">${ayuda}</p>` : ""}`;
// Crear el PIN. `obligatorio`: ya hay secretos guardados sin PIN, así que no se puede seguir sin uno.
window.crearPIN = async (obligatorio) => {
  const v = await modalPin({ titulo: "Crea tu PIN", obligatorio, boton: "Guardar mi PIN", cuerpo: `
    <p class="mut">Tu certificado digital y tus claves del SII quedan guardados en este computador, cifrados. El PIN es la llave: <b>sin tu PIN nadie puede abrirlos</b>, ni siquiera con una copia de la carpeta.</p>
    ${campoPin("pin1", "Tu PIN", "Puede ser una frase corta con espacios, como <i>mi gato se llama tomás</i>. Mientras más larga, mejor. Letras, números o los dos.")}
    ${campoPin("pin2", "Escríbelo otra vez")}
    <label>Bloquear sola si no la uso durante</label><select id="pin_auto">${AUTOBLOQUEOS.map(([m, t]) => `<option value="${m}" ${m === 15 ? "selected" : ""}>${t}</option>`).join("")}</select>
    <div class="banner" style="margin-top:12px"><b>Si lo olvidas, no hay forma de recuperarlo.</b> Tendrías que volver a cargar tu certificado y tus claves. Tus documentos ya emitidos no se pierden.</div>`,
    al: async (m) => {
      const a = m.querySelector("#pin1").value, b = m.querySelector("#pin2").value;
      if (a.length < boveda.pin_minimo) throw new Error(`El PIN debe tener al menos ${boveda.pin_minimo} caracteres.`);
      if (a !== b) throw new Error("Los dos PIN no son iguales. Escríbelos de nuevo.");
      const r = await window.go.main.App.API("POST", "/api/v1/boveda", JSON.stringify({ pin: a, autobloqueo_min: Number(m.querySelector("#pin_auto").value) }));
      if (r.status >= 400) throw new Error(bovedaErr(r));
      Object.assign(boveda, JSON.parse(r.cuerpo)); return true;
    } });
  if (v) aviso("PIN creado. Tus claves quedan bajo llave.", 4000);
  return !!v;
};
// La pantalla de bloqueo: se queda hasta que el PIN sea correcto (o se empiece de nuevo).
async function abrirBoveda() {
  if (modalPIN) return modalPIN;
  modalPIN = modalPin({ titulo: "Escribe tu PIN", obligatorio: true, boton: "Abrir", cuerpo: `
    <p class="mut">La app está bloqueada. Tu certificado y tus claves siguen cifrados hasta que escribas tu PIN.</p>
    ${campoPin("pin_abrir", "Tu PIN")}
    <p class="mut"><span class="lnk" onclick="olvidePIN()">Olvidé mi PIN</span></p>`,
    al: async (m) => {
      const r = await window.go.main.App.API("POST", "/api/v1/boveda/abrir", JSON.stringify({ pin: m.querySelector("#pin_abrir").value }));
      if (r.status >= 400) throw new Error(bovedaErr(r));
      Object.assign(boveda, JSON.parse(r.cuerpo)); return true;
    } });
  const v = await modalPIN; modalPIN = null;
  if (v) { aviso("Abierta.", 2000); render(); }
  return v;
}
window.olvidePIN = async () => {
  const ok = await dialogo({ titulo: "Olvidé mi PIN", cuerpo: `
    <p class="mut">Un PIN no se puede recuperar: por eso sirve. Lo que sí puedes hacer es <b>empezar de nuevo con tus claves</b>:</p>
    <ul class="lista-check"><li><span>Se borran el certificado digital y las claves del SII guardadas.</span></li><li><span>Tus empresas y todos los documentos ya emitidos se quedan.</span></li><li><span>Después creas un PIN nuevo y vuelves a cargar el certificado en Mi empresa.</span></li></ul>`,
    botones: [{ texto: "Volver", clase: "sec", valor: false }, { texto: "Sí, borrar mis claves y empezar de nuevo", clase: "peligro", valor: true }] });
  if (!ok) return;
  const r = await window.go.main.App.API("POST", "/api/v1/boveda/reiniciar", JSON.stringify({ confirmar: true }));
  if (r.status >= 400) return aviso(bovedaErr(r), 6000);
  Object.assign(boveda, JSON.parse(r.cuerpo));
  document.querySelectorAll(".modal").forEach((m) => m.remove()); modalPIN = null;   // cierra la pantalla de bloqueo
  aviso("Claves borradas. Ahora crea un PIN nuevo.", 4000);
  await window.crearPIN(true); render();
};
// Antes de un envío "de verdad": el PIN, una vez por sesión.
async function pinParaReal() {
  if (!boveda.disponible || !boveda.configurada || pinReal) return true;
  const v = await modalPin({ titulo: "Tu PIN para enviar de verdad", obligatorio: false, boton: "Confirmar", cuerpo: `
    <p class="mut">Esto va al SII y cuenta. Para el primer envío real de cada sesión te pedimos el PIN, por si no eres tú quien está frente al computador.</p>${campoPin("pin_real", "Tu PIN")}`,
    al: async (m) => {
      const r = await window.go.main.App.API("POST", "/api/v1/boveda/abrir", JSON.stringify({ pin: m.querySelector("#pin_real").value }));
      if (r.status >= 400) throw new Error(bovedaErr(r)); return true;
    } });
  if (v) pinReal = true;
  return !!v;
}
// Al arrancar: ¿bloqueada? ¿hay secretos guardados sin PIN? Y cada medio minuto, ¿se bloqueó sola?
async function revisarBoveda() {
  if (!estado.listo) return;
  await estadoBoveda();
  if (!boveda.disponible) return;
  if (boveda.bloqueada) { await abrirBoveda(); return; }
  if (!boveda.configurada && boveda.tiene_secretos && !boveda.ofrecida) { boveda.ofrecida = true; await window.crearPIN(true); }
}
function bovedaBloqueada() { boveda.bloqueada = true; pinReal = false; if (!modalPIN) abrirBoveda(); }
setInterval(() => { if (boveda.disponible && boveda.configurada && !modalPIN) estadoBoveda().then(() => { if (boveda.bloqueada) abrirBoveda(); }); }, 30000);
window.bloquearAhora = async () => { await window.go.main.App.API("POST", "/api/v1/boveda/bloquear", ""); bovedaBloqueada(); };
window.cambiarPIN = async () => {
  const v = await modalPin({ titulo: "Cambiar tu PIN", obligatorio: false, boton: "Cambiar", cuerpo: `${campoPin("pc0", "Tu PIN actual")}${campoPin("pc1", "El nuevo")}${campoPin("pc2", "El nuevo, otra vez")}`,
    al: async (m) => {
      const a = m.querySelector("#pc1").value, b = m.querySelector("#pc2").value;
      if (a.length < boveda.pin_minimo) throw new Error(`El PIN debe tener al menos ${boveda.pin_minimo} caracteres.`);
      if (a !== b) throw new Error("Los dos PIN nuevos no son iguales.");
      const r = await window.go.main.App.API("PUT", "/api/v1/boveda", JSON.stringify({ pin_actual: m.querySelector("#pc0").value, pin_nuevo: a }));
      if (r.status >= 400) throw new Error(bovedaErr(r));
      Object.assign(boveda, JSON.parse(r.cuerpo)); return true;
    } });
  if (v) aviso("PIN cambiado.", 3000);
};
window.autobloqueo = async (min) => {
  const v = await modalPin({ titulo: "Confirma con tu PIN", obligatorio: false, boton: "Guardar", cuerpo: `<p class="mut">Para cambiar cuándo se bloquea sola hace falta tu PIN.</p>${campoPin("pa0", "Tu PIN")}`,
    al: async (m) => {
      const r = await window.go.main.App.API("PUT", "/api/v1/boveda", JSON.stringify({ pin_actual: m.querySelector("#pa0").value, autobloqueo_min: Number(min) }));
      if (r.status >= 400) throw new Error(bovedaErr(r));
      Object.assign(boveda, JSON.parse(r.cuerpo)); return true;
    } });
  if (v) aviso("Guardado.", 2000);
  render();
};

// ---------- inicio ----------
vistas.inicio = async (el) => {
  if (!estado.listo) {
    el.innerHTML = `<h1>Hola</h1><p class="sub">${estado.error ? "El motor no pudo arrancar." : "Arrancando el motor de facturación…"}</p>` +
      (estado.error ? `<div class="banner rojo">${esc(estado.error)}${estado.log ? `<br><span class="mut">Registro: <code>${esc(estado.log)}</code></span>` : ""}</div>` : "");
    return;
  }
  await cargarNegocios();
  const e = empresa();
  let cab;
  if (e.demo) {
    const st = await api("GET", "/api/v1/simulacion/estado");
    cab = `<div class="banner verde"><b>Estás practicando con EMPRESA DEMO SPA.</b> Lo que emitas aquí es idéntico a un documento real, pero nunca sale de tu computador.
      Llevas <b>${st.documentos}</b> documento(s); la próxima boleta es la <b>N° ${st.folios["39"]?.siguiente ?? 1}</b>.
      <br><span class="lnk" onclick="reiniciarDemo()">Empezar de nuevo</span> <span class="mut">(solo existe en la práctica: en el mundo real nada se borra)</span></div>`;
  } else {
    cab = `<div class="banner ${mundo() === "real" ? "rojo" : ""}"><b>${esc(e.razon_social || e.rut)}</b> · ${esc(e.rut)} · <span class="pill ${e.usa_sw_propio ? "p-ok" : "p-mut"}">${esc(e.estado_etiqueta || "sin diagnóstico")}</span>
      ${mundo() === "real" ? "<br>Esta empresa está en <b>producción</b>: lo que emitas cuenta ante el SII." : "<br>Esta empresa está en el <b>ambiente de pruebas</b> del SII: el SII responde, pero sin efecto tributario."}</div>`;
  }
  const sinc = (!e.demo && !e.usa_sw_propio) ? `<div class="card accion" data-tour="sincronizar" onclick="sincronizarGratuito()">${icono("sincronizar")}<h3>Traer lo emitido en el SII</h3><p>Esta empresa emite en el sistema del SII. La app entra como tú, lista lo emitido y guarda PDF y XML aquí. Solo lectura.</p></div>` : "";
  const hora = new Date().getHours(), saludo = hora < 12 ? "Buenos días" : hora < 20 ? "Buenas tardes" : "Buenas noches";
  el.innerHTML = `
    <h1>${saludo}</h1>
    <p class="sub">${e.demo ? "Estás en la empresa de práctica: prueba todo sin miedo." : `Trabajando por <b>${esc(e.razon_social || e.rut)}</b>.`} ¿Qué quieres hacer?</p>
    <p class="mut" id="valores_hoy"></p>
    ${cab}
    <div class="grid">
      ${sinc}
      <div class="card accion" data-tour="cobrar" onclick="ir('cobrar')">${icono("cobrar")}<h3>Emitir una boleta o factura</h3><p>A una persona o a una empresa. Ves neto, IVA y total antes de emitir.</p></div>
      <div class="card accion" onclick="ir('documentos')">${icono("documentos")}<h3>Mis documentos</h3><p>Todo lo emitido. Para corregir, se anula con una nota de crédito: nada se borra.</p></div>
      <div class="card accion" onclick="ir('documentos?tab=mes')">${icono("mes")}<h3>Mi mes</h3><p>Cuánto vendiste, cuánto IVA cobraste y qué va a pedir el F29.</p></div>
    </div>
    ${negocios.length ? "" : `<div class="card"><b>Todavía no hay ninguna empresa real.</b><p class="mut">Practica con la de mentira todo lo que quieras. Cuando tengas tu certificado digital, agrega tu empresa y aparecerá arriba para elegirla.</p>
      <button class="sec" data-tour="empresa" onclick="ir('empresa')">Agregar mi empresa</button></div>`}`;
  pintarValores();
};
// Ajustes: lo que no es trabajo diario (conectar la IA, los valores del día, el panel del motor,
// los detalles técnicos). Cuelga del engranaje de la cabecera, no de la barra.
vistas.ajustes = async (el) => {
  el.innerHTML = `
    <h1>Ajustes</h1>
    <p class="sub">Lo que no es trabajo de todos los días.</p>
    <div class="grid">
      <div class="card accion" onclick="ir('ia')">${icono("ia")}<h3>Tu IA</h3><p>Conecta el asistente de IA de este computador para que trabaje con tu facturación.</p></div>
      <div class="card accion" onclick="ir('valores')">${icono("valores")}<h3>Valores</h3><p>UF, UTM y dólar de cualquier día, y el histórico mensual.</p></div>
      <div class="card accion" onclick="ir('empresa')">${icono("panel")}<h3>Mi empresa</h3><p>Tu certificado digital, tu empresa y tus folios, paso a paso.</p></div>
    </div>
    ${boveda.disponible ? `<div class="card"><h3>Tu PIN</h3>
      ${boveda.configurada ? `<p class="mut">Tus claves están bajo llave. La app se bloquea sola ${boveda.autobloqueo_min ? `si pasan ${(AUTOBLOQUEOS.find((a) => a[0] === boveda.autobloqueo_min) || [0, boveda.autobloqueo_min + " minutos"])[1]} sin usarla` : "solo cuando la cierras"}, y pide el PIN antes del primer envío real de cada sesión.</p>
        <label>Bloquear sola si no la uso durante</label><select onchange="autobloqueo(this.value)">${AUTOBLOQUEOS.map(([m, t]) => `<option value="${m}" ${m === boveda.autobloqueo_min ? "selected" : ""}>${t}</option>`).join("")}</select>
        <div class="row" style="margin-top:12px"><button class="sec" onclick="cambiarPIN()">Cambiar PIN</button><button class="sec" onclick="bloquearAhora()">Bloquear ahora</button></div>`
      : `<p class="mut">Todavía no tienes PIN. Con PIN, tu certificado y tus claves quedan cifrados con una llave que solo tú sabes: sin él, ni una copia de la carpeta sirve.</p><button onclick="crearPIN(false).then(render)">Crear mi PIN</button>`}
    </div>` : ""}
    <div class="card"><h3>Dónde quedan tus claves</h3>
      <ul class="lista-check">
        <li><span><b>Tu certificado digital</b> se guarda cifrado en este computador. ${boveda.configurada ? "La llave es tu PIN, y no está escrita en ninguna parte." : "La llave la creó la app aquí la primera vez; con un PIN, la llave pasa a ser el PIN."} Nunca se escribe en claro ni sale de aquí.</span></li>
        <li><span><b>Tu clave del SII</b> solo se pide para operar el sistema gratuito del SII. Vive en memoria mientras la app está abierta y se borra al cerrarla, salvo que marques "recordar en este computador": entonces se guarda cifrada como el certificado.</span></li>
        <li><span><b>La IA</b>, si la conectas, no recibe ninguna clave y no puede emitir sola: todo lo que cuenta ante el SII pasa por tu confirmación en esta app.</span></li>
        <li><span><b>No hay servidor de Faro.</b> Lo único que sale de este computador es lo que va al SII.</span></li>
      </ul></div>
    <div class="card"><h3>¿Algo no funcionó?</h3>
      <p class="mut">La app anota sus fallas en un registro en este computador y no las manda a ningún lado. Si algo salió mal, arma un informe: lo ves completo, con los RUT tachados, y tú decides dónde pegarlo.</p>
      <button class="sec" onclick="informarProblema()">Informar un problema</button></div>
    <div class="card"><h3>Apariencia</h3><p class="mut">Si el fondo oscuro te cansa la vista, elige Claro. Automático sigue al sistema.</p>
      <div class="segmento" id="tema_sel">${Object.entries(TEMAS).map(([k, v]) => `<button class="${(localStorage.getItem("tema") || "") === k ? "on" : ""}" onclick="aplicarTema('${k}'); render()">${v}</button>`).join("")}</div></div>
    <details class="tecnico"><summary>Detalles técnicos</summary>
      <p class="mut">Motor v${esc(estado.version)} en <code>${esc(estado.url)}</code>${estado.externo ? " (externo)" : estado.empaquetado ? " (dentro de la app)" : " (datos del repo)"}.
      ${estado.empaquetado ? `Tus datos (base, documentos y configuración) están en <code>${esc(estado.raiz)}</code>.` : ""}
      Para integradores existe además un <span class="lnk" onclick="abrirPanel('certificados')">panel web del motor</span> (usuarios, claves de API); al abrirlo se muestra su clave.</p></details>`;
};
// El panel web pide usuario y clave; se muestran recién cuando hacen falta, no en Inicio.
window.abrirPanel = async (pagina) => {
  if (estado.clave_admin) {
    const ok = await dialogo({ titulo: "Abrir el panel del motor", ancho: 460,
      cuerpo: `<p class="mut">Se abre en tu navegador. Para entrar usa:</p>
        <div class="rev"><span>Usuario</span><b><code>admin</code></b></div><div class="rev"><span>Clave</span><b><code>${esc(estado.clave_admin)}</code></b></div>
        <p class="mut">Al continuar, la clave queda copiada. Si la pierdes, vuelve aquí: siempre se muestra en este mismo lugar.</p>`,
      botones: [{ texto: "Volver", clase: "sec", valor: false }, { texto: "Copiar clave y abrir", valor: true }] });
    if (!ok) return;
    try { await navigator.clipboard.writeText(estado.clave_admin); aviso("Clave copiada."); } catch {}
  }
  window.go.main.App.AbrirPanel(pagina);
};
// ---------- informar un problema ----------
// Sin Sentry ni reportero en la nube (decisión 2026-10-17, ver docs/APP.md): la app guarda sus
// fallas en logs/app-fallas.log de la carpeta de datos, y el informe se muestra entero antes de
// que la persona lo copie. Nada sale solo.
const fallasSesion = [];
function registrarFalla(origen, texto) {
  const linea = `${new Date().toISOString()} · ${origen} · ${String(texto || "").slice(0, 1500)}`;
  if (fallasSesion.length >= 20 || fallasSesion.some((x) => x.slice(24) === linea.slice(24))) return;
  fallasSesion.push(linea);
  try { window.go?.main?.App?.RegistrarFalla?.(linea); } catch {}
}
window.addEventListener("error", (ev) => registrarFalla("interfaz", `${ev.message} (${(ev.filename || "").split("/").pop()}:${ev.lineno})${ev.error?.stack ? "\n" + ev.error.stack : ""}`));
window.addEventListener("unhandledrejection", (ev) => registrarFalla("interfaz (promesa)", ev.reason?.stack || ev.reason));
const abrirExterno = (u) => (window.go?.main?.App?.AbrirURL ? window.go.main.App.AbrirURL(u) : window.open(u, "_blank"));
const informeBasico = () => `Informe de Faro (desde el navegador)\nMotor ${estado.version || "?"} en ${estado.url || "?"} · ambiente ${estado.ambiente || "?"}\nNavegador: ${navigator.userAgent}\nFecha: ${new Date().toISOString()}\n\n== Fallas de esta sesión ==\n${fallasSesion.length ? fallasSesion.join("\n") : "(ninguna)"}\n\nLos registros completos están en la carpeta de datos del motor (logs/).\n`;
window.informarProblema = async () => {
  let texto;
  try { texto = window.go?.main?.App?.InformeProblema ? await window.go.main.App.InformeProblema() : informeBasico(); }
  catch (e) { texto = informeBasico() + `\n(no pude leer los registros: ${e.message || e})\n`; }
  const v = await dialogo({ titulo: "Informar un problema", ancho: 680,
    cuerpo: `<p class="mut">Esto es <b>todo</b> lo que se compartiría: la versión, el sistema y las últimas líneas de los registros, con los RUT y las claves tachados. <b>Nada sale de tu computador solo</b>: tú lo copias y lo pegas donde quieras, por ejemplo en un reporte en GitHub.</p>
      <textarea class="informe" readonly>${esc(texto)}</textarea>
      <p class="mut">Agrega qué estabas haciendo cuando pasó: es lo que más ayuda a arreglarlo.</p>`,
    botones: [{ texto: "Cerrar", clase: "sec", valor: false }, { texto: "Copiar y abrir GitHub", clase: "sec", valor: "github" }, { texto: "Copiar el informe", valor: true }] });
  if (!v) return;
  try { await navigator.clipboard.writeText(texto); aviso("Informe copiado. Pégalo donde lo vayas a mandar.", 5000); }
  catch { aviso("No pude copiar solo: selecciona el texto del informe y cópialo tú.", 6000); }
  if (v === "github") abrirExterno(`https://github.com/SebastianMoyano/faro-dte/issues/new?title=${encodeURIComponent(`Problema en Faro ${estado.version || ""}`.trim())}&body=${encodeURIComponent("Qué estaba haciendo:\n\n\nInforme de la app (pega aquí lo copiado):\n\n")}`);
};
// UF, UTM y dólar del día (los trae el núcleo Go a la base; Python los lee).
async function pintarValores() {
  try {
    const v = await api("GET", "/api/v1/valores");
    const f = (x, dec) => (x ? x.valor.toLocaleString("es-CL", { minimumFractionDigits: dec, maximumFractionDigits: dec }) + (x.rezago_dias > 2 ? ` <span class="mut">(del ${fechaLarga(x.fecha_valor)})</span>` : "") : "—");
    const el = $("valores_hoy"); if (el) el.innerHTML = `Hoy: UF ${f(v.uf, 2)} · UTM ${f(v.utm, 0)} · dólar ${f(v.dolar, 2)} · <span class="lnk" onclick="ir('valores')">otros días</span>`;
  } catch {}
}
window.reiniciarDemo = async () => {
  const ok = await dialogo({ titulo: "Empezar de nuevo", cuerpo: `<p class="mut">Se borran todos los documentos de práctica y la próxima boleta vuelve a ser la N° 1. Solo pasa en la práctica.</p>`,
    botones: [{ texto: "Volver", clase: "sec", valor: false }, { texto: "Borrar la práctica", clase: "peligro", valor: true }] });
  if (!ok) return;
  const r = await api("POST", "/api/v1/simulacion/reiniciar");
  aviso(`Listo: ${r.documentos_borrados} documento(s) de práctica borrados.`); render();
};

// ---------- claves del SII (canal gratuito) ----------
// Viven en memoria del proceso mientras la app está abierta. Solo se recuerdan (cifradas en
// el keystore del motor) si la persona marca la casilla. Nunca van a logs ni a URLs.
const clavesSesion = {};
async function pedirClaves(rut, necesitaCert) {
  const est = await api("GET", `/api/v1/keystore/claves-sii/${encodeURIComponent(rut)}`).catch(() => ({}));
  const ya = clavesSesion[rut] || {};
  const tieneTrib = ya.tributaria || est.tiene_tributaria, tieneCert = ya.certificado || est.tiene_certificado;
  if (tieneTrib && (!necesitaCert || tieneCert) && (ya.rut_usuario || est.rut_usuario)) return { ...ya, rut_usuario: ya.rut_usuario || est.rut_usuario, recordar: false };
  return new Promise((resolve) => {
    const m = document.createElement("div");
    m.className = "modal";
    m.innerHTML = `<div class="card" style="max-width:460px;width:92%">
      <h2 style="margin-top:0">Claves del SII</h2>
      <p class="mut">Para operar el sistema gratuito del SII por <b>${esc(rut)}</b>, la app entra al SII <b>como tú</b>, igual que en el navegador. Las claves se usan solo para eso, no salen de este computador y se borran al cerrar la app, salvo que marques recordarlas.</p>
      <label>Tu RUT (persona autorizada en el SII)</label><input id="k_rut" value="${esc(ya.rut_usuario || est.rut_usuario || "")}" placeholder="12345678-5">
      <label>Clave tributaria ${tieneTrib ? '<span class="mut">(ya recordada; deja vacío para usarla)</span>' : ""}</label><input id="k_trib" type="password" autocomplete="off">
      ${necesitaCert ? `<label>Clave del certificado digital (para firmar) ${tieneCert ? '<span class="mut">(ya recordada; deja vacío para usarla)</span>' : ""}</label><input id="k_cert" type="password" autocomplete="off">` : ""}
      <div class="chk"><input type="checkbox" id="k_rec"><label for="k_rec" style="margin:0;font-weight:500">Recordar en este computador (se guardan cifradas, como el certificado)</label></div>
      <div class="row"><button class="sec" id="k_no">Cancelar</button><button id="k_ok">Continuar</button></div></div>`;
    document.body.appendChild(m);
    m.querySelector("#k_no").onclick = () => { m.remove(); resolve(null); };
    m.querySelector("#k_ok").onclick = () => {
      const r = { rut_usuario: m.querySelector("#k_rut").value.trim(), tributaria: m.querySelector("#k_trib").value,
                  certificado: necesitaCert ? m.querySelector("#k_cert").value : "", recordar: m.querySelector("#k_rec").checked };
      if (!r.rut_usuario) return aviso("Falta tu RUT.");
      if (!r.tributaria && !tieneTrib) return aviso("Falta la clave tributaria.");
      clavesSesion[rut] = { rut_usuario: r.rut_usuario, tributaria: r.tributaria || ya.tributaria || "", certificado: r.certificado || ya.certificado || "" };
      m.remove(); resolve(r);
    };
  });
}
window.sincronizarGratuito = async () => {
  const e = empresa();
  const k = await pedirClaves(e.rut, true);
  if (!k) return;
  aviso("Entrando al SII y listando lo emitido… puede tardar un minuto.", 8000);
  try {
    // El SII engine corre en el propio cascarón (nucleo/siiengine, Go); el motor Python queda de respaldo.
    const enGo = window.go?.main?.App?.SIIEngineSincronizar;
    const r = enGo
      ? JSON.parse(await enGo(e.rut, k.rut_usuario, k.tributaria || "", k.certificado || "", !!k.recordar))
      : await api("POST", "/api/v1/mipyme/sincronizar", { rut_empresa: e.rut, rut_usuario: k.rut_usuario, clave_tributaria: k.tributaria,
          clave_certificado: k.certificado, con_xml: !!k.certificado, con_pdf: true, recordar: !!k.recordar });
    aviso(`Listo: ${r.listados ?? 0} listados, ${r.importados ?? 0} nuevos, ${r.ya_existian ?? 0} ya estaban, ${r.pdf_ok ?? 0} PDF${r.xml_ok ? `, ${r.xml_ok} XML` : ""}${r.motor === "go" ? " (núcleo Go)" : ""}.`, 8000);
    ir("documentos");
  } catch (err) { aviso("No se pudo: " + err.message, 9000); }
};

// ---------- cobrar ----------
vistas.cobrar = async (el) => {
  await cargarNegocios();
  const e = empresa();
  el.innerHTML = `
    <h1>Emitir una boleta o factura</h1>
    <p class="sub">Llena lo mínimo. Abajo ves cómo queda el documento antes de emitirlo.</p>
    <div class="card">
      <p class="mut">Desde <b>${esc(e.razon_social || e.rut)}</b>${negocios.length ? " (cambia la empresa arriba)" : ""}.</p>
      <label>A quién</label>
      <div class="radio">
        <label><input type="radio" name="c_quien" value="persona" checked> Una persona (boleta)</label>
        <label><input type="radio" name="c_quien" value="empresa"> Una empresa (factura)</label>
      </div>
      <div id="c_datos_emp" style="display:none">
        <div class="row"><div><label>RUT del cliente</label><input id="c_rut" placeholder="${e.demo ? "76111111-6" : "12345678-5"}" value="${e.demo ? "76111111-6" : ""}"></div>
        <div><label>Nombre o razón social</label><input id="c_nombre" placeholder="CLIENTE DEMO SPA" value="${e.demo ? "CLIENTE DEMO SPA" : ""}"></div></div>
        <div id="c_datos_sii" style="display:${!e.demo && !e.usa_sw_propio ? "block" : "none"}">
          <p class="mut">El sistema del SII pide también el giro y la dirección del cliente (como están en el SII; el giro se corta a 40 letras).</p>
          <div class="row"><div><label>Giro del cliente</label><input id="c_giro" placeholder="Comercio"></div><div><label>Dirección</label><input id="c_dir" placeholder="Calle 123"></div></div>
          <div class="row"><div><label>Comuna</label><input id="c_comuna" placeholder="SANTIAGO"></div><div><label>Ciudad</label><input id="c_ciudad" placeholder="Santiago"></div></div>
        </div>
      </div>
      <label>Qué le vendes</label><input id="c_que" placeholder="${e.demo ? "Ej.: Café" : "Ej.: Servicio"}">
      <div class="row"><div><label>Monto</label><input id="c_monto" type="number" inputmode="numeric" min="0" step="1" placeholder="${e.demo ? "Ej.: 1190" : ""}"></div>
        <div><label>Ese monto es…</label><select id="c_modo"><option value="total">con IVA incluido (lo que paga)</option><option value="neto">neto (sin IVA)</option></select></div></div>
      <details class="mas" id="c_mas" ${!e.demo && !e.usa_sw_propio ? "open" : ""}><summary>Más opciones: sin IVA, monto en UF</summary>
        <div class="chk"><input type="checkbox" id="c_exento"><label for="c_exento" style="margin:0;font-weight:500">No lleva IVA (educación, salud, arriendo sin muebles…)</label></div>
        <label>Monto en UF (se convierte con la UF de hoy y se escribe arriba)</label><input id="c_uf" type="number" min="0" step="0.01" placeholder="80">
        <p class="mut" id="c_uf_nota"></p>
      </details>
      <div class="totales"><div><span>Neto</span><b id="t_neto">—</b></div><div><span>IVA 19%</span><b id="t_iva">—</b></div><div><span>Total</span><b id="t_total">—</b></div></div>
      <p class="mut" style="margin-top:10px">El total es lo que paga la persona. El IVA (19%) no es tuyo: lo cobras y después se lo pasas al Estado. Lo tuyo es el neto.</p>
      <div class="accion-fija"><p class="mut" id="c_tipo" style="margin:0"></p><button id="c_btn" style="margin:0;flex:none" onclick="emitir()">Revisar y emitir</button></div>
      <div id="c_aviso"></div>
    </div>`;
  const rec = () => {
    const monto = Number($("c_monto").value || 0), modo = $("c_modo").value, ex = $("c_exento").checked;
    let neto = modo === "neto" ? monto : Math.round(monto / (1 + IVA));
    if (ex) neto = monto;
    const iva = ex ? 0 : Math.round(neto * IVA);
    $("t_neto").textContent = clp(neto); $("t_iva").textContent = clp(iva); $("t_total").textContent = clp(neto + iva);
    const aEmpresa = document.querySelector('input[name="c_quien"]:checked').value === "empresa";
    $("c_datos_emp").style.display = aEmpresa ? "block" : "none";
    const tipo = aEmpresa ? (ex ? 34 : 33) : (ex ? 41 : 39);
    $("c_tipo").innerHTML = `Vas a emitir una <b>${TIPOS[tipo].toLowerCase()}</b>.`;
    const w = mundo();
    $("c_aviso").innerHTML = w === "real"
      ? `<div class="banner rojo"><b>Esto es real.</b> Un folio gastado no vuelve. Revisa el RUT, el monto y el tipo antes de emitir.</div>`
      : w === "pruebas" ? `<div class="banner">Ambiente de <b>pruebas del SII</b>: el documento no tiene efecto tributario.</div>`
      : `<div class="banner verde"><b>Práctica.</b> El documento se arma, se firma y se timbra igual que uno real, pero no sale de aquí.</div>`;
    return { neto, iva, tipo, aEmpresa, ex };
  };
  el.querySelectorAll("input,select").forEach((i) => i.addEventListener("input", rec));
  // Monto en UF: se convierte con la UF vigente a la fecha de emisión (hoy) y se escribe en Monto.
  let ufHoy = null;
  api("GET", "/api/v1/valores/uf").then((v) => { ufHoy = v; $("c_uf_nota").textContent = `UF de hoy: ${num(v.valor, 2)}${v.rezago_dias > 0 ? " (del " + fechaLarga(v.fecha_valor) + ")" : ""}`; }).catch(() => { $("c_uf_nota").textContent = "Todavía no hay UF guardada: abre Valores para traerla."; });
  $("c_uf").addEventListener("input", () => {
    const uf = Number($("c_uf").value || 0);
    if (!uf || !ufHoy) return;
    const pesos = Math.round(uf * ufHoy.valor);
    $("c_monto").value = pesos; $("c_modo").value = "neto";
    $("c_uf_nota").textContent = `${num(uf, 2)} UF × ${num(ufHoy.valor, 2)} (UF del ${fechaLarga(ufHoy.fecha_valor)}) = $ ${num(pesos, 0)}`;
    rec();
  });
  rec();
  window.emitir = async () => {
    const { neto, iva, tipo, aEmpresa, ex } = rec();
    if (!neto) return aviso("Falta el monto.");
    if (aEmpresa && !$("c_rut").value.trim()) return aviso("Falta el RUT del cliente.");
    const que = $("c_que").value.trim() || "Venta";
    const total = neto + iva;
    const para = aEmpresa ? `${$("c_nombre").value.trim() || "CLIENTE"} (${$("c_rut").value.trim()})` : "Consumidor final (una persona, sin nombre)";
    // La revisión va antes de emitir con el motor propio o en práctica; con el sistema del SII
    // la revisión es lo que el propio SII valida, más abajo.
    if ((e.demo || e.usa_sw_propio) && !(await revisar({ tipo, de: e.razon_social || e.rut, para, que, neto, iva, total, w: mundo() }))) return;
    $("c_btn").disabled = true;
    try {
      if (e.demo) {
        // En boletas el precio va CON IVA incluido (así las exige el SII); en facturas, neto.
        const precio = tipo === 39 ? total : neto;
        const body = { tipo_dte: tipo, items: [{ nombre: que, precio_unitario: precio, exento: ex }] };
        if (aEmpresa) body.receptor = { rut: $("c_rut").value.trim(), razon_social: $("c_nombre").value.trim() || "CLIENTE", es_contribuyente: true };
        const r = await api("POST", "/api/v1/simulacion/emitir", body);
        aviso(`Listo: ${TIPOS[r.tipo_dte].toLowerCase()} N° ${r.folio} emitida. Así quedó.`, 6000);
        ir(`ver?id=${r.dte_id}`);
        return;
      }
      if (!e.usa_sw_propio) {
        // SII engine: la app opera el sistema de facturación del SII por la empresa (nucleo/siiengine, Go).
        // Hoy cubre la factura exenta; las boletas van por eboleta (sin captura todavía).
        if (tipo !== 34) {
          $("c_aviso").innerHTML += `<div class="banner"><b>${esc(e.razon_social || e.rut)}</b> emite en el sistema del SII, no con el motor propio. Desde esta app, por ahora, solo la <b>factura exenta</b> (marca "No lleva IVA" y "Una empresa"). Las boletas siguen en eboleta.sii.cl.</div>`;
          return;
        }
        const enGo = window.go?.main?.App?.SIIEngineFactura;
        if (!enGo) { $("c_aviso").innerHTML += `<div class="banner">Esta versión del cascarón no trae el SII engine.</div>`; return; }
        const faltan = [["c_giro", "giro"], ["c_dir", "dirección"], ["c_comuna", "comuna"], ["c_ciudad", "ciudad"]].filter(([id]) => !$(id).value.trim()).map(([, n]) => n);
        if (faltan.length) return aviso("Faltan datos del cliente para el SII: " + faltan.join(", ") + ".");
        const k = await pedirClaves(e.rut, true);
        if (!k) return;
        const doc = { receptor: { rut: $("c_rut").value.trim(), razon_social: $("c_nombre").value.trim() || "CLIENTE", giro: $("c_giro").value.trim(),
                                  direccion: $("c_dir").value.trim(), comuna: $("c_comuna").value.trim(), ciudad: $("c_ciudad").value.trim() },
                      item: { nombre: que, precio: neto, descripcion: "" }, fecha: new Date().toISOString().slice(0, 10), forma_pago: "1" };
        aviso("Validando en el SII (sin gastar folio)…", 6000);
        const v = JSON.parse(await enGo(e.rut, k.rut_usuario, k.tributaria || "", k.certificado || "", !!k.recordar, JSON.stringify(doc), false));
        const nuevos = v.campos_nuevos?.length ? `<p class="mut">⚠️ El formulario del SII trae campos nuevos: ${esc(v.campos_nuevos.join(", "))}.</p>` : "";
        $("c_aviso").innerHTML += `<div class="banner"><b>El SII validó el documento:</b> factura exenta de <b>${clp(v.monto_total)}</b> a <b>${esc(v.receptor.razon_social)}</b> (${esc(v.receptor.rut)}), ${esc(v.receptor.direccion)}, ${esc(v.receptor.comuna)}; fecha ${esc(v.fecha)}, contado. Emisor según el SII: ${esc(v.emisor.razon_social)}.${nuevos}
          ${v.cadena_verificada
            ? `<button id="c_sii_ok">Emitir de verdad en el SII (gasta un folio)</button>`
            : `<p class="mut"><b>Todavía no se emite desde aquí:</b> la cadena de emisión desde el núcleo Go espera su primera corrida real verificada. Hasta entonces se emite con el comando <code>nucleo-siiengine factura</code> (docs/PLAN-PORT.md, fase 6).</p>`}</div>`;
        const b = $("c_sii_ok");
        if (b) b.onclick = async () => {
          if (!(await revisar({ tipo: 34, de: v.emisor.razon_social, para: `${v.receptor.razon_social} (${v.receptor.rut})`, que, neto: v.monto_total, iva: 0, total: v.monto_total, w: "real",
            extra: `<p class="mut">Validado por el SII el ${fechaLarga(v.fecha)}, contado.</p>` }))) return;
          b.disabled = true;
          try {
            const r = JSON.parse(await enGo(e.rut, k.rut_usuario, k.tributaria || "", k.certificado || "", false, JSON.stringify(doc), true));
            aviso(`Emitida en el SII: factura exenta folio ${r.folio} (${clp(r.monto_total)}).`, 8000);
            ir(`ver?id=${r.dte_id}`);
          } catch (err) { $("c_aviso").innerHTML += `<div class="banner rojo">${esc(err.message)}</div>`; b.disabled = false; }
        };
        return;
      }
      // Empresa real con motor propio: firma y persiste el NÚCLEO EN GO (fase 4 del port);
      // Python dibuja el PDF. Misma base, mismas rutas, mismos bytes que el motor Python.
      const doc = { tipo_dte: tipo, folio: 0, fecha_emision: new Date().toISOString().slice(0, 10), emisor: await emisorDe(e.rut),
        receptor: aEmpresa ? { rut: $("c_rut").value.trim(), razon_social: $("c_nombre").value.trim() || "CLIENTE" }
                           : { rut: "66666666-6", razon_social: "CONSUMIDOR FINAL" },
        items: [{ numero_linea: 1, nombre: que, cantidad: 1, precio_unitario: tipo === 39 ? total : neto, exento: ex }], forma_pago: 1 };
      const r = JSON.parse(await window.go.main.App.EmitirNucleo(e.id, JSON.stringify(doc)));
      aviso(`Listo: ${TIPOS[r.tipo_dte]} folio ${r.folio}, ${clp(r.monto_total)}.`, 6000);
      ir(`ver?id=${r.dte_id}`);
    } catch (err) { $("c_aviso").innerHTML += `<div class="banner rojo">${esc(err.message)}${err.ir ? ` <span class="lnk" onclick="ir('${err.ir}')">Ir a Mi empresa</span>` : ""}</div>`; }
    finally { $("c_btn").disabled = false; }
  };
};

// Datos del emisor real (la carátula de cada documento): los guarda cada empresa en
// "Mi empresa" (PUT keystore/negocios/{id}). Si todavía no los tiene, se heredan los que el
// motor tenga en su configuración (EMPRESA_*), solo si son de esta misma empresa.
const CAMPOS_EMISOR = { razon_social: "nombre o razón social", giro: "giro", codigo_actividad: "código de actividad", direccion: "dirección", comuna: "comuna", ciudad: "ciudad" };
const datosEmisor = (n) => ({ razon_social: n.razon_social, giro: n.giro, codigo_actividad: n.codigo_actividad, direccion: n.direccion, comuna: n.comuna, ciudad: n.ciudad, email: n.email, telefono: n.telefono });
const emisorCompleto = (n) => Object.keys(CAMPOS_EMISOR).every((k) => n && n[k]);
async function emisorDe(rut) {
  const neg = negocios.find((n) => n.rut === rut) || { rut };
  let e = datosEmisor(neg);
  if (!emisorCompleto(e)) {
    try { const m = await api("GET", "/api/v1/dte/emisor"); if (m.configurada && m.rut === rut) e = { ...e, ...Object.fromEntries(Object.entries(datosEmisor(m)).filter(([, v]) => v)) }; } catch {}
  }
  const faltan = Object.keys(CAMPOS_EMISOR).filter((k) => !e[k]);
  if (faltan.length) {
    const err = new Error(`A ${neg.razon_social || rut} le falta: ${faltan.map((k) => CAMPOS_EMISOR[k]).join(", ")}. Van impresos en cada documento y sin ellos el SII lo rechaza. Se completan en Mi empresa, en un minuto.`);
    err.ir = "empresa"; throw err;
  }
  return { rut, ...e };
}

// ---------- ver (visor de PDF embebido) ----------
vistas.ver = async (el) => {
  let id = param("id");
  if (!id) { try { id = localStorage.getItem("ultimo_dte"); } catch {} }   // sin id: el último documento visto
  const docs = (await api("GET", "/api/v1/dte/documentos?limite=200")).documentos;
  const d = docs.find((x) => String(x.id) === String(id));
  if (!d) {
    el.innerHTML = `<h1>Documento</h1><p class="sub">Aquí se ve un documento emitido: sus partes, el PDF y las acciones.</p>
      <div class="card"><div class="vacio">${icono("vacio")}Todavía no hay ninguno que mostrar.<br><span class="lnk" onclick="ir('cobrar')">Emitir el primero</span></div></div>`;
    return;
  }
  try { localStorage.setItem("ultimo_dte", String(d.id)); } catch {}
  const b64 = d.tiene_pdf ? await window.go.main.App.Bytes(`/api/v1/dte/documentos/${d.id}/pdf`) : null;
  const sim = d.ambiente === "simulacion";
  el.innerHTML = `
    <h1>${esc(TIPOS[d.tipo_dte] || d.tipo_dte)} N° ${d.folio}</h1>
    <p class="sub">${esc(d.razon_social_receptor || d.rut_receptor)} · ${esc(fechaLarga(d.fecha_emision))} · ${clp(d.monto_total)}
      ${sim ? '<span class="pill p-ok">práctica</span>' : ""} ${d.estado === "anulado_simulado" ? '<span class="pill p-no">anulada</span>' : ""}</p>
    <div class="banner ${sim ? "verde" : ""}">Mira las partes: quién vende y su RUT, el <b>folio</b>, la fecha, el detalle, <b>neto, IVA y total</b>, y abajo el <b>timbre</b> (el código de barras cuadrado). ${sim ? "Es idéntico a uno real; solo que este no salió de tu computador." : ""}</div>
    ${b64 ? `<iframe style="width:100%;height:70vh;border:1px solid var(--bd);border-radius:12px;background:#fff" src="data:application/pdf;base64,${b64}"></iframe>` : '<p class="mut">Este documento no tiene PDF.</p>'}
    <div class="row" style="margin-top:12px">
      ${b64 ? `<button class="sec" onclick="bajar(${d.id},'pdf',${d.tipo_dte},${d.folio})">Guardar PDF</button>` : ""}
      ${d.tiene_xml ? `<button class="sec" onclick="bajar(${d.id},'xml',${d.tipo_dte},${d.folio})">Guardar XML</button>` : ""}
      ${sim && ![56, 61].includes(d.tipo_dte) && d.estado !== "anulado_simulado" ? `<button class="sec" onclick="cobroDemo(${d.id}, ${d.tipo_dte}, ${d.folio})">Agregar un cobro que faltó</button><button class="peligro" onclick="anularDemo(${d.id})">Anular con nota de crédito</button>` : ""}
      ${sim && d.tipo_dte === 61 && d.estado !== "anulado_simulado" ? `<button class="peligro" onclick="anularNCDemo(${d.id}, ${d.folio})">Dejar sin efecto esta nota de crédito</button>` : ""}
      ${!sim && d.canal === "motor" && ![56, 61].includes(d.tipo_dte) ? `<button class="sec" onclick="cobroPropio(${d.id})">Agregar un cobro que faltó</button><button class="peligro" onclick="anularPropio(${d.id})">Anular con nota de crédito</button>` : ""}
      ${!sim && d.canal === "motor" && d.tipo_dte === 61 ? `<button class="peligro" onclick="anularNCPropio(${d.id})">Dejar sin efecto esta nota de crédito</button>` : ""}
      <button class="sec" onclick="ir('documentos')">Mis documentos</button>
      <button onclick="ir('cobrar')">Emitir otro</button>
    </div>`;
};
window.anularDemo = async (id) => {
  const d = await docDe(id); if (!d) return;
  if (!(await dialogoAnular(d.tipo_dte, d.folio, d.monto_total, false))) return;
  try { const nc = await api("POST", `/api/v1/simulacion/anular/${id}`, { motivo: "Anula documento" }); aviso(`Nota de crédito N° ${nc.folio} emitida.`); ir(`ver?id=${nc.dte_id}`); }
  catch (e) { aviso(e.message, 6000); }
};
// ---------- notas: la de crédito resta, la de débito suma ----------
// Los mismos diálogos para la práctica y para el motor propio; cada uno explica antes de emitir.
const dialogoCobro = (tipo, folio, real) => dialogo({ titulo: "Agregar un cobro que faltó", cuerpo: `<p class="mut">Si en ${esc(TIPOS[tipo] || "el documento")} N° ${folio} cobraste de menos, no se corrige el original: se emite una <b>nota de débito</b>, que suma la diferencia y queda enganchada a ese documento.</p>
    <label>¿Cuánto faltó cobrar?${[34, 41].includes(tipo) ? "" : " (sin IVA, se agrega solo)"}</label><input id="nd_monto" inputmode="numeric" placeholder="Ej.: 5000">
    <label>¿Qué es?</label><input id="nd_que" placeholder="Ej.: Entrega a domicilio">${real ? `<div class="banner rojo" style="margin-top:12px"><b>Es un documento real</b>: gasta un folio de nota de débito y queda registrado.</div>` : ""}`,
  botones: [{ texto: "Volver", clase: "sec", valor: false }, { texto: "Emitir la nota de débito", valor: true }],
  leer: () => ({ monto: Number($("nd_monto").value.replace(/\D/g, "")), nombre: $("nd_que").value.trim() }) });
const dialogoSinEfecto = (folio, real) => dialogo({ titulo: "Dejar sin efecto una nota de crédito", cuerpo: `<p class="mut">Una nota de crédito tampoco se borra. Si la N° ${folio} salió mal, se emite una <b>nota de débito</b> por el mismo monto: las dos se anulan entre sí y el documento que esa nota había anulado <b>vuelve a valer</b>.</p>${real ? `<div class="banner rojo"><b>Es un documento real</b>: gasta un folio de nota de débito y queda registrado.</div>` : ""}`,
  botones: [{ texto: "Volver", clase: "sec", valor: false }, { texto: "Emitir la nota de débito", clase: "peligro", valor: true }] });
const dialogoAnular = (tipo, folio, total, real) => dialogo({ titulo: "Anular con nota de crédito", cuerpo: `<p class="mut">Se emite una nota de crédito por el total de ${esc(TIPOS[tipo] || "el documento")} N° ${folio} (${clp(total)}). El original no se borra: queda compensado en cero.</p>${real ? `<div class="banner rojo"><b>Es un documento real</b>: gasta un folio de nota de crédito y queda registrado.</div>` : ""}`,
  botones: [{ texto: "Volver", clase: "sec", valor: false }, { texto: "Anular este documento", clase: "peligro", valor: true }] });

// práctica
window.cobroDemo = async (id, tipo, folio) => {
  const ok = await dialogoCobro(tipo, folio, false);
  if (!ok) return;
  if (!ok.monto) return aviso("Escribe cuánto faltó cobrar.");
  try { const nd = await api("POST", `/api/v1/simulacion/nota-debito/${id}`, { monto: ok.monto, nombre: ok.nombre || undefined, motivo: "Corrige montos" }); aviso(`Nota de débito N° ${nd.folio} emitida por ${clp(nd.monto_total)}.`, 6000); ir(`ver?id=${nd.dte_id}`); }
  catch (e) { aviso(e.message, 6000); }
};
window.anularNCDemo = async (id, folio) => {
  if (!(await dialogoSinEfecto(folio, false))) return;
  try { const nd = await api("POST", `/api/v1/simulacion/nota-debito/${id}`, { motivo: "Anula nota de crédito" }); aviso(`Nota de débito N° ${nd.folio} emitida. La nota de crédito quedó sin efecto.`, 6000); ir(`ver?id=${nd.dte_id}`); }
  catch (e) { aviso(e.message, 6000); }
};

// motor propio: el núcleo firma la nota con los folios (CAF) de la empresa y la guarda junto a
// las demás. El original no cambia de estado: la nota queda enganchada a él por la referencia.
const docDe = async (id) => (await api("GET", "/api/v1/dte/documentos?limite=200")).documentos.find((x) => String(x.id) === String(id));
const lineasAnula = (d) => {
  const n = (TIPOS[d.tipo_dte] || "documento").toLowerCase(), l = [];
  if (d.monto_neto) l.push({ nombre: `Anula ${n} folio ${d.folio}`, precio_unitario: d.monto_neto });
  if (d.monto_exento) l.push({ nombre: `Anula ${n} folio ${d.folio} (exento)`, precio_unitario: d.monto_exento, exento: true });
  return l;
};
async function notaPropia(d, tipoNota, codigo, motivo, items) {
  const e = empresa();
  const doc = { tipo_dte: tipoNota, folio: 0, fecha_emision: new Date().toISOString().slice(0, 10), emisor: await emisorDe(e.rut),
    receptor: { rut: d.rut_receptor, razon_social: d.razon_social_receptor || "CONSUMIDOR FINAL" },
    items: items.map((it, i) => ({ numero_linea: i + 1, nombre: it.nombre.slice(0, 80), cantidad: 1, precio_unitario: it.precio_unitario, exento: !!it.exento })),
    referencias: [{ numero_linea: 1, tipo_doc_ref: d.tipo_dte, folio_ref: d.folio, fecha_doc_ref: String(d.fecha_emision).slice(0, 10), codigo_ref: codigo, razon_ref: motivo.slice(0, 90) }], forma_pago: 1 };
  try { return JSON.parse(await window.go.main.App.EmitirNucleo(e.id, JSON.stringify(doc))); }
  catch (err) {
    if (/CAF activo|No se encontró un CAF|sin folios/i.test(String(err.message || err))) { const e2 = new Error(`A ${e.razon_social || e.rut} le faltan folios de ${TIPOS[tipoNota].toLowerCase()}: sin ellos no hay número para la nota. Se piden en Mi empresa, paso 3, de a 5.`); e2.ir = "empresa"; throw e2; }
    throw err;
  }
}
async function errorNota(err) {
  if (!err.ir) return aviso(err.message || String(err), 7000);
  const ok = await dialogo({ titulo: "Falta algo", cuerpo: `<div class="banner rojo">${esc(err.message)}</div>`, botones: [{ texto: "Cerrar", clase: "sec", valor: false }, { texto: "Ir a Mi empresa", valor: true }] });
  if (ok) ir(err.ir);
}
window.anularPropio = async (id) => {
  const d = await docDe(id); if (!d) return;
  if (!(await dialogoAnular(d.tipo_dte, d.folio, d.monto_total, true))) return;
  if (!(await pinParaReal())) return;
  try { const r = await notaPropia(d, 61, 1, "Anula documento", lineasAnula(d)); aviso(`Nota de crédito N° ${r.folio} emitida.`, 6000); ir(`ver?id=${r.dte_id}`); } catch (e) { errorNota(e); }
};
window.cobroPropio = async (id) => {
  const d = await docDe(id); if (!d) return;
  const ok = await dialogoCobro(d.tipo_dte, d.folio, true);
  if (!ok) return;
  if (!(await pinParaReal())) return;
  if (!ok.monto) return aviso("Escribe cuánto faltó cobrar.");
  const exento = [34, 41].includes(d.tipo_dte) || (!d.monto_neto && !!d.monto_exento);
  try { const r = await notaPropia(d, 56, 3, "Corrige montos", [{ nombre: ok.nombre || `Cobro que faltó en ${(TIPOS[d.tipo_dte] || "documento").toLowerCase()} folio ${d.folio}`, precio_unitario: ok.monto, exento }]); aviso(`Nota de débito N° ${r.folio} emitida por ${clp(r.monto_total)}.`, 6000); ir(`ver?id=${r.dte_id}`); } catch (e) { errorNota(e); }
};
window.anularNCPropio = async (id) => {
  const d = await docDe(id); if (!d) return;
  if (!(await dialogoSinEfecto(d.folio, true))) return;
  if (!(await pinParaReal())) return;
  try { const r = await notaPropia(d, 56, 1, "Anula nota de crédito", lineasAnula(d)); aviso(`Nota de débito N° ${r.folio} emitida. La nota de crédito quedó sin efecto.`, 6000); ir(`ver?id=${r.dte_id}`); } catch (e) { errorNota(e); }
};

// ---------- mi mes ----------
// Las rutas viejas siguen llegando al mismo lugar.
vistas.mes = async () => ir("documentos?tab=mes");
vistas.corregir = async () => ir("documentos");
function pintarMes(el, e) {
  const hoy = new Date(), meses = [];
  for (let i = 0; i < 24; i++) { const d = new Date(hoy.getFullYear(), hoy.getMonth() - i, 1); meses.push(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`); }
  el.innerHTML = `
    <p class="mut">Lo que esta app tiene registrado. El SII arma su propio registro con lo que le llegó; si no coinciden, falta algo de un lado.</p>
    <div class="card"><div class="row">
      <div><label>Mes</label><select id="m_per" onchange="calcMes()">${meses.map((p) => `<option value="${p}">${mesLargo(p)}</option>`).join("")}</select></div></div>
      <div id="m_res"></div></div>`;
  window.calcMes = async () => {
    const p = $("m_per").value;
    const r = await api("GET", "/api/v1/dte/documentos?" + new URLSearchParams({ limite: 200, rut_emisor: e.rut }));
    const docs = r.documentos.filter((d) => (d.fecha_emision || "").startsWith(p));
    const signo = (d) => (d.tipo_dte === 61 ? -1 : 1);
    const sum = (k) => docs.reduce((a, d) => a + signo(d) * (d[k] || 0), 0);
    const neto = sum("monto_neto") + sum("monto_exento"), iva = sum("iva"), total = sum("monto_total");
    $("m_res").innerHTML = `
      <div class="totales"><div><span>Vendido (sin IVA)</span><b>${clp(neto)}</b></div><div><span>IVA cobrado</span><b>${clp(iva)}</b></div><div><span>Total con IVA</span><b>${clp(total)}</b></div></div>
      <p class="mut">${docs.length} documento(s) en ${mesLargo(p)}. Las notas de crédito restan; las de débito suman. El IVA de tus <b>compras</b> con factura (crédito) se descuenta de este débito en el F29; eso lo trae el SII, no esta lista.</p>
      <div class="banner verde">Aunque no haya nada, el F29 se presenta igual: <b>sin movimiento</b>.</div>
      ${docs.length ? tabla(docs) : ""}`;
  };
  calcMes();
}

// ---------- en el SII: los últimos movimientos tal como los tiene el SII ----------
// Sale del registro de compras y ventas del SII, con el certificado de la empresa. Solo se pide
// al pulsar el botón: cada consulta abre una sesión en el SII. Lo último traído queda en memoria.
const movsSII = {};
function pintarSII(el, e) {
  if (e.demo) {
    el.innerHTML = `<div class="card"><p>En la empresa de práctica no hay nada en el SII. Con tu propia empresa, aquí ves lo que el SII tiene registrado: lo que vendiste y lo que te facturaron.</p></div>`;
    return;
  }
  el.innerHTML = `
    <p class="mut">Lo que el SII tiene registrado de <b>${esc(e.razon_social || e.rut)}</b> este mes y el anterior: tus ventas y las facturas que te hicieron otros (compras). Un documento recién emitido puede tardar un rato en aparecer aquí.</p>
    <div class="row"><button id="sii_traer">Traer del SII</button></div>
    <div id="sii_res"></div>`;
  const pintar = (r) => {
    const fila = (m) => `<tr><td>${esc(fechaCorta(m.fecha_doc))}</td><td><span class="pill ${m.tipo_operacion === "VENTA" ? "p-ok" : "p-mut"}">${m.tipo_operacion === "VENTA" ? "venta" : "compra"}</span></td>
      <td>${esc(TIPOS[m.tipo_dte] || m.tipo_dte)}</td><td>${m.folio}</td><td>${esc(m.razon_social || m.rut_contraparte || "")}</td>
      <td class="num">${clp(m.monto_total)}</td><td>${m.estado_sii && m.estado_sii !== "REGISTRO" ? `<span class="pill p-warn">${esc(m.estado_sii.toLowerCase().replace("_", " "))}</span>` : ""}</td></tr>`;
    const meses = r.periodos.map((p) => { const c = r.documentos[p] || {}; return `${mesLargo(p.slice(0, 4) + "-" + p.slice(4))}: ${c.ventas || 0} venta(s) y ${c.compras || 0} compra(s)`; }).join(" · ");
    $("sii_res").innerHTML = r.sin_datos
      ? `<div class="vacio">${icono("vacio")}El SII no tiene movimientos de esta empresa en estos meses.</div>`
      : `<p class="mut">${esc(meses)}.${r.total > r.movimientos.length ? ` Se muestran los ${r.movimientos.length} más recientes.` : ""}</p>
         <div class="card"><div class="tablewrap"><table><tr><th>Fecha</th><th></th><th>Documento</th><th>N°</th><th>De / para</th><th class="num">Total</th><th></th></tr>${r.movimientos.map(fila).join("")}</table></div></div>`;
  };
  $("sii_traer").onclick = async () => {
    const b = $("sii_traer");
    b.disabled = true; b.textContent = "Consultando al SII…";
    try { movsSII[e.rut] = await api("GET", `/api/v1/keystore/negocios/${e.id}/movimientos?limite=100`); pintar(movsSII[e.rut]); }
    catch (err) { $("sii_res").innerHTML = `<div class="banner rojo">${esc(err.message)}</div>`; }
    b.disabled = false; b.textContent = "Volver a traer del SII";
  };
  if (movsSII[e.rut]) { pintar(movsSII[e.rut]); $("sii_traer").textContent = "Volver a traer del SII"; }
}

// Anular por el SII engine: el portal pre-construye la NC del documento (receptor e ítems tal
// cual), el SII la valida en seco, y solo se emite con la cadena verificada desde Go.
window.anularSII = async (id) => {
  const e = empresa();
  const enGo = window.go?.main?.App?.SIIEngineNotaCredito;
  if (!enGo) return aviso("Esta versión del cascarón no trae el SII engine.");
  const k = await pedirClaves(e.rut, true);
  if (!(await pinParaReal())) return;   // el gratuito siempre es real
  if (!k) return;
  if (!k.certificado && !k.rut_usuario) return;
  aviso("Pidiendo al SII la nota de crédito de anulación (sin gastar folio)…", 6000);
  try {
    const v = JSON.parse(await enGo(e.rut, k.rut_usuario, k.tributaria || "", k.certificado || "", !!k.recordar, id, false));
    const lineas = (v.items || []).map((i) => `${esc(i.Nombre)} × ${i.Cantidad} = ${clp(i.Precio * i.Cantidad)}`).join("; ");
    $("cor_aviso").innerHTML = `<div class="banner"><b>El SII validó la nota de crédito:</b> anula ${esc(TIPOS[v.anula.tipo] || v.anula.tipo)} N° ${v.anula.folio} del ${esc(v.anula.fecha)} a <b>${esc(v.receptor.razon_social)}</b> por <b>${clp(v.monto_total)}</b> (${lineas}).
      ${v.cadena_verificada
        ? `<button id="cor_ok">Emitir la nota de crédito en el SII (gasta un folio)</button>`
        : `<p class="mut"><b>Todavía no se emite desde aquí:</b> la cadena de la nota de crédito desde el núcleo Go espera su primera corrida real verificada. Hasta entonces: <code>nucleo-siiengine nc-anular</code> (docs/PLAN-PORT.md, fase 6).</p>`}</div>`;
    const b = $("cor_ok");
    if (b) b.onclick = async () => {
      const ok = await dialogo({ titulo: "Emitir la nota de crédito en el SII", cuerpo: `<div class="rev"><span>Anula</span><b>${esc(TIPOS[v.anula.tipo] || v.anula.tipo)} N° ${v.anula.folio}</b></div><div class="rev"><span>Para</span><b>${esc(v.receptor.razon_social)}</b></div><div class="rev"><span>Total</span><b>${clp(v.monto_total)}</b></div>
          <div class="banner rojo"><b>Esto es real.</b> Un folio gastado no vuelve.</div>`,
        botones: [{ texto: "Volver", clase: "sec", valor: false }, { texto: `Anular en el SII por ${clp(v.monto_total)}`, clase: "peligro", valor: true }] });
      if (!ok) return;
      b.disabled = true;
      try {
        const r = JSON.parse(await enGo(e.rut, k.rut_usuario, k.tributaria || "", k.certificado || "", false, id, true));
        aviso(`Nota de crédito folio ${r.folio} emitida en el SII.`, 8000);
        ir(`ver?id=${r.dte_id}`);
      } catch (err) { $("cor_aviso").innerHTML += `<div class="banner rojo">${esc(err.message)}</div>`; b.disabled = false; }
    };
  } catch (err) { $("cor_aviso").innerHTML = `<div class="banner rojo">${esc(err.message)}</div>`; }
};

// Dejar sin efecto una nota de crédito del sistema del SII: el portal pre-construye la nota de
// débito (misma cadena que la NC, TIPO_NOTA=56), el SII la valida en seco, y solo se emite
// cuando la cadena esté verificada desde el motor.
window.anularNCSII = async (id) => {
  const e = empresa();
  const fn = window.go?.main?.App?.SIIEngineNotaDebito;
  if (!fn) return aviso("Esta versión del cascarón no trae el SII engine.");
  const k = await pedirClaves(e.rut, true);
  if (!(await pinParaReal())) return;   // el gratuito siempre es real
  if (!k) return;
  if (!k.certificado && !k.rut_usuario) return;
  aviso("Pidiendo al SII la nota de débito (sin gastar folio)…", 6000);
  try {
    const v = JSON.parse(await fn(e.rut, k.rut_usuario, k.tributaria || "", k.certificado || "", !!k.recordar, id, false));
    const lineas = (v.items || []).map((i) => `${esc(i.Nombre || i.nombre)} × ${i.Cantidad || i.cantidad} = ${clp((i.Precio || i.precio) * (i.Cantidad || i.cantidad))}`).join("; ");
    $("cor_aviso").innerHTML = `<div class="banner"><b>El SII validó la nota de débito:</b> deja sin efecto la nota de crédito N° ${v.anula.folio} del ${esc(v.anula.fecha)} a <b>${esc(v.receptor.razon_social)}</b> por <b>${clp(v.monto_total)}</b> (${lineas}). El documento que esa nota anulaba vuelve a valer.
      ${v.cadena_verificada
        ? `<button id="cor_ok">Emitir la nota de débito en el SII (gasta un folio)</button>`
        : `<p class="mut"><b>Todavía no se emite desde aquí:</b> esta cadena espera su primera corrida real verificada. Hasta entonces, desde la línea de comandos: <code>nucleo-siiengine nd-anular</code> (docs/MIPYME.md).</p>`}</div>`;
    const b = $("cor_ok");
    if (b) b.onclick = async () => {
      const ok = await dialogo({ titulo: "Emitir la nota de débito en el SII", cuerpo: `<div class="rev"><span>Deja sin efecto</span><b>Nota de crédito N° ${v.anula.folio}</b></div><div class="rev"><span>Para</span><b>${esc(v.receptor.razon_social)}</b></div><div class="rev"><span>Total</span><b>${clp(v.monto_total)}</b></div>
          <div class="banner rojo"><b>Esto es real.</b> Un folio gastado no vuelve.</div>`,
        botones: [{ texto: "Volver", clase: "sec", valor: false }, { texto: `Emitir en el SII por ${clp(v.monto_total)}`, clase: "peligro", valor: true }] });
      if (!ok) return;
      b.disabled = true;
      try {
        const r = JSON.parse(await fn(e.rut, k.rut_usuario, k.tributaria || "", k.certificado || "", false, id, true));
        aviso(`Nota de débito folio ${r.folio} emitida en el SII.`, 8000);
        ir(`ver?id=${r.dte_id}`);
      } catch (err) { $("cor_aviso").innerHTML += `<div class="banner rojo">${esc(err.message)}</div>`; b.disabled = false; }
    };
  } catch (err) { $("cor_aviso").innerHTML = `<div class="banner rojo">${esc(err.message)}</div>`; }
};

// ---------- documentos ----------
vistas.documentos = async (el) => {
  await cargarNegocios();
  const e = empresa();
  const tab = ["mes", "sii"].includes(param("tab")) ? param("tab") : "todos";
  el.innerHTML = `
    <h1>Documentos</h1>
    <p class="sub">Boletas, facturas y notas de <b>${esc(e.razon_social || e.rut)}</b>${e.demo ? " (práctica)" : ", tanto las del motor como las del gratuito del SII"}.</p>
    <div class="tabs"><a class="${tab === "todos" ? "on" : ""}" href="#documentos">Todos</a><a class="${tab === "mes" ? "on" : ""}" href="#documentos?tab=mes">Mi mes</a><a class="${tab === "sii" ? "on" : ""}" href="#documentos?tab=sii">En el SII</a></div>
    <div id="doc_cuerpo"></div>`;
  if (tab === "mes") { pintarMes($("doc_cuerpo"), e); return; }
  if (tab === "sii") { pintarSII($("doc_cuerpo"), e); return; }
  const r = await api("GET", "/api/v1/dte/documentos?" + new URLSearchParams({ limite: 100, rut_emisor: e.rut }));
  $("doc_cuerpo").innerHTML = `
    <div class="card">${tabla(r.documentos)}</div>
    <div id="cor_aviso"></div>
    <p class="mut">Un documento emitido no se borra. Si salió mal, pulsa <b>anular</b> en su fila: una nota de crédito por el total lo deja en cero. Si cobraste de menos, <b>cobrar más</b> emite una nota de débito que suma la diferencia. Y una nota de crédito que salió mal se deja <b>sin efecto</b> con una nota de débito.${e.demo ? "" : " Los documentos del sistema gratuito del SII se anulan con el propio SII (la app te muestra la nota antes); los del motor propio, con tus folios."}</p>`;
};

// Elegir si la empresa se usa para practicar con el SII o para trabajar de verdad.
// Explicado como a un alumno: dos tarjetas, un título y una frase cada una.
function opcionesAmbiente() {
  return `
        <label>¿Para qué vas a usar esta empresa?</label>
        <div class="radio tarjetas">
          <label><input type="radio" name="e_amb" value="certificacion" checked><div><span><b>Para practicar con el SII</b> <span class="semaf amarillo">amarillo</span></span><small>Los documentos sí llegan al SII y él los revisa, pero no valen: son de ensayo. Nadie paga impuestos por ellos.</small></div></label>
          <label><input type="radio" name="e_amb" value="produccion"><div><span><b>Para trabajar de verdad</b> <span class="semaf rojo">rojo</span></span><small>Cada documento vale como una boleta o factura real. Queda registrado en el SII y se pagan impuestos por él.</small></div></label>
        </div>
        <p class="mut">¿No estás seguro? Elige practicar. Cambiar a "de verdad" se hace más adelante, cuando la empresa ya aprobó sus pruebas con el SII.</p>`;
}

function tabla(docs) {
  if (!docs.length) return `<div class="vacio">${icono("vacio")}No hay documentos todavía.<br><span class="lnk" onclick="ir('cobrar')">Emitir el primero</span></div>`;
  const est = (e) => { e = e || ""; const c = e.includes("acept") || e.includes("emitido") || e === "simulado" ? "p-ok" : e.includes("rechaz") || e.includes("anulado") ? "p-no" : e.includes("envi") ? "p-warn" : "p-mut";
    const t = e === "simulado" ? "práctica" : e === "anulado_simulado" ? "anulada" : e.replace("_mipyme", " · gratuito SII"); return `<span class="pill ${c}">${esc(t)}</span>`; };
  return `<div class="tablewrap"><table><tr><th>Fecha</th><th>Documento</th><th>N°</th><th>Para</th><th class="num">Total</th><th>Estado</th><th></th></tr>
    ${docs.map((d) => `<tr><td>${esc(fechaCorta(d.fecha_emision))}</td><td>${esc(TIPOS[d.tipo_dte] || d.tipo_dte)}</td><td>${d.folio}</td>
      <td>${esc(d.razon_social_receptor || d.rut_receptor || "")}</td><td class="num">${clp(d.monto_total)}</td><td>${est(d.estado)}</td>
      <td>${d.tiene_pdf ? `<span class="lnk" onclick="ir('ver?id=${d.id}')">ver</span> <span class="lnk" onclick="bajar(${d.id},'pdf',${d.tipo_dte},${d.folio})">PDF</span>` : ""} ${d.tiene_xml ? `<span class="lnk" onclick="bajar(${d.id},'xml',${d.tipo_dte},${d.folio})">XML</span>` : ""}
        ${![56, 61].includes(d.tipo_dte) && d.estado !== "anulado_simulado" && d.canal !== "mipyme" ? `<span class="lnk" onclick="${d.ambiente === "simulacion" ? `cobroDemo(${d.id}, ${d.tipo_dte}, ${d.folio})` : `cobroPropio(${d.id})`}">cobrar más</span> ` : ""}
        ${![56, 61].includes(d.tipo_dte) && d.estado !== "anulado_simulado" ? `<span class="lnk no" data-tour="anular" onclick="${d.ambiente === "simulacion" ? `anularDemo(${d.id})` : d.canal === "mipyme" ? `anularSII(${d.id})` : `anularPropio(${d.id})`}">anular</span>` : ""}
        ${d.tipo_dte === 61 && d.estado !== "anulado_simulado" ? `<span class="lnk no" onclick="${d.ambiente === "simulacion" ? `anularNCDemo(${d.id}, ${d.folio})` : d.canal === "mipyme" ? `anularNCSII(${d.id})` : `anularNCPropio(${d.id})`}">dejar sin efecto</span>` : ""}</td></tr>`).join("")}
    </table></div>`;
}
window.bajar = async (id, que, tipo, folio) => {
  try { const p = await window.go.main.App.Guardar(`/api/v1/dte/documentos/${id}/${que}`, `T${tipo}_F${folio}.${que}`); if (p) aviso("Guardado en " + p, 5000); }
  catch (e) { aviso("No se pudo guardar: " + e, 6000); }
};

// ---------- valores (UF, UTM, dólar): del día, a una fecha, y el histórico mensual ----------
const num = (x, dec) => (x == null ? "—" : Number(x).toLocaleString("es-CL", { minimumFractionDigits: dec, maximumFractionDigits: dec }));
vistas.valores = async (el) => {
  const hoy = new Date().toISOString().slice(0, 10);
  el.innerHTML = `
    <h1>Valores</h1>
    <p class="sub">UF, UTM y dólar de las páginas oficiales del SII. El histórico se cargó una vez; el año en curso se refresca cada día.</p>
    <div class="card"><div class="row">
      <div><label>¿Cuánto valía el…?</label><input id="v_fecha" type="date" value="${hoy}" max="${hoy}" onchange="valoresFecha()"></div></div>
      <div class="totales" id="v_tres"></div>
      <p class="mut" id="v_nota"></p></div>
    <div class="card"><div class="row">
      <div><label>Histórico mensual</label><select id="v_anio" onchange="valoresAnio()"></select></div></div>
      <div id="v_tabla"></div></div>`;
  window.valoresFecha = async () => {
    try {
      const v = await api("GET", "/api/v1/valores?fecha=" + $("v_fecha").value);
      const c = (x, dec) => `<div><span>${x ? "" : ""}</span><b>${x ? num(x.valor, dec) : "—"}</b><span>${x && x.rezago_dias > 0 ? "del " + x.fecha_valor : ""}</span></div>`;
      $("v_tres").innerHTML = `<div><span>UF</span><b>${v.uf ? num(v.uf.valor, 2) : "—"}</b><span>${v.uf && v.uf.rezago_dias > 0 ? "del " + fechaLarga(v.uf.fecha_valor) : ""}</span></div>
        <div><span>UTM</span><b>${v.utm ? num(v.utm.valor, 0) : "—"}</b><span>${v.utm ? mesLargo(v.utm.fecha_valor) : ""}</span></div>
        <div><span>Dólar</span><b>${v.dolar ? num(v.dolar.valor, 2) : "—"}</b><span>${v.dolar && v.dolar.rezago_dias > 0 ? "del " + fechaLarga(v.dolar.fecha_valor) : ""}</span></div>`;
      $("v_nota").textContent = (v.dolar && v.dolar.rezago_dias > 3) ? "El dólar de la página oficial del SII llega con rezago: se muestra el último publicado y su fecha." : "";
    } catch (e) { $("v_tres").innerHTML = `<div><span>${esc(e.message)}</span></div>`; }
  };
  window.valoresAnio = async () => {
    const a = $("v_anio").value || hoy.slice(0, 4);
    const h = await api("GET", "/api/v1/valores/historico?anio=" + a);
    if (!$("v_anio").options.length) {
      $("v_anio").innerHTML = h.anios_disponibles.slice().reverse().map((x) => `<option value="${x}" ${x === a ? "selected" : ""}>${x}</option>`).join("");
    }
    $("v_tabla").innerHTML = `<div class="tablewrap"><table><tr><th>Mes</th><th class="num">UF día 1</th><th class="num">UF fin de mes</th><th class="num">UTM</th><th class="num">Dólar día 1</th><th class="num">Dólar fin de mes</th></tr>
      ${h.meses.map((m) => `<tr><td>${mesLargo(m.mes)}</td><td class="num">${num(m.uf, 2)}</td><td class="num">${num(m.uf_fin, 2)}</td><td class="num">${num(m.utm, 0)}</td><td class="num">${num(m.dolar, 2)}</td><td class="num">${num(m.dolar_fin, 2)}</td></tr>`).join("")}</table></div>`;
  };
  valoresFecha(); valoresAnio();
};

// ---------- aprender ----------
vistas.aprender = async (el) => {
  const T = window.Tour, et = T ? T.etapas : [], st = T ? T.estado() : { vistos: {}, etapa: 0, checks: {} };
  const hecha = (e) => e.pasos.every((_, i) => st.vistos[`${e.id}:${i}`]);
  const prog = (e) => e.pasos.filter((_, i) => st.vistos[`${e.id}:${i}`]).length;
  const LISTAS = {
    crear: ["Decidí si emito como persona natural (mi RUT) o creo una empresa (SpA, EIRL, Ltda.).",
            "Si es empresa: la constituí en Empresa en un Día (registroempresasysociedades.cl) y tengo el RUT y la escritura.",
            "Sé quién es el representante legal: es quien firma por la empresa ante el SII."],
    iniciar: ["Hice el inicio de actividades en sii.cl y elegí el giro (actividad económica).",
              "Tengo clave tributaria de la persona y, si corresponde, de la empresa.",
              "Compré un certificado digital (.p12) a nombre de una persona autorizada por la empresa.",
              "Cargué el certificado en el panel del motor: la empresa aparece en el selector de arriba.",
              "Decidí por cuál vía emito: con lo que el SII da (etapa 4) o con motor propio (etapa 5)."],
    motor: ["Entiendo qué es un CAF (los folios que el SII me entrega) y que mi certificado firma cada documento.",
            "Probé emitir en el ambiente de pruebas del SII (semáforo amarillo).",
            "Pasé la certificación del SII (set de pruebas + declaración de cumplimiento) para los tipos que voy a emitir."],
  };
  const lista = (e) => LISTAS[e.id] ? `<ul class="lista-check">${LISTAS[e.id].map((t, i) => { const id = e.id + ":" + i; return `<li><input type="checkbox" ${st.checks?.[id] ? "checked" : ""} onchange="Tour.check('${id}', this.checked)"><span>${t}</span></li>`; }).join("")}</ul>` : "";
  el.innerHTML = `
    <h1>Aprender</h1>
    <p class="sub">Para quien no sabe nada de finanzas ni de impuestos. Primero la <b>academia</b>: las ideas, con ejemplos y una pregunta por lección. Después el <b>camino</b>: practicar cada cosa dentro de la app.</p>
    ${academiaHTML()}
    <h2 style="margin-top:28px">El camino: practicar en la app</h2>
    <p class="mut">Cinco etapas en el orden en que pasan en la vida real; cada una se recorre dentro de la app, paso a paso.</p>
    ${et.map((e, i) => `<div class="card" data-tour="etapa-${e.n}" id="etapa-${e.n}" style="${i === st.etapa ? "border-color:var(--acc)" : ""}">
      <div style="display:flex;align-items:center;gap:12px">
        <span class="pill ${hecha(e) ? "p-ok" : i === st.etapa ? "p-warn" : "p-mut"}">${hecha(e) ? "completa" : i === st.etapa ? "en curso" : "pendiente"}</span>
        <span class="pill ${e.mundo === "real" ? "p-no" : e.mundo === "pruebas" ? "p-warn" : "p-ok"}">${e.mundo === "real" ? "real" : e.mundo === "pruebas" ? "pruebas del SII" : "práctica"}</span>
        <span class="mut" style="margin-left:auto">${prog(e)}/${e.pasos.length} pasos</span></div>
      <h3 style="margin:10px 0 4px">${e.n}. ${e.titulo}</h3>
      <p class="mut" style="margin:0 0 6px">${e.resumen}</p>
      ${lista(e)}
      <button class="${hecha(e) ? "sec" : ""}" onclick="Tour.etapa(${i})">${hecha(e) ? "Repetir el recorrido" : prog(e) ? "Seguir el recorrido" : "Empezar el recorrido"}</button>
    </div>`).join("")}
    <p class="mut">Las lecciones también están en el repo (<code>docs/aprender/LECCIONES.md</code>), junto con el quiz y los videos del tramo 1.</p>`;
  const n = param("etapa");
  if (n) document.getElementById("etapa-" + n)?.scrollIntoView({ block: "start", behavior: "smooth" });
  if (param("camino")) document.querySelector("[data-tour='etapa-1']")?.scrollIntoView({ block: "start", behavior: "smooth" });
};

// ---------- mini academia ----------
// Las lecciones viven en academia.js (window.ACADEMIA): módulos y lecciones con concepto,
// ejemplo con números, acción en la app y una pregunta. El progreso (leídas y respondidas
// bien) queda en localStorage; es por computador, como el del camino.
const ACAD_CLAVE = "academia.v1";
function acadEstado() { try { return { leidas: {}, correctas: {}, intentos: {}, ...JSON.parse(localStorage.getItem(ACAD_CLAVE) || "{}") }; } catch { return { leidas: {}, correctas: {}, intentos: {} }; } }
function acadGuardar(st) { try { localStorage.setItem(ACAD_CLAVE, JSON.stringify(st)); } catch {} }
const acadLecciones = () => (window.ACADEMIA?.lecciones || []);
const acadModulos = () => (window.ACADEMIA?.modulos || []);
function academiaHTML() {
  const L = acadLecciones(), M = acadModulos(), st = acadEstado();
  if (!L.length) return "";
  const leidas = L.filter((l) => st.leidas[l.id]).length, bien = L.filter((l) => st.correctas[l.id]).length;
  const pct = Math.round((bien / L.length) * 100);
  const siguiente = L.find((l) => !st.correctas[l.id]) || L[0];
  const completa = bien === L.length;
  return `
    <div class="card acad-cab">
      <div style="display:flex;gap:14px;align-items:center;flex-wrap:wrap">
        <div style="flex:1;min-width:220px"><h2 style="margin:0 0 4px">Mini academia</h2>
          <p class="mut" style="margin:0">${L.length} lecciones cortas en ${M.length} módulos, paso a paso: primero adivinas, después lo ves y lo mueves, al final una pregunta. Puedes saltarte lo que quieras y volver cuando quieras: la app acompaña, no obliga.</p></div>
        <div style="min-width:200px"><div class="barra"><div style="width:${pct}%"></div></div>
          <p class="mut" style="margin:6px 0 0;font-size:.85rem">${bien} de ${L.length} preguntas bien · ${leidas} leídas</p></div>
      </div>
      ${completa ? `<div class="banner verde" style="margin-top:12px"><b>Terminaste la academia.</b> Respondiste bien las ${L.length} preguntas. Ahora toca practicar: el camino, más abajo.</div>`
        : `<div class="row" style="margin-top:12px"><button onclick="ir('leccion?id=${siguiente.id}')">${bien ? "Seguir" : "Empezar"}: ${esc(siguiente.titulo)}</button></div>`}
    </div>
    <div class="grid acad-mods">${M.map((m, i) => { const ls = L.filter((l) => l.modulo === m.id); const ok = ls.filter((l) => st.correctas[l.id]).length; return `
      <div class="card"><div style="display:flex;align-items:center;gap:10px"><span class="acad-num">${i + 1}</span><h3 style="margin:0">${esc(m.titulo)}</h3><span class="pill ${ok === ls.length ? "p-ok" : ok ? "p-warn" : "p-mut"}" style="margin-left:auto">${ok}/${ls.length}</span></div>
        <p class="mut" style="margin:8px 0 10px">${esc(m.resumen)}</p>
        <ul class="acad-lista">${ls.map((l) => `<li onclick="ir('leccion?id=${l.id}')"><span class="acad-ok ${st.correctas[l.id] ? "si" : st.leidas[l.id] ? "leida" : ""}">${st.correctas[l.id] ? "✓" : ""}</span><span>${esc(l.titulo)}</span></li>`).join("")}</ul></div>`; }).join("")}</div>`;
}
// La lección se recorre de a una tarjeta (paso): adivina → ideas con el visual → ejemplo → pregunta → misión → cierre.
// El visual se monta una sola vez y queda arriba; cada paso decide si se ve, en qué parte se enfoca (foco) y
// recibe los números vivos del visual (data-v) para que el texto hable de lo que el alumno está moviendo.
let acadPaso = { id: null, n: 0, max: 0, adivinado: null, valores: null };
const acadFoco = (k) => {
  // si el foco es una opción de un selector del visual que no está elegida, se elige sola (el visual muestra lo que el texto cuenta)
  const op = k && document.querySelector(`#acad_vis .vis-tog button[data-parte="${k}"]:not(.on)`); if (op) op.click();
  document.querySelectorAll("#acad_vis [data-parte]").forEach((e) => { e.classList.toggle("foco", !!k && e.dataset.parte === k); e.classList.toggle("apagado", !!k && e.dataset.parte !== k); });
};
const acadVivos = (v) => { if (!v) return; document.querySelectorAll("#acad_paso [data-vivo]").forEach((e) => { if (v[e.dataset.vivo] != null) e.textContent = clp(v[e.dataset.vivo]); }); };
vistas.leccion = async (el) => {
  const L = acadLecciones(), id = param("id");
  const i = Math.max(0, L.findIndex((l) => l.id === id)), l = L[i];
  if (!l) { ir("aprender"); return; }
  const m = acadModulos().find((x) => x.id === l.modulo) || {};
  if (acadPaso.id !== l.id) acadPaso = { id: l.id, n: 0, max: 0, adivinado: null, valores: null };
  el.innerHTML = `
    <p class="mut" style="margin:0 0 4px"><span class="lnk" onclick="ir('aprender')">Aprender</span> · ${esc(m.titulo || "")} · lección ${i + 1} de ${L.length}</p>
    <h1>${esc(l.titulo)}</h1>
    <p class="sub">${esc(l.resumen)}</p>
    <div class="acad-dots" id="acad_dots"></div>
    <div class="acad-cuerpo"><div class="card acad-vis oculto" id="acad_visc"><div class="vis" id="acad_vis"></div></div>
    <div class="card acad-paso" id="acad_paso"></div></div>
    <div class="row acad-nav" id="acad_nav"></div>`;
  const vc = $("acad_vis");
  const ctx = { valores: () => api("GET", "/api/v1/valores"), cambio: (v) => { acadPaso.valores = v; acadVivos(v); acadFoco((l.pasos[acadPaso.n] || {}).foco); } };
  if (window.AcademiaVis && vc) window.AcademiaVis.montar(l.id, vc, ctx); else $("acad_visc")?.remove();
  acadPintar();
};
// pinta el paso actual de la lección abierta
function acadPintar() {
  const L = acadLecciones(), i = L.findIndex((l) => l.id === acadPaso.id), l = L[i];
  const caja = $("acad_paso"); if (!l || !caja) return;
  const P = l.pasos || [], n = Math.min(acadPaso.n, P.length - 1), p = P[n], st = acadEstado();
  acadPaso.n = n; acadPaso.max = Math.max(acadPaso.max, n);
  const ant = L[i - 1], sig = L[i + 1];
  // puntos de progreso (se puede volver a cualquier paso ya visto)
  $("acad_dots").innerHTML = P.map((x, k) => `<i class="${k === n ? "on" : k <= acadPaso.max ? "visto" : ""}" title="${x.tipo}" ${k <= acadPaso.max ? `onclick="acadSaltar(${k})"` : ""}></i>`).join("") + `<span class="mut">${n + 1} de ${P.length}</span>`;
  // el visual: se ve o no según el paso; en "adivina" se revela al responder
  const visc = $("acad_visc");
  const verVis = p.tipo === "idea" || p.tipo === "pregunta" || (p.tipo === "adivina" && acadPaso.adivinado !== null);
  if (visc) { const estaba = !visc.classList.contains("oculto"); visc.classList.toggle("oculto", !verVis); visc.classList.toggle("revelar", verVis && !estaba); visc.classList.toggle("atenuado", p.tipo === "idea" && p.vis === false); visc.classList.toggle("abajo", p.tipo === "adivina"); }
  acadFoco(p.tipo === "idea" ? p.foco : null);
  let puedeSeguir = true, h = "";
  if (p.tipo === "adivina") {
    const r = acadPaso.adivinado; puedeSeguir = r !== null;
    h = `<p class="acad-tag">Antes de leer, adivina</p><p class="acad-preg">${esc(p.t)}</p>
      <div class="acad-ops">${p.o.map((o, k) => `<button class="op ${r === null ? "" : k === p.c ? "bien" : k === r ? "mal" : ""}" ${r === null ? "" : "disabled"} onclick="acadAdivinar(${k})">${esc(o)}</button>`).join("")}</div>
      <div id="acad_res">${r === null ? `<p class="mut" style="margin:0"><span class="lnk" onclick="acadAdivinar(-1)">Prefiero verlo primero</span></p>` : `<div class="banner ${r === p.c ? "verde" : ""}"><b>${r === p.c ? "Bien pensado." : r === -1 ? "Mira:" : "Muchos piensan eso."}</b> ${esc(p.x)}</div>`}</div>`;
  } else if (p.tipo === "idea") {
    h = `${p.titulo ? `<h3>${esc(p.titulo)}</h3>` : ""}${p.h}`;
  } else if (p.tipo === "pregunta") {
    const hecha = st.correctas[l.id] ? p.c : null;
    h = `<p class="acad-tag">Una pregunta</p><p class="acad-preg">${esc(p.t)}</p>
      <div class="acad-ops">${p.o.map((o, k) => `<button class="op ${hecha === null ? "" : k === p.c ? "bien" : ""}" ${hecha === null ? "" : "disabled"} onclick="acadResponder('${l.id}', ${k})">${esc(o)}</button>`).join("")}</div>
      <div id="acad_res">${hecha === null ? "" : `<div class="banner verde"><b>Correcto.</b> ${esc(p.x)}</div>`}</div>`;
  } else if (p.tipo === "mision") {
    h = `<p class="acad-tag">Si quieres, pruébalo en la app</p><p class="acad-preg">${esc(p.t)}.</p>
      <div id="acad_mision"></div>
      <div class="row"><button class="sec" onclick="ir('${p.v}')">Ir ahora</button><span class="mut" style="font-size:.85rem">No es obligatorio: la lección te espera aquí y vuelves con el botón Aprender.</span></div>`;
    if (p.check) acadMisionHecha(p.check).then((d) => { const c = $("acad_mision"); if (c && d) c.innerHTML = `<div class="banner verde"><b>Ya lo hiciste.</b> Hay ${d}. Si quieres, hazlo de nuevo con otros números.</div>`; });
  } else if (p.tipo === "cierre") {
    if (!st.leidas[l.id]) { st.leidas[l.id] = true; acadGuardar(st); }
    const delMod = L.filter((x) => x.modulo === l.modulo), modListo = delMod.every((x) => st.leidas[x.id]) && (!sig || sig.modulo !== l.modulo);
    const mod = acadModulos().find((x) => x.id === l.modulo) || {};
    h = `<p class="acad-tag">Lo que te llevas</p><ul class="acad-claves">${(p.claves || []).map((c) => `<li>${esc(c)}</li>`).join("")}</ul>
      ${modListo ? `<div class="banner verde"><b>Terminaste «${esc(mod.titulo || "")}».</b> ${sig ? `Lo que sigue es «${esc(acadModulos().find((x) => x.id === sig.modulo)?.titulo || "")}», cuando quieras.` : "Era el último módulo. Ahora toca practicar: el camino, en Aprender."}</div>`
        : sig ? `<p class="mut">La siguiente es «${esc(sig.titulo)}». Sigue cuando quieras; también puedes volver a Aprender.</p>` : ""}`;
  }
  caja.innerHTML = h;
  acadVivos(acadPaso.valores);
  const ultimo = n === P.length - 1;
  $("acad_nav").innerHTML = `
    ${n > 0 ? `<button class="sec" onclick="acadSaltar(${n - 1})">← Atrás</button>` : `<button class="sec" onclick="ir('aprender')">← Aprender</button>`}
    ${ultimo ? (sig ? `<button onclick="ir('leccion?id=${sig.id}')">${esc(sig.titulo)} →</button>` : `<button onclick="ir('aprender')">Volver a Aprender</button>`)
             : `<button onclick="acadSaltar(${n + 1})" ${puedeSeguir ? "" : "disabled"}>Seguir →</button>`}
    ${!puedeSeguir ? `<span class="mut" style="font-size:.85rem">Elige una para seguir.</span>` : ""}`;
}
window.acadSaltar = (k) => { acadPaso.n = k; acadPintar(); if (window.scrollY > 0) window.scrollTo(0, 0); };
window.acadAdivinar = (k) => { acadPaso.adivinado = k; acadPintar(); };
window.acadResponder = (id, k) => {
  const l = acadLecciones().find((x) => x.id === id); if (!l) return;
  const p = (l.pasos || []).find((x) => x.tipo === "pregunta") || l.pregunta;
  const st = acadEstado(); st.intentos[id] = (st.intentos[id] || 0) + 1;
  const bien = k === p.c;
  if (bien) st.correctas[id] = true;
  acadGuardar(st);
  const ops = document.querySelectorAll("#acad_paso .op");
  ops.forEach((b, j) => { b.classList.toggle("bien", bien && j === p.c); b.classList.toggle("mal", !bien && j === k); if (bien) b.disabled = true; });
  const pista = p.xo?.[k];
  $("acad_res").innerHTML = bien
    ? `<div class="banner verde"><b>Correcto.</b> ${esc(p.x)}</div>`
    : `<div class="banner rojo"><b>No es esa.</b> ${pista ? esc(pista) + " " : ""}Prueba otra vez; no hay límite de intentos.</div>`;
};
// ¿la misión ya está hecha? Se mira en los documentos de la app (solo lectura); si no se puede, no pasa nada.
async function acadMisionHecha(check) {
  try {
    const docs = (await api("GET", "/api/v1/dte/documentos?limite=200")).documentos || [];
    const d = docs.find((x) => (check.tipo ? Number(x.tipo_dte) === check.tipo : true) && (check.total ? Number(x.monto_total) === check.total : true));
    return d ? `${(TIPOS[d.tipo_dte] || "un documento").toLowerCase()} N° ${d.folio} por ${clp(d.monto_total)}` : null;
  } catch { return null; }
}
// flechas del teclado para avanzar o volver dentro de una lección
document.addEventListener("keydown", (e) => {
  if (!location.hash.startsWith("#leccion") || !$("acad_nav") || /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName || "")) return;
  if (e.key === "ArrowRight") { const b = $("acad_nav").querySelector("button:not(.sec)"); if (b && !b.disabled && b.textContent.startsWith("Seguir")) b.click(); }
  if (e.key === "ArrowLeft" && acadPaso.n > 0) window.acadSaltar(acadPaso.n - 1);
});

// ---------- Mi empresa: certificado → empresa → folios, explicado como a un alumno ----------
const TIPOS_FOLIO = { 33: "Factura", 34: "Factura exenta", 39: "Boleta", 41: "Boleta exenta", 56: "Nota de débito", 61: "Nota de crédito" };
const errorDe = (r) => { try { const d = JSON.parse(r.cuerpo); return d.error?.mensaje || d.detail?.[0]?.msg || d.detail || r.cuerpo; } catch { return r.cuerpo || `Error ${r.status}`; } };
// Los errores del SII en palabras de persona: lo que la app sabe interpretar, lo explica.
const explicarSII = (m) => {
  m = String(m || "");
  if (/UnknownCA|unknown ca|certificate unknown|handshake/i.test(m)) return "El SII no reconoce este certificado: no es de una empresa certificadora autorizada por el SII, o es el de práctica. Con un certificado comprado a E-Sign, Acepta, CertiNet u otra autorizada sí funciona.";
  if (/timed out|timeout|dns|connection refused|no route/i.test(m)) return "No pude llegar al SII. Revisa tu conexión a internet y vuelve a intentar.";
  if (/expired|vencid/i.test(m)) return "El certificado está vencido. Hay que renovarlo con la empresa que lo emitió.";
  return m;
};
vistas.empresa = async (el) => {
  await cargarNegocios();
  let certs = [];
  try { certs = await api("GET", "/api/v1/keystore/certificados"); } catch {}
  const cert = certs[0];
  el.innerHTML = `
    <h1>Mi empresa</h1>
    <p class="sub">Tres pasos, en orden: tu certificado digital, tu empresa y tus folios. Cada uno explica qué es antes de pedirte nada.</p>

    <div class="card" id="paso1">
      <h3>1. Tu certificado digital ${cert ? '<span class="pill p-ok">listo</span>' : '<span class="pill p-warn">falta</span>'}</h3>
      <p class="mut">Es un archivo (termina en <b>.p12</b> o <b>.pfx</b>) que se compra una vez a una empresa autorizada (E-Sign, Acepta, CertiNet…) a nombre de <b>una persona</b>, no de la empresa. Es tu firma: con él cada documento queda firmado por ti y el SII sabe que fuiste tú. Viene con una clave que te dieron al comprarlo.</p>
      ${cert ? `<div class="rev"><span>De la persona con RUT</span><b>${esc(cert.rut)}</b></div><div class="rev"><span>Vence el</span><b>${esc(fechaLarga(cert.vencimiento))}</b></div>
        <p class="mut">Se guardó cifrado en este computador. Nunca sale de aquí.</p>
        <button class="sec" onclick="quitarCertificado(${cert.id})">Quitar este certificado</button>`
      : `<p class="mut">Elige el archivo y escribe la clave que te dieron con él. La clave se usa una sola vez para abrirlo; el certificado queda guardado cifrado aquí.</p>
        <div class="row"><div><label>Archivo del certificado</label><button class="sec" style="margin:0" id="e_elegir">Elegir archivo…</button> <span class="mut" id="e_nombre"></span></div>
        <div><label>Clave del certificado</label><input id="e_clave" type="password" autocomplete="off" placeholder="La que te dieron al comprarlo"></div></div>
        <button id="e_subir" disabled>Guardar certificado</button><div id="e_aviso"></div>`}
    </div>

    <div class="card" id="paso2" style="${cert ? "" : "opacity:.55"}">
      <h3>2. Tu empresa ${negocios.length ? '<span class="pill p-ok">' + negocios.length + " agregada" + (negocios.length > 1 ? "s" : "") + '</span>' : '<span class="pill p-warn">falta</span>'}</h3>
      <p class="mut">Una empresa es quien emite: puede ser una sociedad (SpA, EIRL, Ltda.) o tú mismo como persona con inicio de actividades. El SII sabe qué empresas puede representar tu certificado; se lo preguntamos y eliges. Después, completa sus <b>datos</b>: el giro y la dirección van impresos en cada documento.</p>
      ${negocios.length ? `<ul class="lista-check">${negocios.map((n) => `<li><span style="flex:1"><b>${esc(n.razon_social || n.rut)}</b> · ${esc(n.rut)} · ${n.ambiente === "produccion" ? '<span class="pill p-no">real</span>' : '<span class="pill p-warn">pruebas del SII</span>'} ${emisorCompleto(n) ? '<span class="pill p-ok">datos completos</span>' : '<span class="pill p-warn">faltan datos</span>'}</span><span class="lnk" onclick="datosEmpresa(${n.id})">${emisorCompleto(n) ? "ver datos" : "completar datos"}</span> <span class="lnk no" onclick="quitarNegocio(${n.id})">quitar</span></li>`).join("")}</ul><div id="e_datos"></div>` : ""}
      ${cert ? `<button class="sec" id="e_buscar">Buscar mis empresas en el SII</button><div id="e_empresas"></div>` : `<p class="mut">Primero guarda tu certificado.</p>`}
    </div>

    <div class="card" id="paso3" style="${negocios.length ? "" : "opacity:.55"}">
      <h3>3. Tus folios</h3>
      <p class="mut"><b>Un folio es el número de cada documento</b>: boleta N° 1, N° 2, N° 3… No los inventa la app: <b>los entrega el SII</b>, de a rangos, en un archivo llamado CAF, y cada documento gasta uno. Por eso un folio gastado no vuelve, y por eso pedimos de a pocos: el SII bloquea a quien acumula folios sin usarlos.</p>
      <p class="mut">Solo hace falta pedir folios si vas a emitir con <b>tu propio motor</b> (etapa 5 de la guía, después de certificarte ante el SII). Si tu empresa emite por el sistema gratuito del SII, los folios los pone el SII y aquí no tienes que hacer nada.</p>
      ${negocios.length ? `<div class="row"><div><label>Empresa</label><select id="f_neg">${negocios.map((n) => `<option value="${n.id}">${esc(n.razon_social || n.rut)}</option>`).join("")}</select></div></div>
        <button class="sec" id="f_ver">Ver qué documentos puede emitir esta empresa</button><div id="f_tabla"></div><div id="f_aviso"></div>` : `<p class="mut">Primero agrega tu empresa.</p>`}
    </div>`;

  // paso 1
  let rutaCert = "";
  const elegir = $("e_elegir");
  if (elegir) {
    elegir.onclick = async () => {
      const r = await window.go.main.App.ElegirArchivo("Elige tu certificado digital", "p12,pfx");
      if (!r) return;
      rutaCert = r; $("e_nombre").textContent = r.split(/[\\/]/).pop(); $("e_subir").disabled = false;
    };
    $("e_subir").onclick = async () => {
      const clave = $("e_clave").value;
      if (!clave) return aviso("Falta la clave del certificado.");
      if (boveda.disponible && !boveda.configurada && !(await window.crearPIN(false))) return aviso("Primero crea tu PIN: es lo que protege el certificado.", 5000);
      $("e_subir").disabled = true; $("e_aviso").innerHTML = `<p class="mut">Abriendo el certificado…</p>`;
      try {
        const r = await window.go.main.App.Formulario("/api/v1/keystore/certificados", JSON.stringify({ password: clave }), rutaCert);
        if (r.status >= 400) throw new Error(errorDe(r));
        aviso("Certificado guardado. Ahora, tu empresa.", 5000); render();
      } catch (e) { $("e_aviso").innerHTML = `<div class="banner rojo">${esc(e.message || e)}<br><span class="mut">Lo más común: la clave no es la del certificado, o el archivo no es un .p12/.pfx.</span></div>`; $("e_subir").disabled = false; }
    };
  }
  // paso 2
  const buscar = $("e_buscar");
  if (buscar) buscar.onclick = async () => {
    buscar.disabled = true; $("e_empresas").innerHTML = `<p class="mut">Preguntando al SII… puede tardar unos segundos.</p>`;
    try {
      const emps = await api("GET", `/api/v1/keystore/certificados/${cert.id}/empresas`);
      if (!emps.length) { $("e_empresas").innerHTML = `<div class="banner">El SII no asocia ninguna empresa a este certificado. Si eres persona natural con inicio de actividades, escribe tu RUT abajo.</div>`; }
      $("e_empresas").innerHTML += `
        <ul class="lista-check">${emps.map((x) => `<li><label style="margin:0;display:flex;gap:10px;align-items:center;flex:1"><input type="radio" name="e_rut" value="${esc(x.rut)}"> <span><b>${esc(x.razon_social || "")}</b> · ${esc(x.rut)}</span></label></li>`).join("")}</ul>
        <label>O escribe el RUT de la empresa</label><input id="e_rut_manual" placeholder="76111111-6">
        ${opcionesAmbiente()}
        <button id="e_agregar">Agregar empresa</button>`;
      $("e_agregar").onclick = async () => {
        const sel = document.querySelector('input[name="e_rut"]:checked');
        const rut = (sel ? sel.value : $("e_rut_manual").value).trim();
        if (!rut) return aviso("Elige una empresa o escribe su RUT.");
        const amb = document.querySelector('input[name="e_amb"]:checked').value;
        if (amb === "produccion" && !(await dialogo({ titulo: "Trabajar de verdad", cuerpo: `<div class="banner rojo"><b>Esto no es práctica.</b> Cada boleta o factura que hagas con ${esc(rut)} quedará registrada en el SII y se pagarán impuestos por ella.</div>`, botones: [{ texto: "Volver", clase: "sec", valor: false }, { texto: "Sí, de verdad", clase: "peligro", valor: true }] }))) return;
        $("e_agregar").disabled = true;
        try {
          const r = await window.go.main.App.Formulario("/api/v1/keystore/negocios", JSON.stringify({ rut, cert_id: String(cert.id), ambiente: amb }), "");
          if (r.status >= 400) throw new Error(errorDe(r));
          aviso("Empresa agregada. Ya aparece arriba para elegirla.", 5000); render();
        } catch (e) { $("e_empresas").innerHTML += `<div class="banner rojo">${esc(e.message || e)}</div>`; $("e_agregar").disabled = false; }
      };
    } catch (e) { $("e_empresas").innerHTML = `<div class="banner rojo">${esc(explicarSII(e.message || e))}</div><p class="mut">Si eres persona natural con inicio de actividades o ya sabes el RUT de tu empresa, puedes escribirlo igual:</p>
        <label>RUT de la empresa</label><input id="e_rut_manual" placeholder="76111111-6">
        ${opcionesAmbiente()}
        <button id="e_agregar">Agregar empresa</button>`; buscar.disabled = false;
      $("e_agregar").onclick = async () => {
        const rut = $("e_rut_manual").value.trim(); if (!rut) return aviso("Escribe el RUT de la empresa.");
        const amb = document.querySelector('input[name="e_amb"]:checked').value;
        $("e_agregar").disabled = true;
        try { const r = await window.go.main.App.Formulario("/api/v1/keystore/negocios", JSON.stringify({ rut, cert_id: String(cert.id), ambiente: amb }), ""); if (r.status >= 400) throw new Error(errorDe(r)); aviso("Empresa agregada.", 5000); render(); }
        catch (e2) { $("e_empresas").innerHTML += `<div class="banner rojo">${esc(explicarSII(e2.message || e2))}</div>`; $("e_agregar").disabled = false; }
      };
    }
  };
  // paso 3
  const ver = $("f_ver");
  if (ver) ver.onclick = async () => {
    const id = $("f_neg").value; ver.disabled = true; $("f_tabla").innerHTML = `<p class="mut">Preguntando al SII qué documentos tiene autorizados esta empresa…</p>`;
    try {
      const t = await api("GET", `/api/v1/keystore/negocios/${id}/timbraje`);
      const negF = negocios.find((x) => String(x.id) === String(id)) || {};
      const notaMundo = negF.ambiente === "produccion"
        ? `<div class="banner rojo"><b>Esta empresa está en "de verdad".</b> Los folios que pidas aquí son números reales del SII: cada uno sirve para un documento que vale.</div>`
        : `<div class="banner"><b>Esta empresa está en "practicar con el SII" (amarillo).</b> Lo que ves es lo que el SII de pruebas le autoriza, y los folios que pidas son de ensayo: sirven para practicar y para la certificación, no para documentos de verdad. Igual pide solo los que vayas a usar: el SII de pruebas también bloquea a quien pide y no usa.</div>`;
      $("f_tabla").innerHTML = `${notaMundo}<div class="tablewrap"><table><tr><th>Documento</th><th>¿Autorizado por el SII?</th><th></th></tr>
        ${t.documentos.map((d) => `<tr><td>${esc(TIPOS_FOLIO[d.tipo] || d.nombre)}</td><td>${d.autorizado ? '<span class="pill p-ok">sí</span>' : '<span class="pill p-mut">todavía no</span>'}${d.bloqueado ? ' <span class="pill p-no">bloqueado por el SII</span>' : ""}</td>
          <td>${d.autorizado && !d.bloqueado ? `<span class="lnk" onclick="pedirFolios(${id}, ${d.tipo}, '${esc(TIPOS_FOLIO[d.tipo] || d.nombre)}')">pedir 5 folios</span>` : ""}</td></tr>`).join("")}</table></div>
        <p class="mut">"Todavía no" significa que el SII aún no te autorizó ese tipo de documento: se consigue certificándose (etapa 5 de la guía). "Bloqueado" es el freno del SII por folios pedidos y no usados; se destraba usándolos o pidiendo al SII que anule los que no vas a usar.</p>`;
    } catch (e) { $("f_tabla").innerHTML = `<div class="banner rojo">${esc(explicarSII(e.message || e))}</div>`; }
    ver.disabled = false;
  };
};
// Los datos del emisor de una empresa, explicados uno por uno. Si la empresa aún no tiene
// datos y el motor trae los suyos (EMPRESA_* del .env) para este mismo RUT, se precargan.
window.datosEmpresa = async (id) => {
  const n = negocios.find((x) => x.id === id); if (!n) return;
  let d = datosEmisor(n), heredado = false;
  if (!emisorCompleto(d)) {
    try { const m = await api("GET", "/api/v1/dte/emisor"); if (m.configurada && m.rut === n.rut) { d = { ...Object.fromEntries(Object.entries(datosEmisor(m)).filter(([, v]) => v)), ...Object.fromEntries(Object.entries(d).filter(([, v]) => v)) }; heredado = true; } } catch {}
  }
  const v = (k) => esc(d[k] == null ? "" : String(d[k]));
  $("e_datos").innerHTML = `
    <div class="card" style="margin-top:12px">
      <h3>Los datos de ${esc(n.razon_social || n.rut)}</h3>
      <p class="mut">Esto va impreso arriba de cada boleta o factura, tal como está inscrito en el SII. Se escribe una vez.</p>
      ${heredado ? `<div class="banner">Traje los datos que el motor tenía guardados para este RUT. Revísalos y guarda.</div>` : ""}
      <label>Nombre o razón social</label><input id="d_razon" value="${v("razon_social")}" placeholder="Ej.: Café Ejemplo SpA">
      <p class="mut">El nombre con que la empresa está inscrita en el SII. Si eres persona natural, tu nombre completo.</p>
      <label>Giro</label><input id="d_giro" value="${v("giro")}" placeholder="Ej.: Venta de café y pastelería">
      <p class="mut">A qué se dedica, en pocas palabras, como lo declaraste al SII.</p>
      <label>Código de actividad</label><input id="d_act" inputmode="numeric" value="${v("codigo_actividad")}" placeholder="Ej.: 561000">
      <p class="mut">Un número de 6 cifras que el SII le pone a cada rubro. Está en tu <b>Carpeta Tributaria</b> o en sii.cl → Mi SII → Datos del contribuyente. Si tienes varios, el principal.</p>
      <div class="row"><div><label>Dirección</label><input id="d_dir" value="${v("direccion")}" placeholder="Ej.: Calle Ejemplo 123"></div>
      <div><label>Comuna</label><input id="d_com" value="${v("comuna")}" placeholder="Ej.: Santiago"></div>
      <div><label>Ciudad</label><input id="d_ciu" value="${v("ciudad")}" placeholder="Ej.: Santiago"></div></div>
      <p class="mut">La dirección de la casa matriz, la que el SII tiene registrada.</p>
      <div class="row"><div><label>Correo (opcional)</label><input id="d_mail" value="${v("email")}" placeholder="Ej.: hola@ejemplo.cl"></div>
      <div><label>Teléfono (opcional)</label><input id="d_tel" value="${v("telefono")}" placeholder="Ej.: +56 9 1234 5678"></div></div>
      <p class="mut">Se imprimen en el documento para que tus clientes te ubiquen.</p>
      <div class="row" style="margin-top:12px"><button id="d_guardar">Guardar datos</button><button class="sec" onclick="$('e_datos').innerHTML=''">Cerrar</button></div><div id="d_aviso"></div>
    </div>`;
  $("e_datos").scrollIntoView({ behavior: "smooth", block: "start" });
  $("d_guardar").onclick = async () => {
    const act = $("d_act").value.trim();
    if (act && !/^\d{6}$/.test(act)) { $("d_aviso").innerHTML = `<div class="banner rojo">El código de actividad son 6 cifras, sin puntos ni letras.</div>`; return; }
    const cuerpo = { razon_social: $("d_razon").value, giro: $("d_giro").value, direccion: $("d_dir").value, comuna: $("d_com").value, ciudad: $("d_ciu").value, email: $("d_mail").value, telefono: $("d_tel").value };
    if (act) cuerpo.codigo_actividad = Number(act);
    $("d_guardar").disabled = true;
    try {
      const r = await api("PUT", `/api/v1/keystore/negocios/${id}`, cuerpo);
      aviso(emisorCompleto(r) ? "Datos guardados. Esta empresa ya puede emitir con motor propio." : "Datos guardados. Todavía faltan algunos para emitir con motor propio.", 6000);
      render();
    } catch (e) { $("d_aviso").innerHTML = `<div class="banner rojo">${esc(e.message || e)}</div>`; $("d_guardar").disabled = false; }
  };
};
window.quitarCertificado = async (id) => {
  const ok = await dialogo({ titulo: "Quitar el certificado", cuerpo: `<p class="mut">Se borra de este computador. Tus documentos ya emitidos no cambian; para volver a emitir tendrás que guardarlo otra vez.</p>`, botones: [{ texto: "Volver", clase: "sec", valor: false }, { texto: "Quitar", clase: "peligro", valor: true }] });
  if (!ok) return;
  try { await api("DELETE", `/api/v1/keystore/certificados/${id}`); aviso("Certificado quitado."); render(); } catch (e) { aviso(e.message, 6000); }
};
window.quitarNegocio = async (id) => {
  const ok = await dialogo({ titulo: "Quitar la empresa", cuerpo: `<p class="mut">Deja de aparecer en el selector. Sus documentos ya emitidos no se borran.</p>`, botones: [{ texto: "Volver", clase: "sec", valor: false }, { texto: "Quitar", clase: "peligro", valor: true }] });
  if (!ok) return;
  try { await api("DELETE", `/api/v1/keystore/negocios/${id}`); aviso("Empresa quitada."); render(); } catch (e) { aviso(e.message, 6000); }
};
window.pedirFolios = async (negocioId, tipo, nombre) => {
  const negP = negocios.find((x) => String(x.id) === String(negocioId)) || {};
  const ok = await dialogo({ titulo: `Pedir 5 folios de ${nombre}`, cuerpo: `<p class="mut">La app entra al SII con tu certificado, pide un rango de 5 números y lo guarda aquí. Desde ese momento puedes emitir 5 documentos de este tipo.</p>${negP.ambiente === "produccion" ? "" : `<p class="mut">Como esta empresa está en "practicar con el SII", los folios vienen del SII de pruebas: son de ensayo y no valen para documentos de verdad.</p>`}<div class="banner">Pide solo lo que vayas a usar: el SII frena a quien acumula folios sin emitirlos.</div>`,
    botones: [{ texto: "Volver", clase: "sec", valor: false }, { texto: "Pedir los folios", valor: true }] });
  if (!ok) return;
  if (negP.ambiente === "produccion" && !(await pinParaReal())) return;
  aviso("Pidiendo folios al SII… puede tardar medio minuto.", 8000);
  try {
    const r = JSON.parse(await window.go.main.App.PedirFolios(negocioId, tipo, 5));
    if (r.caf_id) { $("f_aviso").innerHTML = `<div class="banner verde"><b>Listo.</b> El SII entregó los folios <b>${r.folio_desde}</b> al <b>${r.folio_hasta}</b> de ${esc(nombre)}. Ya puedes emitir con ellos.</div>`; }
    else { $("f_aviso").innerHTML = `<div class="banner rojo"><b>El SII no entregó folios.</b> ${esc(r.mensaje || r.error || (r.bloqueado ? "Este tipo está bloqueado por folios sin usar." : "Sin detalle."))}</div>`; }
  } catch (e) { $("f_aviso").innerHTML = `<div class="banner rojo">${esc(explicarSII(e.message || e))}</div>`; }
};

// ---------- tu IA (clientes MCP del computador) ----------
vistas.ia = async (el) => {
  if (estado.externo) {
    // En el navegador no hay cascarón que escriba la configuración de la IA del computador.
    el.innerHTML = `
      <h1>Tu IA</h1>
      <p class="sub">Si usas un asistente de IA en tu computador, puede trabajar con tu facturación: ver tus documentos, revisar tus folios, preparar una boleta y pedirte que la apruebes.</p>
      <div class="banner"><b>Desde el navegador no puedo conectarla por ti.</b> Conectar una IA escribe en la configuración de ese programa, y eso solo lo hace la app instalada (pestaña «Tu IA») o el binario <code>nucleo-mcp</code> en la terminal.</div>
      <div class="card"><h3>En la terminal</h3>
        <pre style="white-space:pre-wrap">nucleo-mcp clientes                     # qué asistentes hay en este computador
nucleo-mcp instalar --en claude-code    # o claude-desktop, cursor, vscode, windsurf, gemini, codex, todos</pre>
        <p class="mut">Después, cierra y vuelve a abrir ese programa y pídele, por ejemplo: <i>«muéstrame mis últimos documentos»</i>. Guía completa: <code>docs/CONECTAR.md</code>.</p>
      </div>`;
    return;
  }
  let lista;
  try { lista = await window.go.main.App.Conectores(); } catch (e) { el.innerHTML = `<div class="banner rojo">${esc(e.message || e)}</div>`; return; }
  const detectados = lista.filter((c) => c.presente);
  const fila = (c) => `<li>
      <div style="flex:1"><b>${esc(c.nombre)}</b><br><span class="mut">${c.instalado ? "Conectado: ya puede trabajar con tu facturación." : c.presente ? "Está en este computador." : "No lo encontré en este computador."}</span></div>
      ${c.instalado
        ? `<button class="sec" style="margin:0" onclick="conectarIA('${c.id}', true)">Desconectar</button>`
        : `<button class="${c.presente ? "" : "sec"}" style="margin:0" onclick="conectarIA('${c.id}', false)">Conectar</button>`}
    </li>`;
  el.innerHTML = `
    <h1>Tu IA</h1>
    <p class="sub">Si usas un asistente de IA en este computador, puede trabajar con tu facturación: ver tus documentos, revisar tus folios, preparar una boleta y pedirte que la apruebes.</p>
    <div class="banner"><b>Todo queda en este computador.</b> La IA le habla a esta app directamente, sin pasar por internet y sin claves que copiar. Tú sigues decidiendo: lo que gasta folios o cuenta ante el SII te lo muestra antes.</div>
    <div class="card"><h3>Encontré en este computador</h3>
      ${detectados.length ? "" : `<p class="mut">Ningún asistente conocido todavía. Cuando instales Claude, Cursor, VS Code, Windsurf, Gemini CLI o Codex, vuelve aquí.</p>`}
      <ul class="lista-check">${detectados.map(fila).join("")}</ul>
      ${detectados.length ? `<p class="mut">Después de conectar, cierra y vuelve a abrir ese programa y pídele, por ejemplo: <i>«muéstrame mis últimos documentos»</i>.</p>` : ""}
    </div>
    <details class="card"><summary>Otros asistentes</summary>
      <ul class="lista-check">${lista.filter((c) => !c.presente).map(fila).join("")}</ul>
      <p class="mut">Claude, ChatGPT o Gemini <b>en el navegador o en el celular</b> no se conectan así: sus conexiones salen desde la nube de cada empresa, no desde tu computador. Para eso hace falta publicar la app en internet con una dirección propia; está previsto, pero no es esto.</p>
    </details>`;
};
async function conectarIA(id, quitar) {
  try {
    const archivo = await window.go.main.App.ConectarIA(id, quitar);
    aviso(quitar ? "Desconectado." : "Conectado. Reinicia ese programa para que vea la app.", 5000);
    render();
    if (!quitar) console.log("MCP escrito en", archivo);
  } catch (e) { aviso("No pude: " + (e.message || e), 7000); }
}

// ---------- arranque: la entrada (escena nítida) hasta que el motor está listo y la persona entra ----------
function entrar() {
  if (document.documentElement.classList.contains("dentro")) return;
  document.documentElement.classList.add("dentro");          // la escena se desenfoca, la app aparece
  setTimeout(() => $("entrada")?.remove(), 1200);
  render();
}
function pintarEntrada() {
  const b = $("entrar"); if (!b) return;
  if (estado.error) { b.textContent = "El motor no pudo arrancar"; b.disabled = false; b.onclick = entrar; return; }
  b.disabled = !estado.listo; b.textContent = estado.listo ? "Entrar" : "Arrancando el motor…";
  b.onclick = entrar;
  if (estado.listo) b.focus();
}
(async () => {
  estado = await window.go.main.App.Estado();
  pintarSelector(); pintarMundo(); pintarEntrada();
  window.runtime.EventsOn("motor", (e) => { estado = e; pintarMundo(); pintarEntrada(); if (document.documentElement.classList.contains("dentro")) render(); });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Enter" && !$("entrar")?.disabled && !document.documentElement.classList.contains("dentro")) entrar(); });
})();
