# Copyright 2026 Adam Gogacz
# SPDX-License-Identifier: Apache-2.0

"""Unread delivery and bounded process-level waiting over immutable Duet messages."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from board import (
    Snapshot,
    checkpoint_path,
    read_record,
    save_checkpoint,
    scan,
    validate_checkpoint,
)
from messages import Kind, Message
from settings import (
    DuetError,
    ExitCode,
    Settings,
    encoded,
    identifier,
    require,
    strict_json,
)
from startup import opening


class Clock(Protocol):
    """Inject timing at the blocking boundary without initializing a service."""

    def monotonic(self) -> float:
        """Return elapsed-time clock seconds."""
        ...

    def utcnow(self) -> datetime:
        """Return the UTC wall clock for a persisted dependency deadline."""
        ...

    def sleep(self, seconds: float) -> None:
        """Wait inside the process rather than scheduling another agent turn."""
        ...


class SystemClock:
    """Standard-library clock used only while a wait command is active."""

    def monotonic(self) -> float:
        """Keep command duration independent of wall-clock adjustments."""
        return time.monotonic()

    def utcnow(self) -> datetime:
        """Compare a declared UTC dependency deadline."""
        return datetime.now(UTC)

    def sleep(self, seconds: float) -> None:
        """Sleep until the next configured board scan."""
        time.sleep(seconds)


@dataclass(frozen=True)
class Received:
    """A read-only board observation and this participant's recovery cache."""

    snapshot: Snapshot
    checkpoint: dict[str, Any] | None
    checkpoint_error: str | None
    unread: tuple[Message, ...]

    def as_json(self, settings: Settings, *, unread_only: bool) -> dict[str, Any]:
        """Keep ordinary inbox output small; retain full replay for recovery."""
        output = self.snapshot.as_json(self.unread if unread_only else None)
        output["startup"] = opening(
            settings, self.snapshot.history, self.snapshot.conflicts
        ).as_json()
        from enrollment import invitation_for

        invitation = invitation_for(settings)
        if invitation is not None:
            output["collaboration"] = invitation.as_json(
                SystemClock().utcnow().timestamp()
            )
        output["checkpoint_error"] = self.checkpoint_error
        if not unread_only:
            output["checkpoint"] = self.checkpoint
        return output


def receive(settings: Settings, participant: str) -> Received:
    """Discover unread peer messages without acknowledging or consuming them."""
    require(participant in settings.participants, "participant is not in this goal")
    snapshot = scan(settings)
    checkpoint = None
    checkpoint_error = None
    path = checkpoint_path(settings, participant)
    if path.exists() or path.is_symlink():
        try:
            candidate = strict_json(read_record(path, settings.max_checkpoint_bytes))
            validate_checkpoint(settings, participant, candidate, snapshot=snapshot)
            checkpoint = candidate
        except (DuetError, OSError) as error:
            checkpoint_error = str(error)
    processed = set(checkpoint["processed"]) if checkpoint is not None else set()
    unread = tuple(
        message
        for message in snapshot.messages
        if message.sender != participant and message.id not in processed
    )
    return Received(snapshot, checkpoint, checkpoint_error, unread)


def acknowledge(
    settings: Settings,
    participant: str,
    session: str,
    message_ids: list[str],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Merge exact processed IDs into the caller's checkpoint, preserving recovery notes."""
    settings.check_actor(participant, session)
    require(bool(message_ids), "ack requires explicit processed message IDs")
    for item in message_ids:
        identifier(item, "ack message ID")
    received = receive(settings, participant)
    require(
        received.checkpoint_error is None,
        "recover the invalid checkpoint before acknowledging messages",
    )
    history = received.snapshot.history
    require(
        set(message_ids) <= set(history),
        "ack IDs must identify available valid messages",
    )
    data = (
        dict(received.checkpoint)
        if received.checkpoint is not None
        else {
            "goal": settings.goal,
            "participant": participant,
            "processed": [],
            "outgoing": [],
            "deferred": {
                key: "Awaiting causal prerequisites"
                for key in received.snapshot.pending
            },
            "invalid": dict(received.snapshot.invalid),
            "active": [],
            "external_actions": [],
        }
    )
    data["session"] = session
    data["processed"] = sorted(set(data["processed"]) | set(message_ids))
    data["outgoing"] = [
        message.id
        for message in received.snapshot.messages
        if message.sender == participant
    ]
    data["deferred"] = {
        key: reason
        for key, reason in data["deferred"].items()
        if key not in message_ids
    }
    save_checkpoint(settings, participant, session, encoded(data), dry_run=dry_run)
    return {
        "acknowledged": sorted(set(message_ids)),
        "processed_count": len(data["processed"]),
        "saved": not dry_run,
        "dry_run": dry_run,
    }


@dataclass(frozen=True)
class WaitResult:
    """A wakeup is an observed event, never implicit acceptance or a state change."""

    code: ExitCode
    status: str
    received: Received
    matched: tuple[Message, ...] = ()

    def as_json(self, settings: Settings) -> dict[str, Any]:
        """Include only inbox deltas plus exact matched events needed by the caller."""
        output = self.received.as_json(settings, unread_only=True)
        output["status"] = self.status
        output["matched"] = [message.id for message in self.matched]
        visible = {message["id"] for message in output["messages"]}
        output["messages"].extend(
            strict_json(message.raw)
            for message in self.matched
            if message.id not in visible
        )
        return output


def select_matches(
    history: dict[str, Message],
    participant: str,
    reply_to: str | None,
    wait: Message | None,
) -> tuple[Message, ...]:
    """Correlate replies or durable predicates, including already-processed events."""
    if reply_to is not None:
        return tuple(
            message
            for message in history.values()
            if message.sender != participant
            and message.reply_to == reply_to
            and message.kind not in {Kind.RECEIPT, Kind.INFORM}
        )
    if wait is not None:
        condition = wait.body["condition"]
        return tuple(
            message
            for message in history.values()
            if message.kind == condition["kind"]
            and message.sender == condition["from"]
            and message.subject_type == condition["subject"]["type"]
            and message.subject_id == condition["subject"]["id"]
            and (condition["target"] is None or message.reply_to == condition["target"])
        )
    return ()


def wait_for_messages(
    settings: Settings,
    participant: str,
    *,
    reply_to: str | None = None,
    wait_id: str | None = None,
    timeout: float | None = None,
    clock: Clock | None = None,
) -> WaitResult:
    """Block once, wake on correlation or peer attention, and never advance a checkpoint."""
    require(not (reply_to and wait_id), "choose reply_to or wait_id, not both")
    duration = settings.wait_timeout_seconds if timeout is None else timeout
    require(
        type(duration) in (int, float) and math.isfinite(duration) and duration > 0,
        "wait needs a finite positive timeout; supply one if goal policy is indefinite",
    )
    clock = clock if clock is not None else SystemClock()
    stop = clock.monotonic() + duration
    wait = None
    attention = frozenset(
        {Kind.ASK, Kind.REQUEST, Kind.PROPOSE, Kind.SUBMIT, Kind.ALERT}
    )
    while True:
        from enrollment import renew_invitation

        pairing = renew_invitation(settings, clock=clock)
        received = receive(settings, participant)
        if pairing == "expired":
            return WaitResult(ExitCode.ATTENTION, "invitation-expired", received)
        history = received.snapshot.history
        if received.checkpoint_error is not None or received.snapshot.invalid:
            return WaitResult(ExitCode.ERROR, "recovery-required", received)
        if reply_to is not None:
            identifier(reply_to, "reply_to")
            require(
                reply_to in history and history[reply_to].sender == participant,
                "reply target must be an available message authored by this participant",
            )
            require(
                history[reply_to].kind
                in {Kind.ASK, Kind.REQUEST, Kind.PROPOSE, Kind.SUBMIT},
                "reply target must be an ask, request, propose or submit; use a durable wait for other conditions",
            )
        if reply_to is not None:
            from artifacts import verify
            from workflow import is_completion, stale_reasons

            target = history[reply_to]
            if is_completion(target):
                changed = bool(stale_reasons(settings, received.snapshot, target))
                try:
                    for reference in target.body.get("artifacts", []):
                        verify(settings, reference, current_origin=True)
                except (DuetError, OSError):
                    changed = True
                if changed:
                    return WaitResult(
                        ExitCode.ATTENTION, "completion-context-changed", received
                    )
        if wait_id is not None:
            identifier(wait_id, "wait_id")
            require(
                wait_id in history
                and history[wait_id].sender == participant
                and history[wait_id].kind == Kind.AWAIT,
                "wait_id must name your published await",
            )
            wait = history[wait_id]
        matched = select_matches(history, participant, reply_to, wait)
        if matched:
            if any(
                message.reply_to in received.snapshot.conflicts for message in matched
            ):
                return WaitResult(
                    ExitCode.ERROR, "conflicting-replies", received, matched
                )
            rejected = any(
                message.kind in {Kind.DECLINE, Kind.OBJECT, Kind.REJECT}
                for message in matched
            )
            return WaitResult(
                ExitCode.REJECTED if rejected else ExitCode.OK,
                "rejected" if rejected else "matched",
                received,
                matched,
            )
        if reply_to is None and wait is None and received.unread:
            return WaitResult(ExitCode.OK, "messages", received)
        if any(message.kind in attention for message in received.unread):
            return WaitResult(ExitCode.ATTENTION, "peer-attention", received)
        remaining = stop - clock.monotonic()
        dependency_expired = False
        if wait is not None and wait.body["deadline"] is not None:
            deadline = datetime.strptime(
                wait.body["deadline"], "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=UTC)
            dependency_remaining = (deadline - clock.utcnow()).total_seconds()
            dependency_expired = dependency_remaining <= 0
            remaining = min(remaining, dependency_remaining)
        if remaining <= 0:
            return WaitResult(
                ExitCode.TIMEOUT,
                "deadline-expired" if dependency_expired else "timeout",
                received,
            )
        interval = settings.poll_interval_seconds
        if pairing == "waiting":
            interval = min(interval, settings.invitation_ttl_seconds / 3)
        clock.sleep(min(interval, remaining))
