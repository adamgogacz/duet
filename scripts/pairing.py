# Copyright 2026 Adam Gogacz
# SPDX-License-Identifier: Apache-2.0

"""Append-only invitation history for pairing independent agents within one workspace."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from board import atomic_write, read_record
from settings import (
    PROJECT_REGISTRY,
    Settings,
    identifier,
    nonempty,
    require,
    strict_json,
)


@dataclass(frozen=True)
class Invitation:
    """The latest advertised deadline or permanent pairing for a collaboration."""

    session: str
    task: str
    board: Path
    expires_at: float
    peer_task: str | None = None

    @property
    def paired(self) -> bool:
        """Pairing is permanent once the joining peer has recorded its request."""
        return self.peer_task is not None

    def eligible(self, now: float) -> bool:
        """An invitation is claimable only while unmatched, unexpired, and available."""
        return not self.paired and self.expires_at > now and self.board.is_dir()

    def as_json(self, now: float) -> dict[str, Any]:
        """Expose task text for semantic matching without disclosing actor credentials."""
        return {
            "session": self.session,
            "task": self.task,
            "board": str(self.board),
            "state": "paired"
            if self.paired
            else "waiting"
            if self.eligible(now)
            else "expired",
            "expires_at": self.expires_at,
            "peer_task": self.peer_task,
        }


@dataclass(frozen=True)
class Lobby:
    """A bounded, validated snapshot; records never change after publication."""

    raw: bytes
    invitations: tuple[Invitation, ...]

    def candidates(self, now: float) -> tuple[Invitation, ...]:
        """Preserve advertisement order when several unrelated tasks are waiting."""
        return tuple(item for item in self.invitations if item.eligible(now))

    def observation(self, now: float) -> str:
        """Detect newly eligible invitations without treating lease renewal as a new task."""
        return hashlib.sha256(
            json.dumps([item.session for item in self.candidates(now)]).encode()
        ).hexdigest()


def read_lobby(workspace: Path, *, limit: int | None = None) -> Lobby:
    """Replay the registry without reading any collaboration's messages or artifacts."""
    path = workspace / PROJECT_REGISTRY
    if not (path.exists() or path.is_symlink()):
        return Lobby(b"", ())
    raw = read_record(
        path, Settings.default("max_registry_bytes") if limit is None else limit
    )
    require(not raw or raw.endswith(b"\n"), "incomplete pairing registry record")
    invitations: dict[str, Invitation] = {}
    for line in raw.splitlines():
        record = strict_json(line)
        session = identifier(record.get("session"), "collaboration session")
        state = record.get("state")
        if state == "waiting":
            require(
                set(record) == {"session", "state", "task", "board", "expires_at"},
                "invalid invitation fields",
            )
            require(session not in invitations, "duplicate session advertisement")
            task = nonempty(record["task"], "invitation task")
            require(
                task == " ".join(task.split()), "invitation task must be normalized"
            )
            board = Path(nonempty(record["board"], "invitation board"))
            require(
                board.is_absolute() and board.name == session, "invalid session board"
            )
            invitations[session] = Invitation(session, task, board, deadline(record))
        else:
            require(session in invitations, "pairing event has no invitation")
            invitation = invitations[session]
            require(
                not invitation.paired, "paired sessions cannot accept another event"
            )
            if state == "paired":
                require(
                    set(record) == {"session", "state", "task"},
                    "invalid pairing fields",
                )
                invitations[session] = replace(
                    invitation, peer_task=nonempty(record["task"], "peer task")
                )
            else:
                require(
                    state == "renewed"
                    and set(record) == {"session", "state", "expires_at"},
                    "invalid pairing event",
                )
                expires_at = deadline(record)
                require(
                    expires_at > invitation.expires_at,
                    "invitation deadline must advance",
                )
                invitations[session] = replace(invitation, expires_at=expires_at)
    return Lobby(raw, tuple(invitations.values()))


def deadline(record: dict[str, Any]) -> float:
    """Reject malformed and non-finite wall-clock lease deadlines."""
    value = record.get("expires_at")
    require(
        type(value) in (int, float) and math.isfinite(value) and value > 0,
        "invalid invitation deadline",
    )
    return value


def append_event(
    workspace: Path, lobby: Lobby, record: dict[str, Any], *, limit: int | None = None
) -> None:
    """Append logically under the project lock; atomic replacement prevents torn JSONL."""
    line = (
        json.dumps(record, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode()
    raw = lobby.raw + line
    require(
        len(raw)
        <= (Settings.default("max_registry_bytes") if limit is None else limit),
        "pairing registry exceeds configured byte limit",
    )
    atomic_write(workspace / PROJECT_REGISTRY, raw, replace=True)
