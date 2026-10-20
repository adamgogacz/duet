# Copyright 2026 Adam Gogacz
# SPDX-License-Identifier: Apache-2.0

"""Exercise the simple interface without hand-built envelopes or copied digests."""

from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from actions import Connection, retry, send
from artifacts import capture, verify
from board import atomic_write, publish, scan
from completion import complete
from delivery import acknowledge, receive
from enrollment import join, find_run
from messages import Kind
from settings import DuetError, ExitCode
from test_duet import BoardFixture
from test_delivery import FakeClock


class InteractionFixture(BoardFixture):
    """Create independent retained identities with short configurable test waits."""

    def setUp(self) -> None:
        super().setUp()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        first = join(
            self.workspace,
            "Review the project and agree a report.",
            options={"wait_timeout_seconds": 0.1, "poll_interval_seconds": 0.01},
        )
        second = join(self.workspace, "Review the project and agree a report.")
        self.peers = tuple(
            Connection(
                Path(item["settings_path"]), item["participant"], item["session"]
            )
            for item in (first, second)
        )
        self.handles = tuple(peer.save() for peer in self.peers)
        self.report = self.root / "report.md"
        self.report.write_text("Reviewed report revision one.\n")

    def session_status(self) -> dict:
        """Observe this session's consensus without a project-wide active run."""
        settings = self.peers[0].settings
        run = find_run(settings.board, settings.goal)
        return {
            "state": "complete" if run.complete else "unfinished",
            "finished": [
                member.participant for member in run.members if member.finished
            ],
        }

    def plan(self) -> dict:
        """One accepted plan establishes both peers' bounded review responsibilities."""
        for peer in self.peers:
            send(peer, Kind.READY, text="Ready for the shared review")
        proposal = send(
            self.peers[0],
            Kind.PROPOSE,
            scope=["work-division"],
            text="A reviews API, B reviews storage; both cross-check. A writes the shared report; A proposes joint completion. Cover the requested PR and record limitations.",
        )
        return send(self.peers[1], Kind.AGREE, reply_to=proposal["id"])

    def cli(
        self, index: int, *args: str, raw: str | None = None
    ) -> subprocess.CompletedProcess:
        """Exercise the real client interface using only a retained connection."""
        return subprocess.run(
            [
                sys.executable,
                "-B",
                str(SCRIPTS / "duet.py"),
                *args,
                "--connection",
                str(self.handles[index]),
            ],
            input=raw,
            capture_output=True,
            text=True,
            check=False,
            timeout=self.settings.wait_timeout_seconds,
        )


class ActionTests(InteractionFixture):
    """Protect retry, artifact, and processing semantics while removing manual fields."""

    def test_counterplan_gets_its_required_causal_link_without_an_envelope(
        self,
    ) -> None:
        for peer in self.peers:
            send(peer, Kind.READY, text="Ready")
        first = send(
            self.peers[0],
            Kind.PROPOSE,
            scope=["work-division"],
            text="A covers API, B covers storage",
        )
        counter = send(
            self.peers[1],
            Kind.PROPOSE,
            scope=["work-division"],
            text="B covers API, A covers storage; otherwise keep the same criteria and artifact",
        )
        self.assertIn(first["id"], counter["message"]["causes"])
        accepted = send(self.peers[0], Kind.AGREE, reply_to=counter["id"])
        self.assertEqual(accepted["message"]["reply_to"], counter["id"])

    def test_plain_text_send_and_retry_keep_one_generated_identity(self) -> None:
        result = send(self.peers[0], text="A candidate finding")
        replay = retry(Connection.load(self.handles[0]))
        self.assertTrue(replay["duplicate"])
        self.assertEqual(result["id"], replay["id"])
        cli_replay = self.cli(0, "retry")
        self.assertEqual(cli_replay.returncode, 0, cli_replay.stderr)
        self.assertEqual(json.loads(cli_replay.stdout)["id"], result["id"])
        self.assertEqual(len(scan(self.peers[0].settings).messages), 1)
        message = result["message"]
        self.assertEqual(message["from"], "peer-a")
        self.assertEqual(message["body"], {"text": "A candidate finding"})
        self.assertEqual(message["goal"], self.peers[0].settings.goal)

    def test_lost_result_after_commit_retries_original_bytes(self) -> None:
        def lost_result(*args, **kwargs):
            result = publish(*args, **kwargs)
            if not kwargs.get("dry_run", False):
                raise OSError("publication succeeded but result was lost")
            return result

        with (
            patch("actions.publish", side_effect=lost_result),
            self.assertRaises(OSError),
        ):
            send(self.peers[0], text="Publish once despite an uncertain result")
        committed = scan(self.peers[0].settings).messages
        replay = retry(self.peers[0])
        self.assertEqual(len(committed), 1)
        self.assertEqual(replay["id"], committed[0].id)
        self.assertTrue(replay["duplicate"])
        self.assertEqual(
            replay["message"]["body"]["text"],
            "Publish once despite an uncertain result",
        )

    def test_failure_before_commit_blocks_new_send_until_retry(self) -> None:
        def fail_before_commit(*args, **kwargs):
            if not kwargs.get("dry_run", False):
                raise OSError("simulated I/O failure")
            return publish(*args, **kwargs)

        with (
            patch("actions.publish", side_effect=fail_before_commit),
            self.assertRaises(OSError),
        ):
            send(self.peers[0], text="Retain this prepared operation")
        with self.assertRaisesRegex(DuetError, "uncertain outcome"):
            send(self.peers[0], text="Do not overwrite the uncertain operation")
        retried = retry(self.peers[0])
        self.assertEqual(
            retried["message"]["body"]["text"], "Retain this prepared operation"
        )
        self.assertEqual(len(scan(self.peers[0].settings).messages), 1)

    def test_immutable_capture_survives_origin_edit_and_detects_stale_approval(
        self,
    ) -> None:
        result = send(
            self.peers[0], text="Review the attached evidence", artifacts=[self.report]
        )
        reference = result["message"]["body"]["artifacts"][0]
        original = self.report.read_bytes()
        self.report.write_text("A changed working draft.\n")
        captured = verify(self.peers[0].settings, reference)
        self.assertEqual(captured.snapshot.read_bytes(), original)
        with self.assertRaisesRegex(DuetError, "changed since proposal"):
            verify(self.peers[0].settings, reference, current_origin=True)

    def test_reply_derives_subject_and_acknowledges_only_its_target(self) -> None:
        self.plan()
        before = receive(self.peers[1].settings, "peer-b").checkpoint["processed"]
        question = send(self.peers[0], Kind.ASK, text="What supports this finding?")
        answer = send(
            self.peers[1],
            Kind.ANSWER,
            reply_to=question["id"],
            text="The reproduction demonstrates it.",
            evidence=["Focused reproduction output"],
        )
        self.assertEqual(answer["message"]["subject"], question["message"]["subject"])
        self.assertIn(question["id"], answer["message"]["causes"])
        after = receive(self.peers[1].settings, "peer-b").checkpoint["processed"]
        self.assertEqual(set(after), set(before) | {question["id"]})

    def test_receive_handle_does_not_acknowledge_unprocessed_messages(self) -> None:
        message = send(self.peers[0], text="Unread information")
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        result = self.cli(1, "receive", "--unread")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["messages"][0]["id"], message["id"])
        self.assertEqual(
            {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}, before
        )

    def test_approval_copies_target_revision_and_rejects_changed_origin(self) -> None:
        self.plan()
        offer = send(
            self.peers[0],
            Kind.PROPOSE,
            text="Review this precise report revision",
            scope=["report-review"],
            artifacts=[self.report],
        )
        original = self.report.read_bytes()
        self.report.write_text("Changed after submission\n")
        with self.assertRaisesRegex(DuetError, "changed since proposal"):
            send(self.peers[1], Kind.AGREE, reply_to=offer["id"])
        self.report.write_bytes(original)
        approved = send(self.peers[1], Kind.AGREE, reply_to=offer["id"])
        self.assertEqual(
            approved["message"]["body"]["artifacts"],
            offer["message"]["body"]["artifacts"],
        )

    def test_dry_run_has_no_outbox_or_artifact_side_effects(self) -> None:
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        result = send(
            self.peers[0], text="Preview only", artifacts=[self.report], dry_run=True
        )
        self.assertTrue(result["dry_run"])
        self.assertEqual(
            {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}, before
        )

    def test_bounded_capture_and_snapshot_integrity_are_checked(self) -> None:
        settings = replace(self.peers[0].settings, max_artifact_bytes=1)
        with self.assertRaisesRegex(DuetError, "limit"):
            capture(settings, self.report)
        artifact = capture(self.peers[0].settings, self.report)
        artifact.snapshot.write_text("Corrupted captured copy\n")
        with self.assertRaisesRegex(DuetError, "bytes changed"):
            verify(self.peers[0].settings, artifact.reference())

    def test_connection_identity_cannot_be_overridden(self) -> None:
        result = self.cli(0, "receive", "--participant", "peer-b")
        self.assertEqual(result.returncode, 1)
        self.assertIn("do not override", json.loads(result.stderr)["message"])

    def test_corrupt_journal_is_not_replaced_by_a_new_send(self) -> None:
        send(self.peers[0], text="Original operation")
        settings = self.peers[0].settings
        last = (
            settings.board
            / "outbox"
            / settings.goal
            / "peer-a"
            / self.peers[0].session
            / "last.json"
        )
        last.write_text('{"id":"unknown","state":"corrupt"}')
        with self.assertRaisesRegex(DuetError, "invalid retry journal"):
            send(self.peers[0], text="Must not lose the corrupt journal")
        self.assertIn('"corrupt"', last.read_text())

    def test_join_cli_returns_a_retained_handle_without_identity_transfer(self) -> None:
        workspace = self.root / "another-project"
        workspace.mkdir()
        command = [
            sys.executable,
            "-B",
            str(SCRIPTS / "duet.py"),
            "join",
            "--workspace",
            str(workspace),
        ]
        results = [
            subprocess.run(
                command,
                input="Review this second project.",
                text=True,
                capture_output=True,
                check=False,
                timeout=self.settings.wait_timeout_seconds,
            )
            for _ in range(3)
        ]
        for result in results:
            self.assertEqual(result.returncode, 0, result.stderr)
        data = [json.loads(result.stdout) for result in results]
        connections = [Connection.load(Path(item["connection_path"])) for item in data]
        self.assertEqual(connections[0].settings_path, connections[1].settings_path)
        self.assertNotEqual(connections[1].settings_path, connections[2].settings_path)
        self.assertEqual(
            [item["status"] for item in data], ["waiting", "paired", "waiting"]
        )

    def test_reply_result_lost_before_ack_is_retried_without_duplicate_reply(
        self,
    ) -> None:
        self.plan()
        question = send(self.peers[0], Kind.ASK, text="Which evidence supports this?")
        with (
            patch(
                "actions.acknowledge",
                side_effect=OSError("lost before checkpoint write"),
            ),
            self.assertRaises(OSError),
        ):
            send(
                self.peers[1],
                Kind.ANSWER,
                reply_to=question["id"],
                text="The retained reproduction",
                evidence=["Output from the focused check"],
            )
        replay = retry(self.peers[1])
        self.assertTrue(replay["duplicate"])
        history = scan(self.peers[0].settings).messages
        self.assertEqual(sum(message.kind == Kind.ANSWER for message in history), 1)
        self.assertIn(
            question["id"],
            receive(self.peers[1].settings, "peer-b").checkpoint["processed"],
        )

    def test_preparation_failure_does_not_misreport_an_old_retry_as_the_new_send(
        self,
    ) -> None:
        original = send(self.peers[0], text="Earlier committed message")

        def fail_journal(path, *args, **kwargs):
            if path.name == "last.json":
                raise OSError("journal update failed before publication")
            return atomic_write(path, *args, **kwargs)

        with (
            patch("actions.atomic_write", side_effect=fail_journal),
            self.assertRaisesRegex(DuetError, "publication did not start.*rerun send"),
        ):
            send(self.peers[0], text="New message whose preparation failed")
        self.assertEqual(retry(self.peers[0])["id"], original["id"])
        send(self.peers[0], text="New message whose preparation failed")
        self.assertEqual(len(scan(self.peers[0].settings).messages), 2)

    def test_retry_cannot_publish_a_stale_prepared_approval(self) -> None:
        self.plan()
        offer = send(
            self.peers[0],
            Kind.PROPOSE,
            text="Review this report",
            scope=["report-review"],
            artifacts=[self.report],
        )

        def fail_before_commit(*args, **kwargs):
            if not kwargs.get("dry_run", False):
                raise OSError("publication not committed")
            return publish(*args, **kwargs)

        with (
            patch("actions.publish", side_effect=fail_before_commit),
            self.assertRaises(OSError),
        ):
            send(self.peers[1], Kind.AGREE, reply_to=offer["id"])
        self.report.write_text("New bytes before approval was committed\n")
        with self.assertRaisesRegex(DuetError, "changed since proposal"):
            retry(self.peers[1])
        self.assertFalse(
            any(
                message.kind == Kind.AGREE and message.reply_to == offer["id"]
                for message in scan(self.peers[0].settings).messages
            )
        )


class ConsensusTests(InteractionFixture):
    """Keep explicit joint consensus while automating closing acknowledgments."""

    def completion(
        self, connection: Connection, **options: object
    ) -> tuple[dict, ExitCode]:
        """Test consensus deterministically; separate subprocess cases use real clocks."""
        return complete(connection, clock=FakeClock(), **options)

    def test_individual_done_messages_do_not_complete_the_goal(self) -> None:
        self.plan()
        for peer in self.peers:
            send(peer, text="My own part is done")
        self.assertEqual(self.session_status()["state"], "unfinished")
        with self.assertRaisesRegex(DuetError, "completion offer"):
            self.completion(self.peers[1])

    def test_objection_blocks_closure_until_a_revised_offer_is_explicitly_accepted(
        self,
    ) -> None:
        self.plan()
        offered, _ = self.completion(
            self.peers[0],
            propose=True,
            text="Whole review complete",
            artifacts=[self.report],
        )
        objection = send(
            self.peers[1],
            Kind.OBJECT,
            reply_to=offered["proposal"],
            text="Clarify the reported validation limits before closing",
        )
        self.assertEqual(self.session_status()["finished"], [])
        acknowledge(
            self.peers[0].settings, "peer-a", self.peers[0].session, [objection["id"]]
        )
        self.report.write_text("Reviewed report with validation limits clarified.\n")
        revised, _ = self.completion(
            self.peers[0],
            propose=True,
            text="Whole scope complete with the requested limits clarified",
            artifacts=[self.report],
        )
        self.assertNotEqual(revised["proposal"], offered["proposal"])
        self.completion(self.peers[1], agree=revised["proposal"])
        self.assertEqual(self.completion(self.peers[0])[0]["status"], "complete")

    def test_peer_must_explicitly_agree_to_whole_goal_completion(self) -> None:
        self.plan()
        offered, code = self.completion(
            self.peers[0],
            propose=True,
            text="Whole review scope and cross-checks are complete",
            artifacts=[self.report],
        )
        self.assertEqual(code, ExitCode.TIMEOUT)
        self.assertEqual(offered["status"], "awaiting-peer-agreement")
        review, code = self.completion(self.peers[1])
        self.assertEqual(code, ExitCode.OK)
        self.assertEqual(review["status"], "review-required")
        self.assertEqual(self.session_status()["finished"], [])

    def test_joint_consensus_closes_without_manual_finish_calls(self) -> None:
        self.plan()
        offered, _ = self.completion(
            self.peers[0],
            propose=True,
            text="The whole review is complete, with limits and disagreements recorded",
            artifacts=[self.report],
        )
        reviewed, _ = self.completion(self.peers[1], agree=offered["proposal"])
        self.assertIn(reviewed["status"], {"closing", "complete"})
        closed, code = self.completion(self.peers[0])
        self.assertEqual(code, ExitCode.OK)
        self.assertEqual(closed["status"], "complete")
        self.assertEqual(
            self.completion(Connection.load(self.handles[1]))[0]["status"], "complete"
        )
        self.assertEqual(self.session_status()["state"], "complete")
        kinds = [message.kind for message in scan(self.peers[0].settings).messages]
        self.assertNotIn(Kind.REQUEST, kinds)
        self.assertNotIn(Kind.ACCEPT, kinds)

    def test_revised_report_requires_a_fresh_proposal_and_consensus(self) -> None:
        self.plan()
        offered, _ = self.completion(
            self.peers[0],
            propose=True,
            text="Whole goal complete at revision one",
            artifacts=[self.report],
        )
        self.report.write_text("A materially revised report\n")
        with self.assertRaisesRegex(DuetError, "changed since proposal"):
            self.completion(self.peers[1], agree=offered["proposal"])
        self.assertEqual(self.session_status()["finished"], [])
        send(
            self.peers[0],
            Kind.WITHDRAW,
            reply_to=offered["proposal"],
            text="Report materially changed; requires renewed review",
        )
        revised, _ = self.completion(
            self.peers[0],
            propose=True,
            text="Whole goal complete at the revised report",
            artifacts=[self.report],
        )
        self.completion(self.peers[1], agree=revised["proposal"])
        self.assertEqual(self.completion(self.peers[0])[0]["status"], "complete")

    def test_late_peer_message_requires_handling_before_automatic_close(self) -> None:
        self.plan()
        offered, _ = self.completion(
            self.peers[0],
            propose=True,
            text="Whole goal complete",
            artifacts=[self.report],
        )
        late = send(self.peers[1], text="Additional context to assess before closing")
        self.completion(self.peers[1], agree=offered["proposal"])
        result, code = self.completion(self.peers[0])
        self.assertEqual(code, ExitCode.ATTENTION)
        self.assertIn(late["id"], [message["id"] for message in result["messages"]])
        self.assertEqual(self.session_status()["state"], "unfinished")
        acknowledge(
            self.peers[0].settings, "peer-a", self.peers[0].session, [late["id"]]
        )
        self.assertEqual(self.completion(self.peers[0])[0]["status"], "complete")

    def test_new_peer_information_is_handled_before_recording_consensus(self) -> None:
        self.plan()
        offered, _ = self.completion(
            self.peers[0],
            propose=True,
            text="Whole goal complete",
            artifacts=[self.report],
        )
        late = send(self.peers[0], text="Additional evidence published after the offer")
        result, code = self.completion(self.peers[1], agree=offered["proposal"])
        self.assertEqual(code, ExitCode.ATTENTION)
        self.assertEqual(result["messages"][0]["id"], late["id"])
        history = scan(self.peers[0].settings).messages
        self.assertEqual(
            sum(
                message.kind == Kind.AGREE and message.reply_to == offered["proposal"]
                for message in history
            ),
            0,
        )
        self.assertEqual(self.session_status()["finished"], [])

    def test_cli_pair_uses_only_handles_plain_text_and_file_paths(self) -> None:
        self.plan()
        proposal_result = self.cli(
            0,
            "complete",
            "--propose",
            "--text",
            "The shared goal and report are complete",
            "--artifact",
            str(self.report),
            "--timeout-seconds",
            "0.02",
        )
        self.assertEqual(
            proposal_result.returncode, ExitCode.TIMEOUT, proposal_result.stderr
        )
        proposal = json.loads(proposal_result.stdout)["proposal"]
        with ThreadPoolExecutor() as pool:
            a = pool.submit(self.cli, 0, "complete", "--timeout-seconds", "2")
            b = pool.submit(
                self.cli, 1, "complete", "--agree", proposal, "--timeout-seconds", "2"
            )
            results = [a.result(), b.result()]
        for result in results:
            self.assertEqual(result.returncode, ExitCode.OK, result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "complete")
