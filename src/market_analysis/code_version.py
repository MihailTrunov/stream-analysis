"""Observational, injectable application revision capture for analytical runs."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class CodeCaptureStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class CodeVersion:
    """Build and source identity; unknown dirty status is never treated as clean."""

    build_id: str
    revision: str | None
    dirty: bool | None
    status: CodeCaptureStatus
    source: str

    def __post_init__(self) -> None:
        if not self.build_id.strip() or not self.source.strip():
            raise ValueError("CodeVersion build_id and source must be non-empty")
        if self.status is CodeCaptureStatus.AVAILABLE:
            if self.revision is None or not re.fullmatch(r"[0-9a-fA-F]{40,64}", self.revision):
                raise ValueError("available CodeVersion requires a full Git revision")
        elif self.status is CodeCaptureStatus.UNAVAILABLE:
            if self.revision is not None or self.dirty is not None:
                raise ValueError("unavailable CodeVersion cannot claim revision or dirty status")
        else:
            raise ValueError("invalid CodeVersion status")

    @property
    def is_clean_committed(self) -> bool:
        return self.status is CodeCaptureStatus.AVAILABLE and self.dirty is False


def _git(args: tuple[str, ...], repository: Path) -> str | None:
    try:
        result = subprocess.run(
            ("git", "-C", str(repository), *args),
            capture_output=True, text=True, check=False, timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def capture_code_version(
    build_id: str,
    *,
    environ: Mapping[str, str] | None = None,
    repository: Path | None = None,
) -> CodeVersion:
    """Prefer build-pinned metadata, otherwise inspect the local checkout.

    A Git commit without a known cleanliness flag is not eligible for durable
    evaluation preflight. Git absence or failure is recorded, never guessed.
    """
    if environ is None:
        environ = os.environ
    revision = environ.get("STREAM_ANALYSIS_GIT_COMMIT", "").strip()
    dirty_text = environ.get("STREAM_ANALYSIS_GIT_DIRTY", "").strip().lower()
    if revision:
        if not re.fullmatch(r"[0-9a-fA-F]{40,64}", revision) or dirty_text not in (
            "", "true", "false"
        ):
            return CodeVersion(
                build_id, None, None, CodeCaptureStatus.UNAVAILABLE, "invalid-build-environment"
            )
        return CodeVersion(
            build_id=build_id,
            revision=revision,
            dirty=None if not dirty_text else dirty_text == "true",
            status=CodeCaptureStatus.AVAILABLE,
            source="build-environment",
        )
    if repository is None:
        repository = Path(__file__).resolve().parents[2]
    git_revision = _git(("rev-parse", "--verify", "HEAD"), repository)
    if git_revision is None or not re.fullmatch(r"[0-9a-fA-F]{40,64}", git_revision):
        return CodeVersion(build_id, None, None, CodeCaptureStatus.UNAVAILABLE, "git-unavailable")
    status = _git(("status", "--porcelain", "--untracked-files=normal"), repository)
    return CodeVersion(
        build_id=build_id,
        revision=git_revision,
        dirty=None if status is None else bool(status),
        status=CodeCaptureStatus.AVAILABLE,
        source="git-checkout",
    )
