"""Crash-safe immutable staging batches for an unpublished historical import."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from market_analysis.domain.market_data import Bar, DomainValidationError
from market_analysis.persistence.import_jobs import ImportBatch, ImportJobError


def _record(bar: Bar) -> bytes:
    return (
        json.dumps(
            dict(bar.to_canonical_dict()), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        + "\n"
    ).encode()


def _file_digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_job_id(job_id: str) -> str:
    try:
        return str(UUID(job_id))
    except (TypeError, ValueError) as exc:
        raise ImportJobError("import batch job ID must be a UUID") from exc


@dataclass(frozen=True, slots=True)
class ImportBatchStore:
    root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root).expanduser().resolve())

    def _directory(self, job_id: str, *, create: bool) -> Path:
        root = self.root / "imports"
        directory = root / _safe_job_id(job_id)
        if root.is_symlink() or directory.is_symlink():
            raise ImportJobError("import staging path must not be a symlink")
        if create:
            directory.mkdir(parents=True, exist_ok=True)
        return directory

    def write_batch(self, job_id: str, batch_index: int, bars: tuple[Bar, ...]) -> ImportBatch:
        if batch_index < 0 or not bars:
            raise ImportJobError("import batch must be nonempty with nonnegative index")
        if any(not isinstance(bar, Bar) for bar in bars):
            raise ImportJobError("import batch must contain canonical Bars")
        previous = None
        for bar in bars:
            if previous is not None and bar.timestamp <= previous:
                raise ImportJobError("import batch bars must be strictly ordered")
            previous = bar.timestamp
        directory = self._directory(job_id, create=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".batch-", dir=directory)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                for bar in bars:
                    output.write(_record(bar))
                output.flush()
                os.fsync(output.fileno())
            digest = _file_digest(temporary)
            name = f"batch-{batch_index:08d}-{digest}.jsonl"
            target = directory / name
            try:
                os.link(temporary, target)
            except FileExistsError:
                if target.is_symlink() or _file_digest(target) != digest:
                    raise ImportJobError("existing import batch content differs") from None
            dir_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
            batch = ImportBatch(
                job_id,
                batch_index,
                f"imports/{_safe_job_id(job_id)}/{name}",
                digest,
                len(bars),
                bars[0].timestamp,
                bars[-1].timestamp,
            )
            return batch
        finally:
            temporary.unlink(missing_ok=True)

    def read_batch(self, batch: ImportBatch) -> tuple[Bar, ...]:
        directory = self._directory(batch.job_id, create=False)
        expected = (
            f"imports/{_safe_job_id(batch.job_id)}/"
            f"batch-{batch.batch_index:08d}-{batch.sha256}.jsonl"
        )
        if batch.relative_path != expected:
            raise ImportJobError("import batch path does not match checkpoint")
        path = directory / Path(expected).name
        if path.is_symlink() or not path.is_file():
            raise ImportJobError("checkpointed import batch is unavailable")
        if _file_digest(path) != batch.sha256:
            raise ImportJobError("checkpointed import batch checksum differs")
        bars = []
        try:
            with path.open("rb") as stream:
                for line in stream:
                    payload = json.loads(line)
                    if not isinstance(payload, dict):
                        raise ImportJobError("malformed checkpointed import batch")
                    bar = Bar.from_canonical_dict(payload)
                    if _record(bar) != line:
                        raise ImportJobError("noncanonical checkpointed import batch")
                    bars.append(bar)
        except (OSError, ValueError, DomainValidationError) as exc:
            raise ImportJobError("cannot read checkpointed import batch") from exc
        if (
            len(bars) != batch.bar_count
            or not bars
            or bars[0].timestamp != batch.first_timestamp
            or bars[-1].timestamp != batch.last_timestamp
            or any(
                right.timestamp <= left.timestamp
                for left, right in zip(bars, bars[1:], strict=False)
            )
        ):
            raise ImportJobError("checkpointed import batch metadata differs")
        return tuple(bars)

    def iter_bars(self, batches: Iterable[ImportBatch]) -> Iterator[Bar]:
        previous = None
        for batch in batches:
            for bar in self.read_batch(batch):
                if previous is not None and bar.timestamp <= previous:
                    raise ImportJobError("checkpointed import batches are out of order")
                previous = bar.timestamp
                yield bar
