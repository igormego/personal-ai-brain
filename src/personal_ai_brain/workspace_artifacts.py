"""Task-local artifact boundary for a future generic sandboxed workspace.

SOURCE ONLY. This module does not run untrusted code, accept external requests,
mount host directories, read arbitrary paths, or publish outputs. The trusted
operator must supply input bytes after independent resource authorization; the
isolated executor must terminate before outputs are inspected.

Do not connect this directly to a live worker without a separately reviewed
sandbox, lifecycle and resource broker acceptance.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat

MAX_FILES = 32
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_DEPTH = 6
_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z", re.ASCII)
_RESOURCE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}(?:/[a-z0-9][a-z0-9-]{0,63}){0,3}\Z", re.ASCII)


class WorkspaceArtifactDenied(ValueError):
    """Fixed error codes only; do not echo untrusted file names or content."""


@dataclass(frozen=True, slots=True)
class InputGrant:
    """Trusted resource broker materialization, not a remote task argument."""

    resource_id: str
    path: str
    content: bytes


@dataclass(frozen=True, slots=True)
class Artifact:
    path: str
    size: int
    sha256: str
    resource_id: str | None = None


def _safe_path(value: object) -> tuple[str, ...]:
    if type(value) is not str or not 1 <= len(value) <= 256:
        raise WorkspaceArtifactDenied("invalid_artifact_path")
    parts = value.split("/")
    if (not 1 <= len(parts) <= MAX_DEPTH
            or any(not _SEGMENT.fullmatch(part) or part in {".", ".."}
                   or part.startswith(".") for part in parts)):
        raise WorkspaceArtifactDenied("invalid_artifact_path")
    return tuple(parts)


def _safe_resource(value: object) -> bool:
    return type(value) is str and bool(_RESOURCE.fullmatch(value))


def _check_total(count: int, size: int) -> None:
    if count > MAX_FILES or size > MAX_TOTAL_BYTES:
        raise WorkspaceArtifactDenied("artifact_limit")


def stage_inputs(root: Path, grants: tuple[InputGrant, ...]) -> tuple[Artifact, ...]:
    """Stage only authorized in-memory bytes under a new private input directory.

    The source connector/Resource Broker decides what bytes to grant. No source
    filesystem path, URL, token or shell command is accepted in this contract.
    A failed stage is not a successful task; its private directory must be
    retained/reconciled by the owning durable worker, not silently reused.
    """
    if (not isinstance(root, Path) or type(grants) is not tuple
            or not 1 <= len(grants) <= MAX_FILES):
        raise WorkspaceArtifactDenied("invalid_input_grants")
    seen: set[str] = set()
    prepared: list[tuple[InputGrant, tuple[str, ...]]] = []
    total = 0
    for grant in grants:
        if (not isinstance(grant, InputGrant)
                or not _safe_resource(grant.resource_id)
                or type(grant.content) is not bytes):
            raise WorkspaceArtifactDenied("invalid_input_grants")
        parts = _safe_path(grant.path)
        if grant.path in seen or len(grant.content) > MAX_FILE_BYTES:
            raise WorkspaceArtifactDenied("invalid_input_grants")
        seen.add(grant.path)
        total += len(grant.content)
        _check_total(len(prepared) + 1, total)
        prepared.append((grant, parts))

    # A new root is mandatory: no stale artifacts, collisions or caller-controlled
    # preexisting symlinks. Parent location is owned by the trusted operator.
    try:
        root.mkdir(mode=0o700, parents=False, exist_ok=False)
        manifest = []
        for grant, parts in prepared:
            parent = root
            for part in parts[:-1]:
                parent = parent / part
                parent.mkdir(mode=0o700, exist_ok=True)
                if parent.is_symlink() or not parent.is_dir():
                    raise WorkspaceArtifactDenied("invalid_staging_directory")
            target = parent / parts[-1]
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(grant.content)
                stream.flush()
                os.fsync(stream.fileno())
            manifest.append(Artifact(
                grant.path, len(grant.content),
                hashlib.sha256(grant.content).hexdigest(), grant.resource_id,
            ))
    except WorkspaceArtifactDenied:
        raise
    except (OSError, ValueError):
        raise WorkspaceArtifactDenied("staging_failed") from None
    return tuple(sorted(manifest, key=lambda value: value.path))


def inspect_outputs(root: Path) -> tuple[Artifact, ...]:
    """Validate a stopped sandbox's output tree without trusting its file types.

    This is only a bounded artifact inventory, NOT a claim the model's goal was
    achieved. Independent tests/semantic validators and durable terminal truth
    must still decide task success. No output contents are published by this API.
    """
    if not isinstance(root, Path) or root.is_symlink() or not root.is_dir():
        raise WorkspaceArtifactDenied("invalid_output_root")
    manifest: list[Artifact] = []
    total = 0
    stack = [(root, ())]
    try:
        while stack:
            directory, prefix = stack.pop()
            with os.scandir(directory) as iterator:
                entries = list(iterator)
            for entry in entries:
                parts = (*prefix, entry.name)
                rel = "/".join(parts)
                _safe_path(rel)
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    if len(parts) >= MAX_DEPTH:
                        raise WorkspaceArtifactDenied("invalid_artifact_path")
                    stack.append((Path(entry.path), parts))
                    continue
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_size > MAX_FILE_BYTES):
                    raise WorkspaceArtifactDenied("invalid_artifact_type")
                total += info.st_size
                _check_total(len(manifest) + 1, total)
                fd = os.open(entry.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, "rb") as stream:
                    opened = os.fstat(stream.fileno())
                    if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                            or (opened.st_dev, opened.st_ino, opened.st_size)
                            != (info.st_dev, info.st_ino, info.st_size)):
                        raise WorkspaceArtifactDenied("invalid_artifact_type")
                    data = stream.read(MAX_FILE_BYTES + 1)
                    if len(data) != info.st_size:
                        raise WorkspaceArtifactDenied("invalid_artifact_type")
                manifest.append(Artifact(rel, len(data), hashlib.sha256(data).hexdigest()))
    except WorkspaceArtifactDenied:
        raise
    except (OSError, ValueError):
        raise WorkspaceArtifactDenied("output_inspection_failed") from None
    return tuple(sorted(manifest, key=lambda value: value.path))
