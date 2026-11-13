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

// Sucursales: sólo se nombran cuando una cadena tiene más de una en la ciudad.
export const LOCATION_NAMES = {
  walmart_tgu_ffaa: "FF.AA.", walmart_tgu_el_sauce: "El Sauce",
  paiz_tgu_multiplaza: "Multiplaza", paiz_tgu_proceres: "Próceres",
};

export function storeLabel(offer, offers = []) {
  const name = RETAILER_NAMES[offer.supermarket_id] || offer.supermarket_id;
  const siblings = new Set(offers.filter((other) => other.supermarket_id === offer.supermarket_id).map((other) => other.location_id));
  const branch = LOCATION_NAMES[offer.location_id];
  return siblings.size > 1 && branch ? `${name} ${branch}` : name;
}

export function fold(value) {
  return String(value ?? "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase()
    .replace(/[^a-z0-9]+/g, " ").trim();
}

// "1 artículo", "2 artículos", "1,234 productos": número con separador local y plural.
export function countText(value, singular, plural = `${singular}s`) {
  const number = Number(value) || 0;
  return `${number.toLocaleString("es-HN")} ${number === 1 ? singular : plural}`;
}

// "La Colonia, Walmart y Paiz": lista en español con "y" antes del último.
export function listText(items) {
  const names = items.filter(Boolean);
  return names.length > 1 ? `${names.slice(0, -1).join(", ")} y ${names.at(-1)}` : names.join("");
}

export function money(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `L ${number.toLocaleString("es-HN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "—";
}

// Presentación legible y comparable. Un peso que viene de onzas o libras
// ("453.59 g" = 16 oz) se muestra como en la etiqueta, con los gramos al lado:
// "16 oz (454 g)"; desde 2 lb enteras, "2 lb (907 g)". Los pesos métricos redondos
// (500 g, 1 kg) quedan igual y los gramos con decimales largos se redondean.
const GRAMS_PER_OZ = 28.349523125;

function gramsText(grams) {
  if (grams >= 1000) return `${Number((grams / 1000).toFixed(2))} kg`;
  return grams >= 100 ? `${Math.round(grams)} g` : `${Number(grams.toFixed(1))} g`;
}

// Medias onzas sólo si el peso vino convertido (gramos con decimales): "440 g" es
// métrico, no 15.5 oz.
function imperialText(grams, converted) {
  const ounces = grams / GRAMS_PER_OZ;
  const half = converted ? Math.round(ounces * 2) / 2 : Math.round(ounces);
  if (half < 1 || Math.abs(ounces - half) / half > 0.003) return null;
  const pounds = half / 16;
  if (pounds >= 2 && Number.isInteger(pounds * 2)) return `${pounds} lb`;
  return `${Number(half.toFixed(1))} oz`;
}

const ML_PER_FL_OZ = 29.5735295625;
const ML_PER_GAL = 3785.411784;

function litersText(millis) {
  return millis >= 1000 ? `${Number((millis / 1000).toFixed(2))} L` : `${Math.round(millis)} ml`;
}

export function presentationLabel(product) {
  const text = String(product?.presentation ?? "").trim();
  if (/^1\s+unidades$/i.test(text)) return "1 unidad";
  // Medidas en galones, onzas líquidas o libras: con su equivalente métrico al lado.
  const liquid = text.match(/^(\d+(?:\.\d+)?)\s*(fl oz|gal)$/i);
  if (liquid) {
    const value = Number(liquid[1]);
    return `${value} ${liquid[2].toLowerCase()} (${litersText(value * (/gal/i.test(liquid[2]) ? ML_PER_GAL : ML_PER_FL_OZ))})`;
  }
  const pounds = text.match(/^(\d+(?:\.\d+)?)\s*lb$/i);
  if (pounds) return `${Number(pounds[1])} lb (${gramsText(Number(pounds[1]) * 453.59237)})`;
  const match = text.match(/^(\d+(?:\.\d+)?)\s*(g|kg|oz)$/i);
  if (!match) return text;
  const value = Number(match[1]);
  const unit = match[2].toLowerCase();
  if (unit === "oz") {
    const perMass = /^(100 g|kg)$/.test(String(product?.unit_per ?? ""));
    return perMass ? `${Number(value.toFixed(2))} oz (${gramsText(value * GRAMS_PER_OZ)})` : text;
  }
  const grams = unit === "kg" ? value * 1000 : value;
  const metricRound = grams % 50 === 0 || (unit === "kg" && (value * 10) % 1 === 0);
  const imperial = metricRound ? null : imperialText(grams, match[1].includes("."));
  if (imperial) return `${imperial} (${gramsText(grams)})`;
  return match[1].includes(".") && match[1].split(".")[1].length > 1 ? gramsText(grams) : text;
}

// Frutas y verduras: cada súper usa un tipo distinto (Frutas, Verduras, Frutas y
// verduras) y a veces su propio nombre como marca; se muestran igual.
const PRODUCE_TYPES = new Set(["frutas", "verduras", "frutas y verduras", "hierbas y montes"]);
const PRODUCE_LABEL = "Frutas y verduras";
const RETAILER_BRANDS = new Set(Object.values(RETAILER_NAMES).map((name) => fold(name)));
// Unidad o libra al final del nombre ("Aguacate indio und", "Aguacate Liso Un",
// "Aguacate hass libra") cuando el súper no publicó presentación.
const UNIT_TAIL = /\s+(?:x\s*)?(?:und|unds|unid|un|u|unidad|c\/u)\.?$/i;
const POUND_TAIL = /\s+(?:x\s*)?(?:lb|lbs|libra)\.?$/i;
const POUND_WORD = /(?:^|\s)x\s*(?:lb|lbs|libra)(?:\s|$)/i;

// Tamaño escrito en el nombre cuando el súper no publicó presentación
// ("ISOMIL 2 Plus 800 Grms", "MYTHOZ Café 12 onz", "Kelloggs ... 8pk",
// "Pack Nectar 8Unx200Ml", "KG Hummus"). Sólo se usa para mostrar.
const NAME_UNITS = [
  [/^(?:kg|kgs|k|kilo|kilos)$/i, "kg"], [/^(?:g|gr|grs|grx|grm|grms|gramos?)$/i, "g"],
  [/^(?:ml|mls)$/i, "ml"], [/^(?:l|lt|lts|litro|litros)$/i, "L"],
  [/^(?:gal|gl|gal[oó]n|galones)$/i, "gal"], [/^(?:fl\s*oz|floz)$/i, "fl oz"], [/^(?:oz|onz|onzas?|z)$/i, "oz"], [/^(?:lb|lbs|lbr|libras?)$/i, "lb"],
];
// "240 - g", "50-ml" y "1Gal" también; "20w50" es el grado de un aceite, no una medida.
const MEASURE = /(?<![\d.,])(?<!\dw)(\d+(?:[.,]\d+)?)\s*(?:-\s*)?(gal[oó]n|galones|gal|gl|kgs?|kilos?|k|grms?|grx|grs?|gramos?|g|mls?|lts?|litros?|l|fl\s*oz|floz|onzas?|onz|oz|z|lbs?|lbr|libras?)(?![a-z])/gi;
const MULTIPACK = /(\d+)\s*(?:un|u|und|unds|uds)?\s*x\s*(\d+(?:[.,]\d+)?)\s*(ml|g|gr|oz|l|lt)(?![a-z])/i;
const PACK = /(\d+)\s*(?:pk|pack|uds|unds|und)(?![a-z])/i;
// Conteos (GS1: contenido neto en piezas): "18ct", "20 Ct", "15ea", "12 count". Con
// un peso al lado ("100 ct 40.7 oz") manda el peso, que es el total del paquete.
const COUNT = /(\d+)\s*(?:ct|cts|count|ea)(?![a-z])/i;
// "piezas" en un juego (sábanas, ollas) es un solo artículo: en comida son unidades
// con precio por unidad; en lo demás se muestra "4 piezas" sin precio por pieza.
const PIECES = /(\d+)\s*(?:pcs|pz|pzs|pzas|pza|piezas|pieces)(?![a-z])/i;
// Onzas cortadas al final del nombre (Los Andes): "Organic oatmeal 8 o", "12.8 o".
const CUT_OUNCES = /\s(\d+(?:[.,]\d+)?)\s*o$/i;
// Termina en un número sin unidad ("Ensure 237"): no se inventa la unidad.
const BARE_NUMBER = /\s\d+(?:[.,]\d+)?\s*$/;
const N_IN_ONE = /\sen\s+\d+\s*$/i;
// Aerosoles (aceite en spray, desodorante): la etiqueta dice onzas de peso.
const AEROSOL = /(?<![a-z])(?:spray|aerosol|atomizador)(?![a-z])/i;
const FOOD_CATEGORIES = new Set(["alimentos", "bebidas y tabaco"]);

function unitOf(token) {
  const found = NAME_UNITS.find(([pattern]) => pattern.test(token.replace(/\s+/g, " ").trim()));
  return found ? found[1] : null;
}

export function sizeFromName(name, category = "") {
  // "Desinfectante 40 0Ml", "Lysol 34 0gr": el súper metió un espacio en el número.
  const text = String(name ?? "").replace(/(\d+)\s+0\s*(ml|gr|g)(?![a-z])/gi, (_, digits, unit) => `${digits}0 ${unit}`);
  const multi = text.match(MULTIPACK);
  if (multi) {
    const unit = unitOf(multi[3]);
    if (unit) return { presentation: `${Number(multi[1])} × ${multi[2].replace(",", ".")} ${unit}` };
  }
  if (/^kg\s/i.test(text)) return { presentation: "por kg", per: "kg" };
  // "Ayote Sazon Libra ... - Precio Por Unidad": manda lo que aclara el súper.
  if (/precio\s+por\s+unidad/i.test(text)) return { presentation: "1 unidad", per: "unidad" };
  const fraction = text.match(/(?:(\d+)\s*\/\s*(\d+)|\b(medi[ao]))\s*(?:lb|lbs|libras?)(?![a-z])/i);
  if (fraction) {
    const pounds = fraction[3] ? 0.5 : Number(fraction[1]) / Number(fraction[2]);
    // Como el resto de pesos de libras: en gramos, que la app muestra "8 oz (227 g)".
    if (pounds > 0 && pounds < 1) {
      const grams = Number((pounds * 453.59237).toFixed(2));
      return { presentation: `${grams} g`, value: grams, unit: "g" };
    }
  }
  const measures = [...text.matchAll(MEASURE)].filter((match) =>
    (!/^\d+(?:[.,]\d+)?\s*l$/i.test(match[0]) || /\s/.test(match[0]))
    // "4K" es resolución, no kilos: "k" sola sólo con decimales ("1.92k").
    && !(/^k$/i.test(match[2]) && !/[.,]/.test(match[1])));
  const cut = text.match(CUT_OUNCES);
  if (cut && !measures.length) measures.push([cut[0], cut[1], "oz"]);
  const last = measures.at(-1);
  const food = FOOD_CATEGORIES.has(fold(category));
  const pack = text.match(PACK) || (food ? text.match(PIECES) : null);
  if (last) {
    const unit = unitOf(last[2]);
    const value = Number(last[1].replace(",", "."));
    const count = pack ? Number(pack[1]) : 1;
    if (unit && value > 0 && count > 1) {
      return { presentation: `${count} × ${value} ${unit}`, value: value * count, unit };
    }
    if (unit && value > 0) return { presentation: `${value} ${unit}`, value, unit };
  }
  const counted = pack || text.match(COUNT);
  if (counted && Number(counted[1]) > 1) return { presentation: `${Number(counted[1])} unidades`, count: Number(counted[1]) };
  const pieces = food ? null : text.match(PIECES);
  if (pieces && Number(pieces[1]) > 1) return { presentation: `${Number(pieces[1])} piezas` };
  if (/(?:^|[\s(-])(?:lb|lbs|libras?)(?:[\s)]|$)|por libra/i.test(text)) return { presentation: "por libra", per: "libra" };
  return null;
}

// Precio por unidad en la misma base que usan los demás productos de ese tipo
// ("por 100 g" en embutidos, "por kg" en carnes), para poder compararlos.
function unitFromSize(size, price, basis) {
  const amount = Number(price);
  if (!size || !(amount > 0)) return null;
  if (size.per) return { amount: price, per: size.per };
  const grams = { g: 1, kg: 1000, lb: 453.59237, oz: GRAMS_PER_OZ }[size.unit];
  const millis = { ml: 1, L: 1000, "fl oz": ML_PER_FL_OZ, gal: ML_PER_GAL }[size.unit];
  if (grams) {
    const per = basis === "100 g" ? 100 : 1000;
    return { amount: (amount / (size.value * grams) * per).toFixed(2), per: per === 100 ? "100 g" : "kg" };
  }
  if (millis) {
    const per = basis === "100 ml" ? 100 : 1000;
    return { amount: (amount / (size.value * millis) * per).toFixed(2), per: per === 100 ? "100 ml" : "L" };
  }
  if (size.count) return { amount: (amount / size.count).toFixed(2), per: "unidad" };
  return null;
}

export function isProduce(product) {
  return PRODUCE_TYPES.has(fold(product?.product_type));
}

// Nombre, presentación y marca para mostrar; la búsqueda sigue usando el nombre del súper.
export function displayFields(product, basis = null) {
  let name = String(product.product_name ?? "").trim();
  let presentation = product.presentation;
  let unitAmount = product.unit_amount;
  let unitPer = product.unit_per;
  if (!presentation && UNIT_TAIL.test(name)) {
    name = name.replace(UNIT_TAIL, "");
    presentation = "1 unidad";
  } else if (!presentation) {
    const size = sizeFromName(name, product.category);
    if (size?.presentation === "por libra" && POUND_TAIL.test(name)) name = name.replace(POUND_TAIL, "");
    if (!size && POUND_WORD.test(name)) presentation = "por libra";
    // "oz" en el nombre: onzas de peso, salvo en tipos que se miden en volumen
    // (vainilla, bebidas), donde son onzas líquidas (GS1 distingue ONZ y OZA).
    if (size?.unit === "oz" && /^(?:100 ml|L)$/.test(basis ?? "") && !AEROSOL.test(name)) {
      Object.assign(size, { unit: "fl oz", presentation: `${size.value} fl oz` });
    }
    if (size) {
      presentation = size.presentation;
      const unit = unitAmount ? null : unitFromSize(size, product.best_price, basis);
      if (unit) [unitAmount, unitPer] = [unit.amount, unit.per];
    }
  }
  // Onzas publicadas por el súper sin base de precio ("10 oz" en Los Andes): igual que
  // las del nombre, a gramos o mililitros según el tipo, para comparar precio.
  const ounces = !unitAmount && String(presentation ?? "").match(/^(\d+(?:\.\d+)?)\s*(oz|fl oz)$/i);
  if (ounces) {
    const liquid = /fl/i.test(ounces[2]) || (/^(?:100 ml|L)$/.test(basis ?? "") && !AEROSOL.test(name));
    const size = { value: Number(ounces[1]), unit: liquid ? "fl oz" : "oz" };
    presentation = `${size.value} ${size.unit}`;
    const unit = unitFromSize(size, product.best_price, basis);
    if (unit) [unitAmount, unitPer] = [unit.amount, unit.per];
  }
  // Regla GS1 de contenido neto: todo artículo lleva cantidad y unidad. Lo que no
  // trae peso, volumen ni conteo se vende por pieza ("1 unidad"); un número suelto
  // al final ("Ensure 237") queda sin presentación porque no se sabe qué mide.
  if (!presentation && !unitAmount && name && (!BARE_NUMBER.test(name) || N_IN_ONE.test(name))) presentation = "1 unidad";
  // Sin peso publicado, el precio por unidad o por libra es el mismo precio.
  if (!unitAmount && product.best_price && (presentation === "1 unidad" || presentation === "por libra")) {
    unitAmount = product.best_price;
    unitPer = presentation === "1 unidad" ? "unidad" : "libra";
  }
  const produce = isProduce(product);
  const brand = produce && RETAILER_BRANDS.has(fold(product.brand)) ? null : product.brand;
  const brandLabel = brand || (produce ? PRODUCE_LABEL : product.product_type) || "Producto";
  return {
    display_name: name || product.product_name, presentation, brand, brand_label: brandLabel,
    unit_amount: unitAmount, unit_per: unitPer,
  };
}

// Red de seguridad: un tamaño imposible (más de 50 kg o 60 L) o un precio por
// kg/L 100 veces mayor o 20 veces menor que el normal de su tipo es un dato mal
// publicado. La tarjeta muestra sólo el precio; el dato queda en `hidden` para la
// auditoría de calidad (scripts/auditar_calidad_catalogo.mjs).
const HEAVY_OK = /(m[aá]quina|estaci[oó]n|banco|pesa|mancuerna|disco|saco|cemento|alimento para|concentrado|purina|dog chow|cat chow)/i;

export function implausibleSize(product) {
  const size = String(product.presentation ?? "").match(/^(\d+(?:\.\d+)?)\s*(kg|g|L|ml)$/);
  if (!size) return false;
  const value = Number(size[1]);
  const kg = size[2] === "kg" ? value : size[2] === "g" ? value / 1000 : null;
  const liters = size[2] === "L" ? value : size[2] === "ml" ? value / 1000 : null;
  return (kg !== null && kg > 50 && !HEAVY_OK.test(product.product_name ?? "")) || (liters !== null && liters > 60);
}

export function unitPriceMedians(products) {
  const groups = new Map();
  for (const product of products) {
    if (!product.unit_amount || !product.product_type || product.unit_per === "unidad") continue;
    const key = `${product.product_type}|${product.unit_per}`;
    (groups.get(key) || groups.set(key, []).get(key)).push(Number(product.unit_amount));
  }
  const medians = new Map();
  for (const [key, values] of groups) {
    if (values.length < 8) continue;
    values.sort((a, b) => a - b);
    medians.set(key, values[Math.floor(values.length / 2)]);
  }
  return medians;
}

export function unitPriceRatio(product, medians) {
  const reference = medians.get(`${product.product_type}|${product.unit_per}`);
  const amount = Number(product.unit_amount);
  return reference && amount > 0 && product.unit_per !== "unidad" ? amount / reference : null;
}

// Errores del súper con patrón claro: se corrige la medida sólo si el resultado
// queda en el rango normal de precio por kg/L de su tipo. Lo demás se esconde.
// En electrodomésticos, hogar, deportes, ropa y juguetes los litros o kilos son
// capacidad (refrigeradora 409 L, silla para 136 kg), no contenido: va por unidad.
const CAPACITY_DEPARTMENTS = new Set(["electronica y electrodomesticos", "ropa calzado y accesorios"]);
// En hogar, deportes y juguetes sólo en tipos de recipientes, aparatos o equipo; las
// velas, el carbón, el pegamento o la plastilina sí traen contenido neto.
const CAPACITY_TYPES = new Set(["recipientes termos y botellas", "vajillas vasos y cubiertos", "organizacion y almacenamiento",
  "utensilios de cocina y reposteria", "ollas y sartenes", "cocina", "fitness y ejercicio", "articulos deportivos",
  "natacion y piscinas", "juegos de exterior"]);
const LIQUID_DEPARTMENTS = new Set(["bebidas y tabaco", "limpieza"]);
const MASS_PER = new Set(["100 g", "kg"]);
const VOLUME_PER = new Set(["100 ml", "L"]);

function parsedSize(presentation) {
  const text = String(presentation ?? "");
  const multi = text.match(/^(\d+)\s*×\s*(\d+(?:\.\d+)?)\s*(kg|g|L|ml)$/);
  const single = text.match(/^(\d+(?:\.\d+)?)\s*(kg|g|L|ml)$/);
  const [count, value, unit] = multi ? [Number(multi[1]), Number(multi[2]), multi[3]] : single ? [1, Number(single[1]), single[2]] : [];
  if (!unit) return null;
  const base = unit === "kg" || unit === "g" ? "g" : "ml";
  const each = unit === "kg" || unit === "L" ? value * 1000 : value;
  return { base, count, each, total: each * count };
}

function totalText(base, total) {
  return base === "g" ? gramsText(total) : litersText(total);
}

function sizedUnit(product, base, total) {
  const allowed = base === "g" ? MASS_PER : VOLUME_PER;
  const per = allowed.has(product.unit_per) ? product.unit_per : base === "g" ? "kg" : "L";
  const factor = per.startsWith("100") ? 100 : 1000;
  return { per, factor, amount: (Number(product.best_price) / total * factor).toFixed(2) };
}

function repairCandidates(product, size, ratio) {
  const name = String(product.product_name ?? "");
  const out = [];
  const add = (reason, base, total, label) => out.push({ reason, base, total, label: label ?? totalText(base, total) });
  const spaced = name.match(/(?:^|\s)(\d)\s+(\d{2})\s*(?:lt|l)(?![a-z])/i);
  if (spaced) add("decimal escrito con espacio", "ml", Number(`${spaced[1]}.${spaced[2]}`) * 1000);
  const ounces = name.match(/(\d+(?:\.\d+)?)\s*(?:oz|onz)(?![a-z])/i);
  if (size.base === "g" && ounces && Number(ounces[1]) > 100) add("gramos escritos como onzas", "g", Number(ounces[1]));
  if (size.count > 1 && ratio !== null && ratio <= 1 / 20) add("el total es el del paquete, no por unidad", size.base, size.each);
  // Sólo con un tamaño imposible: un precio bajo puede venir de una mediana del tipo
  // sesgada (cosméticos pequeños), no de un error de unidad.
  const tooBig = size.base === "g" ? size.total > 50000 : size.total > 60000;
  if (tooBig) add(size.base === "g" ? "gramos escritos como kilos" : "mililitros escritos como litros", size.base, size.total / 1000);
  if (size.base === "ml" && size.total < 20 && ratio !== null && ratio >= 100 && LIQUID_DEPARTMENTS.has(fold(product.category))) {
    add("litros escritos como mililitros", "ml", size.total * 1000);
  }
  const pack = name.match(/(\d+)\s*(?:sobres|und|uds|unidades|ct)(?![a-z])/i);
  if (size.base === "g" && size.count === 1 && size.total < 10 && pack && ratio !== null && ratio >= 100) {
    const count = Number(pack[1]);
    add("peso de cada sobre, no del paquete", "g", size.total * count, `${count} × ${totalText("g", size.total)}`);
  }
  return out;
}

export function hideImplausible(products, medians = null) {
  medians = medians || unitPriceMedians(products);
  for (const product of products) {
    const size = parsedSize(product.presentation);
    if (size && (CAPACITY_DEPARTMENTS.has(fold(product.category)) || CAPACITY_TYPES.has(fold(product.product_type)))) {
      product.repaired = { from: product.presentation, reason: "capacidad, no contenido" };
      Object.assign(product, { presentation: "1 unidad", unit_amount: product.best_price, unit_per: "unidad" });
      continue;
    }
    const ratio = unitPriceRatio(product, medians);
    if (!implausibleSize(product) && !(ratio !== null && (ratio >= 100 || ratio <= 1 / 20))) continue;
    const reference = ratio !== null ? Number(product.unit_amount) / ratio : null;
    const fixed = size && repairCandidates(product, size, ratio).find((candidate) => {
      const unit = sizedUnit(product, candidate.base, candidate.total);
      if (implausibleSize({ ...product, presentation: candidate.label })) return false;
      const normal = reference && product.unit_per === unit.per ? reference : null;
      if (normal === null) return false;
      const next = Number(unit.amount) / normal;
      return next > 1 / 20 && next < 20;
    });
    if (fixed) {
      const unit = sizedUnit(product, fixed.base, fixed.total);
      product.repaired = { from: product.presentation, reason: fixed.reason, base: fixed.base, total: fixed.total, factor: unit.factor };
      Object.assign(product, { presentation: fixed.label, unit_amount: unit.amount, unit_per: unit.per });
      continue;
    }
    product.hidden = { presentation: product.presentation, unit_amount: product.unit_amount, unit_per: product.unit_per };
    product.suspect = true;
    product.presentation = null;
    product.unit_amount = null;
    product.unit_per = null;
  }
  return products;
}

// Precio por unidad de una oferta cuando la web corrigió la medida del súper.
export function repairedUnitPrice(product, price) {
  const repair = product?.repaired;
  if (!repair?.total || !(Number(price) > 0)) return null;
  return { amount: (Number(price) / repair.total * repair.factor).toFixed(2), per: product.unit_per };
}

// Tipos que no dicen qué es el producto: ahí decide el nombre.
const GENERIC_TYPES = new Set(["", "abarrotes", "otros", "varios", "comidas listas y congeladas", "congelados y comidas preparadas"]);

// Palabras con su raíz simple (huevos → huevo, panes → pan) para comparar búsquedas.
function stem(word) {
  if (word.length > 4 && word.endsWith("es") && !word.endsWith("ses")) return word.slice(0, -2);
  if (word.length > 3 && word.endsWith("s")) return word.slice(0, -1);
  return word;
}

function hasWord(text, term) {
  const target = stem(term);
  return fold(text).split(" ").some((word) => word && stem(word) === target);
}

async function sha256Hex(buffer) {
  if (!globalThis.crypto?.subtle) return null;
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

// Fotos v2: WebP de 800 y 320 px en el bucket público de Cloudflare R2. El nombre
// lleva la huella del contenido (nunca cambia: caché de un año).
export const PHOTO_BASE_V2 = "https://pub-0dcb2204775c484dbe6681e659eee236.r2.dev/fotos/v2/";
// Fotos v1 (WebP 400 px) en la rama product-images, mientras termina la migración.
export const PHOTO_BASE = "https://raw.githubusercontent.com/jchernandez-portfolio/precios-supermercados-sps/product-images/img/";
export const PHOTO_SIZES = [320, 800];

// photo: huella v2 ("a1b2c3d4e5"), número de versión v1 o 0 (sin foto).
export function photoUrl(product, size = 320) {
  const id = String(product?.id ?? "");
  if (!/^[0-9a-z_-]{3,}$/.test(id)) return null;
  const photo = product?.photo;
  if (typeof photo === "string" && /^[0-9a-f]{10}$/.test(photo)) {
    const side = PHOTO_SIZES.includes(size) ? size : 320;
    return `${PHOTO_BASE_V2}${side}/${id.slice(0, 2)}/${id}-${photo}.webp`;
  }
  const version = Number(photo) || 0;
  if (!version) return null;
  return `${PHOTO_BASE}${id.slice(0, 2)}/${id}.webp?v=${version}`;
}

// srcset con los dos tamaños (sólo v2); null si la foto es v1 o no hay.
export function photoSrcset(product) {
  if (typeof product?.photo !== "string") return null;
  const urls = PHOTO_SIZES.map((size) => photoUrl(product, size));
  return urls.every(Boolean) ? urls.map((url, index) => `${url} ${PHOTO_SIZES[index]}w`).join(", ") : null;
}

function photoValue(value) {
  if (typeof value === "string" && /^[0-9a-f]{10}$/.test(value)) return value;
  return Number(value) || 0;
}

// Fecha "AAAA-MM-DD" en Honduras (sin horario de verano: UTC-6), o "" si no es fecha.
export function hondurasDay(value) {
  const time = Date.parse(String(value ?? ""));
  return Number.isFinite(time) ? new Date(time - 6 * 3600 * 1000).toISOString().slice(0, 10) : "";
}

// ------------------------------------------------------------------ códigos de barras
// Formatos de los productos de súper (GS1): EAN-13, EAN-8, UPC-A y UPC-E.
export const BARCODE_FORMATS = ["ean_13", "ean_8", "upc_a", "upc_e"];

// Dígito de control GS1 (8, 12, 13 o 14 dígitos).
export function gtinValid(digits) {
  const text = String(digits ?? "");
  if (!/^(?:\d{8}|\d{12,14})$/.test(text)) return false;
  let sum = 0;
  for (let index = text.length - 2, weight = 3; index >= 0; index -= 1, weight = 4 - weight) sum += Number(text[index]) * weight;
  return (10 - (sum % 10)) % 10 === Number(text.at(-1));
}

// UPC-E (8 dígitos, o 6/7 sin sistema o sin control) → UPC-A de 12 dígitos.
export function expandUpcE(value) {
  let text = String(value ?? "");
  if (text.length === 6) text = `0${text}`;
  if (!/^[01]\d{6,7}$/.test(text)) return null;
  const [n, a, b, c, d, e, f] = text;
  const body = f <= "2" ? `${n}${a}${b}${f}0000${c}${d}${e}`
    : f === "3" ? `${n}${a}${b}${c}00000${d}${e}`
      : f === "4" ? `${n}${a}${b}${c}${d}00000${e}`
        : `${n}${a}${b}${c}${d}${e}0000${f}`;
  if (text.length === 8) return `${body}${text[7]}`;
  for (let check = 0; check <= 9; check += 1) if (gtinValid(`${body}${check}`)) return `${body}${check}`;
  return null;
}

// Código leído o escrito → GTIN-14 posibles (con su dígito de control válido).
// 7411000230701, 07411000230701 y 0007411000230701 dan el mismo; un código de 8
// dígitos escrito a mano puede ser EAN-8 o UPC-E y se prueban los dos.
export function barcodeCandidates(raw, format = "") {
  let digits = String(raw ?? "").replace(/\D/g, "");
  if (digits.length > 14 && /^0+$/.test(digits.slice(0, -14))) digits = digits.slice(-14);
  const found = [];
  const add = (code) => {
    if (!code || !gtinValid(code)) return;
    const gtin = code.padStart(14, "0");
    if (!found.includes(gtin)) found.push(gtin);
  };
  if (format !== "upc_e") add(digits);
  if (format === "upc_e" || (!format && digits.length === 8)) add(expandUpcE(digits));
  return found;
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

  // Carga el manifest y la portada (home.json: ofertas, más comparados, departamentos,
  // ≈11 KB). El índice completo (≈1.4 MB) se pide aparte con loadIndex(); un catálogo
  // viejo sin portada lo carga aquí mismo.
  async load() {
    const response = await fetch(this.manifestUrl, { cache: "no-cache" });
    if (!response.ok) throw new Error(`No se pudo cargar el catálogo (${response.status})`);
    this.manifest = await response.json();
    if (this.manifest.schema !== "rpi-consumer-catalog-manifest/v3") throw new Error("Versión de catálogo no compatible");
    this.files = new Map((this.manifest.files || []).map((item) => [item.path, item]));
    if (!this.manifest.search_file || !this.files.has(this.manifest.search_file)) throw new Error("El catálogo publicado todavía no trae índice de búsqueda");
    this.ready = false;
    this.products = [];
    this.byId = new Map();
    const homeFile = this.manifest.home_file;
    if (!homeFile || !this.files.has(homeFile)) {
      await this.loadIndex();
      return this;
    }
    const home = await this.fetchJson(homeFile);
    if (home.schema !== "rpi-consumer-home/v1") throw new Error("Portada del catálogo no compatible");
    this.retailers = home.retailers;
    this.partitions = home.partitions;
    this.rowCount = home.row_count;
    this.homeDepartments = home.departments || [];
    this.unitBasis = home.unit_basis || {};
    this.unitMedians = new Map(Object.entries(home.unit_medians || {}));
    const rows = this.materialize(home);
    this.home = {
      deals: (home.sections?.deals || []).map((id) => rows.get(id)).filter(Boolean),
      compared: (home.sections?.compared || []).map((id) => rows.get(id)).filter(Boolean),
    };
    return this;
  }

  // Índice completo (search.json), una sola vez.
  loadIndex() {
    if (!this.indexPromise) {
      this.indexPromise = this.readIndex().catch((error) => { this.indexPromise = null; throw error; });
    }
    return this.indexPromise;
  }

  async readIndex() {
    const document = await this.fetchJson(this.manifest.search_file);
    if (document.schema !== "rpi-consumer-search/v1") throw new Error("Índice de búsqueda no compatible");
    this.retailers = document.retailers;
    this.partitions = document.partitions;
    const products = [...this.materialize(document, { finish: false }).values()];
    // Base de precio unitario más común por tipo (para tamaños leídos del nombre).
    const bases = new Map();
    for (const product of products) {
      if (!product.unit_per || !product.product_type) continue;
      const counts = bases.get(product.product_type) || new Map();
      counts.set(product.unit_per, (counts.get(product.unit_per) || 0) + 1);
      bases.set(product.product_type, counts);
    }
    const basisOf = (type) => {
      const counts = bases.get(type);
      return counts ? [...counts.entries()].sort((a, b) => b[1] - a[1])[0][0] : null;
    };
    for (const product of products) Object.assign(product, displayFields(product, basisOf(product.product_type)));
    hideImplausible(products);
    this.products = products;
    this.rowCount = products.length;
    this.byId = new Map(products.map((product) => [product.id, product]));
    this.ready = true;
    return this;
  }

  // Filas del índice (o de la portada) a productos. Con finish, aplica campos de
  // muestra y esconde medidas imposibles con las medianas que trae la portada.
  materialize(document, { finish = true } = {}) {
    const column = Object.fromEntries(document.columns.map((name, index) => [name, index]));
    const products = (document.rows || []).map((row) => {
      const product = {
        id: row[column.id], product_name: row[column.product_name], brand: row[column.brand],
        presentation: row[column.presentation], category: row[column.category], product_type: row[column.product_type],
        partition: document.partitions[row[column.partition]], best_price: row[column.best_price],
        unit_amount: row[column.unit_amount], unit_per: row[column.unit_per],
        retailers: (row[column.retailers] || []).map((index) => document.retailers[index]),
        promo: row[column.promo] === 1, other_count: row[column.other_presentations] || 0,
        comparability: row[column.comparability],
        photo: column.photo === undefined ? 0 : photoValue(row[column.photo]),
      };
      product.text = fold([product.brand, product.product_name, product.presentation, product.product_type].join(" "));
      return product;
    });
    if (finish) {
      for (const product of products) Object.assign(product, displayFields(product, this.unitBasis?.[product.product_type] || null));
      hideImplausible(products, this.unitMedians);
    }
    return new Map(products.map((product) => [product.id, product]));
  }

  // Un producto por id, esperando el índice si todavía no llegó. Un enlace viejo
  // (llave de antes de unir sucursales) se traduce a la llave actual.
  async product(id) {
    if (!this.ready) await this.loadIndex();
    if (this.byId.has(id)) return this.byId.get(id);
    const legacy = await this.legacyIds().catch(() => null);
    return this.byId.get(legacy?.current(id)) || null;
  }

  // Llaves de tarjeta anteriores → llave actual (Tegucigalpa, 2026-11: una tarjeta
  // por producto en vez de una por sucursal). Se pide sólo cuando hace falta.
  legacyIds() {
    const file = this.manifest?.legacy_ids_file;
    if (!file || !this.files?.has(file)) return Promise.resolve(null);
    if (!this.legacyPromise) {
      this.legacyPromise = this.fetchJson(file).then((document) => (
        document.schema === "rpi-consumer-legacy-ids/v1" ? legacyIndex(document) : null
      )).catch((error) => { this.legacyPromise = null; throw error; });
    }
    return this.legacyPromise;
  }

  // Códigos de barras (barcodes.json): GTIN-14 → prefijo de la llave de la fila. Se
  // pide sólo al escanear; null si el catálogo publicado todavía no lo trae.
  barcodeIndex() {
    const file = this.manifest?.barcodes_file;
    if (!file || !this.files?.has(file)) return Promise.resolve(null);
    if (!this.barcodePromise) {
      this.barcodePromise = this.fetchJson(file).then((document) => {
        if (document?.schema !== "rpi-consumer-barcodes/v1") throw new Error("Índice de códigos no compatible");
        return document;
      }).catch((error) => { this.barcodePromise = null; throw error; });
    }
    return this.barcodePromise;
  }

  // Productos de un código leído o escrito: { candidates, invalid, available, products }.
  async findBarcode(raw, format = "") {
    const candidates = barcodeCandidates(raw, format);
    if (!candidates.length) return { candidates, invalid: true, available: true, products: [] };
    const [index] = await Promise.all([this.barcodeIndex(), this.ready ? null : this.loadIndex()]);
    if (!index) return { candidates, invalid: false, available: false, products: [] };
    const length = Number(index.id_prefix_length) || 0;
    if (this.prefixLength !== length || !this.byPrefix) {
      this.byPrefix = new Map();
      for (const product of this.products) {
        const key = product.id.slice(0, length);
        (this.byPrefix.get(key) || this.byPrefix.set(key, []).get(key)).push(product);
      }
      this.prefixLength = length;
    }
    const products = [];
    for (const gtin of candidates) {
      const value = Object.hasOwn(index.codes || {}, gtin) ? index.codes[gtin] : null;
      for (const prefix of [value].flat()) {
        for (const product of this.byPrefix.get(String(prefix)) || []) if (!products.includes(product)) products.push(product);
      }
    }
    products.sort((a, b) => Number(a.best_price || Number.POSITIVE_INFINITY) - Number(b.best_price || Number.POSITIVE_INFINITY));
    return { candidates, invalid: false, available: true, products };
  }

  get asOf() { return this.manifest?.as_of; }
  get productCount() { return this.ready ? this.products.length : (this.rowCount || 0); }

  // Súper cuya última captura aceptada no es del día (manifest.source_freshness). FRESH
  // vale 48 h (sigue entrando a la comparación si falló un día), pero "de hoy" es la
  // fecha de Honduras del catálogo: La Colonia del 12-nov en el catálogo del 13-nov no
  // es "de hoy" aunque siga FRESH.
  staleRetailers() {
    const today = hondurasDay(this.manifest?.as_of);
    return (this.manifest?.source_freshness || [])
      .filter((item) => item.status && item.last_successful_at
        && (item.status !== "FRESH" || (today && hondurasDay(item.last_successful_at) < today)))
      .map((item) => ({ id: item.supermarket_id, location: item.location_id, since: item.last_successful_at }));
  }

  staleSince(offer) {
    const found = this.staleRetailers().find((item) => item.location === offer.location_id || (!item.location && item.id === offer.supermarket_id));
    return found ? found.since : null;
  }

  departments() {
    if (!this.ready) return this.homeDepartments || [];
    const counts = new Map();
    for (const product of this.products) {
      if (!product.category) continue;
      counts.set(product.category, (counts.get(product.category) || 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([name, count]) => ({ name, count }));
  }

  // Nivel de coincidencia: 0 = es ese tipo de producto o esa marca (huevos al buscar
  // "huevo"); 1 = sin tipo específico pero el nombre empieza por la palabra ("Huevos
  // Bonovo" en Abarrotes); 2 = sólo la menciona o es de otro tipo ("fideos de huevo",
  // "huevos de toro" en Res). 0 y 1 se muestran juntos; 2 va después.
  matchTier(product, terms) {
    const first = terms[0];
    if (!first) return 0;
    if (hasWord([product.product_type, product.category].join(" "), first) || fold(product.brand).startsWith(first)) return 0;
    if (!GENERIC_TYPES.has(fold(product.product_type)) && !isProduce(product)) return 2;
    const brand = fold(product.brand);
    let name = fold(product.product_name);
    if (brand && name.startsWith(`${brand} `)) name = name.slice(brand.length + 1);
    const lead = name.split(" ").slice(0, 2).join(" ");
    return hasWord(lead, first) ? 1 : 2;
  }

  search({ query = "", category = "", retailer = "", promoOnly = false, sort = "price", limit = 60 } = {}) {
    const terms = fold(query).split(" ").filter(Boolean);
    const results = this.products.filter((product) => {
      if (category && product.category !== category) return false;
      if (retailer && !product.retailers.includes(retailer)) return false;
      if (promoOnly && !product.promo) return false;
      if (!product.best_price) return false;
      // "huevos" también encuentra "huevo" (y al revés por prefijo).
      return terms.every((term) => product.text.includes(stem(term)));
    });
    const first = terms[0] || "";
    const wordHit = (value) => (first && ` ${fold(value)} `.includes(` ${first} `) ? 0 : 1);
    const brandHit = (product) => (first && fold(product.brand).startsWith(first) ? 0 : 1);
    const typeHit = (product) => wordHit(product.product_type);
    const nameHit = (product) => wordHit(product.product_name);
    const unitValue = (product) => (product.unit_amount ? Number(product.unit_amount) : Number.POSITIVE_INFINITY);
    const priceValue = (product) => Number(product.best_price);
    const comparators = {
      relevance: (a, b) => Math.min(brandHit(a), typeHit(a)) - Math.min(brandHit(b), typeHit(b))
        || nameHit(a) - nameHit(b) || b.retailers.length - a.retailers.length
        || a.product_name.length - b.product_name.length,
      unit: (a, b) => unitValue(a) - unitValue(b) || Number(a.best_price) - Number(b.best_price),
      price: (a, b) => priceValue(a) - priceValue(b) || b.retailers.length - a.retailers.length,
      stores: (a, b) => b.retailers.length - a.retailers.length || Number(a.best_price) - Number(b.best_price),
    };
    const compare = comparators[sort] || comparators.price;
    const tiers = new Map(results.map((product) => [product.id, this.matchTier(product, terms)]));
    // Primero lo que es el producto buscado; dentro de cada grupo, el orden elegido.
    const group = (product) => (tiers.get(product.id) === 2 ? 1 : 0);
    results.sort((a, b) => group(a) - group(b)
      || (sort === "relevance" ? tiers.get(a.id) - tiers.get(b.id) : 0) || compare(a, b));
    const items = results.slice(0, limit).map((product) => Object.assign(product, { tier: tiers.get(product.id) }));
    const related = results.filter((product) => tiers.get(product.id) === 2).length;
    return { total: results.length, items, related };
  }

  // Precio "por unidad" sólo se compara entre productos con conteo publicado ("12
  // unidades") y de un tipo concreto: "1 unidad" es el precio de la pieza, y en un tipo
  // genérico (Abarrotes) una tortilla y una semita no son alternativas.
  alternatives(product, limit = 6) {
    if (!product.product_type || !product.unit_per) return [];
    const perPiece = (item) => item.unit_per === "unidad"
      && (item.presentation === "1 unidad" || GENERIC_TYPES.has(fold(item.product_type)));
    if (perPiece(product)) return [];
    const peers = this.products.filter((other) => other.product_type === product.product_type
      && other.unit_per === product.unit_per && Number(other.unit_amount) > 0 && !perPiece(other));
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

// Ciudades publicadas (cities.json junto al manifest de San Pedro Sula).
export const DEFAULT_CITY = { slug: "san-pedro-sula", name: "San Pedro Sula", manifest: "manifest.json" };

export async function loadCities(manifestUrl) {
  const base = manifestUrl.replace(/manifest\.json(\?.*)?$/, "");
  try {
    const response = await fetch(`${base}cities.json`, { cache: "no-cache" });
    if (!response.ok) throw new Error(String(response.status));
    const index = await response.json();
    const cities = (index.cities || []).filter((city) => /^[a-z0-9-]+$/.test(city.slug || "")
      && /^[\w/.-]*manifest\.json$/.test(city.manifest || "") && !city.manifest.includes(".."));
    if (!cities.length) throw new Error("vacío");
    return { cities: cities.map((city) => ({ ...city, url: base + city.manifest })), fallback: index.default_city || cities[0].slug };
  } catch {
    return { cities: [{ ...DEFAULT_CITY, url: manifestUrl }], fallback: DEFAULT_CITY.slug };
  }
}

const CITY_KEY = "super-compras:city";

export function loadCitySlug() {
  try { return localStorage.getItem(CITY_KEY) || ""; } catch { return ""; }
}

export function saveCitySlug(slug) {
  try { localStorage.setItem(CITY_KEY, slug); } catch { /* sin guardar */ }
}

// Mi compra: lista guardada en este navegador, una por ciudad. Cada línea puede fijar
// el súper elegido (`store` = location_id); sin él se usa el más barato.
function cartKey(city = DEFAULT_CITY.slug) {
  return city === DEFAULT_CITY.slug ? "super-compras:cart:v1" : `super-compras:cart:v1:${city}`;
}

export function loadCart(city) {
  try {
    const value = JSON.parse(localStorage.getItem(cartKey(city)) || "[]");
    return Array.isArray(value)
      ? value.filter((line) => line && typeof line.id === "string" && Number.isInteger(line.qty) && line.qty > 0)
        .map((line) => ({ id: line.id, qty: line.qty, ...(typeof line.store === "string" ? { store: line.store } : {}) }))
      : [];
  } catch {
    return [];
  }
}

// { locations, rows: { llave actual: [[prefijo de llave vieja, índice de sucursal]] } }
// → buscador por prefijo: llave actual y sucursal de la tarjeta vieja.
export function legacyIndex(document) {
  const length = Number(document?.prefix_length) || 12;
  const locations = Array.isArray(document?.locations) ? document.locations : [];
  const map = new Map();
  for (const [current, olds] of Object.entries(document?.rows || {})) {
    for (const old of Array.isArray(olds) ? olds : []) {
      const [prefix, place] = Array.isArray(old) ? old : [old, null];
      map.set(String(prefix), { id: current, store: typeof locations[place] === "string" ? locations[place] : null });
    }
  }
  const find = (id) => map.get(String(id ?? "").slice(0, length)) || null;
  return { current: (id) => find(id)?.id || null, store: (id) => find(id)?.store || null };
}

// Traduce las líneas de Mi compra con llaves viejas; las demás quedan igual. Una
// línea sin súper elegido queda fijada en la sucursal de su tarjeta vieja (era la
// única oferta de esa tarjeta: el precio que vio no cambia solo). Si dos líneas caen
// en el mismo producto y súper, se suman; nunca se agregan líneas.
export function migrateCartLines(lines, known, legacy) {
  const result = [];
  for (const line of lines) {
    let next = line;
    if (!known.has(line.id)) {
      const current = legacy?.current(line.id);
      if (current && known.has(current)) {
        const store = line.store || legacy.store(line.id);
        next = store ? { ...line, id: current, store } : { ...line, id: current };
      }
    }
    const twin = result.find((item) => item.id === next.id && (item.store || null) === (next.store || null));
    if (twin) twin.qty += next.qty; else result.push({ ...next });
  }
  return result;
}

export function saveCart(lines, city) {
  try { localStorage.setItem(cartKey(city), JSON.stringify(lines)); } catch { /* modo privado: sin guardar */ }
}

// ------------------------------------------------------------------ panel de revisión de tamaños
// La cola es pública (rama `revision-tamanos`); el envío lanza el workflow
// "Guardar decisiones de tamaño" con el token fino de GitHub del dueño. El token
// sólo viaja en el encabezado Authorization a api.github.com (nunca en la URL).
export const SIZE_QUEUE_URL =
  "https://raw.githubusercontent.com/jchernandez-portfolio/precios-supermercados-sps/revision-tamanos/cola.json";
export const SIZE_DECISIONS_DISPATCH_URL =
  "https://api.github.com/repos/jchernandez-portfolio/precios-supermercados-sps/actions/workflows/precios-supermercados-sps-size-decisions.yml/dispatches";
export const SIZE_DECISIONS_RUNS_URL =
  "https://github.com/jchernandez-portfolio/precios-supermercados-sps/actions/workflows/precios-supermercados-sps-size-decisions.yml";
export const SIZE_DECISIONS_SCHEMA = "rpi-size-decisions/v1";
export const SIZE_UNITS = ["g", "ml", "oz", "lb", "unit"];
const SIZE_MAX_DECISIONS = 300;
const SIZE_MAX_INPUT_CHARS = 60000;
const GITHUB_TOKEN_PATTERN = /^(github_pat_|ghp_)[A-Za-z0-9_]{20,255}$/;

export async function loadSizeQueue(url = SIZE_QUEUE_URL) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(response.status === 404 ? "Todavía no hay cola publicada." : `No pudimos leer la cola (${response.status}).`);
  const queue = await response.json();
  if (queue?.schema !== "rpi-size-queue/v1" || !Array.isArray(queue.items)) throw new Error("La cola tiene un formato desconocido.");
  return queue;
}

// Decisiones del panel → cuerpo del workflow_dispatch. Falla antes de enviar si
// algo no cumple lo que valida el workflow (mismo contrato, más estricto allá).
export function sizeDecisionsBody(decisions) {
  const clean = [];
  for (const item of decisions) {
    if (!/^\d{8,14}$/.test(String(item.gtin || ""))) throw new Error(`Código inválido: ${item.gtin}`);
    const note = String(item.note || "").trim().slice(0, 200);
    if (item.decision === "different_products") {
      clean.push({ gtin: item.gtin, decision: "different_products", ...(note ? { note } : {}) });
    } else if (item.decision === "size") {
      const value = String(item.value ?? "").trim().replace(",", ".");
      if (!(Number(value) > 0) || !SIZE_UNITS.includes(item.unit)) throw new Error(`Tamaño incompleto en ${item.gtin}`);
      clean.push({ gtin: item.gtin, decision: "size", value, unit: item.unit, ...(note ? { note } : {}) });
    }
  }
  if (!clean.length) throw new Error("No hay decisiones para enviar.");
  if (clean.length > SIZE_MAX_DECISIONS) throw new Error(`Envía como máximo ${SIZE_MAX_DECISIONS} decisiones a la vez.`);
  const text = JSON.stringify({ schema: SIZE_DECISIONS_SCHEMA, decisions: clean });
  if (text.length > SIZE_MAX_INPUT_CHARS) throw new Error("El envío es muy grande; divídelo en dos.");
  return { ref: "main", inputs: { decisions: text } };
}

export async function sendSizeDecisions(token, decisions) {
  const key = String(token || "").trim();
  if (!GITHUB_TOKEN_PATTERN.test(key)) throw new Error("La llave de GitHub no tiene el formato esperado (github_pat_…).");
  const body = sizeDecisionsBody(decisions);
  const response = await fetch(SIZE_DECISIONS_DISPATCH_URL, {
    method: "POST",
    cache: "no-store",
    credentials: "omit",
    referrerPolicy: "no-referrer",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${key}`,
      "Content-Type": "application/json",
      "X-GitHub-Api-Version": "2022-11-28",
    },
    body: JSON.stringify(body),
  });
  if (response.status === 204) return { sent: JSON.parse(body.inputs.decisions).decisions.length };
  const reasons = {
    401: "GitHub no aceptó la llave (vencida o mal copiada).",
    403: "La llave no tiene permiso “Actions: read and write” en este repositorio.",
    404: "GitHub no encontró el workflow con esta llave (¿repositorio correcto?).",
    422: "GitHub rechazó el envío (el workflow debe estar en main).",
  };
  throw new Error(reasons[response.status] || `GitHub respondió ${response.status}.`);
}
