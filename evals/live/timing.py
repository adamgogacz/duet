# Copyright 2026 Adam Gogacz
# SPDX-License-Identifier: Apache-2.0

"""Check bounded-discovery timing separately from post-agreement task execution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from observations import task_tool


def work_stopped_at_terminal(
    audits: Sequence[Mapping[str, Any]], terminal_time: int
) -> bool:
    """Allow report handoff reads, but reject new task work after joint closure."""
    for item in audits:
        if item["time_ns"] <= terminal_time or not task_tool(item):
            continue
        parts = Path(str(item.get("file_path") or "")).parts
        if (
            item.get("tool") in {"Read", "Grep"}
            and "output" in parts
            and "inputs" not in parts
        ):
            continue
        return False
    return True


def work_timing_checks(
    audits: Sequence[Mapping[str, Any]],
    gate_time: int | None,
    readiness: Mapping[str, int],
    participants: Mapping[str, str],
    *,
    allow_discovery: bool,
    blocked: bool,
    require_discovery: bool = False,
) -> dict[str, bool]:
    """Allow only read-only discovery after the owning peer's published readiness."""
    task_calls = [item for item in audits if task_tool(item)]
    before = [
        item for item in task_calls if gate_time is None or item["time_ns"] < gate_time
    ]

    def permitted(item: Mapping[str, Any]) -> bool:
        """A discovery exemption is neither execution permission nor peer consent."""
        participant = participants.get(item.get("session_id"))
        return (
            allow_discovery
            and item.get("tool") in {"Read", "Grep", "Glob"}
            and participant in readiness
            and item["time_ns"] >= readiness[participant]
        )

    checks = {
        "observable_task_timing_valid": not task_calls
        if blocked
        else (
            gate_time is not None
            and bool(task_calls)
            and all(permitted(item) for item in before)
        ),
    }
    if allow_discovery:
        checks["discovery_read_only_after_readiness"] = all(
            permitted(item) for item in before
        )
    if require_discovery:
        discovered = {
            participants[item["session_id"]] for item in before if permitted(item)
        }
        checks["both_peers_inspected_before_gate"] = (
            allow_discovery
            and len(set(participants.values())) == 2
            and discovered == set(participants.values())
        )
    return checks
