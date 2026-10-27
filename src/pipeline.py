"""Master training pipeline for MenteE Arabic Dialect TTS (VITS MMS-TTS-Arabic fine-tune).

Stages:
  1. download     - Pull datasets from HF/OpenSLR/ELRA
  2. preprocess   - 16kHz mono, trim, normalize, split, quality-gate clips
  3. build_manifest - Per-dialect JSONL manifests + balanced combined manifest
  4. asr_verify   - faster-whisper Arabic transcript verification + confidence gating
  5. data_quality - Arabic script check, duration sanity, near-dup detection, filtering
  6. train_vits   - Fine-tune facebook/mms-tts-ara on Arabic dialect data
  7. infer        - Synthesize Arabic with dialect tags + voice cloning

Usage:
    python -m src.pipeline --stage all          # full run
    python -m src.pipeline --stage preprocess   # single stage
    python -m src.pipeline --stage train        # training only
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def run(cmd: list[str], description: str) -> None:
    print(f"\n{'='*60}")
    print(f"[pipeline] {description}")
    print(f"[pipeline] $ {' '.join(cmd)}")
    print(f"{'='*60}\n")
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        print(f"[pipeline] FAILED: {description} (exit {result.returncode})")
        sys.exit(result.returncode)
    print(f"[pipeline] OK: {description}")


def main() -> None:
    parser = argparse.ArgumentParser(description="MenteE Arabic Dialect TTS training pipeline")
    parser.add_argument(
        "--stage",
        choices=["download", "preprocess", "manifest", "asr_verify", "data_quality", "train", "infer", "all"],
        default="all",
    )
    parser.add_argument("--config", default="configs/full.yaml", help="Training YAML config")
    parser.add_argument("--hf-token", default=None, help="Hugging Face token for gated datasets")
    parser.add_argument("--limit-datasets", default=None, help="Comma-separated dataset keys to download")
    args = parser.parse_args()

    stages = [
        "download", "preprocess", "manifest", "asr_verify", "data_quality", "train", "infer",
    ]
    if args.stage == "all":
        selected = stages
    else:
        selected = [args.stage]

    if "download" in selected:
        cmd = [sys.executable, "-m", "src.download"]
        if args.hf_token:
            cmd += ["--token", args.hf_token]
        if args.limit_datasets:
            cmd += ["--dataset", args.limit_datasets]
        else:
            cmd += ["--all"]
        run(cmd, "Download Arabic TTS datasets")

    if "preprocess" in selected:
        run([sys.executable, "-m", "src.preprocess_cli"], "Preprocess raw audio into 16kHz clips")

    if "manifest" in selected:
        run([sys.executable, "-m", "src.build_manifest_cli"], "Build per-dialect JSONL manifests")

    if "asr_verify" in selected:
        run(
            [sys.executable, "-m", "src.asr_verify_cli"],
            "ASR-verify Arabic transcripts with faster-whisper",
        )

    if "data_quality" in selected:
        run(
            [sys.executable, "-m", "src.data_quality_cli", "--drop-errors", "--drop-low-conf"],
            "Data quality gate: filter bad/non-Arabic/low-confidence records",
        )

    if "train" in selected:
        run(
            [sys.executable, "-m", "src.train_vits", "--config", args.config],
            "Fine-tune VITS MMS-TTS-Arabic on Arabic dialect data",
        )

    if "infer" in selected:
        run(
            [sys.executable, "-m", "src.infer_vits", "--text", "مرحبا، كيف حالك؟", "--dialect", "msa"],
            "Test inference with Arabic text",
        )


if __name__ == "__main__":
    main()
