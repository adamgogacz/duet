# Copyright 2026 Adam Gogacz
# SPDX-License-Identifier: Apache-2.0

"""Describe gate changes from immutable messages without judging user requirements."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from difflib import unified_diff
from typing import TYPE_CHECKING, Any, TypedDict

from messages import Kind, Message
from settings import INITIAL_PLAN_SCOPE, Settings
from startup import ancestry, opening

if TYPE_CHECKING:
    from board import Snapshot


class GateDelta(TypedDict):
    """Exact textual changes; removed or reworded criteria are never implicit waivers."""

    base_proposal: str | None
    criteria_added: list[str]
    criteria_removed: list[str]
    criteria_reordered: bool
    stop_condition_diff: list[str]
    plan_diff: list[str]


def _difference(left: Sequence[str], right: Sequence[str]) -> list[str]:
    """Preserve ordering and duplicates when listing newly added or removed criteria."""
    remaining = Counter(right)
    result = []
    for item in left:
        if remaining[item]:
            remaining[item] -= 1
        else:
            result.append(item)
    return result


def _text_diff(before: str, after: str) -> list[str]:
    """Keep unchanged lines out of an exact, explicitly labeled comparison."""
    return list(
        unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile="base",
            tofile="proposed",
            n=0,
            lineterm="",
        )
    )


def compare_gate(base: Message | None, proposed: Message) -> GateDelta:
    """Compare criteria, stopping bounds and plan text without inferring their meaning."""
    before = base.body if base is not None else {}
    old = list(before.get("criteria", []))
    new = list(proposed.body["criteria"])
    return {
        "base_proposal": base.id if base else None,
        "criteria_added": _difference(new, old),
        "criteria_removed": _difference(old, new),
        "criteria_reordered": Counter(old) == Counter(new) and old != new,
        "stop_condition_diff": _text_diff(
            before.get("stop_condition", ""), proposed.body["stop_condition"]
        ),
        "plan_diff": _text_diff(before.get("proposal", ""), proposed.body["proposal"]),
    }


def _maximal(
    messages: Sequence[Message], history: Mapping[str, Message]
) -> list[Message]:
    """Retain competing causal alternatives instead of selecting by arrival order."""
    return [
        item
        for item in messages
        if not any(
            item.id in ancestry(other.causes, history)
            for other in messages
            if item.id != other.id
        )
    ]


def gate_review(settings: Settings, snapshot: Snapshot) -> dict[str, Any] | None:
    """Expose pending changes, their actual bases, and crossed accepted alternatives."""
    history = snapshot.history
    plans = [
        item
        for item in snapshot.messages
        if item.kind == Kind.PROPOSE and tuple(item.body["scope"]) == INITIAL_PLAN_SCOPE
    ]
    pending = [
        item
        for item in plans
        if not any(
            reply.reply_to == item.id
            and reply.kind in {Kind.AGREE, Kind.OBJECT, Kind.WITHDRAW}
            for reply in snapshot.messages
        )
    ]
    initial = opening(settings, history, snapshot.conflicts)
    conflicts = {
        item.id: list(snapshot.conflicts[item.id])
        for item in plans
        if item.id in snapshot.conflicts
    }
    if not pending and not initial.conflicted and not conflicts:
        return None
    current = initial.agreement
    current_plan = history[current.reply_to] if current is not None else None
    changes = []
    for proposal in pending:
        prior = ancestry(proposal.causes, history)
        bases = _maximal([item for item in plans if item.id in prior], history)
        entry = {
            "proposal": proposal.id,
            "proposer": proposal.sender,
            "base_proposals": [item.id for item in bases],
            "based_on_current_gate": current is not None and current.id in prior,
            "changes_from_bases": [compare_gate(base, proposal) for base in bases]
            if bases
            else [compare_gate(None, proposal)],
        }
        if current_plan is not None and (
            not entry["based_on_current_gate"]
            or {base.id for base in bases} != {current_plan.id}
        ):
            entry["changes_from_current_gate"] = compare_gate(current_plan, proposal)
        changes.append(entry)
    agreements = _maximal(
        [
            item
            for item in snapshot.messages
            if item.kind == Kind.AGREE
            and item.reply_to not in snapshot.conflicts
            and tuple(history[item.reply_to].body.get("scope", ()))
            == INITIAL_PLAN_SCOPE
        ],
        history,
    )
    return {
        "pending": changes,
        "competing_agreements": [item.id for item in agreements]
        if initial.conflicted
        else [],
        "conflicting_replies": conflicts,
        "review_note": "Compare changes against user_request and applicable governing instructions. Peer agreement cannot waive user requirements or erase an incident; text changes require semantic review.",
    }
