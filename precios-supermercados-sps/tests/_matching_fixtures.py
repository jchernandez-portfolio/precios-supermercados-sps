"""Catálogo sintético y determinista para las pruebas del motor de matching.

Cuatro cadenas con estilos de nombre distintos (Walmart descriptivo, La Colonia
"tipo marca variante", Colonial en mayúsculas con palabras pegadas,
Comisariato sin marca estructurada). Los GTIN son válidos (check digit GS1).
"""
from __future__ import annotations

from precios_supermercados.matching.records import MatchRecord

PRODUCTS = (
    # (tipo, marca, variante, tamaño, unidad, precio)
    ("Jugo", "Sula", "Naranja", 1890, "ml", 60),
    ("Jugo", "Sula", "Manzana", 1890, "ml", 61),
    ("Jugo", "Sula", "Uva", 946, "ml", 35),
    ("Jugo", "Del Valle", "Durazno", 1000, "ml", 42),
    ("Jugo", "Del Valle", "Mango", 1000, "ml", 42),
    ("Leche", "Leyde", "Entera", 946, "ml", 32),
    ("Leche", "Leyde", "Descremada", 946, "ml", 33),
    ("Leche", "Sula", "Deslactosada", 946, "ml", 36),
    ("Shampoo", "Dove", "Reconstruccion", 370, "ml", 120),
    ("Shampoo", "Dove", "Hidratacion", 370, "ml", 118),
    ("Shampoo", "Pantene", "Rizos", 400, "ml", 125),
    ("Acondicionador", "Dove", "Reconstruccion", 370, "ml", 121),
    ("Mayonesa", "Hellmanns", "Light", 380, "g", 70),
    ("Mayonesa", "Hellmanns", "Original", 380, "g", 68),
    ("Mayonesa", "McCormick", "Limon", 350, "g", 65),
    ("Arroz", "Progreso", "Blanco", 1000, "g", 40),
    ("Arroz", "Progreso", "Precocido", 1000, "g", 44),
    ("Arroz", "Manhattan", "Blanco", 1500, "g", 58),
    ("Frijol", "Ducal", "Rojo", 400, "g", 30),
    ("Frijol", "Ducal", "Negro", 400, "g", 30),
    ("Detergente", "Xedex", "Limon", 800, "g", 55),
    ("Detergente", "Xedex", "Floral", 800, "g", 55),
    ("Detergente", "Ariel", "Original", 1000, "g", 90),
    ("Galleta", "Oreo", "Chocolate", 432, "g", 75),
    ("Galleta", "Oreo", "Vainilla", 432, "g", 75),
    ("Cafe", "Maya", "Original", 400, "g", 110),
    ("Cafe", "Maya", "Descafeinado", 200, "g", 95),
    ("Atun", "Sardimar", "Agua", 140, "g", 38),
    ("Atun", "Sardimar", "Aceite", 140, "g", 38),
    ("Pasta Dental", "Colgate", "Total", 75, "ml", 45),
)


def _gtin(seed: int) -> str:
    body = f"74{seed:010d}"[:12]
    total = sum(int(digit) * (3 if index % 2 else 1) for index, digit in enumerate(body))
    return body + str((10 - total % 10) % 10)


def _glued(text: str) -> str:
    return "".join(part.capitalize() for part in text.split())


def build_records(city: str = "SPS") -> list[MatchRecord]:
    records: list[MatchRecord] = []
    for index, (kind, brand, variant, size, unit, price) in enumerate(PRODUCTS):
        gtin = _gtin(1000 + index)
        unit_label = "ml" if unit == "ml" else "g"
        records.append(
            MatchRecord(
                source_record_id=f"walmart:{100 + index}",
                supermarket_id="walmart",
                city=city,
                source_name=f"{kind} {brand} {variant} - {size} {unit_label}",
                source_brand=brand,
                barcode=gtin,
                current_price=f"{price:.2f}",
                location_ids=("walmart_sps",),
                fingerprint_authority="fixture",
            )
        )
        records.append(
            MatchRecord(
                source_record_id=f"la_colonia:{200 + index}",
                supermarket_id="la_colonia",
                city=city,
                source_name=f"{kind} {brand} {variant} {size} {'Ml' if unit == 'ml' else 'Gr'}",
                source_brand=brand,
                barcode=gtin if index % 3 else None,
                silver_gtin=gtin,
                current_price=f"{price * 1.05:.2f}",
                location_ids=("la_colonia_sps",),
                fingerprint_authority="fixture",
            )
        )
        records.append(
            MatchRecord(
                source_record_id=f"colonial:{300 + index}",
                supermarket_id="colonial",
                city=city,
                source_name=f"{brand.upper()} {_glued(kind + ' ' + variant)} {size}{unit_label}",
                source_brand="RMS",
                source_category="Abarrotes",
                silver_gtin=gtin,
                current_price=f"{price * 0.97:.2f}",
                location_ids=("colonial_sps",),
                fingerprint_authority="fixture",
            )
        )
        records.append(
            MatchRecord(
                source_record_id=f"comisariato_los_andes:{400 + index}",
                supermarket_id="comisariato_los_andes",
                city=city,
                source_name=f"{kind.lower()} {brand.lower()} {variant.lower()} {size}{unit_label}",
                source_brand="Marca COMANDES",
                current_price=f"{price * 1.1:.2f}",
                location_ids=("comisariato_los_andes_sps",),
                fingerprint_authority="fixture",
            )
        )
    return records
