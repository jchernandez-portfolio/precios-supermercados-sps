import {
  RETAILERS, addNewLines, analyzeBasketOptions, cartIdentity, cartSummary, clearDependentFilters,
  confirmLineUpdates, detectCartUpdates, facetsAreCompatible, filterIndexEntries,
  formatHnl, humanFreshness, humanHistoricalPosition, indexPathForType, lineFromOffer, loadCart,
  manifestIsCompatible, moneyToMinor, normalizeText, offerIsUsable, offersByRetailer,
  partitionPaths, prepareBatch, quantityValue, reconcileDependentFilters, relativePriceState,
  refreshCartPrices, saveCart, searchIndexForPrefix,
} from "./catalog.js?v=20260915-2";
import {analysisIsCompatible, renderConsumerAnalysis} from "./analysis.js?v=20260915-2";

export * from "./catalog.js?v=20260915-2";

const RESULT_PAGE_SIZE = 40;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function option(value, label) {
  const node = el("option", null, label);
  node.value = value;
  return node;
}

function productLabel(product) {
  const parts = [product.product_name];
  const normalized = normalizeText(product.product_name);
  for (const value of [product.brand, product.presentation]) {
    if (value && !normalized.includes(normalizeText(value))) parts.push(value);
  }
  return parts.filter(Boolean).join(" · ");
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = el("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.hidden = true;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

function stateLabel(value) {
  return {best: "Mejor precio", intermediate: "Precio intermedio", highest: "Precio más alto", equivalent: "Mismo precio"}[value] ?? "Precio disponible";
}

function createApp() {
  const state = {
    manifest: null, facets: null, analysis: null, baseUrl: null, files: new Map(), indexEntries: [],
    rows: new Map(), visibleRows: [], partitions: new Map(), staging: new Map(),
    pendingUpdates: [], cart: loadCart(localStorage), requestToken: 0,
    displayLimit: RESULT_PAGE_SIZE, matchingCount: 0,
    filter: {category: "", product_type: "", brand: "", presentation: "", query: ""},
  };
  const ui = Object.fromEntries([
    "category-filter", "type-filter", "brand-filter", "presentation-filter", "product-search",
    "clear-filters", "active-filters", "result-count", "data-status", "results", "batch-status",
    "batch-add", "update-confirmation", "update-message", "confirm-updates", "cart-count",
    "mobile-cart-count", "mini-cart-stats", "mini-cart-retailers", "mini-cart-total", "cart-stats",
    "cart-groups", "cart-total", "price-refresh", "price-refresh-message", "price-refresh-button",
    "export-csv", "export-pdf", "export-status", "basket-analysis", "basket-analysis-content",
    "analysis-tab", "shopping-tab", "analysis-panel", "shopping-panel", "analysis-status",
    "analysis-summary", "analysis-retailers", "analysis-opportunities", "analysis-categories",
  ].map((id) => [id, document.getElementById(id)]));

  function showTab(panelId, {updateHash = true} = {}) {
    const analysisSelected = panelId === "analysis-panel";
    ui["analysis-panel"].hidden = !analysisSelected;
    ui["shopping-panel"].hidden = analysisSelected;
    ui["analysis-tab"].classList.toggle("is-active", analysisSelected);
    ui["shopping-tab"].classList.toggle("is-active", !analysisSelected);
    ui["analysis-tab"].setAttribute("aria-selected", String(analysisSelected));
    ui["shopping-tab"].setAttribute("aria-selected", String(!analysisSelected));
    if (updateHash) history.replaceState(null, "", analysisSelected ? "#analisis" : "#compra-inteligente");
  }

  async function digestHex(bytes) {
    const hash = await crypto.subtle.digest("SHA-256", bytes);
    return [...new Uint8Array(hash)].map((value) => value.toString(16).padStart(2, "0")).join("");
  }

  async function fetchJson(path, {manifest = false} = {}) {
    const url = manifest ? path : new URL(path, state.baseUrl).href;
    const response = await fetch(url, {cache: "no-store"});
    if (!response.ok) throw new Error(`public_data_http_${response.status}`);
    const bytes = await response.arrayBuffer();
    if (!manifest) {
      const expected = state.files.get(path);
      if (!expected || bytes.byteLength !== expected.bytes || await digestHex(bytes) !== expected.sha256) throw new Error("public_data_integrity_invalid");
    }
    return JSON.parse(new TextDecoder().decode(bytes));
  }

  function categoryRecord() {
    const value = state.filter.category === "__unknown__" ? null : state.filter.category;
    return state.facets?.categories.find((category) => category.value === value) ?? null;
  }

  function fillSelect(select, values, selected, firstLabel, labeler = (value) => value ?? "Sin normalizar") {
    select.replaceChildren(option("", firstLabel));
    for (const value of values) select.append(option(value ?? "__unknown__", labeler(value)));
    select.value = selected;
  }

  function renderFilters() {
    const categories = state.facets?.categories ?? [];
    fillSelect(ui["category-filter"], categories.map((item) => item.value), state.filter.category, "Elige una categoría", (value) => categories.find((item) => item.value === value)?.label);
    const category = categoryRecord();
    const types = category?.navigation === "facets" ? category.product_types.map((item) => item.value) : [];
    fillSelect(ui["type-filter"], types, state.filter.product_type, category?.navigation === "search" ? "Usa la búsqueda" : "Elige un producto");
    ui["type-filter"].disabled = !category || category.navigation === "search";
    const reconciled = reconcileDependentFilters(state.indexEntries, state.filter);
    state.filter = reconciled.filter;
    fillSelect(ui["brand-filter"], reconciled.options.brands, state.filter.brand, "Todas", (value) => value ?? "Sin marca normalizada");
    fillSelect(ui["presentation-filter"], reconciled.options.presentations, state.filter.presentation, "Todas", (value) => value ?? "Sin presentación normalizada");
    ui["brand-filter"].disabled = !state.indexEntries.length;
    ui["presentation-filter"].disabled = !state.indexEntries.length;
    ui["product-search"].value = state.filter.query;
    const chips = [];
    for (const [field, label] of [["category", "Categoría"], ["product_type", "Producto"], ["brand", "Marca"], ["presentation", "Presentación"]]) {
      if (!state.filter[field]) continue;
      const chip = el("button", "chip", `${state.filter[field] === "__unknown__" ? "Sin normalizar" : state.filter[field]} ×`);
      chip.type = "button";
      chip.setAttribute("aria-label", `Quitar filtro ${label}`);
      chip.addEventListener("click", () => { void changeFilter(field, ""); });
      chips.push(chip);
    }
    ui["active-filters"].replaceChildren(...chips);
  }

  async function loadIndex() {
    const category = categoryRecord();
    let path = null;
    if (category?.navigation === "facets" && state.filter.product_type) path = indexPathForType(category, state.filter.product_type === "__unknown__" ? null : state.filter.product_type);
    if (category?.navigation === "search") path = searchIndexForPrefix(category, state.filter.query)?.path ?? null;
    if (!path) { state.indexEntries = []; return; }
    const index = await fetchJson(path);
    if (index?.schema !== "rpi-consumer-index/v3" || !Array.isArray(index.rows)) throw new Error("catalog_index_invalid");
    state.indexEntries = index.rows;
  }

  async function loadPartitions(paths) {
    await Promise.all(paths.map(async (path) => {
      if (state.partitions.has(path)) return;
      const partition = await fetchJson(path);
      if (partition?.schema !== "rpi-consumer-catalog-partition/v3" || partition.partition !== path || !Array.isArray(partition.rows)) throw new Error("catalog_partition_invalid");
      state.partitions.set(path, partition.rows);
      for (const row of partition.rows) state.rows.set(row.row_id, {...row, _partition: path});
    }));
  }

  async function refreshResults() {
    const token = ++state.requestToken;
    renderFilters();
    const matches = filterIndexEntries(state.indexEntries, state.filter);
    state.matchingCount = matches.length;
    const entries = matches.slice(0, state.displayLimit);
    if (!entries.length) { state.visibleRows = []; renderMatrix(); return; }
    ui["data-status"].textContent = "Cargando precios del filtro…";
    await loadPartitions(partitionPaths(entries));
    if (token !== state.requestToken) return;
    const ids = new Set(entries.map((entry) => entry.row_id));
    state.visibleRows = [...state.rows.values()].filter((row) => ids.has(row.row_id));
    renderMatrix();
    ui["data-status"].textContent = `${state.visibleRows.length} resultados cargados desde la publicación verificada.`;
  }

  async function changeFilter(field, value) {
    state.filter = clearDependentFilters(state.filter, field, value);
    state.displayLimit = RESULT_PAGE_SIZE;
    if (field === "query") state.filter.query = value;
    if (["category", "product_type"].includes(field)) state.staging.clear();
    try {
      if (["category", "product_type", "query"].includes(field)) await loadIndex();
      await refreshResults();
    } catch (error) {
      ui["data-status"].dataset.error = error instanceof Error ? error.message : "unknown_error";
      ui["data-status"].textContent = "No fue posible cargar este grupo. Tu compra permanece guardada.";
    }
  }

  function quantityControl(row, staged) {
    const wrap = el("div", "quantity-control");
    const minus = el("button", null, "−"), input = el("input", "quantity-input"), plus = el("button", null, "+");
    minus.type = plus.type = "button";
    input.type = "number"; input.min = "1"; input.max = "999"; input.inputMode = "numeric"; input.value = String(staged.quantity);
    input.setAttribute("aria-label", `Cantidad de ${row.product_name}`);
    minus.setAttribute("aria-label", `Reducir cantidad de ${row.product_name}`);
    plus.setAttribute("aria-label", `Aumentar cantidad de ${row.product_name}`);
    const update = (value) => { staged.quantity = quantityValue(value, staged.quantity); input.value = String(staged.quantity); minus.disabled = staged.quantity <= 1; renderBatchBar(); };
    minus.addEventListener("click", () => update(staged.quantity - 1));
    plus.addEventListener("click", () => update(staged.quantity + 1));
    input.addEventListener("change", () => update(input.value));
    minus.disabled = staged.quantity <= 1;
    wrap.append(minus, input, plus);
    return wrap;
  }

  function historyDetails(offer) {
    const history = offer.historical_summary;
    if (!history) return null;
    const details = el("details", "price-history");
    details.append(el("summary", null, humanHistoricalPosition(history.historical_position)));
    const grid = el("div", "history-grid");
    for (const [label, value] of [
      ["Anterior", history.previous_price], ["Promedio 30d", history.windows?.["30d"]?.average],
      ["Promedio 90d", history.windows?.["90d"]?.average], ["Mínimo 90d", history.windows?.["90d"]?.minimum],
      ["Máximo 90d", history.windows?.["90d"]?.maximum],
    ]) {
      const item = el("span"); item.append(el("small", null, label), el("strong", null, value ? formatHnl(moneyToMinor(value)) : "Sin historia suficiente")); grid.append(item);
    }
    details.append(grid);
    return details;
  }

  function priceChoice(row, retailer, offer, staged) {
    if (!offer) return el("span", "missing-price", "—");
    const price = moneyToMinor(offer.current_price);
    if (!offerIsUsable(offer) || price === null) return el("span", "missing-price", "—");
    const priceState = relativePriceState(row, offer);
    const selected = staged.source_product_id === offer.source_product_id;
    const wrap = el("div", "price-offer");
    const label = el("label", `price-choice price-${priceState}${selected ? " is-selected" : ""}`);
    const input = el("input", "choice-input");
    input.type = "radio"; input.name = `retailer-${row.row_id}`; input.value = offer.source_product_id; input.checked = selected;
    input.setAttribute("aria-label", `Seleccionar ${retailer.name} para ${row.product_name}, precio ${formatHnl(price)}, ${stateLabel(priceState)}`);
    label.append(input, el("strong", null, formatHnl(price)), el("small", null, stateLabel(priceState)));
    const regular = moneyToMinor(offer.reported_regular_price);
    if (regular !== null) label.append(el("del", null, formatHnl(regular)));
    if (offer.is_promotion === true) label.append(el("span", "promo", "Promoción"));
    label.title = humanFreshness(offer.freshness_status);
    input.addEventListener("change", () => {
      staged.source_product_id = offer.source_product_id;
      renderMatrix();
      const renderedRow = [...ui.results.querySelectorAll("tbody tr")].find((item) => item.dataset.productId === row.row_id);
      renderedRow?.querySelector(".choice-input:checked")?.focus();
    });
    wrap.append(label);
    const history = historyDetails(offer);
    if (history) wrap.append(history);
    return wrap;
  }

  function otherPresentations(row) {
    const others = Array.isArray(row.other_presentations) ? row.other_presentations : [];
    if (!others.length) return null;
    const details = el("details", "other-presentations");
    details.append(el("summary", null, `Otras presentaciones (${others.length})`));
    const list = el("ul");
    for (const other of others) {
      const price = moneyToMinor(other.best_price);
      const unit = other.best_unit_price ? ` · ${formatHnl(moneyToMinor(other.best_unit_price.amount))} / ${other.best_unit_price.per}` : "";
      const item = el("li", null, `${other.presentation || other.product_name}: ${price === null ? "sin precio" : `desde ${formatHnl(price)}`}${unit}`);
      item.title = other.product_name;
      if (other.large_size === true) item.append(el("span", "large-size", "Tamaño grande"));
      list.append(item);
    }
    details.append(list);
    return details;
  }

  function renderMatrix() {
    const results = ui.results;
    results.replaceChildren();
    ui["result-count"].textContent = state.matchingCount > state.visibleRows.length ? `${state.visibleRows.length} de ${state.matchingCount} resultados` : `${state.visibleRows.length} resultado${state.visibleRows.length === 1 ? "" : "s"}`;
    if (!state.visibleRows.length) {
      const category = categoryRecord();
      const text = category?.navigation === "search" && normalizeText(state.filter.query).length < 2 ? "Esta categoría no tiene normalización suficiente. Escribe al menos dos letras en la búsqueda secundaria." : "No hay productos que cumplan exactamente estos filtros.";
      results.append(el("p", "empty-state", text)); renderBatchBar(); return;
    }
    const table = el("table", "comparison-matrix");
    const caption = el("caption", "sr-only", "Comparación de precios por producto y supermercado");
    const thead = el("thead"), head = el("tr");
    for (const label of ["Producto", "Cantidad", ...RETAILERS.map((item) => item.name)]) head.append(el("th", null, label));
    thead.append(head); const tbody = el("tbody");
    for (const row of state.visibleRows) {
      const staged = state.staging.get(row.row_id) ?? {quantity: 1, source_product_id: null, partition: row._partition};
      state.staging.set(row.row_id, staged);
      const tr = el("tr"); tr.dataset.productId = row.row_id;
      const product = el("th", "product-cell"); product.scope = "row";
      product.append(
        el("strong", null, row.product_name),
        el("small", "product-meta", [row.brand, row.presentation].filter(Boolean).join(" · ") || "Datos de presentación no disponibles"),
        el("small", null, row.comparability === "comparable" ? "Comparación disponible" : "Oferta individual"),
      );
      const others = otherPresentations(row);
      if (others) product.append(others);
      tr.append(product);
      const quantity = el("td", "quantity-cell"); quantity.dataset.label = "Cantidad"; quantity.append(quantityControl(row, staged)); tr.append(quantity);
      const offers = offersByRetailer(row);
      for (const retailer of RETAILERS) { const cell = el("td", "price-cell"); cell.dataset.label = retailer.name; cell.append(priceChoice(row, retailer, offers.get(retailer.supermarket_id), staged)); tr.append(cell); }
      tbody.append(tr);
    }
    table.append(caption, thead, tbody); results.append(table);
    if (state.matchingCount > state.visibleRows.length) {
      const more = el("button", "secondary-button load-more", `Mostrar ${Math.min(RESULT_PAGE_SIZE, state.matchingCount - state.visibleRows.length)} más`);
      more.type = "button"; more.addEventListener("click", () => { state.displayLimit += RESULT_PAGE_SIZE; void refreshResults(); }); results.append(more);
    }
    renderBatchBar();
  }

  function preparedRows() { return state.visibleRows.filter((row) => state.staging.get(row.row_id)?.source_product_id); }

  function renderBatchBar() {
    const count = preparedRows().length;
    ui["batch-status"].textContent = count ? `${count} producto${count === 1 ? "" : "s"} preparado${count === 1 ? "" : "s"}` : "Ningún producto preparado";
    ui["batch-add"].disabled = count === 0;
    ui["batch-add"].textContent = count ? `Agregar ${count} producto${count === 1 ? "" : "s"} a Mi Compra` : "Agregar seleccionados a Mi Compra";
  }

  function persistCart() { saveCart(localStorage, state.cart); renderCart(); renderRefresh(); renderBasketAnalysis(); }

  function renderMiniCart(summary) {
    ui["cart-count"].textContent = ui["mobile-cart-count"].textContent = String(summary.products);
    ui["mini-cart-stats"].textContent = `${summary.products} productos · ${summary.units} unidades`;
    ui["mini-cart-retailers"].replaceChildren(...RETAILERS.filter((item) => summary.retailers.has(item.supermarket_id)).map((item) => { const group = summary.retailers.get(item.supermarket_id); const row = el("div", "mini-cart-row"); row.append(el("span", null, item.name), el("strong", null, group.incomplete ? "Incompleto" : formatHnl(group.subtotal_minor))); return row; }));
    ui["mini-cart-total"].textContent = summary.grand_total_minor === null ? "Total incompleto" : `Total ${formatHnl(summary.grand_total_minor)}`;
  }

  function cartQuantity(line, saved) {
    const wrap = el("div", "quantity-control compact"); const minus = el("button", null, "−"), input = el("input", "quantity-input"), plus = el("button", null, "+");
    minus.type = plus.type = "button"; input.type = "number"; input.min = "1"; input.max = "999"; input.value = String(line.quantity); input.setAttribute("aria-label", `Cantidad de ${line.product_name}`);
    const update = (value) => { saved.quantity = quantityValue(value, saved.quantity); persistCart(); };
    minus.addEventListener("click", () => update(saved.quantity - 1)); plus.addEventListener("click", () => update(saved.quantity + 1)); input.addEventListener("change", () => update(input.value)); wrap.append(minus, input, plus); return wrap;
  }

  function retailerPicker(line, saved) {
    const row = state.rows.get(cartIdentity(line)); if (!row) return null;
    const select = el("select", "retailer-picker"); select.setAttribute("aria-label", `Cambiar supermercado de ${line.product_name}`);
    for (const retailer of RETAILERS) { const offer = offersByRetailer(row).get(retailer.supermarket_id); if (offerIsUsable(offer)) select.append(option(offer.source_product_id, `${retailer.name} · ${formatHnl(moneyToMinor(offer.current_price))}`)); }
    select.value = line.source_product_id;
    select.addEventListener("change", () => { const offer = row.offers.find((item) => item.source_product_id === select.value); const replacement = lineFromOffer(row, offer, saved.quantity, row._partition); if (replacement) Object.assign(saved, replacement, {checked: saved.checked}); persistCart(); });
    return select;
  }

  function renderCart() {
    const summary = cartSummary(state.cart); renderMiniCart(summary); ui["cart-groups"].replaceChildren();
    ui["cart-stats"].textContent = `${summary.products} productos · ${summary.units} unidades · ${summary.retailer_count} supermercados · ${summary.incomplete} incompletos`;
    ui["cart-total"].textContent = summary.grand_total_minor === null ? "TOTAL ESTIMADO INCOMPLETO" : `TOTAL ESTIMADO ${formatHnl(summary.grand_total_minor)}`;
    ui["export-csv"].disabled = ui["export-pdf"].disabled = summary.products === 0;
    if (!summary.products) { ui["cart-groups"].append(el("p", "empty-state", "Tu compra está vacía.")); return; }
    for (const retailer of RETAILERS) {
      const group = summary.retailers.get(retailer.supermarket_id); if (!group) continue;
      const section = el("section", "retailer-group"); section.append(el("h3", null, retailer.name));
      const table = el("table", "cart-table"), thead = el("thead"), head = el("tr"); for (const label of ["Producto", "Cantidad", "Precio unitario", "Total"]) head.append(el("th", null, label)); thead.append(head); const body = el("tbody");
      for (const line of group.lines) {
        const saved = state.cart.find((item) => cartIdentity(item) === cartIdentity(line)); if (!saved) continue;
        const tr = el("tr", `${line.checked ? "is-checked" : ""} ${line.invalid ? "is-invalid" : ""}`); const product = el("th", "cart-product"); product.scope = "row";
        const check = el("button", "check-button", line.checked ? "☑" : "☐"); check.type = "button"; check.setAttribute("aria-label", line.checked ? `Marcar pendiente ${line.product_name}` : `Marcar comprado ${line.product_name}`); check.addEventListener("click", () => { saved.checked = !saved.checked; persistCart(); });
        const info = el("div"); info.append(el("strong", null, productLabel(line))); const picker = retailerPicker(line, saved); if (picker) info.append(picker);
        if (line.invalid) info.append(el("small", "danger-text", "Oferta no disponible; no fue sustituida.")); else if (line.freshness_status === "STALE") info.append(el("small", "warning", "Precio no actualizado recientemente."));
        const remove = el("button", "text-button danger-text", "Eliminar"); remove.type = "button"; remove.addEventListener("click", () => { state.cart = state.cart.filter((item) => cartIdentity(item) !== cartIdentity(line)); persistCart(); });
        product.append(check, info, remove); tr.append(product); const quantity = el("td"); quantity.dataset.label = "Cantidad"; quantity.append(cartQuantity(line, saved)); tr.append(quantity);
        const unit = el("td", null, formatHnl(line.unit_price_minor)); unit.dataset.label = "Precio unitario"; tr.append(unit); const total = el("td", "line-total", line.line_total_minor === null ? "Incompleto" : formatHnl(line.line_total_minor)); total.dataset.label = "Total"; tr.append(total); body.append(tr);
      }
      table.append(thead, body); section.append(table, el("div", "retailer-subtotal", group.incomplete ? `Subtotal ${retailer.name}: incompleto` : `Subtotal ${retailer.name}: ${formatHnl(group.subtotal_minor)}`)); ui["cart-groups"].append(section);
    }
  }

  async function ensureCartRows() {
    const savedPaths = [...new Set(state.cart.map((line) => line.catalog_partition).filter(Boolean))];
    await loadPartitions(savedPaths.filter((path) => state.files.has(path)));
  }

  function renderRefresh() {
    const updates = detectCartUpdates(state.cart, state.rows); ui["price-refresh"].hidden = !updates.length; if (!updates.length) return; const parts = [];
    for (const [status, singular, plural] of [["price_changed", "precio cambió", "precios cambiaron"], ["unavailable", "oferta ya no está disponible", "ofertas ya no están disponibles"], ["restored", "oferta volvió a estar disponible", "ofertas volvieron a estar disponibles"]]) { const count = updates.filter((item) => item.status === status).length; if (count) parts.push(`${count} ${count === 1 ? singular : plural}.`); }
    ui["price-refresh-message"].textContent = parts.join(" ");
  }

  function scenarioCard(title, total, details) { const card = el("article", "scenario-card"); card.append(el("h4", null, title), el("strong", null, total === null ? "Total incompleto" : formatHnl(total))); for (const detail of details) card.append(el("p", null, detail)); return card; }

  function renderBasketAnalysis() {
    const panel = ui["basket-analysis"], content = ui["basket-analysis-content"]; content.replaceChildren(); panel.hidden = !state.cart.length; if (!state.cart.length) return;
    const analysis = analyzeBasketOptions(state.cart, state.rows);
    if (analysis.status === "REFRESH_REQUIRED") { content.append(el("p", "empty-state", "Actualiza primero los precios para comparar escenarios con el mismo corte.")); return; }
    if (analysis.status === "COMPARISON_BLOCKED") { content.append(el("p", "empty-state", "Los precios guardados no están suficientemente recientes para comparar escenarios.")); return; }
    if (analysis.status !== "AVAILABLE") { content.append(el("p", "empty-state", "Carga los productos guardados para comparar escenarios seguros.")); return; }
    const optimized = analysis.optimized; content.append(scenarioCard("Mejor precio por producto", optimized.total_minor, [`${optimized.covered_count}/${optimized.requested_count} productos cubiertos`, optimized.status === "COMPLETE" ? `Requiere visitar ${optimized.retailer_count} supermercado${optimized.retailer_count === 1 ? "" : "s"}` : `${optimized.missing_count} sin comparación segura; no se imputa cero`, Number.isSafeInteger(optimized.savings_vs_manual_minor) ? `Diferencia frente a tu selección: ${formatHnl(optimized.savings_vs_manual_minor)}` : "Diferencia no calculable"]));
    for (const scenario of analysis.single_retailer) content.append(scenarioCard(`Todo en ${scenario.name}`, scenario.total_minor, [`${scenario.covered_count}/${scenario.requested_count} productos cubiertos`, scenario.status === "COMPLETE" ? "Canasta completa" : `${scenario.missing_count} faltante${scenario.missing_count === 1 ? "" : "s"}; no se imputa cero`]));
  }

  async function exportCart(kind) {
    if (!state.cart.length) return; ui["export-status"].textContent = `Preparando ${kind.toUpperCase()}…`;
    try { const module = await import("./exports.js"), summary = cartSummary(state.cart), date = new Date().toISOString().slice(0, 10); const content = kind === "csv" ? module.buildCartCsv(summary) : module.buildCartPdf(summary, new Date()); downloadBlob(new Blob([content], {type: kind === "csv" ? "text/csv;charset=utf-8" : "application/pdf"}), `mi-compra-${date}.${kind}`); ui["export-status"].textContent = `${kind.toUpperCase()} generado desde tu lista local.`; } catch { ui["export-status"].textContent = `No fue posible generar ${kind.toUpperCase()}.`; }
  }

  ui["category-filter"].addEventListener("change", () => { void changeFilter("category", ui["category-filter"].value); });
  ui["type-filter"].addEventListener("change", () => { void changeFilter("product_type", ui["type-filter"].value); });
  ui["brand-filter"].addEventListener("change", () => { void changeFilter("brand", ui["brand-filter"].value); });
  ui["presentation-filter"].addEventListener("change", () => { void changeFilter("presentation", ui["presentation-filter"].value); });
  let searchTimer; ui["product-search"].addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { void changeFilter("query", ui["product-search"].value); }, 180); });
  ui["clear-filters"].addEventListener("click", () => { state.filter = {category: "", product_type: "", brand: "", presentation: "", query: ""}; state.indexEntries = []; state.visibleRows = []; state.matchingCount = 0; state.displayLimit = RESULT_PAGE_SIZE; state.staging.clear(); renderFilters(); renderMatrix(); });
  ui["batch-add"].addEventListener("click", () => { const batch = prepareBatch(state.cart, state.visibleRows, state.staging); state.cart = addNewLines(state.cart, batch.additions); state.pendingUpdates = batch.conflicts; ui["update-confirmation"].hidden = !batch.conflicts.length; ui["update-message"].textContent = batch.conflicts.length ? `${batch.conflicts.length} producto${batch.conflicts.length === 1 ? " ya está" : "s ya están"} en Mi Compra. Confirma para cambiar cantidad o supermercado.` : ""; for (const line of [...batch.additions, ...batch.unchanged]) state.staging.delete(line.row_id); persistCart(); renderMatrix(); });
  ui["confirm-updates"].addEventListener("click", () => { state.cart = confirmLineUpdates(state.cart, state.pendingUpdates); for (const line of state.pendingUpdates) state.staging.delete(line.row_id); state.pendingUpdates = []; ui["update-confirmation"].hidden = true; persistCart(); renderMatrix(); });
  ui["price-refresh-button"].addEventListener("click", async () => { await ensureCartRows(); state.cart = refreshCartPrices(state.cart, state.rows); persistCart(); });
  ui["export-csv"].addEventListener("click", () => { void exportCart("csv"); }); ui["export-pdf"].addEventListener("click", () => { void exportCart("pdf"); });
  ui["analysis-tab"].addEventListener("click", () => showTab("analysis-panel"));
  ui["shopping-tab"].addEventListener("click", () => showTab("shopping-panel"));
  for (const link of document.querySelectorAll("[data-tab-link]")) link.addEventListener("click", (event) => { event.preventDefault(); showTab(link.dataset.tabLink); });
  for (const button of document.querySelectorAll("[data-open-shopping]")) button.addEventListener("click", () => { showTab("shopping-panel"); ui["category-filter"].focus(); });
  for (const link of document.querySelectorAll('a[href="#mi-compra"]')) link.addEventListener("click", () => { showTab("shopping-panel", {updateHash: false}); setTimeout(() => document.getElementById("mi-compra")?.scrollIntoView(), 0); });

  async function start() {
    renderCart(); showTab(["#compra-inteligente", "#mi-compra"].includes(location.hash) ? "shopping-panel" : "analysis-panel", {updateHash: false}); const manifestUrl = globalThis.RPI_CONSUMER_CATALOG_URL || document.body.dataset.catalogUrl;
    try { const started = performance.now(); state.manifest = await fetchJson(manifestUrl, {manifest: true}); if (!manifestIsCompatible(state.manifest)) throw new Error("catalog_manifest_invalid"); state.baseUrl = new URL(".", manifestUrl); state.files = new Map(state.manifest.files.map((item) => [item.path, item])); state.facets = await fetchJson("facets-sps.json"); if (!facetsAreCompatible(state.facets, state.manifest)) throw new Error("catalog_facets_invalid"); renderFilters(); await ensureCartRows(); renderCart(); renderRefresh(); renderBasketAnalysis(); ui["data-status"].textContent = `${state.manifest.visible_rows.toLocaleString("es-HN")} productos visibles · 5 supermercados · ${Math.round(performance.now() - started)} ms de carga inicial.`;
      try { state.analysis = await fetchJson(state.manifest.analysis_file); if (!analysisIsCompatible(state.analysis, state.manifest)) throw new Error("consumer_analysis_invalid"); renderConsumerAnalysis({summary: ui["analysis-summary"], retailers: ui["analysis-retailers"], opportunities: ui["analysis-opportunities"], categories: ui["analysis-categories"]}, state.analysis); ui["analysis-status"].textContent = "Análisis calculado en Python desde el mismo corte verificado del catálogo."; }
      catch (error) { ui["analysis-status"].dataset.error = error instanceof Error ? error.message : "unknown_error"; ui["analysis-status"].textContent = "El análisis no está disponible. Compra Inteligente y tu lista local siguen funcionando."; }
    }
    catch (error) { ui["data-status"].dataset.error = error instanceof Error ? error.message : "unknown_error"; ui["data-status"].textContent = "No fue posible verificar el catálogo. Tu compra local permanece disponible."; }
  }
  void start();
}

if (typeof document !== "undefined") createApp();
