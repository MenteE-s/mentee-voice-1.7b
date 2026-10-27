"""Configuration for Arabic dialect TTS fine-tuning pipeline.

Organization: MenteE — menteeai.org
"""
from __future__ import annotations
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
MANIFEST_DIR = DATA_DIR / "manifests"
CHECKPOINT_DIR = ROOT / "checkpoints"
LOGS_DIR = ROOT / "logs"

for _d in [RAW_DIR, PROCESSED_DIR, MANIFEST_DIR, CHECKPOINT_DIR, LOGS_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Audio settings — 24 kHz mono (Qwen3-TTS codec requirement, non-negotiable)
# ---------------------------------------------------------------------------
SAMPLE_RATE = 24000
TARGET_RMS = 0.2
PEAK_CEILING = 0.98
MIN_CLIP_SEC = 3.0
MAX_CLIP_SEC = 15.0
TOP_DB = -40.0

# Quality gates — clips failing these are rejected before manifest
MIN_SNR_DB = 20.0
MAX_CLIPPING_RATIO = 0.001
MAX_SILENCE_RATIO = 0.30
MIN_DURATION_SEC = 3.0

# ---------------------------------------------------------------------------
# Dialect tags -> wrapped for Qwen3-TTS conditioning
# ---------------------------------------------------------------------------
DIALECT_TAGS = {
    "msa": "<ar-msa>",
    "egy": "<ar-eg>",
    "gulf": "<ar-gulf>",
    "lev": "<ar-lev>",
    "maghreb": "<ar-maghreb>",
    "iraqi": "<ar-iq>",
    "yemeni": "<ar-ye>",
    "saudi": "<ar-sa>",
    "emirati": "<ar-ae>",
    "tunisian": "<ar-tn>",
    "moroccan": "<ar-ma>",
    "algerian": "<ar-dz>",
    "lebanese": "<ar-lb>",
    "syrian": "<ar-sy>",
    "sudanese": "<ar-sd>",
}

# ---------------------------------------------------------------------------
# All datasets from Excel
# ---------------------------------------------------------------------------
DATASETS = {
    # MSA / Foundation
    "slr32_msa": {
        "name": "Arabic Speech Corpus (OpenSLR SLR32)",
        "url": "https://www.openslr.org/32/",
        "dialect": "msa",
        "hours": "3.7",
        "source_type": "openslr",
    },
    "common_voice_ar": {
        "name": "Mozilla Common Voice Arabic",
        "url": "https://commonvoice.mozilla.org/en/datasets",
        "dialect": "msa",
        "hours": "~120 validated",
        "source_type": "common_voice",
    },
    # North African / Maghreb
    "openslr_slr46": {
        "name": "OpenSLR SLR46 (Tunisian MSA)",
        "url": "https://www.openslr.org/46/",
        "dialect": "tunisian",
        "hours": "11.2",
        "source_type": "openslr",
    },
    "openslr_slr89": {
        "name": "OpenSLR SLR89 (Moroccan Darija)",
        "url": "https://www.openslr.org/89/",
        "dialect": "moroccan",
        "hours": "~12.4",
        "source_type": "openslr",
    },
    "openslr_slr108": {
        "name": "OpenSLR SLR108 (Algerian Arabic)",
        "url": "https://www.openslr.org/108/",
        "dialect": "algerian",
        "hours": "~14.5",
        "source_type": "openslr",
    },
    "tunarts": {
        "name": "TunArTTS (Tunisian Darija)",
        "url": "https://huggingface.co/datasets/Elyadata/TunArTTS",
        "dialect": "tunisian",
        "hours": "~5.5",
        "source_type": "hf",
    },
    # Gulf
    "namasa_saudi": {
        "name": "NAMAA Saudi TTS V2",
        "url": "https://huggingface.co/datasets/NAMAA-Space/NAMAA-Saudi-TTS-V2",
        "dialect": "saudi",
        "hours": "~18.4",
        "source_type": "hf",
    },
    "habibi_emirati": {
        "name": "Habibi-TTS Emirati Subset",
        "url": "https://huggingface.co/SWivid/Habibi-TTS",
        "dialect": "emirati",
        "hours": "~15.0",
        "source_type": "hf",
    },
    "kfupm_jrcai": {
        "name": "KFUPM-JRCAI Arabic Speech",
        "url": "https://huggingface.co/datasets/KFUPM-JRCAI/arabic_speech",
        "dialect": "gulf",
        "hours": "~13.5",
        "source_type": "hf",
    },
    # Levantine
    "lasc_lebanese": {
        "name": "Lebanese Arabic Speech Corpus (LASC)",
        "url": "http://catalog.elra.info/en-us/repository/browse/ELRA-S0388/",
        "dialect": "lebanese",
        "hours": "~7.2",
        "source_type": "elra",
    },
    "siraj": {
        "name": "SIRAJ Multidialectal Arabic TTS",
        "url": "https://yousefnafea.github.io/Siraj/",
        "dialect": "multi",
        "hours": "~75",
        "source_type": "siraj",
    },
    # Egyptian
    "madel121_egyptian": {
        "name": "MAdel121 Arabic Egyptian Cleaned",
        "url": "https://huggingface.co/datasets/MAdel121/arabic-egy-cleaned",
        "dialect": "egy",
        "hours": "~72.0",
        "source_type": "hf",
    },
    # Iraqi / Yemeni / Sudanese / Multi
    "lahgtna_v2": {
        "name": "Lahgtna V2 Dialectal Arabic",
        "url": "https://huggingface.co/datasets/oddadmix/dialectal-arabic-lahgtna-v2",
        "dialect": "multi",
        "hours": "~591 cleaned",
        "source_type": "hf",
    },
    "lahgtna_iraqi": {
        "name": "Lahgtna Iraqi Subset",
        "url": "https://huggingface.co/datasets/oddadmix/dialectal-arabic-lahgtna-v2",
        "dialect": "iraqi",
        "hours": "~35.0",
        "source_type": "hf",
    },
    "yemeni_spoken": {
        "name": "Yemeni Spoken Arabic Corpus",
        "url": "https://huggingface.co/collections/FatimahEmadEldin/dialectal-arabic-text-to-speech",
        "dialect": "yemeni",
        "hours": "~18.0",
        "source_type": "hf",
    },
    "sudanese_new_lisan": {
        "name": "New Lisan Sudanese TTS Dataset",
        "url": "https://huggingface.co/datasets/AymanMansour/New-Lisan-Sudanese-TTS-Dataset",
        "dialect": "sudanese",
        "hours": "~1.8",
        "source_type": "hf",
    },
}

# Skipped datasets:
#  - palestinian_levantine: text only, no audio
#  - fatimah_collection: Hugging Face index page, not a dataset itself

# ---------------------------------------------------------------------------
# Dialect sampling ratios for balanced training (sum = 1.0)
# ---------------------------------------------------------------------------
DIALECT_BALANCE = {
    "msa": 0.25,
    "egy": 0.20,
    "gulf": 0.15,
    "lev": 0.15,
    "maghreb": 0.15,
    "iraqi": 0.05,
    "yemeni": 0.05,
}

# ---------------------------------------------------------------------------
# Model & Organization
# ---------------------------------------------------------------------------
BASE_MODEL = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
TOKENIZER_MODEL = "Qwen/Qwen3-TTS-Tokenizer-12Hz"
ORG_NAME = "MenteE"
ORG_DOMAIN = "menteeai.org"
MODEL_NAME_PREFIX = "mentee-arabic-tts"
