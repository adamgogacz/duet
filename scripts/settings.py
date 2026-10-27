# Copyright 2026 Adam Gogacz
# SPDX-License-Identifier: Apache-2.0

"""Typed goal settings and strict JSON boundaries for the Duet utilities."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import MISSING, dataclass
from enum import IntEnum
from pathlib import Path
from typing import Any

ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+", re.ASCII)
INITIAL_PLAN_SCOPE = ("work-division",)
DEFAULT_CONTEXT_REVISION = "initial"
PROJECT_LABEL_LIMIT = 32
AUTO_PARTICIPANTS = ("peer-a", "peer-b")
COMPLETION_SCOPE = ("goal-completion",)
CONFLICT_RESOLUTION_SCOPE = ("conflict-resolution",)
PROJECT_REGISTRY = ".duet.jsonl"
PROJECT_LOCK = ".duet.lock"
SESSION_LOCK = "session.lock"


class ExitCode(IntEnum):
    """Stable process outcomes for a caller supervising a Duet command."""

    OK = 0
    ERROR = 1
    TIMEOUT = 2
    ATTENTION = 3
    REJECTED = 5


class DuetError(ValueError):
    """Invalid protocol data or an unsupported board operation."""


def temporary_directory() -> Path:
    """Prefer an existing TMPDIR; otherwise use Python's platform temp-directory lookup."""
    configured = os.environ.get("TMPDIR")
    if configured and Path(configured).is_dir():
        return Path(configured).resolve()
    fallback = Path(tempfile.gettempdir()).resolve()
    require(
        fallback.is_dir(),
        "Python's temporary-directory lookup returned an unavailable directory",
    )
    return fallback


def require(condition: bool, message: str) -> None:
    """Raise a domain error without exposing message contents."""
    if not condition:
        raise DuetError(message)


def identifier(value: Any, field: str) -> str:
    """Validate an opaque identifier before it reaches a filesystem path."""
    require(
        isinstance(value, str) and ID_PATTERN.fullmatch(value) is not None,
        f"{field} must be an ASCII identifier",
    )
    return value


def nonempty(value: Any, field: str) -> str:
    """Require meaningful text at a JSON boundary."""
    require(
        isinstance(value, str) and bool(value.strip()), f"{field} must be nonempty text"
    )
    return value


def strict_json(data: bytes) -> dict[str, Any]:
    """Decode a JSON object, rejecting duplicate keys and non-JSON numbers."""

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            require(key not in result, "duplicate JSON object key")
            result[key] = value
        return result

    def constant(_: str) -> None:
        raise DuetError("non-finite JSON number")

    def finite_float(value: str) -> float:
        number = float(value)
        require(math.isfinite(number), "non-finite JSON number")
        return number

    try:
        result = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=constant,
            parse_float=finite_float,
        )
    except DuetError:
        raise
    except (UnicodeError, ValueError, RecursionError) as error:
        raise DuetError("invalid UTF-8 JSON") from error
    require(isinstance(result, dict), "JSON root must be an object")
    return result


def encoded(value: Mapping[str, Any]) -> bytes:
    """Serialize helper-owned records deterministically."""
    try:
        return (
            json.dumps(
                value, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as error:
        raise DuetError("record cannot be encoded as JSON") from error


@dataclass(frozen=True)
class Settings:
    """Central typed defaults, overridden by task-specific configuration."""

    goal: str
    context_revision: str
    participants: tuple[str, str]
    board: Path
    poll_interval_seconds: float = 1
    wait_timeout_seconds: float | None = 30
    escalation: str = "Resolve routine issues with the peer; involve the user only for user-only authority or missing external prerequisites."
    max_message_bytes: int = 65536
    max_messages: int = 1000
    max_checkpoint_bytes: int = 131072
    max_artifact_bytes: int = 16777216
    invitation_ttl_seconds: float = 300
    max_registry_bytes: int = 16777216
    lock_poll_interval_seconds: float = 0.01

    @classmethod
    def default(cls, name: str) -> Any:
        """Expose central operating defaults before a task configuration exists."""
        field = cls.__dataclass_fields__.get(name)
        require(
            field is not None and field.default is not MISSING, "setting has no default"
        )
        return field.default

    @property
    def initial_proposer(self) -> str:
        """Choose the initial proposer using case-sensitive ASCII ID ordering."""
        return min(self.participants)

    @property
    def completion_proposer(self) -> str:
        """Use the same deterministic participant to initiate the closing decision."""
        return self.initial_proposer

    @property
    def initial_plan_id(self) -> str:
        """Keep startup decisions distinct across task-context revisions."""
        return f"initial-plan-{self.context_revision}"

    @classmethod
    def load(cls, path: Path) -> Settings:
        """Load settings without creating a board or changing an existing configuration."""
        from board import read_record

        return cls.from_data(
            strict_json(read_record(path, cls.default("max_checkpoint_bytes")))
        )

    @classmethod
    def from_data(cls, data: Mapping[str, Any]) -> Settings:
        """Apply declared defaults and reject unknown or invalid configuration."""
        require(
            {"goal", "context_revision", "participants", "board"}
            <= set(data)
            <= set(cls.__dataclass_fields__),
            "settings fields do not match the contract",
        )
        identifier(data["goal"], "goal")
        identifier(data["context_revision"], "context_revision")
        participants = data["participants"]
        require(
            isinstance(participants, list) and len(participants) == 2,
            "participants must contain exactly two identities",
        )
        for item in participants:
            identifier(item, "participant")
        require(
            participants[0] != participants[1], "participant identities must differ"
        )
        board = Path(nonempty(data["board"], "board"))
        require(board.is_absolute(), "board must be an absolute path")
        settings = cls(
            **{**data, "board": board.resolve(), "participants": tuple(participants)}
        )
        nonempty(settings.escalation, "escalation")
        for name in (
            "poll_interval_seconds",
            "wait_timeout_seconds",
            "invitation_ttl_seconds",
            "lock_poll_interval_seconds",
        ):
            value = getattr(settings, name)
            if name == "wait_timeout_seconds" and value is None:
                continue
            try:
                finite = type(value) in (float, int) and math.isfinite(value)
            except OverflowError:
                finite = False
            require(finite and value > 0, f"{name} must be finite and positive")
        for name in (
            "max_message_bytes",
            "max_messages",
            "max_checkpoint_bytes",
            "max_artifact_bytes",
            "max_registry_bytes",
        ):
            value = getattr(settings, name)
            require(
                type(value) is int and value > 0,
                f"{name} must be a positive integer",
            )
        return settings

    def check_actor(self, participant: str, session: str) -> None:
        """Validate the explicitly selected publishing identity."""
        require(participant in self.participants, "participant is not in this goal")
        identifier(session, "session")
