"""Build JSONL manifests from processed clips with dedup, balancing, and Qwen export."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

import soundfile as sf

from .config import DIALECT_BALANCE, DIALECT_TAGS, MANIFEST_DIR


@dataclass
class ManifestRecord:
    id: int
    text: str
    audio_path: str
    dialect: str
    source: str
    duration_seconds: float


def _text_hash(text: str) -> str:
    return hashlib.md5(text.strip().encode("utf-8")).hexdigest()


def _clip_duration(wav: Path) -> float:
    try:
        return float(sf.info(str(wav)).duration)
    except Exception:
        return 0.0


def deduplicate_by_text(records: List[ManifestRecord]) -> List[ManifestRecord]:
    """Drop clips with duplicate normalized text."""
    seen: set = set()
    unique: List[ManifestRecord] = []
    for rec in records:
        h = _text_hash(rec.text or rec.audio_path)
        if h in seen:
            continue
        seen.add(h)
        unique.append(rec)
    return unique


def balance_by_dialect(
    records: List[ManifestRecord],
    target_ratios: Optional[Dict[str, float]] = None,
) -> List[ManifestRecord]:
    """Sub-sample records to match target dialect ratios."""
    if target_ratios is None:
        target_ratios = DIALECT_BALANCE
    by_dialect: Dict[str, List[ManifestRecord]] = defaultdict(list)
    for rec in records:
        by_dialect[rec.dialect].append(rec)
    total = len(records)
    if total == 0:
        return []
    selected: List[ManifestRecord] = []
    for dialect, ratio in target_ratios.items():
        pool = sorted(by_dialect.get(dialect, []), key=lambda r: r.audio_path)
        if not pool:
            continue
        take = min(int(total * ratio), len(pool))
        selected.extend(pool[:take])
    selected.sort(key=lambda r: (r.dialect, r.audio_path))
    return selected


def build_manifest(
    dialect: str,
    clips_dir: Path,
    source_name: str,
    out_name: str,
) -> List[ManifestRecord]:
    """Create a JSONL manifest for one dialect with real durations.

    The ``text`` field stays empty until the ASR verification step fills it.
    """
    tag = DIALECT_TAGS.get(dialect, f"<ar-{dialect}>")
    clips_dir = Path(clips_dir)
    wavs = sorted(clips_dir.glob("*.wav"))
    records: List[ManifestRecord] = []
    for i, wav in enumerate(wavs):
        records.append(
            ManifestRecord(
                id=i,
                text="",
                audio_path=str(wav),
                dialect=tag,
                source=source_name,
                duration_seconds=round(_clip_duration(wav), 2),
            )
        )
    records = deduplicate_by_text(records)
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    out_path = MANIFEST_DIR / out_name
    with open(out_path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")
    print(f"[manifest] {out_name}: {len(records)} clips -> {out_path}")
    return records


def build_all_manifests(
    dialect_clips: dict[str, Path],
    source_names: dict[str, str],
) -> List[str]:
    """Build manifests for multiple dialects at once."""
    written: List[str] = []
    for dialect, clips_dir in dialect_clips.items():
        out_name = f"{dialect}.jsonl"
        build_manifest(dialect, clips_dir, source_names.get(dialect, "unknown"), out_name)
        written.append(str(MANIFEST_DIR / out_name))
    return written


def export_qwen_raw_jsonl(
    manifest_path: str | Path,
    out_path: str | Path,
    ref_audio: str | Path,
) -> Path:
    """Convert an internal manifest to official Qwen SFT input format.

    Official format per QwenLM/Qwen3-TTS/finetuning: each line is
    {"audio": path, "text": transcript, "ref_audio": path}.
    The same ref_audio for all samples is recommended for speaker consistency.
    Only records with non-empty text are exported.
    """
    manifest_path = Path(manifest_path)
    out_path = Path(out_path)
    exported = 0
    with open(manifest_path, "r", encoding="utf-8") as fin, open(out_path, "w", encoding="utf-8") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            text = str(rec.get("text", "") or "").strip()
            audio = rec.get("audio_path", "")
            if not text or not audio:
                continue
            fout.write(
                json.dumps(
                    {"audio": str(audio), "text": text, "ref_audio": str(ref_audio)},
                    ensure_ascii=False,
                )
                + "\n"
            )
            exported += 1
    print(f"[export] {exported} records -> {out_path}")
    return out_path
