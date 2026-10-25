"""Consulta GraphQL pública y mínima utilizada por el extractor de La Colonia."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode

GRAPHQL_ENDPOINT = "https://www.lacolonia.com/_v/segment/graphql/v1"
MAX_CONTROLLED_PRODUCTS = 10
MAX_CATALOG_PAGE_SIZE = 50
ALLOWED_ORDER_BY = frozenset(
    {
        "OrderByReleaseDateDESC",
        "OrderByNameASC",
        "OrderByNameDESC",
        "OrderByPriceASC",
        "OrderByPriceDESC",
    }
)

# Basada en QueryProductSearchV3 de vtex.store-resources. Se solicitan solo
# los campos necesarios y se aliasa el nombre histórico `commertialOffer` al
# nombre correcto usado internamente.
#
# `hideUnavailableItems = false` evita que la búsqueda oculte productos por
# disponibilidad. `skusFilter = ALL` controla, por separado, que todos los SKU
# asociados a cada producto sean devueltos para auditoría.
PRODUCT_SEARCH_QUERY = """
query productSearchV3(
  $query: String
  $fullText: String
  $selectedFacets: [SelectedFacetInput]
  $orderBy: String
  $from: Int
  $to: Int
  $hideUnavailableItems: Boolean
  $skusFilter: ItemsFilter = ALL
) {
  productSearch(
    query: $query
    fullText: $fullText
    selectedFacets: $selectedFacets
    orderBy: $orderBy
    from: $from
    to: $to
    hideUnavailableItems: $hideUnavailableItems
  ) @context(provider: "vtex.search-graphql") {
    recordsFiltered
    products {
      productId
      productName
      productReference
      linkText
      brand
      categories
      items(filter: $skusFilter) {
        itemId
        name
        nameComplete
        ean
        referenceId {
          Key
          Value
        }
        measurementUnit
        unitMultiplier
        images {
          imageUrl
        }
        sellers {
          sellerId
          sellerDefault
          commercialOffer: commertialOffer {
            Price
            ListPrice
            AvailableQuantity
            discountHighlights {
              name
            }
            teasers {
              name
            }
          }
        }
      }
    }
  }
}
""".strip()


def build_product_search_url(
    *,
    page: int = 1,
    page_size: int = 5,
    query: str = "supermercado",
    category_map: str = "category-1",
    full_text: str = "",
    order_by: str | None = None,
) -> str:
    """Construye una consulta GET pública para una página consecutiva.

    Las llamadas controladas heredadas que no especifican ``order_by`` mantienen
    el límite histórico de diez productos. El runner de catálogo declara el
    orden explícitamente y puede evaluar, de forma progresiva, hasta cincuenta.
    """

    if page < 1:
        raise ValueError("page debe ser mayor o igual que 1")
    explicit_catalog_request = order_by is not None
    max_page_size = (
        MAX_CATALOG_PAGE_SIZE if explicit_catalog_request else MAX_CONTROLLED_PRODUCTS
    )
    if not 1 <= page_size <= max_page_size:
        if explicit_catalog_request:
            raise ValueError(
                f"page_size debe estar entre 1 y {MAX_CATALOG_PAGE_SIZE} productos"
            )
        raise ValueError(
            f"La prueba controlada admite entre 1 y {MAX_CONTROLLED_PRODUCTS} productos"
        )
    effective_order_by = order_by or "OrderByReleaseDateDESC"
    if effective_order_by not in ALLOWED_ORDER_BY:
        raise ValueError(f"order_by no permitido: {effective_order_by}")
    if full_text.strip():
        query_value = ""
        selected_facets: list[dict[str, str]] = []
    else:
        if not query.strip() or not category_map.strip():
            raise ValueError("query y category_map no pueden estar vacíos")
        query_value = query
        selected_facets = [{"key": category_map, "value": query}]

    from_index = (page - 1) * page_size
    variables: Mapping[str, Any] = {
        "query": query_value,
        "fullText": full_text.strip(),
        "selectedFacets": selected_facets,
        "orderBy": effective_order_by,
        "from": from_index,
        "to": from_index + page_size - 1,
        "hideUnavailableItems": False,
        "skusFilter": "ALL",
    }
    params = {
        "workspace": "master",
        "maxAge": "short",
        "appsEtag": "remove",
        "domain": "store",
        "locale": "es-HN",
        "operationName": "productSearchV3",
        "query": PRODUCT_SEARCH_QUERY,
        "variables": json.dumps(variables, separators=(",", ":")),
    }
    return f"{GRAPHQL_ENDPOINT}?{urlencode(params)}"
