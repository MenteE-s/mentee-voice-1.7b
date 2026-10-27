"""CLI entry point: build per-dialect JSONL manifests."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import PROCESSED_DIR, MANIFEST_DIR, DIALECT_TAGS
from src.build_manifest import build_all_manifests, build_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build JSONL manifests from processed clips")
    parser.add_argument("--clips-dir", default=str(PROCESSED_DIR), help="Directory with processed wav clips")
    parser.add_argument("--manifest-dir", default=str(MANIFEST_DIR), help="Output directory for manifests")
    parser.add_argument("--dialect", default=None, help="Single dialect to build manifest for")
    parser.add_argument("--source", default="unknown", help="Source name for the dialect")
    args = parser.parse_args()

    clips_dir = Path(args.clips_dir)
    manifest_dir = Path(args.manifest_dir)
    manifest_dir.mkdir(parents=True, exist_ok=True)

    if args.dialect:
        dialect = args.dialect
        tag = DIALECT_TAGS.get(dialect, f"<ar-{dialect}>")
        out_name = f"{dialect}.jsonl"
        build_manifest(tag, clips_dir, args.source, out_name)
    else:
        # Group clips by dialect subdirectory
        dialect_clips = {}
        source_names = {}
        for subdir in sorted(clips_dir.iterdir()):
            if subdir.is_dir():
                dialect = subdir.name
                dialect_clips[dialect] = subdir
                source_names[dialect] = dialect
        build_all_manifests(dialect_clips, source_names)


if __name__ == "__main__":
    main()
