#!/usr/bin/env python3
# Copyright 2026 Adam Gogacz
# SPDX-License-Identifier: Apache-2.0

"""Non-interactive CLI for Duet validation, publication, replay and checkpoints."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from actions import Connection, retry, send
from board import publish, read_record, save_checkpoint
from completion import complete, state
from configure import configure
from delivery import acknowledge, receive, wait_for_messages
from enrollment import check_membership, finish, join, session_status
from messages import SUBJECT_TYPES, Kind, Message
from settings import (
    DEFAULT_CONTEXT_REVISION,
    DuetError,
    ExitCode,
    Settings,
    require,
    strict_json,
)


def parser() -> argparse.ArgumentParser:
    """Expose pairing and collaboration actions without interactive CLI prompts."""
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    status = commands.add_parser(
        "status", help="Inspect project invitations without inferring agent liveness"
    )
    status_target = status.add_mutually_exclusive_group(required=True)
    status_target.add_argument("--workspace", type=Path)
    status_target.add_argument(
        "--connection",
        help="Inspect this actor's recognized gate, obligations, and valid next actions",
    )
    setup = commands.add_parser(
        "setup", help="Create or reuse task settings under TMPDIR"
    )
    setup.add_argument("--workspace", type=Path, required=True)
    setup.add_argument("--goal", required=True)
    setup.add_argument("--participants", nargs=2, required=True)
    setup.add_argument("--context-revision", default=DEFAULT_CONTEXT_REVISION)
    setup.add_argument(
        "--options", type=Path, help="JSON object overriding operating defaults"
    )
    setup.add_argument("--dry-run", action="store_true")
    joining = commands.add_parser(
        "join",
        help="Join a waiting compatible invitation or advertise a fresh collaboration",
    )
    joining.add_argument("--workspace", type=Path, required=True)
    joining.add_argument(
        "--match", help="Claim a waiting session after assessing its task compatibility"
    )
    joining.add_argument(
        "--separate",
        metavar="OBSERVATION",
        help="Advertise a distinct task after assessing all returned candidates",
    )
    joining.add_argument(
        "--options", type=Path, help="Optional operating overrides on the first join"
    )
    joining.add_argument(
        "--dry-run", action="store_true", help="Preview without reserving a slot"
    )
    sending = commands.add_parser(
        "send",
        help="Construct and journal a message from plain text and file attachments",
    )
    sending.add_argument("--connection", required=True)
    sending.add_argument(
        "--kind", choices=[kind.value for kind in Kind], default=Kind.INFORM.value
    )
    sending.add_argument("--text", help="Substantive text; use - for UTF-8 stdin")
    sending.add_argument(
        "--reply-to", help="Reply target; new proposals and alerts use --cause instead"
    )
    sending.add_argument("--subject-type", choices=sorted(SUBJECT_TYPES))
    sending.add_argument("--subject-id")
    sending.add_argument(
        "--scope",
        nargs="+",
        help="Affected scope; required for proposals, requests and alerts unless supplied in --body",
    )
    sending.add_argument("--criteria", action="append")
    sending.add_argument("--evidence", action="append")
    sending.add_argument(
        "--stop-condition",
        help="Bounded unsuccessful-stop/reassessment rule for a gate",
    )
    sending.add_argument("--artifact", action="append", type=Path, default=[])
    sending.add_argument(
        "--body",
        type=Path,
        help="Optional semantic body fields for advanced acts; never an envelope",
    )
    sending.add_argument(
        "--cause",
        action="append",
        default=[],
        help="Link prior evidence or a replaced proposal without turning an initiating act into a reply",
    )
    sending.add_argument("--dry-run", action="store_true")
    sending.add_argument(
        "--details",
        action="store_true",
        help="Include the constructed envelope instead of a compact delivery receipt",
    )
    retrying = commands.add_parser(
        "retry", help="Retry the exact last saved send with its original generated ID"
    )
    retrying.add_argument("--connection", required=True)
    retrying.add_argument("--dry-run", action="store_true")
    retrying.add_argument("--details", action="store_true")
    closing = commands.add_parser(
        "complete",
        help="Propose or explicitly agree to joint completion; handle closing automatically",
    )
    closing.add_argument("--connection", required=True)
    choice = closing.add_mutually_exclusive_group()
    choice.add_argument(
        "--propose",
        action="store_true",
        help="Propose whole-goal completion or an incomplete handoff and confirm exit readiness",
    )
    choice.add_argument(
        "--revise",
        action="store_true",
        help="Prepare a fresh completion offer without manual withdrawal choreography",
    )
    choice.add_argument(
        "--request-changes",
        metavar="PROPOSAL_ID",
        help="Send an attention-bearing correction or late-evidence alert",
    )
    choice.add_argument(
        "--agree",
        metavar="PROPOSAL_ID",
        help="Explicitly confirm this outcome and readiness to exit, not merely report approval",
    )
    closing.add_argument(
        "--text", help="Completion evidence or agreement comment; - reads stdin"
    )
    closing.add_argument("--artifact", action="append", type=Path, default=[])
    closing.add_argument(
        "--evidence",
        action="append",
        help="One statement per gate criterion, in the same order",
    )
    closing.add_argument(
        "--outcome", choices=["complete", "incomplete"], default="complete"
    )
    closing.add_argument("--timeout-seconds", type=float)
    closing.add_argument("--dry-run", action="store_true")
    for name, description in (
        ("validate", "Validate a message from stdin; no board changes"),
        ("publish", "Publish message bytes from stdin, atomically without replacement"),
        (
            "receive",
            "Read the full causal history and own checkpoint; no processing side effects",
        ),
        (
            "wait",
            "Block for peer messages, a correlated reply or a durable await condition",
        ),
        (
            "checkpoint",
            "Save explicit processing decisions from stdin; replaces only own checkpoint",
        ),
        (
            "ack",
            "Record exact processed message IDs without writing a checkpoint document",
        ),
        (
            "finish",
            "Confirm agreed goal completion and wait for the peer's confirmation",
        ),
    ):
        command = commands.add_parser(name, help=description)
        identity = command.add_mutually_exclusive_group(required=True)
        identity.add_argument(
            "--config", type=Path, help="Advanced goal-specific JSON settings"
        )
        identity.add_argument(
            "--connection",
            help="Saved join handle supplying task and identity",
        )
        if name != "validate":
            command.add_argument(
                "--participant", help="Your stable participant ID with --config"
            )
        if name in {"publish", "checkpoint", "ack", "finish"}:
            command.add_argument(
                "--session", help="Your active execution ID with --config"
            )
            command.add_argument(
                "--dry-run", action="store_true", help="Validate without writing"
            )
        if name == "ack":
            command.add_argument(
                "--messages",
                nargs="+",
                required=True,
                help="Exact IDs already processed; never acknowledges unseen arrivals",
            )
        if name == "receive":
            command.add_argument(
                "--unread",
                action="store_true",
                help="Return only unprocessed peer messages; omit checkpoint contents",
            )
        if name == "finish":
            command.add_argument(
                "--agreement", required=True, help="Agreed goal-completion message ID"
            )
            command.add_argument(
                "--timeout-seconds", type=float, help="Finite closing wait budget"
            )
        if name == "wait":
            selection = command.add_mutually_exclusive_group()
            selection.add_argument(
                "--reply-to",
                help="Wait for a substantive peer reply to your message ID",
            )
            selection.add_argument(
                "--for-wait",
                help="Wait for the condition and original deadline of your await ID",
            )
            command.add_argument(
                "--timeout-seconds",
                type=float,
                help="Finite command duration; defaults to configured wait timeout",
            )
    return result


def main() -> int:
    """Keep machine data on stdout, bounded inputs and diagnostics on stderr."""
    arguments = parser().parse_args()
    try:
        if arguments.command in {"send", "retry", "complete"}:
            connection = Connection.load(arguments.connection)
            if arguments.command == "retry":
                output = retry(connection, dry_run=arguments.dry_run)
                code = ExitCode.OK
            else:
                text = read_text(arguments.text, connection.settings.max_message_bytes)
                if arguments.command == "send":
                    body = (
                        strict_json(
                            read_record(
                                arguments.body, connection.settings.max_message_bytes
                            )
                        )
                        if arguments.body is not None
                        else None
                    )
                    output = send(
                        connection,
                        Kind(arguments.kind),
                        text=text,
                        reply_to=arguments.reply_to,
                        scope=arguments.scope,
                        criteria=arguments.criteria,
                        stop_condition=arguments.stop_condition,
                        evidence=arguments.evidence,
                        artifacts=arguments.artifact,
                        subject_type=arguments.subject_type,
                        subject_id=arguments.subject_id,
                        body=body,
                        causes=arguments.cause,
                        dry_run=arguments.dry_run,
                    )
                    code = ExitCode.OK
                else:
                    output, code = complete(
                        connection,
                        propose=arguments.propose,
                        revise=arguments.revise,
                        request_changes=arguments.request_changes,
                        evidence=arguments.evidence,
                        outcome=arguments.outcome,
                        agree=arguments.agree,
                        text=text,
                        artifacts=arguments.artifact,
                        timeout=arguments.timeout_seconds,
                        dry_run=arguments.dry_run,
                    )
            if (
                arguments.command in {"send", "retry"}
                and not arguments.details
                and "message" in output
            ):
                message = output["message"]
                output = {
                    **{key: value for key, value in output.items() if key != "message"},
                    "id": message["id"],
                    "kind": message["kind"],
                    "artifacts": message["body"].get("artifacts", []),
                }
            print(json.dumps(output, sort_keys=True, allow_nan=False))
            return code
        if arguments.command == "status":
            output = (
                state(Connection.load(arguments.connection))
                if arguments.connection
                else session_status(arguments.workspace)
            )
            print(json.dumps(output, sort_keys=True))
            return ExitCode.OK
        if arguments.command == "join":
            limit = Settings.default("max_message_bytes")
            raw = sys.stdin.buffer.read(limit + 1)
            require(len(raw) <= limit, "task request exceeds configured input limit")
            try:
                task = raw.decode("utf-8")
            except UnicodeError as error:
                raise DuetError("task request must be UTF-8") from error
            options = (
                strict_json(
                    read_record(
                        arguments.options, Settings.default("max_checkpoint_bytes")
                    )
                )
                if arguments.options is not None
                else None
            )
            output = join(
                arguments.workspace,
                task,
                options=options,
                dry_run=arguments.dry_run,
                match=arguments.match,
                separate=arguments.separate,
            )
            if not arguments.dry_run and "settings_path" in output:
                connection = Connection(
                    Path(output["settings_path"]),
                    output["participant"],
                    output["session"],
                )
                output["connection_path"] = str(connection.save())
                try:
                    output["connection"] = connection.save_alias()
                except (DuetError, OSError):
                    # Failure of optional shorthand must not strand an enrolled actor.
                    output["connection"] = output["connection_path"]
                    output["connection_notice"] = (
                        "short handle unavailable; retain the full connection path"
                    )
            print(
                json.dumps(output, ensure_ascii=True, sort_keys=True, allow_nan=False)
            )
            return ExitCode.OK
        if arguments.command == "setup":
            options = (
                strict_json(
                    read_record(
                        arguments.options, Settings.default("max_checkpoint_bytes")
                    )
                )
                if arguments.options is not None
                else None
            )
            output = configure(
                arguments.workspace,
                arguments.goal,
                tuple(arguments.participants),
                context_revision=arguments.context_revision,
                options=options,
                dry_run=arguments.dry_run,
            )
            print(
                json.dumps(output, ensure_ascii=True, sort_keys=True, allow_nan=False)
            )
            return ExitCode.OK
        resolve_identity(arguments)
        settings = Settings.load(arguments.config)
        exit_code = ExitCode.OK
        if arguments.command in {"publish", "checkpoint", "ack"}:
            check_membership(settings, arguments.participant, arguments.session)
        if arguments.command == "receive":
            received = receive(settings, arguments.participant)
            require(
                not arguments.unread or received.checkpoint_error is None,
                "checkpoint is invalid; use full receive to recover before unread delivery",
            )
            output = received.as_json(settings, unread_only=arguments.unread)
        elif arguments.command == "wait":
            result = wait_for_messages(
                settings,
                arguments.participant,
                reply_to=arguments.reply_to,
                wait_id=arguments.for_wait,
                timeout=arguments.timeout_seconds,
            )
            output = result.as_json(settings)
            exit_code = result.code
        elif arguments.command == "ack":
            output = acknowledge(
                settings,
                arguments.participant,
                arguments.session,
                arguments.messages,
                dry_run=arguments.dry_run,
            )
        elif arguments.command == "finish":
            output, exit_code = finish(
                settings,
                arguments.participant,
                arguments.session,
                arguments.agreement,
                timeout=arguments.timeout_seconds,
                dry_run=arguments.dry_run,
            )
        else:
            limit = (
                settings.max_checkpoint_bytes
                if arguments.command == "checkpoint"
                else settings.max_message_bytes
            )
            raw = sys.stdin.buffer.read(limit + 1)
            require(len(raw) <= limit, "stdin exceeds configured byte limit")
            if arguments.command == "validate":
                message = Message.parse(raw, settings)
                output = {
                    "valid": True,
                    "id": message.id,
                    "validation": "structure-only",
                }
            else:
                operation = (
                    publish if arguments.command == "publish" else save_checkpoint
                )
                output = operation(
                    settings,
                    arguments.participant,
                    arguments.session,
                    raw,
                    dry_run=arguments.dry_run,
                )
        print(json.dumps(output, ensure_ascii=True, sort_keys=True, allow_nan=False))
        return exit_code
    except (DuetError, OSError, RecursionError) as error:
        print(
            json.dumps({"error": type(error).__name__, "message": str(error)}),
            file=sys.stderr,
        )
        return ExitCode.ERROR


def read_text(value: str | None, limit: int) -> str | None:
    """Keep long or dynamic text out of shell interpolation while bounding input."""
    if value != "-":
        return value
    raw = sys.stdin.buffer.read(limit + 1)
    require(len(raw) <= limit, "text exceeds configured message limit")
    try:
        return raw.decode("utf-8")
    except UnicodeError as error:
        raise DuetError("text must be UTF-8") from error


def resolve_identity(arguments: argparse.Namespace) -> None:
    """Support one retained handle while preserving the advanced explicit interface."""
    if arguments.connection is not None:
        require(
            getattr(arguments, "participant", None) is None
            and getattr(arguments, "session", None) is None,
            "do not override identity supplied by a connection",
        )
        connection = Connection.load(arguments.connection)
        arguments.config = connection.settings_path
        arguments.participant = connection.participant
        arguments.session = connection.session
    elif arguments.command != "validate":
        require(bool(arguments.participant), "--participant is required with --config")
        if arguments.command in {"publish", "checkpoint", "ack", "finish"}:
            require(bool(arguments.session), "--session is required with --config")


if __name__ == "__main__":
    raise SystemExit(main())
