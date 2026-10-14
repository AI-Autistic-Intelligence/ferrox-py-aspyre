"""
Distribution Packager for ferrox-py-aspyre-enterprise.
Creates a clean, audit-ready ZIP archive excluding git metadata, caches, and virtualenvs.
"""

from __future__ import annotations

import os
from pathlib import Path
import zipfile

EXCLUDE_DIRS = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    "dist",
    "build",
    ".idea",
    ".vscode",
}

EXCLUDE_EXTENSIONS = {
    ".pyc",
    ".pyo",
    ".pyd",
}

EXCLUDE_FILES = {
    "ferrox-py-aspyre-enterprise.zip",
    ".coverage",
}


def should_exclude(rel_path: Path) -> bool:
    for part in rel_path.parts:
        if part in EXCLUDE_DIRS:
            return True
        if part.endswith(".egg-info"):
            return True
    if rel_path.name in EXCLUDE_FILES:
        return True
    if rel_path.suffix in EXCLUDE_EXTENSIONS:
        return True
    return False


def create_package(output_filename: str = "ferrox-py-aspyre-enterprise.zip") -> Path:
    root_dir = Path(__file__).resolve().parent.parent
    zip_path = root_dir / output_filename

    print(f"📦 Packaging ferrox-py-aspyre from: {root_dir}")
    print(f"🎯 Target archive: {zip_path}")

    if zip_path.exists():
        try:
            zip_path.unlink()
        except Exception:
            pass

    file_count = 0
    total_bytes = 0

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(root_dir):
            # Prune excluded dirs in-place
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.endswith(".egg-info")]

            for file in files:
                abs_file = Path(root) / file
                rel_file = abs_file.relative_to(root_dir)

                if should_exclude(rel_file):
                    continue

                zf.write(abs_file, arcname=f"ferrox-py-aspyre-enterprise/{rel_file.as_posix()}")
                file_count += 1
                total_bytes += abs_file.stat().st_size

    archive_size_kb = zip_path.stat().st_size / 1024
    print(f"✅ Successfully packaged {file_count} files ({archive_size_kb:.1f} KB compressed)")
    print(f"📁 Output file: {zip_path.name}\n")
    return zip_path


if __name__ == "__main__":
    create_package()
