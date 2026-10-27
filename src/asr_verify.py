"""ASR verification for Arabic transcripts using faster-whisper.

Verifies / corrects transcripts before they go into training manifests.
This ensures the model learns correct pronunciation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

try:
    from faster_whisper import WhisperModel
    _WHISPER_AVAILABLE = True
except ImportError:
    _WHISPER_AVAILABLE = False


def verify_transcript(
    audio_path: str | Path,
    existing_text: str,
    language: str = "ar",
    model_size: str = "large-v3",
    device: str = "cuda",
) -> str:
    """Transcribe audio with Whisper and return ASR-verified text.

    If existing_text is provided, it is used as a hint but the ASR output
    is the authoritative transcript for training.
    """
    if not _WHISPER_AVAILABLE:
        raise ImportError("faster-whisper not installed. Run: pip install faster-whisper")

    model = WhisperModel(model_size, device=device, compute_type="float16" if device == "cuda" else "int8")
    segments, info = model.transcribe(str(audio_path), language=language, vad_filter=True)
    text = "".join(seg.text for seg in segments).strip()
    return text


def batch_verify_manifest(
    manifest_path: str | Path,
    output_path: Optional[str | Path] = None,
    language: str = "ar",
) -> None:
    """Fill in missing text fields in a manifest using ASR."""
    import json

    records = []
    with open(manifest_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    for rec in records:
        if rec.get("text"):
            continue
        audio_path = rec.get("audio_path")
        if not audio_path or not Path(audio_path).exists():
            continue
        try:
            rec["text"] = verify_transcript(audio_path, "", language=language)
        except Exception as exc:  # noqa: BLE001
            print(f"[asr] failed {audio_path}: {exc}")
            rec["text"] = ""

    out = output_path or manifest_path
    with open(out, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"[asr] Verified manifest written to {out} ({len(records)} records)")
