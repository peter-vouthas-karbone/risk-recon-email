"""
Input file archival and incoming file management.

Provides three operations for the daily integration workflow:
  1. archive_current_inputs  – snapshot active files to data/archive/YYYY-MM-DD/
  2. validate_incoming       – sanity-check files in the incoming drop zone
  3. promote_incoming        – move validated incoming files to the active data/ dir
"""

import hashlib
import json
import shutil
from datetime import date, datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
ARCHIVE_DIR = DATA_DIR / "archive"
INCOMING_DIR = DATA_DIR / "incoming"

CANONICAL_MO = "rins_tradesheet.csv"
CANONICAL_FUELS = "fuels_tradesheet.csv"

MO_MARKER_COLUMN = "Trade ID"
FUELS_MARKER_COLUMN = "Trade Number"


def _sha256(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def archive_current_inputs(
    business_date: date,
    mo_path: Path | None = None,
    fuels_path: Path | None = None,
    *,
    force: bool = False,
) -> Path | None:
    """Copy the current active tradesheet files to data/archive/YYYY-MM-DD/.

    Returns the archive directory path, or None if there was nothing to archive.
    Raises FileExistsError if the archive already exists (unless force=True).
    """
    mo_path = mo_path or DATA_DIR / CANONICAL_MO
    fuels_path = fuels_path or DATA_DIR / CANONICAL_FUELS

    files_to_archive = [p for p in (mo_path, fuels_path) if p.exists()]
    if not files_to_archive:
        return None

    archive_day_dir = ARCHIVE_DIR / str(business_date)

    if archive_day_dir.exists() and not force:
        raise FileExistsError(
            f"Archive already exists for {business_date}: {archive_day_dir}\n"
            "Use --force to overwrite."
        )

    archive_day_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "business_date": str(business_date),
        "archived_at": datetime.now().isoformat(),
        "files": {},
    }

    for src in files_to_archive:
        dest = archive_day_dir / src.name
        shutil.copy2(src, dest)
        manifest["files"][src.name] = {
            "sha256": _sha256(dest),
            "size_bytes": dest.stat().st_size,
            "original_path": str(src),
        }

    manifest_path = archive_day_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    return archive_day_dir


def _sniff_csv_type(filepath: Path) -> str | None:
    """Return 'mo' or 'fuels' based on header inspection, or None if unknown."""
    try:
        df = pd.read_csv(filepath, header=0, nrows=0, dtype=str)
        if FUELS_MARKER_COLUMN in df.columns:
            return "fuels"
        if MO_MARKER_COLUMN in df.columns:
            return "mo"
        # MO files sometimes have a summary row above the real header
        df = pd.read_csv(filepath, header=1, nrows=0, dtype=str)
        if MO_MARKER_COLUMN in df.columns:
            return "mo"
    except Exception:
        pass
    return None


def validate_incoming(incoming_dir: Path | None = None) -> dict[str, Path]:
    """Check that the incoming directory has the expected tradesheet files.

    Returns a dict mapping canonical role ('mo', 'fuels') to the incoming file path.
    Raises FileNotFoundError or ValueError on problems.
    """
    incoming_dir = incoming_dir or INCOMING_DIR

    if not incoming_dir.exists():
        raise FileNotFoundError(f"Incoming directory does not exist: {incoming_dir}")

    csv_files = sorted(incoming_dir.glob("*.csv"))
    if not csv_files:
        raise FileNotFoundError(f"No CSV files found in {incoming_dir}")

    detected: dict[str, Path] = {}
    for f in csv_files:
        if f.stat().st_size == 0:
            raise ValueError(f"Incoming file is empty: {f}")

        kind = _sniff_csv_type(f)
        if kind is None:
            raise ValueError(
                f"Cannot identify file type for {f.name} — "
                f"expected '{MO_MARKER_COLUMN}' (MO) or '{FUELS_MARKER_COLUMN}' (Fuels) in headers"
            )
        if kind in detected:
            raise ValueError(
                f"Multiple {kind.upper()} files detected: {detected[kind].name} and {f.name}"
            )
        detected[kind] = f

    missing = {"mo", "fuels"} - set(detected.keys())
    if missing:
        raise FileNotFoundError(
            f"Missing incoming files for: {', '.join(sorted(missing))}. "
            f"Found: {', '.join(f.name for f in csv_files)}"
        )

    return detected


def promote_incoming(
    detected: dict[str, Path],
    incoming_dir: Path | None = None,
) -> dict[str, Path]:
    """Move validated incoming files to data/ with canonical names.

    Returns a dict mapping role ('mo', 'fuels') to the new active file path.
    """
    incoming_dir = incoming_dir or INCOMING_DIR
    promoted: dict[str, Path] = {}

    canonical_names = {
        "mo": CANONICAL_MO,
        "fuels": CANONICAL_FUELS,
    }

    for role, src_path in detected.items():
        dest = DATA_DIR / canonical_names[role]
        shutil.move(str(src_path), str(dest))
        promoted[role] = dest

    remaining = [f for f in incoming_dir.iterdir() if f.name != ".gitkeep"]
    if not remaining:
        pass  # incoming dir is clean

    return promoted
