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
    parent: str | None = None


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
                    row.get("parent_tool_use_id"),
                )
                if (
                    not call.session
                    or not call.id
                    or not isinstance(call.arguments, dict)
                    or (call.parent is not None and not isinstance(call.parent, str))
                ):
                    raise ValueError("Native tool call lacks its identity or input")
                key = (call.session, call.id)
                if key in calls and calls[key] != call:
                    raise ValueError("Native tool call changed under an existing ID")
                calls[key] = call
    return tuple(calls.values())


def helper_lineage(
    calls: Sequence[NativeCall], audits: Sequence[dict[str, Any]], helper: str
) -> bool:
    """Bind each observed helper to its root launch, type, and restricted tool pool."""
    by_id = {(call.session, call.id): call for call in calls}
    observed = {
        (item.get("session_id"), item.get("tool_use_id")): item for item in audits
    }
    identities: dict[tuple[str, str], str] = {}
    for call in calls:
        audit = observed.get((call.session, call.id), {})
        if call.parent is None:
            if audit.get("agent_id"):
                return False
            if call.tool == "Agent" and call.arguments.get("subagent_type") != helper:
                return False
            continue
        launcher = by_id.get((call.session, call.parent))
        identity = audit.get("agent_id")
        if (
            launcher is None
            or launcher.parent is not None
            or launcher.tool != "Agent"
            or launcher.arguments.get("subagent_type") != helper
            or call.tool not in {"Read", "Grep", "Glob"}
            or not isinstance(identity, str)
            or not identity
            or audit.get("agent_type") != helper
        ):
            return False
        key = (call.session, call.parent)
        if key in identities and identities[key] != identity:
            return False
        identities[key] = identity
    return True


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
            or not isinstance(item.get("cwd"), str)
            or item.get("input_digest") != input_digest(call.arguments, item.get("cwd"))
            or type(item.get("time_ns")) is not int
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
    """Track task content, execution and artifacts separately from filename lookup."""
    if record.get("tool") == "Glob":
        return False
    path = str(record.get("file_path") or "")
    parts = Path(path).parts
    if set(parts) & {"inputs", "output"} or Path(path).name == "case_checks.py":
        return True
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


def file_tools_scoped(
    audits: Sequence[Mapping[str, Any]], workspace: Path, temporary: Path
) -> bool:
    """Check explicit native file operations against the evaluation reading/writing areas."""
    roots = (workspace.resolve(), temporary.resolve())
    for item in audits:
        tool = item.get("tool")
        if tool not in {"Read", "Grep", "Glob", "Write", "Edit"}:
            continue
        raw = item.get("file_path") or "."
        if not isinstance(raw, str):
            return False
        if tool == "Glob":
            pattern = item.get("pattern")
            if not isinstance(pattern, str) or ".." in Path(pattern).parts:
                return False
            raw = os.path.join(raw, pattern)
        try:
            target = (Path(item.get("cwd") or workspace) / raw).resolve()
        except (TypeError, ValueError, OSError, RuntimeError):
            return False
        if tool in {"Write", "Edit"}:
            if not target.is_relative_to(roots[0] / "output"):
                return False
        elif not any(target.is_relative_to(root) for root in roots):
            return False
    return True


def validated_joins(
    streams: Sequence[Sequence[dict[str, Any]]],
    calls: Sequence[NativeCall],
    members: Iterable[Mapping[str, Any]],
    script: Path,
) -> tuple[tuple[str, Mapping[str, Any]], ...]:
    """Return root join results whose actor credentials match actual enrollment."""
    joins = {
        (call.session, call.id): call
        for call in calls
        if call.tool == "Bash"
        and call.parent is None
        and script_command(str(call.arguments.get("command", "")))
        == (str(script), "join")
    }
    enrolled = {(member["participant"], member["session"]) for member in members}
    results: dict[tuple[str, str], Mapping[str, Any]] = {}
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
                    results[key] = result
    return tuple((session, result) for (session, _), result in results.items())


def joined_participants(
    streams: Sequence[Sequence[dict[str, Any]]],
    calls: Sequence[NativeCall],
    members: Iterable[Mapping[str, Any]],
    script: Path,
) -> dict[str, str]:
    """Bind native sessions through successful join results checked against enrollment."""
    participants: dict[str, set[tuple[str, str]]] = {}
    for session, result in validated_joins(streams, calls, members, script):
        participants.setdefault(session, set()).add(
            (result["participant"], result["session"])
        )
    return {
        session: next(iter(actors))[0]
        for session, actors in participants.items()
        if len(actors) == 1
    }


def helper_credentials_isolated(
    streams: Sequence[Sequence[dict[str, Any]]],
    calls: Sequence[NativeCall],
    members: Sequence[Mapping[str, Any]],
    script: Path,
    workspace: Path,
) -> bool:
    """Keep enrolled actor capabilities out of helper prompts and file-tool scope."""
    capabilities = {
        member["session"]
        for member in members
        if isinstance(member.get("session"), str)
    }
    for _, result in validated_joins(streams, calls, members, script):
        capabilities.update(
            result[field]
            for field in ("connection", "connection_path")
            if isinstance(result.get(field), str) and result[field]
        )
    for call in calls:
        if call.tool == "Agent":
            arguments = json.dumps(call.arguments, ensure_ascii=False)
            if any(
                capability and capability in arguments for capability in capabilities
            ):
                return False
        if call.parent is not None:
            raw = call.arguments.get("file_path", call.arguments.get("path", ""))
            if isinstance(raw, str):
                try:
                    resolved = (workspace / raw).resolve()
                except (OSError, ValueError, RuntimeError):
                    return False
                pattern = str(call.arguments.get("pattern") or "")
                if (
                    "connections" in Path(raw).parts
                    or "connections" in resolved.parts
                    or "connections" in Path(pattern).parts
                ):
                    return False
    return True


def helper_task_path(call: NativeCall, workspace: Path) -> bool:
    """Allow fixture inputs, supplied measurement source and named root instructions."""
    root = workspace.resolve()
    raw = call.arguments.get("file_path", call.arguments.get("path"))
    if raw is not None and not isinstance(raw, str):
        return False
    if call.tool == "Glob":
        pattern = call.arguments.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            return False
        raw = os.path.join(raw or ".", pattern)
    if not isinstance(raw, str) or not raw or "connections" in Path(raw).parts:
        return False
    try:
        target = (root / raw).resolve()
    except (ValueError, OSError):
        return False
    return target.is_relative_to(root / "inputs") or target in {
        root / "TASK.md",
        root / "AGENTS.md",
        root / "CLAUDE.md",
        root / "case_checks.py",
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
