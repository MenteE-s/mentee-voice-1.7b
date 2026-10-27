"""ASR verification for Arabic transcripts using faster-whisper.

Verifies / corrects transcripts before they go into training manifests.
This ensures the model learns correct pronunciation.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from faster_whisper import WhisperModel
    _WHISPER_AVAILABLE = True
except ImportError:
    _WHISPER_AVAILABLE = False

from .normalize_text import normalize_arabic


@dataclass
class ASRResult:
    text: str
    confidence: float
    language: str
    duration_sec: float


class ASRVerifier:
    """Loads Whisper once and reuses it across many verification calls."""

    def __init__(
        self,
        model_size: str = "large-v3",
        device: str = "cuda",
        language: str = "ar",
        min_confidence: float = 0.5,
    ):
        if not _WHISPER_AVAILABLE:
            raise ImportError("faster-whisper not installed. Run: pip install faster-whisper")
        compute_type = "float16" if device == "cuda" else "int8"
        self.model = WhisperModel(model_size, device=device, compute_type=compute_type)
        self.language = language
        self.min_confidence = min_confidence

    def verify(
        self,
        audio_path: str | Path,
        existing_text: Optional[str] = None,
    ) -> ASRResult:
        """Transcribe audio and return ASR-verified text with confidence.

        existing_text is kept for reference but the ASR output is authoritative.
        """
        segments, info = self.model.transcribe(
            str(audio_path), language=self.language, vad_filter=True
        )
        text_parts = []
        total_logprob = 0.0
        total_tokens = 0
        for seg in segments:
            text_parts.append(seg.text)
            total_logprob += getattr(seg, "avg_logprob", -1.0) * len(seg.text.split())
            total_tokens += max(1, len(seg.text.split()))
        text = "".join(text_parts).strip()
        text = normalize_arabic(text)
        confidence = float(total_logprob / max(total_tokens, 1)) if total_tokens else 0.0
        # Map avg_logprob (negative) to a 0..1 confidence roughly
        confidence = max(0.0, min(1.0, (confidence + 1.0) / 1.0))
        return ASRResult(
            text=text,
            confidence=confidence,
            language=getattr(info, "language", self.language),
            duration_sec=getattr(info, "duration", 0.0),
        )


def batch_verify_manifest(
    manifest_path: str | Path,
    output_path: Optional[str | Path] = None,
    language: str = "ar",
    model_size: str = "large-v3",
    device: str = "cuda",
    min_confidence: float = 0.5,
    low_conf_out: Optional[str | Path] = None,
) -> None:
    """Fill in missing text fields in a manifest using ASR, with confidence gating.

    Records whose ASR confidence falls below ``min_confidence`` are flagged.
    If ``low_conf_out`` is given, those records are written to a separate file
    for manual review instead of being silently dropped.
    """
    import json

    verifier = ASRVerifier(
        model_size=model_size, device=device, language=language,
        min_confidence=min_confidence,
    )

    records = []
    with open(manifest_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    low_conf_records = []
    n_filled = 0
    n_low = 0
    for rec in records:
        if rec.get("text"):
            continue
        audio_path = rec.get("audio_path")
        if not audio_path or not Path(audio_path).exists():
            continue
        try:
            result = verifier.verify(audio_path)
            if result.confidence < min_confidence:
                rec["text"] = result.text
                rec["asr_confidence"] = round(result.confidence, 3)
                rec["asr_flag"] = "low_confidence"
                low_conf_records.append(rec)
                n_low += 1
            else:
                rec["text"] = result.text
                rec["asr_confidence"] = round(result.confidence, 3)
                rec["asr_flag"] = "ok"
                n_filled += 1
        except Exception as exc:  # noqa: BLE001
            print(f"[asr] failed {audio_path}: {exc}")
            rec["text"] = ""
            rec["asr_flag"] = "error"

    out = output_path or manifest_path
    with open(out, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    if low_conf_out and low_conf_records:
        with open(low_conf_out, "w", encoding="utf-8") as f:
            for rec in low_conf_records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(
        f"[asr] Verified manifest written to {out} "
        f"({len(records)} records, {n_filled} filled, {n_low} low-confidence)"
    )
    if low_conf_out:
        print(f"[asr] Low-confidence records written to {low_conf_out}")
