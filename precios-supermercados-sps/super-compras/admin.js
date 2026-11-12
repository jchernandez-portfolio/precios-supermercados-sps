// Panel interno de revisión de tamaños (#/admin/revision).
// Mismo código de barras con tamaños distintos entre súper: una persona revisa la
// etiqueta en la página del súper y fija el tamaño oficial (o marca que no es el
// mismo producto). "Guardar" lanza el workflow que valida y guarda la decisión; el
// motor la aplica en el siguiente refresco: las ofertas con el tamaño oficial
// comparan y las demás quedan aparte.
// La llave de GitHub se guarda sólo en esta pestaña (sessionStorage) y sólo viaja
// en el encabezado Authorization a api.github.com.
import {
  RETAILER_NAMES, SIZE_DECISIONS_RUNS_URL, SIZE_UNITS, loadSizeQueue, sendSizeDecisions,
} from "./data.js?v=20261112-1";

const TOKEN_KEY = "super-compras-admin-github-token";
const UNIT_LABEL = { g: "g", ml: "ml", oz: "oz", lb: "lb", unit: "unidades" };
const KIND_LABEL = {
  empate: "Empate: hoy no se compara",
  atipico: "Un súper distinto: quedó fuera de la comparación",
};
// Búsqueda del código de barras en cada súper (EAN-13; Los Andes no tiene buscador por código).
const STORE_SEARCH = {
  walmart: (ean) => `https://www.walmart.com.hn/${ean}?_q=${ean}&map=ft`,
  paiz: (ean) => `https://www.paiz.com.hn/${ean}?_q=${ean}&map=ft`,
  la_colonia: (ean) => `https://www.lacolonia.com/${ean}?_q=${ean}&map=ft`,
  colonial: (ean) => `https://supercolonial.com/search?q=${ean}`,
  comisariato_los_andes: () => "https://comisariatolosandes.com/todos",
};

const review = { queue: null, error: null, loading: null, filter: "todos", decisions: {}, sent: new Set(), status: null, sending: false };

function readToken() {
  try { return sessionStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; }
}

function writeToken(value) {
  try {
    if (value) sessionStorage.setItem(TOKEN_KEY, value); else sessionStorage.removeItem(TOKEN_KEY);
  } catch { /* sin almacenamiento: la llave vive sólo en el campo */ }
}

export function ean13(gtin) {
  const digits = String(gtin || "").replace(/^0+/, "");
  return digits.length <= 8 ? digits.padStart(8, "0") : digits.padStart(13, "0");
}

// Tamaños publicados, el más repetido primero: son los botones rápidos.
export function sizeOptions(item) {
  const groups = new Map();
  for (const offer of item.offers) {
    if (!offer.value || !offer.unit) continue;
    const key = `${offer.value} ${offer.unit}`;
    const entry = groups.get(key) || { value: offer.value, unit: offer.unit, stores: [] };
    entry.stores.push(RETAILER_NAMES[offer.store] || offer.store);
    groups.set(key, entry);
  }
  return [...groups.values()].sort((a, b) => b.stores.length - a.stores.length);
}

export function decisionOf(entry) {
  if (!entry) return null;
  if (entry.different) return "different_products";
  return Number(String(entry.value ?? "").replace(",", ".")) > 0 && SIZE_UNITS.includes(entry.unit) ? "size" : null;
}

function pendingDecisions() {
  return Object.entries(review.decisions)
    .filter(([gtin, entry]) => decisionOf(entry) && !review.sent.has(gtin))
    .map(([gtin, entry]) => ({ gtin, decision: decisionOf(entry), value: entry.value, unit: entry.unit, note: entry.note }));
}

export function mountSizeReview(main, { h, fill }) {
  const list = h("div", { class: "rv-list" });
  const count = h("span", { class: "muted" });
  const status = h("p", { class: "rv-status", role: "status", "aria-live": "polite" });
  const send = h("button", { type: "button", class: "primary" }, "Guardar y resolver");
  const token = h("input", {
    id: "rv-token", type: "password", autocomplete: "off", spellcheck: "false", placeholder: "github_pat_…", value: readToken(),
  });
  token.addEventListener("input", () => writeToken(token.value.trim()));
  const forget = h("button", { type: "button", class: "rv-link" }, "Olvidar llave");
  forget.addEventListener("click", () => { token.value = ""; writeToken(""); token.focus(); });

  const chips = [
    ["todos", "Todos"], ["empate", "Empate"], ["atipico", "Un súper distinto"], ["pendiente", "Sin decidir"],
  ].map(([key, label]) => {
    const chip = h("button", { type: "button", class: "rv-chip", "aria-pressed": String(review.filter === key) }, label);
    chip.addEventListener("click", () => {
      review.filter = key;
      for (const other of chips) other.setAttribute("aria-pressed", String(other === chip));
      renderList();
    });
    return chip;
  });

  function offerRow(item, offer) {
    const search = STORE_SEARCH[offer.store];
    return h("div", { class: `rv-offer${offer.excluded ? " out" : ""}` },
      h("span", {},
        h("strong", {}, RETAILER_NAMES[offer.store] || offer.store),
        h("span", { class: "rv-source" }, offer.name)),
      h("span", { class: "rv-size" }, offer.size || "sin tamaño"),
      search ? h("a", { class: "rv-ext", href: search(ean13(item.gtin)), target: "_blank", rel: "noopener noreferrer" }, "Ver ↗") : h("span"));
  }

  function rowFor(item) {
    const entry = review.decisions[item.gtin] || {};
    const decided = decisionOf(entry);
    const sent = review.sent.has(item.gtin);
    const update = (changes) => {
      review.decisions[item.gtin] = { ...(review.decisions[item.gtin] || {}), ...changes };
      row.replaceWith(rowFor(item));
      renderCount();
    };
    const value = h("input", { type: "text", inputmode: "decimal", placeholder: "Tamaño", "aria-label": "Tamaño correcto", value: entry.value ?? "" });
    value.addEventListener("change", () => update({ value: value.value.trim(), different: false }));
    const unit = h("select", { "aria-label": "Unidad" },
      h("option", { value: "" }, "Unidad"),
      SIZE_UNITS.map((key) => h("option", { value: key, selected: entry.unit === key }, UNIT_LABEL[key])));
    unit.addEventListener("change", () => update({ unit: unit.value, different: false }));
    const different = h("input", { type: "checkbox", checked: Boolean(entry.different) });
    different.addEventListener("change", () => update({ different: different.checked }));
    const note = h("input", { type: "text", maxlength: "200", placeholder: "Nota (opcional): p. ej. la etiqueta dice 5 fl oz", "aria-label": "Nota", value: entry.note ?? "" });
    note.addEventListener("change", () => update({ note: note.value }));
    const picks = sizeOptions(item).map((option) => {
      const active = !entry.different && entry.value === option.value && entry.unit === option.unit;
      const button = h("button", { type: "button", class: "rv-pick", "aria-pressed": String(active), title: option.stores.join(", ") },
        `${option.value} ${UNIT_LABEL[option.unit] || option.unit}`, h("span", { class: "muted" }, ` (${option.stores.length})`));
      button.addEventListener("click", () => update({ value: option.value, unit: option.unit, different: false }));
      return button;
    });
    const row = h("article", { class: `rv-row${decided ? " done" : ""}${sent ? " sent" : ""}` },
      h("div", {},
        h("h2", { class: "rv-name" }, item.name),
        h("p", { class: "rv-meta" }, `GTIN ${item.gtin} · ${KIND_LABEL[item.kind] || item.kind}`),
        h("div", { class: "rv-offers" }, item.offers.map((offer) => offerRow(item, offer)))),
      h("div", { class: "rv-decide" },
        sent ? h("p", { class: "rv-sent" }, "Enviado: se aplica en el próximo refresco.") : null,
        h("p", { class: "rv-label" }, "Tamaño correcto"),
        h("div", { class: "rv-picks" }, picks),
        h("div", { class: "rv-manual" }, value, unit),
        h("label", { class: "rv-different" }, different, " No es el mismo producto (código reutilizado)"),
        note));
    return row;
  }

  function visibleItems() {
    const items = review.queue?.items || [];
    if (review.filter === "todos") return items;
    if (review.filter === "pendiente") return items.filter((item) => !decisionOf(review.decisions[item.gtin]) && !review.sent.has(item.gtin));
    return items.filter((item) => item.kind === review.filter);
  }

  function renderCount() {
    const pending = pendingDecisions().length;
    const total = review.queue?.items.length || 0;
    count.textContent = `${pending} por enviar · ${review.sent.size} enviados · ${total} en la cola`;
    send.disabled = review.sending || pending === 0;
  }

  function renderList() {
    if (review.error) {
      fill(list, h("p", { class: "rv-status warn" }, review.error));
    } else if (!review.queue) {
      fill(list, h("p", { class: "muted" }, "Cargando la cola…"));
    } else {
      const items = visibleItems();
      fill(list, items.length ? items.map(rowFor) : h("p", { class: "muted" }, "No hay productos en esta vista."));
    }
    renderCount();
  }

  function showStatus(text, kind = "", link = null) {
    fill(status, text, link ? [" ", h("a", { href: link, target: "_blank", rel: "noopener noreferrer" }, "Ver ejecución ↗")] : null);
    status.className = `rv-status ${kind}`;
  }

  send.addEventListener("click", async () => {
    const decisions = pendingDecisions();
    if (!token.value.trim()) { showStatus("Pega tu llave de GitHub antes de guardar.", "warn"); token.focus(); return; }
    review.sending = true;
    renderCount();
    showStatus(`Enviando ${decisions.length} decisiones…`);
    try {
      const result = await sendSizeDecisions(token.value, decisions);
      for (const item of decisions) review.sent.add(item.gtin);
      showStatus(`Listo: ${result.sent} decisiones enviadas. GitHub las valida y lanza el refresco (unos minutos).`, "ok", SIZE_DECISIONS_RUNS_URL);
    } catch (error) {
      showStatus(error.message, "warn");
    } finally {
      review.sending = false;
      renderList();
    }
  });

  const page = h("div", { class: "page rv-page" },
    h("header", { class: "rv-head" },
      h("div", {},
        h("h1", {}, "Revisión de tamaños"),
        h("p", { class: "muted" }, "Mismo código de barras, distinto tamaño publicado. Revisa la etiqueta en el súper y elige el tamaño correcto.")),
      h("div", { class: "rv-auth" },
        h("label", { for: "rv-token" }, "Llave de GitHub (sólo esta pestaña)"),
        token, forget)),
    h("div", { class: "rv-chips", role: "group", "aria-label": "Filtrar" }, chips),
    list,
    h("div", { class: "rv-bar" }, h("div", { class: "rv-bar-in" }, count, status, send)));
  fill(main, page);
  renderList();

  if (!review.queue && !review.loading) {
    review.loading = loadSizeQueue().then((queue) => { review.queue = queue; review.error = null; })
      .catch((error) => { review.error = error.message; })
      .finally(() => { review.loading = null; if (page.isConnected) renderList(); });
  } else if (review.loading) {
    review.loading.then(() => { if (page.isConnected) renderList(); });
  }
}
