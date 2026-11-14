// Súper Compras: comparador de precios de supermercados (San Pedro Sula y Tegucigalpa).
// Vistas: inicio, búsqueda, ficha de producto, ofertas y Mi compra; #/admin/revision
// (panel interno de tamaños) se carga aparte sólo cuando se abre.
import {
  Catalog, RETAILER_COLORS, rowKey, RETAILER_NAMES, countText, listText, loadCart, loadCities, loadCitySlug,
  migrateCartLines, money, photoSrcset, photoUrl, presentationLabel, repairedUnitPrice, saveCart, saveCitySlug, storeLabel,
} from "./data.js?v=20261114-2";
import { productIllustration } from "./illustration.js?v=20261114-2";

// Foto real si existe; si no hay o falla al cargar, la ilustración de siempre.
// Tarjetas y miniaturas usan 320 px; el detalle deja que el navegador elija 320 u
// 800 según la pantalla (sizes = ancho mostrado).
function productVisual(product, { size = 118, decorative = true, sizes = null } = {}) {
  const url = photoUrl(product, sizes ? 800 : 320);
  if (!url) return productIllustration(product, { size, decorative });
  const image = document.createElement("img");
  image.className = "photo";
  // Sin atributo de origen cruzado: las fotos no se leen desde canvas y el bucket R2 no envía CORS;
  // con CORS la primera foto v2 caía a la ilustración (2026-10-25).
  image.loading = sizes ? "eager" : "lazy";
  image.decoding = "async";
  image.width = 800;
  image.height = 800;
  const srcset = sizes ? photoSrcset(product) : null;
  if (srcset) {
    image.srcset = srcset;
    image.sizes = sizes;
  }
  image.alt = decorative ? "" : [product.brand, product.product_name, product.presentation].filter(Boolean).join(" ");
  image.addEventListener("error", () => image.replaceWith(productIllustration(product, { size, decorative })), { once: true });
  image.src = url;
  return image;
}

const DEFAULT_CATALOG =
  "https://raw.githubusercontent.com/jchernandez-portfolio/precios-supermercados-sps/portfolio-data/precios-supermercados-sps/published/rpi/v3/manifest.json";
const PAGE_SIZE = 48;

// ------------------------------------------------------------------ DOM helpers
function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (key === "style" && typeof value === "object") Object.assign(node.style, value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

// Reemplaza el contenido de un nodo; acepta listas y omite null/false.
function fill(node, ...children) {
  node.replaceChildren(...children.flat(Infinity).filter((child) => child !== null && child !== undefined && child !== false)
    .map((child) => (child instanceof Node ? child : document.createTextNode(String(child)))));
  return node;
}

const ICONS = {
  search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zm9 16-3.5-3.5",
  home: "M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z",
  tag: "M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8zM7.5 6a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3z",
  cart: "M2 3h3l2.6 12.4a1 1 0 0 0 1 .8h9.7a1 1 0 0 0 1-.8L21 7H6M9 20a1 1 0 1 0 0 .01M18 20a1 1 0 1 0 0 .01",
  back: "M15 18l-6-6 6-6",
  share: "M4 12v7a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-7M16 6l-4-4-4 4M12 2v13",
  pin: "M12 21s-7-6.2-7-11.5A7 7 0 0 1 19 9.5C19 14.8 12 21 12 21zM12 7a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z",
  plus: "M12 5v14M5 12h14",
  scan: "M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2M7 8v8M10 8v8M13 8v8M17 8v8",
};

function icon(name, size = 22) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", ICONS[name]);
  svg.append(path);
  return svg;
}

// ------------------------------------------------------------------ state
const state = {
  catalog: null,
  renderToken: 0,
  cities: [],
  city: null,
  cart: [],
  limit: PAGE_SIZE,
  lastQueryKey: "",
  // Franjas de precios por súper: un observador por vista y la carga de cada tarjeta.
  observer: null,
  loaders: new Map(),
};

function catalogUrl() {
  const params = new URLSearchParams(location.search);
  const local = params.get("catalogo");
  // Sólo rutas relativas del mismo sitio (vista previa); nunca otro dominio.
  if (local && /^[\w./-]+manifest\.json$/.test(local) && !local.includes("..")) return new URL(local, location.href).href;
  return document.body.dataset.catalogUrl || DEFAULT_CATALOG;
}

function route() {
  const hash = location.hash.replace(/^#/, "") || "/";
  const [path, query = ""] = hash.split("?");
  return { path, params: new URLSearchParams(query) };
}

function go(path, params) {
  const query = params && [...params].length ? `?${params}` : "";
  location.hash = `#${path}${query}`;
}

function cartCount() {
  const byId = state.catalog?.ready ? state.catalog.byId : null;
  return state.cart.filter((line) => !byId || byId.has(line.id)).reduce((sum, line) => sum + line.qty, 0);
}

// store = location_id del súper elegido; sin store, Mi compra usa el más barato.
function addToCart(id, store = null, label = "") {
  const line = state.cart.find((item) => item.id === id && (item.store || null) === store);
  if (line) line.qty += 1;
  else state.cart.push(store ? { id, qty: 1, store } : { id, qty: 1 });
  saveCart(state.cart, state.city.slug);
  renderChrome();
  toast(label ? `Agregado a Mi compra · ${label}` : "Agregado a Mi compra");
}

function removeMissing() {
  const byId = state.catalog?.byId;
  if (!byId) return;
  state.cart = state.cart.filter((line) => byId.has(line.id));
  saveCart(state.cart, state.city.slug);
  renderChrome();
  render();
}

function updateLine(index, changes) {
  state.cart = state.cart.map((line, position) => (position === index ? { ...line, ...changes } : line))
    .map((line) => (line.store ? line : { id: line.id, qty: line.qty }))
    .filter((line) => line.qty > 0);
  // Une líneas repetidas del mismo producto y súper.
  const merged = [];
  for (const line of state.cart) {
    const twin = merged.find((item) => item.id === line.id && (item.store || null) === (line.store || null));
    if (twin) twin.qty += line.qty; else merged.push({ ...line });
  }
  state.cart = merged;
  saveCart(state.cart, state.city.slug);
  if (route().path.startsWith("/compra")) render(); else renderChrome();
}

let toastTimer;
function toast(text) {
  const box = document.getElementById("toast");
  box.textContent = text;
  box.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => box.classList.remove("visible"), 1800);
}

// Sin red, fetch falla con un TypeError en inglés ("Failed to fetch" en Chrome, "Load
// failed" en Safari, "NetworkError…" en Firefox): se explica en español. Los demás
// errores ya vienen redactados por data.js.
function errorText(error) {
  const network = error instanceof TypeError && /fetch|network|load failed/i.test(String(error.message));
  if (network || navigator.onLine === false) {
    return "Parece que no hay conexión a internet. Revisa tu conexión e intenta de nuevo.";
  }
  return error?.message || "Ocurrió un error inesperado.";
}

function shortDate(value) {
  return new Date(value).toLocaleDateString("es-HN", { day: "numeric", month: "short" });
}

// "Precios de hoy" sólo si todos los súper se capturaron hoy; si no, se dice cuáles
// vienen de un día anterior (Colonial y Los Andes quedaron atrás 2026-10-22 a 24).
function freshnessText() {
  const asOf = state.catalog?.asOf;
  if (!asOf) return "";
  const date = new Date(asOf);
  const when = date.toLocaleString("es-HN", { weekday: "long", hour: "numeric", minute: "2-digit" });
  const stale = state.catalog.staleRetailers();
  if (!stale.length) return `Precios de hoy · actualizados ${when}`;
  const names = stale.map((item) => `${RETAILER_NAMES[item.id] || item.id} del ${shortDate(item.since)}`);
  return `Actualizado ${when} · ${names.join(", ")}`;
}

// ------------------------------------------------------------------ shared pieces
function searchForm(value = "", { large = false } = {}) {
  const input = h("input", {
    type: "search", name: "q", value, placeholder: "Buscar producto o marca", "aria-label": "Buscar producto o marca",
    autocomplete: "off", enterkeyhint: "search",
  });
  return h("form", {
    class: `search${large ? " search-large" : ""}`, role: "search",
    onsubmit: (event) => {
      event.preventDefault();
      const params = new URLSearchParams();
      if (input.value.trim()) params.set("q", input.value.trim());
      go("/buscar", params);
    },
  }, icon("search", 18), input,
  h("button", { type: "button", class: "search-scan", "data-scan": "", "aria-label": "Escanear código de barras", title: "Escanear código de barras" }, icon("scan", 20)));
}

// Precio por unidad de una oferta: el del súper, el recalculado si la web corrigió
// la medida, o ninguno si la medida era imposible (ver hideImplausible).
function offerUnitPrice(product, offer) {
  if (!offer || product.suspect) return null;
  if (product.repaired) return repairedUnitPrice(product, offer.current_price);
  return offer.unit_price || null;
}

function sizePill(product) {
  const text = typeof product === "string" ? product : presentationLabel(product);
  return text ? h("span", { class: "pill" }, String(text).toUpperCase()) : null;
}

function cityName() {
  return state.city?.name || "San Pedro Sula";
}

function citySelect() {
  if (state.cities.length < 2) return null;
  return h("label", { class: "city-select" },
    icon("pin", 16),
    h("span", { class: "sr-only" }, "Ciudad"),
    h("select", {
      "aria-label": "Ciudad",
      onchange: (event) => switchCity(event.target.value),
    }, state.cities.map((city) => h("option", { value: city.slug, selected: city.slug === state.city?.slug }, city.name))));
}

function storesLine(product) {
  const count = product.retailers.length;
  return h("span", { class: count > 1 ? "stores compare" : "stores" },
    count > 1 ? `Compara en ${count} súper` : `En ${RETAILER_NAMES[product.retailers[0]] || "1 súper"}`);
}

function sectionHeader(title, link) {
  return h("div", { class: "section-head" }, h("h2", {}, title), link || null);
}

function productName(product) {
  return product.display_name ?? product.product_name;
}

// Etiqueta lima del precio (identidad «Lima»).
function priceTag(value, extra = "") {
  return h("span", { class: `price-tag${extra ? ` ${extra}` : ""}` }, money(value));
}

function storeDot(id) {
  return h("i", { class: "dot", style: { background: RETAILER_COLORS[id] || "#545B55" }, "aria-hidden": "true" });
}

// Rebaja publicada por el súper: precio regular contra el actual.
function discountOf(offer) {
  const regular = Number(offer?.reported_regular_price);
  const current = Number(offer?.current_price);
  if (!(regular > current) || !(current > 0)) return null;
  return { regular, pct: Math.round((1 - current / regular) * 100) };
}

function bestDiscount(offers) {
  let best = null;
  for (const offer of offers) {
    const discount = discountOf(offer);
    if (discount && (!best || discount.pct > best.pct)) best = { ...discount, offer };
  }
  return best;
}

function liveOffers(row) {
  return (row?.offers || []).filter((offer) => offer.current_price && offer.availability !== "out_of_stock")
    .sort((a, b) => Number(a.current_price) - Number(b.current_price));
}

// Precios de una fila (partición del catálogo) sin bloquear la vista: si fallan o la
// vista cambió, la tarjeta se queda como está. Catalog.row guarda cada partición una
// sola vez, así varias tarjetas de la misma partición comparten una descarga.
function withRow(product, onOffers) {
  const catalog = state.catalog;
  const token = state.renderToken;
  return catalog.row(product).then((row) => {
    if (state.catalog !== catalog || state.renderToken !== token || !row) return null;
    const offers = liveOffers(row);
    onOffers(offers, row);
    return offers;
  }).catch(() => null);
}

// Franja de precios por súper: una celda por cadena (su sucursal más barata); la más
// barata en verde sólo si hay con qué comparar.
function storeStrip(offers) {
  const bySuper = new Map();
  for (const offer of offers) if (!bySuper.has(offer.supermarket_id)) bySuper.set(offer.supermarket_id, offer);
  // Con un solo súper no hay nada que comparar: la tarjeta ya dice dónde está.
  if (bySuper.size < 2) return null;
  const minimum = Math.min(...[...bySuper.values()].map((offer) => Number(offer.current_price)));
  return h("ul", { class: "strip", "aria-label": "Precio en cada súper" },
    [...bySuper.entries()].map(([id, offer]) => {
      const cheapest = Number(offer.current_price) === minimum;
      return h("li", { class: cheapest ? "cheapest" : null },
        h("span", { class: "strip-store" }, RETAILER_NAMES[id] || id),
        h("strong", {}, money(offer.current_price)),
        cheapest ? h("span", { class: "sr-only" }, " (el más barato)") : null);
    }));
}

// Carga diferida: sólo cuando la tarjeta se ve (o, sin IntersectionObserver, ya).
function watchVisible(node, load) {
  if (!("IntersectionObserver" in window)) { load(); return; }
  if (!state.observer) {
    state.observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        state.observer.unobserve(entry.target);
        state.loaders.get(entry.target)?.();
        state.loaders.delete(entry.target);
      }
    }, { rootMargin: "160px 0px" });
  }
  state.loaders.set(node, load);
  state.observer.observe(node);
}

// Después de pintar: las franjas nunca retrasan la vista.
function afterPaint(callback) {
  const token = state.renderToken;
  const run = () => { if (state.renderToken === token) callback(); };
  if ("requestIdleCallback" in window) window.requestIdleCallback(run, { timeout: 800 });
  else setTimeout(run, 120);
}

// ------------------------------------------------------------------ filtros desplegables
// Botón píldora + lista de opciones (una sola, como super/cat/orden en la URL). En
// celular la lista sube desde abajo; en computadora flota bajo el botón.
let openMenu = null;

function closeMenu({ focus = true } = {}) {
  if (!openMenu) return;
  const { button, panel } = openMenu;
  openMenu = null;
  panel.hidden = true;
  button.setAttribute("aria-expanded", "false");
  document.body.classList.remove("sheet-open");
  if (focus && button.isConnected) button.focus();
}

function filterMenu({ id, label, value, options, defaultValue = "", onPick }) {
  const current = options.find(([optionValue]) => optionValue === value);
  const active = Boolean(current) && value !== defaultValue;
  const listId = `menu-${id}`;
  const button = h("button", {
    type: "button", class: `filter-btn${active ? " is-set" : ""}`, "aria-haspopup": "listbox", "aria-expanded": "false",
    "aria-controls": listId, "aria-label": active ? `${label}: ${current[1]}` : label,
  }, h("span", { class: "filter-text" }, active ? current[1] : label), h("span", { class: "chev", "aria-hidden": "true" }));
  const items = options.map(([optionValue, text]) => h("button", {
    type: "button", role: "option", class: "menu-option", "aria-selected": String(optionValue === value),
    tabindex: optionValue === value ? "0" : "-1",
    onclick: () => {
      closeMenu({ focus: optionValue === value });
      if (optionValue !== value) onPick(optionValue);
    },
  }, text));
  const list = h("div", {
    class: "menu-list", role: "listbox", id: listId, "aria-label": label,
    onkeydown: (event) => {
      const position = items.indexOf(document.activeElement);
      const target = { ArrowDown: position + 1, ArrowUp: position - 1, Home: 0, End: items.length - 1 }[event.key];
      if (target === undefined) return;
      event.preventDefault();
      items[Math.max(0, Math.min(items.length - 1, target))]?.focus();
    },
  }, items);
  const panel = h("div", { class: "menu", hidden: true }, h("p", { class: "menu-head", "aria-hidden": "true" }, label), list);
  const wrap = h("div", {
    class: "dd",
    onfocusout: (event) => { if (openMenu?.wrap === wrap && !wrap.contains(event.relatedTarget)) closeMenu({ focus: false }); },
  }, button, panel);
  button.addEventListener("click", () => {
    if (openMenu?.button === button) { closeMenu(); return; }
    closeMenu({ focus: false });
    panel.hidden = false;
    panel.classList.remove("menu-right");
    button.setAttribute("aria-expanded", "true");
    document.body.classList.add("sheet-open");
    openMenu = { button, panel, wrap };
    if (panel.getBoundingClientRect().right > window.innerWidth - 8) panel.classList.add("menu-right");
    (items.find((item) => item.getAttribute("aria-selected") === "true") || items[0])?.focus();
  });
  return wrap;
}

document.addEventListener("pointerdown", (event) => {
  if (openMenu && !openMenu.wrap.contains(event.target)) closeMenu({ focus: false });
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && openMenu) { event.preventDefault(); closeMenu(); }
});

// ------------------------------------------------------------------ views
// Portada: ofertas y más comparados vienen en home.json (misma selección que aquí);
// un catálogo sin portada los calcula del índice completo.
function homeSections(catalog) {
  if (catalog.home) return { deals: catalog.home.deals.slice(0, 12), compared: catalog.home.compared.slice(0, 12) };
  const deals = catalog.products
    .filter((product) => product.promo && product.best_price)
    .sort((a, b) => b.retailers.length - a.retailers.length || b.other_count - a.other_count)
    .slice(0, 12);
  const shown = new Set(deals.map((product) => product.id));
  const compared = catalog.products
    .filter((product) => product.retailers.length >= 3 && !shown.has(product.id))
    .sort((a, b) => b.retailers.length - a.retailers.length || a.product_name.length - b.product_name.length)
    .slice(0, 12);
  return { deals, compared };
}

function loadingPage(text) {
  return h("div", { class: "page loading", "aria-busy": "true" }, h("div", { class: "spinner", "aria-hidden": "true" }), h("p", {}, text));
}

// Vistas que necesitan el índice completo: lo piden y se vuelven a pintar al llegar.
function whenIndexed(catalog, text = "Cargando el catálogo…") {
  const token = state.renderToken;
  catalog.loadIndex().then(() => { if (state.renderToken === token && state.catalog === catalog) render(); })
    .catch((error) => {
      if (state.renderToken !== token) return;
      fill(document.getElementById("main"), h("div", { class: "page empty" }, h("strong", {}, "No pudimos cargar el catálogo."), h("p", {}, errorText(error)),
        h("button", { type: "button", class: "primary", onclick: () => render() }, "Reintentar")));
    });
  return loadingPage(text);
}

// Foto de cada departamento: el primer producto con foto entre los datos ya cargados
// (portada o índice completo); sin foto, la inicial.
function departmentPictures(catalog, departments) {
  const wanted = new Set(departments.map((department) => department.name));
  const found = new Map();
  const pool = catalog.ready ? catalog.products : [...(catalog.home?.deals || []), ...(catalog.home?.compared || [])];
  for (const product of pool) {
    if (found.size === wanted.size) break;
    if (product.category && wanted.has(product.category) && !found.has(product.category) && photoUrl(product, 320)) found.set(product.category, product);
  }
  return found;
}

function departmentCircle(department, product) {
  return h("a", { class: "circle", href: `#/buscar?cat=${encodeURIComponent(department.name)}`, "data-department": department.name },
    h("span", { class: "circle-disc" }, product ? productVisual(product, { size: 44 }) : h("span", { class: "circle-initial", "aria-hidden": "true" }, department.name.slice(0, 1))),
    h("span", { class: "circle-name" }, department.name));
}

// Tarjeta ancha de rebaja: foto, nombre, precio lima y, cuando llegan los precios de la
// partición, el precio anterior, el % y la franja por súper.
function dealCard(product) {
  const was = h("span", { class: "deal-was" });
  const slot = h("span", { class: "strip-slot" });
  const node = h("a", { class: "deal", href: `#/p/${product.id}` },
    h("span", { class: "thumb" }, productVisual(product, { size: 76 })),
    h("span", { class: "deal-body" },
      h("strong", { class: "deal-name" }, productName(product)),
      h("span", { class: "muted small" }, [presentationLabel(product), product.retailers.length > 1 ? `${product.retailers.length} súper` : null].filter(Boolean).join(" · ")),
      h("span", { class: "deal-price" }, priceTag(product.best_price), was)),
    slot);
  const load = () => withRow(product, (offers) => {
    const discount = bestDiscount(offers);
    if (discount) {
      fill(was, discount.offer === offers[0]
        ? [h("del", {}, money(discount.regular)), h("span", { class: "pct" }, `−${discount.pct}%`)]
        : h("span", { class: "pct" }, `−${discount.pct}% en ${storeLabel(discount.offer, offers)}`));
    }
    const strip = storeStrip(offers);
    if (strip) slot.replaceWith(strip);
  });
  return { node, load };
}

// "Los más comparados": la nota de ahorro sólo usa particiones que ya se bajaron.
function comparedRow(product) {
  const note = storesLine(product);
  const node = h("a", { class: "compared", href: `#/p/${product.id}` },
    h("span", { class: "thumb thumb-sm" }, productVisual(product, { size: 52 })),
    h("span", { class: "compared-body" },
      h("strong", {}, productName(product)),
      presentationLabel(product) ? h("span", { class: "muted small" }, presentationLabel(product)) : null,
      note),
    priceTag(product.best_price));
  const loadIfCached = () => {
    if (!state.catalog.partitionCache?.has(product.partition)) return;
    withRow(product, (offers) => {
      if (!offers.length) return;
      const top = Number(offers[offers.length - 1].current_price);
      const saving = top - Number(offers[0].current_price);
      const stores = new Set(offers.map((offer) => offer.supermarket_id)).size;
      fill(note, [storeLabel(offers[0], offers), `${stores} súper`, saving > 0.005 ? `ahorras ${money(saving)}` : null].filter(Boolean).join(" · "));
      note.className = "stores compare";
    });
  };
  return { node, loadIfCached };
}

function viewHome() {
  const catalog = state.catalog;
  const { deals, compared } = homeSections(catalog);
  const departments = catalog.departments();
  const pictures = departmentPictures(catalog, departments);
  const dealCards = deals.map(dealCard);
  const comparedRows = compared.map(comparedRow);
  afterPaint(() => {
    Promise.all(dealCards.slice(0, 6).map((card) => card.load()))
      .then(() => { for (const row of comparedRows) row.loadIfCached(); });
  });
  // Al llegar el índice completo (se baja de todos modos en segundo plano), los
  // departamentos que quedaron con la inicial reciben la foto de un producto suyo.
  if (!catalog.ready) {
    const token = state.renderToken;
    catalog.loadIndex().then(() => {
      if (state.renderToken !== token || state.catalog !== catalog) return;
      const found = departmentPictures(catalog, departments);
      for (const circle of document.querySelectorAll("[data-department]")) {
        const product = found.get(circle.dataset.department);
        const disc = circle.querySelector(".circle-disc");
        if (product && disc?.querySelector(".circle-initial")) fill(disc, productVisual(product, { size: 44 }));
      }
    }).catch(() => {});
  }
  return h("div", { class: "page home" },
    h("section", { class: "home-intro" },
      h("h1", {}, `Compara precios en ${cityName()}`),
      h("p", { class: "fresh" }, freshnessText()),
      h("p", { class: "muted small" }, `${countText(catalog.productCount, "producto")} en ${listText(catalog.retailers.map((id) => RETAILER_NAMES[id] || id))}.`)),
    departments.length ? h("section", { class: "circles-section" },
      h("h2", { class: "sr-only" }, "Departamentos"),
      h("div", { class: "circles" }, departments.map((department) => departmentCircle(department, pictures.get(department.name))))) : null,
    deals.length ? h("section", {},
      sectionHeader("En rebaja hoy", h("a", { href: "#/ofertas" }, "Ver todas")),
      h("div", { class: "deal-list" }, dealCards.map((card) => card.node))) : null,
    compared.length ? h("section", {},
      sectionHeader("Los más comparados"),
      h("div", { class: "compared-list" }, comparedRows.map((row) => row.node))) : null,
    h("section", {},
      sectionHeader("Departamentos"),
      h("div", { class: "departments" }, departments.map((department) =>
        h("a", { class: "department", href: `#/buscar?cat=${encodeURIComponent(department.name)}` },
          h("strong", {}, department.name),
          h("span", {}, countText(department.count, "producto")))))),
    h("section", { class: "stores-strip" },
      h("span", { class: "label" }, "Comparamos"),
      catalog.retailers.map((id) => h("span", { class: "chip" }, storeDot(id), RETAILER_NAMES[id])),
      h("p", { class: "muted small" }, "Precios públicos en línea de cada supermercado; pueden variar en tienda. Esta página no vende productos.")));
}

// Resultado: foto, nombre, presentación, precio por unidad, precio lima y la franja por
// súper (sólo cuando la tarjeta se ve).
function resultCard(product) {
  const slot = h("span", { class: "strip-slot" });
  const node = h("a", { class: "result", href: `#/p/${product.id}` },
    h("span", { class: "thumb" }, productVisual(product, { size: 72 })),
    h("span", { class: "result-body" },
      h("span", { class: "result-brand" }, product.brand_label ?? (product.brand || product.product_type || "")),
      h("strong", { class: "result-name" }, productName(product)),
      h("span", { class: "result-meta" }, sizePill(product),
        product.unit_amount ? h("span", { class: "muted small" }, `${money(product.unit_amount)} por ${product.unit_per}`) : null),
      storesLine(product)),
    h("span", { class: "result-price" },
      product.retailers.length > 1 ? h("small", {}, "desde") : null,
      priceTag(product.best_price),
      product.promo ? h("span", { class: "offer-pill" }, "Oferta") : null),
    slot);
  watchVisible(node, () => withRow(product, (offers) => {
    const strip = storeStrip(offers);
    if (strip) slot.replaceWith(strip);
  }));
  return node;
}

// Tarjeta chica (carrusel / mosaico / lista de ofertas): foto, nombre, precio y %.
function miniCard(product, extra = "") {
  const pct = h("span", { class: "mini-pct" });
  const node = h("a", { class: `mini${extra ? ` ${extra}` : ""}`, href: `#/p/${product.id}` },
    h("span", { class: "thumb" }, productVisual(product, { size: 72 })),
    h("span", { class: "mini-name" }, productName(product)),
    h("span", { class: "mini-price" }, priceTag(product.best_price), pct));
  return { node, pct, product };
}

function featuredCard(product, discount = null, offers = []) {
  const cheapest = offers[0];
  return h("a", { class: "featured", href: `#/p/${product.id}` },
    h("span", { class: "featured-head" },
      h("span", { class: "featured-eyebrow" }, discount ? "La mejor rebaja de hoy" : "Oferta destacada"),
      discount ? h("span", { class: "pct-pill" }, `−${discount.pct}%`) : null),
    h("span", { class: "featured-photo" }, productVisual(product, { size: 120 })),
    h("span", { class: "featured-body" },
      h("strong", { class: "featured-name" }, productName(product)),
      presentationLabel(product) ? h("span", { class: "featured-size" }, presentationLabel(product)) : null,
      h("span", { class: "featured-price" }, money(discount ? discount.offer.current_price : product.best_price)),
      discount ? h("span", { class: "featured-was" }, `antes ${money(discount.regular)} · ${storeLabel(discount.offer, offers)}`)
        : cheapest ? h("span", { class: "featured-was" }, `en ${storeLabel(cheapest, offers)}`) : null));
}

// Ofertas: destacada (la mayor rebaja entre las primeras), carrusel (celular) o mosaico
// 3×2 (computadora) y luego el resto.
function offersLayout(items) {
  const featuredSlot = h("div", { class: "featured-slot" }, featuredCard(items[0]));
  const minis = items.slice(1, 7).map((product, index) => miniCard(product, index >= 4 ? "mosaic-extra" : ""));
  const mosaic = h("div", { class: "mosaic" }, minis.map((mini) => mini.node));
  const rest = items.slice(5).map((product, index) => miniCard(product, `list-item${index < 2 ? " mobile-only" : ""}`).node);
  afterPaint(() => {
    const top = items.slice(0, 7);
    Promise.all(top.map((product) => withRow(product, () => {}))).then((results) => {
      const info = results.map((offers) => (offers ? { offers, discount: bestDiscount(offers) } : null));
      minis.forEach((mini, index) => {
        const discount = info[index + 1]?.discount;
        if (discount) fill(mini.pct, `−${discount.pct}%`);
      });
      // La mayor rebaja entre la destacada y el carrusel pasa al frente.
      let best = 0;
      for (let index = 1; index < 5; index += 1) {
        if ((info[index]?.discount?.pct || 0) > (info[best]?.discount?.pct || 0)) best = index;
      }
      if (!featuredSlot.isConnected) return;
      if (best > 0 && minis[best - 1]) {
        const swapped = miniCard(items[0], minis[best - 1].node.className.replace(/^mini\s?/, ""));
        if (info[0]?.discount) fill(swapped.pct, `−${info[0].discount.pct}%`);
        minis[best - 1].node.replaceWith(swapped.node);
      }
      const chosen = info[best];
      fill(featuredSlot, featuredCard(items[best], chosen?.discount || null, chosen?.offers || []));
    });
  });
  return [
    h("div", { class: "offers-top" }, featuredSlot, mosaic),
    rest.length ? h("div", { class: "offer-list" }, rest) : null,
  ];
}

function viewSearch(params, { offers = false } = {}) {
  const catalog = state.catalog;
  if (!catalog.ready) return whenIndexed(catalog, "Cargando el catálogo para buscar…");
  const query = params.get("q") || "";
  const category = params.get("cat") || "";
  const retailer = params.get("super") || "";
  const promoOnly = offers || params.get("oferta") === "1";
  const sort = params.get("orden") || "price";
  const key = [query, category, retailer, promoOnly, sort].join("|");
  if (key !== state.lastQueryKey) { state.limit = PAGE_SIZE; state.lastQueryKey = key; }
  const { total, items, related } = catalog.search({ query, category, retailer, promoOnly, sort, limit: state.limit });

  const update = (name, value) => {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value); else next.delete(name);
    go(offers ? "/ofertas" : "/buscar", next);
  };

  const filters = h("div", { class: "filters" },
    filterMenu({
      id: "super", label: "Supermercado", value: retailer, onPick: (value) => update("super", value),
      options: [["", "Todos los súper"], ...catalog.retailers.map((id) => [id, RETAILER_NAMES[id]])],
    }),
    filterMenu({
      id: "cat", label: "Departamento", value: category, onPick: (value) => update("cat", value),
      options: [["", "Todos los departamentos"], ...catalog.departments().map((d) => [d.name, d.name])],
    }),
    filterMenu({
      id: "orden", label: "Ordenar", value: sort, defaultValue: "price", onPick: (value) => update("orden", value),
      options: [["price", "Precio más bajo"], ["unit", "Más barato por kg/L"], ["relevance", "Más relevantes"], ["stores", "Más súper para comparar"]],
    }),
    offers ? null : h("button", {
      type: "button", class: `toggle${promoOnly ? " on" : ""}`, "aria-pressed": String(promoOnly),
      onclick: () => update("oferta", promoOnly ? "" : "1"),
    }, h("span", { class: "switch", "aria-hidden": "true" }), "En oferta"));

  const title = offers ? "Ofertas de hoy" : query ? `Resultados para “${query}”` : category || "Todos los productos";
  const body = !total
    ? h("div", { class: "empty" }, h("strong", {}, "No encontramos productos con esa búsqueda."), h("p", {}, "Prueba con otra palabra, la marca o quita filtros."))
    : offers ? offersLayout(items) : resultGrid(items, query, related);
  return h("div", { class: `page${offers ? " offers-page" : " search-page"}` },
    offers ? null : h("div", { class: "search-top" }, searchForm(query)),
    h("div", { class: "results-head" }, h("h1", {}, title), h("span", { class: "muted" }, countText(total, "producto"))),
    filters,
    body,
    total > items.length ? h("button", {
      type: "button", class: "more", onclick: () => { state.limit += PAGE_SIZE; render(); },
    }, `Mostrar más (${(total - items.length).toLocaleString("es-HN")})`) : null);
}

// Lo que sólo menciona la palabra ("fideos de huevo") va aparte, después de lo buscado.
function resultGrid(items, query, related) {
  const main = items.filter((product) => product.tier !== 2);
  const extra = items.filter((product) => product.tier === 2);
  if (!query || !extra.length) return h("div", { class: "results" }, items.map(resultCard));
  return [
    main.length ? h("div", { class: "results" }, main.map(resultCard)) : null,
    h("div", { class: "related-head" },
      h("h2", {}, `También mencionan “${query}”`),
      h("span", { class: "muted small" }, `${related.toLocaleString("es-HN")} productos de otros tipos`)),
    h("div", { class: "results" }, extra.map(resultCard)),
  ];
}

function historyBlock(offer) {
  const summary = offer?.historical_summary;
  const average = summary?.windows?.["30d"]?.average;
  const minimum = summary?.observed_minimum;
  const maximum = summary?.observed_maximum;
  if (!summary || !average || !minimum || !maximum || Number(maximum) <= Number(minimum)) return null;
  const current = Number(offer.current_price);
  const position = Math.min(100, Math.max(0, ((current - Number(minimum)) / (Number(maximum) - Number(minimum))) * 100));
  const verdict = current < Number(average) ? `Está por debajo de su precio usual en ${RETAILER_NAMES[offer.supermarket_id]}.`
    : current > Number(average) ? `Está por encima de su precio usual en ${RETAILER_NAMES[offer.supermarket_id]}.`
      : "Está en su precio usual.";
  return h("section", { class: "box" },
    h("h2", {}, "¿Es buen precio hoy?"),
    h("div", { class: "stats" },
      h("div", {}, h("span", {}, "Hoy"), h("strong", { class: "good" }, money(current))),
      h("div", {}, h("span", {}, "Usual (30 días)"), h("strong", {}, money(average))),
      h("div", {}, h("span", {}, "Más alto visto"), h("strong", {}, money(maximum)))),
    h("div", { class: "range", role: "img", "aria-label": `Precio de hoy en el ${Math.round(position)} % del rango observado` },
      h("div", { class: "range-fill", style: { width: `${position}%` } }),
      h("div", { class: "range-mark", style: { left: `${position}%` } })),
    h("div", { class: "range-labels" }, h("span", {}, `Más bajo ${money(minimum)}`), h("span", {}, `Más alto ${money(maximum)}`)),
    h("p", { class: "muted" }, verdict));
}

function viewProduct(id, container, code = "") {
  const catalog = state.catalog;
  if (catalog.ready && catalog.byId.has(id)) return renderProduct(catalog.byId.get(id), container, code);
  // Enlace abierto antes de tener el índice: se espera a que llegue.
  const token = state.renderToken;
  fill(container, loadingPage("Abriendo el producto…"));
  catalog.product(id).then((product) => {
    if (state.renderToken !== token || state.catalog !== catalog) return;
    const view = renderProduct(product, container, code);
    if (view) fill(container, view);
  }).catch((error) => {
    if (state.renderToken !== token) return;
    fill(container, h("div", { class: "page empty" }, h("h1", {}, "No pudimos abrir el producto."), h("p", {}, errorText(error)), h("a", { class: "primary", href: "#/" }, "Volver al inicio")));
  });
  return null;
}

// "Precio en cada súper": barra = 40 % + 60 % × (precio − mínimo) / (máximo − mínimo).
function barWidth(price, minimum, maximum) {
  if (!(maximum > minimum)) return 100;
  return 40 + (60 * (price - minimum)) / (maximum - minimum);
}

function offerNotes(offers, catalog) {
  const present = new Set(offers.map((offer) => offer.supermarket_id));
  const absent = catalog.retailers.filter((id) => !present.has(id)).map((id) => RETAILER_NAMES[id] || id);
  const notes = [];
  if (absent.length && offers.length) notes.push(`Hoy no lo encontramos en ${listText(absent)}.`);
  for (const offer of offers) {
    const discount = discountOf(offer);
    const label = storeLabel(offer, offers);
    if (discount) notes.push(`${label} la tiene en oferta, antes ${money(discount.regular)}.`);
    else if (offer.is_promotion === true) notes.push(`${label} la tiene en oferta.`);
  }
  return notes;
}

function renderProduct(product, container, code = "") {
  const catalog = state.catalog;
  if (!product) return h("div", { class: "page empty" }, h("h1", {}, "Este producto ya no está en el catálogo de hoy."), h("a", { class: "primary", href: "#/" }, "Volver al inicio"));
  const unit = product.unit_amount ? `${money(product.unit_amount)} por ${product.unit_per}` : "";
  const page = h("div", { class: "page product" },
    h("div", { class: "product-top" },
      h("button", { type: "button", class: "icon-button", "aria-label": "Volver", onclick: () => history.length > 1 ? history.back() : go("/") }, icon("back")),
      h("button", {
        type: "button", class: "icon-button", "aria-label": "Compartir",
        onclick: async () => {
          const data = { title: product.product_name, text: `${product.product_name} desde ${money(product.best_price)} en Súper Compras`, url: location.href };
          try { if (navigator.share) await navigator.share(data); else { await navigator.clipboard.writeText(location.href); toast("Enlace copiado"); } } catch { /* cancelado */ }
        },
      }, icon("share", 20))),
    h("div", { class: "product-layout" },
      h("div", { class: "product-image" }, productVisual(product, { size: 120, decorative: false, sizes: "(min-width: 1024px) 180px, 120px" })),
      h("div", { class: "product-title" },
        h("strong", { class: "brand-xl" }, product.brand_label ?? (product.brand || product.product_type || "")),
        h("h1", {}, productName(product)),
        h("div", { class: "pills" }, sizePill(product), unit ? h("span", { class: "muted small" }, unit) : null),
        h("span", { class: "muted small" }, [product.category, product.product_type].filter(Boolean).join(" › "))),
      h("p", { class: "product-from" }, h("span", {}, "desde"), h("strong", {}, money(product.best_price)))),
    h("section", { class: "offers", "aria-busy": "true" }, h("h2", {}, "Precio en cada súper"), h("p", { class: "muted" }, "Cargando precios…")),
    h("div", { class: "add-bar", hidden: true }),
    h("div", { class: "product-extra" }));
  fill(container, page);

  catalog.row(product).then((row) => {
    const offersBox = page.querySelector(".offers");
    if (!row) { fill(offersBox, h("h2", {}, "Precio en cada súper"), h("p", {}, "No pudimos cargar los precios.")); return; }
    const offers = liveOffers(row);
    const cheapest = offers[0];
    const cheapestLabel = cheapest ? storeLabel(cheapest, offers) : "";
    const prices = offers.map((offer) => Number(offer.current_price));
    const minimum = Math.min(...prices);
    const maximum = Math.max(...prices);
    offersBox.removeAttribute("aria-busy");
    fill(offersBox,
      h("h2", {}, "Precio en cada súper"),
      code ? scanSummary(code, cheapest, cheapestLabel, offers.length) : null,
      offers.length ? h("div", { class: "bars" }, offers.map((offer) => {
        const price = Number(offer.current_price);
        const regular = Number(offer.reported_regular_price);
        const onSale = offer.is_promotion === true || (regular && regular > price);
        const label = storeLabel(offer, offers);
        const unitPrice = offerUnitPrice(product, offer);
        const staleSince = catalog.staleSince(offer);
        const best = offers.length > 1 && price === minimum;
        return h("div", { class: `bar-row${best ? " best" : ""}` },
          h("div", { class: "bar-store" },
            h("span", { class: "bar-name" }, storeDot(offer.supermarket_id), h("strong", {}, label),
              best ? h("span", { class: "badge-best" }, "MÁS BARATO") : null),
            unitPrice ? h("span", { class: "muted small" }, `${money(unitPrice.amount)} por ${unitPrice.per}`) : null,
            staleSince ? h("span", { class: "stale" }, `Precio del ${shortDate(staleSince)} · puede haber cambiado`) : null),
          h("div", { class: "bar-track", "aria-hidden": "true" }, h("span", { class: "bar", style: { width: `${barWidth(price, minimum, maximum)}%` } })),
          h("div", { class: "bar-price" },
            h("strong", {}, money(offer.current_price)),
            onSale && regular > price ? h("span", { class: "sale" }, "Oferta · antes ", h("del", {}, money(regular))) : onSale ? h("span", { class: "sale" }, "Oferta") : null),
          h("button", {
            type: "button", class: "add-store", "aria-label": `Agregar a Mi compra en ${label}`, title: `Agregar en ${label}`,
            onclick: () => addToCart(product.id, offer.location_id, label),
          }, icon("plus", 18)));
      })) : h("p", {}, "Hoy ningún súper lo tiene con precio."),
      offerNotes(offers, catalog).map((note) => h("p", { class: "bar-note" }, note)),
      h("p", { class: "muted small" }, offers.some((offer) => catalog.staleSince(offer))
        ? "Precios en línea; los marcados con fecha no se pudieron actualizar hoy. Pueden variar en tienda."
        : "Precios en línea de hoy; pueden variar en tienda. Usa + para elegir en qué súper comprarlo."));

    const addBar = page.querySelector(".add-bar");
    fill(addBar, h("button", {
      type: "button", class: "primary wide",
      onclick: () => addToCart(product.id, offers.length > 1 && cheapest ? cheapest.location_id : null, offers.length > 1 ? cheapestLabel : ""),
    }, offers.length > 1 ? `Agregar a Mi compra · ${cheapestLabel}` : "Agregar a Mi compra"));
    addBar.hidden = false;
    page.classList.add("has-add-bar");

    if (!page.isConnected) return;
    const extra = page.querySelector(".product-extra");
    const priceHistory = historyBlock(cheapest);
    const others = (row.other_presentations || []).map((other) => {
      const otherProduct = catalog.byId.get(rowKey(other.row_id));
      // Tamaño o precio por unidad imposible: sólo el precio (ver hideImplausible).
      if (otherProduct?.suspect) return { other: { ...other, presentation: null, best_unit_price: null }, otherProduct };
      return { other, otherProduct };
    });
    const bestUnit = others.reduce((best, item) => {
      const value = Number(item.other.best_unit_price?.amount);
      return value && (!best || value < best) ? value : best;
    }, Number(offerUnitPrice(product, cheapest)?.amount) || null);
    const alternatives = catalog.alternatives(product);
    fill(extra,
      priceHistory,
      others.length ? h("section", {},
        sectionHeader("Otros tamaños", h("span", { class: "muted small" }, "precio por unidad para comparar tamaños")),
        h("div", { class: "row-scroll sizes" }, others.map(({ other, otherProduct }) => {
          const unitValue = Number(other.best_unit_price?.amount);
          return h("a", { class: `size-card${unitValue && unitValue === bestUnit ? " best" : ""}`, href: otherProduct ? `#/p/${otherProduct.id}` : "#" },
            sizePill({ presentation: other.presentation || "—", pack: other.pack }),
            priceTag(other.best_price),
            other.best_unit_price ? h("span", { class: "muted small" }, `${money(other.best_unit_price.amount)} / ${other.best_unit_price.per}`) : null,
            otherProduct ? h("span", { class: "muted small" }, otherProduct.retailers.map((r) => RETAILER_NAMES[r]).join(", ")) : null,
            unitValue && unitValue === bestUnit ? h("span", { class: "badge-text" }, "MÁS CONVENIENTE") : null,
            other.large_size ? h("span", { class: "badge-text muted" }, "TAMAÑO GRANDE") : null);
        }))) : null,
      alternatives.length ? h("section", {},
        sectionHeader("Alternativas", h("span", { class: "muted small" }, "otras marcas, por precio por unidad")),
        h("div", { class: "alt-list" }, alternatives.map((alt) => h("a", { class: "alt", href: `#/p/${alt.id}` },
          h("span", { class: "alt-tile" }, productVisual(alt, { size: 52 })),
          h("span", { class: "alt-main" }, h("strong", {}, alt.brand || ""), h("span", { class: "muted small" }, `${productName(alt)}`), storesLine(alt)),
          h("span", { class: "alt-price" }, priceTag(alt.best_price),
            h("span", { class: Number(alt.unit_amount) < Number(product.unit_amount) ? "good small" : "muted small" }, `${money(alt.unit_amount)} / ${alt.unit_per}`)))))) : null);
  }).catch((error) => {
    if (!page.isConnected) return;
    const offersBox = page.querySelector(".offers");
    offersBox.removeAttribute("aria-busy");
    fill(offersBox, h("h2", {}, "Precio en cada súper"), h("p", {}, `No pudimos cargar los precios. ${errorText(error)}`),
      h("button", { type: "button", class: "primary", onclick: () => render() }, "Reintentar"));
  });
  return null;
}

// Después de escanear: el código leído y dónde está más barato, arriba de las ofertas.
function scanSummary(code, cheapest, label, count) {
  const verdict = !cheapest ? "Hoy ningún súper lo tiene con precio."
    : count > 1 ? `Más barato hoy en ${label}: ${money(cheapest.current_price)}`
      : `Sólo lo encontramos en ${label}: ${money(cheapest.current_price)}`;
  return h("div", { class: "scan-hit", role: "status" },
    h("span", { class: "muted small" }, `Código ${code}`),
    h("strong", {}, verdict));
}

// #/codigo/<números>: un producto abre su ficha; varios, una lista; ninguno, el aviso.
function viewBarcode(raw, params, container) {
  const catalog = state.catalog;
  const digits = String(raw).replace(/\D/g, "").slice(0, 20);
  const format = params.get("f") || "";
  const token = state.renderToken;
  fill(container, loadingPage("Buscando el código…"));
  catalog.findBarcode(digits, format).then((result) => {
    if (state.renderToken !== token || state.catalog !== catalog) return;
    if (result.products.length === 1) {
      location.replace(`#/p/${encodeURIComponent(result.products[0].id)}?codigo=${digits}`);
      return;
    }
    fill(container, barcodeResult(digits, result));
  }).catch((error) => {
    if (state.renderToken !== token) return;
    fill(container, h("div", { class: "page empty" }, h("strong", {}, "No pudimos buscar el código."), h("p", {}, errorText(error)),
      h("button", { type: "button", class: "primary", onclick: () => render() }, "Reintentar")));
  });
  return null;
}

function scanAgainActions() {
  return h("div", { class: "scan-actions" },
    h("button", { type: "button", class: "primary", "data-scan": "" }, icon("scan", 18), "Escanear otro"),
    h("a", { class: "secondary", href: "#/buscar" }, "Buscar por nombre"));
}

function barcodeResult(digits, result) {
  if (!result.products.length) {
    const title = result.invalid ? "Ese código no es válido" : "No tenemos ese código todavía";
    const detail = result.invalid
      ? "Revisa los números: el último dígito no cuadra con los demás."
      : `Ningún súper de ${cityName()} lo publica en línea con ese código, o todavía no lo hemos visto. Prueba buscarlo por nombre.`;
    return h("div", { class: "page empty scan-empty" },
      h("h1", {}, title),
      h("p", { class: "scan-code" }, "Código leído: ", h("strong", {}, digits || "—")),
      h("p", {}, detail),
      scanAgainActions());
  }
  const catalog = state.catalog;
  const items = result.products.map((product) => {
    const best = h("span", { class: "muted small" }, `Desde ${money(product.best_price)}`);
    catalog.row(product).then((row) => {
      const offers = liveOffers(row);
      if (offers[0]) fill(best, h("strong", { class: "good" }, `Más barato en ${storeLabel(offers[0], offers)}: ${money(offers[0].current_price)}`));
    }).catch(() => {});
    return h("a", { class: "alt", href: `#/p/${product.id}?codigo=${digits}` },
      h("span", { class: "alt-tile" }, productVisual(product, { size: 52 })),
      h("span", { class: "alt-main" },
        h("strong", {}, [product.brand_label ?? product.brand, presentationLabel(product)].filter(Boolean).join(" · ")),
        h("span", { class: "muted small" }, productName(product)),
        best));
  });
  return h("div", { class: "page" },
    h("div", { class: "results-head" }, h("h1", {}, "Productos con este código"), h("span", { class: "muted" }, `Código ${digits}`)),
    h("p", { class: "muted" }, "El mismo código aparece en varias fichas (cada súper lo publica a su manera). Elige la que buscas."),
    h("div", { class: "alt-list scan-list" }, items),
    scanAgainActions());
}

// Tabla producto × súper (computadora): total de cada línea en cada súper; el más
// barato de cada fila en verde y negrita; faltante como "—".
function cartTable(loaded, stores) {
  const ids = stores.map(([id]) => id);
  return h("div", { class: "cart-table-wrap" },
    h("table", { class: "cart-table" },
      h("caption", {}, "Cada producto en cada súper (precio × cantidad)"),
      h("thead", {}, h("tr", {},
        h("th", { scope: "col" }, "Producto"),
        ids.map((id) => h("th", { scope: "col" }, storeDot(id), RETAILER_NAMES[id] || id)))),
      h("tbody", {}, loaded.map((line) => {
        const cells = ids.map((id) => line.offers.find((offer) => offer.supermarket_id === id) || null);
        const values = cells.filter(Boolean).map((offer) => Number(offer.current_price));
        const minimum = values.length > 1 ? Math.min(...values) : null;
        return h("tr", {},
          h("th", { scope: "row" }, productName(line.product), line.qty > 1 ? h("span", { class: "muted small" }, ` × ${line.qty}`) : null),
          cells.map((offer) => (offer
            ? h("td", { class: Number(offer.current_price) === minimum ? "cheapest" : null }, money(Number(offer.current_price) * line.qty),
              Number(offer.current_price) === minimum ? h("span", { class: "sr-only" }, " (el más barato)") : null)
            : h("td", { class: "missing" }, "—"))));
      })),
      h("tfoot", {}, h("tr", {},
        h("th", { scope: "row" }, "Todo en ese súper"),
        stores.map(([, entry]) => h("td", {}, money(entry.total),
          entry.missing ? h("span", { class: "warn small" }, ` (faltan ${entry.missing})`) : null))))));
}

function viewCart(container) {
  const catalog = state.catalog;
  if (!catalog.ready) return whenIndexed(catalog, "Cargando los precios de tu compra…");
  const lines = state.cart.map((line, index) => ({ ...line, index, product: catalog.byId.get(line.id) })).filter((line) => line.product);
  const missing = state.cart.filter((line) => !catalog.byId.get(line.id));
  const missingNote = missing.length ? h("div", { class: "notice warn small", role: "status" },
    h("span", {}, missing.length === 1
      ? "1 producto de tu lista ya no está en el catálogo de hoy (el súper dejó de publicarlo o cambió de ficha)."
      : `${missing.length} productos de tu lista ya no están en el catálogo de hoy (el súper dejó de publicarlos o cambiaron de ficha).`),
    h("button", { type: "button", class: "link", onclick: () => removeMissing() }, "Quitar de la lista")) : null;
  if (!lines.length) {
    return h("div", { class: "page empty" }, h("h1", {}, "Mi compra"), missingNote,
      h("p", {}, missing.length ? "No queda ningún producto con precio hoy." : "Todavía no agregaste productos."),
      h("a", { class: "primary", href: "#/buscar" }, "Buscar productos"));
  }
  const head = () => h("div", { class: "results-head" }, h("h1", {}, "Mi compra"), h("span", { class: "muted" }, countText(cartCount(), "artículo")));
  const page = h("div", { class: "page cart" },
    head(),
    missingNote,
    h("section", { class: "compare" },
      h("h2", {}, "¿Dónde te sale más barata toda la lista?"),
      h("div", { class: "compare-cards" }, h("p", { class: "muted" }, "Calculando…"))),
    h("div", { class: "cart-table-slot" }),
    h("section", { class: "cart-lines", "aria-label": "Productos de tu lista" }, h("p", { class: "muted" }, "Cargando precios…")),
    h("section", { class: "cart-summary" }));
  fill(container, page);

  Promise.all(lines.map((line) => catalog.row(line.product).then((row) => ({ ...line, offers: liveOffers(row) })))).then((loaded) => {
    let chosenTotal = 0;
    let split = 0;
    const where = new Map();
    const perStore = new Map(catalog.retailers.map((id) => [id, { total: 0, missing: 0 }]));
    const lineViews = loaded.map((line) => {
      const { offers } = line;
      const best = offers[0] || null;
      const picked = (line.store && offers.find((offer) => offer.location_id === line.store)) || null;
      const used = picked || best;
      if (used) chosenTotal += Number(used.current_price) * line.qty;
      if (best) {
        split += Number(best.current_price) * line.qty;
        const label = storeLabel(best, offers);
        where.set(label, (where.get(label) || 0) + 1);
      }
      for (const [id, entry] of perStore) {
        const offer = offers.find((item) => item.supermarket_id === id);
        if (offer) entry.total += Number(offer.current_price) * line.qty; else entry.missing += 1;
      }
      const gone = line.store && !picked;
      return h("div", { class: "line" },
        h("span", { class: "thumb thumb-line" }, productVisual(line.product, { size: 52 })),
        h("div", { class: "line-body" },
          h("a", { class: "line-main", href: `#/p/${line.id}` },
            h("strong", {}, [line.product.brand, presentationLabel(line.product)].filter(Boolean).join(" · ") || line.product.product_name),
            h("span", { class: "muted small" }, productName(line.product))),
          best ? h("span", { class: "line-cheapest" }, storeDot(best.supermarket_id),
            offers.length > 1 ? `Más barato en ${storeLabel(best, offers)}` : `Sólo en ${storeLabel(best, offers)}`) : null,
          offers.length ? h("label", { class: "line-store" },
            h("span", { class: "sr-only" }, "Súper"),
            h("select", {
              "aria-label": `Súper para ${line.product.product_name}`,
              onchange: (event) => updateLine(line.index, { store: event.target.value || undefined }),
            },
            h("option", { value: "", selected: !picked }, `Donde esté más barato (${best ? storeLabel(best, offers) : "—"})`),
            offers.map((offer) => h("option", { value: offer.location_id, selected: picked === offer },
              `${storeLabel(offer, offers)} · ${money(offer.current_price)}`)))) : h("span", { class: "warn small" }, "Sin precio hoy"),
          gone ? h("span", { class: "warn small" }, "Ese súper ya no lo tiene hoy; usamos el más barato.") : null,
          h("div", { class: "line-actions" },
            h("div", { class: "qty" },
              h("button", { type: "button", "aria-label": "Quitar uno", onclick: () => updateLine(line.index, { qty: line.qty - 1 }) }, "−"),
              h("span", {}, String(line.qty)),
              h("button", { type: "button", "aria-label": "Agregar uno", onclick: () => updateLine(line.index, { qty: line.qty + 1 }) }, "+")),
            h("span", { class: "line-total" }, h("strong", {}, used ? money(Number(used.current_price) * line.qty) : "—")))));
    });
    fill(page.querySelector(".cart-lines"), h("h2", {}, "Tu lista"), lineViews);

    const stores = [...perStore.entries()].filter(([, entry]) => entry.missing < loaded.length)
      .sort((a, b) => a[1].missing - b[1].missing || a[1].total - b[1].total);
    const saving = chosenTotal - split;
    const bestSingle = stores.find(([, entry]) => entry.missing === 0)?.[0] || null;
    fill(page.querySelector(".compare-cards"),
      h("div", { class: "cmp cmp-mix" },
        h("span", { class: "cmp-title" }, where.size > 1 ? `Mezclando ${where.size} súper` : "Donde es más barato"),
        h("strong", { class: "cmp-total" }, money(split)),
        h("span", { class: "cmp-note" }, "La opción más barata"),
        h("span", { class: "small" }, [...where.entries()].map(([label, count]) => `${label} (${count})`).join(" · "))),
      stores.map(([id, entry]) => {
        const extra = entry.total - split;
        return h("div", { class: `cmp${id === bestSingle ? " cmp-best" : ""}${entry.missing ? " cmp-incomplete" : ""}` },
          h("span", { class: "cmp-title" }, storeDot(id), `Todo en ${RETAILER_NAMES[id]}`),
          h("strong", { class: "cmp-total" }, money(entry.total)),
          entry.missing
            ? h("span", { class: "cmp-note warn" }, `Le ${entry.missing === 1 ? "falta 1 producto" : `faltan ${entry.missing} productos`}`)
            : h("span", { class: "cmp-note" }, extra > 0.005 ? `+ ${money(extra)} frente a mezclar` : "Igual que mezclar"));
      }));
    fill(page.querySelector(".cart-table-slot"), stores.length > 1 ? cartTable(loaded, stores) : null);
    fill(page.querySelector(".cart-summary"),
      h("div", { class: "best-total chosen" },
        h("span", { class: "badge-text" }, "CON LOS SÚPER QUE ELEGISTE"),
        h("strong", {}, money(chosenTotal)),
        saving > 0.005 ? h("span", { class: "warn small" }, `Podrías ahorrar ${money(saving)} comprando cada cosa donde es más barata.`) : h("span", { class: "good small" }, "Es lo más barato posible hoy.")),
      h("p", { class: "muted small" }, `Total estimado con los precios públicos de hoy en ${cityName()}. Tu lista se guarda en este dispositivo.`));
  }).catch((error) => {
    if (!page.isConnected) return;
    fill(page.querySelector(".cart-lines"), h("p", {}, `No pudimos cargar los precios. ${errorText(error)}`),
      h("button", { type: "button", class: "primary", onclick: () => render() }, "Reintentar"));
    fill(page.querySelector(".compare-cards"), h("p", { class: "muted" }, "Sin precios no podemos calcular el total. Tu lista sigue guardada."));
  });
  return null;
}

// ------------------------------------------------------------------ chrome & router
function renderChrome() {
  const count = cartCount();
  for (const badge of document.querySelectorAll("[data-cart-count]")) {
    badge.textContent = String(count);
    badge.hidden = count === 0;
  }
  for (const total of document.querySelectorAll("[data-cart-total]")) total.textContent = ` · ${count}`;
  const { path, params } = route();
  const active = path.startsWith("/buscar") ? "buscar" : path.startsWith("/ofertas") ? "ofertas" : path.startsWith("/compra") ? "compra" : path === "/" ? "inicio" : "";
  for (const link of document.querySelectorAll("[data-nav]")) {
    link.classList.toggle("active", link.dataset.nav === active);
    if (link.dataset.nav === active) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
  }
  // Buscador de la barra (computadora): muestra lo buscado sin pisar lo que se escribe.
  const input = document.querySelector("[data-top-search] input");
  if (input && document.activeElement !== input) input.value = path.startsWith("/buscar") ? params.get("q") || "" : "";
}

// El buscador visible (barra en computadora, arriba de los resultados en celular).
function focusSearch() {
  const input = [...document.querySelectorAll("input[type=search]")].find((item) => item.offsetParent !== null);
  input?.focus();
}

// ------------------------------------------------------------------ animación de entrada
// Sólo celular, una vez por sesión; sale cuando cargó la ciudad (mínimo 1.1 s, máximo
// 2.6 s desde que abrió la página) o al tocarla. Nunca retrasa la carga de datos.
const INTRO_KEY = "super-compras:intro";
const INTRO_MIN_MS = 1100;
const INTRO_MAX_MS = 2600;

function setupIntro() {
  const node = document.querySelector(".intro");
  if (!node) return;
  let seen = false;
  try { seen = sessionStorage.getItem(INTRO_KEY) === "1"; } catch { /* sin sessionStorage */ }
  if (seen || !window.matchMedia("(max-width: 1023px)").matches) { node.remove(); return; }
  try { sessionStorage.setItem(INTRO_KEY, "1"); } catch { /* sin guardar */ }
  node.addEventListener("pointerdown", () => dismissIntro({ now: true }), { once: true });
  setTimeout(() => dismissIntro({ now: true }), Math.max(0, INTRO_MAX_MS - performance.now()));
}

function dismissIntro({ now = false } = {}) {
  const node = document.querySelector(".intro");
  if (!node || node.classList.contains("is-leaving")) return;
  const wait = now ? 0 : INTRO_MIN_MS - performance.now();
  if (wait > 0) { setTimeout(() => dismissIntro({ now: true }), wait); return; }
  node.classList.add("is-leaving");
  const remove = () => node.remove();
  node.addEventListener("animationend", (event) => { if (event.target === node) remove(); });
  setTimeout(remove, 600);
}

const ADMIN_ROUTE = "/admin/revision";
let adminModule = null;

// El panel no necesita el catálogo: se monta una vez y conserva lo que se escribió.
async function renderAdmin(main) {
  if (main.dataset.view === "admin") return;
  main.dataset.view = "admin";
  fill(main, loadingPage("Abriendo el panel de revisión…"));
  try {
    adminModule = adminModule || await import("./admin.js?v=20261114-2");
    if (route().path.startsWith(ADMIN_ROUTE) && main.dataset.view === "admin") adminModule.mountSizeReview(main, { h, fill });
  } catch (error) {
    delete main.dataset.view;
    fill(main, h("div", { class: "page empty" }, h("h1", {}, "No pudimos abrir el panel."), h("p", {}, errorText(error))));
  }
  renderChrome();
}

function render() {
  const main = document.getElementById("main");
  if (route().path.startsWith(ADMIN_ROUTE)) {
    renderAdmin(main);
    return;
  }
  delete main.dataset.view;
  if (!state.catalog) return;
  state.renderToken += 1;
  closeMenu({ focus: false });
  state.observer?.disconnect();
  state.observer = null;
  state.loaders.clear();
  const { path, params } = route();
  let view;
  if (path.startsWith("/p/")) view = viewProduct(safeDecode(path.slice(3)), main, (params.get("codigo") || "").replace(/\D/g, ""));
  else if (path.startsWith("/codigo/")) view = viewBarcode(safeDecode(path.slice(8)), params, main);
  else if (path.startsWith("/buscar")) view = viewSearch(params);
  else if (path.startsWith("/ofertas")) view = viewSearch(params, { offers: true });
  else if (path.startsWith("/compra")) view = viewCart(main);
  else view = viewHome();
  if (view) fill(main, view);
  renderChrome();
}

// "#/p/%" o un enlace cortado a medias: decodeURIComponent lanza URIError.
function safeDecode(text) {
  try { return decodeURIComponent(text); } catch { return text; }
}

function pickCity(cities, fallback) {
  const wanted = new URLSearchParams(location.search).get("ciudad") || loadCitySlug() || fallback;
  return cities.find((city) => city.slug === wanted) || cities.find((city) => city.slug === fallback) || cities[0];
}

async function loadCity(city) {
  const main = document.getElementById("main");
  if (main.dataset.view !== "admin") fill(main, loadingPage(`Cargando los precios de ${city.name}…`));
  try {
    const catalog = await new Catalog(city.url).load();
    state.catalog = catalog;
    state.city = city;
    state.limit = PAGE_SIZE;
    state.lastQueryKey = "";
    state.cart = loadCart(city.slug);
    document.title = `Súper Compras · Compara precios de súper en ${city.name}`;
    for (const node of document.querySelectorAll("[data-city]")) fill(node, citySelect());
    render();
    // El índice completo se baja en segundo plano; al llegar se traducen las llaves
    // viejas de Mi compra (tarjetas por sucursal) y se quitan los productos que ya
    // no están en el catálogo.
    catalog.loadIndex().then(async () => {
      if (state.catalog !== catalog) return;
      const known = new Set(catalog.products.map((product) => product.id));
      if (!state.cart.some((line) => !known.has(line.id))) return;
      const legacy = await catalog.legacyIds().catch(() => null);
      if (state.catalog !== catalog) return;
      const migrated = migrateCartLines(state.cart, known, legacy);
      if (JSON.stringify(migrated) !== JSON.stringify(state.cart)) saveCart(migrated, city.slug);
      // Las líneas que ya no están en el catálogo se conservan y Mi compra lo avisa.
      state.cart = migrated;
      renderChrome();
      if (route().path.startsWith("/compra")) render();
    }).catch(() => {});
  } catch (error) {
    // El panel interno no depende del catálogo: un fallo al cargar precios no lo tapa.
    if (main.dataset.view === "admin") return;
    fill(main, h("div", { class: "page empty" }, h("h1", {}, "No pudimos cargar los precios."), h("p", {}, errorText(error)),
      h("button", { type: "button", class: "primary", onclick: () => location.reload() }, "Reintentar")));
  }
}

async function switchCity(slug) {
  const city = state.cities.find((item) => item.slug === slug);
  if (!city || city.slug === state.city?.slug) return;
  saveCitySlug(city.slug);
  if (location.hash.startsWith("#/p/") || location.hash.startsWith("#/compra")) location.hash = "#/";
  await loadCity(city);
}

async function start() {
  setupIntro();
  for (const node of document.querySelectorAll("[data-top-search]")) fill(node, searchForm(""));
  if (route().path.startsWith(ADMIN_ROUTE)) renderAdmin(document.getElementById("main"));
  try {
    const url = catalogUrl();
    // Una vista previa con ?catalogo= usa sólo ese catálogo; el sitio publicado lee cities.json.
    const { cities, fallback } = url === DEFAULT_CATALOG || url === document.body.dataset.catalogUrl
      ? await loadCities(url)
      : { cities: [{ slug: "san-pedro-sula", name: "San Pedro Sula", url }], fallback: "san-pedro-sula" };
    state.cities = cities;
    await loadCity(pickCity(cities, fallback));
  } finally {
    dismissIntro();
  }
}

// Escáner: el módulo (y la cámara) se piden sólo al tocar un botón [data-scan].
let scannerModule = null;
async function startScanner(opener) {
  try {
    scannerModule = scannerModule || await import("./scanner.js?v=20261114-2");
    scannerModule.openScanner({
      h, opener,
      onCode: (code, format) => {
        const before = location.hash;
        go(`/codigo/${code}`, format ? new URLSearchParams({ f: format }) : null);
        if (location.hash === before) render();  // el mismo código otra vez
      },
    });
  } catch {
    toast("No pudimos abrir el escáner");
  }
}

// Botón central Buscar (y Buscar del riel): lleva a #/buscar y enfoca el buscador.
let pendingSearchFocus = false;
document.addEventListener("click", (event) => {
  const searchLink = event.target.closest?.("[data-focus-search]");
  if (searchLink) {
    if (route().path.startsWith("/buscar")) { event.preventDefault(); focusSearch(); } else pendingSearchFocus = true;
    return;
  }
  const button = event.target.closest?.("[data-scan]");
  if (!button) return;
  event.preventDefault();
  // Escanear de la barra: sin cámara en el navegador, a la búsqueda por nombre.
  if (button.hasAttribute("data-scan-nav") && !navigator.mediaDevices) {
    pendingSearchFocus = true;
    if (route().path.startsWith("/buscar")) { pendingSearchFocus = false; focusSearch(); } else go("/buscar");
    return;
  }
  startScanner(button);
});
// Al cambiar de vista el foco pasa al contenido (sin mover la página): los lectores de
// pantalla anuncian la vista nueva y el siguiente Tab sigue desde ahí.
window.addEventListener("hashchange", () => {
  render();
  window.scrollTo(0, 0);
  document.getElementById("main")?.focus({ preventScroll: true });
  if (pendingSearchFocus) { pendingSearchFocus = false; focusSearch(); }
});
document.addEventListener("DOMContentLoaded", start);
if ("serviceWorker" in navigator && location.protocol === "https:") {
  navigator.serviceWorker.register("./sw.js").catch(() => {});
}
