"""Inference for fine-tuned VITS MMS-TTS-Arabic model.

Generates Arabic speech from text with dialect conditioning via speaker_id.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf
import torch
from transformers import AutoProcessor, VitsModel

from src.config import BASE_MODEL, DIALECT_TAGS, DIALECT_TO_SPEAKER, SAMPLE_RATE
from src.normalize_text import normalize_arabic


def load_model_for_inference(
    model_id: str = BASE_MODEL,
    checkpoint: Optional[str | Path] = None,
    device: str = "cuda",
):
    """Load VITS MMS-TTS-Arabic model, optionally with a fine-tuned checkpoint."""
    processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
    model = VitsModel.from_pretrained(model_id, trust_remote_code=True)

    if checkpoint is not None:
        state = torch.load(checkpoint, map_location=device)
        model.load_state_dict(state)
        print(f"[infer] Loaded fine-tuned checkpoint: {checkpoint}")

    model = model.to(device)
    model.eval()
    return model, processor


def synthesize(
    model,
    processor,
    text: str,
    dialect: str = "msa",
    speaker_id: Optional[int] = None,
    speed: float = 1.0,
    output_path: Optional[str | Path] = None,
    device: str = "cuda",
) -> np.ndarray:
    """Synthesize Arabic speech with dialect conditioning.

    Args:
        model: loaded VitsModel
        processor: VitsTokenizer
        text: Arabic text to synthesize
        dialect: dialect key (msa, egy, gulf, lev, maghreb, ...)
        speaker_id: override speaker_id (defaults to dialect mapping)
        speed: playback speed multiplier (applied post-hoc by resampling)
        output_path: if set, write wav here
        device: cuda or cpu

    Returns:
        float32 numpy audio array at 16 kHz
    """
    speaker_id = DIALECT_TO_SPEAKER.get(dialect, 0) if speaker_id is None else speaker_id
    normalized = normalize_arabic(text)

    inputs = processor(
        text=normalized,
        return_tensors="pt",
        padding=True,
    )
    input_ids = inputs["input_ids"].to(device)
    attention_mask = inputs["attention_mask"].to(device)
    speaker_id_t = torch.tensor([speaker_id], dtype=torch.long, device=device)

    with torch.no_grad():
        outputs = model(
            input_ids=input_ids,
            attention_mask=attention_mask,
            speaker_id=speaker_id_t,
        )
    audio = outputs.waveform[0].cpu().numpy().astype(np.float32)

    if speed != 1.0:
        # Simple speed change via resampling
        import scipy.signal
        n_out = max(1, int(round(audio.shape[0] / speed)))
        audio = scipy.signal.resample(audio, n_out).astype(np.float32)

    if output_path is not None:
        sf.write(str(output_path), audio, SAMPLE_RATE)
        print(f"[infer] Wrote {output_path} ({len(audio)/SAMPLE_RATE:.2f}s)")

    return audio


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Synthesize Arabic with fine-tuned VITS")
    parser.add_argument("--text", required=True, help="Arabic text to synthesize")
    parser.add_argument("--dialect", default="msa", help="Dialect tag (msa, egy, gulf, ...)")
    parser.add_argument("--speaker-id", type=int, default=None, help="Override speaker_id")
    parser.add_argument("--checkpoint", default=None, help="Path to fine-tuned .pt checkpoint")
    parser.add_argument("--speed", type=float, default=1.0, help="Playback speed")
    parser.add_argument("--output", default="output.wav", help="Output wav path")
    parser.add_argument("--device", default="cuda", help="cuda or cpu")
    args = parser.parse_args()

    model, processor = load_model_for_inference(
        checkpoint=args.checkpoint, device=args.device,
    )
    audio = synthesize(
        model, processor,
        text=args.text,
        dialect=args.dialect,
        speaker_id=args.speaker_id,
        speed=args.speed,
        output_path=args.output,
        device=args.device,
    )
    print(f"[infer] Generated {len(audio)} samples ({len(audio) / SAMPLE_RATE:.2f} s)")


if __name__ == "__main__":
    main()
