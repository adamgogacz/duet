# Copyright 2026 Adam Gogacz
# SPDX-License-Identifier: Apache-2.0

"""Derive the opening work-division exchange without relying on arrival order."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from messages import Kind, Message
from settings import INITIAL_PLAN_SCOPE, Settings


def ancestry(causes: tuple[str, ...], history: Mapping[str, Message]) -> set[str]:
    """Return transitive causal IDs from an already validated history."""
    found: set[str] = set()
    pending = list(causes)
    while pending:
        item = pending.pop()
        if item not in found:
            found.add(item)
            pending.extend(history[item].causes)
    return found


def causal_frontier(history: Mapping[str, Message]) -> set[str]:
    """Pin all history through its maximal events, preserving independent branches."""
    covered = {key for message in history.values() for key in message.causes}
    return set(history) - covered


@dataclass(frozen=True)
class Opening:
    """Structural startup facts; plan content still requires participant review."""

    proposer: str
    decision: str
    both_ready: bool
    proposal: Message | None
    agreement: Message | None
    conflicted: bool

    def as_json(self) -> dict[str, Any]:
        """Expose the next startup step without assigning permanent authority."""
        if self.conflicted:
            phase = "conflict"
        elif not self.both_ready:
            phase = "awaiting-readiness"
        elif self.proposal is None:
            phase = "awaiting-initial-proposal"
        elif self.agreement is None:
            phase = "awaiting-plan-agreement"
        else:
            phase = "agreed"
        return {
            "initial_proposer": self.proposer,
            "decision": self.decision,
            "phase": phase,
            "proposal": self.proposal.id if self.proposal else None,
            "agreement": self.agreement.id if self.agreement else None,
        }


def opening(
    settings: Settings,
    history: Mapping[str, Message],
    conflicts: Mapping[str, tuple[str, ...]],
) -> Opening:
    """Recognize a root plan or agreed counterproposal under the same planning scope."""
    ready = {
        message.sender
        for message in history.values()
        if message.kind == Kind.READY
        and message.body["context_revision"] == settings.context_revision
    }
    roots = [
        message
        for message in history.values()
        if message.kind == Kind.PROPOSE
        and message.subject_type == "decision"
        and message.subject_id == settings.initial_plan_id
    ]
    root = roots[0] if len(roots) == 1 else None
    invalid = len(roots) > 1
    if root is not None:
        predecessors = ancestry(root.causes, history)
        prior_ready = {
            history[key].sender
            for key in predecessors
            if history[key].kind == Kind.READY
            and history[key].body["context_revision"] == settings.context_revision
            and (
                history[key].sender != root.sender
                or history[key].session == root.session
            )
        }
        invalid |= (
            root.sender != settings.initial_proposer
            or tuple(root.body["scope"]) != INITIAL_PLAN_SCOPE
            or prior_ready != set(settings.participants)
        )
    agreements: list[Message] = []
    if root is not None and not invalid:
        for message in history.values():
            if message.kind != Kind.AGREE or message.reply_to in conflicts:
                continue
            proposal = history[message.reply_to]
            if tuple(proposal.body["scope"]) == INITIAL_PLAN_SCOPE and (
                proposal.id == root.id or root.id in ancestry(proposal.causes, history)
            ):
                agreements.append(message)
    # Competing accepted alternatives require explicit reconciliation, not a winner.
    accepted = {message.reply_to for message in agreements}
    maximal = [
        message
        for message in agreements
        if not any(
            message.id in ancestry(other.causes, history)
            for other in agreements
            if other.id != message.id
        )
    ]
    invalid |= len(accepted) > 1 and len(maximal) > 1
    return Opening(
        settings.initial_proposer,
        settings.initial_plan_id,
        ready == set(settings.participants),
        root,
        maximal[0] if len(maximal) == 1 and not invalid else None,
        invalid,
    )
