# Copyright 2026 Adam Gogacz
# SPDX-License-Identifier: Apache-2.0

"""Exercise context-efficient inboxes and waits without consuming messages."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from delivery import receive, wait_for_messages
from settings import DuetError, ExitCode, encoded
from test_duet import BoardFixture


@dataclass
class FakeClock:
    """Advance elapsed time deterministically and publish events between scans."""

    elapsed: float = 0
    on_sleep: Callable[[], None] | None = None
    base: datetime = datetime(2026, 1, 1, tzinfo=UTC)

    def monotonic(self) -> float:
        """Return test elapsed time."""
        return self.elapsed

    def utcnow(self) -> datetime:
        """Keep test wall time aligned with the injected elapsed clock."""
        return self.base + timedelta(seconds=self.elapsed)

    def sleep(self, seconds: float) -> None:
        """Run one scheduled peer action without a real sleep."""
        self.elapsed += seconds
        action, self.on_sleep = self.on_sleep, None
        if action is not None:
            action()


class DeliveryTests(BoardFixture):
    """Verify delivery semantics through both pure clock control and real CLI processes."""

    def question(self) -> None:
        """Publish a question after the readiness exchange."""
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

    def answer(self) -> None:
        """Publish the exact substantive answer sought by a correlated wait."""
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

    def save_processed(self, ids: list[str]) -> None:
        """Acknowledge only explicit processing choices, retaining all outgoing IDs."""
        data = self.checkpoint()
        data["processed"] = ids
        data["outgoing"] = [
            message.id
            for message in receive(self.settings, "alpha").snapshot.messages
            if message.sender == "alpha"
        ]
        result = self.cli("checkpoint", raw=encoded(data))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unread_output_is_a_peer_delta_and_does_not_consume(self) -> None:
        self.ready()
        self.save_processed(["alpha-ready", "beta-ready"])
        self.send(
            self.message(
                "finding", "inform", sender="beta", body={"text": "New result"}
            )
        )
        first = json.loads(self.cli("receive", extra=("--unread",)).stdout)
        second = json.loads(self.cli("receive", extra=("--unread",)).stdout)
        self.assertEqual([message["id"] for message in first["messages"]], ["finding"])
        self.assertEqual(first, second)
        self.assertNotIn("checkpoint", first)
        self.save_processed(["alpha-ready", "beta-ready", "finding"])
        self.assertFalse(
            json.loads(self.cli("receive", extra=("--unread",)).stdout)["messages"]
        )
        self.assertEqual(len(json.loads(self.cli("receive").stdout)["messages"]), 3)

    def test_wait_times_out_without_creating_or_acknowledging_anything(self) -> None:
        clock = FakeClock()
        result = wait_for_messages(self.settings, "alpha", timeout=0.25, clock=clock)
        self.assertEqual(result.code, ExitCode.TIMEOUT)
        self.assertEqual(clock.elapsed, 0.25)
        self.assertFalse(self.settings.board.exists())

    def test_wait_sleeps_in_process_then_delivers_peer_event(self) -> None:
        clock = FakeClock(
            on_sleep=lambda: self.send(
                self.message(
                    "arrival", "inform", sender="beta", body={"text": "Available now"}
                )
            )
        )
        result = wait_for_messages(self.settings, "alpha", timeout=3, clock=clock)
        self.assertEqual(result.code, ExitCode.OK)
        self.assertEqual(clock.elapsed, self.settings.poll_interval_seconds)
        self.assertEqual(
            [message.id for message in result.received.unread], ["arrival"]
        )
        self.assertFalse((self.settings.board / "checkpoints").exists())

    def test_correlated_wait_ignores_receipts_and_unrelated_information(self) -> None:
        self.question()
        self.send(
            self.message(
                "receipt",
                "receipt",
                sender="beta",
                parent="question",
                subject_type="question",
                subject_id="q",
            )
        )
        self.send(
            self.message(
                "unrelated", "inform", sender="beta", body={"text": "Other progress"}
            )
        )
        clock = FakeClock(on_sleep=self.answer)
        result = wait_for_messages(
            self.settings, "alpha", reply_to="question", timeout=3, clock=clock
        )
        self.assertGreater(clock.elapsed, 0)
        self.assertEqual([message.id for message in result.matched], ["answer"])
        payload = result.as_json(self.settings)
        self.assertEqual(payload["matched"], ["answer"])
        self.assertEqual(
            sum(message["id"] == "answer" for message in payload["messages"]), 1
        )

    def test_processed_reply_still_satisfies_a_retried_wait(self) -> None:
        self.question()
        self.answer()
        self.save_processed(
            [
                message.id
                for message in receive(self.settings, "alpha").snapshot.messages
            ]
        )
        result = wait_for_messages(
            self.settings, "alpha", reply_to="question", clock=FakeClock()
        )
        self.assertEqual(result.code, ExitCode.OK)
        self.assertFalse(result.received.unread)
        self.assertEqual(
            [message["id"] for message in result.as_json(self.settings)["messages"]],
            ["answer"],
        )

    def test_crossed_questions_interrupt_both_waiters(self) -> None:
        self.question()
        self.send(
            self.message(
                "peer-question",
                "ask",
                sender="beta",
                subject_type="question",
                subject_id="peer-q",
                body={"question": "Can you clarify the input?"},
            )
        )
        first = wait_for_messages(
            self.settings, "alpha", reply_to="question", clock=FakeClock()
        )
        second = wait_for_messages(
            self.settings, "beta", reply_to="peer-question", clock=FakeClock()
        )
        self.assertEqual(first.code, ExitCode.ATTENTION)
        self.assertEqual(second.code, ExitCode.ATTENTION)
        self.assertEqual(first.status, "peer-attention")
        self.assertFalse((self.settings.board / "checkpoints").exists())

    def test_peer_rejection_is_an_explicit_process_outcome(self) -> None:
        self.request()
        self.send(
            self.message(
                "declined",
                "decline",
                sender="beta",
                parent="request-1",
                subject_type="task",
                subject_id="review",
                body={"reason": "Scope needs revision"},
            )
        )
        result = wait_for_messages(
            self.settings, "alpha", reply_to="request-1", clock=FakeClock()
        )
        self.assertEqual(result.code, ExitCode.REJECTED)
        self.assertEqual(result.status, "rejected")

    def test_durable_wait_uses_its_original_deadline(self) -> None:
        self.question()
        self.send(
            self.message(
                "durable-wait",
                "await",
                subject_type="wait",
                subject_id="w",
                body={
                    "blocks": ["integration"],
                    "reason": "Requirement needed",
                    "deadline": "2026-01-01T00:00:02Z",
                    "condition": {
                        "kind": "answer",
                        "subject": {"type": "question", "id": "q"},
                        "from": "beta",
                        "target": "question",
                    },
                },
            )
        )
        clock = FakeClock()
        first = wait_for_messages(
            self.settings, "alpha", wait_id="durable-wait", clock=clock
        )
        second = wait_for_messages(
            self.settings, "alpha", wait_id="durable-wait", clock=clock
        )
        self.assertEqual(first.code, ExitCode.TIMEOUT)
        self.assertEqual(second.code, ExitCode.TIMEOUT)
        self.assertEqual(first.status, "deadline-expired")
        self.assertEqual(second.status, "deadline-expired")
        self.assertEqual(clock.elapsed, 2)
        self.assertEqual(
            receive(self.settings, "alpha")
            .snapshot.history["durable-wait"]
            .body["deadline"],
            "2026-01-01T00:00:02Z",
        )

    def test_durable_wait_wakes_only_for_its_condition(self) -> None:
        self.question()
        self.send(
            self.message(
                "durable-wait",
                "await",
                subject_type="wait",
                subject_id="w",
                body={
                    "blocks": ["integration"],
                    "reason": "Requirement needed",
                    "deadline": "2026-01-01T00:00:05Z",
                    "condition": {
                        "kind": "answer",
                        "subject": {"type": "question", "id": "q"},
                        "from": "beta",
                        "target": "question",
                    },
                },
            )
        )
        clock = FakeClock(on_sleep=self.answer)
        result = wait_for_messages(
            self.settings, "alpha", wait_id="durable-wait", clock=clock
        )
        self.assertEqual(result.code, ExitCode.OK)
        self.assertEqual(result.matched[0].id, "answer")
        self.assertNotIn(
            "resume", [message.kind for message in result.received.snapshot.messages]
        )

    def test_wait_requires_a_finite_command_budget_and_own_target(self) -> None:
        indefinite = replace(self.settings, wait_timeout_seconds=None)
        with self.assertRaisesRegex(DuetError, "finite"):
            wait_for_messages(indefinite, "alpha", clock=FakeClock())
        with self.assertRaisesRegex(DuetError, "available message"):
            wait_for_messages(
                self.settings, "alpha", reply_to="not-published", clock=FakeClock()
            )
        for timeout in (float("nan"), float("inf"), 0, -1):
            with self.subTest(timeout=timeout), self.assertRaises(DuetError):
                wait_for_messages(
                    self.settings, "alpha", timeout=timeout, clock=FakeClock()
                )

    def test_corrupt_checkpoint_requires_recovery_before_wait_or_delta(self) -> None:
        path = self.settings.board / "checkpoints" / "goal-1" / "alpha.json"
        path.parent.mkdir(parents=True)
        path.write_text('{"goal":"wrong"}')
        result = wait_for_messages(self.settings, "alpha", clock=FakeClock())
        self.assertEqual(result.code, ExitCode.ERROR)
        self.assertEqual(result.status, "recovery-required")
        self.assertNotEqual(self.cli("receive", extra=("--unread",)).returncode, 0)
        self.assertIsNotNone(json.loads(self.cli("receive").stdout)["checkpoint_error"])

    def test_wait_command_has_no_background_worker_or_checkpoint_side_effect(
        self,
    ) -> None:
        self.question()
        command = [
            sys.executable,
            str(SCRIPTS / "duet.py"),
            "wait",
            "--config",
            str(self.config),
            "--participant",
            "alpha",
            "--reply-to",
            "question",
        ]
        with subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        ) as process:
            self.answer()
            stdout, stderr = process.communicate(
                timeout=self.settings.wait_timeout_seconds
            )
        self.assertEqual(process.returncode, ExitCode.OK, stderr)
        self.assertEqual(json.loads(stdout)["matched"], ["answer"])
        self.assertFalse((self.settings.board / "checkpoints").exists())

    def test_wait_cli_timeout_and_argument_exclusion(self) -> None:
        result = self.cli("wait", extra=("--timeout-seconds", "0.01"))
        self.assertEqual(result.returncode, ExitCode.TIMEOUT)
        self.assertEqual(json.loads(result.stdout)["status"], "timeout")
        invalid = self.cli("wait", extra=("--reply-to", "a", "--for-wait", "b"))
        self.assertNotEqual(invalid.returncode, 0)

    def test_ordinary_timeout_can_be_followed_by_a_normal_reply_without_new_protocol_acts(
        self,
    ) -> None:
        self.question()
        first = wait_for_messages(
            self.settings, "alpha", reply_to="question", timeout=1, clock=FakeClock()
        )
        self.assertEqual(first.status, "timeout")
        second = wait_for_messages(
            self.settings,
            "alpha",
            reply_to="question",
            timeout=3,
            clock=FakeClock(on_sleep=self.answer),
        )
        self.assertEqual(second.code, ExitCode.OK)
        self.assertNotIn(
            "alert", [message.kind for message in second.received.snapshot.messages]
        )

    def test_dependency_without_a_deadline_survives_multiple_bounded_commands(
        self,
    ) -> None:
        self.question()
        self.send(
            self.message(
                "durable-wait",
                "await",
                subject_type="wait",
                subject_id="w",
                body={
                    "blocks": ["integration"],
                    "reason": "Await requirement",
                    "deadline": None,
                    "condition": {
                        "kind": "answer",
                        "subject": {"type": "question", "id": "q"},
                        "from": "beta",
                        "target": "question",
                    },
                },
            )
        )
        first = wait_for_messages(
            self.settings, "alpha", wait_id="durable-wait", timeout=1, clock=FakeClock()
        )
        second = wait_for_messages(
            self.settings,
            "alpha",
            wait_id="durable-wait",
            timeout=3,
            clock=FakeClock(on_sleep=self.answer),
        )
        self.assertEqual(first.status, "timeout")
        self.assertEqual(second.status, "matched")
        self.assertIsNone(
            second.received.snapshot.history["durable-wait"].body["deadline"]
        )

    def test_conflicting_replies_are_not_a_successful_wakeup(self) -> None:
        self.request()
        for key, kind, body in (
            ("accepted", "accept", {}),
            ("declined", "decline", {"reason": "Changed mind"}),
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
        result = wait_for_messages(
            self.settings, "alpha", reply_to="request-1", clock=FakeClock()
        )
        self.assertEqual(result.code, ExitCode.ERROR)
        self.assertEqual(result.status, "conflicting-replies")
