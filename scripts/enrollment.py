# Copyright 2026 Adam Gogacz
# SPDX-License-Identifier: Apache-2.0

"""Pair fresh CLI conversations and keep membership/completion local to each board."""

from __future__ import annotations

import math
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, nullcontext
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from board import atomic_write, directory, read_record, scan
from configure import configure, project_board
from delivery import Clock, SystemClock
from locking import file_lock
from messages import Kind
from pairing import Invitation, Lobby, append_event, read_lobby
from settings import (
    AUTO_PARTICIPANTS,
    COMPLETION_SCOPE,
    SESSION_LOCK,
    PROJECT_LOCK,
    PROJECT_REGISTRY,
    ExitCode,
    Settings,
    encoded,
    identifier,
    nonempty,
    require,
    strict_json,
)


@dataclass(frozen=True)
class Member:
    """One provisioned participant identity; credentials are never transferred."""

    participant: str
    session: str
    finished: bool = False


@dataclass(frozen=True)
class Run:
    """One collaboration's members and bilateral closing acknowledgments."""

    goal: str
    workspace: str
    task: str
    settings_path: str
    members: tuple[Member, ...]
    agreement: str | None = None

    @property
    def complete(self) -> bool:
        """Both peers must observe the same explicit completion agreement."""
        return all(member.finished for member in self.members)

    @classmethod
    def load(cls, path: Path) -> Run:
        """Validate local session state instead of searching other conversations."""
        data = strict_json(read_record(path, Settings.default("max_checkpoint_bytes")))
        require(set(data) == set(cls.__dataclass_fields__), "invalid enrollment fields")
        identifier(data["goal"], "enrollment.goal")
        nonempty(data["task"], "task")
        for name in ("workspace", "settings_path"):
            require(
                Path(nonempty(data[name], name)).is_absolute(),
                f"{name} must be absolute",
            )
        require(
            isinstance(data["members"], list)
            and len(data["members"]) == len(AUTO_PARTICIPANTS),
            "invalid member count",
        )
        members = []
        for member in data["members"]:
            require(
                isinstance(member, dict)
                and set(member) == set(Member.__dataclass_fields__),
                "invalid member fields",
            )
            require(
                member["participant"] in AUTO_PARTICIPANTS
                and type(member["finished"]) is bool,
                "invalid participant slot",
            )
            identifier(member["session"], "session")
            members.append(Member(**member))
        require(
            {member.participant for member in members} == set(AUTO_PARTICIPANTS)
            and len({member.session for member in members}) == len(members),
            "duplicate enrollment identity",
        )
        if data["agreement"] is not None:
            identifier(data["agreement"], "agreement")
        require(
            not any(member.finished for member in members)
            or data["agreement"] is not None,
            "finished enrollment lacks completion evidence",
        )
        return cls(
            data["goal"],
            data["workspace"],
            data["task"],
            data["settings_path"],
            tuple(members),
            data["agreement"],
        )


def membership_paths(board: Path, *, create: bool = False) -> tuple[Path, Path]:
    """Keep membership separate from the workspace's invitation registry."""
    root = directory(board, ("enrollment",), create=create)
    return root / "run.json", root


@contextmanager
def session_lock(board: Path, clock: Clock) -> Iterator[None]:
    """Serialize short completion mutations for this collaboration only."""
    _, root = membership_paths(board, create=True)
    with file_lock(root / SESSION_LOCK, clock):
        yield


def write_run(path: Path, run: Run, *, replace_existing: bool) -> None:
    """Save bounded membership/closing state atomically."""
    raw = encoded(asdict(run))
    require(
        len(raw) <= Settings.default("max_checkpoint_bytes"),
        "task context exceeds enrollment record limit",
    )
    atomic_write(path, raw, replace=replace_existing)


def find_run(board: Path, goal: str) -> Run:
    """Read only this board; other paired collaborations cannot affect it."""
    path, _ = membership_paths(board)
    run = Run.load(path)
    require(run.goal == goal, "board belongs to another collaboration")
    return run


def result_for(run: Run, member: Member, board: Path, state: str) -> dict[str, Any]:
    """Supply generated identifiers and a session-specific artifact directory."""
    return {
        "status": state,
        "goal": run.goal,
        "participant": member.participant,
        "peer": next(peer for peer in AUTO_PARTICIPANTS if peer != member.participant),
        "session": member.session,
        "settings_path": run.settings_path,
        "board": str(board),
        "temp_directory": str(board.parents[2]),
        "artifact_directory": str(board / "work"),
        "initial_proposer": min(AUTO_PARTICIPANTS),
        "completion_proposer": min(AUTO_PARTICIPANTS),
        "task": run.task,
    }


def check_options(run: Run, options: Mapping[str, Any] | None) -> None:
    """A joining peer uses the advertiser's already committed operating settings."""
    if options is None:
        return
    configured = Settings.load(Path(run.settings_path))
    data = {
        **asdict(configured),
        "board": str(configured.board),
        "participants": list(configured.participants),
        **options,
    }
    require(
        Settings.from_data(data) == configured,
        "operating options differ from the invitation",
    )


def join(
    workspace: Path,
    task: str,
    *,
    options: Mapping[str, Any] | None = None,
    match: str | None = None,
    separate: str | None = None,
    clock: Clock | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Join an eligible invitation before advertising, atomically under one project lock."""
    root = workspace.resolve(strict=True)
    require(root.is_dir(), "workspace must be an existing directory")
    task = " ".join(nonempty(task, "shared task request").split())
    require(
        len(task.encode()) <= Settings.default("max_message_bytes"),
        "task request exceeds input limit",
    )
    require(
        not (match is not None and separate is not None),
        "choose matching or a separate task",
    )
    if options is not None:
        require(
            not {"goal", "context_revision", "participants", "board"}.intersection(
                options
            ),
            "join options cannot replace task identity",
        )
        Settings.from_data(
            {
                **options,
                "goal": "preview",
                "context_revision": "initial",
                "participants": list(AUTO_PARTICIPANTS),
                "board": str(root),
            }
        )
    registry_limit = (
        options.get("max_registry_bytes", Settings.default("max_registry_bytes"))
        if options is not None
        else Settings.default("max_registry_bytes")
    )
    if match is not None:
        identifier(match, "selected invitation")
    clock = clock if clock is not None else SystemClock()
    with nullcontext() if dry_run else file_lock(root / PROJECT_LOCK, clock):
        now = clock.utcnow().timestamp()
        lobby = read_lobby(root, limit=registry_limit)
        candidates = lobby.candidates(now)
        selected = (
            next((item for item in candidates if item.session == match), None)
            if match is not None
            else next((item for item in candidates if item.task == task), None)
        )
        observation = lobby.observation(now)
        changed = (match is not None and selected is None) or (
            selected is None and separate is not None and separate != observation
        )
        if changed or (selected is None and candidates and separate is None):
            return {
                "status": "match-required",
                "observation": observation,
                "candidates": [item.as_json(now) for item in candidates],
                "dry_run": dry_run,
            }
        if selected is not None:
            run = find_run(selected.board, selected.session)
            check_options(run, options)
            if not dry_run:
                append_event(
                    root,
                    lobby,
                    {"session": selected.session, "state": "paired", "task": task},
                    limit=registry_limit,
                )
            return {
                **result_for(run, run.members[1], selected.board, "paired"),
                "dry_run": dry_run,
            }
        return advertise(
            root, task, options=options, clock=clock, dry_run=dry_run, lobby=lobby
        )


def advertise(
    root: Path,
    task: str,
    *,
    options: Mapping[str, Any] | None,
    clock: Clock,
    dry_run: bool,
    lobby: Lobby,
) -> dict[str, Any]:
    """Prepare one isolated board before advertising it; caller holds the project lock."""
    goal = f"collab-{uuid.uuid4().hex}"
    board = directory(project_board(root), (goal,))
    configured = configure(
        root, goal, AUTO_PARTICIPANTS, options=options, dry_run=dry_run, board=board
    )
    members = tuple(
        Member(peer, f"actor-{uuid.uuid4().hex}") for peer in AUTO_PARTICIPANTS
    )
    run = Run(goal, str(root), task, configured["settings_path"], members)
    if not dry_run:
        path, _ = membership_paths(board, create=True)
        write_run(path, run, replace_existing=False)
        settings = Settings.load(Path(run.settings_path))
        append_event(
            root,
            lobby,
            {
                "session": goal,
                "state": "waiting",
                "task": task,
                "board": str(board),
                "expires_at": clock.utcnow().timestamp()
                + settings.invitation_ttl_seconds,
            },
            limit=settings.max_registry_bytes,
        )
    return {**result_for(run, members[0], board, "waiting"), "dry_run": dry_run}


def invitation_for(settings: Settings) -> Invitation | None:
    """Find this board's pairing, leaving explicitly configured message boards supported."""
    path, _ = membership_paths(settings.board)
    if not (path.exists() or path.is_symlink()):
        return None
    run = find_run(settings.board, settings.goal)
    invitation = next(
        (
            item
            for item in read_lobby(
                Path(run.workspace), limit=settings.max_registry_bytes
            ).invitations
            if item.session == run.goal
        ),
        None,
    )
    require(
        invitation is not None and invitation.board == settings.board,
        "collaboration advertisement is unavailable",
    )
    return invitation


def pairing_state(settings: Settings, *, clock: Clock | None = None) -> str:
    """Read the invitation without renewing it or changing processing state."""
    invitation = invitation_for(settings)
    if invitation is None:
        return "explicit"
    now = (clock if clock is not None else SystemClock()).utcnow().timestamp()
    return (
        "paired"
        if invitation.paired
        else "waiting"
        if invitation.eligible(now)
        else "expired"
    )


def renew_invitation(settings: Settings, *, clock: Clock | None = None) -> str:
    """Normal waiting renews only a still-valid unmatched invitation, never a paired session."""
    clock = clock if clock is not None else SystemClock()
    state = pairing_state(settings, clock=clock)
    if state != "waiting":
        return state
    run = find_run(settings.board, settings.goal)
    root = Path(run.workspace)
    with file_lock(root / PROJECT_LOCK, clock):
        now = clock.utcnow().timestamp()
        lobby = read_lobby(root, limit=settings.max_registry_bytes)
        invitation = next(
            item for item in lobby.invitations if item.session == run.goal
        )
        if invitation.paired:
            return "paired"
        if not invitation.eligible(now):
            return "expired"
        if invitation.expires_at - now <= settings.invitation_ttl_seconds / 2:
            append_event(
                root,
                lobby,
                {
                    "session": run.goal,
                    "state": "renewed",
                    "expires_at": clock.utcnow().timestamp()
                    + settings.invitation_ttl_seconds,
                },
                limit=settings.max_registry_bytes,
            )
        return "waiting"


def check_membership(settings: Settings, participant: str, session: str) -> None:
    """Reject expired invitations and unknown or finished writers within their own board."""
    path, _ = membership_paths(settings.board)
    if not (path.exists() or path.is_symlink()):
        return
    run = find_run(settings.board, settings.goal)
    member = next(
        (item for item in run.members if item.participant == participant), None
    )
    require(
        member is not None and member.session == session and not member.finished,
        "session is not an active enrolled writer for this task",
    )
    state = pairing_state(settings)
    require(
        state != "expired",
        "unmatched invitation expired; join again with the task to form a fresh pair",
    )
    require(
        state != "waiting" or participant == AUTO_PARTICIPANTS[0],
        "the second seat has not been claimed",
    )


def session_status(workspace: Path) -> dict[str, Any]:
    """Inspect all session invitations without guessing agent identity or liveness."""
    root = workspace.resolve(strict=True)
    require(root.is_dir(), "workspace must be an existing directory")
    now = SystemClock().utcnow().timestamp()
    lobby = read_lobby(root)
    return {
        "registry": str(root / PROJECT_REGISTRY),
        "observation": lobby.observation(now),
        "sessions": [item.as_json(now) for item in lobby.invitations],
    }


def finish(
    settings: Settings,
    participant: str,
    session: str,
    agreement: str,
    *,
    timeout: float | None = None,
    clock: Clock | None = None,
    dry_run: bool = False,
) -> tuple[dict[str, Any], ExitCode]:
    """Confirm bilateral completion and wait briefly for the peer to observe it too."""
    settings.check_actor(participant, session)
    identifier(agreement, "completion agreement")
    require(
        pairing_state(settings, clock=clock) == "paired",
        "pair both participants before finishing",
    )
    snapshot = scan(settings)
    approved = snapshot.history.get(agreement)
    require(
        approved is not None
        and approved.kind == Kind.AGREE
        and approved.reply_to not in snapshot.conflicts,
        "completion requires an unconflicted peer agreement",
    )
    proposal = snapshot.history[approved.reply_to]
    require(
        proposal.subject_type == "decision"
        and tuple(proposal.body["scope"]) == COMPLETION_SCOPE,
        "completion proposal must use the goal-completion scope",
    )
    duration = settings.wait_timeout_seconds if timeout is None else timeout
    require(
        type(duration) in (int, float) and duration > 0,
        "finish needs a finite positive command budget",
    )
    require(math.isfinite(duration), "finish needs a finite positive command budget")
    clock = clock if clock is not None else SystemClock()
    with nullcontext() if dry_run else session_lock(settings.board, clock):
        current = find_run(settings.board, settings.goal)
        require(
            current.agreement in (None, agreement),
            "peers must finish against the same completion agreement",
        )
        require(
            any(
                item.participant == participant and item.session == session
                for item in current.members
            ),
            "unknown completion session",
        )
        members = tuple(
            replace(item, finished=True) if item.participant == participant else item
            for item in current.members
        )
        current = replace(current, members=members, agreement=agreement)
        if dry_run:
            return {
                "dry_run": True,
                "would_complete": current.complete,
                "goal": current.goal,
                "agreement": agreement,
            }, ExitCode.OK
        path, _ = membership_paths(settings.board)
        write_run(path, current, replace_existing=True)
    stop = clock.monotonic() + duration
    while True:
        observed = find_run(settings.board, settings.goal)
        if observed.complete:
            return {
                "status": "complete",
                "complete": True,
                "goal": settings.goal,
                "agreement": agreement,
            }, ExitCode.OK
        remaining = stop - clock.monotonic()
        if remaining <= 0:
            return {
                "status": "closing",
                "complete": False,
                "goal": settings.goal,
                "agreement": agreement,
                "next_action": "repeat-finish-with-the-same-agreement",
            }, ExitCode.TIMEOUT
        clock.sleep(min(settings.poll_interval_seconds, remaining))
