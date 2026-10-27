# Copyright 2026 Adam Gogacz
# SPDX-License-Identifier: Apache-2.0

"""Correlate passive hook observations with native calls and enrolled participants."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def input_digest(value: Mapping[str, Any], cwd: str | None = None) -> str:
    """Bind the entire tool input without duplicating report contents in the audit."""
    normalized = dict(value)
    if cwd:
        for field in ("file_path", "path"):
            path = normalized.get(field)
            if isinstance(path, str) and path:
                normalized[field] = os.path.normpath(os.path.join(cwd, path))
    return hashlib.sha256(
        json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(frozen=True)
class NativeCall:
    """One native tool identity, scoped to its host session rather than its Duet role."""

    session: str
    id: str
    tool: str
    arguments: Mapping[str, Any]


def native_calls(streams: Sequence[Sequence[dict[str, Any]]]) -> tuple[NativeCall, ...]:
    """Deduplicate identical stream replay; reject changed inputs under the same ID."""
    calls: dict[tuple[str, str], NativeCall] = {}
    for stream in streams:
        for row in stream:
            message = row.get("message")
            if row.get("type") != "assistant" or not isinstance(message, dict):
                continue
            for block in message.get("content", []):
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                call = NativeCall(
                    row.get("session_id", ""),
                    block.get("id", ""),
                    block.get("name", ""),
                    block.get("input", {}),
                )
                if (
                    not call.session
                    or not call.id
                    or not isinstance(call.arguments, dict)
                ):
                    raise ValueError("Native tool call lacks its identity or input")
                key = (call.session, call.id)
                if key in calls and calls[key] != call:
                    raise ValueError("Native tool call changed under an existing ID")
                calls[key] = call
    return tuple(calls.values())


def audit_complete(
    calls: Sequence[NativeCall], audits: Sequence[dict[str, Any]]
) -> bool:
    """Require exactly one matching observation per native call, with no dropped errors."""
    expected = {(call.session, call.id): call for call in calls}
    observed: set[tuple[str, str]] = set()
    for item in audits:
        key = (item.get("session_id", ""), item.get("tool_use_id", ""))
        call = expected.get(key)
        if (
            item.get("error")
            or call is None
            or key in observed
            or item.get("tool") != call.tool
            or item.get("input_digest") != input_digest(call.arguments, item.get("cwd"))
            or type(item.get("time_ns")) is not int
            or not isinstance(item.get("cwd"), str)
        ):
            return False
        observed.add(key)
    return bool(expected) and observed == set(expected)


def script_command(command: str) -> tuple[str, str]:
    """Recognize literal Python script invocations, not names quoted in message text."""
    try:
        words = shlex.split(command)
    except ValueError:
        return "", ""
    if not words or Path(words[0]).name not in {"python", "python3"}:
        return "", ""
    index = next(
        (i for i, word in enumerate(words[1:], 1) if not word.startswith("-")), None
    )
    if index is None:
        return "", ""
    return words[index], words[index + 1] if len(words) > index + 1 else ""


def task_tool(record: Mapping[str, Any]) -> bool:
    """Include unscoped content searches and task-targeted filename searches."""
    path = str(record.get("file_path") or "")
    parts = Path(path).parts
    if set(parts) & {"inputs", "output"} or Path(path).name == "case_checks.py":
        return True
    if record.get("tool") == "Glob":
        # A filename-only instruction locator is not evidence of reading task content.
        return bool(
            set(Path(str(record.get("pattern") or "")).parts) & {"inputs", "output"}
        )
    if record.get("tool") == "Grep":
        if ".claude" in parts and "skills" in parts:
            return False
        return not (
            path
            and Path(path).name
            in {
                "TASK.md",
                "AGENTS.md",
                "CLAUDE.md",
                "SKILL.md",
            }
        )
    script, _ = script_command(str(record.get("command") or ""))
    return Path(script).name == "case_checks.py"


def joined_participants(
    streams: Sequence[Sequence[dict[str, Any]]],
    calls: Sequence[NativeCall],
    members: Iterable[Mapping[str, Any]],
    script: Path,
) -> dict[str, str]:
    """Bind native sessions through successful join results checked against enrollment."""
    joins = {
        (call.session, call.id): call
        for call in calls
        if call.tool == "Bash"
        and script_command(str(call.arguments.get("command", "")))
        == (str(script), "join")
    }
    enrolled = {(member["participant"], member["session"]) for member in members}
    participants: dict[str, set[tuple[str, str]]] = {}
    for stream in streams:
        for row in stream:
            message = row.get("message")
            if row.get("type") != "user" or not isinstance(message, dict):
                continue
            content = message.get("content", [])
            if not isinstance(content, list):
                continue
            for block in content:
                key = (row.get("session_id", ""), block.get("tool_use_id", ""))
                if (
                    key not in joins
                    or block.get("type") != "tool_result"
                    or block.get("is_error")
                ):
                    continue
                raw = block.get("content")
                if isinstance(raw, list):
                    raw = "\n".join(
                        item.get("text", "")
                        for item in raw
                        if item.get("type") == "text"
                    )
                try:
                    result = json.loads(raw) if isinstance(raw, str) else {}
                except ValueError:
                    continue
                if not isinstance(result, dict) or result.get("dry_run") is not False:
                    continue
                actor = (result.get("participant"), result.get("session"))
                if actor in enrolled:
                    participants.setdefault(key[0], set()).add(actor)
    return {
        session: next(iter(actors))[0]
        for session, actors in participants.items()
        if len(actors) == 1
    }


def staged_evidence_checks(
    audits: Sequence[Mapping[str, Any]],
    participants: Mapping[str, str],
    offers: Sequence[Mapping[str, Any]],
    messages: Sequence[Mapping[str, Any]],
    times: Mapping[str, int],
    evidence: str,
) -> dict[str, bool]:
    """Check sealed evidence order and attribute the reviewer's read before its objection."""
    reads = [
        item
        for item in audits
        if item.get("tool") == "Read"
        and str(item.get("file_path", "")).endswith(evidence)
    ]
    reviewer_reads = [
        item
        for item in reads
        if offers
        and participants.get(item.get("session_id")) is not None
        and participants[item["session_id"]] != offers[0]["from"]
    ]
    objections = [
        item
        for item in messages
        if offers
        and item.get("kind") == "object"
        and item.get("reply_to") == offers[0]["id"]
        and item.get("from") != offers[0]["from"]
    ]
    return {
        "non_proposer_read_late_evidence": bool(reviewer_reads),
        "late_evidence_after_initial_offer": bool(offers and reads)
        and all(item["time_ns"] >= times[offers[0]["id"]] for item in reads),
        "late_evidence_before_final_offer": bool(offers and reviewer_reads)
        and any(item["time_ns"] < times[offers[-1]["id"]] for item in reviewer_reads),
        "initial_offer_challenged": bool(objections),
        "reviewer_read_before_challenge": any(
            item["time_ns"] < times[objection["id"]]
            for item in reviewer_reads
            for objection in objections
            if participants[item["session_id"]] == objection["from"]
        ),
    }
