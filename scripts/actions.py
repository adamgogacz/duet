# Copyright 2026 Adam Gogacz
# SPDX-License-Identifier: Apache-2.0

"""Build valid messages, retain retry identity, and acknowledge handled replies."""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from artifacts import capture, verify
from board import atomic_write, directory, publish, read_record, scan
from delivery import acknowledge
from enrollment import check_membership
from locking import file_lock
from messages import Kind, Message
from settings import (
    COMPLETION_SCOPE,
    INITIAL_PLAN_SCOPE,
    DuetError,
    Settings,
    encoded,
    identifier,
    nonempty,
    require,
    strict_json,
    temporary_directory,
)
from startup import causal_frontier, opening


@dataclass(frozen=True)
class Connection:
    """A saved join result, so agents carry one handle instead of envelope fields."""

    settings_path: Path
    participant: str
    session: str

    @property
    def settings(self) -> Settings:
        """Load immutable task configuration at the command boundary."""
        return Settings.load(self.settings_path)

    @classmethod
    def load(cls, path: Path | str) -> Connection:
        """Validate a retained identity without guessing an anonymous participant."""
        token = str(path)
        short = re.fullmatch(r"conn-[0-9a-f]{32}", token) is not None
        path = (
            directory(temporary_directory(), ("duet", "connections")) / f"{token}.json"
            if short
            else Path(path)
        )
        data = strict_json(read_record(path, Settings.default("max_checkpoint_bytes")))
        require(set(data) == set(cls.__dataclass_fields__), "invalid connection record")
        settings_path = Path(
            nonempty(data["settings_path"], "connection settings path")
        )
        require(
            settings_path.is_absolute(), "connection settings path must be absolute"
        )
        connection = cls(
            settings_path,
            identifier(data["participant"], "participant"),
            identifier(data["session"], "session"),
        )
        connection.settings.check_actor(connection.participant, connection.session)
        require(
            not short or token == connection.short_handle,
            "connection handle does not match its saved actor",
        )
        return connection

    def save(self) -> Path:
        """Persist this conversation's assigned connection idempotently."""
        settings = self.settings
        root = directory(
            settings.board,
            ("connections", settings.goal, self.participant),
            create=True,
        )
        path = root / f"{self.session}.json"
        atomic_write(
            path,
            encoded(
                {
                    "settings_path": str(self.settings_path),
                    "participant": self.participant,
                    "session": self.session,
                }
            ),
            replace=False,
        )
        return path

    @property
    def short_handle(self) -> str:
        """Bind a compact capability explicitly to this actor and collaboration."""
        raw = encoded(
            {
                "settings_path": str(self.settings_path),
                "participant": self.participant,
                "session": self.session,
            }
        )
        return "conn-" + hashlib.sha256(raw).hexdigest()[:32]

    def save_alias(self) -> str:
        """Save an optional local-temp lookup; never infer identity from a workspace."""
        root = directory(temporary_directory(), ("duet", "connections"), create=True)
        atomic_write(
            root / f"{self.short_handle}.json",
            encoded(
                {
                    "settings_path": str(self.settings_path),
                    "participant": self.participant,
                    "session": self.session,
                }
            ),
            replace=False,
        )
        return self.short_handle


def _outbox(connection: Connection, *, create: bool) -> Path:
    """Keep each writer's retry journal separate from published messages."""
    settings = connection.settings
    return directory(
        settings.board,
        ("outbox", settings.goal, connection.participant, connection.session),
        create=create,
    )


def _field(kind: Kind) -> str:
    """Map a plain-text statement to the act's substantive field."""
    return {
        Kind.READY: "summary",
        Kind.INFORM: "text",
        Kind.ASK: "question",
        Kind.ANSWER: "answer",
        Kind.REQUEST: "objective",
        Kind.PROPOSE: "proposal",
        Kind.DECLINE: "reason",
        Kind.OBJECT: "reason",
        Kind.REJECT: "reason",
        Kind.AWAIT: "reason",
        Kind.RELEASE: "reason",
        Kind.WITHDRAW: "reason",
        Kind.ALERT: "reason",
        Kind.SUBMIT: "summary",
    }.get(kind, "text")


def compose(
    connection: Connection,
    kind: Kind,
    *,
    text: str | None = None,
    reply_to: str | None = None,
    scope: Sequence[str] | None = None,
    criteria: Sequence[str] | None = None,
    stop_condition: str | None = None,
    outcome: str = "complete",
    evidence: Sequence[str] | None = None,
    artifacts: Sequence[Path] = (),
    subject_type: str | None = None,
    subject_id: str | None = None,
    body: Mapping[str, Any] | None = None,
    causes: Sequence[str] = (),
    dry_run: bool = False,
) -> bytes:
    """Fill mechanical fields while leaving commitments and judgments explicit."""
    settings = connection.settings
    snapshot = scan(settings)
    history = snapshot.history
    target = None
    causal = set(causes)
    if reply_to is not None:
        identifier(reply_to, "reply_to")
        require(reply_to in history, "reply target is missing or invalid")
        target = history[reply_to]
        causal.add(reply_to)
        subject_type = subject_type or target.subject_type
        subject_id = subject_id or target.subject_id
    payload = dict(body or {})
    require(
        "artifacts" not in payload, "use --artifact to attach verified file revisions"
    )
    if text is not None:
        field = _field(kind)
        require(field not in payload, "text field was supplied twice")
        payload[field] = text
    for field, value in (
        ("scope", scope),
        ("criteria", criteria),
        ("evidence", evidence),
    ):
        if value is not None:
            require(field not in payload, f"{field} was supplied twice")
            payload[field] = list(value)
    if stop_condition is not None:
        require("stop_condition" not in payload, "stop condition supplied twice")
        payload["stop_condition"] = stop_condition
    if kind == Kind.READY:
        payload.setdefault("context_revision", settings.context_revision)
        subject_type, subject_id = "goal", settings.goal
    if kind == Kind.REQUEST:
        payload.setdefault("inputs", [])
    if kind == Kind.SUBMIT:
        payload.setdefault("artifacts", [])
    if artifacts:
        require(
            opening(settings, history, snapshot.conflicts).agreement is not None,
            "agree completion criteria before capturing task artifacts",
        )
        payload["artifacts"] = [
            capture(settings, path, dry_run=dry_run).reference() for path in artifacts
        ]
    if target is not None and kind in {Kind.AGREE, Kind.VERIFY}:
        references = target.body.get("artifacts", [])
        for reference in references:
            verify(settings, reference, current_origin=True)
        if references:
            require(
                not artifacts, "approval uses the target's exact artifact revisions"
            )
            payload["artifacts"] = list(references)
    initial = opening(settings, history, snapshot.conflicts)
    if (
        kind == Kind.PROPOSE
        and tuple(payload.get("scope", [])) == INITIAL_PLAN_SCOPE
        and initial.proposal is None
    ):
        subject_type, subject_id = "decision", settings.initial_plan_id
        causal.update(
            item.id
            for item in history.values()
            if item.kind == Kind.READY
            and item.body["context_revision"] == settings.context_revision
        )
    elif (
        kind == Kind.PROPOSE
        and tuple(payload.get("scope", [])) == INITIAL_PLAN_SCOPE
        and initial.agreement is None
        and initial.proposal is not None
    ):
        causal.add(initial.proposal.id)
        if initial.conflicted:
            causal.update(
                item.id
                for item in history.values()
                if item.kind == Kind.AGREE
                and tuple(history[item.reply_to].body.get("scope", []))
                == INITIAL_PLAN_SCOPE
            )
    if (
        kind == Kind.PROPOSE
        and tuple(payload.get("scope", [])) == INITIAL_PLAN_SCOPE
        and initial.agreement is not None
    ):
        causal.add(initial.agreement.id)
    if kind == Kind.RELEASE and target is not None:
        causal.update(
            item.id
            for item in history.values()
            if item.subject_type == target.subject_type
            and item.subject_id == target.subject_id
        )
    if kind == Kind.REQUEST and initial.agreement is not None:
        causal.add(initial.agreement.id)
    if kind == Kind.PROPOSE and tuple(payload.get("scope", [])) == COMPLETION_SCOPE:
        require(
            initial.agreement is not None,
            "agree completion criteria before proposing completion",
        )
        require(
            "gate" not in payload and "outcome" not in payload,
            "gate and outcome are constructed by the utility",
        )
        from workflow import effect_fingerprint

        payload["effects"] = effect_fingerprint(snapshot)
        payload["gate"] = initial.agreement.id
        payload["outcome"] = outcome
        # A closing offer pins the journal basis; later task messages require fresh assent.
        causal.update(causal_frontier(history))
    key = f"{connection.participant}-{uuid.uuid4().hex}"
    subject_type = subject_type or {
        Kind.ASK: "question",
        Kind.REQUEST: "task",
        Kind.PROPOSE: "decision",
        Kind.AWAIT: "wait",
        Kind.ALERT: "incident",
    }.get(kind, "goal")
    subject_id = subject_id or (settings.goal if subject_type == "goal" else key)
    raw = encoded(
        {
            "id": key,
            "goal": settings.goal,
            "from": connection.participant,
            "session": connection.session,
            "to": next(
                peer for peer in settings.participants if peer != connection.participant
            ),
            "subject": {"type": subject_type, "id": subject_id},
            "kind": kind.value,
            "reply_to": reply_to,
            "causes": sorted(causal),
            "body": payload,
        }
    )
    publish(settings, connection.participant, connection.session, raw, dry_run=True)
    return raw


def _deliver(connection: Connection, raw: bytes, root: Path) -> dict:
    """Publish one saved envelope, then acknowledge only its handled reply target."""
    settings = connection.settings
    message = Message.parse(raw, settings)
    history = scan(settings).history
    if message.id in history:
        from enrollment import find_run, membership_paths

        path, _ = membership_paths(settings.board)
        if path.exists() and find_run(settings.board, settings.goal).terminal:
            require(
                history[message.id].raw == raw,
                "message ID already exists with different bytes",
            )
            return {
                "id": message.id,
                "published": False,
                "duplicate": True,
                "message": strict_json(raw),
            }
    check_membership(settings, connection.participant, connection.session)
    try:
        history = scan(settings).history
        if (
            message.id not in history
            and message.reply_to is not None
            and message.kind in {Kind.AGREE, Kind.VERIFY}
        ):
            target = history.get(message.reply_to)
            require(target is not None, "approval target is missing or invalid")
            for reference in target.body.get("artifacts", []):
                verify(settings, reference, current_origin=True)
        result = publish(settings, connection.participant, connection.session, raw)
        if message.reply_to is not None:
            acknowledge(
                settings, connection.participant, connection.session, [message.reply_to]
            )
    except DuetError:
        state = "prepared" if message.id in scan(settings).history else "rejected"
        atomic_write(
            root / "last.json",
            encoded({"id": message.id, "state": state}),
            replace=True,
        )
        raise
    atomic_write(
        root / "last.json",
        encoded({"id": message.id, "state": "published"}),
        replace=True,
    )
    return {
        **result,
        "message": strict_json(raw),
        "retry": "retry with the same connection",
    }


def send(
    connection: Connection,
    kind: Kind = Kind.INFORM,
    *,
    dry_run: bool = False,
    **options: Any,
) -> dict:
    """Save generated identity before publication; uncertain sends require retry."""
    settings = connection.settings
    check_membership(settings, connection.participant, connection.session)
    if dry_run:
        raw = compose(connection, kind, dry_run=True, **options)
        return {"dry_run": True, "message": strict_json(raw)}
    root = _outbox(connection, create=True)
    with file_lock(root / "send.lock"):
        last = root / "last.json"
        if last.exists() or last.is_symlink():
            record = last_operation(settings, root)
            if record["state"] == "prepared":
                require(
                    record["id"] in scan(settings).history,
                    "previous send has an uncertain outcome; use retry before sending another message",
                )
                _deliver(
                    connection,
                    read_record(
                        root / f"{identifier(record['id'], 'outbox id')}.json",
                        settings.max_message_bytes,
                    ),
                    root,
                )
        try:
            raw = compose(connection, kind, **options)
        except (DuetError, OSError) as error:
            raise DuetError(
                f"send was not prepared; no new message was published: {error}"
            ) from error
        key = strict_json(raw)["id"]
        try:
            atomic_write(root / f"{key}.json", raw, replace=False)
            atomic_write(last, encoded({"id": key, "state": "prepared"}), replace=True)
        except OSError as error:
            prepared = last.exists() and last_operation(settings, root)["id"] == key
            advice = (
                "use retry with this connection"
                if prepared
                else "correct the storage failure and rerun send"
            )
            raise DuetError(f"publication did not start; {advice}: {error}") from error
        return _deliver(connection, raw, root)


def retry(connection: Connection, *, dry_run: bool = False) -> dict:
    """Retry the exact saved envelope, even when the original command result was lost."""
    settings = connection.settings
    root = _outbox(connection, create=False)
    if dry_run:
        record = last_operation(settings, root)
        raw = read_record(root / f"{record['id']}.json", settings.max_message_bytes)
        publish(settings, connection.participant, connection.session, raw, dry_run=True)
        return {"dry_run": True, "message": strict_json(raw)}
    with file_lock(root / "send.lock"):
        # One writer owns this connection; lock also prevents accidental overlapping sends.
        record = last_operation(settings, root)
        raw = read_record(root / f"{record['id']}.json", settings.max_message_bytes)
        return _deliver(connection, raw, root)


def last_operation(settings: Settings, root: Path) -> dict:
    """Fail closed on a corrupt retry journal rather than lose an uncertain send."""
    record = strict_json(read_record(root / "last.json", settings.max_checkpoint_bytes))
    require(
        set(record) == {"id", "state"}
        and isinstance(record["state"], str)
        and record["state"] in {"prepared", "published", "rejected"},
        "invalid retry journal; recover it before sending",
    )
    identifier(record["id"], "outbox id")
    return record
