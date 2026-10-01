(() => {
  const STATE = Object.freeze({
    asOf: '2026-09-08',
    supermarkets: 6,
    locations: 11,
    products: 58114,
    history: 127980,
    chains: Object.freeze([
      Object.freeze({ name: 'La Colonia', locations: 'SPS · Tegucigalpa' }),
      Object.freeze({ name: 'Supermercados Colonial', locations: 'SPS' }),
      Object.freeze({ name: 'Walmart', locations: 'SPS · TGU FFAA · TGU El Sauce' }),
      Object.freeze({ name: 'PriceSmart', locations: 'SPS 6603 · Florencia 6602' }),
      Object.freeze({ name: 'Comisariato Los Andes', locations: 'SPS' }),
      Object.freeze({ name: 'Paiz', locations: 'TGU Multiplaza · TGU Próceres' })
    ])
  });

  const SAFE_SAMPLE_SCHEMA = 'precios-sps-safe-portfolio-sample/v1';
  const SAFE_POLICY = 'fail_closed_strong_identity_and_commercial_consistency';
  const SAFE_SAMPLE_REPOSITORY_URL = 'https://raw.githubusercontent.com/jchernandez-portfolio/precios-supermercados-sps/portfolio-data/precios-supermercados-sps/portfolio/sample-data.json';
  const SAFE_SAMPLE_URL = ['localhost', '127.0.0.1'].includes(window.location.hostname)
    ? 'precios-supermercados-sps/portfolio/sample-data.json'
    : SAFE_SAMPLE_REPOSITORY_URL;
  const EXPECTED_SCOPE = Object.freeze([
    Object.freeze({ supermarket_id: 'la_colonia', location_id: 'la_colonia_sps' }),
    Object.freeze({ supermarket_id: 'walmart', location_id: 'walmart_sps' })
  ]);

  let verifiedSample = null;
  let sampleLoadStarted = false;

  const copy = {
    es: {
      cardLabels: ['supermercados', 'ubicaciones', 'productos registrados', 'registros históricos'],
      stage: '6 supermercados integrados, 11 ubicaciones, 58K+ productos registrados y 127K+ periodos históricos de precio.',
      scaleTitle: 'Cobertura productiva actual',
      scaleBody: 'Estado verificado al 8 de septiembre de 2026. La cobertura productiva incluye las seis cadenas que ya tienen datos aceptados en la base.',
      scaleLabels: ['supermercados', 'ubicaciones monitoreadas', 'productos registrados', 'registros históricos de precio'],
      coverageTitle: 'Supermercados con datos disponibles',
      coverageBody: 'Cada fila corresponde a una cadena con datos productivos aceptados. La cobertura de scraping y la cobertura de comparación cross-source se muestran como conceptos separados.',
      chain: 'Supermercado',
      locations: 'Ubicaciones con datos',
      status: 'Estado',
      accepted: 'Datos aceptados',
      sampleTitle: 'Comparaciones cross-source con identidad fuerte',
      sampleBody: 'La muestra anterior se retiró porque marca + presentación no demuestran que dos registros sean el mismo producto. El comparador ahora exige identidad fuerte y consistencia comercial antes de calcular un mejor precio.',
      sampleNote: 'El dataset público se vuelve a habilitar únicamente con filas que superen el gate fail-closed. Passion Jaguar y Passion Especial quedan bloqueados como comparación automática aunque compartan marca y presentación.',
      safeTitle: 'Precios comparables con identidad fuerte verificada',
      safeBody: 'Cada fila proviene del dataset fail-closed generado después de homologación. La identidad se apoya en GTIN y coherencia comercial; el navegador no vuelve a hacer matching por nombre, marca ni presentación.',
      safeProduct: 'Producto verificado',
      safeGtin: 'GTIN',
      safeLaColonia: 'La Colonia · SPS',
      safeWalmart: 'Walmart · SPS',
      safeBest: 'Mejor precio',
      safeSavings: 'Ahorro vs. mayor',
      safeNote: 'Muestra pública derivada únicamente de productos comparables y con precio en ambas ubicaciones del alcance. Se refresca automáticamente después de cada actualización analítica válida y el navegador nunca consulta Turso.',
      matchingBody: 'La homologación propone identidades; el gate de comparación exige evidencia fuerte y coherencia comercial antes de calcular ahorros. Marca + presentación nunca bastan por sí solas.',
      insightsBody: 'Los hallazgos actuales usan identidades demostradas dentro de una misma cadena. Las comparaciones cross-source permanecen cerradas si la identidad comercial es ambigua.',
      roadmap: 'Seis cadenas productivas integradas bajo una estructura común.'
    },
    en: {
      cardLabels: ['retail chains', 'locations', 'products recorded', 'historical records'],
      stage: '6 integrated retail chains, 11 locations, 58K+ products recorded, and 127K+ historical price periods.',
      scaleTitle: 'Current production coverage',
      scaleBody: 'Verified state as of September 8, 2026. Production coverage includes all six chains with accepted data in the database.',
      scaleLabels: ['retail chains', 'monitored locations', 'products recorded', 'historical price records'],
      coverageTitle: 'Retail chains with available data',
      coverageBody: 'Each row is a chain with accepted production data. Scraping coverage and cross-source comparison coverage are shown as separate concepts.',
      chain: 'Retail chain',
      locations: 'Locations with data',
      status: 'Status',
      accepted: 'Accepted data',
      sampleTitle: 'Cross-source comparisons with strong identity',
      sampleBody: 'The previous sample was removed because brand + presentation do not prove that two records are the same commercial product. The comparator now requires strong identity and commercial consistency before calculating a best price.',
      sampleNote: 'The public comparison dataset is enabled only for rows that pass the fail-closed gate. Passion Jaguar and Passion Especial remain blocked from automatic comparison even when brand and presentation match.',
      safeTitle: 'Comparable prices with verified strong identity',
      safeBody: 'Every row comes from the fail-closed dataset generated after homologation. Identity is based on GTIN plus commercial consistency; the browser does not redo matching by name, brand, or presentation.',
      safeProduct: 'Verified product',
      safeGtin: 'GTIN',
      safeLaColonia: 'La Colonia · SPS',
      safeWalmart: 'Walmart · SPS',
      safeBest: 'Best price',
      safeSavings: 'Savings vs. highest',
      safeNote: 'This public sample contains only products that are comparable and priced in both locations in scope. It refreshes automatically after every valid analytics update, and the browser never queries Turso.',
      matchingBody: 'Homologation proposes identities; the comparison gate requires strong evidence and commercial consistency before calculating savings. Brand + presentation are never sufficient on their own.',
      insightsBody: 'Current findings use proven identities within the same retail chain. Cross-source comparisons remain closed whenever commercial identity is ambiguous.',
      roadmap: 'Six production retail chains integrated under one shared structure.'
    }
  };

  function locale() {
    return window.PortfolioI18n?.getLocale?.() === 'en' ? 'en' : 'es';
  }

  function text() {
    return copy[locale()];
  }

  function setText(node, value) {
    if (node) node.textContent = value;
  }

  function money(value) {
    const amount = Number(value);
    return Number.isFinite(amount) ? `L ${amount.toFixed(2)}` : '';
  }

  function exactScope(scope) {
    if (!Array.isArray(scope) || scope.length !== EXPECTED_SCOPE.length) return false;
    return EXPECTED_SCOPE.every(expected => scope.some(item =>
      item?.supermarket_id === expected.supermarket_id && item?.location_id === expected.location_id
    ));
  }

  function validatePublishedSample(document) {
    if (!document || typeof document !== 'object') return null;
    if (document.schema !== SAFE_SAMPLE_SCHEMA || document.comparison_policy !== SAFE_POLICY) return null;
    if (!exactScope(document.scope)) return null;
    if (!Number.isInteger(document.row_count) || document.row_count < 1 || document.row_count > 10) return null;
    if (!Array.isArray(document.rows) || document.rows.length !== document.row_count) return null;

    for (const row of document.rows) {
      if (!row || typeof row !== 'object') return null;
      if (typeof row.canonical_product_id !== 'string' || !row.canonical_product_id) return null;
      if (typeof row.canonical_gtin !== 'string' || !/^\d{8,14}$/.test(row.canonical_gtin)) return null;
      if (!Array.isArray(row.offers) || row.offers.length !== 2) return null;
      if (!Number.isFinite(Number(row.best_price)) || Number(row.best_price) <= 0) return null;
      if (!Number.isFinite(Number(row.savings_vs_highest)) || Number(row.savings_vs_highest) < 0) return null;

      const supermarkets = new Set();
      for (const offer of row.offers) {
        if (!offer || typeof offer !== 'object') return null;
        if (!['la_colonia', 'walmart'].includes(offer.supermarket_id)) return null;
        const expectedLocation = offer.supermarket_id === 'la_colonia' ? 'la_colonia_sps' : 'walmart_sps';
        if (offer.location_id !== expectedLocation) return null;
        if (supermarkets.has(offer.supermarket_id)) return null;
        supermarkets.add(offer.supermarket_id);
        if (typeof offer.source_record_id !== 'string' || !offer.source_record_id) return null;
        if (typeof offer.source_name !== 'string' || !offer.source_name.trim()) return null;
        if (!Number.isFinite(Number(offer.current_price)) || Number(offer.current_price) <= 0) return null;
      }
      if (supermarkets.size !== 2) return null;
    }
    return document;
  }

  function patchCard() {
    const card = document.querySelector('#proyectos .price-card');
    if (!card) return;
    const values = ['6', '11', '58K+', '127K+'];
    card.querySelectorAll('.price-card__signal > span').forEach((item, index) => {
      setText(item.querySelector('strong'), values[index]);
      setText(item.querySelector('small'), text().cardLabels[index]);
    });
  }

  function coverageMarkup() {
    const c = text();
    const rows = STATE.chains.map(chain => `
      <tr>
        <td>${chain.name}</td>
        <td>${chain.locations}</td>
        <td><span class="price-coverage-status">${c.accepted}</span></td>
      </tr>`).join('');

    return `
      <div class="price-coverage-block" data-price-coverage>
        <div class="price-coverage__head">
          <h4>${c.coverageTitle}</h4>
          <p>${c.coverageBody}</p>
        </div>
        <div class="price-coverage-wrap" tabindex="0" aria-label="${c.coverageTitle}">
          <table class="price-coverage-table">
            <thead>
              <tr>
                <th>${c.chain}</th>
                <th>${c.locations}</th>
                <th>${c.status}</th>
              </tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </div>`;
  }

  function disableLegacyCrossSourceSample(detail) {
    const c = text();
    const sampleTitle = detail.querySelector('#price-sample-title');
    setText(sampleTitle, c.sampleTitle);
    const sampleSection = sampleTitle?.closest('.price-section');
    const sampleHead = sampleTitle?.closest('.price-section__head');
    setText(sampleHead?.querySelector('p:not(.price-eyebrow)'), c.sampleBody);

    const tableWrap = sampleSection?.querySelector('.price-table-wrap');
    if (tableWrap) {
      tableWrap.hidden = true;
      tableWrap.setAttribute('aria-hidden', 'true');
    }
    sampleSection?.querySelector('[data-price-ranking-legend]')?.remove();
    const note = sampleSection?.querySelector('.price-note');
    setText(note, c.sampleNote);
    if (note) note.dataset.comparisonSafety = 'fail-closed';
  }

  function sourceLabel(offer) {
    return offer.supermarket_id === 'la_colonia' ? 'La Colonia' : 'Walmart';
  }

  function renderSafeSample(detail) {
    if (!verifiedSample) return;
    const c = text();
    const sampleTitle = detail.querySelector('#price-sample-title');
    const sampleSection = sampleTitle?.closest('.price-section');
    const sampleHead = sampleTitle?.closest('.price-section__head');
    const tableWrap = sampleSection?.querySelector('.price-table-wrap');
    const table = tableWrap?.querySelector('table');
    if (!sampleTitle || !sampleSection || !tableWrap || !table) return;

    setText(sampleTitle, c.safeTitle);
    setText(sampleHead?.querySelector('p:not(.price-eyebrow)'), c.safeBody);

    const thead = document.createElement('thead');
    const headingRow = document.createElement('tr');
    [c.safeProduct, c.safeGtin, c.safeLaColonia, c.safeWalmart, c.safeBest, c.safeSavings].forEach(label => {
      const th = document.createElement('th');
      th.textContent = label;
      headingRow.appendChild(th);
    });
    thead.appendChild(headingRow);

    const tbody = document.createElement('tbody');
    verifiedSample.rows.forEach(row => {
      const tr = document.createElement('tr');
      const offers = Object.fromEntries(row.offers.map(offer => [offer.supermarket_id, offer]));

      const productCell = document.createElement('td');
      row.offers.forEach(offer => {
        const line = document.createElement('div');
        const label = document.createElement('strong');
        label.textContent = `${sourceLabel(offer)} · `;
        const name = document.createElement('span');
        name.textContent = offer.source_name;
        line.append(label, name);
        if (offer.source_presentation) {
          const presentation = document.createElement('small');
          presentation.textContent = ` · ${offer.source_presentation}`;
          line.appendChild(presentation);
        }
        productCell.appendChild(line);
      });
      tr.appendChild(productCell);

      const gtin = document.createElement('td');
      gtin.textContent = row.canonical_gtin;
      tr.appendChild(gtin);

      for (const supermarketId of ['la_colonia', 'walmart']) {
        const price = document.createElement('td');
        price.className = `price-number${offers[supermarketId].is_best_price ? ' is-best' : ''}`;
        price.textContent = money(offers[supermarketId].current_price);
        tr.appendChild(price);
      }

      const best = document.createElement('td');
      best.className = 'price-number price-best';
      best.textContent = money(row.best_price);
      tr.appendChild(best);

      const savings = document.createElement('td');
      savings.className = 'price-number';
      savings.textContent = money(row.savings_vs_highest);
      tr.appendChild(savings);
      tbody.appendChild(tr);
    });

    table.replaceChildren(thead, tbody);
    tableWrap.hidden = false;
    tableWrap.removeAttribute('aria-hidden');
    tableWrap.setAttribute('aria-label', c.safeTitle);
    const note = sampleSection.querySelector('.price-note');
    setText(note, c.safeNote);
    if (note) note.dataset.comparisonSafety = 'verified-strong-identity';
  }

  async function loadSafeSample() {
    if (sampleLoadStarted) return;
    sampleLoadStarted = true;
    try {
      const response = await fetch(new URL(SAFE_SAMPLE_URL, document.baseURI), {
        credentials: 'same-origin',
        cache: 'no-store'
      });
      if (!response.ok) return;
      const candidate = validatePublishedSample(await response.json());
      if (!candidate) return;
      verifiedSample = candidate;
      patchDetail();
    } catch (_) {
      // Fail closed: ausencia, JSON inválido o error de red mantienen la muestra oculta.
    }
  }

  function patchAnalyticalCopy(detail) {
    const c = text();
    const capabilities = detail.querySelectorAll('.price-capabilities .price-value');
    if (capabilities.length >= 3) setText(capabilities[2].querySelector('p'), c.matchingBody);
    const insightsTitle = detail.querySelector('#price-insights-title');
    const insightsHead = insightsTitle?.closest('.price-section__head');
    setText(insightsHead?.querySelector('p:not(.price-eyebrow)'), c.insightsBody);
  }

  function patchDetail() {
    const detail = document.querySelector('#price-project-view');
    if (!detail) return;

    const c = text();
    setText(detail.querySelector('.price-stage span'), c.stage);

    const scaleTitle = detail.querySelector('#price-scale-title');
    setText(scaleTitle, c.scaleTitle);
    const scaleHead = scaleTitle?.closest('.price-section__head');
    setText(scaleHead?.querySelector('p:not(.price-eyebrow)'), c.scaleBody);

    const values = ['6', '11', '58K+', '127K+'];
    detail.querySelectorAll('.price-kpis .price-kpi').forEach((item, index) => {
      setText(item.querySelector('strong'), values[index]);
      setText(item.querySelector('span'), c.scaleLabels[index]);
    });

    const scaleSection = scaleTitle?.closest('.price-section');
    scaleSection?.querySelector('[data-price-coverage]')?.remove();
    scaleSection?.insertAdjacentHTML('beforeend', coverageMarkup());

    disableLegacyCrossSourceSample(detail);
    if (verifiedSample) renderSafeSample(detail);
    patchAnalyticalCopy(detail);

    const roadmap = detail.querySelector('.price-roadmap__item.is-done span');
    setText(roadmap, c.roadmap);
  }

  function apply() {
    patchCard();
    patchDetail();
  }

  function mount() {
    apply();
    void loadSafeSample();
    window.PortfolioI18n?.onChange?.(apply);
    window.PricePortfolioCurrentState = STATE;
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount, { once: true });
  } else {
    mount();
  }
})();
