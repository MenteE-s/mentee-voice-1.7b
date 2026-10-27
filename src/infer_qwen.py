"""Inference for fine-tuned Qwen3-TTS Arabic model.

Generates audio from text using a trained checkpoint.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf
import torch

from .config import BASE_MODEL, DIALECT_TAGS, SAMPLE_RATE
from .normalize_text import normalize_arabic


def load_model_for_inference(
    model_id: str = BASE_MODEL,
    checkpoint: Optional[str | Path] = None,
    device: str = "cuda",
):
    """Load Qwen3-TTS model, optionally with a fine-tuned checkpoint."""
    try:
        from qwen_tts import Qwen3TTSModel
        model = Qwen3TTSModel.from_pretrained(model_id, device_map=device)
    except ImportError:
        raise ImportError("qwen_tts package not installed. Run: pip install qwen-tts")

    if checkpoint is not None:
        state = torch.load(checkpoint, map_location=device)
        model.load_state_dict(state)
        print(f"[infer] Loaded fine-tuned checkpoint: {checkpoint}")

    model.eval()
    return model


def synthesize(
    model,
    text: str,
    dialect: str = "msa",
    ref_audio: Optional[str | Path] = None,
    speed: float = 1.0,
    output_path: Optional[str | Path] = None,
) -> np.ndarray:
    """Synthesize Arabic speech with dialect tag.

    Args:
        model: loaded Qwen3TTSModel
        text: Arabic text to synthesize
        dialect: dialect key (msa, egy, gulf, lev, maghreb, etc.)
        ref_audio: optional reference wav for voice cloning
        speed: playback speed multiplier
        output_path: if set, write wav here

    Returns:
        float32 numpy audio array at 24 kHz (Qwen3-TTS output rate)
    """
    tag = DIALECT_TAGS.get(dialect, f"<ar-{dialect}>")
    normalized = normalize_arabic(text)
    tagged_text = tag + " " + normalized

    # Build reference speaker embedding if ref_audio provided
    ref_s = None
    if ref_audio is not None:
        import soundfile as sf
        y, sr = sf.read(str(ref_audio), dtype="float32", always_2d=True)
        if y.ndim == 2:
            y = y.mean(axis=1)
        y = np.ascontiguousarray(y, dtype=np.float32)
        ref_s = torch.from_numpy(y[:512]).float().unsqueeze(0)
        if ref_s.shape[1] < 512:
            ref_s = torch.nn.functional.pad(ref_s, (0, 512 - ref_s.shape[1]))

    with torch.no_grad():
        # Qwen3-TTS generate signature may vary; this is the common pattern
        audio = model.generate(
            text=tagged_text,
            speaker_embedding=ref_s,
            speed=speed,
        )

    if isinstance(audio, torch.Tensor):
        audio = audio.detach().cpu().numpy()
    audio = np.ascontiguousarray(audio, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=-1)

    if output_path is not None:
        sf.write(str(output_path), audio, SAMPLE_RATE)
        print(f"[infer] Wrote {output_path}")

    return audio


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Synthesize Arabic with fine-tuned Qwen3-TTS")
    parser.add_argument("--text", required=True, help="Arabic text to synthesize")
    parser.add_argument("--dialect", default="msa", help="Dialect tag (msa, egy, gulf, ...)")
    parser.add_argument("--checkpoint", default=None, help="Path to fine-tuned .pt checkpoint")
    parser.add_argument("--ref-audio", default=None, help="Reference wav for voice cloning")
    parser.add_argument("--speed", type=float, default=1.0, help="Playback speed")
    parser.add_argument("--output", default="output.wav", help="Output wav path")
    parser.add_argument("--device", default="cuda", help="cuda or cpu")
    args = parser.parse_args()

    model = load_model_for_inference(
        checkpoint=args.checkpoint,
        device=args.device,
    )
    audio = synthesize(
        model,
        text=args.text,
        dialect=args.dialect,
        ref_audio=args.ref_audio,
        speed=args.speed,
        output_path=args.output,
    )
    print(f"[infer] Generated {len(audio)} samples ({len(audio) / SAMPLE_RATE:.2f} s)")


if __name__ == "__main__":
    main()
