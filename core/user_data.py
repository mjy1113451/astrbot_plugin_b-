"""Package-local user-data paths and explicit, non-destructive legacy migration.

Importing this module never creates directories or imports another profile's data.
The host must configure the package runtime before importing business modules.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

from .. import _runtime


def _default_user_data_dir() -> Path:
    """Return the standalone profile; the application directory is read-only."""
    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / "BiliLearn"
    return Path.home() / "AppData" / "Local" / "BiliLearn"


def _select_user_data_dir() -> Path:
    if _runtime.plugin_mode:
        if _runtime.data_root is None:
            raise RuntimeError("Plugin user-data root must be configured before business imports")
        # A host-owned plugin profile must never inherit a global standalone env.
        return Path(_runtime.data_root).expanduser().resolve()
    explicit = os.getenv("BILI_USER_DATA_DIR", "").strip()
    return (Path(explicit).expanduser() if explicit else _default_user_data_dir()).resolve()


USER_DATA_DIR = _select_user_data_dir()
DATA_DIR = USER_DATA_DIR / "Data"
# Source/resources are for reading only, including in non-frozen checkouts.
PROJECT_DIR = (
    Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
)
ARTIFACTS_DIR = USER_DATA_DIR
KNOWLEDGE_BASE_DIR = USER_DATA_DIR / "KnowledgeBase"
HIGHLIGHTS_DIR = USER_DATA_DIR / "highlights"
HTML_EXPORTS_DIR = USER_DATA_DIR / "html_exports"
MINDMAPS_DIR = USER_DATA_DIR / "MindMaps"
WORD_DIR = USER_DATA_DIR / "Word"
QR_CODES_DIR = USER_DATA_DIR / "qr_codes"
BACKUPS_DIR = USER_DATA_DIR / "backups"
TEMP_DIR = USER_DATA_DIR / "temp"
CACHE_DIR = USER_DATA_DIR / "cache"
MODELS_DIR = USER_DATA_DIR / "models"

_USER_DIRECTORIES = (
    DATA_DIR, KNOWLEDGE_BASE_DIR, HIGHLIGHTS_DIR, HTML_EXPORTS_DIR, MINDMAPS_DIR,
    WORD_DIR, QR_CODES_DIR, BACKUPS_DIR, TEMP_DIR, CACHE_DIR, MODELS_DIR,
)
# Only these legacy data entries may be imported. Never copy source, docs,
# caches, launch scripts, or arbitrary files from an old installation.
LEGACY_DATA_ENTRIES = (
    "Data", "KnowledgeBase", "highlights", "html_exports", "MindMaps", "Word",
    "qr_codes", "bot_memory.json", "bot_journal.md", "knowledge_metadata.json",
    "learning_log.md", ".cipher_key",
)
_ACCOUNT_ENTRIES = (
    ".cipher_key", "Data/config.json", "Data/bilibili_cookies.json",
    "Data/accounts.json", "Data/web_auth.json", "Data/web_account.json",
    "Data/web_accounts.json", "Data/web_password.json",
)


def resolve_output_path(
    value: str | os.PathLike[str] | None,
    default: str | os.PathLike[str] | None = None,
) -> Path:
    """Resolve output against this profile without creating anything.

    Empty values use ``default`` (or the profile root). Relative paths retain
    their subdirectories and may not escape the root, including via symlinks.
    Plugin absolute paths must also stay inside the root. Only standalone mode
    permits an explicitly absolute output outside the profile.
    """
    selected = default if value is None or value == "" else value
    path = Path(selected).expanduser() if selected is not None and selected != "" else USER_DATA_DIR
    absolute = path.is_absolute()
    try:
        resolved = (path if absolute else USER_DATA_DIR / path).resolve()
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Cannot resolve output path: {path}") from exc
    if _runtime.plugin_mode or not absolute:
        if not resolved.is_relative_to(USER_DATA_DIR):
            raise ValueError(f"Output path must remain inside user-data root: {path}")
    return resolved


def ensure_user_data_dir() -> Path:
    """Create this profile's directories only; migration is always explicit."""
    # Check every path before creating any directories. Even standalone default
    # folders must not be redirected out of the selected root by symlinks.
    directories = [resolve_output_path(directory.relative_to(USER_DATA_DIR))
                   for directory in _USER_DIRECTORIES]
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
    return USER_DATA_DIR


def _is_link(path: Path) -> bool:
    """Include Windows junctions/reparse points, not just POSIX symlinks."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _has_link_component(path: Path) -> bool:
    return any(_is_link(component) for component in (path, *path.parents))


def managed_file_path(value: str | os.PathLike[str]) -> Path:
    """Private files never opt into standalone external exports or follow links."""
    path = Path(value).expanduser()
    path = path.absolute() if path.is_absolute() else USER_DATA_DIR / path
    if _has_link_component(path):
        raise ValueError("Private data path must not contain links")
    resolved = path.resolve()
    if not resolved.is_relative_to(USER_DATA_DIR):
        raise ValueError("Private data path must remain inside user-data root")
    return resolved


def write_private_json(value: str | os.PathLike[str], data, *, indent=2) -> None:
    """Atomically replace a private JSON file using an exclusive random temp file."""
    target = managed_file_path(value)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=".private-",
                                         suffix=".tmp", dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=indent)
        managed_file_path(target)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _copy_missing(source: Path, destination: Path, copied: list[Path]) -> None:
    """Copy regular files exclusively; never traverse either side's links."""
    if _has_link_component(source) or _has_link_component(destination):
        return
    try:
        info = source.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISDIR(info.st_mode):
        if os.path.lexists(destination) and not destination.is_dir():
            return
        destination.mkdir(parents=True, exist_ok=True)
        for child in sorted(source.iterdir()):
            _copy_missing(child, destination / child.name, copied)
    elif stat.S_ISREG(info.st_mode) and not os.path.lexists(destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(source, flags), "rb") as incoming:
            if not stat.S_ISREG(os.fstat(incoming.fileno()).st_mode):
                return
            try:
                outgoing = destination.open("xb")
            except FileExistsError:
                return
            try:
                with outgoing:
                    shutil.copyfileobj(incoming, outgoing)
            except BaseException:
                destination.unlink(missing_ok=True)
                raise
        copied.append(destination)


def migrate_legacy_data(source: str | os.PathLike[str]) -> list[Path]:
    """Explicitly copy the fixed legacy data set into an unclaimed profile.

    Returns newly copied target files. Existing files are never overwritten,
    source files are never removed, and links on either side are never followed.
    If any account/config/key entry already exists, reject the entire import
    rather than mixing identities or merging credentials. Call before importing
    ``core.config``, which may initialize a new default configuration.
    """
    source_root = Path(source).expanduser().absolute()
    if _has_link_component(source_root):
        raise ValueError("Legacy source must not contain symlinks or junctions")
    if not source_root.is_dir():
        raise FileNotFoundError(f"Legacy data directory does not exist: {source_root}")
    source_root = source_root.resolve()
    if source_root.is_relative_to(USER_DATA_DIR) or USER_DATA_DIR.is_relative_to(source_root):
        raise ValueError("Legacy source and user-data root must not overlap")
    for name in _ACCOUNT_ENTRIES:
        target = USER_DATA_DIR / name
        if _has_link_component(target) or os.path.lexists(target):
            raise ValueError("Target profile already contains account/configuration data")
    ensure_user_data_dir()
    copied: list[Path] = []
    for name in LEGACY_DATA_ENTRIES:
        _copy_missing(source_root / name, USER_DATA_DIR / name, copied)
    return copied
