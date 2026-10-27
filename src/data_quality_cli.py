"""CLI entry point: data quality checks on a manifest."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data_quality import (
    load_manifest, save_manifest, run_quality_checks, print_quality_report,
    filter_manifest,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run data quality checks on a JSONL manifest")
    parser.add_argument("manifest", help="Path to JSONL manifest")
    parser.add_argument("--min-confidence", type=float, default=0.5, help="ASR confidence floor")
    parser.add_argument("--no-dupes", action="store_true", help="Skip near-duplicate detection")
    parser.add_argument("--drop-errors", action="store_true", help="Drop records with empty/non-Arabic text")
    parser.add_argument("--drop-low-conf", action="store_true", help="Drop records below min confidence")
    parser.add_argument("--output", default=None, help="Write cleaned manifest here (overwrites input if omitted)")
    args = parser.parse_args()

    records = load_manifest(args.manifest)
    report = run_quality_checks(
        records, min_confidence=args.min_confidence,
        check_near_dupes=not args.no_dupes,
    )
    print_quality_report(report)

    if args.drop_errors or args.drop_low_conf:
        cleaned = filter_manifest(
            records, report,
            drop_errors=args.drop_errors,
            drop_low_confidence=args.drop_low_conf,
        )
        out = args.output or args.manifest
        save_manifest(cleaned, out)
        print(f"[data-quality] Wrote {len(cleaned)} records -> {out} (dropped {len(records) - len(cleaned)})")


if __name__ == "__main__":
    main()
