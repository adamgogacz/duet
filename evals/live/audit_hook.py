# Copyright 2026 Adam Gogacz
# SPDX-License-Identifier: Apache-2.0

"""Observe native tool starts without approving, denying, or steering agent actions."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

from observations import input_digest


def append(destination: Path, name: str, record: dict) -> None:
    """Append one observation or diagnostic without affecting the observed tool."""
    destination.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        destination / name, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600
    )
    with os.fdopen(descriptor, "a") as stream:
        stream.write(json.dumps(record) + "\n")


def main() -> None:
    """Write per-session observations; no stdout means no change to tool behavior."""
    destination = Path(sys.argv[1])
    limit = int(sys.argv[2])
    raw = sys.stdin.buffer.read(limit + 1)
    if len(raw) > limit:
        append(
            destination,
            "errors.jsonl",
            {"time_ns": time.time_ns(), "error": "hook input exceeds audit bound"},
        )
        return
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or not isinstance(data.get("tool_input"), dict):
            raise TypeError("invalid hook input")
    except (ValueError, TypeError):
        append(
            destination,
            "errors.jsonl",
            {"time_ns": time.time_ns(), "error": "invalid hook input"},
        )
        return
    session = data.get("session_id", "")
    if (
        not isinstance(session, str)
        or re.fullmatch(r"[A-Za-z0-9_-]+", session) is None
        or not data.get("tool_use_id")
    ):
        append(
            destination,
            "errors.jsonl",
            {"time_ns": time.time_ns(), "error": "missing hook identity"},
        )
        return
    tool_input = data.get("tool_input", {})
    record = {
        "time_ns": time.time_ns(),
        "session_id": session,
        "agent_id": data.get("agent_id"),
        "agent_type": data.get("agent_type"),
        "tool_use_id": data["tool_use_id"],
        "tool": data.get("tool_name"),
        "cwd": data.get("cwd"),
        "input_digest": input_digest(tool_input, data.get("cwd")),
        "file_path": tool_input.get("file_path", tool_input.get("path")),
        "command": tool_input.get("command"),
        "pattern": tool_input.get("pattern"),
    }
    append(destination, f"{session}.jsonl", record)


if __name__ == "__main__":
    main()
