# Copyright 2026 Adam Gogacz
# SPDX-License-Identifier: Apache-2.0

"""Explicit whole-goal consensus with automatic closing acknowledgments."""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path

from actions import Connection, send
from artifacts import verify
from board import completion_proposal, scan
from delivery import Clock, SystemClock, acknowledge, receive, wait_for_messages
from enrollment import find_run, finish
from messages import Kind, Message
from settings import COMPLETION_SCOPE, ExitCode, require, strict_json
from startup import ancestry


def _offer(connection: Connection) -> tuple[Message, Message | None]:
    """Find the one usable designated completion offer, retaining historical conflicts."""
    settings = connection.settings
    snapshot = scan(settings)
    candidates = []
    for message in snapshot.messages:
        if (
            not completion_proposal(message)
            or message.sender != settings.completion_proposer
            or message.id in snapshot.conflicts
        ):
            continue
        replies = [
            item
            for item in snapshot.messages
            if item.reply_to == message.id
            and item.kind in {Kind.AGREE, Kind.OBJECT, Kind.WITHDRAW}
        ]
        if not replies or (len(replies) == 1 and replies[0].kind == Kind.AGREE):
            candidates.append((message, replies[0] if replies else None))
    require(
        len(candidates) == 1,
        "no single usable completion offer; proposer must create or repair it explicitly",
    )
    return candidates[0]


def _attention(
    connection: Connection, offer: Message, approval: Message | None = None
) -> dict | None:
    """Return unhandled peer information before consent or automatic closing."""
    observed = receive(connection.settings, connection.participant)
    require(
        observed.checkpoint_error is None,
        "recover the invalid checkpoint before closing",
    )
    known = (
        ancestry(offer.causes, observed.snapshot.history)
        | set(offer.causes)
        | {offer.id}
    )
    if approval is not None:
        known.add(approval.id)
    messages = [message for message in observed.unread if message.id not in known]
    if not messages:
        return None
    return {
        "status": "peer-attention",
        "messages": [strict_json(message.raw) for message in messages],
        "consensus_recorded": approval is not None,
        "next_action": "handle and ack these exact messages; then resume complete"
        if approval is not None
        else "handle and ack these messages, reassess the whole goal, then explicitly agree or object",
    }


def complete(
    connection: Connection,
    *,
    propose: bool = False,
    agree: str | None = None,
    text: str | None = None,
    artifacts: Sequence[Path] = (),
    timeout: float | None = None,
    clock: Clock | None = None,
    dry_run: bool = False,
) -> tuple[dict, ExitCode]:
    """Propose or explicitly agree to joint completion, then handle closing mechanically."""
    settings = connection.settings
    require(not (propose and agree is not None), "choose either proposing or agreeing")
    require(
        propose or not artifacts, "attach final artifacts to the completion proposal"
    )
    duration = settings.wait_timeout_seconds if timeout is None else timeout
    require(
        type(duration) in (int, float) and math.isfinite(duration) and duration > 0,
        "complete needs a finite positive command budget",
    )
    clock = clock if clock is not None else SystemClock()
    stop = clock.monotonic() + duration
    sent = None
    if propose:
        require(
            text is not None and bool(text.strip()),
            "state why the whole shared goal is complete",
        )
        sent = send(
            connection,
            Kind.PROPOSE,
            text=text,
            scope=COMPLETION_SCOPE,
            artifacts=artifacts,
            dry_run=dry_run,
        )
    elif agree is not None:
        offer, existing = _offer(connection)
        require(
            offer.id == agree and existing is None,
            "review the current unsettled completion offer before agreeing",
        )
        attention = _attention(connection, offer)
        if attention:
            return {**attention, "dry_run": dry_run}, ExitCode.ATTENTION
        sent = send(
            connection,
            Kind.AGREE,
            reply_to=agree,
            text=text
            or "I explicitly agree that the shared goal is complete at this offer's exact revisions, with its stated checks, limits, and recorded disagreements.",
            dry_run=dry_run,
        )
    if dry_run:
        return sent or {"dry_run": True, "status": "no-consent-recorded"}, ExitCode.OK
    run = find_run(settings.board, settings.goal)
    require(
        any(
            member.participant == connection.participant
            and member.session == connection.session
            for member in run.members
        ),
        "unknown completion connection",
    )
    if run.complete:
        completed_snapshot = scan(settings)
        history = completed_snapshot.history
        approval = history.get(run.agreement)
        require(
            approval is not None
            and approval.kind == Kind.AGREE
            and approval.reply_to in history,
            "recover missing completed agreement history",
        )
        offer = history[approval.reply_to]
        require(
            completion_proposal(offer) and offer.id not in completed_snapshot.conflicts,
            "recover invalid completed consensus history",
        )
        references = list(offer.body.get("artifacts", []))
        for reference in references:
            verify(settings, reference)
        return {
            "status": "complete",
            "goal": run.goal,
            "agreement": run.agreement,
            "artifacts": references,
        }, ExitCode.OK
    offer, approval = _offer(connection)
    if approval is None:
        if connection.participant != settings.completion_proposer:
            return {
                "status": "review-required",
                "proposal": offer.id,
                "message": "Review the whole-goal completion claim and explicitly use complete --agree, or object.",
            }, ExitCode.OK
        remaining = stop - clock.monotonic()
        if remaining <= 0:
            return {
                "status": "awaiting-peer-agreement",
                "proposal": offer.id,
            }, ExitCode.TIMEOUT
        outcome = wait_for_messages(
            settings,
            connection.participant,
            reply_to=offer.id,
            timeout=remaining,
            clock=clock,
        )
        if outcome.code != ExitCode.OK:
            result = outcome.as_json(settings)
            if outcome.code == ExitCode.TIMEOUT:
                result["status"] = "awaiting-peer-agreement"
            return {
                **result,
                "proposal": offer.id,
                "next_action": "address the objection; the proposer must publish a revised completion offer"
                if outcome.code == ExitCode.REJECTED
                else "handle attention or resume complete with this connection",
            }, outcome.code
        offer, approval = _offer(connection)
    require(approval is not None, "completion requires explicit peer agreement")
    for reference in offer.body.get("artifacts", []):
        verify(settings, reference)
    attention = _attention(connection, offer, approval)
    if attention:
        return attention, ExitCode.ATTENTION
    member = next(
        member for member in run.members if member.participant == connection.participant
    )
    if not member.finished:
        acknowledge(
            settings,
            connection.participant,
            connection.session,
            [offer.id, approval.id],
        )
    remaining = stop - clock.monotonic()
    if remaining <= 0:
        return {
            "status": "closing",
            "agreement": approval.id,
            "next_action": "resume complete with this connection",
        }, ExitCode.TIMEOUT
    result, code = finish(
        settings,
        connection.participant,
        connection.session,
        approval.id,
        timeout=remaining,
        clock=clock,
    )
    return {
        **result,
        "artifacts": list(offer.body.get("artifacts", [])),
        "next_action": "stop"
        if code == ExitCode.OK
        else "resume complete with this connection",
    }, code
