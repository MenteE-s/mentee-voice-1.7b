"""Download datasets for Arabic dialect TTS fine-tuning."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from .config import RAW_DIR, DATASETS

try:
    from huggingface_hub import snapshot_download
    _HF_AVAILABLE = True
except ImportError:  # pragma: no cover
    _HF_AVAILABLE = False


def download_hf_dataset(repo_id: str, dest: Path, token: Optional[str] = None) -> Path:
    """Download a Hugging Face dataset snapshot to ``dest``."""
    if not _HF_AVAILABLE:
        raise ImportError("huggingface_hub not installed. Run: pip install huggingface_hub")
    dest.mkdir(parents=True, exist_ok=True)
    token = token or os.environ.get("HF_TOKEN")
    snapshot_download(
        repo_id=repo_id,
        repo_type="dataset",
        local_dir=str(dest),
        token=token,
    )
    return dest


def _extract_repo_id(url: str) -> Optional[str]:
    """Extract a Hugging Face repo_id from a dataset URL."""
    if "huggingface.co" not in url:
        return None
    parts = url.replace("https://", "").replace("http://", "").split("/")
    parts = [p for p in parts if p]
    try:
        if "datasets" in parts:
            idx = parts.index("datasets")
            return "/".join(parts[idx + 1 : idx + 3])
        # Collections or model pages: last two segments
        if len(parts) >= 2:
            return "/".join(parts[-2:])
    except IndexError:
        return None
    return None


def download_all_datasets(token: Optional[str] = None) -> None:
    """Download every HF-hosted dataset listed in DATASETS."""
    for key, meta in DATASETS.items():
        if meta["source_type"] != "hf":
            print(f"[skip] {meta['name']}: non-HF source ({meta['source_type']})")
            continue

        repo_id = _extract_repo_id(meta["url"])
        if repo_id is None:
            print(f"[skip] {meta['name']}: could not parse repo_id from {meta['url']}")
            continue

        dest = RAW_DIR / key
        print(f"[download] {meta['name']} -> {repo_id}")
        try:
            download_hf_dataset(repo_id, dest, token=token)
        except Exception as exc:  # noqa: BLE001
            print(f"[error] {key}: {exc}")


def download_dataset(key: str, token: Optional[str] = None) -> Optional[Path]:
    """Download a single dataset by its config key."""
    if key not in DATASETS:
        print(f"[error] unknown dataset key: {key}")
        return None
    meta = DATASETS[key]
    if meta["source_type"] != "hf":
        print(f"[skip] {meta['name']}: non-HF source")
        return None
    repo_id = _extract_repo_id(meta["url"])
    if repo_id is None:
        print(f"[error] could not parse repo_id for {key}")
        return None
    dest = RAW_DIR / key
    return download_hf_dataset(repo_id, dest, token=token)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Download Arabic TTS datasets")
    parser.add_argument("--all", action="store_true", help="Download all HF datasets")
    parser.add_argument("--dataset", default=None, help="Single dataset key from config")
    args = parser.parse_args()

    if args.dataset:
        download_dataset(args.dataset)
    elif args.all:
        download_all_datasets()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
