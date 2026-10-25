"""Adapter BigQuery backend-neutral y fake transaccional para pruebas offline."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol

from .bigquery_contract import (
    BIGQUERY_TABLE_NAMES,
    BIGQUERY_TABLE_SPECS,
    INVENTARIO_HISTORICO,
    PRECIOS_HISTORICOS,
    PRODUCTOS,
    SCRAPE_RUNS,
    BigQueryTableSpec,
)
from .bigquery_persistence import BigQueryWritePlan

_BOUND_EVIDENCE_PREFIX = "crev1_"
_BOUND_EVIDENCE_HEX_LENGTH = 64


class BigQueryAdapterError(RuntimeError):
    """Error fail-closed del adapter de persistencia."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class BigQueryReplayConflict(BigQueryAdapterError):
    """El mismo ID de run reapareció con evidencia distinta."""


IMMUTABLE_TABLES = frozenset(
    {
        PRECIOS_HISTORICOS.name,
        INVENTARIO_HISTORICO.name,
        SCRAPE_RUNS.name,
        "quality_events",
    }
)


class BigQueryClientPort(Protocol):
    """Superficie mínima que separa dominio/adapter del SDK de Google."""

    def ensure_dataset(self, dataset_id: str) -> None: ...

    def ensure_table(self, dataset_id: str, spec: BigQueryTableSpec) -> None: ...

    def get_row(
        self,
        dataset_id: str,
        table_name: str,
        logical_key: tuple[Any, ...],
    ) -> Mapping[str, Any] | None: ...

    def apply_atomic(
        self,
        dataset_id: str,
        rows: Mapping[str, tuple[Mapping[str, Any], ...]],
        *,
        immutable_tables: frozenset[str],
    ) -> tuple[int, int, int]: ...

    def read_rows(self, dataset_id: str, table_name: str) -> tuple[Mapping[str, Any], ...]: ...


@dataclass(frozen=True, slots=True)
class BigQueryApplyResult:
    scrape_run_id: str
    created: int
    updated: int
    replayed_rows: int
    exact_run_replay: bool


@dataclass(frozen=True, slots=True)
class BigQueryReadBack:
    """Snapshot analítico reconstruido desde observaciones durables."""

    products: tuple[Mapping[str, Any], ...]
    latest_prices: tuple[Mapping[str, Any], ...]
    latest_inventory: tuple[Mapping[str, Any], ...]
    runs: tuple[Mapping[str, Any], ...]


def _is_bound_run_evidence_id(value: object) -> bool:
    """Reconoce únicamente el binding durable producido por commercial_run_evidence."""

    if not isinstance(value, str):
        return False
    if not value.startswith(_BOUND_EVIDENCE_PREFIX):
        return False
    digest = value[len(_BOUND_EVIDENCE_PREFIX) :]
    return len(digest) == _BOUND_EVIDENCE_HEX_LENGTH and all(
        char in "0123456789abcdef" for char in digest
    )


class BigQueryAdapter:
    """Orquesta bootstrap, replay y escrituras atómicas sin acoplar el dominio."""

    def __init__(self, client: BigQueryClientPort, *, dataset_id: str) -> None:
        if client is None:
            raise BigQueryAdapterError("client_required")
        if not isinstance(dataset_id, str) or not dataset_id.strip():
            raise BigQueryAdapterError("dataset_id_invalid")
        self._client = client
        self.dataset_id = dataset_id.strip()

    def bootstrap(self) -> None:
        self._client.ensure_dataset(self.dataset_id)
        for spec in BIGQUERY_TABLE_SPECS:
            self._client.ensure_table(self.dataset_id, spec)

    def apply(self, plan: BigQueryWritePlan) -> BigQueryApplyResult:
        if not isinstance(plan, BigQueryWritePlan):
            raise BigQueryAdapterError("write_plan_invalid")

        run_row = plan.rows[SCRAPE_RUNS.name][0]
        if run_row["commercial_update_allowed"] and not _is_bound_run_evidence_id(
            run_row["run_evidence_id"]
        ):
            raise BigQueryAdapterError("authoritative_run_evidence_required")

        existing = self._client.get_row(
            self.dataset_id,
            SCRAPE_RUNS.name,
            (plan.scrape_run_id,),
        )
        if existing is not None:
            if existing.get("run_fingerprint") != plan.run_fingerprint:
                raise BigQueryReplayConflict("conflicting_run_replay")
            return BigQueryApplyResult(
                scrape_run_id=plan.scrape_run_id,
                created=0,
                updated=0,
                replayed_rows=sum(len(rows) for rows in plan.rows.values()),
                exact_run_replay=True,
            )

        try:
            created, updated, replayed = self._client.apply_atomic(
                self.dataset_id,
                plan.rows,
                immutable_tables=IMMUTABLE_TABLES,
            )
        except BigQueryAdapterError:
            raise
        except Exception as exc:  # pragma: no cover - defensa frente a clientes reales
            raise BigQueryAdapterError("atomic_write_failed") from exc

        # El precheck anterior no es un lock. Un retry concurrente puede ganar la
        # carrera después de él; por eso la postcondición durable vuelve a ligar el
        # run persistido con el fingerprint que este caller intentó aplicar.
        persisted = self._client.get_row(
            self.dataset_id,
            SCRAPE_RUNS.name,
            (plan.scrape_run_id,),
        )
        if persisted is None:
            raise BigQueryAdapterError("scrape_run_missing_after_atomic_write")
        if persisted.get("run_fingerprint") != plan.run_fingerprint:
            raise BigQueryReplayConflict("conflicting_run_replay")

        total_rows = sum(len(rows) for rows in plan.rows.values())
        exact_after_race = created == 0 and updated == 0 and replayed == total_rows
        return BigQueryApplyResult(
            scrape_run_id=plan.scrape_run_id,
            created=created,
            updated=updated,
            replayed_rows=replayed,
            exact_run_replay=exact_after_race,
        )

    def read_back(self, *, supermarket_id: str, location_id: str) -> BigQueryReadBack:
        if not supermarket_id.strip() or not location_id.strip():
            raise BigQueryAdapterError("read_back_scope_invalid")
        products = tuple(
            row
            for row in self._client.read_rows(self.dataset_id, PRODUCTOS.name)
            if row["supermarket_id"] == supermarket_id
        )
        prices = tuple(
            row
            for row in self._client.read_rows(self.dataset_id, PRECIOS_HISTORICOS.name)
            if row["supermarket_id"] == supermarket_id
            and row["location_id"] == location_id
        )
        inventory = tuple(
            row
            for row in self._client.read_rows(self.dataset_id, INVENTARIO_HISTORICO.name)
            if row["supermarket_id"] == supermarket_id
            and row["location_id"] == location_id
        )
        runs = tuple(
            row
            for row in self._client.read_rows(self.dataset_id, SCRAPE_RUNS.name)
            if row["supermarket_id"] == supermarket_id
            and row["location_id"] == location_id
        )

        def latest_by_source(
            source_rows: Sequence[Mapping[str, Any]],
        ) -> tuple[Mapping[str, Any], ...]:
            latest: dict[str, Mapping[str, Any]] = {}
            for row in source_rows:
                key = str(row["source_product_id"])
                previous = latest.get(key)
                if previous is None or str(row["observed_at_utc"]) > str(
                    previous["observed_at_utc"]
                ):
                    latest[key] = row
            return tuple(latest[key] for key in sorted(latest))

        return BigQueryReadBack(
            products=tuple(sorted(products, key=lambda row: str(row["source_product_id"]))),
            latest_prices=latest_by_source(prices),
            latest_inventory=latest_by_source(inventory),
            runs=tuple(sorted(runs, key=lambda row: str(row["started_at_utc"]))),
        )


class FakeBigQueryClient:
    """Cliente in-memory que modela DDL, atomicidad, replay y fallos parciales.

    ``fail_after_mutations`` inyecta un fallo después de N mutaciones staged. La
    copia sólo se publica al final, demostrando que un fallo parcial no contamina
    el estado durable observable.
    """

    def __init__(self) -> None:
        self.datasets: set[str] = set()
        self.table_specs: dict[tuple[str, str], BigQueryTableSpec] = {}
        self._rows: dict[
            tuple[str, str], dict[tuple[Any, ...], dict[str, Any]]
        ] = {}
        self.fail_after_mutations: int | None = None

    def ensure_dataset(self, dataset_id: str) -> None:
        if not dataset_id:
            raise BigQueryAdapterError("dataset_id_invalid")
        self.datasets.add(dataset_id)

    def ensure_table(self, dataset_id: str, spec: BigQueryTableSpec) -> None:
        if dataset_id not in self.datasets:
            raise BigQueryAdapterError("dataset_missing")
        key = (dataset_id, spec.name)
        previous = self.table_specs.get(key)
        if previous is not None and previous != spec:
            raise BigQueryAdapterError("table_schema_conflict")
        self.table_specs[key] = spec
        self._rows.setdefault(key, {})

    def _spec(self, dataset_id: str, table_name: str) -> BigQueryTableSpec:
        try:
            return self.table_specs[(dataset_id, table_name)]
        except KeyError as exc:
            raise BigQueryAdapterError("table_missing") from exc

    @staticmethod
    def _key(spec: BigQueryTableSpec, row: Mapping[str, Any]) -> tuple[Any, ...]:
        return tuple(row[column] for column in spec.logical_key)

    def get_row(
        self,
        dataset_id: str,
        table_name: str,
        logical_key: tuple[Any, ...],
    ) -> Mapping[str, Any] | None:
        spec = self._spec(dataset_id, table_name)
        if len(logical_key) != len(spec.logical_key):
            raise BigQueryAdapterError("logical_key_shape_invalid")
        row = self._rows[(dataset_id, table_name)].get(tuple(logical_key))
        return None if row is None else MappingProxyType(dict(row))

    def apply_atomic(
        self,
        dataset_id: str,
        rows: Mapping[str, tuple[Mapping[str, Any], ...]],
        *,
        immutable_tables: frozenset[str],
    ) -> tuple[int, int, int]:
        if set(rows) != set(BIGQUERY_TABLE_NAMES):
            raise BigQueryAdapterError("atomic_tables_mismatch")
        staged = {
            key: {row_key: dict(row) for row_key, row in table.items()}
            for key, table in self._rows.items()
        }
        created = updated = replayed = mutations = 0
        for table_name in BIGQUERY_TABLE_NAMES:
            spec = self._spec(dataset_id, table_name)
            table = staged[(dataset_id, table_name)]
            for source_row in rows[table_name]:
                row = dict(source_row)
                key = self._key(spec, row)
                previous = table.get(key)
                if previous is None:
                    table[key] = row
                    created += 1
                elif previous == row:
                    replayed += 1
                elif table_name in immutable_tables:
                    raise BigQueryReplayConflict(
                        f"immutable_row_conflict:{table_name}"
                    )
                else:
                    table[key] = row
                    updated += 1
                mutations += 1
                if (
                    self.fail_after_mutations is not None
                    and mutations >= self.fail_after_mutations
                ):
                    self.fail_after_mutations = None
                    raise BigQueryAdapterError("injected_partial_failure")
        self._rows = staged
        self.fail_after_mutations = None
        return created, updated, replayed

    def read_rows(
        self,
        dataset_id: str,
        table_name: str,
    ) -> tuple[Mapping[str, Any], ...]:
        self._spec(dataset_id, table_name)
        rows = self._rows[(dataset_id, table_name)]
        return tuple(
            MappingProxyType(dict(rows[key]))
            for key in sorted(rows, key=lambda value: tuple(str(item) for item in value))
        )

    def count(self, dataset_id: str, table_name: str) -> int:
        self._spec(dataset_id, table_name)
        return len(self._rows[(dataset_id, table_name)])
