// Súper Compras: comparador de precios de supermercados (San Pedro Sula).
// Vistas: inicio, búsqueda, ficha de producto, ofertas y Mi compra.
import {
  Catalog, RETAILER_COLORS, rowKey, RETAILER_INITIALS, RETAILER_NAMES, loadCart, money, saveCart,
} from "./data.js";
import { productIllustration } from "./illustration.js";

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
  cart: loadCart(),
  limit: PAGE_SIZE,
  lastQueryKey: "",
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
  return state.cart.reduce((sum, line) => sum + line.qty, 0);
}

function addToCart(id) {
  const line = state.cart.find((item) => item.id === id);
  if (line) line.qty += 1;
  else state.cart.push({ id, qty: 1 });
  saveCart(state.cart);
  renderChrome();
  toast("Agregado a Mi compra");
}

function setQty(id, qty) {
  state.cart = state.cart.map((line) => (line.id === id ? { ...line, qty } : line)).filter((line) => line.qty > 0);
  saveCart(state.cart);
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

function freshnessText() {
  const asOf = state.catalog?.asOf;
  if (!asOf) return "";
  const date = new Date(asOf);
  return `Precios de hoy · actualizados ${date.toLocaleString("es-HN", { weekday: "long", hour: "numeric", minute: "2-digit" })}`;
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
  }, icon("search", 18), input);
}

function sizePill(text) {
  return text ? h("span", { class: "pill" }, String(text).toUpperCase()) : null;
}

function storesLine(product) {
  const count = product.retailers.length;
  return h("span", { class: count > 1 ? "stores compare" : "stores" },
    count > 1 ? `Compara en ${count} súper` : `En ${RETAILER_NAMES[product.retailers[0]] || "1 súper"}`);
}

function productCard(product) {
  return h("a", { class: "card", href: `#/p/${product.id}` },
    h("div", { class: "tile" },
      product.promo ? h("span", { class: "badge-offer" }, "Oferta") : null,
      productIllustration(product, { size: 118 })),
    h("div", { class: "pills" }, sizePill(product.presentation)),
    h("strong", { class: "brand" }, product.brand || product.product_type || "Producto"),
    h("span", { class: "name" }, product.product_name),
    h("span", { class: "price" }, h("small", {}, "desde "), money(product.best_price)),
    product.unit_amount ? h("span", { class: "unit" }, `${money(product.unit_amount)} por ${product.unit_per}`) : h("span", { class: "unit" }, " "),
    storesLine(product));
}

function sectionHeader(title, link) {
  return h("div", { class: "section-head" }, h("h2", {}, title), link || null);
}

// ------------------------------------------------------------------ views
function viewHome() {
  const catalog = state.catalog;
  const deals = catalog.products
    .filter((product) => product.promo && product.best_price)
    .sort((a, b) => b.retailers.length - a.retailers.length || b.other_count - a.other_count)
    .slice(0, 12);
  const shown = new Set(deals.map((product) => product.id));
  const compared = catalog.products
    .filter((product) => product.retailers.length >= 3 && !shown.has(product.id))
    .sort((a, b) => b.retailers.length - a.retailers.length || a.product_name.length - b.product_name.length)
    .slice(0, 12);
  return h("div", { class: "page" },
    h("section", { class: "hero" },
      h("h1", {}, "Compara precios de súper en San Pedro Sula"),
      h("p", { class: "muted" }, `${catalog.products.length.toLocaleString("es-HN")} productos en ${catalog.retailers.map((id) => RETAILER_NAMES[id]).join(", ")}.`),
      h("div", { class: "hero-search" }, searchForm("", { large: true })),
      h("p", { class: "fresh" }, icon("pin", 14), freshnessText())),
    h("section", {},
      sectionHeader("Ofertas de hoy", h("a", { href: "#/ofertas" }, "Ver todas")),
      h("div", { class: "row-scroll" }, deals.map(productCard))),
    h("section", {},
      sectionHeader("Los más comparados"),
      h("div", { class: "row-scroll" }, compared.map(productCard))),
    h("section", {},
      sectionHeader("Departamentos"),
      h("div", { class: "departments" }, catalog.departments().map((department) =>
        h("a", { class: "department", href: `#/buscar?cat=${encodeURIComponent(department.name)}` },
          h("strong", {}, department.name),
          h("span", {}, `${department.count.toLocaleString("es-HN")} productos`))))),
    h("section", { class: "stores-strip" },
      h("span", { class: "label" }, "Comparamos"),
      catalog.retailers.map((id) => h("span", { class: "chip" }, h("i", { style: { background: RETAILER_COLORS[id] } }), RETAILER_NAMES[id])),
      h("p", { class: "muted small" }, "Precios públicos en línea de cada supermercado; pueden variar en tienda. Esta página no vende productos.")));
}

function viewSearch(params, { offers = false } = {}) {
  const catalog = state.catalog;
  const query = params.get("q") || "";
  const category = params.get("cat") || "";
  const retailer = params.get("super") || "";
  const promoOnly = offers || params.get("oferta") === "1";
  const sort = params.get("orden") || (query ? "relevance" : "unit");
  const key = [query, category, retailer, promoOnly, sort].join("|");
  if (key !== state.lastQueryKey) { state.limit = PAGE_SIZE; state.lastQueryKey = key; }
  const { total, items } = catalog.search({ query, category, retailer, promoOnly, sort, limit: state.limit });

  const update = (name, value) => {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value); else next.delete(name);
    go(offers ? "/ofertas" : "/buscar", next);
  };
  const select = (name, label, value, options) => h("label", { class: "filter" },
    h("span", { class: "sr-only" }, label),
    h("select", { "aria-label": label, onchange: (event) => update(name, event.target.value) },
      options.map(([optionValue, text]) => h("option", { value: optionValue, selected: optionValue === value }, text))));

  const filters = h("div", { class: "filters" },
    select("orden", "Ordenar", sort, [["relevance", "Más relevantes"], ["unit", "Más barato por kg/L"], ["price", "Precio más bajo"], ["stores", "Más súper para comparar"]]),
    select("super", "Supermercado", retailer, [["", "Todos los súper"], ...catalog.retailers.map((id) => [id, RETAILER_NAMES[id]])]),
    select("cat", "Departamento", category, [["", "Todos los departamentos"], ...catalog.departments().map((d) => [d.name, d.name])]),
    offers ? null : h("button", {
      type: "button", class: `toggle${promoOnly ? " on" : ""}`, "aria-pressed": String(promoOnly),
      onclick: () => update("oferta", promoOnly ? "" : "1"),
    }, "En oferta"));

  const title = offers ? "Ofertas de hoy" : query ? `Resultados para “${query}”` : category || "Todos los productos";
  return h("div", { class: "page" },
    h("div", { class: "search-top" }, offers ? null : searchForm(query)),
    h("div", { class: "results-head" }, h("h1", {}, title), h("span", { class: "muted" }, `${total.toLocaleString("es-HN")} productos`)),
    filters,
    total ? h("div", { class: "grid" }, items.map(productCard))
      : h("div", { class: "empty" }, h("strong", {}, "No encontramos productos con esa búsqueda."), h("p", {}, "Prueba con otra palabra, la marca o quita filtros.")),
    total > items.length ? h("button", {
      type: "button", class: "more", onclick: () => { state.limit += PAGE_SIZE; render(); },
    }, `Mostrar más (${(total - items.length).toLocaleString("es-HN")})`) : null);
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

function viewProduct(id, container) {
  const catalog = state.catalog;
  const product = catalog.byId.get(id);
  if (!product) return h("div", { class: "page empty" }, h("strong", {}, "Este producto ya no está en el catálogo de hoy."), h("a", { href: "#/" }, "Volver al inicio"));
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
      h("div", { class: "product-image" }, productIllustration(product, { size: 240, decorative: false })),
      h("div", { class: "product-info" },
        h("div", { class: "product-title" },
          h("strong", { class: "brand-xl" }, product.brand || product.product_type || ""),
          h("h1", {}, product.product_name),
          h("div", { class: "pills" }, sizePill(product.presentation),
            h("span", { class: "muted small" }, [product.category, product.product_type].filter(Boolean).join(" › ")))),
        h("section", { class: "offers", "aria-busy": "true" }, h("h2", {}, "Dónde comprar"), h("p", { class: "muted" }, "Cargando precios…")))),
    h("div", { class: "product-extra" }));
  fill(container, page);

  catalog.row(product).then((row) => {
    const offersBox = page.querySelector(".offers");
    if (!row) { fill(offersBox, h("h2", {}, "Dónde comprar"), h("p", {}, "No pudimos cargar los precios.")); return; }
    const offers = row.offers
      .filter((offer) => offer.current_price && offer.availability !== "out_of_stock")
      .sort((a, b) => Number(a.current_price) - Number(b.current_price));
    const cheapest = offers[0];
    offersBox.removeAttribute("aria-busy");
    fill(offersBox, 
      h("h2", {}, "Dónde comprar"),
      offers.map((offer, index) => {
        const regular = Number(offer.reported_regular_price);
        const onSale = offer.is_promotion === true || (regular && regular > Number(offer.current_price));
        return h("div", { class: `offer${index === 0 && offers.length > 1 ? " best" : ""}` },
          h("span", { class: "store-badge", style: { background: RETAILER_COLORS[offer.supermarket_id] } }, RETAILER_INITIALS[offer.supermarket_id] || "?"),
          h("div", { class: "offer-main" },
            h("div", {}, h("strong", {}, RETAILER_NAMES[offer.supermarket_id] || offer.supermarket_id),
              index === 0 && offers.length > 1 ? h("span", { class: "badge-best" }, "MÁS BARATO") : null),
            offer.unit_price ? h("span", { class: "muted small" }, `${money(offer.unit_price.amount)} por ${offer.unit_price.per}`) : null),
          h("div", { class: "offer-price" },
            h("strong", {}, money(offer.current_price)),
            onSale && regular > Number(offer.current_price) ? h("span", { class: "sale" }, "Oferta · antes ", h("del", {}, money(regular))) : onSale ? h("span", { class: "sale" }, "Oferta") : null));
      }),
      h("p", { class: "muted small" }, "Precios en línea de hoy; pueden variar en tienda."),
      h("button", { type: "button", class: "primary wide", onclick: () => addToCart(product.id) }, "Agregar a Mi compra"));

    const extra = page.querySelector(".product-extra");
    const priceHistory = historyBlock(cheapest);
    const others = (row.other_presentations || []).map((other) => {
      const otherProduct = catalog.byId.get(rowKey(other.row_id));
      return { other, otherProduct };
    });
    const bestUnit = others.reduce((best, item) => {
      const value = Number(item.other.best_unit_price?.amount);
      return value && (!best || value < best) ? value : best;
    }, Number(cheapest?.unit_price?.amount) || null);
    const alternatives = catalog.alternatives(product);
    fill(extra, 
      priceHistory,
      others.length ? h("section", {},
        sectionHeader("Otras presentaciones", h("span", { class: "muted small" }, "precio por unidad para comparar tamaños")),
        h("div", { class: "row-scroll sizes" }, others.map(({ other, otherProduct }) => {
          const unit = Number(other.best_unit_price?.amount);
          return h("a", { class: `size-card${unit && unit === bestUnit ? " best" : ""}`, href: otherProduct ? `#/p/${otherProduct.id}` : "#" },
            sizePill(other.presentation || "—"),
            h("strong", {}, money(other.best_price)),
            other.best_unit_price ? h("span", { class: "muted small" }, `${money(other.best_unit_price.amount)} / ${other.best_unit_price.per}`) : null,
            otherProduct ? h("span", { class: "muted small" }, otherProduct.retailers.map((r) => RETAILER_NAMES[r]).join(", ")) : null,
            unit && unit === bestUnit ? h("span", { class: "badge-text" }, "MÁS CONVENIENTE") : null,
            other.large_size ? h("span", { class: "badge-text muted" }, "TAMAÑO GRANDE") : null);
        }))) : null,
      alternatives.length ? h("section", {},
        sectionHeader("Alternativas", h("span", { class: "muted small" }, "otras marcas, por precio por unidad")),
        h("div", { class: "alt-list" }, alternatives.map((alt) => h("a", { class: "alt", href: `#/p/${alt.id}` },
          h("span", { class: "alt-tile" }, productIllustration(alt, { size: 52 })),
          h("span", { class: "alt-main" }, h("strong", {}, alt.brand || ""), h("span", { class: "muted small" }, `${alt.product_name}`), storesLine(alt)),
          h("span", { class: "alt-price" }, h("strong", {}, money(alt.best_price)),
            h("span", { class: Number(alt.unit_amount) < Number(product.unit_amount) ? "good small" : "muted small" }, `${money(alt.unit_amount)} / ${alt.unit_per}`)))))) : null);
  }).catch((error) => {
    fill(page.querySelector(".offers"), h("h2", {}, "Dónde comprar"), h("p", {}, `No pudimos cargar los precios: ${error.message}`));
  });
  return null;
}

function viewCart(container) {
  const catalog = state.catalog;
  const lines = state.cart.map((line) => ({ ...line, product: catalog.byId.get(line.id) })).filter((line) => line.product);
  if (!lines.length) {
    return h("div", { class: "page empty" }, h("h1", {}, "Mi compra"), h("p", {}, "Todavía no agregaste productos."), h("a", { class: "primary", href: "#/buscar" }, "Buscar productos"));
  }
  const page = h("div", { class: "page cart" },
    h("div", { class: "cart-layout" },
      h("section", { class: "cart-lines" },
        h("div", { class: "results-head" }, h("h1", {}, "Mi compra"), h("span", { class: "muted" }, `${cartCount()} artículos`)),
        lines.map((line) => h("div", { class: "line" },
          h("span", { class: "alt-tile" }, productIllustration(line.product, { size: 52 })),
          h("div", { class: "line-body" },
            h("a", { class: "line-main", href: `#/p/${line.id}` },
              h("strong", {}, [line.product.brand, line.product.presentation].filter(Boolean).join(" · ") || line.product.product_name),
              h("span", { class: "muted small" }, line.product.product_name)),
            h("div", { class: "line-actions" },
              h("div", { class: "qty" },
                h("button", { type: "button", "aria-label": "Quitar uno", onclick: () => setQty(line.id, line.qty - 1) }, "−"),
                h("span", {}, String(line.qty)),
                h("button", { type: "button", "aria-label": "Agregar uno", onclick: () => setQty(line.id, line.qty + 1) }, "+")),
              h("span", { class: "line-total" }, h("small", { class: "muted" }, "desde "), h("strong", {}, money(Number(line.product.best_price) * line.qty)))))))),
      h("aside", { class: "cart-summary" }, h("h2", {}, "¿Dónde me sale más barato?"), h("p", { class: "muted" }, "Calculando…"))));
  fill(container, page);

  Promise.all(lines.map((line) => catalog.row(line.product).then((row) => ({ ...line, row })))).then((loaded) => {
    let split = 0;
    const where = new Map();
    const perStore = new Map(catalog.retailers.map((id) => [id, { total: 0, missing: 0 }]));
    for (const line of loaded) {
      const offers = (line.row?.offers || []).filter((offer) => offer.current_price && offer.availability !== "out_of_stock");
      const best = offers.reduce((a, b) => (!a || Number(b.current_price) < Number(a.current_price) ? b : a), null);
      if (best) {
        split += Number(best.current_price) * line.qty;
        where.set(best.supermarket_id, [...(where.get(best.supermarket_id) || []), line.product.brand || line.product.product_name]);
      }
      for (const [id, entry] of perStore) {
        const offer = offers.filter((item) => item.supermarket_id === id).sort((a, b) => Number(a.current_price) - Number(b.current_price))[0];
        if (offer) entry.total += Number(offer.current_price) * line.qty; else entry.missing += 1;
      }
    }
    const stores = [...perStore.entries()].filter(([, entry]) => entry.missing < loaded.length)
      .sort((a, b) => a[1].missing - b[1].missing || a[1].total - b[1].total);
    fill(page.querySelector(".cart-summary"), 
      h("h2", {}, "¿Dónde me sale más barato?"),
      h("div", { class: "best-total" },
        h("span", { class: "badge-text" }, "COMPRANDO CADA COSA DONDE ES MÁS BARATA"),
        h("strong", {}, money(split)),
        h("span", { class: "muted small" }, [...where.entries()].map(([id, items]) => `${RETAILER_NAMES[id]} (${items.length})`).join(" · "))),
      stores.map(([id, entry]) => h("div", { class: "store-total" },
        h("i", { style: { background: RETAILER_COLORS[id] } }),
        h("span", { class: "store-total-main" }, h("strong", {}, `Todo en ${RETAILER_NAMES[id]}`),
          h("span", { class: entry.missing ? "warn small" : "muted small" }, entry.missing ? `Le ${entry.missing === 1 ? "falta 1 producto" : `faltan ${entry.missing} productos`}` : "Tiene todo")),
        h("strong", {}, money(entry.total)))),
      h("p", { class: "muted small" }, "Total estimado con los precios públicos de hoy. Tu lista se guarda en este dispositivo."));
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
  const { path } = route();
  const active = path.startsWith("/buscar") ? "buscar" : path.startsWith("/ofertas") ? "ofertas" : path.startsWith("/compra") ? "compra" : path === "/" ? "inicio" : "";
  for (const link of document.querySelectorAll("[data-nav]")) link.classList.toggle("active", link.dataset.nav === active);
}

function render() {
  const main = document.getElementById("main");
  if (!state.catalog) return;
  const { path, params } = route();
  let view;
  if (path.startsWith("/p/")) view = viewProduct(decodeURIComponent(path.slice(3)), main);
  else if (path.startsWith("/buscar")) view = viewSearch(params);
  else if (path.startsWith("/ofertas")) view = viewSearch(params, { offers: true });
  else if (path.startsWith("/compra")) view = viewCart(main);
  else view = viewHome();
  if (view) fill(main, view);
  renderChrome();
}

async function start() {
  const main = document.getElementById("main");
  try {
    state.catalog = await new Catalog(catalogUrl()).load();
    const known = new Set(state.catalog.products.map((product) => product.id));
    state.cart = state.cart.filter((line) => known.has(line.id));
    render();
  } catch (error) {
    fill(main, h("div", { class: "page empty" }, h("strong", {}, "No pudimos cargar los precios."), h("p", {}, error.message),
      h("button", { type: "button", class: "primary", onclick: () => location.reload() }, "Reintentar")));
  }
}

window.addEventListener("hashchange", () => { render(); window.scrollTo(0, 0); });
document.addEventListener("DOMContentLoaded", start);
if ("serviceWorker" in navigator && location.protocol === "https:") {
  navigator.serviceWorker.register("./sw.js").catch(() => {});
}
