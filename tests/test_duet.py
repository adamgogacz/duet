# Copyright 2026 Adam Gogacz
# SPDX-License-Identifier: Apache-2.0

"""Exercise Duet delivery under concurrent writes, replay and interrupted state."""

from __future__ import annotations

import errno
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from board import publish, save_checkpoint, scan, validate_checkpoint
from messages import Message
from settings import DuetError, Settings, encoded, strict_json, temporary_directory
from startup import opening


class BoardFixture(unittest.TestCase):
    """Reusable isolated boards; no fixture writes to live collaboration state."""

    def setUp(self) -> None:
        """Centralize test operating settings and owned scratch storage."""
        self.scratch = tempfile.TemporaryDirectory(
            prefix="duet-tests-", dir=temporary_directory()
        )
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.environment = patch.dict(os.environ, {"TMPDIR": str(self.root)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.settings = Settings(
            "goal-1",
            "context-v1",
            ("alpha", "beta"),
            self.root / "board",
            1,
            60,
            "Report to the user",
            65536,
            100,
            65536,
        )
        self.config = self.root / "settings.json"
        data = asdict(self.settings)
        data["board"] = str(self.settings.board)
        self.config.write_bytes(encoded(data))

    def message(
        self,
        key: str,
        kind: str,
        *,
        sender: str = "alpha",
        parent: str | None = None,
        causes: list[str] | None = None,
        subject_type: str = "goal",
        subject_id: str = "goal-1",
        body: dict | None = None,
    ) -> bytes:
        """Make explicit sample envelopes for protocol scenarios."""
        return encoded(
            {
                "id": key,
                "goal": self.settings.goal,
                "from": sender,
                "to": "beta" if sender == "alpha" else "alpha",
                "session": f"session-{sender}",
                "subject": {"type": subject_type, "id": subject_id},
                "kind": kind,
                "reply_to": parent,
                "causes": causes
                if causes is not None
                else ([parent] if parent else []),
                "body": body if body is not None else {},
            }
        )

    def send(self, raw: bytes, **options: object) -> dict:
        """Publish under the envelope's explicit identity."""
        data = strict_json(raw)
        return publish(self.settings, data["from"], data["session"], raw, **options)

    def inject(self, raw: bytes) -> None:
        """Simulate external or corrupted publications for receiver robustness."""
        data = strict_json(raw)
        path = (
            self.settings.board
            / "messages"
            / self.settings.goal
            / data["from"]
            / f"{data['id']}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def ready(self) -> None:
        """Complete the minimum two-peer startup exchange."""
        for sender in self.settings.participants:
            self.send(
                self.message(
                    f"{sender}-ready",
                    "ready",
                    sender=sender,
                    body={
                        "context_revision": "context-v1",
                        "summary": "No prior commitments",
                    },
                )
            )

    def request(self) -> None:
        """Negotiate a bounded task after matching readiness."""
        self.ready()
        agreement = self.initial_plan()
        self.send(
            self.message(
                "request-1",
                "request",
                subject_type="task",
                subject_id="review",
                causes=[agreement],
                body={
                    "objective": "Review contract",
                    "scope": ["review-output"],
                    "inputs": [],
                    "criteria": ["Resolve ambiguity"],
                },
            )
        )

    def initial_plan(self, settings: Settings | None = None) -> str:
        """Agree a work division after readiness, with a recoverable decision ID."""
        settings = settings or self.settings
        proposer = settings.initial_proposer
        peer = next(sender for sender in settings.participants if sender != proposer)
        ready_ids = [
            message.id
            for message in scan(settings).messages
            if message.kind == "ready"
            and message.body["context_revision"] == settings.context_revision
        ]
        proposal_id = f"opening-{settings.context_revision}"
        agreement_id = f"agreement-{settings.context_revision}"
        proposal = self.message(
            proposal_id,
            "propose",
            sender=proposer,
            subject_type="decision",
            subject_id=settings.initial_plan_id,
            causes=ready_ids,
            body={
                "proposal": "Divide implementation and review",
                "scope": ["work-division"],
            },
        )
        publish(settings, proposer, f"session-{proposer}", proposal)
        agreement = self.message(
            agreement_id,
            "agree",
            sender=peer,
            parent=proposal_id,
            subject_type="decision",
            subject_id=settings.initial_plan_id,
        )
        publish(settings, peer, f"session-{peer}", agreement)
        return agreement_id

    def checkpoint(self) -> dict:
        """Create an explicit empty local processing checkpoint."""
        return {
            "goal": "goal-1",
            "participant": "alpha",
            "session": "session-alpha",
            "processed": [],
            "outgoing": [],
            "deferred": {},
            "invalid": {},
            "active": [],
            "external_actions": [],
        }

    def cli(
        self, command: str, *, raw: bytes = b"", extra: tuple[str, ...] = ()
    ) -> subprocess.CompletedProcess:
        """Exercise the public process boundary and machine-readable output."""
        args = [
            sys.executable,
            str(SCRIPTS / "duet.py"),
            command,
            "--config",
            str(self.config),
        ]
        if command != "validate":
            args += ["--participant", "alpha"]
        if command in {"publish", "checkpoint", "ack"}:
            args += ["--session", "session-alpha"]
        return subprocess.run(
            [*args, *extra],
            input=raw,
            capture_output=True,
            check=False,
            timeout=self.settings.wait_timeout_seconds,
        )


class BoardTests(BoardFixture):
    """Exercise publication, replay, negotiation and checkpoint invariants."""

    def test_independent_concurrent_publications_are_complete(self) -> None:
        """Both senders may publish simultaneously without partial reader-visible files."""
        raw = [
            self.message(
                f"message-{index}",
                "inform",
                sender=self.settings.participants[index % 2],
                body={"text": f"Finding {index}"},
            )
            for index in range(20)
        ]
        with ThreadPoolExecutor() as pool:
            results = list(pool.map(self.send, raw))
        self.assertTrue(all(result["published"] for result in results))
        snapshot = scan(self.settings)
        self.assertEqual(len(snapshot.messages), len(raw))
        self.assertFalse(snapshot.invalid)
        self.assertEqual({item.raw for item in snapshot.messages}, set(raw))

    def test_same_id_concurrent_retries_publish_once(self) -> None:
        raw = self.message("retry", "inform", body={"text": "One logical finding"})
        with ThreadPoolExecutor() as pool:
            results = list(pool.map(lambda _: self.send(raw), range(12)))
        self.assertEqual(sum(result["published"] for result in results), 1)
        self.assertEqual(len(scan(self.settings).messages), 1)

    def test_separate_cli_processes_publish_to_the_same_board(self) -> None:
        def run(sender: str) -> subprocess.CompletedProcess:
            raw = self.message(
                f"{sender}-process",
                "inform",
                sender=sender,
                body={"text": "Independent process"},
            )
            return subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "duet.py"),
                    "publish",
                    "--config",
                    str(self.config),
                    "--participant",
                    sender,
                    "--session",
                    f"session-{sender}",
                ],
                input=raw,
                capture_output=True,
                check=False,
                timeout=self.settings.wait_timeout_seconds,
            )

        with ThreadPoolExecutor() as pool:
            results = list(pool.map(run, self.settings.participants))
        for result in results:
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(scan(self.settings).messages), 2)

    def test_duplicate_ids_across_senders_do_not_choose_a_winner(self) -> None:
        for sender in self.settings.participants:
            self.inject(
                self.message(
                    "same-id", "inform", sender=sender, body={"text": "Collision"}
                )
            )
        snapshot = scan(self.settings)
        self.assertIn("same-id", snapshot.invalid)
        self.assertFalse(snapshot.messages)

    def test_collision_cannot_replace_existing_bytes(self) -> None:
        original = self.message("collision", "inform", body={"text": "Original"})
        self.send(original)
        with self.assertRaises(DuetError):
            self.send(self.message("collision", "inform", body={"text": "Replacement"}))
        self.assertEqual(scan(self.settings).history["collision"].raw, original)

    def test_dry_run_and_receive_do_not_create_board(self) -> None:
        result = self.send(
            self.message("dry", "inform", body={"text": "Preview"}), dry_run=True
        )
        self.assertTrue(result["dry_run"])
        received = self.cli("receive")
        self.assertEqual(received.returncode, 0, received.stderr)
        startup = json.loads(received.stdout)["startup"]
        self.assertEqual(startup["initial_proposer"], "alpha")
        self.assertEqual(startup["phase"], "awaiting-readiness")
        self.assertFalse(self.settings.board.exists())

    def test_opening_role_is_independent_of_start_and_configuration_order(self) -> None:
        for order in (("alpha", "beta"), ("beta", "alpha")):
            with self.subTest(order=order):
                self.settings = replace(
                    self.settings,
                    participants=("beta", "alpha"),
                    board=self.root / f"board-{order[0]}",
                )
                for sender in order:
                    self.send(
                        self.message(
                            f"{sender}-ready",
                            "ready",
                            sender=sender,
                            body={
                                "context_revision": "context-v1",
                                "summary": "Ready on empty board",
                            },
                        )
                    )
                snapshot = scan(self.settings)
                state = opening(self.settings, snapshot.history, snapshot.conflicts)
                self.assertEqual(state.proposer, "alpha")
                self.assertEqual(state.as_json()["phase"], "awaiting-initial-proposal")
                with self.assertRaisesRegex(DuetError, "sorts first"):
                    self.send(
                        self.message(
                            "wrong-opening",
                            "propose",
                            sender="beta",
                            subject_type="decision",
                            subject_id=self.settings.initial_plan_id,
                            causes=["alpha-ready", "beta-ready"],
                            body={
                                "proposal": "I started first",
                                "scope": ["work-division"],
                            },
                        )
                    )
                self.initial_plan()
                snapshot = scan(self.settings)
                self.assertEqual(
                    opening(
                        self.settings, snapshot.history, snapshot.conflicts
                    ).as_json()["phase"],
                    "agreed",
                )

    def test_simultaneous_readiness_needs_no_first_writer(self) -> None:
        messages = [
            self.message(
                f"{sender}-ready",
                "ready",
                sender=sender,
                body={
                    "context_revision": "context-v1",
                    "summary": "Started concurrently",
                },
            )
            for sender in self.settings.participants
        ]
        with ThreadPoolExecutor() as pool:
            results = list(pool.map(self.send, messages))
        self.assertTrue(all(result["published"] for result in results))
        snapshot = scan(self.settings)
        self.assertEqual(
            opening(self.settings, snapshot.history, snapshot.conflicts).as_json()[
                "phase"
            ],
            "awaiting-initial-proposal",
        )
        self.initial_plan()

    def test_initial_proposal_requires_both_readiness_causes(self) -> None:
        self.ready()
        with self.assertRaisesRegex(DuetError, "causally include both readiness"):
            self.send(
                self.message(
                    "unjustified-opening",
                    "propose",
                    subject_type="decision",
                    subject_id=self.settings.initial_plan_id,
                    causes=["alpha-ready"],
                    body={"proposal": "Work division", "scope": ["work-division"]},
                )
            )

    def test_requests_wait_for_initial_agreement_and_both_peers_can_then_initiate(
        self,
    ) -> None:
        self.ready()
        request = self.message(
            "early-request",
            "request",
            sender="beta",
            subject_type="task",
            subject_id="implementation",
            body={
                "objective": "Implement contract",
                "scope": ["implementation-output"],
                "inputs": [],
                "criteria": ["Checked"],
            },
        )
        with self.assertRaisesRegex(DuetError, "work division must be agreed"):
            self.send(request)
        agreement = self.initial_plan()
        with self.assertRaisesRegex(DuetError, "causally include"):
            self.send(request)
        value = strict_json(request)
        value["causes"] = [agreement]
        self.send(encoded(value))
        self.assertEqual(scan(self.settings).history["early-request"].sender, "beta")

    def test_counterproposal_can_establish_the_initial_work_division(self) -> None:
        self.ready()
        self.send(
            self.message(
                "opening",
                "propose",
                subject_type="decision",
                subject_id=self.settings.initial_plan_id,
                causes=["alpha-ready", "beta-ready"],
                body={"proposal": "First division", "scope": ["work-division"]},
            )
        )
        self.send(
            self.message(
                "counterplan",
                "propose",
                sender="beta",
                subject_type="decision",
                subject_id="counter-division",
                causes=["opening"],
                body={
                    "proposal": "Reverse the responsibilities",
                    "scope": ["work-division"],
                },
            )
        )
        self.send(
            self.message(
                "counter-agreed",
                "agree",
                parent="counterplan",
                subject_type="decision",
                subject_id="counter-division",
            )
        )
        snapshot = scan(self.settings)
        state = opening(self.settings, snapshot.history, snapshot.conflicts)
        self.assertEqual(state.agreement.id, "counter-agreed")
        self.send(
            self.message(
                "task-after-counter",
                "request",
                causes=["counter-agreed"],
                subject_type="task",
                subject_id="implementation",
                body={
                    "objective": "Implement",
                    "scope": ["implementation-output"],
                    "inputs": [],
                    "criteria": ["Checked"],
                },
            )
        )

    def test_recovery_reuses_the_agreed_opening(self) -> None:
        self.ready()
        agreement = self.initial_plan()
        value = strict_json(
            self.message(
                "alpha-resumed",
                "ready",
                causes=[agreement],
                body={
                    "context_revision": "context-v1",
                    "summary": "Recovered agreed division",
                },
            )
        )
        value["session"] = "session-alpha-next"
        publish(self.settings, "alpha", "session-alpha-next", encoded(value))
        value = strict_json(
            self.message(
                "resumed-task",
                "request",
                causes=[agreement, "alpha-resumed"],
                subject_type="task",
                subject_id="new-task",
                body={
                    "objective": "Continue",
                    "scope": ["next-output"],
                    "inputs": [],
                    "criteria": ["Checked"],
                },
            )
        )
        value["session"] = "session-alpha-next"
        publish(self.settings, "alpha", "session-alpha-next", encoded(value))
        snapshot = scan(self.settings)
        state = opening(self.settings, snapshot.history, snapshot.conflicts)
        self.assertEqual(state.agreement.id, agreement)
        self.assertEqual(
            sum(message.kind == "propose" for message in snapshot.messages), 1
        )

    def test_readiness_is_required_before_joint_work(self) -> None:
        request = self.message(
            "early",
            "request",
            subject_type="task",
            subject_id="review",
            body={
                "objective": "Review",
                "scope": ["output"],
                "inputs": [],
                "criteria": ["Checked"],
            },
        )
        with self.assertRaisesRegex(DuetError, "ready"):
            self.send(request)
        self.assertFalse(self.settings.board.exists())

    def test_context_and_session_identity_are_checked(self) -> None:
        with self.assertRaisesRegex(DuetError, "context"):
            self.send(
                self.message(
                    "bad-ready",
                    "ready",
                    body={"context_revision": "other", "summary": "Ready"},
                )
            )
        raw = self.message("identity", "inform", body={"text": "Finding"})
        with self.assertRaisesRegex(DuetError, "identity"):
            publish(self.settings, "beta", "session-beta", raw)

    def test_goal_revision_preserves_history_and_requires_fresh_readiness(self) -> None:
        self.ready()
        updated = replace(self.settings, context_revision="context-v2")
        self.assertEqual(len(scan(updated).messages), 2)
        raw = self.message(
            "new-task",
            "request",
            subject_type="task",
            subject_id="new-review",
            body={
                "objective": "Review updated requirements",
                "scope": ["review-output"],
                "inputs": [],
                "criteria": ["New requirement checked"],
            },
        )
        with self.assertRaisesRegex(DuetError, "ready"):
            publish(updated, "alpha", "session-alpha", raw)
        for sender in updated.participants:
            ready = self.message(
                f"{sender}-ready-v2",
                "ready",
                sender=sender,
                causes=[f"{sender}-ready"],
                body={"context_revision": "context-v2", "summary": "Revision reviewed"},
            )
            publish(updated, sender, f"session-{sender}", ready)
        agreement = self.initial_plan(updated)
        value = strict_json(raw)
        value["causes"] = [agreement]
        raw = encoded(value)
        publish(updated, "alpha", "session-alpha", raw)
        self.assertFalse(scan(updated).invalid)

    def test_missing_cause_is_pending_then_replayed(self) -> None:
        self.inject(
            self.message(
                "child", "inform", causes=["parent"], body={"text": "Depends on parent"}
            )
        )
        self.assertEqual(scan(self.settings).pending, {"child": ("parent",)})
        self.send(self.message("parent", "inform", body={"text": "Arrived later"}))
        self.assertEqual(
            [item.id for item in scan(self.settings).messages], ["parent", "child"]
        )

    def test_cycles_and_their_descendants_are_not_applied(self) -> None:
        for key, cause in (("a", "b"), ("b", "a"), ("child", "a")):
            self.inject(
                self.message(
                    key, "inform", causes=[cause], body={"text": "Cyclic history"}
                )
            )
        snapshot = scan(self.settings)
        self.assertEqual(set(snapshot.invalid), {"a", "b", "child"})
        self.assertFalse(snapshot.messages)
        self.assertFalse(snapshot.pending)

    def test_wrong_reply_author_and_subject_are_invalid(self) -> None:
        self.request()
        for key, sender, subject_id in (
            ("wrong-owner", "alpha", "review"),
            ("wrong-subject", "beta", "other"),
        ):
            raw = self.message(
                key,
                "accept",
                sender=sender,
                parent="request-1",
                subject_type="task",
                subject_id=subject_id,
            )
            with self.assertRaises(DuetError):
                self.send(raw)

    def test_conflicting_control_replies_remain_visible(self) -> None:
        self.request()
        for key, kind, body in (
            ("accepted", "accept", {}),
            ("declined", "decline", {"reason": "Conflicting response"}),
        ):
            self.send(
                self.message(
                    key,
                    kind,
                    sender="beta",
                    parent="request-1",
                    subject_type="task",
                    subject_id="review",
                    body=body,
                )
            )
        snapshot = scan(self.settings)
        self.assertEqual(snapshot.conflicts["request-1"], ("accepted", "declined"))
        self.assertTrue(snapshot.as_json()["semantic_review_required"])

    def test_wait_resume_requires_satisfying_event_and_keeps_deadline(self) -> None:
        self.ready()
        self.send(
            self.message(
                "question",
                "ask",
                subject_type="question",
                subject_id="q",
                body={"question": "Is empty input valid?"},
            )
        )
        deadline = "2026-01-01T00:00:00Z"
        self.send(
            self.message(
                "wait",
                "await",
                subject_type="wait",
                subject_id="w",
                body={
                    "blocks": ["integration"],
                    "reason": "Need requirement",
                    "deadline": deadline,
                    "condition": {
                        "kind": "answer",
                        "subject": {"type": "question", "id": "q"},
                        "from": "beta",
                        "target": "question",
                    },
                },
            )
        )
        with self.assertRaisesRegex(DuetError, "satisfying"):
            self.send(
                self.message(
                    "premature",
                    "resume",
                    parent="wait",
                    subject_type="wait",
                    subject_id="w",
                    body={"evidence": ["No actual answer"]},
                )
            )
        self.send(
            self.message(
                "answer",
                "answer",
                sender="beta",
                parent="question",
                subject_type="question",
                subject_id="q",
                body={"answer": "Yes", "evidence": ["User requirement"]},
            )
        )
        self.send(
            self.message(
                "resumed",
                "resume",
                parent="wait",
                causes=["wait", "answer"],
                subject_type="wait",
                subject_id="w",
                body={"evidence": ["Requirement resolves the question"]},
            )
        )
        self.assertEqual(scan(self.settings).history["wait"].body["deadline"], deadline)

    def test_command_timeout_does_not_impose_a_dependency_deadline(self) -> None:
        raw = self.message(
            "wait",
            "await",
            subject_type="wait",
            subject_id="w",
            body={
                "blocks": ["write"],
                "reason": "Need decision",
                "deadline": None,
                "condition": {
                    "kind": "agree",
                    "subject": {"type": "decision", "id": "d"},
                    "from": "beta",
                    "target": None,
                },
            },
        )
        Message.parse(raw, self.settings)
        Message.parse(raw, replace(self.settings, wait_timeout_seconds=None))

    def test_receipt_is_not_acceptance_and_cannot_form_loop(self) -> None:
        self.request()
        self.send(
            self.message(
                "received",
                "receipt",
                sender="beta",
                parent="request-1",
                subject_type="task",
                subject_id="review",
            )
        )
        with self.assertRaisesRegex(DuetError, "loops"):
            self.send(
                self.message(
                    "loop",
                    "receipt",
                    parent="received",
                    subject_type="task",
                    subject_id="review",
                )
            )
        self.assertNotIn(
            "accept", [message.kind for message in scan(self.settings).messages]
        )

    def test_checkpoint_is_explicit_and_cannot_impersonate_peer(self) -> None:
        self.ready()
        received = json.loads(self.cli("receive").stdout)
        self.assertIsNone(received["checkpoint"])
        data = self.checkpoint()
        data["processed"] = ["alpha-ready", "beta-ready"]
        data["outgoing"] = ["alpha-ready"]
        save_checkpoint(self.settings, "alpha", "session-alpha", encoded(data))
        received = json.loads(self.cli("receive").stdout)
        self.assertEqual(received["checkpoint"]["processed"], data["processed"])
        with self.assertRaisesRegex(DuetError, "identity"):
            save_checkpoint(self.settings, "beta", "session-beta", encoded(data))
        self.assertFalse(
            (self.settings.board / "checkpoints" / "goal-1" / "beta.json").exists()
        )

    def test_old_checkpoint_allows_new_messages_on_recovery(self) -> None:
        self.ready()
        data = self.checkpoint()
        data["outgoing"] = ["alpha-ready"]
        save_checkpoint(self.settings, "alpha", "session-alpha", encoded(data))
        self.send(self.message("later", "inform", body={"text": "After checkpoint"}))
        validate_checkpoint(self.settings, "alpha", data)
        recovered = json.loads(self.cli("receive").stdout)
        self.assertIsNone(recovered["checkpoint_error"])
        self.assertEqual(len(recovered["messages"]), 3)

    def test_corrupt_checkpoint_is_reported_for_replay(self) -> None:
        path = self.settings.board / "checkpoints" / "goal-1" / "alpha.json"
        path.parent.mkdir(parents=True)
        path.write_text('{"goal":"wrong"}')
        received = json.loads(self.cli("receive").stdout)
        self.assertIsNone(received["checkpoint"])
        self.assertIsNotNone(received["checkpoint_error"])

    def test_limits_fail_without_partial_history(self) -> None:
        raw = self.message("large", "inform", body={"text": "Finding"})
        with self.assertRaises(DuetError):
            Message.parse(raw, replace(self.settings, max_message_bytes=len(raw) - 1))
        self.ready()
        with self.assertRaisesRegex(DuetError, "no partial"):
            scan(replace(self.settings, max_messages=1))

    def test_duplicate_keys_unknown_fields_and_invalid_ids_are_rejected(self) -> None:
        invalid = [
            b'{"goal":"first","goal":"other"}',
            self.message("../escape", "inform", body={"text": "Unsafe ID"}),
            self.message("unknown", "invented", body={}),
            self.message(
                "field", "inform", body={"text": "Finding", "unexpected": "value"}
            ),
        ]
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(DuetError):
                Message.parse(raw, self.settings)

    def test_invalid_numbers_return_json_diagnostics(self) -> None:
        for raw in (
            b'{"number":1e9999}',
            b'{"number":NaN}',
            b'{"number":' + b"9" * 5000 + b"}",
        ):
            result = self.cli("validate", raw=raw)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stderr)["error"], "DuetError")

    def test_symlink_records_and_sender_directories_are_refused(self) -> None:
        self.ready()
        sender = self.settings.board / "messages" / "goal-1" / "alpha"
        (sender / "linked.json").symlink_to(self.config)
        self.assertIn("linked", scan(self.settings).invalid)
        other = replace(self.settings, board=self.root / "other-board")
        goal = other.board / "messages" / "goal-1"
        goal.mkdir(parents=True)
        (goal / "alpha").symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(DuetError, "symlink"):
            scan(other)

    def test_publication_failure_does_not_fall_back_to_copy(self) -> None:
        raw = self.message("unsupported", "inform", body={"text": "Finding"})
        with (
            patch(
                "board.os.link",
                side_effect=OSError(errno.EXDEV, "Different filesystems"),
            ),
            self.assertRaises(OSError),
        ):
            self.send(raw)
        self.assertFalse(scan(self.settings).messages)
        self.assertFalse(list(self.root.glob("duet-*")))

    def test_missing_tmpdir_uses_python_temporary_directory(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("settings.tempfile.gettempdir", return_value=str(self.root)),
        ):
            self.send(self.message("no-temp", "inform", body={"text": "Finding"}))
        self.assertIn("no-temp", scan(self.settings).history)

    def test_cli_help_and_json_error_boundary(self) -> None:
        for command in (
            [],
            ["setup"],
            ["join"],
            ["status"],
            ["send"],
            ["retry"],
            ["complete"],
            ["finish"],
            ["ack"],
            ["publish"],
            ["receive"],
            ["wait"],
            ["validate"],
            ["checkpoint"],
        ):
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "duet.py"), *command, "--help"],
                capture_output=True,
                check=False,
                timeout=self.settings.wait_timeout_seconds,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        invalid = self.cli("validate", raw=b"not JSON")
        self.assertEqual(invalid.returncode, 1)
        self.assertEqual(json.loads(invalid.stderr)["error"], "DuetError")
        self.assertFalse(invalid.stdout)

    def test_whole_task_review_exchange(self) -> None:
        self.request()
        for key, kind, sender, parent, body in (
            ("accepted", "accept", "beta", "request-1", {}),
            (
                "result-v1",
                "submit",
                "beta",
                "accepted",
                {
                    "summary": "Review complete",
                    "artifacts": [],
                    "evidence": ["All criteria addressed"],
                },
            ),
            (
                "revision-needed",
                "reject",
                "alpha",
                "result-v1",
                {
                    "reason": "Missing empty-input case",
                    "criteria": ["Resolve ambiguity"],
                },
            ),
            (
                "result-v2",
                "submit",
                "beta",
                "revision-needed",
                {
                    "summary": "Empty input reviewed",
                    "artifacts": [],
                    "evidence": ["Requirement checked"],
                },
            ),
            (
                "verified",
                "verify",
                "alpha",
                "result-v2",
                {"evidence": ["Acceptance criteria independently checked"]},
            ),
        ):
            self.send(
                self.message(
                    key,
                    kind,
                    sender=sender,
                    parent=parent,
                    subject_type="task",
                    subject_id="review",
                    body=body,
                )
            )
        snapshot = scan(self.settings)
        self.assertEqual(snapshot.messages[-1].id, "verified")
        self.assertFalse(snapshot.conflicts)
        self.assertFalse(snapshot.invalid)


if __name__ == "__main__":
    unittest.main()
