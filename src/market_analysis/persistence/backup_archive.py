"""Portable, checksum-verified local data archive without credentials or live PG volume."""

from __future__ import annotations

import json
import stat
import zipfile
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import IO, Any

BACKUP_FORMAT_VERSION = "stream-analysis-backup-v1"
_MANIFEST = "backup-manifest.json"
_DUMP = "postgres.dump"
_DATA_FOLDERS = frozenset({"datasets", "artifacts", "exports"})
_CHUNK_SIZE = 1024 * 1024
_MAX_MANIFEST_BYTES = 16 * 1024 * 1024


class BackupArchiveError(ValueError):
    """The archive or destination does not satisfy the portable-backup contract."""


def _copy_hash(source: IO[bytes], target: IO[bytes] | None = None) -> tuple[int, str]:
    digest = sha256()
    size = 0
    while chunk := source.read(_CHUNK_SIZE):
        digest.update(chunk)
        size += len(chunk)
        if target is not None:
            target.write(chunk)
    return size, digest.hexdigest()


def _safe_member(name: str) -> bool:
    path = PurePosixPath(name)
    if not name or name.startswith("/") or "\\" in name or "//" in name:
        return False
    if any(part in {"", ".", ".."} for part in name.split("/")):
        return False
    if str(path) != name:
        return False
    if name == _DUMP:
        return True
    return len(path.parts) >= 3 and path.parts[0] == "data" and path.parts[1] in _DATA_FOLDERS


def _regular_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise BackupArchiveError(f"backup input must be a regular file: {path}")


def _archive_inputs(data_root: Path, dump_path: Path) -> list[tuple[str, Path]]:
    _regular_file(dump_path)
    result = [(_DUMP, dump_path)]
    for folder_name in sorted(_DATA_FOLDERS):
        folder = data_root / folder_name
        if folder.is_symlink():
            raise BackupArchiveError(f"backup folder must not be a symlink: {folder}")
        if not folder.exists():
            continue
        if not folder.is_dir():
            raise BackupArchiveError(f"backup folder is not a directory: {folder}")
        for path in sorted(folder.rglob("*")):
            if path.is_symlink():
                raise BackupArchiveError(f"backup input must not be a symlink: {path}")
            name_parts = path.relative_to(data_root).parts
            if any(
                part == ".env" or part.startswith(".env.")
                or part.lower().startswith("credentials")
                or part.lower().endswith((".key", ".pem"))
                for part in name_parts
            ):
                raise BackupArchiveError(f"backup contains a possible credential file: {path}")
            if folder_name == "datasets" and any(
                part.startswith(".staging-") for part in name_parts
            ):
                raise BackupArchiveError("unfinished dataset publication must be resolved")
            if path.is_dir():
                continue
            _regular_file(path)
            name = "data/" + path.relative_to(data_root).as_posix()
            if not _safe_member(name):
                raise BackupArchiveError(f"unsafe backup input path: {name}")
            result.append((name, path))
    return result


def create_archive(data_root: Path, dump_path: Path, archive_path: Path) -> Path:
    """Write a new archive; never replace an existing archive or include the PG volume."""
    data_root = Path(data_root)
    if data_root.is_symlink() or not data_root.is_dir():
        raise BackupArchiveError("backup data root must be an existing real directory")
    archive_path = Path(archive_path)
    if archive_path.resolve().is_relative_to(data_root.resolve()):
        raise BackupArchiveError("archive output must be outside the data root")
    inputs = _archive_inputs(data_root, Path(dump_path))
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with archive_path.open("xb") as output:
        try:
            with zipfile.ZipFile(
                output, "w", compression=zipfile.ZIP_STORED, allowZip64=True,
            ) as archive:
                files: dict[str, dict[str, object]] = {}
                for name, path in inputs:
                    with path.open("rb") as source, archive.open(
                        name, "w", force_zip64=True,
                    ) as target:
                        size, checksum = _copy_hash(source, target)
                    files[name] = {"size": size, "sha256": checksum}
                manifest: dict[str, object] = {
                    "format_version": BACKUP_FORMAT_VERSION,
                    "files": files,
                }
                archive.writestr(
                    _MANIFEST,
                    json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False).encode("utf-8") + b"\n",
                )
        except Exception:
            # A failed new archive is partial, never a valid backup.
            archive_path.unlink(missing_ok=True)
            raise
    try:
        verify_archive(archive_path)
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise
    return archive_path


def verify_archive(archive_path: Path) -> dict[str, dict[str, object]]:
    """Validate structure and every byte before any restore mutation."""
    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)) or names.count(_MANIFEST) != 1:
                raise BackupArchiveError("archive contains duplicate or missing manifest members")
            for info in infos:
                if info.filename != _MANIFEST and not _safe_member(info.filename):
                    raise BackupArchiveError(f"unsafe archive member: {info.filename}")
                if info.compress_type != zipfile.ZIP_STORED:
                    raise BackupArchiveError("compressed archive members are unsupported")
                mode = info.external_attr >> 16
                if info.is_dir() or stat.S_IFMT(mode) not in {0, stat.S_IFREG}:
                    raise BackupArchiveError(
                        f"archive member is not a regular file: {info.filename}"
                    )
                if info.flag_bits & 0x1:
                    raise BackupArchiveError("encrypted archive members are unsupported")
            if archive.getinfo(_MANIFEST).file_size > _MAX_MANIFEST_BYTES:
                raise BackupArchiveError("backup manifest is too large")
            manifest_bytes = archive.read(_MANIFEST)
            manifest = json.loads(manifest_bytes)
            if (not isinstance(manifest, dict)
                or manifest.get("format_version") != BACKUP_FORMAT_VERSION):
                raise BackupArchiveError("unsupported backup format version")
            files = manifest.get("files")
            if not isinstance(files, dict) or set(files) != set(names) - {_MANIFEST}:
                raise BackupArchiveError("backup manifest does not match archive members")
            if _DUMP not in files:
                raise BackupArchiveError("archive has no PostgreSQL dump")
            canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=False).encode("utf-8") + b"\n"
            if manifest_bytes != canonical:
                raise BackupArchiveError("backup manifest is not canonical JSON")
            for name, expected in files.items():
                if not isinstance(expected, dict) or set(expected) != {"size", "sha256"}:
                    raise BackupArchiveError(f"invalid backup entry metadata: {name}")
                size = expected["size"]
                checksum = expected["sha256"]
                if (isinstance(size, bool) or not isinstance(size, int) or size < 0
                    or not isinstance(checksum, str) or len(checksum) != 64
                    or any(char not in "0123456789abcdef" for char in checksum)):
                    raise BackupArchiveError(f"invalid backup checksum or size: {name}")
                if archive.getinfo(name).file_size != size:
                    raise BackupArchiveError(f"backup member size differs: {name}")
                with archive.open(name) as stream:
                    actual = _copy_hash(stream)
                if actual != (size, checksum):
                    raise BackupArchiveError(f"backup member checksum differs: {name}")
            return files
    except (OSError, zipfile.BadZipFile, UnicodeError, ValueError, KeyError) as exc:
        if isinstance(exc, BackupArchiveError):
            raise
        raise BackupArchiveError(f"cannot verify backup archive: {exc}") from exc


def require_empty_data_root(root: Path) -> None:
    """Reject an existing research installation before opening a restore archive."""
    root = Path(root)
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise BackupArchiveError("restore data root must be a real directory")
    if root.exists() and any(root.iterdir()):
        raise BackupArchiveError("restore requires an empty data root")


def extract_verified_archive(
    archive_path: Path, data_root: Path, files: Mapping[str, Mapping[str, Any]],
) -> Path:
    """Extract data after verification; return a temporary dump path for pg_restore.

    The caller must already have checked that the target root and database
    were empty. PostgreSQL uses a named volume and must not create root files.
    """
    data_root = Path(data_root)
    if data_root.is_symlink() or not data_root.is_dir():
        raise BackupArchiveError("restore data root is unavailable or unsafe")
    if any(data_root.iterdir()):
        raise BackupArchiveError("restore data root already contains research data")
    dump_path = data_root / ".restore-postgres.dump"
    with zipfile.ZipFile(archive_path) as archive:
        for name in sorted(files):
            target = (dump_path if name == _DUMP else
                      data_root.joinpath(*PurePosixPath(name).parts[1:]))
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink():
                raise BackupArchiveError(f"restore target is a symlink: {target}")
            with archive.open(name) as source, target.open("xb") as output:
                actual = _copy_hash(source, output)
            if actual != (files[name]["size"], files[name]["sha256"]):
                raise BackupArchiveError(f"restore copy checksum differs: {name}")
    return dump_path
