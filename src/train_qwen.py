"""Qwen3-TTS 1.7B fine-tuning loop for Arabic dialects.

Fine-tunes Qwen/Qwen3-TTS-12Hz-1.7B-Base on dialect-tagged Arabic data.
Designed to run on a vast.ai GPU VM (A100/H100/RTX 4090).

Usage:
    python -m src.train_qwen --config configs/full.yaml
    python -m src.train_qwen --config configs/smoke.yaml
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf
import torch
import torch.nn as nn
import yaml

# Make src importable when run as `python -m src.train_qwen`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from .config import (
    BASE_MODEL,
    CHECKPOINT_DIR,
    DIALECT_TAGS,
    LOGS_DIR,
    SAMPLE_RATE,
)
from .normalize_text import normalize_arabic


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class Clip:
    """One training sample: normalized text, audio, dialect tag, source."""
    text: str
    audio: np.ndarray
    dialect: str
    source: str
    audio_path: str

    @property
    def duration_seconds(self) -> float:
        return float(len(self.audio) / SAMPLE_RATE)


@dataclass
class TrainConfig:
    model: str = BASE_MODEL
    device: str = "cuda"
    batch_size: int = 8
    grad_accumulation: int = 2
    learning_rate: float = 1e-4
    warmup_steps: int = 50
    epochs: int = 30
    early_stopping_patience: int = 3
    max_phoneme_len: int = 510
    save_every: int = 500
    log_every: int = 10
    mixed_precision: str = "bf16"  # bf16 | fp16 | fp32
    manifest: str = "data/manifests/combined.jsonl"
    clips_dir: str = "data/processed"
    output_dir: str = "checkpoints/full"
    seed: int = 42


# ---------------------------------------------------------------------------
# Manifest loading
# ---------------------------------------------------------------------------


def load_manifest(manifest_path: str | Path) -> list[dict]:
    """Load JSONL manifest records."""
    records: list[dict] = []
    with open(manifest_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def load_clips_from_manifest(
    manifest_path: str | Path,
    clips_dir: str | Path,
) -> list[Clip]:
    """Load audio + text from a manifest into Clip objects."""
    records = load_manifest(manifest_path)
    clips: list[Clip] = []
    for rec in records:
        text = str(rec.get("text", "") or "").strip()
        if not text:
            continue  # skip until ASR-verified text is filled in
        audio_path = rec.get("audio_path", "")
        if not audio_path or not Path(audio_path).exists():
            # Try resolving relative to clips_dir
            alt = Path(clips_dir) / Path(audio_path).name if audio_path else None
            if alt is None or not alt.exists():
                continue
            audio_path = str(alt)
        dialect = str(rec.get("dialect", "<ar-msa>"))
        source = str(rec.get("source", "unknown"))
        y, sr = sf.read(audio_path, dtype="float32", always_2d=True)
        if y.ndim == 2:
            y = y.mean(axis=1)
        y = np.ascontiguousarray(y, dtype=np.float32)
        # Resample if needed
        if sr != SAMPLE_RATE:
            n_out = max(1, int(round(y.shape[0] * SAMPLE_RATE / sr)))
            idx = np.clip(
                np.round(np.linspace(0.0, y.shape[0] - 1, n_out)).astype(np.int64),
                0, y.shape[0] - 1,
            )
            y = y[idx]
        clips.append(
            Clip(
                text=normalize_arabic(text),
                audio=y,
                dialect=dialect,
                source=source,
                audio_path=audio_path,
            )
        )
    return clips


def build_combined_manifest(
    manifest_dir: str | Path,
    out_name: str = "combined.jsonl",
) -> Path:
    """Merge all per-dialect JSONL manifests into one combined file."""
    manifest_dir = Path(manifest_dir)
    out_path = manifest_dir / out_name
    records: list[dict] = []
    for jsonl in sorted(manifest_dir.glob("*.jsonl")):
        if jsonl.name == out_name:
            continue
        with open(jsonl, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    with open(out_path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"[manifest] combined: {len(records)} records -> {out_path}")
    return out_path


# ---------------------------------------------------------------------------
# Model loading (Qwen3-TTS)
# ---------------------------------------------------------------------------


def load_qwen_model(model_id: str, device: str):
    """Load Qwen3-TTS base model. Falls back gracefully if qwen_tts pkg missing."""
    try:
        from qwen_tts import Qwen3TTSModel
        model = Qwen3TTSModel.from_pretrained(model_id, device_map=device)
        return model
    except ImportError:
        print("[warn] qwen_tts package not installed. Install: pip install qwen-tts")
        print("[warn] Returning a placeholder model for smoke-test shape validation.")
        return _PlaceholderQwenModel(device=device)


class _PlaceholderQwenModel(nn.Module):
    """Placeholder so the training loop can run shape checks without the real model."""

    def __init__(self, device: str = "cpu"):
        super().__init__()
        self.device = device
        self.vocab = {p: i for i, p in enumerate([f"<tok_{i}>" for i in range(512)])}
        self.dummy = nn.Linear(1, 1)

    def forward(self, input_ids, ref_s, speed, return_output=True):
        class Out:
            def __init__(self, shape):
                self.audio = torch.zeros(shape, device=self.device)
                self.pred_dur = torch.zeros((shape[0], 1), device=self.device)
        return Out((input_ids.shape[0], 16000))


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------


def build_batch(model, clips: list[Clip], device: str, max_len: int = 512):
    """Assemble clips into a batch of (input_ids, ref_s, audio) tensors."""
    ref_s = torch.zeros((512,), dtype=torch.float32, device=device)
    rows = []
    for clip in clips:
        if not clip.text or clip.audio.size == 0:
            continue
        # Prefix text with dialect tag for multilingual conditioning
        tagged_text = clip.dialect + " " + clip.text
        ids = [i for i in (model.vocab.get(p) for p in tagged_text) if i is not None]
        if len(ids) + 2 > max_len:
            continue
        input_ids = torch.tensor([[0, *ids, 0]], dtype=torch.long, device=device)
        target = torch.from_numpy(np.ascontiguousarray(clip.audio, dtype=np.float32)).unsqueeze(0).to(device)
        rows.append((input_ids, ref_s.unsqueeze(0), target))
    if not rows:
        return None
    stacked = torch.cat([r[0] for r in rows], dim=0)
    refs = torch.cat([r[1] for r in rows], dim=0)
    targets = torch.cat([r[2] for r in rows], dim=0)
    return stacked, refs, targets


def mse_loss(pred: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
    return torch.mean((pred - target) ** 2)


def duration_loss(pred_dur) -> torch.Tensor:
    if pred_dur is None:
        return torch.tensor(0.0, requires_grad=False)
    return pred_dur.float().mean().reshape(1)


def train(model, train_clips, config: TrainConfig, device: str):
    """Fine-tune Qwen3-TTS on Arabic clips with audio + duration loss."""
    model = model.to(device)
    model.train()
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.learning_rate)
    best_state = copy.deepcopy(model.state_dict())
    best_loss = float("inf")
    patience_counter = 0
    global_step = 0

    scaler = None
    if config.mixed_precision == "bf16" and torch.cuda.is_available() and torch.cuda.is_bf16_supported():
        scaler = torch.amp.GradScaler("cuda", dtype=torch.bfloat16)
    elif config.mixed_precision == "fp16" and torch.cuda.is_available():
        scaler = torch.amp.GradScaler("cuda")

    for epoch in range(1, max(1, config.epochs) + 1):
        model.train()
        total_loss = 0.0
        n_batches = 0
        np.random.shuffle(train_clips)

        for start in range(0, len(train_clips), config.batch_size):
            chunk = train_clips[start : start + config.batch_size]
            batch = build_batch(model, chunk, device, config.max_phoneme_len)
            if batch is None:
                continue
            inputs, refs, targets = batch

            optimizer.zero_grad()
            if scaler is not None:
                with torch.autocast(device_type="cuda", dtype=torch.bfloat16):
                    outputs = model(inputs, refs, 1.0, return_output=True)
                    loss = mse_loss(outputs.audio, targets) + 0.1 * duration_loss(outputs.pred_dur)
                scaler.scale(loss).backward()
                if global_step % config.grad_accumulation == 0:
                    scaler.unscale_(optimizer)
                    nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                    scaler.step(optimizer)
                    scaler.update()
                    optimizer.zero_grad()
            else:
                outputs = model(inputs, refs, 1.0, return_output=True)
                loss = mse_loss(outputs.audio, targets) + 0.1 * duration_loss(outputs.pred_dur)
                loss.backward()
                if global_step % config.grad_accumulation == 0:
                    nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                    optimizer.step()
                    optimizer.zero_grad()

            total_loss += float(loss.detach())
            n_batches += 1
            global_step += 1

            if global_step % config.log_every == 0:
                print(f"  step {global_step}: loss {float(loss.detach()):.6f}")

            if global_step % config.save_every == 0:
                ckpt_path = Path(config.output_dir) / f"step_{global_step}.pt"
                ckpt_path.parent.mkdir(parents=True, exist_ok=True)
                torch.save(model.state_dict(), ckpt_path)
                print(f"[checkpoint] saved {ckpt_path}")

        if n_batches:
            avg_loss = total_loss / n_batches
            print(f"epoch {epoch}: avg loss {avg_loss:.6f}")
            if avg_loss < best_loss:
                best_loss = avg_loss
                best_state = copy.deepcopy(model.state_dict())
                patience_counter = 0
                best_path = Path(config.output_dir) / "best.pt"
                best_path.parent.mkdir(parents=True, exist_ok=True)
                torch.save(best_state, best_path)
                print(f"[checkpoint] new best saved {best_path} (loss {avg_loss:.6f})")
            else:
                patience_counter += 1
                if patience_counter >= config.early_stopping_patience:
                    print(f"[early stopping] patience exhausted at epoch {epoch}")
                    break

    model.load_state_dict(best_state)
    return model


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def load_config(path: str | Path) -> TrainConfig:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return TrainConfig(**cfg)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fine-tune Qwen3-TTS 1.7B on Arabic dialects")
    parser.add_argument("--config", required=True, help="Path to YAML config")
    parser.add_argument("--build-manifest", action="store_true", help="Build combined manifest before training")
    args = parser.parse_args()

    config = load_config(args.config)
    device = config.device
    if device == "cuda" and not torch.cuda.is_available():
        print("[warn] CUDA not available, falling back to CPU")
        device = "cpu"
        config.device = "cpu"

    torch.manual_seed(config.seed)
    np.random.seed(config.seed)

    # Build combined manifest if requested
    if args.build_manifest:
        manifest_path = build_combined_manifest(Path(config.manifest).parent)
        config.manifest = str(manifest_path)

    # Load model
    print(f"[model] Loading {config.model} ...")
    model = load_qwen_model(config.model, device)
    print(f"[model] Loaded on {device}")

    # Load clips
    print(f"[data] Loading manifest: {config.manifest}")
    clips = load_clips_from_manifest(config.manifest, config.clips_dir)
    print(f"[data] {len(clips)} clips loaded")

    if len(clips) == 0:
        print("[error] No training clips found. Fill in manifest 'text' fields with ASR-verified transcripts first.")
        sys.exit(1)

    # Train
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[train] Starting fine-tune on {device} ...")
    model = train(model, clips, config, device)

    final_path = output_dir / "final.pt"
    torch.save(model.state_dict(), final_path)
    print(f"[train] Done. Final model saved to {final_path}")


if __name__ == "__main__":
    main()
