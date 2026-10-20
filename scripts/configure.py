# Copyright 2026 Adam Gogacz
# SPDX-License-Identifier: Apache-2.0

"""Create reusable task settings in shared temporary storage without overwrites."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

from board import atomic_write, directory, read_record
from settings import (
    DEFAULT_CONTEXT_REVISION,
    PROJECT_LABEL_LIMIT,
    Settings,
    encoded,
    identifier,
    require,
    strict_json,
    temporary_directory,
)


def project_board(workspace: Path) -> Path:
    """Derive a project storage root; a paired session always has its own child board."""
    root = workspace.resolve(strict=True)
    require(root.is_dir(), "workspace must be an existing directory")
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()
    label = (
        re.sub(r"[^A-Za-z0-9_-]+", "-", root.name).strip("-_")[:PROJECT_LABEL_LIMIT]
        or "project"
    )
    return directory(temporary_directory(), ("duet", f"{label}-{digest}"))


def configure(
    workspace: Path,
    goal: str,
    participants: tuple[str, str],
    *,
    context_revision: str = DEFAULT_CONTEXT_REVISION,
    options: Mapping[str, Any] | None = None,
    dry_run: bool = False,
    board: Path | None = None,
) -> dict[str, Any]:
    """Create immutable settings for a supplied session board or an explicit tooling goal."""
    root = workspace.resolve(strict=True)
    require(root.is_dir(), "workspace must be an existing directory")
    identifier(goal, "goal")
    identifier(context_revision, "context_revision")
    identity_fields = {"goal", "context_revision", "participants", "board"}
    options = dict(options) if options is not None else {}
    require(
        not identity_fields.intersection(options),
        "setup options cannot replace task identity or board",
    )
    automatic = board is not None
    board = board if automatic else project_board(root)
    parent = board.parents[2] if automatic else board.parent.parent
    settings = Settings.from_data(
        {
            **options,
            "goal": goal,
            "context_revision": context_revision,
            "participants": sorted(participants),
            "board": str(board),
        }
    )
    data = asdict(settings)
    data["board"] = str(settings.board)
    settings_parts = ("settings", goal, context_revision)
    path = directory(board, settings_parts) / "settings.json"
    created = False
    if not dry_run:
        directory(board, settings_parts, create=True)
        if path.exists() or path.is_symlink():
            existing = Settings.from_data(
                strict_json(read_record(path, Settings.default("max_checkpoint_bytes")))
            )
            require(
                existing == settings,
                "settings differ from the existing task configuration",
            )
        else:
            created = atomic_write(path, encoded(data), replace=False)
    return {
        "settings_path": str(path),
        "board": str(board),
        "workspace": str(root),
        "temp_directory": str(parent),
        "created": created,
        "dry_run": dry_run,
        "settings": data,
        "initial_proposer": settings.initial_proposer,
        "completion_proposer": settings.completion_proposer,
    }
