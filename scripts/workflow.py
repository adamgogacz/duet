# Copyright 2026 Adam Gogacz
# SPDX-License-Identifier: Apache-2.0

"""Derive gates, explicit obligations, and completion freshness from immutable history."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from messages import Kind, Message
from settings import (
    COMPLETION_SCOPE,
    CONFLICT_RESOLUTION_SCOPE,
    INITIAL_PLAN_SCOPE,
    Settings,
    require,
)
from startup import ancestry, opening

if TYPE_CHECKING:
    from board import Snapshot


@dataclass(frozen=True)
class Gate:
    """The exact accepted opening, including its criteria and unsuccessful stop boundary."""

    proposal: Message
    agreement: Message

    @property
    def criteria(self) -> tuple[str, ...]:
        """Preserve criterion ordering for the completion evidence mapping."""
        return tuple(self.proposal.body["criteria"])

    def as_json(self) -> dict[str, Any]:
        """Expose compact terms without inventing satisfaction judgments."""
        return {
            "agreement": self.agreement.id,
            "criteria": list(self.criteria),
            "stop_condition": self.proposal.body["stop_condition"],
        }


def gate(settings: Settings, snapshot: Snapshot) -> Gate:
    """Require explicit bilateral acceptance, not merely a proposed work split."""
    initial = opening(settings, snapshot.history, snapshot.conflicts)
    require(
        initial.agreement is not None and not initial.conflicted,
        "agree on completion criteria and a stop condition before task work",
    )
    return Gate(snapshot.history[initial.agreement.reply_to], initial.agreement)


def is_completion(message: Message) -> bool:
    """Identify a whole-goal closing claim, including an explicitly incomplete stop."""
    return (
        message.kind == Kind.PROPOSE
        and tuple(message.body["scope"]) == COMPLETION_SCOPE
    )


def work_plan(message: Message) -> bool:
    """Identify an initial gate or a proposed amendment to it."""
    return (
        message.kind == Kind.PROPOSE
        and tuple(message.body["scope"]) == INITIAL_PLAN_SCOPE
    )


def responses(
    target: Message, history: Mapping[str, Message], kinds: set[Kind]
) -> list[Message]:
    """Read explicit responses rather than inferring decisions from acknowledgment."""
    return [
        item
        for item in history.values()
        if item.reply_to == target.id and item.kind in kinds
    ]


def reconciled_conflicts(snapshot: Snapshot) -> dict[str, str]:
    """Retire only conflicts explicitly covered by an unambiguous bilateral replacement."""
    candidates: dict[str, list[Message]] = {}
    for agreement in snapshot.messages:
        if agreement.kind != Kind.AGREE or agreement.reply_to in snapshot.conflicts:
            continue
        proposal = snapshot.history[agreement.reply_to]
        if tuple(proposal.body.get("scope", ())) not in {
            CONFLICT_RESOLUTION_SCOPE,
            INITIAL_PLAN_SCOPE,
        }:
            continue
        for target, replies in snapshot.conflicts.items():
            if set(replies) <= set(proposal.causes):
                candidates.setdefault(target, []).append(agreement)
    result = {}
    for target, agreements in candidates.items():
        maximal = [
            item
            for item in agreements
            if not any(
                item.id in ancestry(other.causes, snapshot.history)
                for other in agreements
                if item.id != other.id
            )
        ]
        if len(maximal) == 1:
            result[target] = maximal[0].id
    return result


def commitment_state(accepted: Message, snapshot: Snapshot) -> str:
    """Follow one accepted branch; releasing it never implies criterion satisfaction."""
    history = snapshot.history

    def belongs(item: Message) -> bool:
        """Use response lineage rather than incidental causal links to another branch."""
        while item.reply_to is not None:
            if item.reply_to == accepted.id:
                return True
            item = history[item.reply_to]
        return False

    related = [
        item
        for item in history.values()
        if item.kind in {Kind.SUBMIT, Kind.REJECT, Kind.VERIFY, Kind.RELEASE}
        and belongs(item)
    ]
    releases = responses(accepted, history, {Kind.RELEASE})
    if releases:
        basis = ancestry(releases[0].causes, history) | {releases[0].id}
        return (
            "released"
            if len(releases) == 1 and all(item.id in basis for item in related)
            else "conflicted"
        )
    cursor = accepted
    state = "accepted"
    while True:
        if cursor.id in snapshot.conflicts:
            return "conflicted"
        events = responses(cursor, history, {Kind.SUBMIT, Kind.REJECT, Kind.VERIFY})
        if len(events) != 1:
            return state
        cursor = events[0]
        state = {
            Kind.SUBMIT: "submitted",
            Kind.REJECT: "rejected",
            Kind.VERIFY: "verified",
        }[cursor.kind]
        if cursor.kind == Kind.VERIFY:
            return state


def obligations(snapshot: Snapshot) -> list[dict[str, str | bool]]:
    """Summarize recognized tasks/waits; prose-only requirements remain peer judgments."""
    history = snapshot.history
    reconciled = reconciled_conflicts(snapshot)
    result: list[dict[str, str | bool]] = []
    for root in snapshot.messages:
        if root.kind == Kind.REQUEST:
            state = "requested"
            controls = responses(
                root, history, {Kind.ACCEPT, Kind.DECLINE, Kind.WITHDRAW}
            )
            if root.id in snapshot.conflicts:
                accepts = [item for item in controls if item.kind == Kind.ACCEPT]
                disposed = all(
                    commitment_state(item, snapshot) in {"released", "verified"}
                    for item in accepts
                )
                state = (
                    "reconciled" if root.id in reconciled and disposed else "conflicted"
                )
            elif len(controls) == 1:
                response = controls[0]
                state = (
                    commitment_state(response, snapshot)
                    if response.kind == Kind.ACCEPT
                    else {Kind.DECLINE: "declined", Kind.WITHDRAW: "withdrawn"}[
                        response.kind
                    ]
                )
            result.append(
                {
                    "kind": "task",
                    "id": root.id,
                    "state": state,
                    "blocks_completion": state
                    not in {
                        "verified",
                        "released",
                        "declined",
                        "withdrawn",
                        "reconciled",
                    },
                }
            )
        elif root.kind == Kind.AWAIT:
            controls = responses(root, history, {Kind.RESUME, Kind.WITHDRAW})
            state = "conflicted" if root.id in snapshot.conflicts else "active"
            if root.id in reconciled:
                state = "reconciled"
            elif len(controls) == 1 and state != "conflicted":
                state = "released" if controls[0].kind == Kind.RESUME else "withdrawn"
            result.append(
                {
                    "kind": "wait",
                    "id": root.id,
                    "state": state,
                    "blocks_completion": state in {"active", "conflicted"}
                    and bool(set(root.body["blocks"]) & set(COMPLETION_SCOPE)),
                }
            )
    result.extend(
        {
            "kind": "recorded-effect",
            "id": item["id"],
            "participant": item["participant"],
            "state": item["status"],
            "summary": item["summary"],
            "blocks_completion": item["status"] in {"running", "uncertain", "invalid"},
        }
        for item in snapshot.effects
    )
    return result


def effect_fingerprint(snapshot: Snapshot) -> str:
    """Bind the reported effect context, excluding ordinary processing checkpoints."""
    records = sorted(
        snapshot.effects, key=lambda item: (item["participant"], item["id"])
    )
    return hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()


def completion_blockers(settings: Settings, snapshot: Snapshot) -> list[dict[str, Any]]:
    """Keep unresolved machine-visible work from becoming successful completion."""
    result = [item for item in obligations(snapshot) if item["blocks_completion"]]
    reconciled = reconciled_conflicts(snapshot)
    for name, values in (
        ("invalid", snapshot.invalid),
        ("pending", snapshot.pending),
        ("conflict", set(snapshot.conflicts) - set(reconciled)),
    ):
        result.extend(
            {
                "kind": name,
                "id": key,
                **(
                    {"replies": list(snapshot.conflicts[key])}
                    if name == "conflict"
                    else {}
                ),
            }
            for key in values
        )
    initial = opening(settings, snapshot.history, snapshot.conflicts)
    accepted_basis = (
        ancestry(initial.agreement.causes, snapshot.history)
        if initial.agreement is not None
        else set()
    )
    for message in snapshot.messages:
        if (
            work_plan(message)
            and message.id not in accepted_basis
            and not responses(
                message, snapshot.history, {Kind.AGREE, Kind.OBJECT, Kind.WITHDRAW}
            )
        ):
            result.append({"kind": "gate-amendment", "id": message.id})
    return result


def current_offer(snapshot: Snapshot) -> tuple[Message | None, Message | None]:
    """Select causally maximal offers; never choose a winner by filesystem order."""
    offers = [item for item in snapshot.messages if is_completion(item)]
    maximal = [
        item
        for item in offers
        if not any(
            item.id in ancestry(other.causes, snapshot.history)
            for other in offers
            if item.id != other.id
        )
    ]
    require(
        len(maximal) <= 1,
        "conflicting completion offers require explicit reconciliation",
    )
    if not maximal:
        return None, None
    offer = maximal[0]
    replies = responses(
        offer, snapshot.history, {Kind.AGREE, Kind.OBJECT, Kind.WITHDRAW}
    )
    require(
        offer.id not in snapshot.conflicts,
        "conflicting completion responses require explicit reconciliation",
    )
    approval = next((item for item in replies if item.kind == Kind.AGREE), None)
    return offer, approval


def stale_reasons(
    settings: Settings, snapshot: Snapshot, offer: Message, *, historical: bool = False
) -> list[str]:
    """An acknowledgment cannot erase changed terms or new potentially material context."""
    current = gate(settings, snapshot)
    reasons = []
    if not historical and offer.body["effects"] != effect_fingerprint(snapshot):
        reasons.append("recorded external-effect context changed")
    if offer.body["gate"] != current.agreement.id:
        reasons.append("completion offer refers to an older gate agreement")
    known = ancestry(offer.causes, snapshot.history) | {offer.id}
    for item in snapshot.messages:
        if item.id in known or item.kind == Kind.RECEIPT:
            continue
        if item.reply_to == offer.id and item.kind in {
            Kind.AGREE,
            Kind.OBJECT,
            Kind.WITHDRAW,
        }:
            continue
        reasons.append(f"new context since the offer: {item.id}")
    return reasons


def validate_offer(
    settings: Settings, snapshot: Snapshot, offer: Message, *, historical: bool = False
) -> Gate:
    """Check explicit gate/evidence binding and successful-completion obligations."""
    current = gate(settings, snapshot)
    require(
        offer.body["gate"] == current.agreement.id,
        "completion offer has a stale gate; propose a fresh offer",
    )
    require(
        historical or offer.body["effects"] == effect_fingerprint(snapshot),
        "recorded effect context changed; propose a fresh offer",
    )
    require(
        len(offer.body["evidence"]) == len(current.criteria),
        "provide one evidence statement for each agreed completion criterion",
    )
    require(
        current.agreement.id in ancestry(offer.causes, snapshot.history),
        "completion must causally include its gate agreement",
    )
    if offer.body["outcome"] == "complete":
        blocked = [
            item
            for item in completion_blockers(settings, snapshot)
            if not historical or item["kind"] != "recorded-effect"
        ]
        require(
            not blocked,
            "unresolved completion obligations: "
            + ", ".join(str(item["id"]) for item in blocked),
        )
    return current


def validate_approval(
    settings: Settings,
    snapshot: Snapshot,
    approval: Message,
    *,
    historical: bool = False,
) -> Message:
    """Recheck the same closing basis at acceptance and each provisional finish."""
    offer, current = current_offer(snapshot)
    require(
        offer is not None and current is not None and current.id == approval.id,
        "finish against the current unconflicted completion agreement",
    )
    validate_offer(settings, snapshot, offer, historical=historical)
    require(
        not stale_reasons(settings, snapshot, offer, historical=historical),
        "completion context changed; review and agree a fresh offer",
    )
    return offer


def check_completion(message: Message, settings: Settings, snapshot: Snapshot) -> None:
    """Guard one current offer while allowing explicit reassessment before terminal close."""
    history = snapshot.history
    if (
        message.kind == Kind.PROPOSE
        and tuple(message.body["scope"]) == CONFLICT_RESOLUTION_SCOPE
    ):
        require(
            any(
                set(replies) <= set(message.causes)
                for replies in snapshot.conflicts.values()
            ),
            "a reconciliation must explicitly cite every reply of at least one conflict",
        )
    if is_completion(message):
        require(
            message.sender == settings.completion_proposer,
            "only the designated completion proposer may propose goal completion",
        )
        validate_offer(settings, snapshot, message)
        old, approval = current_offer(snapshot)
        if old is not None:
            settled = responses(old, history, {Kind.OBJECT, Kind.WITHDRAW})
            require(
                bool(settled) or bool(stale_reasons(settings, snapshot, old)),
                "completion is already agreed"
                if approval
                else "a completion proposal is already pending; reuse or revise it",
            )
            require(
                old.id in ancestry(message.causes, history),
                "a revised offer must cite the previous completion context",
            )
    elif (
        message.kind in {Kind.AGREE, Kind.OBJECT, Kind.WITHDRAW}
        and message.reply_to is not None
    ):
        target = history[message.reply_to]
        if not is_completion(target):
            return
        require(
            not responses(target, history, {Kind.AGREE, Kind.OBJECT, Kind.WITHDRAW}),
            "completion proposal is already settled; use an alert for new information after agreement",
        )
        if message.kind == Kind.AGREE:
            validate_offer(settings, snapshot, target)
            require(
                not stale_reasons(settings, snapshot, target),
                "completion context changed; review a fresh offer before agreeing",
            )
