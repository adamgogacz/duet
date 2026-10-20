# Copyright 2026 Adam Gogacz
# SPDX-License-Identifier: Apache-2.0

"""Duet message structure and response validation, independent of file delivery."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from settings import Settings, identifier, nonempty, require, strict_json


class Kind(StrEnum):
    """The closed vocabulary of Duet communicative acts."""

    READY = "ready"
    INFORM = "inform"
    RECEIPT = "receipt"
    ASK = "ask"
    ANSWER = "answer"
    REQUEST = "request"
    ACCEPT = "accept"
    DECLINE = "decline"
    PROPOSE = "propose"
    AGREE = "agree"
    OBJECT = "object"
    SUBMIT = "submit"
    VERIFY = "verify"
    REJECT = "reject"
    AWAIT = "await"
    RESUME = "resume"
    RELEASE = "release"
    WITHDRAW = "withdraw"
    ALERT = "alert"


BODY_FIELDS = MappingProxyType(
    {
        Kind.READY: frozenset({"context_revision", "summary"}),
        Kind.INFORM: frozenset({"text"}),
        Kind.RECEIPT: frozenset(),
        Kind.ASK: frozenset({"question"}),
        Kind.ANSWER: frozenset({"answer", "evidence"}),
        Kind.REQUEST: frozenset({"objective", "scope", "inputs", "criteria"}),
        Kind.ACCEPT: frozenset(),
        Kind.DECLINE: frozenset({"reason"}),
        Kind.PROPOSE: frozenset({"proposal", "scope"}),
        Kind.AGREE: frozenset(),
        Kind.OBJECT: frozenset({"reason"}),
        Kind.SUBMIT: frozenset({"summary", "artifacts", "evidence"}),
        Kind.VERIFY: frozenset({"evidence"}),
        Kind.REJECT: frozenset({"reason", "criteria"}),
        Kind.AWAIT: frozenset({"blocks", "condition", "deadline", "reason"}),
        Kind.RESUME: frozenset({"evidence"}),
        Kind.RELEASE: frozenset({"reason"}),
        Kind.WITHDRAW: frozenset({"reason"}),
        Kind.ALERT: frozenset({"reason", "scope"}),
    }
)
SUBJECT_TYPES = frozenset({"goal", "task", "question", "decision", "wait", "incident"})
INITIATORS = frozenset(
    {Kind.READY, Kind.ASK, Kind.REQUEST, Kind.PROPOSE, Kind.AWAIT, Kind.ALERT}
)
TARGET_KINDS = MappingProxyType(
    {
        Kind.ANSWER: frozenset({Kind.ASK}),
        Kind.ACCEPT: frozenset({Kind.REQUEST}),
        Kind.DECLINE: frozenset({Kind.REQUEST}),
        Kind.AGREE: frozenset({Kind.PROPOSE}),
        Kind.OBJECT: frozenset({Kind.PROPOSE}),
        Kind.SUBMIT: frozenset({Kind.ACCEPT, Kind.REJECT}),
        Kind.VERIFY: frozenset({Kind.SUBMIT}),
        Kind.REJECT: frozenset({Kind.SUBMIT}),
        Kind.RESUME: frozenset({Kind.AWAIT}),
        Kind.RELEASE: frozenset({Kind.ACCEPT}),
        Kind.WITHDRAW: frozenset({Kind.ASK, Kind.REQUEST, Kind.PROPOSE, Kind.AWAIT}),
    }
)


def subject(value: Any) -> None:
    """Validate the closed subject structure."""
    require(
        isinstance(value, dict) and set(value) == {"type", "id"},
        "invalid subject fields",
    )
    require(
        isinstance(value["type"], str) and value["type"] in SUBJECT_TYPES,
        "invalid subject type",
    )
    identifier(value["id"], "subject.id")


def condition(value: Any, settings: Settings) -> None:
    """Validate a wait predicate without claiming its truth."""
    require(
        isinstance(value, dict) and set(value) == {"kind", "subject", "from", "target"},
        "invalid wait condition fields",
    )
    require(
        value["kind"] in ("answer", "accept", "agree", "verify", "release"),
        "invalid wait condition kind",
    )
    subject(value["subject"])
    require(value["from"] in settings.participants, "invalid condition sender")
    if value["target"] is not None:
        identifier(value["target"], "condition.target")


def validate_body(kind: Kind, body: Any, settings: Settings) -> None:
    """Check required fields, field types, and configured wait policy."""
    require(isinstance(body, dict), "body must be an object")
    fields = BODY_FIELDS[kind]
    require(
        fields <= set(body) <= fields | {"text", "artifacts"},
        "body fields do not match act",
    )
    for name, value in body.items():
        if name in {"scope", "blocks", "criteria", "evidence"}:
            require(
                isinstance(value, list) and bool(value),
                f"{name} must be a nonempty array",
            )
            for item in value:
                (identifier if name in {"scope", "blocks"} else nonempty)(item, name)
        elif name in {"inputs", "artifacts"}:
            require(isinstance(value, list), f"{name} must be an array")
            for artifact in value:
                require(
                    isinstance(artifact, dict)
                    and set(artifact) == {"id", "revision", "description"},
                    "invalid artifact fields",
                )
                identifier(artifact["id"], "artifact.id")
                for key in ("revision", "description"):
                    nonempty(artifact[key], f"artifact.{key}")
                require(
                    artifact["revision"].lower() != "latest",
                    "artifact revision must be immutable",
                )
        elif name == "condition":
            condition(value, settings)
        elif name == "deadline":
            if value is None:
                continue
            else:
                nonempty(value, "deadline")
                try:
                    parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
                        tzinfo=UTC
                    )
                except ValueError:
                    require(False, "deadline must be a UTC timestamp")
                require(
                    parsed.strftime("%Y-%m-%dT%H:%M:%SZ") == value,
                    "deadline must use exact UTC form",
                )
        else:
            nonempty(value, name)
    if kind == Kind.READY:
        identifier(body["context_revision"], "context_revision")


@dataclass(frozen=True)
class Message:
    """Validated envelope retaining its exact received JSON bytes."""

    id: str
    sender: str
    session: str
    recipient: str
    kind: Kind
    subject_type: str
    subject_id: str
    reply_to: str | None
    causes: tuple[str, ...]
    body: Mapping[str, Any]
    raw: bytes

    @classmethod
    def parse(cls, raw: bytes, settings: Settings) -> Message:
        """Validate structure without executing task or decision effects."""
        require(
            len(raw) <= settings.max_message_bytes,
            "message exceeds configured byte limit",
        )
        data = strict_json(raw)
        require(
            set(data)
            == {
                "id",
                "goal",
                "from",
                "session",
                "to",
                "subject",
                "kind",
                "reply_to",
                "causes",
                "body",
            },
            "invalid envelope fields",
        )
        require(data["goal"] == settings.goal, "goal mismatch")
        for name in ("id", "from", "to", "session"):
            identifier(data[name], name)
        require(
            data["from"] in settings.participants
            and data["to"] in settings.participants
            and data["from"] != data["to"],
            "invalid sender or recipient",
        )
        require(
            isinstance(data["kind"], str) and data["kind"] in BODY_FIELDS, "unknown act"
        )
        kind = Kind(data["kind"])
        subject(data["subject"])
        expected = {
            Kind.READY: "goal",
            Kind.ASK: "question",
            Kind.REQUEST: "task",
            Kind.PROPOSE: "decision",
            Kind.AWAIT: "wait",
            Kind.ALERT: "incident",
        }.get(kind)
        if expected:
            require(data["subject"]["type"] == expected, "act has wrong subject type")
        if kind == Kind.READY:
            require(
                data["subject"]["id"] == settings.goal,
                "ready must name the current goal",
            )
        parent = data["reply_to"]
        if parent is not None:
            identifier(parent, "reply_to")
        if kind in INITIATORS:
            require(parent is None, "initiating act cannot reply")
        elif kind != Kind.INFORM:
            require(parent is not None, "response requires reply_to")
        causes = data["causes"]
        require(isinstance(causes, list), "causes must be an array")
        for item in causes:
            identifier(item, "cause")
        require(
            len(causes) == len(set(causes)) and data["id"] not in causes,
            "invalid causal IDs",
        )
        require(parent is None or parent in causes, "reply_to must appear in causes")
        validate_body(kind, data["body"], settings)
        return cls(
            data["id"],
            data["from"],
            data["session"],
            data["to"],
            kind,
            data["subject"]["type"],
            data["subject"]["id"],
            parent,
            tuple(causes),
            MappingProxyType(data["body"]),
            raw,
        )

    def check_response(self, history: Mapping[str, Message]) -> None:
        """Check reply kind, identity, subject, and resumption evidence."""
        if self.reply_to is None or self.kind == Kind.INFORM:
            return
        target = history[self.reply_to]
        require(
            (self.subject_type, self.subject_id)
            == (target.subject_type, target.subject_id),
            "response subject differs from its target",
        )
        if self.kind == Kind.RECEIPT:
            require(target.kind != Kind.RECEIPT, "receipt loops are prohibited")
        else:
            require(
                target.kind in TARGET_KINDS[self.kind], "response targets the wrong act"
            )
        same_author = self.kind in {Kind.RESUME, Kind.RELEASE, Kind.WITHDRAW}
        same_author |= self.kind == Kind.SUBMIT and target.kind == Kind.ACCEPT
        require(
            (self.sender == target.sender) == same_author,
            "response has the wrong author",
        )
        if self.kind == Kind.RESUME:
            predicate = target.body["condition"]
            matches = [history[item] for item in self.causes if item != target.id]
            require(
                any(
                    event.kind == predicate["kind"]
                    and event.sender == predicate["from"]
                    and event.subject_type == predicate["subject"]["type"]
                    and event.subject_id == predicate["subject"]["id"]
                    and (
                        predicate["target"] is None
                        or event.reply_to == predicate["target"]
                    )
                    for event in matches
                ),
                "resume lacks its satisfying event in causes",
            )
