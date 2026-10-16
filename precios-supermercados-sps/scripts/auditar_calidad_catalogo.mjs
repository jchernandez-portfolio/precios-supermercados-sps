// Auditoría de calidad de datos de Súper Compras.
//
// Lee el catálogo público ya publicado (las mismas URLs que usa la app), le aplica
// la misma lógica de presentación que ve el usuario (super-compras/data.js) y
// lista lo que está mal o incompleto por ciudad y supermercado. Sólo lee datos
// públicos; no consulta Turso ni los supermercados.
//
//   node scripts/auditar_calidad_catalogo.mjs --out DIR [--previous DIR] [--data DIR]
//
// --data lee el catálogo de una copia local de la rama `portfolio-data` en vez de
// la red (en el workflow, recién sincronizada, sin esperar la caché de raw).
//
// Escribe en DIR: errores_detectados.csv, resumen.md e historial.csv (este último
// se acumula a partir del de --previous). "detectado_desde" conserva la fecha en
// que cada error apareció por primera vez.
import { mkdirSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import {
  Catalog, presentationLabel, RETAILER_NAMES, implausibleSize, unitPriceMedians, unitPriceRatio,
} from "../super-compras/data.js";

export const DATA_BASE =
  "https://raw.githubusercontent.com/jchernandez-portfolio/precios-supermercados-sps/portfolio-data/precios-supermercados-sps/published/rpi/v3/";
export const SITE = "https://jchernandez-portfolio.github.io/Portafolio/precios-supermercados-sps/super-compras/";

export const PROBLEMS = {
  peso_imposible: {
    label: "Peso o volumen imposible", severity: "alta",
    help: "La medida que se muestra no puede ser real (más de 50 kg o 60 L en un producto de súper): el súper escribió mal la medida o un código se leyó como peso.",
  },
  precio_unidad_error: {
    label: "Precio por unidad: probable error", severity: "alta",
    help: "El precio por kg/L es 100 veces mayor o 20 veces menor que el de su tipo de producto: el tamaño o el precio están mal publicados.",
  },
  precio_unidad_revisar: {
    label: "Precio por unidad: revisar", severity: "media",
    help: "El precio por kg/L es entre 20 y 100 veces mayor que el de su tipo: puede ser error o un producto muy pequeño (té en sobres, repuestos, pegamento).",
  },
  medida_mal_escrita: {
    label: "Medida mal escrita por el súper", severity: "media",
    help: "El nombre trae un espacio dentro del número (\"40 0Ml\") y no se puede leer la medida.",
  },
  distinto_entre_ciudades: {
    label: "Distinto entre ciudades", severity: "media",
    help: "El mismo producto del mismo súper sale por libra en una ciudad y no en la otra.",
  },
  sin_presentacion: {
    label: "Sin presentación", severity: "baja",
    help: "El nombre termina en un número sin unidad (\"Ensure 237\"): no se sabe qué mide.",
  },
  corregido_en_web: {
    label: "Corregido en la web", severity: "info",
    help: "El súper publica mal la medida (\"1100 Lt\", \"397 Oz\" que son gramos) y la web la corrige con un patrón seguro; sigue mal en el súper.",
  },
};
const ORDER = Object.keys(PROBLEMS);
const SPACED_MEASURE = /\d+\s+0\s*(?:ml|l|gr|g|kg)\b/i;

function money(value) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number.toFixed(2) : "";
}

// Problemas de un catálogo ya cargado (productos con displayFields aplicado).
export function auditProducts(products, city) {
  const found = [];
  // Lo que la web esconde (hideImplausible) se audita con el dato original.
  const views = products.map((product) => (product.suspect ? { ...product, ...product.hidden } : product));
  const add = (product, problem, detail) => found.push({ product, problem, detail, city });
  const medians = unitPriceMedians(views);
  views.forEach((view, index) => {
    const product = products[index];
    const hidden = product.suspect ? " En la web se esconde el tamaño y el precio por unidad." : "";
    if (product.repaired?.total) {
      add(product, "corregido_en_web", `El súper publica ${presentationLabel({ ...product, presentation: product.repaired.from })}; la web muestra ${presentationLabel(product)} (${product.repaired.reason}).`);
    }
    if (implausibleSize(view)) add(product, "peso_imposible", `Se muestra ${presentationLabel(view)}.${hidden}`);
    if (!view.presentation) add(product, "sin_presentacion", "El nombre termina en un número sin unidad.");
    const spaced = view.presentation === "1 unidad" && String(view.product_name).match(SPACED_MEASURE);
    if (spaced) add(product, "medida_mal_escrita", `El nombre dice "${spaced[0]}".`);
    const ratio = unitPriceRatio(view, medians);
    if (ratio !== null) {
      const reference = Number(view.unit_amount) / ratio;
      const text = `L ${Number(view.unit_amount).toFixed(2)} por ${view.unit_per}; lo normal en ${view.product_type} es L ${reference.toFixed(2)}`;
      if (ratio >= 100 || ratio <= 1 / 20) add(product, "precio_unidad_error", `${text} (${ratio >= 1 ? `${Math.round(ratio)} veces más` : `${Math.round(1 / ratio)} veces menos`}).${hidden}`);
      else if (ratio >= 20) add(product, "precio_unidad_revisar", `${text} (${Math.round(ratio)} veces más).`);
    }
  });
  return found;
}

// Mismo súper y mismo nombre en dos ciudades: por libra en una y no en la otra.
export function auditBetweenCities(first, second) {
  const key = (product) => `${String(product.product_name).trim().toLowerCase()}|${[...product.retailers].sort().join(",")}`;
  const index = new Map(first.products.map((product) => [key(product), product]));
  const found = [];
  for (const product of second.products) {
    const other = index.get(key(product));
    if (!other) continue;
    const a = presentationLabel(other) || "sin presentación";
    const b = presentationLabel(product) || "sin presentación";
    if (a !== b && (a === "por libra" || b === "por libra")) {
      found.push({ product, problem: "distinto_entre_ciudades", city: second.city,
        detail: `En ${first.city.name} sale "${a}" y en ${second.city.name} "${b}".` });
    }
  }
  return found;
}

// Una fila por producto y supermercado, sin duplicados.
export function toRows(found, today, previous = new Map()) {
  const rows = [];
  const seen = new Set();
  for (const { product, problem, detail, city } of found) {
    for (const retailer of product.retailers) {
      const id = `${city.slug}|${retailer}|${product.id}|${problem}`;
      if (seen.has(id)) continue;
      seen.add(id);
      rows.push({
        ciudad: city.name, supermercado: RETAILER_NAMES[retailer] || retailer, problema: PROBLEMS[problem].label,
        gravedad: PROBLEMS[problem].severity, que_esta_mal: detail, producto: product.product_name,
        presentacion_mostrada: presentationLabel(product) || "", desde: money(product.best_price),
        precio_por_unidad: product.unit_amount ? `L ${money(product.unit_amount)} por ${product.unit_per}` : "",
        departamento: product.category || "", tipo: product.product_type || "",
        enlace: `${SITE}?ciudad=${city.slug}#/p/${product.id}`, id: product.id,
        detectado_desde: previous.get(id) || today, _key: id, _order: ORDER.indexOf(problem),
      });
    }
  }
  rows.sort((a, b) => a.ciudad.localeCompare(b.ciudad) || a.supermercado.localeCompare(b.supermercado)
    || a._order - b._order || a.producto.localeCompare(b.producto));
  return rows;
}

export const COLUMNS = ["ciudad", "supermercado", "problema", "gravedad", "que_esta_mal", "producto",
  "presentacion_mostrada", "desde", "precio_por_unidad", "departamento", "tipo", "enlace", "id", "detectado_desde"];

function csvCell(value) {
  const text = String(value ?? "");
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function toCsv(rows, columns = COLUMNS) {
  return [columns.join(","), ...rows.map((row) => columns.map((column) => csvCell(row[column])).join(","))].join("\n") + "\n";
}

export function parseCsv(text) {
  const rows = [];
  let row = [], cell = "", quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    if (quoted) {
      if (char === '"' && text[i + 1] === '"') { cell += '"'; i += 1; }
      else if (char === '"') quoted = false;
      else cell += char;
    } else if (char === '"') quoted = true;
    else if (char === ",") { row.push(cell); cell = ""; }
    else if (char === "\n") { row.push(cell); rows.push(row); row = []; cell = ""; }
    else if (char !== "\r") cell += char;
  }
  if (cell || row.length) { row.push(cell); rows.push(row); }
  const [header, ...body] = rows;
  return header ? body.filter((r) => r.length > 1).map((r) => Object.fromEntries(header.map((h, i) => [h, r[i] ?? ""]))) : [];
}

// Fecha de primera detección de cada error, leída del CSV anterior.
export function previousFirstSeen(csvText, cities) {
  const slugByName = new Map(cities.map((city) => [city.name, city.slug]));
  const retailerByName = new Map(Object.entries(RETAILER_NAMES).map(([id, name]) => [name, id]));
  const problemByLabel = new Map(Object.entries(PROBLEMS).map(([id, problem]) => [problem.label, id]));
  const map = new Map();
  for (const row of parseCsv(csvText)) {
    const key = `${slugByName.get(row.ciudad)}|${retailerByName.get(row.supermercado) || row.supermercado}|${row.id}|${problemByLabel.get(row.problema)}`;
    if (row.detectado_desde) map.set(key, row.detectado_desde);
  }
  return map;
}

export function summaryMarkdown(rows, cities, today) {
  const lines = [
    "# Errores de datos en Súper Compras", "",
    `Actualizado automáticamente el ${today} a partir del catálogo publicado (${cities.map((c) => `${c.name}: ${c.as_of}`).join("; ")}).`, "",
    "- [`errores_detectados.csv`](errores_detectados.csv): todo lo que la revisión automática encuentra hoy, una fila por producto y supermercado, con enlace al producto.",
    "- [`errores_confirmados.csv`](errores_confirmados.csv): errores revisados a mano, de dónde vienen (súper o Súper Compras) y su estado.",
    "- [`historial.csv`](historial.csv): cuántos errores había en cada publicación.", "",
  ];
  for (const city of cities) {
    const own = rows.filter((row) => row.ciudad === city.name);
    const retailers = [...new Set(own.map((row) => row.supermercado))].sort();
    lines.push(`## ${city.name}`, "", `| Problema | ${retailers.join(" | ")} | Total |`, `|---|${retailers.map(() => "---:").join("|")}|---:|`);
    for (const id of ORDER) {
      const label = PROBLEMS[id].label;
      const counts = retailers.map((name) => own.filter((row) => row.supermercado === name && row.problema === label).length);
      const total = counts.reduce((a, b) => a + b, 0);
      if (total) lines.push(`| ${label} | ${counts.join(" | ")} | ${total} |`);
    }
    lines.push("");
  }
  lines.push("## Qué significa cada problema", "");
  for (const id of ORDER) lines.push(`- **${PROBLEMS[id].label}** (gravedad ${PROBLEMS[id].severity}): ${PROBLEMS[id].help}`);
  lines.push("");
  return lines.join("\n");
}

export function historyRows(rows, cities, today) {
  const out = [];
  for (const city of cities) {
    for (const id of ORDER) {
      const count = rows.filter((row) => row.ciudad === city.name && row.problema === PROBLEMS[id].label).length;
      out.push({ fecha: today, ciudad: city.name, catalogo: city.as_of, problema: PROBLEMS[id].label, cantidad: count });
    }
  }
  return out;
}

async function loadCities(base) {
  const index = await (await fetch(`${base}cities.json`, { cache: "no-store" })).json();
  const cities = [];
  for (const city of index.cities) {
    const catalog = new Catalog(base + city.manifest);
    await catalog.load();
    cities.push({ name: city.name, slug: city.slug, as_of: catalog.asOf, catalog, products: catalog.products });
  }
  return cities;
}

// Sirve las URLs del catálogo desde una copia local de la rama portfolio-data.
export function localFetch(dataDir) {
  const root = join(dataDir, "precios-supermercados-sps", "published", "rpi", "v3");
  const original = globalThis.fetch;
  return async (url, options) => {
    const text = String(url);
    if (!text.startsWith(DATA_BASE)) return original(url, options);
    const relative = text.slice(DATA_BASE.length).split("?")[0];
    if (relative.split("/").includes("..")) throw new Error("ruta inválida");
    const bytes = readFileSync(join(root, relative));
    return new Response(bytes, { status: 200 });
  };
}

async function main(argv) {
  const out = argv[argv.indexOf("--out") + 1];
  if (argv.includes("--data")) globalThis.fetch = localFetch(argv[argv.indexOf("--data") + 1]);
  if (!argv.includes("--out") || !out) throw new Error("uso: --out DIR [--previous DIR]");
  const previousDir = argv.includes("--previous") ? argv[argv.indexOf("--previous") + 1] : null;
  const today = new Date().toISOString().slice(0, 10);
  const cities = await loadCities(DATA_BASE);
  const found = cities.flatMap((city) => auditProducts(city.products, city));
  for (let i = 1; i < cities.length; i += 1) {
    found.push(...auditBetweenCities({ ...cities[0], city: cities[0] }, { ...cities[i], city: cities[i] }));
  }
  const read = (name) => (previousDir && existsSync(join(previousDir, name)) ? readFileSync(join(previousDir, name), "utf8") : "");
  const rows = toRows(found, today, previousFirstSeen(read("errores_detectados.csv"), cities));
  mkdirSync(out, { recursive: true });
  writeFileSync(join(out, "errores_detectados.csv"), toCsv(rows));
  writeFileSync(join(out, "resumen.md"), summaryMarkdown(rows, cities, today));
  const history = parseCsv(read("historial.csv")).filter((row) => row.fecha !== today);
  const historyColumns = ["fecha", "ciudad", "catalogo", "problema", "cantidad"];
  writeFileSync(join(out, "historial.csv"), toCsv([...history, ...historyRows(rows, cities, today)], historyColumns));
  console.log(JSON.stringify({ rows: rows.length, cities: cities.map((c) => ({ city: c.slug, as_of: c.as_of, rows: rows.filter((r) => r.ciudad === c.name).length })) }));
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main(process.argv.slice(2)).catch((error) => { console.error(error); process.exit(1); });
}
