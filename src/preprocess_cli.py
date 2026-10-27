"""CLI entry point: preprocess raw audio into 16kHz clips."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import RAW_DIR, PROCESSED_DIR, SAMPLE_RATE
from src.preprocess import preprocess_directory


def main() -> None:
    parser = argparse.ArgumentParser(description="Preprocess raw Arabic audio into 16kHz training clips")
    parser.add_argument("--in-dir", default=str(RAW_DIR), help="Directory with raw wav files")
    parser.add_argument("--out-dir", default=str(PROCESSED_DIR), help="Output directory for processed clips")
    parser.add_argument("--prefix", default="clip", help="Filename prefix for output clips")
    parser.add_argument("--no-quality-check", action="store_true", help="Skip quality gates")
    args = parser.parse_args()

    in_dir = Path(args.in_dir)
    out_dir = Path(args.out_dir)
    saved, reports = preprocess_directory(
        in_dir, out_dir, prefix=args.prefix, apply_quality_check=not args.no_quality_check,
    )
    rejected = sum(1 for r in reports if r.get("status") == "rejected")
    print(f"[preprocess] Saved {len(saved)} clips, rejected {rejected} -> {out_dir}")


if __name__ == "__main__":
    main()
