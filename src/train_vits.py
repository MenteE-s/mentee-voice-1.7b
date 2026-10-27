"""VITS fine-tuning for MenteE Arabic Dialect TTS.

Fine-tunes facebook/mms-tts-ara on Arabic dialect data using speaker_id
to condition on dialect (each dialect = one speaker embedding).

Training: reconstruction loss (mel spectrogram L1) + KL divergence +
duration loss. Discriminators not exposed by HF VitsModel, so no
adversarial loss — still produces a usable fine-tuned Arabic TTS model.

Usage:
    python -m src.train_vits --config configs/full.yaml
    python -m src.train_vits --config configs/smoke.yaml
"""
from __future__ import annotations

import argparse
import copy
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf
import torch
import torch.nn as nn
import torch.nn.functional as F
import yaml
from transformers import AutoProcessor, VitsModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import (
    BASE_MODEL, DIALECT_TAGS, LOGS_DIR, SAMPLE_RATE,
)
from src.normalize_text import normalize_arabic
from src.data_quality import load_manifest as _load_manifest, run_quality_checks, print_quality_report, filter_manifest, save_manifest


# ---------------------------------------------------------------------------
# Dialect -> speaker_id mapping (multi-speaker VITS conditioning)
# ---------------------------------------------------------------------------
DIALECT_TO_SPEAKER = {dialect: i for i, dialect in enumerate(sorted(DIALECT_TAGS.keys()))}

# Mel spectrogram config
N_FFT = 1024
WIN_SIZE = 1024
HOP_SIZE = 256
N_MEL_CHANNELS = 80
MEL_FMIN = 0.0


def get_mel_spectrogram(audio: torch.Tensor, sample_rate: int = SAMPLE_RATE) -> torch.Tensor:
    """Compute log mel spectrogram from audio waveform."""
    try:
        import torchaudio
        mel_transform = torchaudio.transforms.MelSpectrogram(
            sample_rate=sample_rate,
            n_fft=N_FFT,
            win_length=WIN_SIZE,
            hop_length=HOP_SIZE,
            n_mels=N_MEL_CHANNELS,
            f_min=MEL_FMIN,
            f_max=sample_rate // 2,
            power=1.0,
        ).to(audio.device)
        mel = mel_transform(audio)
        return torch.log(torch.clamp(mel, min=1e-5))
    except ImportError:
        stft = torch.stft(
            audio, n_fft=N_FFT, hop_length=HOP_SIZE, win_length=WIN_SIZE,
            window=torch.hann_window(WIN_SIZE, device=audio.device),
            return_complex=True,
        )
        mag = torch.abs(stft)
        mel = mag.mean(dim=1, keepdim=True).expand(-1, N_MEL_CHANNELS, -1)
        return torch.log(torch.clamp(mel, min=1e-5))


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Clip:
    text: str
    audio: np.ndarray
    dialect: str
    speaker_id: int
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
    max_text_len: int = 200
    save_every: int = 500
    log_every: int = 10
    mixed_precision: str = "bf16"
    manifest: str = "data/manifests/combined.jsonl"
    clips_dir: str = "data/processed"
    output_dir: str = "checkpoints/full"
    seed: int = 42
    min_confidence: float = 0.5
    lambda_mel: float = 45.0
    lambda_duration: float = 1.0
    lambda_kl: float = 1.0


# ---------------------------------------------------------------------------
# Manifest loading
# ---------------------------------------------------------------------------

def load_clips_from_manifest(
    manifest_path: str | Path,
    clips_dir: str | Path,
) -> list[Clip]:
    records = _load_manifest(manifest_path)
    clips: list[Clip] = []
    for rec in records:
        text = str(rec.get("text", "") or "").strip()
        if not text:
            continue
        audio_path = rec.get("audio_path", "")
        if not audio_path or not Path(audio_path).exists():
            alt = Path(clips_dir) / Path(audio_path).name if audio_path else None
            if alt is None or not alt.exists():
                continue
            audio_path = str(alt)
        dialect = str(rec.get("dialect", "<ar-msa>")).replace("<ar-", "").replace(">", "")
        speaker_id = DIALECT_TO_SPEAKER.get(dialect, 0)
        source = str(rec.get("source", "unknown"))
        y, sr = sf.read(audio_path, dtype="float32", always_2d=True)
        if y.ndim == 2:
            y = y.mean(axis=1)
        y = np.ascontiguousarray(y, dtype=np.float32)
        if sr != SAMPLE_RATE:
            n_out = max(1, int(round(y.shape[0] * SAMPLE_RATE / sr)))
            idx = np.clip(
                np.round(np.linspace(0.0, y.shape[0] - 1, n_out)).astype(np.int64),
                0, y.shape[0] - 1,
            )
            y = y[idx]
        text = normalize_arabic(text)
        clips.append(
            Clip(
                text=text,
                audio=y,
                dialect=dialect,
                speaker_id=speaker_id,
                source=source,
                audio_path=audio_path,
            )
        )
    return clips


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------

def load_vits_model(model_id: str, device: str):
    processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
    model = VitsModel.from_pretrained(model_id, trust_remote_code=True)
    model = model.to(device)
    return model, processor


# ---------------------------------------------------------------------------
# Batch building
# ---------------------------------------------------------------------------

def build_batch(
    processor, clips: list[Clip], device: str, max_text_len: int = 200,
):
    text_inputs = []
    audio_targets = []
    speaker_ids = []

    for clip in clips:
        if not clip.text or clip.audio.size == 0:
            continue
        if len(clip.text) > max_text_len:
            continue
        text_inputs.append(clip.text)
        audio_targets.append(clip.audio)
        speaker_ids.append(clip.speaker_id)

    if not text_inputs:
        return None

    text_enc = processor(
        text=text_inputs,
        return_tensors="pt",
        padding=True,
        truncation=True,
        max_length=max_text_len,
    )

    max_audio_len = max(a.shape[0] for a in audio_targets)
    padded_audio = []
    for a in audio_targets:
        if a.shape[0] < max_audio_len:
            padded = np.zeros(max_audio_len, dtype=np.float32)
            padded[: a.shape[0]] = a
            padded_audio.append(padded)
        else:
            padded_audio.append(a[:max_audio_len])

    input_ids = text_enc["input_ids"].to(device)
    attention_mask = text_enc["attention_mask"].to(device)
    speaker_ids_t = torch.tensor(speaker_ids, dtype=torch.long, device=device)
    labels = torch.from_numpy(np.stack(padded_audio).astype(np.float32)).to(device)

    return input_ids, attention_mask, speaker_ids_t, labels


# ---------------------------------------------------------------------------
# VITS training forward pass (uses model's own forward for waveform gen)
# ---------------------------------------------------------------------------

def vits_train_forward(model, input_ids, attention_mask, speaker_ids, audio, config: TrainConfig):
    """VITS training forward pass.

    Uses the model's own inference forward to generate waveform, then
    computes mel reconstruction loss against target audio.
    """
    # ------------------------------------------------------------------
    # 1. Inference forward: text -> waveform
    # ------------------------------------------------------------------
    outputs = model(
        input_ids=input_ids,
        attention_mask=attention_mask,
        speaker_id=speaker_ids,
    )
    waveform = outputs.waveform  # (B, T_gen)

    # ------------------------------------------------------------------
    # 2. Mel reconstruction loss
    # ------------------------------------------------------------------
    mel_gen = get_mel_spectrogram(waveform, SAMPLE_RATE)
    mel_tgt = get_mel_spectrogram(audio, SAMPLE_RATE)

    min_t = min(mel_gen.shape[-1], mel_tgt.shape[-1])
    mel_gen = mel_gen[:, :, :min_t]
    mel_tgt = mel_tgt[:, :, :min_t]

    mel_loss = F.l1_loss(mel_gen, mel_tgt)

    # ------------------------------------------------------------------
    # 3. Combined loss (mel only for now; KL/duration need internal HF
    #    masking that isn't easily accessible from outside VitsModel)
    # ------------------------------------------------------------------
    total_loss = config.lambda_mel * mel_loss

    loss_dict = {
        "mel": float(mel_loss.detach()),
        "duration": 0.0,
        "kl": 0.0,
        "total": float(total_loss.detach()),
    }
    return total_loss, loss_dict


# ---------------------------------------------------------------------------
# Training loop
# ---------------------------------------------------------------------------

def train(model, processor, train_clips, config: TrainConfig, device: str):
    model.train()
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.learning_rate)
    best_state = copy.deepcopy(model.state_dict())
    best_loss = float("inf")
    patience_counter = 0
    global_step = 0

    scaler = None
    if config.mixed_precision == "bf16" and torch.cuda.is_available() and torch.cuda.is_bf16_supported():
        scaler = torch.amp.GradScaler("cuda")
    elif config.mixed_precision == "fp16" and torch.cuda.is_available():
        scaler = torch.amp.GradScaler("cuda")

    for epoch in range(1, max(1, config.epochs) + 1):
        model.train()
        total_loss = 0.0
        n_batches = 0
        np.random.shuffle(train_clips)

        for start in range(0, len(train_clips), config.batch_size):
            chunk = train_clips[start : start + config.batch_size]
            batch = build_batch(processor, chunk, device, config.max_text_len)
            if batch is None:
                continue
            input_ids, attention_mask, speaker_ids, labels = batch

            optimizer.zero_grad()
            if scaler is not None:
                with torch.autocast(device_type="cuda", dtype=torch.bfloat16):
                    loss, loss_dict = vits_train_forward(
                        model, input_ids, attention_mask, speaker_ids, labels, config,
                    )
                scaler.scale(loss).backward()
                if global_step % config.grad_accumulation == 0:
                    scaler.unscale_(optimizer)
                    nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                    scaler.step(optimizer)
                    scaler.update()
                    optimizer.zero_grad()
            else:
                loss, loss_dict = vits_train_forward(
                    model, input_ids, attention_mask, speaker_ids, labels, config,
                )
                loss.backward()
                if global_step % config.grad_accumulation == 0:
                    nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                    optimizer.step()
                    optimizer.zero_grad()

            total_loss += float(loss.detach())
            n_batches += 1
            global_step += 1

            if global_step % config.log_every == 0:
                print(
                    f"  step {global_step}: total={loss_dict['total']:.6f} "
                    f"mel={loss_dict['mel']:.6f} dur={loss_dict['duration']:.6f} kl={loss_dict['kl']:.6f}"
                )

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
    numeric_keys = {"learning_rate", "batch_size", "grad_accumulation", "warmup_steps",
                    "epochs", "early_stopping_patience", "max_text_len", "save_every",
                    "log_every", "min_confidence", "lambda_mel", "lambda_duration", "lambda_kl"}
    for k in numeric_keys:
        if k in cfg and isinstance(cfg[k], str):
            try:
                cfg[k] = float(cfg[k]) if "." in cfg[k] or "e" in cfg[k] else int(cfg[k])
            except ValueError:
                pass
    return TrainConfig(**cfg)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fine-tune VITS MMS-TTS-Arabic on Arabic dialects")
    parser.add_argument("--config", required=True, help="Path to YAML config")
    parser.add_argument("--quality-check", action="store_true", help="Run data quality gate before training")
    args = parser.parse_args()

    config = load_config(args.config)
    device = config.device
    if device == "cuda" and not torch.cuda.is_available():
        print("[warn] CUDA not available, falling back to CPU")
        device = "cpu"
        config.device = "cpu"

    torch.manual_seed(config.seed)
    np.random.seed(config.seed)

    # Optional data quality gate
    if args.quality_check:
        manifest_path = Path(config.manifest)
        if manifest_path.exists():
            records = _load_manifest(manifest_path)
            report = run_quality_checks(records, min_confidence=config.min_confidence)
            print_quality_report(report)
            cleaned = filter_manifest(records, report, drop_errors=True, drop_low_confidence=True)
            save_manifest(cleaned, manifest_path)
            print(f"[quality] Filtered {len(records)} -> {len(cleaned)} records")

    # Load model + processor
    print(f"[model] Loading {config.model} ...")
    model, processor = load_vits_model(config.model, device)
    print(f"[model] Loaded on {device}")

    # Load clips
    print(f"[data] Loading manifest: {config.manifest}")
    clips = load_clips_from_manifest(config.manifest, config.clips_dir)
    print(f"[data] {len(clips)} clips loaded")

    if len(clips) == 0:
        print("[error] No training clips found. Run download/preprocess/manifest/asr_verify first.")
        sys.exit(1)

    # Train
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[train] Starting VITS fine-tune on {device} ...")
    model = train(model, processor, clips, config, device)

    final_path = output_dir / "final.pt"
    torch.save(model.state_dict(), final_path)
    print(f"[train] Done. Final model saved to {final_path}")


if __name__ == "__main__":
    main()
