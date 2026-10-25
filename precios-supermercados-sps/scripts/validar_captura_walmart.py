"""Reconstruct Walmart snapshots from an existing RAW capture; always offline."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from actualizar_mvp_sqlite_la_colonia import validate_snapshot_bytes  # noqa: E402
from precios_supermercados.scrapers.walmart import reconcile_capture  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output must be new")
    snapshots = reconcile_capture(args.capture)
    encoded = [(s, json.dumps(s, ensure_ascii=False, separators=(",", ":")).encode()) for s in snapshots]
    for _, raw in encoded:
        validate_snapshot_bytes(raw, supermarket_id="walmart")
    args.output.mkdir(parents=True)
    for snapshot, raw in encoded:
        (args.output / (snapshot["location_id"] + ".json")).write_bytes(raw)
        print(json.dumps({k: snapshot[k] for k in ["location_id", "catalog_products_reported", "skus_extracted", "skus_with_price", "availability_counts", "observed_at_utc"]}))


if __name__ == "__main__":
    main()
