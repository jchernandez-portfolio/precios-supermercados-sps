(() => {
  const PROJECT_ID = 'precios-supermercados';
  const REPO = 'https://github.com/jchernandez-portfolio/precios-supermercados-sps/tree/main/precios-supermercados-sps';
  const B2C_URL = 'precios-supermercados-sps/b2c/';
  const SOURCE_URL = 'https://comisariatolosandes.com/';
  const EVIDENCE_URL = 'https://github.com/jchernandez-portfolio/precios-supermercados-sps/tree/main/precios-supermercados-sps/reports/comisariato-los-andes/2026-09-04-full';
  const CODE_URL = 'https://github.com/jchernandez-portfolio/precios-supermercados-sps/tree/main/precios-supermercados-sps/src/precios_supermercados';

  const SCRAPING_PROOF = Object.freeze({
    capturedAtUtc: '2026-09-04T01:44:35.172709Z',
    source: 'Comisariato Los Andes',
    city: 'San Pedro Sula',
    productsWithPrice: 6646,
    promotions: 120,
    artifactId: 9920279680,
    snapshotSha256: 'a1fe77e3c3132c96c01f7cd792084d47ae25fbb09e3eb69fb67b230d5f09f9fc'
  });

  const translations = {
    es: {
      'card.badge': 'Retail Price Intelligence · proyecto principal',
      'card.preview.sources': 'supermercados',
      'card.preview.locations': 'ubicaciones monitoreadas',
      'card.preview.products': 'productos registrados',
      'card.preview.history': 'registros históricos',
      'card.previewLabel': 'Abrir plataforma de inteligencia de precios minoristas',
      'card.title': 'Retail Price Intelligence para supermercados',
      'card.body': 'Plataforma que convierte precios públicos validados en comparaciones seguras, histórico y una experiencia B2C para planificar compras por supermercado.',
      'card.open': 'Explorar proyecto',
      'card.b2c': 'Ver resultado',
      'card.repo': 'Ver código',
      'dialog.close': 'Cerrar',
      'dialog.back': 'Volver a proyectos',
      'hero.kicker': 'Retail Price Intelligence · datos reales · producto digital',
      'hero.title': 'Inteligencia de precios para comprar y decidir mejor',
      'hero.question': 'De catálogos públicos a comparaciones confiables, historia de precios y una lista de compra accionable.',
      'hero.body': 'La plataforma integra adquisición, calidad, homologación y marts analíticos. Compra Inteligente lleva esa evidencia al consumidor sin exponer la base productiva ni inventar precios faltantes.',
      'hero.stageTitle': 'Escala actual',
      'hero.stageBody': '6 supermercados integrados, 11 ubicaciones, 58K+ productos registrados y 127K+ periodos históricos de precio.',
      'flow.title': 'Cómo llegan los datos',
      'flow.body': 'El recorrido completo deja claro que el proyecto no parte de un archivo preparado: empieza en sitios web públicos.',
      'flow.web.title': 'Sitios web',
      'flow.web.body': 'Catálogos públicos de supermercados con productos y precios.',
      'flow.scrape.title': 'Web Scraping',
      'flow.scrape.body': 'Python, Playwright y clientes HTTP extraen la información necesaria.',
      'flow.validate.title': 'Validación',
      'flow.validate.body': 'La captura se revisa antes de aceptar cambios en los datos.',
      'flow.history.title': 'Histórico',
      'flow.history.body': 'El estado actual y las variaciones comerciales quedan conservados.',
      'flow.analysis.title': 'Análisis',
      'flow.analysis.body': 'Los datos quedan listos para comparaciones seguras, tendencias y dashboards.',
      'proof.title': 'Extracción web comprobable',
      'proof.body': 'Una captura real aceptada permite seguir la evidencia desde la página pública consultada hasta el resultado guardado en GitHub.',
      'proof.status': 'Captura aceptada',
      'proof.sourceLabel': 'Fuente web',
      'proof.dateLabel': 'Captura',
      'proof.productsLabel': 'Productos con precio',
      'proof.promotionsLabel': 'Promociones detectadas',
      'proof.artifactLabel': 'Evidencia de ejecución',
      'proof.artifact': 'Artifact de GitHub Actions #9920279680',
      'proof.sourceAction': 'Abrir página de origen',
      'proof.evidenceAction': 'Ver evidencia en GitHub',
      'proof.codeAction': 'Ver código de extracción',
      'proof.note': 'Snapshot SHA-256: a1fe77e3…f09f9fc. No se publican cookies, credenciales ni el dataset productivo completo.',
      'scale.title': 'Cobertura productiva actual',
      'scale.body': 'Seis cadenas y once contextos productivos demostrados; la cobertura de scraping permanece separada de la cobertura de comparación cross-source.',
      'scale.sources': 'supermercados',
      'scale.locations': 'ubicaciones monitoreadas',
      'scale.products': 'productos registrados',
      'scale.history': 'registros históricos de precio',
      'sample.title': 'Comparaciones cross-source con identidad fuerte',
      'sample.body': 'La comparación pública se habilita sólo después de validar identidad fuerte y consistencia comercial. Marca y presentación nunca bastan por sí solas.',
      'sample.product': 'Producto verificado',
      'sample.gtin': 'GTIN',
      'sample.laColonia': 'La Colonia · SPS',
      'sample.walmart': 'Walmart · SPS',
      'sample.best': 'Mejor precio',
      'sample.savings': 'Ahorro vs. mayor',
      'sample.note': 'Fail-closed: la tabla permanece oculta hasta que el dataset público seguro supera schema, política, alcance e identidad. El navegador no crea equivalencias.',
      'cap.title': 'Qué demuestra este proyecto',
      'cap.body': 'La extracción es sólo el primer paso. El proyecto demuestra un flujo completo desde la web hasta datos listos para análisis.',
      'cap.scrape.title': 'Web Scraping',
      'cap.scrape.body': 'Extracción automatizada desde sitios web reales y catálogos públicos.',
      'cap.auto.title': 'Automatización',
      'cap.auto.body': 'Ejecuciones controladas con GitHub Actions y procesos repetibles.',
      'cap.match.title': 'Homologación de productos',
      'cap.match.body': 'La identidad fuerte y la coherencia comercial autorizan comparaciones; la similitud textual sólo puede apoyar revisión.',
      'cap.history.title': 'Análisis histórico',
      'cap.history.body': 'Cada estado aceptado alimenta un histórico útil para estudiar cambios de precio.',
      'insights.title': 'Qué ya permiten ver los datos',
      'insights.body': 'Los hallazgos usan identidades demostradas. Las comparaciones entre cadenas quedan cerradas cuando la identidad comercial es ambigua.',
      'insights.one.value': '255',
      'insights.one.body': 'artículos presentaron diferencias comerciales entre dos tiendas de una misma cadena, dentro de 12,042 artículos comparables.',
      'insights.two.value': '115',
      'insights.two.body': 'artículos tuvieron distinto precio actual entre dos clubes, dentro de 5,129 productos con precio en ambos.',
      'insights.three.value': '120',
      'insights.three.body': 'promociones fueron verificadas en la captura comprobable de 6,646 productos con precio.',
      'value.title': 'Por qué este proyecto importa',
      'value.body': 'Convierte una tarea repetitiva de revisión web en una fuente estructurada para responder preguntas de negocio y apoyar compras.',
      'value.manual.title': 'Reduce trabajo manual',
      'value.manual.body': 'Centraliza miles de productos que de otra manera tendrían que revisarse uno por uno.',
      'value.changes.title': 'Hace visibles las diferencias',
      'value.changes.body': 'Permite observar variaciones de precio y promociones cuando existe una comparación válida.',
      'value.history.title': 'Construye contexto',
      'value.history.body': 'El histórico permite pasar de “cuánto cuesta hoy” a entender cómo cambia un precio con el tiempo.',
      'roadmap.title': 'De scraping a inteligencia de precios',
      'roadmap.body': 'La adquisición, histórico, comparación segura y superficies B2B/B2C ya forman un producto integrado.',
      'roadmap.01.status': 'Completado',
      'roadmap.01.title': 'Extracción web',
      'roadmap.01.body': 'Seis cadenas productivas integradas bajo una estructura común.',
      'roadmap.02.status': 'Activo',
      'roadmap.02.title': 'Historial de precios',
      'roadmap.02.body': 'Se conservan estados aceptados y cambios comerciales.',
      'roadmap.03.status': 'Activo',
      'roadmap.03.title': 'Comparaciones verificadas',
      'roadmap.03.body': 'El gate fail-closed publica sólo equivalencias con identidad fuerte y consistencia comercial.',
      'roadmap.04.status': 'Completado',
      'roadmap.04.title': 'Compra Inteligente B2C',
      'roadmap.04.body': 'Comparación por facetas, historial y lista de compra por supermercado.',
      'roadmap.05.status': 'Activo',
      'roadmap.05.title': 'Inteligencia comercial B2B',
      'roadmap.05.body': 'Business Mart y activos reproducibles para análisis en Power BI.',
      'tech.title': 'Tecnología detrás del proyecto',
      'tech.body': 'La implementación técnica queda al final para que primero se entienda el resultado.',
      'tech.scrape.title': 'Python · Playwright · Requests',
      'tech.scrape.body': 'Navegación, extracción y consumo de superficies públicas.',
      'tech.storage.title': 'Turso / SQLite',
      'tech.storage.body': 'Estado actual, histórico y trazabilidad de ejecuciones.',
      'tech.actions.title': 'GitHub Actions',
      'tech.actions.body': 'Automatización, evidencia y controles antes de integrar y publicar datos.',
      'tech.tests.title': 'Pruebas automatizadas',
      'tech.tests.body': 'Validan reglas de datos, persistencia, publicación y seguridad.',
      'end.repo': 'Ver implementación en GitHub',
      'end.b2c': 'Usar Compra Inteligente'
    },
    en: {
      'card.badge': 'Retail Price Intelligence · featured project',
      'card.preview.sources': 'retail chains',
      'card.preview.locations': 'monitored locations',
      'card.preview.products': 'products recorded',
      'card.preview.history': 'historical records',
      'card.previewLabel': 'Open retail price intelligence platform',
      'card.title': 'Retail Price Intelligence for grocery',
      'card.body': 'A platform that turns validated public prices into safe comparisons, price history, and a B2C shopping-planning experience.',
      'card.open': 'Explore project',
      'card.b2c': 'View result',
      'card.repo': 'View code',
      'dialog.close': 'Close',
      'dialog.back': 'Back to projects',
      'hero.kicker': 'Retail Price Intelligence · real data · digital product',
      'hero.title': 'Price intelligence for better shopping and decisions',
      'hero.question': 'From public catalogs to trustworthy comparisons, price history, and an actionable shopping list.',
      'hero.body': 'The platform integrates acquisition, quality, product resolution, and analytical marts. Smart Shopping brings that evidence to consumers without exposing the production database or inventing missing prices.',
      'hero.stageTitle': 'Current scale',
      'hero.stageBody': '6 integrated retail chains, 11 locations, 58K+ products recorded, and 127K+ historical price periods.',
      'flow.title': 'How the data gets here',
      'flow.body': 'The full path makes it clear that the project does not begin with a prepared file: it starts on public websites.',
      'flow.web.title': 'Websites',
      'flow.web.body': 'Public grocery catalogs containing products and prices.',
      'flow.scrape.title': 'Web Scraping',
      'flow.scrape.body': 'Python, Playwright, and HTTP clients extract the required information.',
      'flow.validate.title': 'Validation',
      'flow.validate.body': 'Each capture is checked before data changes are accepted.',
      'flow.history.title': 'History',
      'flow.history.body': 'Current state and commercial changes are preserved.',
      'flow.analysis.title': 'Analysis',
      'flow.analysis.body': 'The data is ready for safe comparisons, trends, and dashboards.',
      'proof.title': 'Verifiable web extraction',
      'proof.body': 'A real accepted capture links the public page that was queried with the result preserved in GitHub.',
      'proof.status': 'Accepted capture',
      'proof.sourceLabel': 'Web source',
      'proof.dateLabel': 'Captured',
      'proof.productsLabel': 'Products with price',
      'proof.promotionsLabel': 'Promotions detected',
      'proof.artifactLabel': 'Execution evidence',
      'proof.artifact': 'GitHub Actions artifact #9920279680',
      'proof.sourceAction': 'Open source website',
      'proof.evidenceAction': 'View evidence on GitHub',
      'proof.codeAction': 'View extraction code',
      'proof.note': 'Snapshot SHA-256: a1fe77e3…f09f9fc. Cookies, credentials, and the full production dataset are not published.',
      'scale.title': 'Current production coverage',
      'scale.body': 'Six chains and eleven demonstrated production contexts; scraping coverage remains separate from cross-source comparison coverage.',
      'scale.sources': 'retail chains',
      'scale.locations': 'monitored locations',
      'scale.products': 'products recorded',
      'scale.history': 'historical price records',
      'sample.title': 'Cross-source comparisons with strong identity',
      'sample.body': 'Public comparison is enabled only after strong identity and commercial consistency are validated. Brand and presentation are never sufficient on their own.',
      'sample.product': 'Verified product',
      'sample.gtin': 'GTIN',
      'sample.laColonia': 'La Colonia · SPS',
      'sample.walmart': 'Walmart · SPS',
      'sample.best': 'Best price',
      'sample.savings': 'Savings vs. highest',
      'sample.note': 'Fail-closed: the table remains hidden until the safe public dataset passes schema, policy, scope, and identity validation. The browser does not create equivalences.',
      'cap.title': 'What this project demonstrates',
      'cap.body': 'Extraction is only the first step. The project demonstrates an end-to-end path from the web to analysis-ready data.',
      'cap.scrape.title': 'Web Scraping',
      'cap.scrape.body': 'Automated extraction from real websites and public catalogs.',
      'cap.auto.title': 'Automation',
      'cap.auto.body': 'Controlled GitHub Actions runs and repeatable processes.',
      'cap.match.title': 'Product resolution',
      'cap.match.body': 'Strong identity and commercial consistency authorize comparisons; textual similarity can only support review.',
      'cap.history.title': 'Historical analysis',
      'cap.history.body': 'Every accepted state contributes to history that can be used to study price changes.',
      'insights.title': 'What the data can already reveal',
      'insights.body': 'Findings use proven identities. Cross-source comparisons remain closed whenever commercial identity is ambiguous.',
      'insights.one.value': '255',
      'insights.one.body': 'items showed commercial differences between two stores from the same chain, among 12,042 comparable items.',
      'insights.two.value': '115',
      'insights.two.body': 'items had a different current price between two clubs, among 5,129 products priced in both.',
      'insights.three.value': '120',
      'insights.three.body': 'promotions were verified in the demonstrable capture containing 6,646 priced products.',
      'value.title': 'Why this project matters',
      'value.body': 'It turns repetitive web checking into structured information that can answer business questions and support shopping decisions.',
      'value.manual.title': 'Reduces manual work',
      'value.manual.body': 'Centralizes thousands of products that would otherwise need to be checked individually.',
      'value.changes.title': 'Makes differences visible',
      'value.changes.body': 'It can expose price variations and promotions when the comparison is valid.',
      'value.history.title': 'Builds context',
      'value.history.body': 'History moves the question beyond “what does it cost today?” toward understanding change over time.',
      'roadmap.title': 'From scraping to price intelligence',
      'roadmap.body': 'Acquisition, history, safe comparison, and B2B/B2C surfaces now form one integrated product.',
      'roadmap.01.status': 'Completed',
      'roadmap.01.title': 'Web extraction',
      'roadmap.01.body': 'Six production retail chains integrated under one shared structure.',
      'roadmap.02.status': 'Active',
      'roadmap.02.title': 'Price history',
      'roadmap.02.body': 'Accepted states and commercial changes are preserved.',
      'roadmap.03.status': 'Active',
      'roadmap.03.title': 'Verified comparisons',
      'roadmap.03.body': 'The fail-closed gate publishes only equivalences with strong identity and commercial consistency.',
      'roadmap.04.status': 'Completed',
      'roadmap.04.title': 'B2C Smart Shopping',
      'roadmap.04.body': 'Faceted comparisons, history, and shopping lists grouped by retailer.',
      'roadmap.05.status': 'Active',
      'roadmap.05.title': 'B2B commercial intelligence',
      'roadmap.05.body': 'Business Mart and reproducible assets for Power BI analysis.',
      'tech.title': 'Technology behind the project',
      'tech.body': 'Technical implementation comes last so the outcome is clear first.',
      'tech.scrape.title': 'Python · Playwright · Requests',
      'tech.scrape.body': 'Navigation, extraction, and public-surface consumption.',
      'tech.storage.title': 'Turso / SQLite',
      'tech.storage.body': 'Current state, history, and execution traceability.',
      'tech.actions.title': 'GitHub Actions',
      'tech.actions.body': 'Automation, evidence, and controls before data integration and publication.',
      'tech.tests.title': 'Automated tests',
      'tech.tests.body': 'Protect data, persistence, publication, and security rules.',
      'end.repo': 'View implementation on GitHub',
      'end.b2c': 'Use Smart Shopping'
    }
  };

  function i18n() { return window.PortfolioI18n; }
  function t(key) { return i18n()?.t(`prices.${key}`) ?? translations.es[key] ?? key; }

  function registerTranslations() {
    const api = i18n();
    if (!api) throw new Error('El módulo de precios requiere PortfolioI18n.');
    api.registerCatalog('prices', translations);
  }

  function card() {
    return `
      <article class="card project-card price-card fade-in is-visible">
        <button class="price-card__preview" data-price-open type="button" aria-label="${t('card.previewLabel')}">
          <span class="price-card__signal" aria-hidden="true">
            <span><strong>6</strong><small>${t('card.preview.sources')}</small></span>
            <span><strong>11</strong><small>${t('card.preview.locations')}</small></span>
            <span><strong>58K+</strong><small>${t('card.preview.products')}</small></span>
            <span><strong>127K+</strong><small>${t('card.preview.history')}</small></span>
          </span>
        </button>
        <div class="price-card__content">
          <p class="price-card__badge">${t('card.badge')}</p>
          <div class="tags">
            <span class="tag">Retail Intelligence</span>
            <span class="tag">Python</span>
            <span class="tag">Playwright</span>
            <span class="tag">GitHub Actions</span>
          </div>
          <h3>${t('card.title')}</h3>
          <p>${t('card.body')}</p>
          <div class="price-card__actions">
            <button class="text-link" data-price-open type="button">${t('card.open')}</button>
            <a class="text-link" href="${B2C_URL}">${t('card.b2c')}</a>
            <a class="text-link text-link--muted" href="${REPO}" target="_blank" rel="noopener noreferrer">${t('card.repo')}</a>
          </div>
        </div>
      </article>`;
  }

  function view() {
    return `
      <dialog class="price-view" id="price-project-view" aria-labelledby="price-title">
        <div class="price-view__top">
          <span class="price-view__brand">JC HERNANDEZ</span>
          <button class="price-close" data-price-close type="button">${t('dialog.close')}</button>
        </div>
        <div class="price-view__body">
          <header class="price-hero">
            <div>
              <p class="price-kicker">${t('hero.kicker')}</p>
              <h2 id="price-title">${t('hero.title')}</h2>
              <p class="price-hero__question">${t('hero.question')}</p>
              <p>${t('hero.body')}</p>
            </div>
            <aside class="price-stage" aria-label="${t('hero.stageTitle')}">
              <strong>${t('hero.stageTitle')}</strong>
              <span>${t('hero.stageBody')}</span>
            </aside>
          </header>

          <section class="price-section" aria-labelledby="price-flow-title">
            <div class="price-section__head">
              <p class="price-eyebrow">01</p>
              <h3 id="price-flow-title">${t('flow.title')}</h3>
              <p>${t('flow.body')}</p>
            </div>
            <div class="price-flow">
              <article class="price-flow__step"><strong>${t('flow.web.title')}</strong><span>${t('flow.web.body')}</span></article>
              <article class="price-flow__step is-scraping"><strong>${t('flow.scrape.title')}</strong><span>${t('flow.scrape.body')}</span></article>
              <article class="price-flow__step"><strong>${t('flow.validate.title')}</strong><span>${t('flow.validate.body')}</span></article>
              <article class="price-flow__step"><strong>${t('flow.history.title')}</strong><span>${t('flow.history.body')}</span></article>
              <article class="price-flow__step"><strong>${t('flow.analysis.title')}</strong><span>${t('flow.analysis.body')}</span></article>
            </div>
          </section>

          <section class="price-section price-proof-section" aria-labelledby="price-proof-title">
            <div class="price-section__head">
              <p class="price-eyebrow">02</p>
              <h3 id="price-proof-title">${t('proof.title')}</h3>
              <p>${t('proof.body')}</p>
            </div>
            <div class="price-proof">
              <div class="price-proof__status"><span class="price-proof__dot" aria-hidden="true"></span><strong>${t('proof.status')}</strong></div>
              <dl class="price-proof__grid">
                <div><dt>${t('proof.sourceLabel')}</dt><dd>${SCRAPING_PROOF.source}</dd></div>
                <div><dt>${t('proof.dateLabel')}</dt><dd>4 Sep 2026 · 01:44 UTC</dd></div>
                <div><dt>${t('proof.productsLabel')}</dt><dd>${SCRAPING_PROOF.productsWithPrice.toLocaleString('en-US')}</dd></div>
                <div><dt>${t('proof.promotionsLabel')}</dt><dd>${SCRAPING_PROOF.promotions}</dd></div>
                <div class="price-proof__wide"><dt>${t('proof.artifactLabel')}</dt><dd>${t('proof.artifact')}</dd></div>
              </dl>
              <div class="price-proof__actions">
                <a class="price-link" href="${SOURCE_URL}" target="_blank" rel="noopener noreferrer">${t('proof.sourceAction')}</a>
                <a class="price-link" href="${EVIDENCE_URL}" target="_blank" rel="noopener noreferrer">${t('proof.evidenceAction')}</a>
                <a class="price-link" href="${CODE_URL}" target="_blank" rel="noopener noreferrer">${t('proof.codeAction')}</a>
              </div>
              <p class="price-note">${t('proof.note')}</p>
            </div>
          </section>

          <section class="price-section" aria-labelledby="price-scale-title">
            <div class="price-section__head">
              <p class="price-eyebrow">03</p>
              <h3 id="price-scale-title">${t('scale.title')}</h3>
              <p>${t('scale.body')}</p>
            </div>
            <div class="price-kpis">
              <div class="price-kpi"><strong>6</strong><span>${t('scale.sources')}</span></div>
              <div class="price-kpi"><strong>11</strong><span>${t('scale.locations')}</span></div>
              <div class="price-kpi"><strong>58K+</strong><span>${t('scale.products')}</span></div>
              <div class="price-kpi"><strong>127K+</strong><span>${t('scale.history')}</span></div>
            </div>
          </section>

          <section class="price-section" aria-labelledby="price-sample-title">
            <div class="price-section__head">
              <p class="price-eyebrow">04</p>
              <h3 id="price-sample-title">${t('sample.title')}</h3>
              <p>${t('sample.body')}</p>
            </div>
            <div class="price-table-wrap" tabindex="0" aria-label="${t('sample.title')}" hidden aria-hidden="true">
              <table class="price-table">
                <thead><tr>
                  <th>${t('sample.product')}</th>
                  <th>${t('sample.gtin')}</th>
                  <th>${t('sample.laColonia')}</th>
                  <th>${t('sample.walmart')}</th>
                  <th>${t('sample.best')}</th>
                  <th>${t('sample.savings')}</th>
                </tr></thead>
                <tbody></tbody>
              </table>
            </div>
            <p class="price-note" data-comparison-safety="fail-closed">${t('sample.note')}</p>
          </section>

          <section class="price-section" aria-labelledby="price-cap-title">
            <div class="price-section__head">
              <p class="price-eyebrow">05</p>
              <h3 id="price-cap-title">${t('cap.title')}</h3>
              <p>${t('cap.body')}</p>
            </div>
            <div class="price-capabilities">
              <article class="price-value"><h4>${t('cap.scrape.title')}</h4><p>${t('cap.scrape.body')}</p></article>
              <article class="price-value"><h4>${t('cap.auto.title')}</h4><p>${t('cap.auto.body')}</p></article>
              <article class="price-value"><h4>${t('cap.match.title')}</h4><p>${t('cap.match.body')}</p></article>
              <article class="price-value"><h4>${t('cap.history.title')}</h4><p>${t('cap.history.body')}</p></article>
            </div>
          </section>

          <section class="price-section" aria-labelledby="price-insights-title">
            <div class="price-section__head">
              <p class="price-eyebrow">06</p>
              <h3 id="price-insights-title">${t('insights.title')}</h3>
              <p>${t('insights.body')}</p>
            </div>
            <div class="price-insights">
              <article class="price-insight"><strong>${t('insights.one.value')}</strong><p>${t('insights.one.body')}</p></article>
              <article class="price-insight"><strong>${t('insights.two.value')}</strong><p>${t('insights.two.body')}</p></article>
              <article class="price-insight"><strong>${t('insights.three.value')}</strong><p>${t('insights.three.body')}</p></article>
            </div>
          </section>

          <section class="price-section" aria-labelledby="price-value-title">
            <div class="price-section__head">
              <p class="price-eyebrow">07</p>
              <h3 id="price-value-title">${t('value.title')}</h3>
              <p>${t('value.body')}</p>
            </div>
            <div class="price-value-grid">
              <article class="price-value"><h4>${t('value.manual.title')}</h4><p>${t('value.manual.body')}</p></article>
              <article class="price-value"><h4>${t('value.changes.title')}</h4><p>${t('value.changes.body')}</p></article>
              <article class="price-value"><h4>${t('value.history.title')}</h4><p>${t('value.history.body')}</p></article>
            </div>
          </section>

          <section class="price-section" aria-labelledby="price-roadmap-title">
            <div class="price-section__head">
              <p class="price-eyebrow">08</p>
              <h3 id="price-roadmap-title">${t('roadmap.title')}</h3>
              <p>${t('roadmap.body')}</p>
            </div>
            <div class="price-roadmap">
              <article class="price-roadmap__item is-done"><b>01 · ${t('roadmap.01.status')}</b><strong>${t('roadmap.01.title')}</strong><span>${t('roadmap.01.body')}</span></article>
              <article class="price-roadmap__item is-active"><b>02 · ${t('roadmap.02.status')}</b><strong>${t('roadmap.02.title')}</strong><span>${t('roadmap.02.body')}</span></article>
              <article class="price-roadmap__item is-active"><b>03 · ${t('roadmap.03.status')}</b><strong>${t('roadmap.03.title')}</strong><span>${t('roadmap.03.body')}</span></article>
              <article class="price-roadmap__item is-done"><b>04 · ${t('roadmap.04.status')}</b><strong>${t('roadmap.04.title')}</strong><span>${t('roadmap.04.body')}</span></article>
              <article class="price-roadmap__item is-active"><b>05 · ${t('roadmap.05.status')}</b><strong>${t('roadmap.05.title')}</strong><span>${t('roadmap.05.body')}</span></article>
            </div>
          </section>

          <section class="price-section" aria-labelledby="price-tech-title">
            <div class="price-section__head">
              <p class="price-eyebrow">09</p>
              <h3 id="price-tech-title">${t('tech.title')}</h3>
              <p>${t('tech.body')}</p>
            </div>
            <div class="price-tech">
              <article class="price-tech__item"><strong>${t('tech.scrape.title')}</strong><p>${t('tech.scrape.body')}</p></article>
              <article class="price-tech__item"><strong>${t('tech.storage.title')}</strong><p>${t('tech.storage.body')}</p></article>
              <article class="price-tech__item"><strong>${t('tech.actions.title')}</strong><p>${t('tech.actions.body')}</p></article>
              <article class="price-tech__item"><strong>${t('tech.tests.title')}</strong><p>${t('tech.tests.body')}</p></article>
            </div>
            <div class="price-end-actions">
              <button class="price-back" data-price-close type="button">${t('dialog.back')}</button>
              <a class="price-link" href="${B2C_URL}">${t('end.b2c')}</a>
              <a class="price-link" href="${REPO}" target="_blank" rel="noopener noreferrer">${t('end.repo')}</a>
            </div>
          </section>
        </div>
      </dialog>`;
  }

  function templateElement(markup) {
    const template = document.createElement('template');
    template.innerHTML = markup.trim();
    return template.content.firstElementChild;
  }

  function setup({ card: cardElement, detail }) {
    if (!(detail instanceof HTMLDialogElement)) throw new Error('El detalle de precios debe montarse como dialog nativo.');
    let opener = null;

    function open(event) {
      opener = event.currentTarget;
      if (!detail.open) detail.showModal();
      document.body.classList.add('is-locked');
      detail.scrollTop = 0;
      detail.querySelector('[data-price-close]')?.focus();
    }

    function close() { if (detail.open) detail.close(); }
    function restoreAfterClose() {
      document.body.classList.remove('is-locked');
      if (opener?.isConnected) opener.focus();
    }

    cardElement.addEventListener('click', event => {
      const control = event.target.closest('[data-price-open]');
      if (control && cardElement.contains(control)) open({ currentTarget: control });
    });
    detail.addEventListener('click', event => { if (event.target.closest('[data-price-close]')) close(); });
    detail.addEventListener('close', restoreAfterClose);
    detail.addEventListener('cancel', () => document.body.classList.remove('is-locked'));

    i18n().onChange(() => {
      const wasOpen = detail.open;
      const freshCard = templateElement(card());
      const freshDetail = templateElement(view());
      cardElement.innerHTML = freshCard.innerHTML;
      detail.innerHTML = freshDetail.innerHTML;
      if (wasOpen) {
        opener = cardElement.querySelector('[data-price-open]');
        detail.querySelector('[data-price-close]')?.focus();
      }
    });
  }

  function mount() {
    if (!window.PortfolioProjects || !i18n()) {
      console.error('No se encontraron las dependencias del proyecto Precios de Supermercados.');
      return;
    }
    registerTranslations();
    window.PortfolioProjects.register({ id: PROJECT_ID, cardHtml: card(), detailHtml: view(), setup });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount, { once: true });
  else mount();
})();