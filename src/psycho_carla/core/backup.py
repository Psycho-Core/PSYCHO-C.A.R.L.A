"""One-file backup and restore of everything that is *yours*.

A backup is a zip with your config, memory, sessions, knowledge store,
skills and pending approvals. It never contains downloaded models, the
llama.cpp engine, or (unless asked) the encrypted vault with API keys.
Databases are copied with SQLite's own backup API so a backup taken while
C.A.R.L.A. is running is still consistent.
"""

from __future__ import annotations

import io
import shutil
import sqlite3
import tempfile
import time
import zipfile
from pathlib import Path

#: Top-level names inside a backup (and the only ones restore will write).
CONFIG_FILE = "psycho_carla.toml"
DB_NAMES = ("memory.db", "sessions.db", "knowledge.db")
EXTRA_FILES = ("approvals.json",)
VAULT_FILES = ("vault.enc", "vault.key")


def _data_dir(home: Path) -> Path:
    return home / "data"


def _copy_db(src: Path, dst: Path) -> None:
    """Consistent copy of a (possibly in-use) SQLite database."""
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(dst)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def create_backup(home: str | Path, include_vault: bool = False) -> bytes:
    """Return the backup zip as bytes."""
    home = Path(home)
    buf = io.BytesIO()
    with tempfile.TemporaryDirectory() as tmp, \
            zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        cfg = home / CONFIG_FILE
        if cfg.is_file():
            zf.write(cfg, CONFIG_FILE)
        data = _data_dir(home)
        for name in DB_NAMES:
            src = data / name
            if src.is_file():
                snap = Path(tmp) / name
                try:
                    _copy_db(src, snap)
                    zf.write(snap, f"data/{name}")
                except sqlite3.Error:
                    zf.write(src, f"data/{name}")        # not a live DB: plain copy
        for name in EXTRA_FILES:
            if (data / name).is_file():
                zf.write(data / name, f"data/{name}")
        skills = home / "skills"
        if skills.is_dir():
            for path in skills.rglob("*"):
                if path.is_file():
                    zf.write(path, "skills/" + path.relative_to(skills).as_posix())
        if include_vault:
            for name in VAULT_FILES:
                for cand in (home / name, data / name):
                    if cand.is_file():
                        zf.write(cand, name)
        zf.writestr("BACKUP.txt", f"PSYCHO-C.A.R.L.A. backup, {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    return buf.getvalue()


def _allowed(member: str, include_vault: bool) -> bool:
    if member in (CONFIG_FILE, "BACKUP.txt") or (include_vault and member in VAULT_FILES):
        return True
    if member.startswith("data/") and member.count("/") == 1:
        return member[5:] in DB_NAMES + EXTRA_FILES
    return member.startswith("skills/") and ".." not in member.split("/")


def restore_backup(home: str | Path, data: bytes) -> dict:
    """Restore a backup zip into ``home``. The current state is kept first.

    Existing files that would be overwritten are moved to
    ``<home>/backups/pre-restore-<timestamp>/`` so a restore can be undone.
    """
    home = Path(home).resolve()
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return {"ok": False, "message": "that is not a valid backup file"}
    names = zf.namelist()
    if "BACKUP.txt" not in names:
        return {"ok": False, "message": "that zip is not a PSYCHO-C.A.R.L.A. backup"}
    has_vault = any(n in VAULT_FILES for n in names)
    safe = [n for n in names if not n.endswith("/") and _allowed(n, has_vault)]
    for n in safe:
        target = (home / n).resolve()
        if home != target and home not in target.parents:
            return {"ok": False, "message": f"unsafe path in backup: {n}"}
    stash = home / "backups" / f"pre-restore-{time.strftime('%Y%m%d-%H%M%S')}"
    restored = 0
    for n in safe:
        if n == "BACKUP.txt":
            continue
        target = home / n
        if target.exists():
            keep = stash / n
            keep.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), keep)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(zf.read(n))
        restored += 1
    for stale in ("memory.db-wal", "memory.db-shm", "sessions.db-wal", "sessions.db-shm"):
        (home / "data" / stale).unlink(missing_ok=True)
    return {"ok": True, "restored": restored, "previous_state": str(stash) if stash.exists() else "",
            "message": f"restored {restored} file(s) — restart C.A.R.L.A. to load them"}
