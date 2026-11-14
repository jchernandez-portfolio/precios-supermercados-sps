"""Ofertas no vistas en la última captura aceptada ("ofertas fantasma").

La persistencia diaria sólo compara lo que llega: un producto que el súper deja
de publicar conserva su periodo abierto en ``price_history`` (ausencia no es
``out_of_stock``) y el catálogo público lo seguía mostrando FRESH con su último
precio. Este módulo resuelve "¿salió en la última captura aceptada de su
contexto?" sin escribir nada por producto:

- **Persistencia** (detrás de ``record_capture_membership``): en la misma
  transacción que registra el ``scrape_runs`` aceptado, guarda UNA fila por
  contexto (súper + ubicación) con el conjunto de llaves fuente del snapshot,
  comprimido. Se sobrescribe en cada captura aceptada: 1 fila escrita por
  contexto y día, tabla de tamaño constante (11 filas).
- **Exportador** (lee ≤ 11 filas): una oferta current cuya llave no está en la
  membresía de la última captura aceptada de su contexto es "no vista". Con
  ``hide_unseen_offers`` se deja de publicar; si no, sólo se mide.

Garantías conservadoras:

- un snapshot rechazado/incompleto nunca llega a persistirse, así que no toca
  la membresía; una cadena sin captura hoy conserva la de su última captura
  aceptada (la salida no cambia);
- la membresía sólo se usa si su ``scrape_run_id`` es exactamente la última
  captura aceptada del contexto (``scrape_runs``); si no, no se oculta nada;
- si "no vistas" supera ``max_unseen_fraction`` de las ofertas del contexto, se
  trata como anomalía y no se oculta nada;
- no cambia disponibilidad ni historia: la oferta vuelve sola en cuanto una
  captura aceptada la vuelve a traer.

El módulo es puro: no hace red. Lo consumen los persistidores Turso y el
exportador del catálogo público.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import zlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

PROJECT_ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = PROJECT_ROOT / "config" / "catalog" / "unseen-offers-v1.json"
POLICY_SCHEMA = "rpi-unseen-offers-policy/v1"
TABLE_NAME = "capture_membership"
CODEC = "zlib-base64-v1"
KEY_SEPARATOR = "\x1f"
# Defensa al descomprimir: 25 mil llaves de ~20 bytes son ~0.5 MB.
MAX_DECODED_BYTES = 16 * 1024 * 1024

TABLE_SQL = f"""CREATE TABLE IF NOT EXISTS {TABLE_NAME}(
    supermarket_id TEXT NOT NULL,
    location_id TEXT NOT NULL,
    scrape_run_id TEXT NOT NULL,
    observed_at_utc TEXT NOT NULL,
    key_count INTEGER NOT NULL CHECK(key_count > 0),
    keys_sha256 TEXT NOT NULL CHECK(length(keys_sha256) = 64),
    keys_codec TEXT NOT NULL CHECK(keys_codec = '{CODEC}'),
    keys_payload TEXT NOT NULL,
    PRIMARY KEY(supermarket_id, location_id)) STRICT"""

STATUS_HIDDEN = "hidden"
STATUS_MEASURED = "measured"
STATUS_NO_MEMBERSHIP = "no_membership"
STATUS_INVALID = "invalid_membership"
STATUS_RUN_MISMATCH = "run_mismatch"
STATUS_ABOVE_LIMIT = "above_limit"


class CaptureMembershipError(ValueError):
    """Política o membresía inválida."""


@dataclass(frozen=True, slots=True)
class UnseenOffersPolicy:
    record_capture_membership: bool = False
    hide_unseen_offers: bool = False
    max_unseen_fraction: float = 0.5


@dataclass(frozen=True, slots=True)
class EncodedMembership:
    key_count: int
    keys_sha256: str
    keys_payload: str


@dataclass(frozen=True, slots=True)
class ContextMembership:
    supermarket_id: str
    location_id: str
    scrape_run_id: str
    keys: frozenset[str] | None  # ``None`` = fila presente pero inválida


def load_policy(path: Path = POLICY_PATH) -> UnseenOffersPolicy:
    """Lee el interruptor versionado; sin archivo = todo apagado."""
    if not path.is_file():
        return UnseenOffersPolicy()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CaptureMembershipError("unseen_offers_policy_invalid") from exc
    expected = {"schema", "record_capture_membership", "hide_unseen_offers", "max_unseen_fraction", "notes"}
    if not isinstance(document, dict) or set(document) - expected or document.get("schema") != POLICY_SCHEMA:
        raise CaptureMembershipError("unseen_offers_policy_invalid")
    record = document.get("record_capture_membership")
    hide = document.get("hide_unseen_offers")
    fraction = document.get("max_unseen_fraction")
    if type(record) is not bool or type(hide) is not bool:
        raise CaptureMembershipError("unseen_offers_policy_invalid")
    if type(fraction) not in {int, float} or not 0 < fraction < 1:
        raise CaptureMembershipError("unseen_offers_policy_invalid")
    return UnseenOffersPolicy(record, hide, float(fraction))


def capture_key(source_key_type: object, source_key: object) -> str:
    """Identidad fuente dentro de un súper: ``source_key_type`` + ``source_key``."""
    return f"{source_key_type}{KEY_SEPARATOR}{source_key}"


def snapshot_keys(snapshot: Mapping[str, Any]) -> list[str]:
    """Llaves de ``products`` del snapshot aceptado (con y sin precio)."""
    rows = snapshot.get("products")
    if not isinstance(rows, list) or not rows:
        raise CaptureMembershipError("capture_membership_snapshot_empty")
    return [capture_key(row["source_key_type"], row["source_key"]) for row in rows]


def _canonical_bytes(keys: Iterable[str]) -> tuple[int, bytes]:
    values = sorted(set(keys))
    if not values or any(not key or "\n" in key for key in values):
        raise CaptureMembershipError("capture_membership_keys_invalid")
    return len(values), "\n".join(values).encode("utf-8")


def encode_keys(keys: Iterable[str]) -> EncodedMembership:
    count, raw = _canonical_bytes(keys)
    payload = base64.b64encode(zlib.compress(raw, 9)).decode("ascii")
    return EncodedMembership(count, hashlib.sha256(raw).hexdigest(), payload)


def decode_keys(key_count: object, keys_sha256: object, codec: object, payload: object) -> frozenset[str] | None:
    """Conjunto de llaves, o ``None`` si la fila no es íntegra (falla abierta: no oculta)."""
    if codec != CODEC or not isinstance(payload, str) or type(key_count) is not int or key_count <= 0:
        return None
    try:
        decompressor = zlib.decompressobj()
        raw = decompressor.decompress(base64.b64decode(payload.encode("ascii"), validate=True), MAX_DECODED_BYTES)
        if decompressor.unconsumed_tail or not decompressor.eof:
            return None
    except (binascii.Error, ValueError, zlib.error, UnicodeEncodeError):
        return None
    if hashlib.sha256(raw).hexdigest() != keys_sha256:
        return None
    keys = frozenset(raw.decode("utf-8").split("\n"))
    return keys if len(keys) == key_count else None


def persistence_steps(
    keys: Sequence[str],
    *,
    supermarket_id: str,
    location_id: str,
    run_id: str,
    observed_at: str,
) -> list[tuple[str, str, tuple[object, ...]]]:
    """Sentencias para la transacción de persistencia (antes de ``COMMIT``).

    Una fila por contexto, sobrescrita: 1 escritura por captura aceptada. La
    guarda comprueba que la fila quedó ligada a este ``scrape_run_id`` (lectura
    por PK). Exige la tabla temporal ``guard_ok`` de la persistencia.
    """
    encoded = encode_keys(keys)
    return [
        ("capture_membership_table", TABLE_SQL, ()),
        (
            "upsert_capture_membership",
            f"""INSERT INTO {TABLE_NAME}(
                supermarket_id,location_id,scrape_run_id,observed_at_utc,
                key_count,keys_sha256,keys_codec,keys_payload)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(supermarket_id,location_id) DO UPDATE SET
                scrape_run_id=excluded.scrape_run_id,observed_at_utc=excluded.observed_at_utc,
                key_count=excluded.key_count,keys_sha256=excluded.keys_sha256,
                keys_codec=excluded.keys_codec,keys_payload=excluded.keys_payload""",
            (
                supermarket_id, location_id, run_id, observed_at,
                encoded.key_count, encoded.keys_sha256, CODEC, encoded.keys_payload,
            ),
        ),
        (
            "guard_capture_membership",
            f"""INSERT INTO guard_ok SELECT CASE WHEN EXISTS(
                SELECT 1 FROM {TABLE_NAME}
                WHERE supermarket_id=? AND location_id=? AND scrape_run_id=?
                  AND key_count=? AND keys_sha256=?) THEN 0 ELSE 1 END""",
            (supermarket_id, location_id, run_id, encoded.key_count, encoded.keys_sha256),
        ),
    ]


def table_exists(backend: Any) -> bool:
    rows = backend.query(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (TABLE_NAME,)
    )
    return bool(rows)


def fetch_memberships(
    backend: Any, contexts: Iterable[tuple[str, str]]
) -> dict[tuple[str, str], ContextMembership]:
    """Lee la tabla completa (una fila por contexto; ≤ 11 filas) y la acota al scope."""
    wanted = set(contexts)
    result: dict[tuple[str, str], ContextMembership] = {}
    rows = backend.query(
        f"SELECT supermarket_id,location_id,scrape_run_id,key_count,keys_sha256,keys_codec,keys_payload FROM {TABLE_NAME}"
    )
    for supermarket_id, location_id, run_id, key_count, keys_sha256, codec, payload in rows:
        context = (str(supermarket_id), str(location_id))
        if context not in wanted:
            continue
        keys = decode_keys(key_count, keys_sha256, codec, payload) if isinstance(run_id, str) else None
        result[context] = ContextMembership(context[0], context[1], str(run_id), keys)
    return result


Offer = TypeVar("Offer")


def apply_unseen_policy(
    offers: Sequence[Offer],
    *,
    contexts: Sequence[tuple[str, str]],
    memberships: Mapping[tuple[str, str], ContextMembership],
    latest_accepted_runs: Mapping[tuple[str, str], str | None],
    policy: UnseenOffersPolicy,
    context_of: Callable[[Offer], tuple[str, str]],
    key_of: Callable[[Offer], str | None],
) -> tuple[list[Offer], list[dict[str, object]]]:
    """Devuelve (ofertas publicables, informe por contexto).

    Sólo se oculta en un contexto cuya membresía es íntegra, pertenece a la
    última captura aceptada, no supera el umbral de anomalía y con
    ``hide_unseen_offers`` activo. Una oferta sin llave cuenta como vista.
    """
    by_context: dict[tuple[str, str], list[Offer]] = {context: [] for context in contexts}
    for offer in offers:
        by_context.setdefault(context_of(offer), []).append(offer)
    hidden_ids: set[int] = set()
    report: list[dict[str, object]] = []
    for context in contexts:
        current = by_context.get(context, [])
        membership = memberships.get(context)
        entry: dict[str, object] = {
            "supermarket_id": context[0],
            "location_id": context[1],
            "current_offers": len(current),
            "unseen_offers": None,
            "capture_run": None if membership is None else membership.scrape_run_id,
        }
        if membership is None:
            entry["status"] = STATUS_NO_MEMBERSHIP
        elif membership.keys is None:
            entry["status"] = STATUS_INVALID
        elif membership.scrape_run_id != latest_accepted_runs.get(context):
            entry["status"] = STATUS_RUN_MISMATCH
        else:
            keys = membership.keys
            unseen = [offer for offer in current if (key := key_of(offer)) is not None and key not in keys]
            entry["unseen_offers"] = len(unseen)
            if current and len(unseen) > policy.max_unseen_fraction * len(current):
                entry["status"] = STATUS_ABOVE_LIMIT
            elif policy.hide_unseen_offers:
                entry["status"] = STATUS_HIDDEN
                hidden_ids.update(id(offer) for offer in unseen)
            else:
                entry["status"] = STATUS_MEASURED
        report.append(entry)
    kept = [offer for offer in offers if id(offer) not in hidden_ids]
    return kept, report
