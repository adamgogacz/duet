# Copyright 2026 Adam Gogacz
# SPDX-License-Identifier: Apache-2.0

"""Atomic message delivery and bounded causal replay on a local POSIX board."""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Mapping
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from locking import file_lock
from messages import Kind, Message, subject
from settings import (
    COMPLETION_SCOPE,
    SESSION_LOCK,
    INITIAL_PLAN_SCOPE,
    DuetError,
    Settings,
    encoded,
    identifier,
    nonempty,
    require,
    strict_json,
)
from startup import ancestry, opening


def directory(root: Path, parts: tuple[str, ...], *, create: bool = False) -> Path:
    """Walk protocol-owned directories without following descendant symlinks."""
    path = root
    if create:
        root.mkdir(parents=True, exist_ok=True)
    for part in parts:
        path = path / part
        require(not path.is_symlink(), "board directory cannot be a symlink")
        if create:
            path.mkdir(exist_ok=True)
        require(not path.exists() or path.is_dir(), "board path is not a directory")
    return path


def read_record(path: Path, limit: int) -> bytes:
    """Read only bounded regular files, refusing final-entry symlinks."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        require(
            stat.S_ISREG(os.fstat(stream.fileno()).st_mode),
            "record is not a regular file",
        )
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, "record exceeds configured byte limit")
    return raw


def atomic_write(path: Path, raw: bytes, *, replace: bool) -> bool:
    """Publish a closed staged file; return False for an identical existing message."""
    descriptor, name = tempfile.mkstemp(prefix=".duet-", dir=path.parent)
    staged = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            require(not path.is_symlink(), "checkpoint cannot be a symlink")
            os.replace(staged, path)
        else:
            try:
                os.link(staged, path, follow_symlinks=False)
            except FileExistsError:
                require(
                    read_record(path, len(raw)) == raw,
                    "message ID already exists with different bytes",
                )
                return False
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return True
    finally:
        staged.unlink(missing_ok=True)


@dataclass(frozen=True)
class Snapshot:
    """Validated causal history, with explicit incomplete and invalid records."""

    messages: tuple[Message, ...]
    pending: Mapping[str, tuple[str, ...]]
    invalid: Mapping[str, str]
    conflicts: Mapping[str, tuple[str, ...]]

    @property
    def history(self) -> dict[str, Message]:
        """Index causal-ready records; this is not a task-state interpreter."""
        return {message.id: message for message in self.messages}

    def as_json(self, messages: tuple[Message, ...] | None = None) -> dict[str, Any]:
        """Make the validation boundary explicit to consumers."""
        return {
            "messages": [
                strict_json(message.raw)
                for message in (self.messages if messages is None else messages)
            ],
            "pending": dict(self.pending),
            "invalid": dict(self.invalid),
            "conflicts": dict(self.conflicts),
            "semantic_review_required": True,
        }


def find_conflicts(messages: tuple[Message, ...]) -> dict[str, tuple[str, ...]]:
    """Expose competing control replies without choosing an arrival-order winner."""
    controls = {
        Kind.ACCEPT,
        Kind.DECLINE,
        Kind.AGREE,
        Kind.OBJECT,
        Kind.ANSWER,
        Kind.SUBMIT,
        Kind.VERIFY,
        Kind.REJECT,
        Kind.RESUME,
        Kind.RELEASE,
        Kind.WITHDRAW,
    }
    replies: dict[str, list[str]] = {}
    for message in messages:
        if message.reply_to is not None and message.kind in controls:
            replies.setdefault(message.reply_to, []).append(message.id)
    return {
        target: tuple(sorted(ids)) for target, ids in replies.items() if len(ids) > 1
    }


def scan(settings: Settings) -> Snapshot:
    """Read and causally order all records, never marking them processed."""
    goal_dir = directory(settings.board, ("messages", settings.goal))
    candidates: dict[str, Message] = {}
    invalid: dict[str, str] = {}
    count = 0
    if goal_dir.exists():
        for participant_dir in sorted(goal_dir.iterdir()):
            require(
                participant_dir.name in settings.participants,
                "unexpected participant directory",
            )
            sender_dir = directory(
                settings.board, ("messages", settings.goal, participant_dir.name)
            )
            for path in sorted(sender_dir.iterdir()):
                if path.suffix != ".json":
                    continue
                count += 1
                require(
                    count <= settings.max_messages,
                    "board exceeds configured message count; no partial scan returned",
                )
                key = path.stem
                try:
                    identifier(key, "message filename")
                    message = Message.parse(
                        read_record(path, settings.max_message_bytes), settings
                    )
                    require(
                        message.id == key and message.sender == participant_dir.name,
                        "message identity differs from its path",
                    )
                    require(
                        key not in candidates and key not in invalid,
                        "duplicate message ID across senders",
                    )
                    candidates[key] = message
                except (DuetError, OSError) as error:
                    candidates.pop(key, None)
                    invalid[key] = str(error)
    ordered: dict[str, Message] = {}
    waiting = dict(candidates)
    while waiting:
        progressed = False
        for key, message in tuple(sorted(waiting.items())):
            if any(item in invalid for item in message.causes):
                invalid[key] = "invalid causal prerequisite"
            elif not all(item in ordered for item in message.causes):
                continue
            else:
                try:
                    message.check_response(ordered)
                    ordered[key] = message
                except DuetError as error:
                    invalid[key] = str(error)
            del waiting[key]
            progressed = True
        if not progressed:
            break
    # Reachability distinguishes true cycles from merely missing prerequisites.
    for key in tuple(waiting):
        frontier = list(waiting[key].causes)
        visited: set[str] = set()
        while frontier:
            item = frontier.pop()
            if item == key:
                invalid[key] = "causal cycle"
                break
            if item in waiting and item not in visited:
                visited.add(item)
                frontier.extend(waiting[item].causes)
    changed = True
    while changed:
        changed = False
        for key, message in waiting.items():
            if key not in invalid and any(item in invalid for item in message.causes):
                invalid[key] = "invalid causal prerequisite"
                changed = True
    pending = {
        key: tuple(item for item in message.causes if item not in ordered)
        for key, message in waiting.items()
        if key not in invalid
    }
    messages = tuple(ordered.values())
    return Snapshot(messages, pending, invalid, find_conflicts(messages))


def completion_proposal(message: Message) -> bool:
    """Identify the reserved goal-closing decision without inspecting its prose."""
    return (
        message.kind == Kind.PROPOSE
        and tuple(message.body["scope"]) == COMPLETION_SCOPE
    )


def check_completion(message: Message, settings: Settings, snapshot: Snapshot) -> None:
    """Keep one proposer and one unsettled completion offer; reject stale replies."""
    history = snapshot.history
    controls = {Kind.AGREE, Kind.OBJECT, Kind.WITHDRAW}
    if completion_proposal(message):
        require(
            message.sender == settings.completion_proposer,
            "only the designated completion proposer may propose goal completion",
        )
        for proposal in history.values():
            if not completion_proposal(proposal):
                continue
            replies = [
                item
                for item in history.values()
                if item.reply_to == proposal.id and item.kind in controls
            ]
            require(
                bool(replies),
                "a completion proposal is already pending; reuse it instead of proposing another",
            )
            require(
                proposal.id in snapshot.conflicts
                or not any(item.kind == Kind.AGREE for item in replies),
                "completion is already agreed; finish using the existing agreement",
            )
    elif message.kind in controls and message.reply_to is not None:
        target = history[message.reply_to]
        if not completion_proposal(target):
            return
        if message.kind == Kind.AGREE:
            require(
                target.sender == settings.completion_proposer,
                "agree only to the designated proposer's completion proposal",
            )
        require(
            not any(
                item.reply_to == target.id and item.kind in controls
                for item in history.values()
            ),
            "completion proposal is already settled or conflicted; refresh the board before replying",
        )


def publish(
    settings: Settings,
    participant: str,
    session: str,
    raw: bytes,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Serialize closing decisions with enrollment before checking and publishing."""
    message = Message.parse(raw, settings)
    guarded = completion_proposal(message) or (
        message.subject_type == "decision"
        and message.kind in {Kind.AGREE, Kind.OBJECT, Kind.WITHDRAW}
    )
    lock = nullcontext()
    if guarded and not dry_run:
        root = directory(settings.board, ("enrollment",), create=True)
        lock = file_lock(root / SESSION_LOCK)
    with lock:
        return _publish(settings, participant, session, raw, dry_run=dry_run)


def _publish(
    settings: Settings,
    participant: str,
    session: str,
    raw: bytes,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Validate identity, prerequisites and readiness, then publish without replacement."""
    settings.check_actor(participant, session)
    message = Message.parse(raw, settings)
    require(
        message.sender == participant and message.session == session,
        "publishing identity mismatch",
    )
    snapshot = scan(settings)
    history = snapshot.history
    if message.id in history:
        require(
            history[message.id].raw == raw,
            "message ID already exists with different bytes",
        )
        return {
            "id": message.id,
            "published": False,
            "duplicate": True,
            "dry_run": dry_run,
        }
    from enrollment import check_membership

    check_membership(settings, participant, session)
    require(
        message.id not in snapshot.invalid and message.id not in snapshot.pending,
        "message ID is already present but not usable",
    )
    require(
        all(item in history for item in message.causes),
        "causal prerequisite is missing or invalid",
    )
    message.check_response(history)
    check_completion(message, settings, snapshot)
    if message.kind not in {Kind.READY, Kind.INFORM, Kind.ALERT, Kind.RECEIPT}:
        for sender in settings.participants:
            require(
                any(
                    item.kind == Kind.READY
                    and item.sender == sender
                    and item.body["context_revision"] == settings.context_revision
                    and (sender != participant or item.session == session)
                    for item in history.values()
                ),
                "both participants must publish ready before joint work",
            )
    if message.kind == Kind.READY:
        require(
            message.body["context_revision"] == settings.context_revision,
            "goal context revision mismatch",
        )
        require(
            not any(
                item.kind == Kind.READY
                and item.sender == participant
                and item.session == session
                and item.body["context_revision"] == settings.context_revision
                for item in history.values()
            ),
            "session already announced readiness; retry the original ID",
        )
    initial = opening(settings, history, snapshot.conflicts)
    predecessors = ancestry(message.causes, history)
    if message.kind == Kind.PROPOSE:
        if message.subject_id == settings.initial_plan_id or initial.proposal is None:
            require(
                not initial.conflicted and initial.proposal is None,
                "initial plan already exists or is conflicted",
            )
            require(
                participant == settings.initial_proposer,
                "only the participant whose ID sorts first may propose the initial plan",
            )
            require(
                message.subject_id == settings.initial_plan_id
                and tuple(message.body["scope"]) == INITIAL_PLAN_SCOPE,
                "opening proposal must use the initial-plan decision and work-division scope",
            )
            for sender in settings.participants:
                require(
                    any(
                        history[key].kind == Kind.READY
                        and history[key].sender == sender
                        and history[key].body["context_revision"]
                        == settings.context_revision
                        and (sender != participant or history[key].session == session)
                        for key in predecessors
                    ),
                    "initial plan must causally include both readiness messages",
                )
        elif initial.agreement is None:
            require(
                not initial.conflicted
                and initial.proposal.id in predecessors
                and tuple(message.body["scope"]) == INITIAL_PLAN_SCOPE,
                "before startup agreement, propose a counterplan citing the opening proposal",
            )
    if message.kind == Kind.REQUEST:
        require(
            initial.agreement is not None and not initial.conflicted,
            "initial work division must be agreed before requesting tasks",
        )
        require(
            initial.agreement.id in predecessors,
            "task request must causally include the initial-plan agreement",
        )
    destination = (
        settings.board / "messages" / settings.goal / participant / f"{message.id}.json"
    )
    if dry_run:
        return {"id": message.id, "path": str(destination), "dry_run": True}
    directory(settings.board, ("messages", settings.goal, participant), create=True)
    created = atomic_write(destination, raw, replace=False)
    return {
        "id": message.id,
        "published": created,
        "duplicate": not created,
        "dry_run": False,
    }


def checkpoint_path(settings: Settings, participant: str) -> Path:
    """Locate only the selected participant's recovery record."""
    require(participant in settings.participants, "participant is not in this goal")
    return (
        directory(settings.board, ("checkpoints", settings.goal))
        / f"{participant}.json"
    )


def save_checkpoint(
    settings: Settings,
    participant: str,
    session: str,
    raw: bytes,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Persist the agent's explicit processing decisions; receiving never implies acceptance."""
    from enrollment import check_membership

    settings.check_actor(participant, session)
    check_membership(settings, participant, session)
    require(
        len(raw) <= settings.max_checkpoint_bytes,
        "checkpoint exceeds configured byte limit",
    )
    data = strict_json(raw)
    validate_checkpoint(settings, participant, data, session=session)
    payload = encoded(data)
    require(
        len(payload) <= settings.max_checkpoint_bytes,
        "encoded checkpoint exceeds configured byte limit",
    )
    path = checkpoint_path(settings, participant)
    if not dry_run:
        directory(settings.board, ("checkpoints", settings.goal), create=True)
        atomic_write(path, payload, replace=True)
    return {"participant": participant, "saved": not dry_run, "dry_run": dry_run}


def validate_checkpoint(
    settings: Settings,
    participant: str,
    data: Mapping[str, Any],
    *,
    session: str | None = None,
    snapshot: Snapshot | None = None,
) -> None:
    """Check cached evidence on recovery, allowing records published since the save."""
    require(
        set(data)
        == {
            "goal",
            "participant",
            "session",
            "processed",
            "outgoing",
            "deferred",
            "invalid",
            "active",
            "external_actions",
        },
        "invalid checkpoint fields",
    )
    require(
        (data["goal"], data["participant"]) == (settings.goal, participant),
        "checkpoint identity mismatch",
    )
    identifier(data["session"], "checkpoint.session")
    require(
        session is None or data["session"] == session, "checkpoint session mismatch"
    )
    snapshot = snapshot if snapshot is not None else scan(settings)
    history = snapshot.history
    for name in ("processed", "outgoing"):
        values = data[name]
        require(isinstance(values, list), f"checkpoint {name} must be an array")
        for item in values:
            identifier(item, name)
        require(
            len(values) == len(set(values)) and set(values) <= set(history),
            f"checkpoint {name} contains duplicate or unavailable IDs",
        )
    outgoing = {item.id for item in history.values() if item.sender == participant}
    require(
        set(data["outgoing"]) <= outgoing, "checkpoint outgoing contains peer messages"
    )
    require(
        session is None or set(data["outgoing"]) == outgoing,
        "checkpoint outgoing must include all of this participant's published messages",
    )
    for name in ("deferred", "invalid"):
        require(isinstance(data[name], dict), f"checkpoint {name} must be an object")
        for key, reason in data[name].items():
            nonempty(key, name)
            nonempty(reason, name)
    require(isinstance(data["active"], list), "checkpoint active must be an array")
    for entry in data["active"]:
        require(
            isinstance(entry, dict) and set(entry) == {"subject", "summary", "causes"},
            "invalid active checkpoint entry",
        )
        subject(entry["subject"])
        nonempty(entry["summary"], "active.summary")
        require(
            isinstance(entry["causes"], list) and bool(entry["causes"]),
            "active entry needs supporting causes",
        )
        for item in entry["causes"]:
            identifier(item, "active cause")
            require(item in data["processed"], "active cause must have been processed")
    require(
        isinstance(data["external_actions"], list), "external_actions must be an array"
    )
    for action in data["external_actions"]:
        require(
            isinstance(action, dict) and set(action) == {"id", "status", "summary"},
            "invalid external action entry",
        )
        identifier(action["id"], "action.id")
        require(
            action["status"] in ("planned", "running", "uncertain", "resolved"),
            "invalid action status",
        )
        nonempty(action["summary"], "action.summary")
