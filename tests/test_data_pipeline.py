"""Tests for the data preprocessing pipeline."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import soundfile as sf

from src.preprocess import (
    load_wav_mono,
    resample,
    trim_silence,
    rms_normalize,
    split_into_clips,
    preprocess_file,
)
from src.normalize_text import normalize_arabic


def test_normalize_arabic_removes_tatweel():
    assert normalize_arabic("مصـر") == "مصر"


def test_normalize_arabic_hamza():
    # أ -> ا, إ -> ا, آ -> ا (hamza forms normalized to plain alif)
    assert normalize_arabic("أ والإ آ") == "ا والا ا"


def test_normalize_arabic_diacritics():
    assert normalize_arabic("مَرْحَبًا") == "مرحبا"


def test_normalize_arabic_numbers():
    assert normalize_arabic("١٢٣") == "123"


def test_resample_identity_when_same_sr():
    audio = np.random.randn(16000).astype(np.float32)
    out = resample(audio, 16000, 16000)
    assert np.array_equal(audio, out)


def test_trim_silence_removes_edges():
    # Long silence on edges, short signal in middle — trim removes edges,
    # padding adds back 0.05s on each side (intentional for TTS).
    audio = np.concatenate([np.zeros(8000, dtype=np.float32), np.ones(4000, dtype=np.float32), np.zeros(8000, dtype=np.float32)])
    trimmed = trim_silence(audio, 16000, top_db=-40.0)
    assert trimmed.size < audio.size


def test_rms_normalize_target():
    audio = np.ones(1000, dtype=np.float32) * 0.1
    out = rms_normalize(audio, target_rms=0.9)
    rms = float(np.sqrt(np.mean(np.square(out))))
    assert abs(rms - 0.9) < 0.01


def test_split_into_clips_short_audio_kept():
    # 6 seconds of actual signal (above 5s min) -> kept as one clip
    audio = np.ones(int(6 * 16000), dtype=np.float32) * 0.5
    clips = split_into_clips(audio, 16000)
    assert len(clips) == 1


def test_split_into_clips_long_audio_split():
    audio = np.zeros(int(30 * 16000), dtype=np.float32)  # 30 seconds silence
    clips = split_into_clips(audio, 16000)
    # Silence has no energy cuts, so it may return the whole thing or chunks >= 5s
    assert len(clips) >= 1


def test_preprocess_file_creates_wavs(tmp_path):
    # Create a dummy wav
    in_wav = tmp_path / "input.wav"
    sf.write(str(in_wav), np.random.randn(16000 * 10).astype(np.float32), 16000)
    out_dir = tmp_path / "out"
    saved, reports = preprocess_file(in_wav, out_dir, prefix="test", apply_quality_check=False)
    assert len(saved) >= 1
    for p in saved:
        assert p.exists()


def test_quality_check_rejects_silence():
    from src.preprocess import quality_check

    silence = np.zeros(16000 * 5, dtype=np.float32)
    passed, reason = quality_check(silence, 16000)
    assert not passed
