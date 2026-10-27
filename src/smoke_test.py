"""Smoke test: tiny end-to-end run to validate the pipeline before vast.ai.

Drops one wav into data/raw/, preprocesses it, builds a manifest,
and confirms the data pipeline works end to end.
"""
from __future__ import annotations

from pathlib import Path

from .preprocess import preprocess_file
from .build_manifest import build_manifest


RAW_DIR = Path("data/raw")
PROCESSED_DIR = Path("data/processed")
MANIFEST_DIR = Path("data/manifests")


def run_smoke_test() -> None:
    raw_files = sorted(RAW_DIR.rglob("*.wav"))[:1]
    if not raw_files:
        print("[smoke] No wav found in data/raw. Drop one Arabic wav there and re-run.")
        return

    wav = raw_files[0]
    print(f"[smoke] Processing {wav.name} ...")
    clips, reports = preprocess_file(wav, PROCESSED_DIR / "smoke", prefix="smoke")
    print(f"[smoke] Produced {len(clips)} clips, rejected {len(reports)}")
    for r in reports:
        print(f"[smoke] rejected: {r}")

    if clips:
        build_manifest(
            dialect="msa",
            clips_dir=PROCESSED_DIR / "smoke",
            source_name="smoke_source",
            out_name="smoke_msa.jsonl",
        )
        print("[smoke] Done. Check data/manifests/smoke_msa.jsonl")


if __name__ == "__main__":
    run_smoke_test()
