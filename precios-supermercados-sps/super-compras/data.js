// Datos de Súper Compras: manifest, índice de búsqueda y particiones del catálogo v3.
// Todo es estático y público (rama portfolio-data); el navegador no consulta Turso
// ni sitios de supermercados.

export const RETAILER_NAMES = {
  la_colonia: "La Colonia", colonial: "Colonial", walmart: "Walmart", pricesmart: "PriceSmart",
  comisariato_los_andes: "Los Andes", paiz: "Paiz",
};
export const RETAILER_COLORS = {
  la_colonia: "#C2410C", colonial: "#1D4ED8", walmart: "#0369A1", pricesmart: "#0E6B4F",
  comisariato_los_andes: "#7C3AED", paiz: "#B45309",
};
export const RETAILER_INITIALS = {
  la_colonia: "LC", colonial: "CO", walmart: "WM", pricesmart: "PS", comisariato_los_andes: "LA", paiz: "PZ",
};

export function fold(value) {
  return String(value ?? "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase()
    .replace(/[^a-z0-9]+/g, " ").trim();
}

export function money(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `L ${number.toLocaleString("es-HN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "—";
}

async function sha256Hex(buffer) {
  if (!globalThis.crypto?.subtle) return null;
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

// id público = row_id sin su prefijo ("source-…", "canonical-…").
export function rowKey(rowId) {
  const text = String(rowId ?? "");
  return text.slice(text.indexOf("-") + 1);
}

export class Catalog {
  constructor(manifestUrl) {
    this.manifestUrl = manifestUrl;
    this.base = manifestUrl.replace(/manifest\.json(\?.*)?$/, "");
    this.partitionCache = new Map();
  }

  async fetchJson(relative, { verify = true } = {}) {
    const response = await fetch(this.base + relative, { cache: "no-cache" });
    if (!response.ok) throw new Error(`No se pudo cargar ${relative} (${response.status})`);
    const buffer = await response.arrayBuffer();
    const meta = verify ? this.files.get(relative) : null;
    if (meta) {
      const hash = await sha256Hex(buffer);
      if (hash && hash !== meta.sha256) throw new Error(`Archivo alterado o desactualizado: ${relative}`);
    }
    return JSON.parse(new TextDecoder().decode(buffer));
  }

  async load() {
    const response = await fetch(this.manifestUrl, { cache: "no-cache" });
    if (!response.ok) throw new Error(`No se pudo cargar el catálogo (${response.status})`);
    this.manifest = await response.json();
    if (this.manifest.schema !== "rpi-consumer-catalog-manifest/v3") throw new Error("Versión de catálogo no compatible");
    this.files = new Map((this.manifest.files || []).map((item) => [item.path, item]));
    const searchFile = this.manifest.search_file;
    if (!searchFile || !this.files.has(searchFile)) throw new Error("El catálogo publicado todavía no trae índice de búsqueda");
    const search = await this.fetchJson(searchFile);
    if (search.schema !== "rpi-consumer-search/v1") throw new Error("Índice de búsqueda no compatible");
    const column = Object.fromEntries(search.columns.map((name, index) => [name, index]));
    this.retailers = search.retailers;
    this.partitions = search.partitions;
    this.products = search.rows.map((row) => {
      const product = {
        id: row[column.id], product_name: row[column.product_name], brand: row[column.brand],
        presentation: row[column.presentation], category: row[column.category], product_type: row[column.product_type],
        partition: search.partitions[row[column.partition]], best_price: row[column.best_price],
        unit_amount: row[column.unit_amount], unit_per: row[column.unit_per],
        retailers: (row[column.retailers] || []).map((index) => search.retailers[index]),
        promo: row[column.promo] === 1, other_count: row[column.other_presentations] || 0,
        comparability: row[column.comparability],
      };
      product.text = fold([product.brand, product.product_name, product.presentation, product.product_type].join(" "));
      return product;
    });
    this.byId = new Map(this.products.map((product) => [product.id, product]));
    return this;
  }

  get asOf() { return this.manifest?.as_of; }

  departments() {
    const counts = new Map();
    for (const product of this.products) {
      if (!product.category) continue;
      counts.set(product.category, (counts.get(product.category) || 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([name, count]) => ({ name, count }));
  }

  search({ query = "", category = "", retailer = "", promoOnly = false, sort = "relevance", limit = 60 } = {}) {
    const terms = fold(query).split(" ").filter(Boolean);
    let results = this.products.filter((product) => {
      if (category && product.category !== category) return false;
      if (retailer && !product.retailers.includes(retailer)) return false;
      if (promoOnly && !product.promo) return false;
      if (!product.best_price) return false;
      return terms.every((term) => product.text.includes(term));
    });
    const first = terms[0] || "";
    const wordHit = (value) => (first && ` ${fold(value)} `.includes(` ${first} `) ? 0 : 1);
    const brandHit = (product) => (first && fold(product.brand).startsWith(first) ? 0 : 1);
    const typeHit = (product) => wordHit(product.product_type);
    const nameHit = (product) => wordHit(product.product_name);
    const unitValue = (product) => (product.unit_amount ? Number(product.unit_amount) : Number.POSITIVE_INFINITY);
    const comparators = {
      relevance: (a, b) => Math.min(brandHit(a), typeHit(a)) - Math.min(brandHit(b), typeHit(b))
        || nameHit(a) - nameHit(b) || b.retailers.length - a.retailers.length
        || a.product_name.length - b.product_name.length,
      unit: (a, b) => unitValue(a) - unitValue(b) || Number(a.best_price) - Number(b.best_price),
      price: (a, b) => Number(a.best_price) - Number(b.best_price),
      stores: (a, b) => b.retailers.length - a.retailers.length || Number(a.best_price) - Number(b.best_price),
    };
    results.sort(comparators[sort] || comparators.relevance);
    return { total: results.length, items: results.slice(0, limit) };
  }

  alternatives(product, limit = 6) {
    if (!product.product_type || !product.unit_per) return [];
    const peers = this.products.filter((other) => other.product_type === product.product_type
      && other.unit_per === product.unit_per && Number(other.unit_amount) > 0);
    // Descarta precios por unidad imposibles (tamaños mal publicados, p. ej. "1100 Lt").
    const values = peers.map((other) => Number(other.unit_amount)).sort((a, b) => a - b);
    const median = values[Math.floor(values.length / 2)] || 0;
    return peers
      .filter((other) => other.id !== product.id && fold(other.brand) !== fold(product.brand)
        && Number(other.unit_amount) >= median / 6 && Number(other.unit_amount) <= median * 6)
      .sort((a, b) => Number(a.unit_amount) - Number(b.unit_amount) || b.retailers.length - a.retailers.length)
      .slice(0, limit);
  }

  async row(product) {
    if (!this.partitionCache.has(product.partition)) {
      this.partitionCache.set(product.partition, this.fetchJson(product.partition));
    }
    const document = await this.partitionCache.get(product.partition);
    return document.rows.find((row) => rowKey(row.row_id) === product.id) || null;
  }
}

// Mi compra: lista guardada en este navegador.
const CART_KEY = "super-compras:cart:v1";

export function loadCart() {
  try {
    const value = JSON.parse(localStorage.getItem(CART_KEY) || "[]");
    return Array.isArray(value) ? value.filter((line) => line && typeof line.id === "string") : [];
  } catch {
    return [];
  }
}

export function saveCart(lines) {
  try { localStorage.setItem(CART_KEY, JSON.stringify(lines)); } catch { /* modo privado: sin guardar */ }
}
