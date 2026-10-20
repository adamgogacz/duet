# Copyright 2026 Adam Gogacz
# SPDX-License-Identifier: Apache-2.0

"""Capture and verify immutable file revisions without agent-transcribed digests."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from board import atomic_write, directory, read_record
from settings import Settings, encoded, identifier, nonempty, require, strict_json


class ArtifactReference(TypedDict):
    """The existing Duet artifact fields consumed by message validation."""

    id: str
    revision: str
    description: str


@dataclass(frozen=True)
class CapturedArtifact:
    """One content revision with its original path and immutable review copy."""

    id: str
    revision: str
    origin: Path
    snapshot: Path
    size: int

    def reference(self) -> ArtifactReference:
        """Point readers to the captured bytes rather than a mutable working file."""
        return {
            "id": self.id,
            "revision": self.revision,
            "description": f"{self.origin.name}; captured copy: {self.snapshot}",
        }

    def record(self) -> dict:
        """Serialize only stable capture metadata at the storage boundary."""
        return {
            "id": self.id,
            "revision": self.revision,
            "origin": str(self.origin),
            "snapshot": str(self.snapshot),
            "size": self.size,
        }


def digest(raw: bytes) -> str:
    """Use the exact captured bytes for revision identity."""
    return hashlib.sha256(raw).hexdigest()


def capture(
    settings: Settings, path: Path, *, dry_run: bool = False
) -> CapturedArtifact:
    """Read a bounded regular file once and publish an immutable review copy."""
    origin = path.resolve(strict=True)
    raw = read_record(origin, settings.max_artifact_bytes)
    key = f"artifact-{digest(str(origin).encode('utf-8'))}"
    revision = digest(raw)
    root = directory(
        settings.board, ("artifacts", settings.goal, key, revision), create=not dry_run
    )
    artifact = CapturedArtifact(
        key, f"sha256:{revision}", origin, root / "content", len(raw)
    )
    if not dry_run:
        atomic_write(artifact.snapshot, raw, replace=False)
        atomic_write(root / "record.json", encoded(artifact.record()), replace=False)
    return artifact


def verify(
    settings: Settings, reference: Mapping[str, str], *, current_origin: bool = False
) -> CapturedArtifact:
    """Verify captured bytes, and optionally reject an edited working-file origin."""
    key = identifier(reference["id"], "artifact.id")
    revision = nonempty(reference["revision"], "artifact.revision")
    require(
        revision.startswith("sha256:"), "artifact is not a utility-captured revision"
    )
    checksum = revision.removeprefix("sha256:")
    require(
        len(checksum) == hashlib.sha256().digest_size * 2
        and all(c in "0123456789abcdef" for c in checksum),
        "invalid artifact digest",
    )
    root = directory(settings.board, ("artifacts", settings.goal, key, checksum))
    data = strict_json(read_record(root / "record.json", settings.max_checkpoint_bytes))
    require(
        set(data) == set(CapturedArtifact.__dataclass_fields__),
        "invalid artifact capture record",
    )
    origin = Path(nonempty(data["origin"], "artifact origin"))
    require(origin.is_absolute(), "artifact origin must be absolute")
    require(
        data["id"] == key
        and data["revision"] == revision
        and data["snapshot"] == str(root / "content"),
        "artifact capture identity mismatch",
    )
    raw = read_record(root / "content", settings.max_artifact_bytes)
    require(
        type(data["size"]) is int
        and data["size"] == len(raw)
        and digest(raw) == checksum,
        "captured artifact bytes changed",
    )
    if current_origin:
        require(
            digest(read_record(origin, settings.max_artifact_bytes)) == checksum,
            "artifact changed since proposal; publish and review a revised offer",
        )
    return CapturedArtifact(key, revision, origin, root / "content", len(raw))
