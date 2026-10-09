# Retail Price Intelligence Platform — Honduras

Plataforma de **Retail Price Intelligence** que captura, valida e historiza precios públicos de supermercados y los transforma en dos productos: analítica B2B reproducible para Power BI y la experiencia web B2C **Compra Inteligente**.

El alcance productivo incluye San Pedro Sula y Tegucigalpa, y Compra Inteligente se publica para ambas ciudades.

> **Estado al 2026-09-30:** el último corte público aceptado es del **2026-09-21**. La captura diaria está detenida por la cuota de lecturas de Turso y por un cambio en el sitio de Colonial. El detalle está en [`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md).
>
> **Repositorio:** desde el 2026-09-30 el proyecto vive en [`jchernandez-portfolio/precios-supermercados-sps`](https://github.com/jchernandez-portfolio/precios-supermercados-sps), separado del monorepo `Portafolio` con su historial.

## Compra Inteligente

La aplicación estática vive en [`b2c/`](b2c/) y consume únicamente datos publicados en `portfolio-data`. No consulta Turso ni sitios de supermercados desde el navegador y no hace matching en JavaScript. Es un **planificador de compra**, no una tienda ni un checkout. Se publica en el sitio del portafolio: https://jchernandez-portfolio.github.io/Portafolio/precios-supermercados-sps/b2c/

Tiene dos pestañas:

- **Análisis:** productos visibles y comparables, promociones activas, ahorro unitario observado, precios que bajaron o subieron, cobertura y "mejores precios" por supermercado, oportunidades del día y liderazgo por categoría.
- **Compra Inteligente:**
  - selector de ciudad (San Pedro Sula o Tegucigalpa), navegación por facetas y búsqueda;
  - matriz por supermercado o sucursal y selección manual de la oferta exacta;
  - cantidades y alta por lote;
  - `Mi Compra` guardada en el dispositivo por ciudad y agrupada por supermercado;
  - actualización explícita de precios sin sustitución silenciosa;
  - escenarios de canasta manual, de un solo supermercado y de mejor precio seguro;
  - historial resumido por oferta, exportación CSV/PDF y compartir por WhatsApp;
  - estado visible del último corte publicado.

Alcance público B2C:

| Ciudad | Supermercados / sucursales |
| --- | --- |
| San Pedro Sula | La Colonia, Supermercados Colonial, Walmart, PriceSmart, Comisariato Los Andes |
| Tegucigalpa | La Colonia, Walmart FFAA, Walmart El Sauce, PriceSmart Florencia, Paiz Multiplaza, Paiz Próceres |

Corte publicado vigente (**2026-09-21**):

| Métrica | San Pedro Sula | Tegucigalpa |
| --- | ---: | ---: |
| Filas visibles | 36,886 | 33,370 |
| Filas comparables | 2,612 | 8,321 |
| Ofertas fuente | 39,498 | 59,786 |

Las cifras son una medición del corte, no valores permanentes. El valor vigente y su historia están en `docs/PROJECT_STATE.md`.

**Visible no significa comparable.** Productos sin suficiente evidencia cross-retailer pueden mostrarse individualmente, pero no reciben ranking ni recomendación inventada.

## Retail Price Intelligence B2B

`rpi-business-mart/v1` materializa el producto analítico privado para Power BI con:

- comparación actual;
- histórico de precios;
- análisis promocional;
- canastas;
- cobertura;
- freshness;
- dimensiones de producto, retailer, ubicación, categoría y marca.

Los activos reproducibles de Power BI viven en [`powerbi/rpi/`](powerbi/rpi/): Power Query, DAX, relaciones, tema y especificación de nueve páginas.

La lógica crítica —matching, PCI, ranking, freshness, deltas e historia— permanece en Python. Power BI agrega y presenta; no vuelve a decidir identidad.

## Contratos RPI vigentes

| Contrato | Uso |
| --- | --- |
| `rpi-business-mart/v1` | B2B privado / Power BI |
| `rpi-consumer-mart/v2` | comparación analítica segura B2C |
| `rpi-consumer-catalog/v3` | navegación pública escalable, un catálogo por ciudad |
| `rpi-consumer-city-index/v1` | índice de ciudades publicadas (`v3/cities.json`) |
| `rpi-consumer-analysis/v1` | pestaña Análisis, un archivo por ciudad |
| `rpi-marts-manifest/v1` | integridad de marts |
| `rpi-consumer-catalog-manifest/v3` | integridad del catálogo público |

`precios-sps-publication/v1` y `precios-sps-static-bi-dataset/v1` permanecen como contratos legados/compatibilidad, no como arquitectura principal.

## Flujo de datos

```text
Sitios públicos
  ↓
Captura especializada por fuente
  ↓
Validación de ubicación + completitud
  ↓
Último dato válido / histórico compacto
  ↓
Turso
  ↓
Homologación conservadora
  ↓
Comparabilidad + freshness
  ↓
Python analytics
  ↓
├─ Business Mart v1 → Power BI
├─ Consumer Mart v2 → comparación/escenarios
├─ Consumer Catalog v3 (SPS y TGU) → Compra Inteligente
└─ Consumer Analysis v1 (SPS y TGU) → pestaña Análisis
```

La cobertura productiva general integra seis cadenas y once contextos. Eso no significa que todos los artículos puedan compararse entre sí.

## Precio e histórico

Se distinguen explícitamente:

```text
current_price
reported_regular_price
historical_previous_price
```

`current_price` es el precio efectivo observado usado para cálculos. `reported_regular_price` es sólo referencia declarada por la tienda y nunca se convierte en historia real.

Los periodos históricos sólo cambian cuando cambia un estado comercial relevante; no se crean snapshots diarios redundantes.

## Mi Compra — contrato monetario

```text
unit_price = current_price
line_total = unit_price * quantity
retailer_subtotal = sum(line_total)
grand_total = sum(retailer_subtotal)
```

No se agregan impuestos/ISV, delivery, service fees o membership fees inferidos. Un faltante no vale cero: deja el total dependiente incompleto.

## Operación recurrente

La información se actualiza **una vez al día**: la captura arranca a las 05:17 de Honduras (disparo puntual desde un Worker de Cloudflare, `edge/daily-trigger`; si no llega, el operador la arranca a las 08:17). Si alguna cadena falla, sólo esa cadena se reintenta a las 08:17 y a las 12:17, con un máximo de tres intentos. Luego se persiste en Turso y se publica una sola vez.

El workflow diario común cubre:

- La Colonia SPS + TGU;
- Colonial SPS;
- Walmart SPS + dos contextos TGU;
- PriceSmart SPS + TGU;
- Comisariato Los Andes SPS;
- Paiz en dos contextos TGU.

Cada cadena se captura de forma aislada. Una cadena sólo deja un handoff reutilizable después de superar sus validaciones; la persistencia global continúa siendo fail-closed y exige handoffs aceptados de todas las cadenas.

Si un run programado termina en fallo o timeout, el operador revisa automáticamente a las 08:17 y 12:17 de Honduras. Puede reejecutar únicamente los jobs fallidos y sus dependencias, reutilizando las capturas válidas de cadenas que ya terminaron bien. El límite es un intento inicial más hasta dos recuperaciones y nunca se crea un crawl nuevo cuando no existe un run programado del día.

Los avisos por correo de fallos pertenecen a las preferencias de notificación de la cuenta de GitHub; no requieren guardar credenciales de correo en este repositorio.

El estado operativo, último run y cualquier incidente vigente están únicamente en [`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md).

## Publicación

Después de una actualización aceptada:

```text
Turso
→ homologación derivada
→ exportación RPI read-only
→ validación de schema/scope/hash/secretos
→ publicación atómica en portfolio-data
```

Consumer Mart v2 y Consumer Catalog v3 son públicos. Business Mart v1 permanece privado.

Si la cadena derivada falla, se conserva el último corte público válido.

## Evidencia de scraping

El web scraping es una capacidad demostrada de la plataforma, pero no define por sí solo el producto. La evidencia histórica versionada de Comisariato Los Andes permanece en:

[`reports/comisariato-los-andes/2026-09-04-full/`](reports/comisariato-los-andes/2026-09-04-full/)

Metadatos públicos reducidos:

[`portfolio/scraping-proof.json`](portfolio/scraping-proof.json)

## Fuentes de verdad

- Estado actual: [`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md)
- Producto: [`docs/RPI-PRODUCT-SPEC.md`](docs/RPI-PRODUCT-SPEC.md)
- Arquitectura: [`docs/arquitectura.md`](docs/arquitectura.md)
- Data marts / catálogo: [`docs/RPI-DATA-MART-DICTIONARY.md`](docs/RPI-DATA-MART-DICTIONARY.md)
- Power BI: [`docs/BI-IMPLEMENTATION-GUIDE.md`](docs/BI-IMPLEMENTATION-GUIDE.md)
- Comparabilidad: [`docs/COMPARATOR-METHODOLOGY.md`](docs/COMPARATOR-METHODOLOGY.md)
- Publicación/legado: [`docs/PUBLICATION-DATA-DICTIONARY.md`](docs/PUBLICATION-DATA-DICTIONARY.md)
- Identidad de producto: [`docs/homologation/product-identity-standard-v1.md`](docs/homologation/product-identity-standard-v1.md)
- Presentación pública: [`docs/portfolio-showcase.md`](docs/portfolio-showcase.md)

## Principios

1. La fuente manda; no se inventan precio, ubicación o atributos.
2. Un run rechazado no sustituye el último estado comercial válido.
3. Homologar no equivale a autorizar comparación.
4. Marca + presentación no bastan para matching cross-retailer.
5. Freshness insuficiente bloquea ranking/PCI/recomendación nueva.
6. Power BI y Compra Inteligente consumen contratos derivados; no ejecutan scraping.
7. Los datos públicos no contienen secretos, RAW o colas privadas.
8. Empates y faltantes se conservan explícitamente.

## Reproducibilidad y pruebas

Desde la raíz del repositorio:

```bash
python -m pip check
python -m compileall precios-supermercados-sps/src precios-supermercados-sps/scripts
pytest precios-supermercados-sps/tests
```

El conteo exacto de pruebas y el estado operativo cambian con el proyecto y por eso se registran en GitHub/`PROJECT_STATE.md`, no como una cifra permanente en este README.
