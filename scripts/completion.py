# Copyright 2026 Adam Gogacz
# SPDX-License-Identifier: Apache-2.0

"""Gate-bound whole-goal consensus with provisional bilateral closing."""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from actions import Connection, send
from artifacts import capture, verify
from board import scan
from delivery import Clock, SystemClock, acknowledge, receive, wait_for_messages
from enrollment import find_run, finish, terminal_result
from messages import Kind, Message
from planning import gate_review
from settings import COMPLETION_SCOPE, DuetError, ExitCode, require, strict_json
from startup import ancestry, opening
from workflow import (
    completion_blockers,
    current_offer,
    gate,
    obligations,
    reconciled_conflicts,
    responses,
    stale_reasons,
    validate_approval,
)


def _fresh(connection: Connection, offer: Message) -> list[str]:
    """Read-only freshness assessment; acknowledging updates never restores old consent."""
    snapshot = scan(connection.settings)
    reasons = stale_reasons(connection.settings, snapshot, offer)
    try:
        for reference in offer.body.get("artifacts", []):
            verify(connection.settings, reference, current_origin=True)
    except (DuetError, OSError) as error:
        reasons.append(str(error))
    return reasons


def _attention(connection: Connection, known: set[str]) -> dict[str, Any] | None:
    """Surface exact unhandled content without treating read or receipt as approval."""
    observed = receive(connection.settings, connection.participant)
    require(
        observed.checkpoint_error is None,
        "repair the invalid checkpoint before completion",
    )
    messages = [
        message
        for message in observed.unread
        if message.id not in known and message.kind != Kind.RECEIPT
    ]
    if not messages:
        return None
    return {
        "status": "peer-attention",
        "messages": [strict_json(message.raw) for message in messages],
        "consensus_recorded": False,
        "next_action": "assess and acknowledge these messages before making a completion decision",
    }


def state(connection: Connection) -> dict[str, Any]:
    """Describe recognized obligations and valid next actions, not requirements hidden in prose."""
    settings = connection.settings
    run = find_run(settings.board, settings.goal)
    snapshot = scan(settings)
    if run.terminal:
        approval = snapshot.history.get(run.agreement)
        require(approval is not None, "terminal agreement history is unavailable")
        offer = validate_approval(settings, snapshot, approval, historical=True)
        require(
            run.outcome == offer.body["outcome"],
            "terminal outcome differs from its approval",
        )
        for reference in offer.body.get("artifacts", []):
            verify(settings, reference)
        return {
            **terminal_result(run),
            "user_request": run.task,
            "artifacts": list(offer.body.get("artifacts", [])),
            "next_actions": ["report the outcome and stop this collaboration"],
        }
    result: dict[str, Any] = {
        "goal": settings.goal,
        "user_request": run.task,
        "terminal": False,
        "complete": False,
        "recognized_obligations": obligations(snapshot),
        "reconciled_conflicts": reconciled_conflicts(snapshot),
        "unread_count": len(receive(settings, connection.participant).unread),
    }
    planning = gate_review(settings, snapshot)
    if planning is not None:
        result["gate_review"] = planning
    try:
        current = gate(settings, snapshot)
    except DuetError:
        initial = opening(settings, snapshot.history, snapshot.conflicts)
        conflicted = initial.conflicted or any(
            snapshot.history[key].kind == Kind.PROPOSE
            and snapshot.history[key].body.get("scope") == ["work-division"]
            for key in snapshot.conflicts
        )
        return {
            **result,
            "status": "gate-conflict" if conflicted else "awaiting-gate",
            "next_actions": [
                "propose replacement gate terms citing all competing evidence; obtain fresh peer agreement"
                if conflicted
                else "agree completion criteria and a stop condition before task work"
            ],
        }
    result["gate"] = current.as_json()
    result["blockers"] = completion_blockers(settings, snapshot)
    offer, approval = current_offer(snapshot)
    if offer is None:
        return {
            **result,
            "status": "working",
            "next_actions": [
                "resolve obligations and evidence the gate",
                "proposer offers completion or an explicitly incomplete stop",
            ],
        }
    reasons = _fresh(connection, offer)
    settled = responses(offer, snapshot.history, {Kind.OBJECT, Kind.WITHDRAW})
    result.update(
        {
            "proposal": offer.id,
            "approval": approval.id if approval else None,
            "outcome": offer.body["outcome"],
            "stale_reasons": reasons,
        }
    )
    if reasons or settled:
        basis = ancestry(offer.causes, snapshot.history) | {offer.id}
        result["messages"] = [
            strict_json(item.raw)
            for item in snapshot.messages
            if item.id not in basis
            and item.kind != Kind.RECEIPT
            and not (
                item.reply_to == offer.id
                and item.kind in {Kind.AGREE, Kind.OBJECT, Kind.WITHDRAW}
            )
        ]
        result["consensus_recorded"] = approval is not None
        result["consensus_current"] = False
        return {
            **result,
            "status": "revision-required",
            "next_actions": [
                "assess changed information",
                "proposer issues a fresh reviewed offer with current evidence",
            ],
        }
    if approval is None:
        return {
            **result,
            "status": "awaiting-peer-agreement"
            if connection.participant == settings.completion_proposer
            else "review-required",
            "next_actions": ["wait for the peer or use complete --revise"]
            if connection.participant == settings.completion_proposer
            else [
                "review captured bytes and all host-goal requirements; agree only when ready to exit, otherwise request changes"
            ],
        }
    return {
        **result,
        "status": "closing",
        "next_actions": [
            "continue complete; closing acknowledgments remain provisional"
        ],
    }


def _exit_statement(outcome: str, text: str | None, *, agreeing: bool = False) -> str:
    """Record explicit exit readiness without pretending to inspect the host's goal."""
    action = "I explicitly agree to" if agreeing else "I propose"
    if outcome == "complete":
        statement = (
            f"{action} successful whole-goal completion at these exact revisions. "
            "All mandatory task work is satisfied, including my host-goal requirements; "
            "no required action, approval or cleanup remains pending. "
            "I am ready to exit after bilateral closing."
        )
    else:
        stop = "stop" if agreeing else "stopping"
        statement = (
            f"{action} {stop} this collaboration incomplete; unmet requirements remain unmet. "
            "I am ready to hand off this outcome after bilateral closing, "
            "without claiming host-goal success."
        )
    return f"{statement}\n\n{text}" if text else statement


def complete(
    connection: Connection,
    *,
    propose: bool = False,
    revise: bool = False,
    agree: str | None = None,
    request_changes: str | None = None,
    text: str | None = None,
    artifacts: Sequence[Path] = (),
    evidence: Sequence[str] | None = None,
    outcome: str = "complete",
    timeout: float | None = None,
    clock: Clock | None = None,
    dry_run: bool = False,
) -> tuple[dict[str, Any], ExitCode]:
    """Keep judgments explicit; journal corrections and revalidate before each finish."""
    settings = connection.settings
    require(
        sum((propose, revise, agree is not None, request_changes is not None)) <= 1,
        "choose one completion action",
    )
    require(
        (propose or revise)
        or (not artifacts and evidence is None and outcome == "complete"),
        "artifacts, evidence, and outcome belong to a completion proposal",
    )
    duration = settings.wait_timeout_seconds if timeout is None else timeout
    require(
        type(duration) in (int, float) and math.isfinite(duration) and duration > 0,
        "complete needs a finite positive command budget",
    )
    clock = clock if clock is not None else SystemClock()
    stop = clock.monotonic() + duration
    status = state(connection)
    if status["terminal"]:
        require(
            not (propose or revise or agree or request_changes),
            "collaboration is already terminal; do not publish new decisions",
        )
        return status, ExitCode.OK
    require(
        status["status"] != "awaiting-gate",
        "agree completion criteria and a stop condition before completion",
    )
    snapshot = scan(settings)
    current = gate(settings, snapshot)
    offer, approval = current_offer(snapshot)
    sent = None
    if request_changes is not None:
        require(
            offer is not None and offer.id == request_changes,
            "request changes to the current completion offer",
        )
        require(
            connection.participant != offer.sender,
            "the proposer uses complete --revise for its own offer",
        )
        require(
            text is not None and bool(text.strip()),
            "state the correction or new evidence",
        )
        if approval is None and not responses(
            offer, snapshot.history, {Kind.OBJECT, Kind.WITHDRAW}
        ):
            sent = send(
                connection, Kind.OBJECT, reply_to=offer.id, text=text, dry_run=dry_run
            )
        else:
            sent = send(
                connection,
                Kind.ALERT,
                text=text,
                scope=COMPLETION_SCOPE,
                causes=[offer.id],
                dry_run=dry_run,
            )
        return {
            "status": "changes-requested",
            "message": sent,
            "dry_run": dry_run,
        }, ExitCode.OK
    if propose or revise:
        require(
            connection.participant == settings.completion_proposer,
            "only the designated completion proposer may propose goal completion",
        )
        require(text is not None and bool(text.strip()), "state the whole-goal outcome")
        require(
            evidence is not None and len(evidence) == len(current.criteria),
            "provide one evidence statement for each agreed completion criterion",
        )
        require(
            isinstance(outcome, str) and outcome in {"complete", "incomplete"},
            "outcome must be complete or incomplete",
        )
        require(
            all(isinstance(item, str) and bool(item.strip()) for item in evidence),
            "criterion evidence must contain meaningful text",
        )
        if outcome == "complete" and status["blockers"]:
            return {
                **status,
                "status": "blocked",
                "next_actions": [
                    "resolve the listed obligations; limitations do not satisfy mandatory requirements"
                ],
            }, ExitCode.ATTENTION
        known = ancestry(current.agreement.causes, snapshot.history) | {
            current.agreement.id
        }
        if offer is not None:
            known |= ancestry(offer.causes, snapshot.history) | {offer.id}
            known.update(
                item.id
                for item in responses(
                    offer, snapshot.history, {Kind.AGREE, Kind.OBJECT, Kind.WITHDRAW}
                )
            )
        attention = _attention(connection, known)
        if attention:
            return attention, ExitCode.ATTENTION
        if revise:
            # Validate new inputs before invalidating the previous claim.
            for path in artifacts:
                capture(settings, path, dry_run=True)
        if (
            revise
            and offer is not None
            and not responses(offer, snapshot.history, {Kind.OBJECT, Kind.WITHDRAW})
        ):
            if approval is None:
                sent = send(
                    connection,
                    Kind.WITHDRAW,
                    reply_to=offer.id,
                    text="Revising the final claim; a fresh exact revision requires review",
                    dry_run=dry_run,
                )
            elif not stale_reasons(settings, snapshot, offer):
                sent = send(
                    connection,
                    Kind.ALERT,
                    text="Reassessing the final claim before terminal closing",
                    scope=COMPLETION_SCOPE,
                    causes=[offer.id, approval.id],
                    dry_run=dry_run,
                )
            if dry_run:
                return {
                    "status": "would-revise",
                    "first_action": sent,
                    "dry_run": True,
                }, ExitCode.OK
        sent = send(
            connection,
            Kind.PROPOSE,
            text=_exit_statement(outcome, text),
            scope=COMPLETION_SCOPE,
            artifacts=artifacts,
            evidence=evidence,
            outcome=outcome,
            dry_run=dry_run,
        )
    elif agree is not None:
        require(
            offer is not None and offer.id == agree and approval is None,
            "review the current unsettled offer before agreeing",
        )
        # An edited origin is an error, not an invitation to approve the old bytes.
        for reference in offer.body.get("artifacts", []):
            verify(settings, reference, current_origin=True)
        if status["status"] == "revision-required":
            return status, ExitCode.ATTENTION
        attention = _attention(
            connection,
            ancestry(current.agreement.causes, snapshot.history)
            | {current.agreement.id, offer.id},
        )
        if attention:
            return attention, ExitCode.ATTENTION
        sent = send(
            connection,
            Kind.AGREE,
            reply_to=agree,
            text=_exit_statement(offer.body["outcome"], text, agreeing=True),
            dry_run=dry_run,
        )
    if dry_run:
        return sent or {"dry_run": True, "status": "no-consent-recorded"}, ExitCode.OK
    status = state(connection)
    if status["status"] in {"working", "revision-required"}:
        return status, ExitCode.ATTENTION if status[
            "status"
        ] == "revision-required" else ExitCode.OK
    if status["status"] == "review-required":
        return status, ExitCode.OK
    if status["status"] == "awaiting-peer-agreement":
        remaining = stop - clock.monotonic()
        if remaining <= 0:
            return status, ExitCode.TIMEOUT
        waiting = wait_for_messages(
            settings,
            connection.participant,
            reply_to=status["proposal"],
            timeout=remaining,
            clock=clock,
        )
        status = state(connection)
        if status["status"] != "closing":
            return {
                **status,
                "messages": waiting.as_json(settings)["messages"],
            }, waiting.code if status[
                "status"
            ] != "revision-required" else ExitCode.ATTENTION
    snapshot = scan(settings)
    offer, approval = current_offer(snapshot)
    require(
        offer is not None and approval is not None,
        "completion requires explicit peer agreement",
    )
    validate_approval(settings, snapshot, approval)
    try:
        acknowledge(
            settings,
            connection.participant,
            connection.session,
            [offer.id, approval.id],
        )
    except DuetError:
        # A peer may commit the terminal cutoff after our preceding observation.
        # Preserve unrelated errors; only an independently validated terminal result wins.
        if not find_run(settings.board, settings.goal).terminal:
            raise
        return state(connection), ExitCode.OK
    remaining = stop - clock.monotonic()
    if remaining <= 0:
        return {**state(connection), "status": "closing"}, ExitCode.TIMEOUT
    result, code = finish(
        settings,
        connection.participant,
        connection.session,
        approval.id,
        timeout=remaining,
        clock=clock,
    )
    if result.get("terminal"):
        # An older suspended waiter may observe a newer, fully signed closing basis.
        # Reload the actual terminal offer instead of attaching its old artifacts.
        return state(connection), ExitCode.OK
    return {**result, "artifacts": list(offer.body.get("artifacts", []))}, code
