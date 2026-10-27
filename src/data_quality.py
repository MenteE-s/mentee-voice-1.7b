"""Data quality gates for Arabic TTS training manifests.

Runs after ASR verification to catch:
- Non-Arabic or empty transcripts
- Text/audio duration mismatches
- Near-duplicate clips
- Low-confidence ASR output
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf

from .config import DIALECT_TAGS, SAMPLE_RATE


@dataclass
class QualityIssue:
    record_id: int
    audio_path: str
    check: str
    severity: str  # "error" | "warn"
    detail: str


@dataclass
class QualityReport:
    total: int = 0
    passed: int = 0
    errors: int = 0
    warns: int = 0
    issues: list[QualityIssue] = field(default_factory=list)
    dialect_distribution: dict[str, int] = field(default_factory=dict)
    avg_duration_sec: float = 0.0
    avg_confidence: float = 0.0


def _text_hash(text: str) -> str:
    return hashlib.md5(text.strip().encode("utf-8")).hexdigest()


def _audio_fingerprint(audio_path: str) -> Optional[tuple[float, float, float]]:
    """Simple spectral fingerprint: (centroid, zero_crossing_rate, rms)."""
    try:
        y, sr = sf.read(str(audio_path), dtype="float32", always_2d=True)
        if y.ndim == 2:
            y = y.mean(axis=1)
        if y.size == 0:
            return None
        y = np.ascontiguousarray(y, dtype=np.float32)
        # Spectral centroid proxy via energy-weighted mean of FFT bins
        spectrum = np.abs(np.fft.rfft(y))
        if spectrum.size == 0 or spectrum.sum() == 0:
            centroid = 0.0
        else:
            freqs = np.fft.rfftfreq(y.size, 1.0 / sr)
            centroid = float(np.sum(freqs * spectrum) / np.sum(spectrum))
        zcr = float(np.mean(np.diff(np.signbit(y)).astype(np.float32)))
        rms = float(np.sqrt(np.mean(np.square(y))))
        return (centroid, zcr, rms)
    except Exception:
        return None


def validate_arabic_script(text: str) -> bool:
    """Return True when text contains Arabic script characters."""
    if not text or not text.strip():
        return False
    return any("\u0600" <= ch <= "\u06FF" for ch in text)


def duration_text_sanity(duration_sec: float, text: str, min_cps: float = 2.0, max_cps: float = 12.0) -> bool:
    """Sanity check: characters-per-second should be in a human speech range."""
    if duration_sec <= 0:
        return False
    cps = len(text.strip()) / duration_sec
    return min_cps <= cps <= max_cps


def find_near_duplicates(
    records: list[dict],
    audio_similarity_threshold: float = 0.95,
) -> list[tuple[int, int, str]]:
    """Find near-duplicate records by text hash + audio fingerprint similarity.

    Returns list of (id_a, id_b, reason) pairs.
    """
    dupes: list[tuple[int, int, str]] = []
    text_groups: dict[str, list[dict]] = defaultdict(list)
    for rec in records:
        text = str(rec.get("text", "") or "").strip()
        if text:
            text_groups[_text_hash(text)].append(rec)

    for group in text_groups.values():
        if len(group) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                reason = "duplicate_text"
                fp_a = _audio_fingerprint(str(a.get("audio_path", ""))) if a.get("audio_path") else None
                fp_b = _audio_fingerprint(str(b.get("audio_path", ""))) if b.get("audio_path") else None
                if fp_a and fp_b:
                    diff = np.linalg.norm(np.array(fp_a) - np.array(fp_b))
                    scale = np.linalg.norm(np.array(fp_a)) + np.linalg.norm(np.array(fp_b)) + 1e-9
                    sim = 1.0 - diff / scale
                    if sim >= audio_similarity_threshold:
                        reason = f"duplicate_text_and_audio_{sim:.2f}"
                        dupes.append((int(a.get("id", -1)), int(b.get("id", -1)), reason))
                    else:
                        dupes.append((int(a.get("id", -1)), int(b.get("id", -1)), reason))
                else:
                    dupes.append((int(a.get("id", -1)), int(b.get("id", -1)), reason))
    return dupes


def run_quality_checks(
    records: list[dict],
    min_confidence: float = 0.5,
    check_near_dupes: bool = True,
) -> QualityReport:
    """Run all data quality checks and return a report."""
    report = QualityReport(total=len(records))
    confidences: list[float] = []
    durations: list[float] = []

    for rec in records:
        rec_id = int(rec.get("id", -1))
        audio_path = str(rec.get("audio_path", ""))
        text = str(rec.get("text", "") or "").strip()
        dialect = str(rec.get("dialect", ""))
        confidence = float(rec.get("asr_confidence", 0.0) or 0.0)
        duration = float(rec.get("duration_seconds", 0.0) or 0.0)

        report.dialect_distribution[dialect] = report.dialect_distribution.get(dialect, 0) + 1
        if confidence:
            confidences.append(confidence)
        if duration:
            durations.append(duration)

        rec_issues: list[QualityIssue] = []

        # 1. Empty text
        if not text:
            rec_issues.append(QualityIssue(rec_id, audio_path, "empty_text", "error", "no transcript"))
        # 2. Non-Arabic script
        elif not validate_arabic_script(text):
            rec_issues.append(QualityIssue(rec_id, audio_path, "non_arabic", "error", "text lacks Arabic script"))
        # 3. Duration/text mismatch
        if duration > 0 and text and not duration_text_sanity(duration, text):
            rec_issues.append(
                QualityIssue(
                    rec_id, audio_path, "duration_text_mismatch", "warn",
                    f"{len(text)} chars over {duration:.1f}s",
                )
            )
        # 4. Low ASR confidence
        if confidence and confidence < min_confidence:
            rec_issues.append(
                QualityIssue(rec_id, audio_path, "low_asr_confidence", "warn", f"{confidence:.2f}")
            )

        report.issues.extend(rec_issues)
        if not rec_issues:
            report.passed += 1
        else:
            for iss in rec_issues:
                if iss.severity == "error":
                    report.errors += 1
                else:
                    report.warns += 1

    # 5. Near-duplicate detection
    if check_near_dupes:
        for id_a, id_b, reason in find_near_duplicates(records):
            rec_a = next((r for r in records if int(r.get("id", -1)) == id_a), None)
            rec_b = next((r for r in records if int(r.get("id", -1)) == id_b), None)
            path_a = str(rec_a.get("audio_path", "")) if rec_a else ""
            path_b = str(rec_b.get("audio_path", "")) if rec_b else ""
            report.issues.append(QualityIssue(id_a, path_a, "near_duplicate", "warn", f"dup of {id_b}: {reason}"))
            report.issues.append(QualityIssue(id_b, path_b, "near_duplicate", "warn", f"dup of {id_a}: {reason}"))
            report.warns += 2

    report.avg_duration_sec = float(np.mean(durations)) if durations else 0.0
    report.avg_confidence = float(np.mean(confidences)) if confidences else 0.0
    return report


def print_quality_report(report: QualityReport) -> None:
    print("[data-quality] ============ DATA QUALITY REPORT ============")
    print(f"[data-quality] Total records:   {report.total}")
    print(f"[data-quality] Passed:          {report.passed}")
    print(f"[data-quality] Errors:          {report.errors}")
    print(f"[data-quality] Warnings:        {report.warns}")
    print(f"[data-quality] Avg duration:    {report.avg_duration_sec:.2f}s")
    print(f"[data-quality] Avg confidence:  {report.avg_confidence:.3f}")
    print("[data-quality] Dialect distribution:")
    for d, c in sorted(report.dialect_distribution.items()):
        print(f"[data-quality]   {d}: {c}")
    if report.issues:
        print("[data-quality] Top issues:")
        for iss in report.issues[:20]:
            print(f"[data-quality]   [{iss.severity}] {iss.check}: id={iss.record_id} {iss.detail} ({iss.audio_path})")
    print("[data-quality] ===============================================")


def filter_manifest(
    records: list[dict],
    report: QualityReport,
    drop_errors: bool = True,
    drop_low_confidence: bool = False,
) -> list[dict]:
    """Return records filtered by quality report decisions."""
    error_ids = {
        iss.record_id for iss in report.issues
        if iss.severity == "error" and iss.check in ("empty_text", "non_arabic")
    }
    low_conf_ids = {
        iss.record_id for iss in report.issues
        if iss.check == "low_asr_confidence"
    }
    filtered = []
    for rec in records:
        rid = int(rec.get("id", -1))
        if drop_errors and rid in error_ids:
            continue
        if drop_low_confidence and rid in low_conf_ids:
            continue
        filtered.append(rec)
    return filtered


def load_manifest(manifest_path: str | Path) -> list[dict]:
    records: list[dict] = []
    with open(manifest_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def save_manifest(records: list[dict], manifest_path: str | Path) -> None:
    with open(manifest_path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run data quality checks on a manifest")
    parser.add_argument("manifest", help="Path to JSONL manifest")
    parser.add_argument("--min-confidence", type=float, default=0.5, help="ASR confidence floor")
    parser.add_argument("--no-dupes", action="store_true", help="Skip near-duplicate detection")
    parser.add_argument(
        "--drop-errors", action="store_true",
        help="Drop records with empty/non-Arabic text and write cleaned manifest",
    )
    parser.add_argument(
        "--drop-low-conf", action="store_true",
        help="Drop records below min confidence",
    )
    parser.add_argument(
        "--output", default=None, help="Write cleaned manifest here (overwrites input if omitted)"
    )
    args = parser.parse_args()

    records = load_manifest(args.manifest)
    report = run_quality_checks(
        records, min_confidence=args.min_confidence,
        check_near_dupes=not args.no_dupes,
    )
    print_quality_report(report)

    if args.drop_errors or args.drop_low_conf:
        cleaned = filter_manifest(
            records, report,
            drop_errors=args.drop_errors,
            drop_low_confidence=args.drop_low_conf,
        )
        out = args.output or args.manifest
        save_manifest(cleaned, out)
        print(f"[data-quality] Wrote {len(cleaned)} records -> {out} (dropped {len(records) - len(cleaned)})")


if __name__ == "__main__":
    main()
