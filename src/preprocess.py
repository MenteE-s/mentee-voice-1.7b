"""Audio preprocessing with quality gates: load, resample, trim, normalize, split, validate."""
from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

import numpy as np
import soundfile as sf

from .config import (
    MAX_CLIP_SEC,
    MAX_CLIPPING_RATIO,
    MAX_SILENCE_RATIO,
    MIN_CLIP_SEC,
    MIN_DURATION_SEC,
    MIN_SNR_DB,
    PEAK_CEILING,
    SAMPLE_RATE,
    TOP_DB,
    TARGET_RMS,
)


def load_wav_mono(path: Path) -> Tuple[np.ndarray, int]:
    """Load a wav file as mono float32 at its native sample rate."""
    audio, sr = sf.read(str(path), dtype="float32", always_2d=True)
    if audio.ndim == 2:
        audio = audio.mean(axis=1)
    return np.ascontiguousarray(audio, dtype=np.float32), int(sr)


def resample(audio: np.ndarray, sr: int, target_sr: int = SAMPLE_RATE) -> np.ndarray:
    """Resample with polyphase filtering when scipy is available, else linear interp.

    Nearest-neighbor rounding causes aliasing — never use it for TTS training data.
    """
    audio = np.ascontiguousarray(audio, dtype=np.float32)
    if sr == target_sr or audio.size == 0:
        return audio
    try:
        from math import gcd

        from scipy.signal import resample_poly

        g = gcd(sr, target_sr)
        return np.ascontiguousarray(
            resample_poly(audio, target_sr // g, sr // g).astype(np.float32)
        )
    except ImportError:
        # Linear-interpolation fallback: better than nearest-neighbor.
        n_out = max(1, int(round(audio.shape[0] * target_sr / sr)))
        positions = np.linspace(0.0, audio.shape[0] - 1, n_out)
        lo = np.floor(positions).astype(np.int64)
        hi = np.clip(lo + 1, 0, audio.shape[0] - 1)
        lo = np.clip(lo, 0, audio.shape[0] - 1)
        frac = (positions - lo).astype(np.float32)
        return np.ascontiguousarray(
            (audio[lo] * (1.0 - frac) + audio[hi] * frac).astype(np.float32)
        )


def trim_silence(
    audio: np.ndarray,
    sr: int,
    top_db: float = TOP_DB,
    pad_s: float = 0.05,
) -> np.ndarray:
    """Trim leading/trailing silence with an RMS gate and pad the edges."""
    audio = np.asarray(audio, dtype=np.float32)
    if audio.size == 0:
        return audio
    frame = max(1, int(round(0.025 * sr)))
    hop = max(1, frame // 2)
    if audio.shape[0] > frame:
        n_frames = 1 + int((audio.shape[0] - frame) // hop)
    else:
        n_frames = 1
    frames = np.stack([audio[i * hop : i * hop + frame] for i in range(n_frames)])
    rms = np.sqrt(np.mean(np.square(frames.astype(np.float64)), axis=1))
    threshold = 10.0 ** (top_db / 20.0)
    active = np.nonzero(rms > threshold)[0]
    if active.size == 0:
        return audio
    start = int(round(active[0] * hop))
    end = min(audio.shape[0], int(round((active[-1] + 1) * hop)) + frame)
    trimmed = audio[start:end]
    if pad_s > 0:
        pad = int(round(pad_s * sr))
        trimmed = np.pad(trimmed, (pad, pad), mode="reflect")
    return np.ascontiguousarray(trimmed, dtype=np.float32)


def rms_normalize(audio: np.ndarray, target_rms: float = TARGET_RMS) -> np.ndarray:
    """Scale audio to target RMS, then peak-limit to avoid clipping."""
    audio = np.asarray(audio, dtype=np.float32)
    if audio.size == 0:
        return audio
    rms = float(np.sqrt(np.mean(np.square(audio.astype(np.float64)))))
    if rms <= 0.0:
        return audio
    gain = target_rms / rms
    out = audio * gain
    peak = float(np.max(np.abs(out))) if out.size else 0.0
    if peak > PEAK_CEILING:
        out = out * (PEAK_CEILING / peak)
    return np.ascontiguousarray(out.astype(np.float32))


def _frame_energies(audio: np.ndarray, sr: int) -> np.ndarray:
    frame = max(1, int(round(0.025 * sr)))
    hop = max(1, frame // 2)
    if audio.shape[0] <= frame:
        return np.array([float(np.sqrt(np.mean(np.square(audio.astype(np.float64)))))])
    n_frames = 1 + (audio.shape[0] - frame) // hop
    return np.sqrt(
        np.mean(
            np.square(
                np.stack([audio[i * hop : i * hop + frame] for i in range(n_frames)]).astype(
                    np.float64
                )
            ),
            axis=1,
        )
    )


def compute_snr_db(audio: np.ndarray, sr: int) -> float:
    """Estimate SNR from active vs silent frame energies."""
    energies = _frame_energies(audio, sr)
    if energies.size < 2:
        return 0.0
    noise_floor = float(np.percentile(energies, 10))
    signal_floor = float(np.percentile(energies, 90))
    if noise_floor <= 1e-9:
        return 60.0
    return float(20.0 * np.log10(signal_floor / noise_floor))


def compute_clipping_ratio(audio: np.ndarray, threshold: float = 0.99) -> float:
    """Fraction of samples at or near full scale."""
    if audio.size == 0:
        return 0.0
    return float(np.mean(np.abs(audio) >= threshold))


def compute_silence_ratio(audio: np.ndarray, sr: int, top_db: float = -40.0) -> float:
    """Fraction of frames below the silence threshold."""
    energies = _frame_energies(audio, sr)
    threshold = 10.0 ** (top_db / 20.0)
    return float(np.sum(energies < threshold) / max(energies.size, 1))


def split_into_clips(
    audio: np.ndarray,
    sr: int,
    min_sec: float = MIN_CLIP_SEC,
    max_sec: float = MAX_CLIP_SEC,
) -> List[np.ndarray]:
    """Split long audio into 3-15 s clips on low-energy boundaries."""
    clips: List[np.ndarray] = []
    min_samples = int(min_sec * sr)
    max_samples = int(max_sec * sr)

    if audio.size <= max_samples:
        if audio.size >= min_samples:
            clips.append(audio)
        return clips

    energies = _frame_energies(audio, sr)
    frame = max(1, int(round(0.025 * sr)))
    hop = max(1, frame // 2)
    threshold = float(np.percentile(energies, 20))
    cut_points = np.nonzero(energies < threshold)[0] * hop

    start = 0
    for cut in cut_points:
        if cut - start >= min_samples:
            segment = audio[start : min(cut, start + max_samples)]
            if segment.size >= min_samples:
                clips.append(segment)
            start = cut
    if audio.shape[0] - start >= min_samples:
        clips.append(audio[start:])
    return clips


def quality_check(
    audio: np.ndarray,
    sr: int,
    min_duration_sec: float = MIN_DURATION_SEC,
) -> Tuple[bool, str]:
    """Return (pass, reason). Rejects noisy, clipped, or mostly-silent audio."""
    if audio.size < int(min_duration_sec * sr):
        return False, f"too_short_{audio.size / sr:.1f}s"

    snr = compute_snr_db(audio, sr)
    if snr < MIN_SNR_DB:
        return False, f"low_snr_{snr:.1f}db"

    clip_ratio = compute_clipping_ratio(audio)
    if clip_ratio > MAX_CLIPPING_RATIO:
        return False, f"clipping_{clip_ratio:.4f}"

    silence_ratio = compute_silence_ratio(audio, sr)
    if silence_ratio > MAX_SILENCE_RATIO:
        return False, f"too_silent_{silence_ratio:.2f}"

    return True, "ok"


def preprocess_file(
    wav_path: Path,
    out_dir: Path,
    prefix: str = "clip",
    apply_quality_check: bool = True,
) -> Tuple[List[Path], List[dict]]:
    """Full pipeline: load, resample to 24k, trim, normalize, split, quality-gate, save."""
    y, sr = load_wav_mono(wav_path)
    y = resample(y, sr)
    y = trim_silence(y, SAMPLE_RATE)
    y = rms_normalize(y)

    if apply_quality_check:
        passed, reason = quality_check(y, SAMPLE_RATE)
        if not passed:
            return [], [{"file": str(wav_path), "status": "rejected", "reason": reason}]

    clips = split_into_clips(y, SAMPLE_RATE)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: List[Path] = []
    reports: List[dict] = []

    for i, clip in enumerate(clips):
        if apply_quality_check:
            passed, reason = quality_check(clip, SAMPLE_RATE)
            if not passed:
                reports.append({"file": str(wav_path), "clip": i, "status": "rejected", "reason": reason})
                continue
        out_path = out_dir / f"{prefix}_{i:04d}.wav"
        sf.write(str(out_path), clip, SAMPLE_RATE)
        saved.append(out_path)
    return saved, reports


def preprocess_directory(
    in_dir: Path,
    out_dir: Path,
    prefix: str = "clip",
) -> Tuple[List[Path], List[dict]]:
    """Preprocess all wav files in ``in_dir`` into ``out_dir``."""
    in_dir = Path(in_dir)
    out_dir = Path(out_dir)
    saved: List[Path] = []
    reports: List[dict] = []
    wavs = sorted(in_dir.rglob("*.wav"))
    for i, wav in enumerate(wavs):
        file_prefix = f"{prefix}_{i:04d}"
        file_saved, file_reports = preprocess_file(wav, out_dir, prefix=file_prefix)
        saved.extend(file_saved)
        reports.extend(file_reports)
    return saved, reports
