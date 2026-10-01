"""Contrato de entrada del motor: un registro por producto fuente y ciudad.

El motor no lee supermercados. Recibe registros ya persistidos (Turso
``products``) o reconstruidos offline desde el catálogo publicado. ``barcode`` es
el identificador que la producción ya acepta; ``silver_gtin`` es evidencia
auxiliar sólo para etiquetas de entrenamiento/evaluación y nunca entra al score.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Iterator

from ..product_homologation import SourceProductRecord

RECORD_SCHEMA = "precios-sps-matching-record/v1"
FINGERPRINT_AUTHORITIES = frozenset({"turso_products", "offline_reconstruction", "fixture"})


class MatchRecordError(ValueError):
    """Un registro de entrada no cumple el contrato."""


@dataclass(frozen=True, slots=True)
class MatchRecord:
    source_record_id: str
    supermarket_id: str
    city: str
    source_name: str
    source_brand: str | None = None
    source_presentation: str | None = None
    source_category: str | None = None
    barcode: str | None = None
    current_price: str | None = None
    location_ids: tuple[str, ...] = ()
    published_canonical_product_id: str | None = None
    published_row_id: str | None = None
    silver_gtin: str | None = None
    name_reconstructed: bool = False
    fingerprint_authority: str = "fixture"
    extra: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("source_record_id", "supermarket_id", "city", "source_name"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise MatchRecordError(f"{name}_missing")
        if self.fingerprint_authority not in FINGERPRINT_AUTHORITIES:
            raise MatchRecordError("fingerprint_authority_invalid")
        if self.current_price is not None:
            try:
                price = Decimal(str(self.current_price))
            except InvalidOperation as exc:
                raise MatchRecordError("current_price_invalid") from exc
            if not price.is_finite() or price < 0:
                raise MatchRecordError("current_price_invalid")
        object.__setattr__(self, "location_ids", tuple(self.location_ids))

    @property
    def price(self) -> Decimal | None:
        if self.current_price is None:
            return None
        value = Decimal(str(self.current_price))
        return value if value > 0 else None

    def to_source_record(self) -> SourceProductRecord:
        return SourceProductRecord(
            source_record_id=self.source_record_id,
            supermarket_id=self.supermarket_id,
            source_name=self.source_name,
            source_brand=self.source_brand,
            source_presentation=self.source_presentation,
            source_category=self.source_category,
            barcode=self.barcode,
        )

    def to_json(self) -> dict[str, object]:
        payload = asdict(self)
        payload["location_ids"] = list(self.location_ids)
        payload["schema"] = RECORD_SCHEMA
        return payload

    @classmethod
    def from_json(cls, payload: dict[str, object]) -> "MatchRecord":
        if not isinstance(payload, dict):
            raise MatchRecordError("record_not_object")
        data = dict(payload)
        schema = data.pop("schema", RECORD_SCHEMA)
        if schema != RECORD_SCHEMA:
            raise MatchRecordError("record_schema_invalid")
        allowed = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        unknown = set(data) - allowed
        if unknown:
            raise MatchRecordError("record_fields_unknown:" + ",".join(sorted(unknown)))
        locations = data.get("location_ids") or ()
        if not isinstance(locations, (list, tuple)):
            raise MatchRecordError("location_ids_invalid")
        data["location_ids"] = tuple(str(item) for item in locations)
        extra = data.get("extra") or {}
        if not isinstance(extra, dict):
            raise MatchRecordError("extra_invalid")
        data["extra"] = {str(key): str(value) for key, value in extra.items()}
        return cls(**data)  # type: ignore[arg-type]


def read_records(path: Path) -> tuple[MatchRecord, ...]:
    records: list[MatchRecord] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise MatchRecordError("records_unreadable") from exc
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise MatchRecordError(f"records_json_invalid:{number}") from exc
        records.append(MatchRecord.from_json(payload))
    validate_unique(records)
    return tuple(records)


def write_records(path: Path, records: Iterable[MatchRecord]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record.to_json(), ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def validate_unique(records: Iterable[MatchRecord]) -> None:
    seen: set[tuple[str, str]] = set()
    for record in records:
        key = (record.city, record.source_record_id)
        if key in seen:
            raise MatchRecordError("record_duplicate:" + record.source_record_id)
        seen.add(key)


def split_by_city(records: Iterable[MatchRecord]) -> dict[str, tuple[MatchRecord, ...]]:
    result: dict[str, list[MatchRecord]] = {}
    for record in records:
        result.setdefault(record.city, []).append(record)
    return {city: tuple(sorted(rows, key=lambda item: item.source_record_id)) for city, rows in sorted(result.items())}


def iter_cross_retailer(records: Iterable[MatchRecord]) -> Iterator[tuple[MatchRecord, MatchRecord]]:
    rows = list(records)
    for index, left in enumerate(rows):
        for right in rows[index + 1 :]:
            if left.supermarket_id != right.supermarket_id:
                yield left, right
