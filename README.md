# Súper Compras — precios de supermercados de Honduras

[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](precios-supermercados-sps/pyproject.toml)
[![SQLite · Turso](https://img.shields.io/badge/SQLite-Turso%20(libSQL)-003B57?logo=sqlite&logoColor=white)](precios-supermercados-sps/docs/modelo-datos.md)
[![Pruebas](https://github.com/jchernandez-portfolio/precios-supermercados-sps/actions/workflows/precios-supermercados-sps-tests.yml/badge.svg?branch=main)](https://github.com/jchernandez-portfolio/precios-supermercados-sps/actions/workflows/precios-supermercados-sps-tests.yml)
[![GitHub Actions](https://img.shields.io/badge/GitHub%20Actions-captura%20diaria-2088FF?logo=githubactions&logoColor=white)](.github/workflows)
[![Claude AI](https://img.shields.io/badge/Claude%20AI-visi%C3%B3n%20%2B%20b%C3%BAsqueda%20web-D97757?logo=anthropic&logoColor=white)](precios-supermercados-sps/docs/revision-tamanos.md)
[![Sin dependencias en el sitio](https://img.shields.io/badge/web%20app-JS%20sin%20framework%20%C2%B7%20PWA-C6F432?labelColor=1C1F1D)](precios-supermercados-sps/super-compras/)

Captura, validación, homologación y publicación diaria de precios públicos de seis cadenas de supermercados en San Pedro Sula y Tegucigalpa, con la web app **Súper Compras** para compararlos y saber en qué súper conviene hacer la compra.

- **App:** https://jchernandez-portfolio.github.io/precios-supermercados-sps/super-compras/
- **Caso de estudio:** https://jchernandez-portfolio.github.io/Portafolio/precios-supermercados-sps/portfolio/caso/
- **Código y documentación:** [`precios-supermercados-sps/`](precios-supermercados-sps/) — detalle en [`precios-supermercados-sps/README.md`](precios-supermercados-sps/README.md).
- **Ramas de datos:** `portfolio-data` (catálogo público), `product-images` (fotos), `calidad-datos` (registro público de errores), `revision-tamanos` (cola de revisión).
- **Portafolio:** https://jchernandez-portfolio.github.io/Portafolio/

<p align="center">
  <img src="precios-supermercados-sps/docs/capturas/super-compras-inicio.webp" alt="Inicio: ofertas del día y precio en cada súper" width="200">
  <img src="precios-supermercados-sps/docs/capturas/super-compras-busqueda.webp" alt="Búsqueda con tamaño y precio por unidad" width="200">
  <img src="precios-supermercados-sps/docs/capturas/super-compras-ficha.webp" alt="Ficha: precio en cada supermercado, el más barato marcado" width="200">
  <img src="precios-supermercados-sps/docs/capturas/super-compras-ofertas.webp" alt="Ofertas del día" width="200">
</p>

## Estado del proyecto

Corte del **18 de noviembre de 2026**. 🟢 funciona solo · 🟡 funciona con trabajo pendiente · 🔴 bloqueado.

| Pieza | Estado | Avance | Detalle |
| --- | :---: | --- | --- |
| Scrapers (6 cadenas, 11 sucursales) | 🟢 | `██████████` 100 % | Corte diario 05:17 con reintento por cadena; persistencia parcial si una falla. |
| Base de datos (Turso / libSQL) | 🟢 | `██████████` 100 % | Último dato válido + histórico compacto; los workflows sólo leen salvo los pasos de guardado. |
| Motor de IA (Claude, API de Anthropic) | 🟡 | `████████░░` 80 % | Lee las fotos y busca el peso oficial en la web; aprueba solo lo verificado. Falta la corrida masiva sobre la cola. |
| Panel de revisión de tamaños | 🟡 | `███░░░░░░░` 29 % | 284 de 980 códigos resueltos (216 por la IA, 68 por una persona); 696 en cola (244 empates, 452 atípicos). |
| Frontend Súper Compras | 🟢 | `██████████` 100 % | Diseño Lima, PWA, escáner propio, esqueletos de carga; CSP estricta y cero dependencias. |
| PriceSmart: códigos de barras | 🔴 | `█░░░░░░░░░` 2 % | De 1,823 tarjetas sólo 30 traen GTIN; la foto del catálogo es el frente del empaque. El código está en la galería (foto trasera): falta capturarla. |

## Cómo funciona

```mermaid
flowchart LR
    S["🛒 Scrapers<br/>6 cadenas · 11 sucursales"] -->|captura validada| T[("Turso<br/>products · price_history")]
    T -->|refresco de homologación| H["Homologación<br/>GTIN · GS1 · maestro"]
    H -->|tamaños en conflicto| Q["Cola de revisión<br/>cola.json"]
    Q -->|fotos R2| G["Claude<br/>visión + búsqueda web"]
    G -->|Ley de la Mayoría o fuente citada| A{"¿Verificado?"}
    A -->|sí| D[("size_decisions<br/>engine:claude_auto")]
    A -->|no| P["Panel #/admin/revision<br/>decide una persona"]
    P -->|human:*| D
    D -->|siguiente refresco| H
    H -->|exportación read-only| J["JSON estático<br/>rama portfolio-data"]
    J --> W["🌐 Súper Compras<br/>GitHub Pages"]
```

Revisión de un código con tamaños distintos (por ejemplo Heinz 566 g contra 20 oz):

```mermaid
sequenceDiagram
    autonumber
    participant R as Refresco de homologación
    participant C as Workflow de la cola
    participant G as Claude
    participant T as Turso
    participant P as Panel (persona)
    R->>C: terminó (workflow_run)
    C->>T: SELECT grupos GTIN en conflicto
    C->>G: fotos de cada oferta (visión)
    G-->>C: «NET WT 20 OZ (566 g)» por foto
    alt todas las fotos coinciden y es el tamaño de la mayoría
        C->>T: size_decisions (engine:claude_auto)
    else conflicto, minoría o ilegible
        C->>G: marca, nombre, GTIN y tamaños publicados
        G-->>C: peso oficial + fuente citada (búsqueda web)
        alt la cita trae el número y coincide con algún súper
            C->>T: size_decisions (engine:claude_auto)
        else no se pudo verificar
            C-->>P: cola.json con la nota «Requiere revisión humana»
            P->>T: decisión humana (reemplaza a la IA)
        end
    end
    T-->>R: el próximo refresco aplica la decisión
```

## Modelo de datos

Tablas principales en Turso (SQLite). Columnas resumidas; el detalle está en [`docs/modelo-datos.md`](precios-supermercados-sps/docs/modelo-datos.md).

```mermaid
erDiagram
    supermarkets ||--o{ products : publica
    products ||--o{ price_history : "precio por sucursal y fecha"
    products ||--o{ master_product_links : "se enlaza a"
    master_products ||--o{ master_product_links : agrupa
    master_products }o--o| size_decisions : "primary_gtin = canonical_gtin"
    size_decisions ||--o{ size_offer_evidence : "evidencia por súper"

    products {
        INTEGER product_id PK
        TEXT supermarket_id FK
        TEXT source_key
        TEXT ean
        TEXT name
        TEXT brand
        TEXT presentation
    }
    price_history {
        INTEGER product_id FK
        TEXT location_id
        INTEGER current_price_minor
        INTEGER reported_regular_price_minor
        TEXT availability
        TEXT valid_from_utc
        TEXT valid_to_utc
    }
    master_products {
        TEXT master_product_id PK
        TEXT primary_gtin
        TEXT display_name
        TEXT net_content_value
        TEXT net_content_unit
        INTEGER pack_count
        TEXT status
    }
    master_product_links {
        INTEGER link_id PK
        INTEGER product_id FK
        TEXT master_product_id FK
        TEXT link_method
        TEXT decided_by
    }
    size_decisions {
        TEXT canonical_gtin PK
        TEXT decision
        TEXT net_content_value
        TEXT net_content_unit
        TEXT decided_by
        TEXT decided_at_utc
    }
    size_offer_evidence {
        TEXT canonical_gtin PK
        TEXT supermarket_id PK
        TEXT value
        TEXT unit
        TEXT source
    }
```

## Cómo decide la IA

- **[Ley de la Mayoría](precios-supermercados-sps/README.md#ley-de-la-mayoría):** la lectura de fotos guarda sola un tamaño sólo si todas las fotos dicen lo mismo y es el de más de la mitad de las ofertas.
- **[Búsqueda web con Claude](precios-supermercados-sps/README.md#búsqueda-web-con-claude):** para el resto, se aprueba solo lo que una fuente citada confirma y coincide con algún súper; lo demás queda para una persona.

## Sobre el repositorio

El proyecto se separó del antiguo monorepo `Portafolio` conservando su historial. Se mantiene dentro de `precios-supermercados-sps/` para que las rutas de workflows, tests y datos sigan funcionando igual.
