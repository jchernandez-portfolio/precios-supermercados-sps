# Encargo: rediseño «Lima» de Súper Compras

Repositorio: `jchernandez-portfolio/precios-supermercados-sps`. La app está en `precios-supermercados-sps/super-compras/` (HTML + CSS + JavaScript sin framework: `index.html`, `styles.css`, `app.js`, `data.js`, `admin.js`, `scanner.js`, `sw.js`, `manifest.webmanifest`, `fonts/`, `icons/`).
En vivo: https://jchernandez-portfolio.github.io/precios-supermercados-sps/super-compras/

Cambiar el diseño visual y la estructura de la app al diseño «Lima» (opción 03 elegida por el dueño en un lienzo de diseño). **No cambiar la lógica de datos** (catálogo, búsqueda, precios, Mi compra, escáner, panel de revisión): es un cambio de presentación y de estructura de pantallas.

Reglas del dueño: rama `ui/diseno-lima` desde `main` actualizado; pruebas (`cd precios-supermercados-sps && PYTHONPATH=src python -m pytest -q`) y lint (`npx --yes @biomejs/biome@2.3.5 lint super-compras`) en verde; capturas Playwright a 390×844 y 1280×800 sirviendo la carpeta con `python -m http.server`; si algo de la especificación choca con el código real, no inventar: anotar y usar la alternativa más cercana; nada de credenciales; no agregar dependencias npm ni librerías al sitio; no cargar nada de otros dominios (CSP de `index.html`: fuentes, estilos y scripts propios; fotos del bucket R2 ya permitido); no hacer merge: abrir el PR y esperar aprobación.

(El dueño pidió hacerlo TODO de una vez, sin detenerse entre fases.)

---

## 1. Identidad «Lima»

**Idea:** carbón y lima. Fondo claro cálido, barras carbón, precios en una etiqueta lima. Nada de rojo/amarillo, azul/amarillo ni verde/naranja (no debe parecerse a ningún supermercado de Honduras).

### 1.1 Colores (tokens)

Reemplazar los tokens actuales de `:root` en `styles.css` por estos (y migrar los usos de `--green`, `--amber`, etc. a los nuevos nombres; los colores de cada súper en `RETAILER_COLORS` de `data.js` **no cambian**):

```css
:root {
  /* Lima — carbón y lima */
  --bg: #F4F5F0;          /* fondo de la página */
  --surface: #FFFFFF;     /* tarjetas */
  --ink: #1C1F1D;         /* texto */
  --muted: #545B55;       /* texto secundario (6:1 sobre --bg) */
  --line: #DCDFD6;        /* bordes y divisores */
  --charcoal: #1C1F1D;    /* barras, botón principal */
  --charcoal-2: #2A2E2B;  /* placa del logo, hover sobre carbón */
  --on-charcoal: #F4F5F0; /* texto sobre carbón */
  --lime: #C6F432;        /* acento: etiqueta de precio, descuento, ícono activo */
  --on-lime: #1C1F1D;     /* texto sobre lima */
  --good: #3B6B00;        /* ahorro / más barato */
  --good-soft: #EEF9D0;   /* fondo de ahorro */
  --danger: #B42318;      /* errores (se mantiene) */
  --photo-bg: #FFFFFF;    /* recuadro de fotos (las fotos traen fondo blanco) */
  --radius: 10px;
  --radius-sm: 6px;
  --shadow: 0 1px 3px rgba(0, 0, 0, .10);
  --font-display: "Space Grotesk", system-ui, sans-serif;
  --font-body: Inter, system-ui, -apple-system, "Segoe UI", sans-serif;
  --topbar: 64px;
  --tabbar: 76px;
  --rail: 84px;
  color-scheme: light;
}
```

Reglas de uso:
- **Precio**: etiqueta lima → `background: var(--lime); color: var(--on-lime); border-radius: var(--radius-sm); padding: 2px 8px; font-family: var(--font-display); font-weight: 700`.
- **Descuento** (−18%): píldora lima con texto carbón, 800, 11–13px.
- **Ahorro / el más barato**: texto `--good` sobre `--good-soft`.
- **Botón principal**: fondo `--charcoal`, texto `--lime`, alto 52px, radio `--radius`.
- **Fotos**: siempre dentro de un recuadro `--photo-bg` con `object-fit: contain` y 4–8px de margen interno.
- **Foco visible**: `outline: 3px solid var(--charcoal); outline-offset: 2px;` y sobre fondos carbón `outline-color: var(--lime)`.
- `<meta name="theme-color">` → `#1C1F1D`. En `manifest.webmanifest`: `theme_color` y `background_color` → `#1C1F1D`.

### 1.2 Tipografía

- Títulos, precios y nombre de la marca: **Space Grotesk** 500 y 700, `letter-spacing: -0.02em`.
- Texto: **Inter** 400, 600 y 700.
- Servidas desde `super-compras/fonts/` en woff2 (subconjunto latino, con `unicode-range` como el que ya usa Figtree) con sus licencias OFL (`LICENSE-SpaceGrotesk.txt`, `LICENSE-Inter.txt`). Bajarlas una sola vez de una fuente oficial (paquetes `@fontsource/space-grotesk` e `@fontsource/inter` vía `npm pack`, o el repositorio de Google Fonts), fijar la versión en el commit y no agregar nada a `package.json` del sitio.
- `preload` de `index.html`: Inter 400 y Space Grotesk 700. Cuando todo use las nuevas fuentes, borrar Figtree (archivos, `@font-face`, preload y su entrada en `sw.js`).
- Escala: h1 28px celular / 36px computadora; h2 19 / 24; texto 15; secundario 13; precio grande 26–44.

### 1.3 Logo

Etiqueta de precio lima con un signo % en carbón. Guardar como `super-compras/icons/logo.svg` y usarlo en la barra junto al nombre «Súper Compras» en Space Grotesk 700:

```svg
<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120" viewBox="0 0 120 120" fill="none"><path d="M18 24h52l34 36-34 36H18z" fill="#C6F432" stroke="#1C1F1D" stroke-width="6" stroke-linejoin="round"/><circle cx="82" cy="60" r="7" fill="#1C1F1D"/><circle cx="33" cy="44" r="7" stroke="#1C1F1D" stroke-width="5"/><circle cx="55" cy="76" r="7" stroke="#1C1F1D" stroke-width="5"/><path d="M56 40 32 80" stroke="#1C1F1D" stroke-width="6" stroke-linecap="round"/></svg>
```

En la barra el logo va sobre una «placa» de 38px (celular) / 48px (computadora), fondo `--charcoal-2`, radio 10px; el SVG dentro mide 28 / 36px.

### 1.4 Ícono de la app

Reemplazar `icons/icon.svg` por este y regenerar `icon-180.png`, `icon-192.png` e `icon-512.png` (con Playwright o la herramienta local; no meter la herramienta al repo):

```svg
<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512"><rect width="512" height="512" rx="112" fill="#1C1F1D"/><g transform="translate(76 76) scale(3)"><path d="M18 24h52l34 36-34 36H18z" fill="#C6F432" stroke="#C6F432" stroke-width="6" stroke-linejoin="round"/><circle cx="82" cy="60" r="7" fill="#1C1F1D"/><circle cx="33" cy="44" r="7" fill="none" stroke="#1C1F1D" stroke-width="5"/><circle cx="55" cy="76" r="7" fill="none" stroke="#1C1F1D" stroke-width="5"/><path d="M56 40 32 80" stroke="#1C1F1D" stroke-width="6" stroke-linecap="round"/></g></svg>
```

Agregar también un ícono `maskable` (512, con margen seguro del 20%) en el manifest.

---

## 2. Estructura general

### 2.1 Celular (hasta 1023px de ancho)

- **Barra superior** fija, 64px, fondo `--charcoal`, texto `--on-charcoal`: placa del logo + «Súper Compras» (20px) + a la derecha el selector de ciudad como píldora con borde `currentColor` («San Pedro Sula ▾»). Respetar `env(safe-area-inset-top)`.
- **Barra inferior** fija, 76px, fondo blanco, borde superior `--line`, 5 columnas: `Inicio` · `Ofertas` · **[botón central Buscar]** · `Escanear` · `Mi compra` (con el contador actual).
  - Íconos de 22px + etiqueta 11px; inactivos `--muted`, activo `--ink` con peso 800 y un punto o subrayado lima de 3px.
  - Botón central: círculo de 64px, fondo `--charcoal`, lupa lima de 26px, subido 26px sobre la barra (`margin-top: -26px`), borde de 4px blanco y sombra `0 8px 20px rgba(0,0,0,.22)`. Lleva a `#/buscar` y enfoca el buscador.
  - `Escanear` abre el escáner que ya existe (`startScanner`). Si el navegador no tiene cámara, lleva a `#/buscar`.
  - Respetar `env(safe-area-inset-bottom)`.
- Contenido con 16px de margen lateral.

### 2.2 Computadora (1024px o más)

- **Riel lateral** fijo a la izquierda, 84px, alto completo, fondo `--charcoal`: placa del logo (48px) arriba y debajo Inicio, Buscar, Ofertas, Escanear y Mi compra (cada uno 72×64, ícono + texto 11px). Activo: fondo `rgba(255,255,255,.12)`, radio 14px y color `--lime`.
- **Barra superior delgada** de 64px sobre el contenido, fondo `--bg` con borde inferior `--line`: buscador ancho (42px de alto), selector de ciudad y a la derecha el botón «Mi compra · N» (fondo carbón, texto lima).
- Contenido a la derecha del riel, ancho máximo 1200px, padding 24–32px.
- Se ocultan la barra inferior y la barra superior carbón del celular.

El pie de página actual se mantiene con los nuevos colores.

---

## 3. Pantallas

En todas: fotos reales de producto (`productVisual` ya las trae del índice de fotos R2), precios en etiqueta lima y súper con su punto de color.

### 3.1 Inicio — «muro de rebajas»

1. Línea corta: «Compara precios en San Pedro Sula · actualizado …» (usar `freshnessText()` o su equivalente real). En celular **no** poner el buscador grande (lo da el botón central); en computadora el buscador ya está en la barra.
2. **Departamentos como círculos** con scroll horizontal: círculo de 58px (76px en computadora), borde 3px `--charcoal`, fondo blanco, con la foto de un producto representativo del departamento (el primer producto con foto en los datos ya cargados; si no hay, la inicial) y el nombre debajo (11–13px, 700). Lleva a `#/buscar?cat=…`.
3. **«Bajaron de precio hoy»** (ofertas de la portada, `homeSections().deals` o su equivalente real `catalog.home.deals`): tarjetas anchas, 1 columna en celular y 2 en computadora. Cada una: foto 76px (110px en computadora), nombre + presentación, precio en etiqueta lima, precio anterior tachado, % de rebaja en `--good` y debajo una **franja de precios por súper**: celdas iguales con nombre corto del súper y su precio; la más barata con fondo `--good-soft`, borde y texto `--good`.
   - Precios por súper desde `catalog.row(product)` (ofertas de la partición), carga diferida sólo para las primeras 6 tarjetas; si fallan o tardan, la tarjeta se ve completa sin la franja. No bloquear el render.
4. **«Los más comparados»**: lista con foto de 52px, nombre, nota en verde («Walmart · 5 súper · ahorras L 36.25» cuando el dato exista) y precio.
5. Departamentos en tarjetas (lo actual) y la franja «Comparamos» con los súper de la ciudad (dinámica: SPS 5, TGU incluye Paiz).

### 3.2 Búsqueda y resultados — filtros en menús desplegables

- Arriba el buscador (en celular 44px de alto, con el texto buscado).
- **Fila de filtros desplegables** (nada en columna lateral). Quitar los `<select>` nativos visibles y poner botones píldora: `Supermercado ▾`, `Departamento ▾`, `Ordenar ▾` y el interruptor `En oferta`.
  - Al tocar un botón se abre un panel flotante bajo el botón (en celular puede ser hoja que sube desde abajo) con opciones como lista de radio (una opción, como hoy `super`, `cat` y `orden` en la URL). Al elegir se aplica y se cierra.
  - Botón con filtro activo: muestra el valor («Walmart ▾») y borde 2px `--charcoal`.
  - Accesible: `<button aria-haspopup="listbox" aria-expanded>`, opciones con `role="option"` o radios reales con `<label>`, Esc y clic fuera cierran, el foco vuelve al botón. Mínimo 44px por opción.
  - La fila hace scroll horizontal en celular si no cabe.
- **Resultados como tarjetas con todos los precios**: 1 columna en celular, 2 en computadora. Foto 72px (96px), nombre + presentación, precio por unidad, precio en etiqueta lima a la derecha y la misma franja por súper (carga diferida, sólo resultados visibles).
- Se mantienen «También mencionan …», el contador y «Mostrar más».

### 3.3 Ficha de producto — «gráfica por súper»

- Cabecera: foto en recuadro blanco de 120px (180px en computadora), nombre (Space Grotesk 22 / 36px), presentación y precio por unidad, y a la derecha «desde **L 34.00**» grande en `--ink`.
- Tarjeta **«Precio en cada súper»**: una fila por oferta: punto + nombre del súper, barra horizontal y precio. Ancho = `40% + 60% × (precio − mínimo) / (máximo − mínimo)` (100% si son iguales); la más barata en `--good`, las demás en `--muted` con 35% de opacidad; alto 12px / 22px, radio 6px. Debajo notas cortas: qué súper no lo vende y cuál está en oferta («Walmart la tiene en oferta, antes L 37.50»).
- Se mantienen historial de precios, «Otros tamaños», alternativas y el resumen de código escaneado, con el nuevo estilo.
- Botón «Agregar a Mi compra» (carbón con texto lima, 52px): en celular fijo abajo, sobre la barra inferior.

### 3.4 Ofertas — «destacada + carrusel»

- Título «Ofertas de hoy», el conteo y la misma fila de filtros desplegables.
- **Tarjeta destacada**: la oferta con mayor % de rebaja (precio regular publicado contra el actual; sin regular, la primera). Fondo `--charcoal`, texto claro, «La mejor rebaja de hoy», píldora lima con el %, foto 120px (300px en computadora) en recuadro blanco, nombre, precio grande (30 / 40px) en lima y «antes L xx · súper».
- Celular: debajo, **carrusel horizontal** con las 4 siguientes (tarjetas de 122px: foto, nombre, precio y %), y después lista compacta con el resto.
- Computadora: destacada a la izquierda y mosaico 3×2 a la derecha; debajo, la cuadrícula normal con «Mostrar más».

### 3.5 Mi compra — «comparar súper»

Mantener toda la lógica actual (`perStore`, `split`, `chosenTotal`, selector de súper por línea, cantidades, aviso de productos que ya no están). Presentación:
- Arriba: «¿Dónde te sale más barata toda la lista?» y **tarjetas de comparación** (2 columnas en celular, 5 en computadora): primera «Mezclando N súper» con el total `split`, fondo `--good-soft`, borde 2px `--good`, «La opción más barata»; luego una por súper: «Todo en Walmart» con su total y «+ L 7.15» frente a la mezcla, o «Le falta 1 producto». La más barata de un solo súper con fondo `--charcoal` y texto claro.
- Debajo, las líneas: foto 44px (64px), nombre, súper más barato con su punto, el selector actual, cantidad (botones de 44px) y total.
- En computadora, además, **tabla producto × súper** con el precio de cada línea en cada súper y el más barato de cada fila en `--good` y negrita.
- Se mantienen los avisos y el texto final de total estimado.

### 3.6 Panel de revisión (`#/admin/revision`)

Sólo hereda colores y fuentes nuevas. No cambiar su estructura ni su lógica.

---

## 4. Animación de entrada (sólo celular)

**Cuándo:** ancho hasta 1023px, una vez por sesión (bandera en `sessionStorage`, con try/catch). En computadora o en la segunda apertura de la sesión se quita al instante.

**Secuencia (≈1.3 s):**
1. 0 ms: fondo carbón a pantalla completa.
2. 0–600 ms: la etiqueta lima entra con «pop»: de escala 0.4 y −18° a normal, con rebote (`cubic-bezier(.34, 1.56, .64, 1)`).
3. 350–800 ms: el signo % se dibuja trazo por trazo (`stroke-dashoffset`).
4. 650–1100 ms: «Súper Compras» sube 12px y aparece; debajo una línea lima de 3px se extiende de izquierda a derecha.
5. Salida: cuando el catálogo cargó (o falló) y pasó un mínimo de 1100 ms (máximo 2600 ms aunque no haya cargado), la pantalla sube y desaparece en 450 ms (`translateY(-100%)`, `cubic-bezier(.7, 0, .84, 0)`) y se elimina del DOM.
- Tocar la pantalla la cierra de inmediato.
- `prefers-reduced-motion: reduce`: sin movimiento; estática y se desvanece en 150 ms.
- `aria-hidden="true"`, no atrapa el foco y no retrasa la carga de datos.
- La CSP no permite estilos en línea: todo el CSS en `styles.css` y el marcado estático en `index.html`.

Marcado (primer hijo de `<body>`):

```html
<div class="intro" aria-hidden="true">
  <svg class="intro-mark" viewBox="0 0 120 120" width="112" height="112">
    <path class="intro-tag" d="M18 24h52l34 36-34 36H18z" fill="#C6F432" stroke="#C6F432" stroke-width="6" stroke-linejoin="round"/>
    <circle cx="82" cy="60" r="7" fill="#1C1F1D"/>
    <g class="intro-pct" fill="none" stroke="#1C1F1D" stroke-linecap="round">
      <circle cx="33" cy="44" r="7" stroke-width="5" pathLength="60"/>
      <circle cx="55" cy="76" r="7" stroke-width="5" pathLength="60"/>
      <path d="M56 40 32 80" stroke-width="6" pathLength="60"/>
    </g>
  </svg>
  <p class="intro-word">Súper Compras</p>
  <span class="intro-line"></span>
</div>
```

CSS de partida:

```css
.intro { position: fixed; inset: 0; z-index: 1000; display: grid; place-content: center; justify-items: center; gap: 14px; background: #1C1F1D; color: #F4F5F0; }
.intro-mark { animation: intro-pop 600ms cubic-bezier(.34, 1.56, .64, 1) both; }
.intro-pct > * { stroke-dasharray: 60; stroke-dashoffset: 60; animation: intro-draw 450ms 350ms ease-out forwards; }
.intro-word { margin: 0; font: 700 28px var(--font-display); letter-spacing: -0.02em; opacity: 0; transform: translateY(12px); animation: intro-rise 450ms 650ms ease-out forwards; }
.intro-line { width: 120px; height: 3px; border-radius: 3px; background: #C6F432; transform: scaleX(0); transform-origin: left; animation: intro-sweep 400ms 900ms ease-out forwards; }
.intro.is-leaving { animation: intro-out 450ms cubic-bezier(.7, 0, .84, 0) forwards; }
@keyframes intro-pop { from { transform: scale(.4) rotate(-18deg); opacity: 0; } to { transform: none; opacity: 1; } }
@keyframes intro-draw { to { stroke-dashoffset: 0; } }
@keyframes intro-rise { to { opacity: 1; transform: none; } }
@keyframes intro-sweep { to { transform: scaleX(1); } }
@keyframes intro-out { to { transform: translateY(-100%); } }
@media (min-width: 1024px) { .intro { display: none; } }
@media (prefers-reduced-motion: reduce) {
  .intro-mark, .intro-pct > *, .intro-word, .intro-line { animation: none; opacity: 1; transform: none; stroke-dashoffset: 0; }
  .intro.is-leaving { animation: intro-fade 150ms linear forwards; }
  @keyframes intro-fade { to { opacity: 0; } }
}
```

En `app.js`: función `dismissIntro()` que agrega `.is-leaving`, espera `animationend` (respaldo de 600 ms) y quita el nodo. Llamarla al terminar la primera carga de ciudad (éxito o error), respetando mínimo y máximo. Subir la versión con `scripts/versionar_super_compras.py` y revisar que `sw.js` precargue los archivos nuevos (fuentes, logo, íconos).

---

## 5. Calidad

- Contraste AA en todo el texto; áreas táctiles de 44px o más.
- Nada de scroll horizontal de la página en 360px.
- Las vistas funcionan para Tegucigalpa (6 súper) y San Pedro Sula (5 súper): nada de listas fijas de súper en la vista.
- Sin errores en la consola. La CSP de `index.html` no se relaja.
- `prefers-reduced-motion` respetado en toda animación nueva.

## 6. Entrega

Revisar las 5 pantallas en 360, 390, 768, 1024 y 1440 px; pruebas y lint; versión de la app; PR hacia `main` con capturas antes/después en la descripción. **No hacer merge.**
