// Ilustraciones de producto (sin fotos): forma por tipo de producto y color por marca.
// Devuelve un nodo <svg> construido con createElementNS (nunca HTML en texto).

const SVG_NS = "http://www.w3.org/2000/svg";

const BRAND_COLORS = {
  "pepsi": "#1D4ED8", "coca cola": "#DC2626", "coca-cola": "#DC2626", "fanta": "#EA580C", "sprite": "#15803D",
  "nescafe": "#7C4A2D", "nestle": "#1E3A8A", "dos pinos": "#1D4ED8", "sula": "#0E7490", "leyde": "#0369A1",
  "maggi": "#DC2626", "kelloggs": "#B91C1C", "kellogg's": "#B91C1C", "colgate": "#DC2626", "dove": "#1E3A8A",
  "great value": "#1D4ED8", "member's selection": "#0E6B4F", "badia": "#B45309", "don julio": "#B91C1C",
  "mazola": "#B45309", "xedex": "#0F766E", "ariel": "#047857", "suavitel": "#7C3AED", "gatorade": "#EA580C",
  "yes": "#DB2777", "yoplait": "#BE185D", "huggies": "#DC2626", "pampers": "#0D9488", "purina": "#DC2626",
};
const PALETTE = ["#1D4ED8", "#B91C1C", "#0F766E", "#B45309", "#7C3AED", "#BE185D", "#0369A1", "#4D7C0F", "#9A3412", "#334155"];

function fold(value) {
  return String(value || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

export function brandColor(brand, name) {
  const key = fold(brand).trim();
  if (key && BRAND_COLORS[key]) return BRAND_COLORS[key];
  const source = key || fold(name).split(" ")[0] || "x";
  let hash = 0;
  for (const char of source) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return PALETTE[hash % PALETTE.length];
}

const KIND_RULES = [
  ["can", /\blata\b|\bcan\b|enlatad|\batun\b|sardina|\bspam\b/],
  ["tube", /pasta dental|crema dental|dentifric/],
  ["carton", /\bleche\b(?!.*polvo)|jugo.*(carton|tetra)|\bnectar\b/],
  ["bottle", /gaseosa|refresco|\bagua\b|\bjugo\b|bebida|aceite|\bvino\b|licor|cerveza|whisky|\bron\b|vodka|salsa|ketchup|vinagre|shampoo|champu|acondicionador|cloro|desinfectante|suavizante|detergente liquido|lavaplatos|jabon liquido|enjuague|energizante|hidratante|sangria/],
  ["jar", /cafe instant|mayonesa|mermelada|jalea|\bmiel\b|crema de mani|nutella|especia|condimento|sazon|consome|pimienta|canela|yogur|queso crema|mantequilla|margarina|crema corporal|desodorante/],
  ["box", /cereal|galleta|\bte\b|infusion|panal|toallitas|servilleta|panuelo|\bcaja\b|leche en polvo|formula|gelatina|\bflan\b|pudin|juguete|electr/],
  ["bag", /arroz|frijol|azucar|harina|detergente|\bpolvo\b|\bpapas\b|chips|boquita|fritura|snack|tajad|\bpasta\b|espagueti|fideo|\bcafe\b|alimento para (perro|gato)|\bperro|\bgato|\bbolsa\b|\bpan\b|tortilla|\bmani\b|nuez|semilla|\bcarne|\bpollo\b|\bres\b|cerdo|embutido|chorizo|salchicha|jamon|fruta|verdura|congelad|quinoa|avena|lenteja/],
];

export function illustrationKind(row) {
  const text = fold([row.product_type, row.product_name, row.category].join(" "));
  for (const [kind, pattern] of KIND_RULES) if (pattern.test(text)) return kind;
  return "pack";
}

function el(tag, attrs) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, String(value));
  return node;
}

function shortLabel(row) {
  const brand = String(row.brand || "").trim();
  const base = brand || String(row.product_name || "").split(/\s+/).slice(0, 2).join(" ");
  return base.toUpperCase().slice(0, 12);
}

const SHAPES = {
  bottle: (c) => [
    el("path", { d: "M44 6h12v14c0 6 12 10 12 28v80a8 8 0 0 1-8 8H40a8 8 0 0 1-8-8V48c0-18 12-22 12-28z", fill: c }),
    el("rect", { x: 44, y: 6, width: 12, height: 7, rx: 2, fill: "#0F172A", opacity: 0.55 }),
    el("rect", { x: 32, y: 66, width: 36, height: 30, fill: "#FFFFFF", opacity: 0.94 }),
  ],
  can: (c) => [
    el("rect", { x: 28, y: 22, width: 44, height: 100, rx: 9, fill: c }),
    el("rect", { x: 31, y: 18, width: 38, height: 8, rx: 3, fill: "#94A3B8" }),
    el("rect", { x: 28, y: 60, width: 44, height: 30, fill: "#FFFFFF", opacity: 0.94 }),
  ],
  carton: (c) => [
    el("path", { d: "M30 34 40 18h20l10 16v88a4 4 0 0 1-4 4H34a4 4 0 0 1-4-4z", fill: c }),
    el("rect", { x: 46, y: 10, width: 8, height: 9, rx: 2, fill: "#F8FAFC" }),
    el("rect", { x: 30, y: 62, width: 40, height: 30, fill: "#FFFFFF", opacity: 0.94 }),
  ],
  jar: (c) => [
    el("rect", { x: 30, y: 32, width: 40, height: 14, rx: 4, fill: "#1E293B", opacity: 0.8 }),
    el("rect", { x: 24, y: 44, width: 52, height: 78, rx: 12, fill: c }),
    el("rect", { x: 24, y: 66, width: 52, height: 30, fill: "#FFFFFF", opacity: 0.94 }),
  ],
  box: (c) => [
    el("rect", { x: 22, y: 20, width: 56, height: 104, rx: 5, fill: c }),
    el("rect", { x: 22, y: 20, width: 56, height: 12, fill: "#000000", opacity: 0.12 }),
    el("rect", { x: 22, y: 62, width: 56, height: 30, fill: "#FFFFFF", opacity: 0.94 }),
  ],
  bag: (c) => [
    el("path", { d: "M24 24h52l4 96a4 4 0 0 1-4 4H24a4 4 0 0 1-4-4z", fill: c }),
    el("rect", { x: 20, y: 16, width: 60, height: 12, rx: 3, fill: c, opacity: 0.75 }),
    el("rect", { x: 26, y: 62, width: 48, height: 30, rx: 4, fill: "#FFFFFF", opacity: 0.94 }),
  ],
  tube: (c) => [
    el("path", { d: "M30 20h40l-6 92H36z", fill: c }),
    el("rect", { x: 42, y: 112, width: 16, height: 12, rx: 2, fill: "#F8FAFC" }),
    el("rect", { x: 32, y: 52, width: 36, height: 28, fill: "#FFFFFF", opacity: 0.94 }),
  ],
  pack: (c) => [
    el("rect", { x: 18, y: 30, width: 64, height: 90, rx: 14, fill: c }),
    el("rect", { x: 18, y: 62, width: 64, height: 30, fill: "#FFFFFF", opacity: 0.94 }),
  ],
};

const LABEL_Y = { bottle: 84, can: 78, carton: 80, jar: 84, box: 80, bag: 80, tube: 69, pack: 80 };

export function productIllustration(row, { size = 120, decorative = true } = {}) {
  const kind = illustrationKind(row);
  const color = brandColor(row.brand, row.product_name);
  const svg = el("svg", { viewBox: "0 0 100 140", width: Math.round(size * 0.72), height: size, class: "illustration" });
  if (decorative) svg.setAttribute("aria-hidden", "true");
  else {
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", `Ilustración de ${row.product_name}`);
  }
  for (const shape of SHAPES[kind](color)) svg.append(shape);
  const label = el("text", {
    x: 50, y: LABEL_Y[kind], "text-anchor": "middle", "font-size": 9, "font-weight": 800,
    "font-family": "Figtree, system-ui, sans-serif", fill: color,
  });
  label.textContent = shortLabel(row);
  svg.append(label);
  return svg;
}
