"""Implementación Google Cloud del port usado por ``BigQueryAdapter``.

No crea proyectos ni datasets. ``ensure_dataset`` exige que el dataset exista;
la creación/selección del proyecto, billing, API y permisos pertenece a la
frontera humana/cloud. Las tablas sí pueden materializarse después de esa
configuración explícita.

Las escrituras comerciales usan tablas staging con expiración y una única
transacción DML sobre tablas destino. Un fallo durante staging no toca destino;
un fallo dentro del script revierte todos los cambios destino. Staging es sólo
infraestructura efímera y se elimina best-effort en ``finally``.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from google.api_core.exceptions import (
    Conflict,
    InternalServerError,
    NotFound,
    ServiceUnavailable,
    TooManyRequests,
)
from google.cloud import bigquery

from .bigquery_adapter import BigQueryAdapterError, BigQueryReplayConflict
from .bigquery_contract import BIGQUERY_TABLE_BY_NAME, BigQueryTableSpec

_DATASET_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,1023}$")
_MAX_ATOMIC_JOB_ATTEMPTS = 4


class GoogleCloudBigQueryClient:
    """Cliente productivo del port, aislado del dominio comercial."""

    def __init__(self, client: bigquery.Client | None = None) -> None:
        self._client = client or bigquery.Client()

    def _dataset_ref(self, dataset_id: str) -> str:
        if not _DATASET_RE.fullmatch(dataset_id):
            raise BigQueryAdapterError("dataset_id_invalid")
        return f"{self._client.project}.{dataset_id}"

    def _table_ref(self, dataset_id: str, table_name: str) -> str:
        if table_name not in BIGQUERY_TABLE_BY_NAME:
            raise BigQueryAdapterError("table_unknown")
        return f"{self._dataset_ref(dataset_id)}.{table_name}"

    @staticmethod
    def _schema(spec: BigQueryTableSpec) -> list[bigquery.SchemaField]:
        return [
            bigquery.SchemaField(
                field.name,
                field.field_type,
                mode="REQUIRED" if field.required else "NULLABLE",
            )
            for field in spec.fields
        ]

    @staticmethod
    def _schema_signature(
        schema: list[bigquery.SchemaField],
    ) -> tuple[tuple[str, str, str], ...]:
        return tuple((field.name, field.field_type, field.mode) for field in schema)

    @staticmethod
    def _json_compatible_value(field_type: str, value: Any) -> Any:
        """Convierte tipos Python ricos a representaciones JSON aceptadas por load jobs."""

        if value is None:
            return None
        if field_type == "TIMESTAMP" and isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise BigQueryAdapterError("timestamp_timezone_required")
            if value.utcoffset().total_seconds() != 0:
                raise BigQueryAdapterError("timestamp_must_be_utc")
            return value.isoformat(timespec="microseconds").replace("+00:00", "Z")
        if field_type == "DATE" and isinstance(value, date) and not isinstance(value, datetime):
            return value.isoformat()
        if field_type == "NUMERIC" and isinstance(value, Decimal):
            return format(value, "f")
        return value

    @classmethod
    def _json_compatible_row(
        cls,
        spec: BigQueryTableSpec,
        row: Mapping[str, Any],
    ) -> dict[str, Any]:
        if set(row) != set(spec.columns):
            raise BigQueryAdapterError(f"staging_row_schema_mismatch:{spec.name}")
        fields = {field.name: field for field in spec.fields}
        return {
            column: cls._json_compatible_value(fields[column].field_type, row[column])
            for column in spec.columns
        }

    def ensure_dataset(self, dataset_id: str) -> None:
        ref = self._dataset_ref(dataset_id)
        try:
            self._client.get_dataset(ref)
        except NotFound as exc:
            raise BigQueryAdapterError("dataset_missing_human_cloud_boundary") from exc

    def ensure_table(self, dataset_id: str, spec: BigQueryTableSpec) -> None:
        table_ref = self._table_ref(dataset_id, spec.name)
        expected_schema = self._schema(spec)
        try:
            existing = self._client.get_table(table_ref)
        except NotFound:
            table = bigquery.Table(table_ref, schema=expected_schema)
            if spec.partition_field is not None:
                table.time_partitioning = bigquery.TimePartitioning(
                    type_=bigquery.TimePartitioningType.DAY,
                    field=spec.partition_field,
                )
            table.clustering_fields = list(spec.clustering_fields) or None
            self._client.create_table(table)
            return

        if self._schema_signature(list(existing.schema)) != self._schema_signature(
            expected_schema
        ):
            raise BigQueryAdapterError(f"table_schema_conflict:{spec.name}")
        existing_partition = (
            existing.time_partitioning.field
            if existing.time_partitioning is not None
            else None
        )
        if existing_partition != spec.partition_field:
            raise BigQueryAdapterError(f"table_partition_conflict:{spec.name}")
        if tuple(existing.clustering_fields or ()) != spec.clustering_fields:
            raise BigQueryAdapterError(f"table_clustering_conflict:{spec.name}")

    @staticmethod
    def _parameter_type(field_type: str) -> str:
        return {
            "STRING": "STRING",
            "BOOL": "BOOL",
            "INT64": "INT64",
            "NUMERIC": "NUMERIC",
            "TIMESTAMP": "TIMESTAMP",
            "DATE": "DATE",
            "JSON": "JSON",
        }[field_type]

    def get_row(
        self,
        dataset_id: str,
        table_name: str,
        logical_key: tuple[Any, ...],
    ) -> Mapping[str, Any] | None:
        spec = BIGQUERY_TABLE_BY_NAME[table_name]
        if len(logical_key) != len(spec.logical_key):
            raise BigQueryAdapterError("logical_key_shape_invalid")
        predicates: list[str] = []
        parameters: list[bigquery.ScalarQueryParameter] = []
        for index, (column, value) in enumerate(zip(spec.logical_key, logical_key)):
            field = next(field for field in spec.fields if field.name == column)
            name = f"key_{index}"
            predicates.append(f"`{column}` = @{name}")
            parameters.append(
                bigquery.ScalarQueryParameter(
                    name,
                    self._parameter_type(field.field_type),
                    value,
                )
            )
        sql = (
            f"SELECT * FROM `{self._table_ref(dataset_id, table_name)}` "
            f"WHERE {' AND '.join(predicates)} LIMIT 1"
        )
        rows = list(
            self._client.query(
                sql,
                job_config=bigquery.QueryJobConfig(query_parameters=parameters),
            ).result()
        )
        if not rows:
            return None
        return dict(rows[0].items())

    @staticmethod
    def _join_predicate(spec: BigQueryTableSpec, left: str, right: str) -> str:
        return " AND ".join(
            f"{left}.`{column}` = {right}.`{column}`" for column in spec.logical_key
        )

    @staticmethod
    def _row_difference_predicate(
        spec: BigQueryTableSpec,
        left: str,
        right: str,
    ) -> str:
        compared = tuple(
            column for column in spec.columns if column not in spec.logical_key
        )
        if not compared:
            return "FALSE"
        return " OR ".join(
            f"{left}.`{column}` IS DISTINCT FROM {right}.`{column}`"
            for column in compared
        )

    @classmethod
    def _immutable_guard_sql(
        cls,
        *,
        spec: BigQueryTableSpec,
        target: str,
        staging_ref: str,
        join: str,
    ) -> str:
        # Una logical key existente sólo es conflicto si el payload difiere. Un
        # replay idéntico se deja pasar y el INSERT posterior omite esa fila.
        difference = cls._row_difference_predicate(spec, "T", "S")
        return (
            "SELECT IF(COUNTIF("
            f"{difference}) = 0, TRUE, ERROR('immutable_conflict:{spec.name}')) "
            f"FROM `{target}` T JOIN `{staging_ref}` S ON {join};"
        )

    @staticmethod
    def _immutable_insert_sql(
        *,
        spec: BigQueryTableSpec,
        target: str,
        staging_ref: str,
        join: str,
    ) -> str:
        columns = ", ".join(f"`{column}`" for column in spec.columns)
        source_columns = ", ".join(f"S.`{column}`" for column in spec.columns)
        return (
            f"INSERT INTO `{target}` ({columns}) "
            f"SELECT {source_columns} FROM `{staging_ref}` S "
            f"WHERE NOT EXISTS (SELECT 1 FROM `{target}` T WHERE {join});"
        )

    @staticmethod
    def _atomic_job_id(dataset_id: str, run_id: str, attempt: int = 0) -> str:
        raw = f"{dataset_id}\x00{run_id}".encode()
        digest = hashlib.sha256(raw).hexdigest()
        return f"precios_sps_atomic_{digest}_{attempt}"

    @staticmethod
    def _retryable_atomic_error(exc: Exception) -> bool:
        if isinstance(
            exc,
            (InternalServerError, ServiceUnavailable, TooManyRequests),
        ):
            return True
        text = str(exc).lower()
        return "transaction" in text and any(
            token in text for token in ("concurrent", "conflict", "cancel", "aborted")
        )

    def _existing_key_count(
        self,
        dataset_id: str,
        spec: BigQueryTableSpec,
        staging_ref: str,
    ) -> int:
        target = self._table_ref(dataset_id, spec.name)
        join = self._join_predicate(spec, "T", "S")
        sql = f"SELECT COUNT(*) AS n FROM `{target}` T JOIN `{staging_ref}` S ON {join}"
        row = next(iter(self._client.query(sql).result()))
        return int(row["n"])

    def _run_atomic_script(
        self,
        *,
        dataset_id: str,
        run_id: str,
        script: str,
        location: str | None,
    ) -> bool:
        """Ejecuta exactamente una transacción por slot de job idempotente.

        ``jobs.insert`` con un job ID conocido evita que dos callers concurrentes
        ejecuten dos copias de la misma transacción. Si un slot previo terminó con
        un error transitorio, ambos callers avanzan al mismo siguiente slot
        determinista; errores lógicos no se reintentan.

        Devuelve ``True`` cuando este caller reutilizó un job ya existente.
        """

        for attempt in range(_MAX_ATOMIC_JOB_ATTEMPTS):
            job_id = self._atomic_job_id(dataset_id, run_id, attempt)
            reused = False
            try:
                job = self._client.query(
                    script,
                    job_id=job_id,
                    location=location,
                    job_retry=None,
                )
            except Conflict:
                job = self._client.get_job(job_id, location=location)
                reused = True

            try:
                job.result()
                return reused
            except Exception as exc:
                text = str(exc)
                if "immutable_conflict:" in text:
                    raise BigQueryReplayConflict("immutable_row_conflict") from exc
                if self._retryable_atomic_error(exc) and attempt + 1 < _MAX_ATOMIC_JOB_ATTEMPTS:
                    continue
                raise

        raise BigQueryAdapterError("atomic_retry_exhausted")

    def apply_atomic(
        self,
        dataset_id: str,
        rows: Mapping[str, tuple[Mapping[str, Any], ...]],
        *,
        immutable_tables: frozenset[str],
    ) -> tuple[int, int, int]:
        if set(rows) != set(BIGQUERY_TABLE_BY_NAME):
            raise BigQueryAdapterError("atomic_tables_mismatch")
        run_rows = rows.get("scrape_runs", ())
        if len(run_rows) != 1:
            raise BigQueryAdapterError("exactly_one_scrape_run_required")
        run_id = str(run_rows[0]["scrape_run_id"])
        digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16]
        attempt_nonce = uuid.uuid4().hex[:16]
        staged: dict[str, str] = {}
        existing_mutable = 0
        existing_immutable = 0
        immutable_count = 0
        mutable_count = 0
        total_rows = sum(len(source_rows) for source_rows in rows.values())

        try:
            dataset = self._client.get_dataset(self._dataset_ref(dataset_id))
            location = getattr(dataset, "location", None)

            for table_name, source_rows in rows.items():
                if not source_rows:
                    continue
                spec = BIGQUERY_TABLE_BY_NAME[table_name]
                staging_name = f"_stg_{table_name}_{digest}_{attempt_nonce}"
                staging_ref = f"{self._dataset_ref(dataset_id)}.{staging_name}"
                self._client.delete_table(staging_ref, not_found_ok=True)
                staging = bigquery.Table(staging_ref, schema=self._schema(spec))
                staging.expires = datetime.now(UTC) + timedelta(hours=2)
                self._client.create_table(staging)
                job_config = bigquery.LoadJobConfig(
                    schema=self._schema(spec),
                    write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
                )
                json_rows = [
                    self._json_compatible_row(spec, row) for row in source_rows
                ]
                self._client.load_table_from_json(
                    json_rows,
                    staging_ref,
                    job_config=job_config,
                    location=location,
                ).result()
                staged[table_name] = staging_ref
                existing = self._existing_key_count(dataset_id, spec, staging_ref)
                if table_name in immutable_tables:
                    immutable_count += len(source_rows)
                    existing_immutable += existing
                else:
                    mutable_count += len(source_rows)
                    existing_mutable += existing

            statements = ["BEGIN TRANSACTION;"]
            for table_name, staging_ref in staged.items():
                spec = BIGQUERY_TABLE_BY_NAME[table_name]
                target = self._table_ref(dataset_id, table_name)
                columns = ", ".join(f"`{column}`" for column in spec.columns)
                source_columns = ", ".join(f"S.`{column}`" for column in spec.columns)
                join = self._join_predicate(spec, "T", "S")
                if table_name in immutable_tables:
                    statements.append(
                        self._immutable_guard_sql(
                            spec=spec,
                            target=target,
                            staging_ref=staging_ref,
                            join=join,
                        )
                    )
                    statements.append(
                        self._immutable_insert_sql(
                            spec=spec,
                            target=target,
                            staging_ref=staging_ref,
                            join=join,
                        )
                    )
                    continue

                update_columns = [
                    column for column in spec.columns if column not in spec.logical_key
                ]
                updates = ", ".join(
                    f"T.`{column}` = S.`{column}`" for column in update_columns
                )
                statements.append(
                    f"MERGE `{target}` T USING `{staging_ref}` S ON {join} "
                    f"WHEN MATCHED THEN UPDATE SET {updates} "
                    f"WHEN NOT MATCHED THEN INSERT ({columns}) VALUES ({source_columns});"
                )
            statements.append("COMMIT TRANSACTION;")
            reused_job = self._run_atomic_script(
                dataset_id=dataset_id,
                run_id=run_id,
                script="\n".join(statements),
                location=location,
            )
        except BigQueryReplayConflict:
            raise
        except Exception as exc:
            text = str(exc)
            if "immutable_conflict:" in text:
                raise BigQueryReplayConflict("immutable_row_conflict") from exc
            raise BigQueryAdapterError("atomic_write_failed") from exc
        finally:
            for staging_ref in staged.values():
                try:
                    self._client.delete_table(staging_ref, not_found_ok=True)
                except Exception:
                    # Expiran automáticamente; nunca ocultamos el resultado target.
                    pass

        if reused_job:
            return 0, 0, total_rows

        created_mutable = mutable_count - existing_mutable
        created_immutable = immutable_count - existing_immutable
        return created_immutable + created_mutable, existing_mutable, existing_immutable

    def read_rows(
        self,
        dataset_id: str,
        table_name: str,
    ) -> tuple[Mapping[str, Any], ...]:
        spec = BIGQUERY_TABLE_BY_NAME[table_name]
        order = ", ".join(f"`{column}`" for column in spec.logical_key)
        sql = f"SELECT * FROM `{self._table_ref(dataset_id, table_name)}` ORDER BY {order}"
        return tuple(dict(row.items()) for row in self._client.query(sql).result())
