"""CLI entry point: ASR-verify Arabic transcripts with faster-whisper."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.asr_verify import batch_verify_manifest
from src.config import MANIFEST_DIR


def main() -> None:
    parser = argparse.ArgumentParser(description="ASR-verify Arabic transcripts in a manifest")
    parser.add_argument("--manifest", default=str(MANIFEST_DIR / "combined.jsonl"), help="Input manifest path")
    parser.add_argument("--output", default=None, help="Output manifest path (overwrites input if omitted)")
    parser.add_argument("--language", default="ar", help="Language code for ASR")
    parser.add_argument("--model-size", default="large-v3", help="Whisper model size")
    parser.add_argument("--device", default="cuda", help="cuda or cpu")
    parser.add_argument("--min-confidence", type=float, default=0.5, help="ASR confidence floor")
    parser.add_argument("--low-conf-out", default=str(MANIFEST_DIR / "low_confidence.jsonl"), help="Low-confidence records output")
    args = parser.parse_args()

    batch_verify_manifest(
        args.manifest,
        output_path=args.output,
        language=args.language,
        model_size=args.model_size,
        device=args.device,
        min_confidence=args.min_confidence,
        low_conf_out=args.low_conf_out,
    )


if __name__ == "__main__":
    main()
