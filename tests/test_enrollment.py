# Copyright 2026 Adam Gogacz
# SPDX-License-Identifier: Apache-2.0

"""Verify automatic pairing, session isolation, leases, and explicit consensus."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from board import publish, scan
from delivery import receive, wait_for_messages
from enrollment import (
    Run,
    check_membership,
    find_run,
    finish,
    join,
    membership_paths,
    pairing_state,
    renew_invitation,
    session_status,
)
from pairing import read_lobby
from settings import DuetError, ExitCode, Settings, encoded
from test_delivery import FakeClock
from test_duet import BoardFixture


class EnrollmentTests(BoardFixture):
    """Use real isolated files and independent processes for enrollment races."""

    task = "Audit the shared scientific foundations and produce a report."

    def peers(self) -> tuple[dict, dict]:
        """Join two independent executions without preselected identities."""
        return join(self.root, self.task), join(self.root, self.task)

    def event(
        self,
        connection: dict,
        key: str,
        kind: str,
        *,
        subject_id: str,
        subject_type: str = "decision",
        parent: str | None = None,
        causes: list[str] | None = None,
        body: dict | None = None,
    ) -> None:
        """Publish under the assigned identity and current registry guard."""
        settings = Settings.load(Path(connection["settings_path"]))
        check_membership(settings, connection["participant"], connection["session"])
        if (
            kind == "propose"
            and body is not None
            and body.get("scope") == ["goal-completion"]
        ):
            from workflow import effect_fingerprint, gate

            current = gate(settings, scan(settings))
            body = {
                **body,
                "gate": current.agreement.id,
                "evidence": ["Fixture evidence for the agreed criterion"],
                "outcome": "complete",
                "effects": effect_fingerprint(scan(settings)),
            }
        raw = encoded(
            {
                "id": key,
                "goal": connection["goal"],
                "from": connection["participant"],
                "session": connection["session"],
                "to": connection["peer"],
                "subject": {"type": subject_type, "id": subject_id},
                "kind": kind,
                "reply_to": parent,
                "causes": causes
                if causes is not None
                else ([parent] if parent else []),
                "body": body if body is not None else {},
            }
        )
        publish(settings, connection["participant"], connection["session"], raw)

    def planned_audit(self) -> tuple[dict, dict]:
        """Enroll both peers, announce readiness, and agree the opening plan."""
        first, second = self.peers()
        settings = Settings.load(Path(first["settings_path"]))
        for key, connection in (("ready-a", first), ("ready-b", second)):
            self.event(
                connection,
                key,
                "ready",
                subject_type="goal",
                subject_id=connection["goal"],
                body={
                    "context_revision": "initial",
                    "summary": "Ready for shared audit",
                },
            )
        self.event(
            first,
            "division",
            "propose",
            subject_id=settings.initial_plan_id,
            causes=["ready-a", "ready-b"],
            body={
                "proposal": "Divide scientific and implementation review",
                "scope": ["work-division"],
                "criteria": ["Produce the agreed evidence-based result"],
                "stop_condition": "Stop incomplete if the bounded checks cannot establish the required evidence",
            },
        )
        self.event(
            second,
            "division-agreed",
            "agree",
            subject_id=settings.initial_plan_id,
            parent="division",
        )
        return first, second

    def completed_audit(self) -> tuple[dict, dict, str]:
        """Exercise the language through a bilateral completion decision."""
        first, second = self.planned_audit()
        self.event(
            first,
            "done-proposal",
            "propose",
            subject_id="completion",
            causes=["division-agreed"],
            body={
                "proposal": "Audit report and checks complete",
                "scope": ["goal-completion"],
            },
        )
        self.event(
            second,
            "done-agreed",
            "agree",
            subject_id="completion",
            parent="done-proposal",
        )
        return first, second, "done-agreed"

    def close(
        self, connection: dict, agreement: str, *, clock: FakeClock | None = None
    ) -> tuple[dict, ExitCode]:
        """Use a short deterministic command budget without changing task state."""
        return finish(
            Settings.load(Path(connection["settings_path"])),
            connection["participant"],
            connection["session"],
            agreement,
            timeout=1,
            clock=clock or FakeClock(),
        )

    def test_join_assigns_every_identifier_and_whitespace_does_not_split_the_task(
        self,
    ) -> None:
        first = join(self.root, self.task)
        second = join(
            self.root,
            "  Audit the shared scientific foundations\n and produce a report. ",
        )
        self.assertEqual(first["goal"], second["goal"])
        self.assertEqual(first["settings_path"], second["settings_path"])
        self.assertEqual(
            (first["participant"], second["participant"]), ("peer-a", "peer-b")
        )
        self.assertNotEqual(first["session"], second["session"])
        self.assertEqual(first["initial_proposer"], "peer-a")
        self.assertEqual(first["completion_proposer"], "peer-a")
        self.assertEqual(first["status"], "waiting")
        self.assertEqual(second["status"], "paired")

    def test_concurrent_processes_receive_distinct_slots_and_one_task(self) -> None:
        def start(_: int) -> dict:
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "duet.py"),
                    "join",
                    "--workspace",
                    str(self.root),
                ],
                input=self.task.encode(),
                capture_output=True,
                check=False,
                timeout=self.settings.wait_timeout_seconds,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

        with ThreadPoolExecutor() as pool:
            connections = list(pool.map(start, range(2)))
        self.assertEqual(
            {connection["participant"] for connection in connections},
            {"peer-a", "peer-b"},
        )
        self.assertEqual(len({connection["goal"] for connection in connections}), 1)

    def test_join_preview_does_not_create_a_board(self) -> None:
        preview = join(self.root, self.task, dry_run=True)
        self.assertTrue(preview["dry_run"])
        self.assertFalse(Path(preview["board"]).exists())

    def test_waiting_finisher_releases_the_registry_lock(self) -> None:
        first, second, agreement = self.completed_audit()
        clock = FakeClock(on_sleep=lambda: self.close(second, agreement))
        result, code = self.close(first, agreement, clock=clock)
        self.assertEqual(code, ExitCode.OK)
        self.assertTrue(result["complete"])

    def test_enrolled_identity_guard_blocks_unknown_and_finished_writers(self) -> None:
        first, second, agreement = self.completed_audit()
        settings = Settings.load(Path(first["settings_path"]))
        with self.assertRaisesRegex(DuetError, "active enrolled writer"):
            check_membership(settings, first["participant"], "unknown-session")
        self.close(first, agreement)
        # A provisional finisher can still assess and answer late information.
        check_membership(settings, first["participant"], first["session"])
        self.close(second, agreement)
        with self.assertRaisesRegex(DuetError, "active enrolled writer"):
            check_membership(settings, first["participant"], first["session"])

    def test_finish_requires_goal_completion_agreement_and_preview_is_read_only(
        self,
    ) -> None:
        first, second, agreement = self.completed_audit()
        with self.assertRaisesRegex(
            DuetError, "current unconflicted completion agreement"
        ):
            self.close(first, "division-agreed")
        settings = Settings.load(Path(first["settings_path"]))
        result, code = finish(
            settings,
            first["participant"],
            first["session"],
            agreement,
            timeout=1,
            clock=FakeClock(),
            dry_run=True,
        )
        self.assertEqual(code, ExitCode.OK)
        self.assertTrue(result["dry_run"])
        active, _ = membership_paths(settings.board)
        self.assertFalse(any(member.finished for member in Run.load(active).members))
        self.assertEqual(second["goal"], first["goal"])

    def test_project_discovery_lock_is_bounded(self) -> None:
        with (self.root / ".duet.lock").open("w") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(DuetError, "busy"):
                join(self.root, self.task, clock=FakeClock())
        self.assertFalse((self.root / ".duet.jsonl").exists())

    def propose_completion(
        self, peer: dict, key: str, *, causes: list[str] | None = None
    ) -> None:
        """Offer one exact completion revision after the agreed opening."""
        self.event(
            peer,
            key,
            "propose",
            subject_id=key,
            causes=causes if causes is not None else ["division-agreed"],
            body={
                "proposal": "Report revision and checks complete",
                "scope": ["goal-completion"],
            },
        )

    def test_simultaneous_completion_proposals_leave_only_the_designated_offer(
        self,
    ) -> None:
        peers = self.planned_audit()
        barrier = threading.Barrier(2)

        def propose(index: int) -> bool:
            barrier.wait(timeout=self.settings.wait_timeout_seconds)
            try:
                self.propose_completion(peers[index], f"completion-{index}")
                return True
            except DuetError as error:
                self.assertIn("designated completion proposer", str(error))
                return False

        with ThreadPoolExecutor() as pool:
            published = list(pool.map(propose, range(2)))
        self.assertEqual(published, [True, False])
        settings = Settings.load(Path(peers[0]["settings_path"]))
        self.assertIn("completion-0", scan(settings).history)
        self.assertNotIn("completion-1", scan(settings).history)
        self.assertFalse(scan(settings).conflicts)

    def test_pending_completion_is_reused_and_byte_identical_retry_is_safe(
        self,
    ) -> None:
        first, _ = self.planned_audit()
        self.propose_completion(first, "completion")
        self.propose_completion(first, "completion")
        with self.assertRaisesRegex(DuetError, "already pending"):
            self.propose_completion(first, "another-completion")

    def test_agreement_to_withdrawn_completion_is_rejected(self) -> None:
        first, second = self.planned_audit()
        self.propose_completion(first, "completion")
        self.event(
            first,
            "withdrawn",
            "withdraw",
            subject_id="completion",
            parent="completion",
            body={"reason": "Evidence needs correction"},
        )
        with self.assertRaisesRegex(DuetError, "already settled"):
            self.event(
                second,
                "stale-agree",
                "agree",
                subject_id="completion",
                parent="completion",
            )
        settings = Settings.load(Path(first["settings_path"]))
        self.assertFalse(scan(settings).conflicts)

    def test_withdrawal_after_agreement_is_rejected_and_both_finish_normally(
        self,
    ) -> None:
        first, second, agreement = self.completed_audit()
        with self.assertRaisesRegex(DuetError, "already settled"):
            self.event(
                first,
                "late-withdrawal",
                "withdraw",
                subject_id="completion",
                parent="done-proposal",
                body={"reason": "Yield to the other peer"},
            )
        with self.assertRaisesRegex(DuetError, "already agreed"):
            self.propose_completion(first, "another-completion")
        self.assertEqual(self.close(first, agreement)[1], ExitCode.TIMEOUT)
        self.assertEqual(self.close(second, agreement)[1], ExitCode.OK)

    def test_concurrent_agree_and_withdraw_commit_only_one_control_reply(self) -> None:
        first, second = self.planned_audit()
        self.propose_completion(first, "completion")
        barrier = threading.Barrier(2)

        def respond(index: int) -> bool:
            peer = (first, second)[index]
            kind = ("withdraw", "agree")[index]
            message = {
                "id": kind,
                "goal": peer["goal"],
                "from": peer["participant"],
                "session": peer["session"],
                "to": peer["peer"],
                "subject": {"type": "decision", "id": "completion"},
                "kind": kind,
                "reply_to": "completion",
                "causes": ["completion"],
                "body": {"reason": "Revise completion evidence"} if index == 0 else {},
            }
            barrier.wait(timeout=self.settings.wait_timeout_seconds)
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(SCRIPTS / "duet.py"),
                    "publish",
                    "--config",
                    peer["settings_path"],
                    "--participant",
                    peer["participant"],
                    "--session",
                    peer["session"],
                ],
                input=encoded(message),
                capture_output=True,
                check=False,
                timeout=self.settings.wait_timeout_seconds,
            )
            if result.returncode:
                self.assertIn("already settled", json.loads(result.stderr)["message"])
            return result.returncode == 0

        with ThreadPoolExecutor() as pool:
            accepted = list(pool.map(respond, range(2)))
        self.assertEqual(sum(accepted), 1)
        settings = Settings.load(Path(first["settings_path"]))
        self.assertFalse(scan(settings).conflicts)
        if accepted[0]:
            self.propose_completion(first, "revised-completion", causes=["withdraw"])
            self.event(
                second,
                "revised-agree",
                "agree",
                subject_id="revised-completion",
                parent="revised-completion",
            )
            agreement = "revised-agree"
        else:
            agreement = "agree"
        self.close(first, agreement)
        self.assertEqual(self.close(second, agreement)[1], ExitCode.OK)

    def test_other_peer_may_still_initiate_noncompletion_decisions(self) -> None:
        _, second = self.planned_audit()
        self.event(
            second,
            "independent-decision",
            "propose",
            subject_id="investigation",
            causes=["division-agreed"],
            body={
                "proposal": "Run an independent counterexample check",
                "scope": ["evidence-review"],
            },
        )

    def test_four_concurrent_processes_form_two_disjoint_pairs(self) -> None:
        def start(_: int) -> dict:
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(SCRIPTS / "duet.py"),
                    "join",
                    "--workspace",
                    str(self.root),
                ],
                input=self.task,
                text=True,
                capture_output=True,
                check=False,
                timeout=self.settings.wait_timeout_seconds,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

        with ThreadPoolExecutor(max_workers=4) as pool:
            peers = list(pool.map(start, range(4)))
        sessions = {peer["goal"] for peer in peers}
        self.assertEqual(len(sessions), 2)
        for goal in sessions:
            members = [peer for peer in peers if peer["goal"] == goal]
            self.assertEqual(
                {peer["participant"] for peer in members}, {"peer-a", "peer-b"}
            )
            self.assertEqual(len({peer["board"] for peer in members}), 1)
        self.assertEqual(len({peer["session"] for peer in peers}), 4)
        self.assertEqual(
            [
                record["state"]
                for record in map(
                    json.loads, (self.root / ".duet.jsonl").read_text().splitlines()
                )
            ],
            ["waiting", "paired", "waiting", "paired"],
        )

    def test_paired_sessions_do_not_block_or_receive_new_agents(self) -> None:
        first, second = self.planned_audit()
        old_board = Path(first["board"])
        before = {p: p.read_bytes() for p in old_board.rglob("*") if p.is_file()}
        third, fourth = self.peers()
        self.assertNotEqual(first["goal"], third["goal"])
        self.assertNotEqual(first["board"], third["board"])
        self.assertEqual(third["board"], fourth["board"])
        self.assertEqual(second["board"], first["board"])
        self.assertTrue(all(path.read_bytes() == raw for path, raw in before.items()))
        self.assertFalse(scan(Settings.load(Path(third["settings_path"]))).history)
        self.assertEqual(
            pairing_state(Settings.load(Path(first["settings_path"]))), "paired"
        )

    def test_close_of_one_session_cannot_finish_another(self) -> None:
        first, second, agreement = self.completed_audit()
        other, _ = self.peers()
        self.close(first, agreement)
        self.close(second, agreement)
        self.assertTrue(find_run(Path(first["board"]), first["goal"]).complete)
        self.assertFalse(find_run(Path(other["board"]), other["goal"]).complete)
        with self.assertRaises(DuetError):
            check_membership(
                Settings.load(Path(other["settings_path"])),
                first["participant"],
                first["session"],
            )

    def test_different_prompts_are_assessed_before_claiming(self) -> None:
        first = join(self.root, self.task)
        text = "Review scientific foundations; write an audit report."
        choice = join(self.root, text)
        self.assertEqual(choice["status"], "match-required")
        self.assertEqual(
            [item["session"] for item in choice["candidates"]], [first["goal"]]
        )
        second = join(self.root, text, match=first["goal"])
        self.assertEqual(first["board"], second["board"])
        self.assertEqual(read_lobby(self.root).invitations[0].peer_task, text)

    def test_distinct_tasks_can_wait_and_pair_simultaneously(self) -> None:
        first = join(self.root, self.task)
        choice = join(self.root, "Implement a different feature.")
        other = join(
            self.root, "Implement a different feature.", separate=choice["observation"]
        )
        peer = join(self.root, self.task)
        other_peer = join(self.root, "Implement a different feature.")
        self.assertEqual(first["goal"], peer["goal"])
        self.assertEqual(other["goal"], other_peer["goal"])
        self.assertNotEqual(first["goal"], other["goal"])

    def test_stale_semantic_choice_must_reconsider_new_invitations(self) -> None:
        first = join(self.root, self.task)
        choice = join(self.root, "Different task.")
        join(self.root, self.task)
        new = join(self.root, "Different task.")
        raced = join(self.root, "Different task.", separate=choice["observation"])
        # Exact matching always takes precedence over advertising another session.
        self.assertEqual(raced["status"], "paired")
        self.assertEqual(raced["goal"], new["goal"])
        stale_claim = join(self.root, self.task, match=first["goal"])
        self.assertEqual(stale_claim["status"], "match-required")

    def test_second_claim_race_has_one_winner_and_one_reassessment(self) -> None:
        first = join(self.root, self.task)
        with ThreadPoolExecutor() as pool:
            results = list(
                pool.map(
                    lambda _: join(
                        self.root, "Equivalent wording.", match=first["goal"]
                    ),
                    range(2),
                )
            )
        self.assertEqual(
            sorted(item["status"] for item in results), ["match-required", "paired"]
        )

    def test_expired_unmatched_advertisement_is_ignored_and_cannot_be_revived(
        self,
    ) -> None:
        clock = FakeClock()
        first = join(
            self.root, self.task, options={"invitation_ttl_seconds": 5}, clock=clock
        )
        clock.sleep(6)
        fresh = join(self.root, self.task, clock=clock)
        settings = Settings.load(Path(first["settings_path"]))
        self.assertNotEqual(first["goal"], fresh["goal"])
        self.assertEqual(pairing_state(settings, clock=clock), "expired")
        self.assertEqual(renew_invitation(settings, clock=clock), "expired")
        with self.assertRaisesRegex(DuetError, "expired"):
            check_membership(settings, first["participant"], first["session"])
        result = wait_for_messages(
            settings, first["participant"], timeout=1, clock=clock
        )
        self.assertEqual(result.status, "invitation-expired")

    def test_wait_renews_valid_invitation_without_consuming_messages(self) -> None:
        clock = FakeClock()
        first = join(
            self.root, self.task, options={"invitation_ttl_seconds": 4}, clock=clock
        )
        settings = Settings.load(Path(first["settings_path"]))
        result = wait_for_messages(
            settings, first["participant"], timeout=10, clock=clock
        )
        self.assertEqual(result.code, ExitCode.TIMEOUT)
        self.assertEqual(pairing_state(settings, clock=clock), "waiting")
        self.assertGreater(
            read_lobby(self.root).invitations[0].expires_at, clock.utcnow().timestamp()
        )
        self.assertFalse(receive(settings, first["participant"]).checkpoint)

    def test_paired_invitation_never_expires_or_renews(self) -> None:
        clock = FakeClock()
        first = join(
            self.root, self.task, options={"invitation_ttl_seconds": 2}, clock=clock
        )
        join(self.root, self.task, clock=clock)
        before = (self.root / ".duet.jsonl").read_bytes()
        clock.sleep(100)
        self.assertEqual(
            renew_invitation(Settings.load(Path(first["settings_path"])), clock=clock),
            "paired",
        )
        self.assertEqual((self.root / ".duet.jsonl").read_bytes(), before)

    def test_cross_tmpdir_join_uses_invitation_board_without_temp_lookup(self) -> None:
        first = join(self.root, self.task)
        with (
            patch.dict(os.environ, {"TMPDIR": str(self.root / "missing")}),
            patch(
                "settings.tempfile.gettempdir",
                side_effect=AssertionError("joining must not choose a temp root"),
            ),
        ):
            second = join(self.root, self.task)
        self.assertEqual(first["board"], second["board"])
        self.assertEqual(first["temp_directory"], second["temp_directory"])

    def test_missing_tmpdir_uses_python_temp_lookup_for_new_advertisement(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "settings.tempfile.gettempdir", return_value=str(self.root)
            ) as lookup,
        ):
            first = join(self.root, self.task)
        lookup.assert_called()
        self.assertEqual(first["temp_directory"], str(self.root))
        self.assertIn(first["board"], (self.root / ".duet.jsonl").read_text())

    def test_old_project_pointer_does_not_block_or_get_modified(self) -> None:
        old = self.root / ".duet.json"
        old.write_text('{"board":"/old/board"}')
        before = old.read_bytes()
        first, second = self.peers()
        self.assertEqual(first["goal"], second["goal"])
        self.assertEqual(old.read_bytes(), before)

    def test_registry_reading_is_read_only_and_exposes_no_credentials(self) -> None:
        before = set(self.root.iterdir())
        self.assertEqual(session_status(self.root)["sessions"], [])
        self.assertEqual(set(self.root.iterdir()), before)
        first = join(self.root, self.task)
        path = self.root / ".duet.jsonl"
        raw = path.read_bytes()
        status = session_status(self.root)
        self.assertEqual(status["sessions"][0]["state"], "waiting")
        self.assertNotIn(first["session"], json.dumps(status))
        self.assertEqual(path.read_bytes(), raw)

    def test_registry_symlink_corruption_and_failed_append_preserve_history(
        self,
    ) -> None:
        first = join(self.root, self.task)
        path = self.root / ".duet.jsonl"
        raw = path.read_bytes()
        with (
            patch("pairing.atomic_write", side_effect=OSError("interrupted write")),
            self.assertRaises(OSError),
        ):
            join(self.root, self.task)
        self.assertEqual(path.read_bytes(), raw)
        self.assertFalse(read_lobby(self.root).invitations[0].paired)
        target = self.root / "saved-registry"
        path.rename(target)
        path.symlink_to(target)
        with self.assertRaises(OSError):
            join(self.root, self.task)
        path.unlink()
        path.write_bytes(raw + b'{"partial":')
        with self.assertRaisesRegex(DuetError, "incomplete"):
            join(self.root, self.task)
        self.assertIn(first["goal"].encode(), path.read_bytes())

    def test_options_cannot_change_identity_or_existing_invitation_settings(
        self,
    ) -> None:
        first = join(self.root, self.task, options={"wait_timeout_seconds": 7})
        with self.assertRaisesRegex(DuetError, "differ"):
            join(self.root, self.task, options={"wait_timeout_seconds": 9})
        with self.assertRaises(DuetError):
            join(self.root, self.task, options={"goal": "other"})
        second = join(self.root, self.task)
        self.assertEqual(first["goal"], second["goal"])
        self.assertEqual(
            Settings.load(Path(second["settings_path"])).wait_timeout_seconds, 7
        )

    def test_simultaneous_clients_with_distinct_temp_environments_form_one_pair(
        self,
    ) -> None:
        scratch = [self.root / "client-a", self.root / "client-b"]
        for folder in scratch:
            folder.mkdir()

        def start(index: int) -> dict:
            env = {**os.environ, "TMPDIR": str(scratch[index])}
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(SCRIPTS / "duet.py"),
                    "join",
                    "--workspace",
                    str(self.root),
                ],
                input=self.task,
                text=True,
                capture_output=True,
                check=False,
                env=env,
                timeout=self.settings.wait_timeout_seconds,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(start, range(2)))
        self.assertEqual(results[0]["board"], results[1]["board"])
        advertiser = next(item for item in results if item["participant"] == "peer-a")
        self.assertEqual(
            {item["temp_directory"] for item in results}, {advertiser["temp_directory"]}
        )

    def test_wait_process_renews_until_it_is_stopped_then_invitation_expires(
        self,
    ) -> None:
        import time

        first = join(
            self.root,
            self.task,
            options={"invitation_ttl_seconds": 0.8, "poll_interval_seconds": 0.05},
        )
        from actions import Connection

        handle = Connection(
            Path(first["settings_path"]), first["participant"], first["session"]
        ).save()
        process = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(SCRIPTS / "duet.py"),
                "wait",
                "--connection",
                str(handle),
                "--timeout-seconds",
                "10",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            # The active wait must keep advertising past its original lease.
            time.sleep(1)
            invitation = read_lobby(self.root).invitations[0]
            self.assertGreater(invitation.expires_at, time.time())
            self.assertIsNone(process.poll())
        finally:
            process.terminate()
            process.communicate(timeout=self.settings.wait_timeout_seconds)
        time.sleep(max(0, invitation.expires_at - time.time()) + 0.05)
        second = join(self.root, self.task)
        third = join(self.root, self.task)
        self.assertNotEqual(first["goal"], second["goal"])
        self.assertEqual(second["goal"], third["goal"])

    def test_newly_created_invitation_invalidates_a_separate_task_observation(
        self,
    ) -> None:
        first = join(self.root, self.task)
        assessed = join(self.root, "Task C")
        other_choice = join(self.root, "Task B")
        other = join(self.root, "Task B", separate=other_choice["observation"])
        raced = join(self.root, "Task C", separate=assessed["observation"])
        self.assertEqual(raced["status"], "match-required")
        self.assertEqual(
            {item["session"] for item in raced["candidates"]},
            {first["goal"], other["goal"]},
        )

    def test_registry_limits_and_invalid_timing_do_not_publish_partial_records(
        self,
    ) -> None:
        for options in [
            {"invitation_ttl_seconds": 0},
            {"invitation_ttl_seconds": True},
            {"max_registry_bytes": 0},
        ]:
            with self.subTest(options=options), self.assertRaises(DuetError):
                join(self.root, self.task, options=options)
        self.assertFalse((self.root / ".duet.jsonl").exists())
        with self.assertRaisesRegex(DuetError, "registry exceeds"):
            join(self.root, self.task, options={"max_registry_bytes": 10})
        self.assertFalse((self.root / ".duet.jsonl").exists())
        first = join(self.root, self.task)
        path = self.root / ".duet.jsonl"
        original = path.read_bytes()
        with self.assertRaises(DuetError):
            join(self.root, self.task, options={"max_registry_bytes": 10})
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(first["goal"], read_lobby(self.root).invitations[0].session)

    def test_malformed_complete_registry_events_are_not_ignored(self) -> None:
        first = join(self.root, self.task)
        path = self.root / ".duet.jsonl"
        raw = path.read_bytes()
        for event in [
            {"session": first["goal"], "state": "waiting"},
            {"session": first["goal"], "state": "paired"},
            {"session": "unknown", "state": "paired", "task": self.task},
            {"session": first["goal"], "state": "renewed", "expires_at": 0},
        ]:
            path.write_bytes(raw + json.dumps(event).encode() + b"\n")
            before = path.read_bytes()
            with self.subTest(event=event), self.assertRaises(DuetError):
                join(self.root, self.task)
            self.assertEqual(path.read_bytes(), before)

    def test_small_lease_renews_even_when_message_poll_interval_is_longer(self) -> None:
        clock = FakeClock()
        first = join(
            self.root,
            self.task,
            options={"invitation_ttl_seconds": 0.4, "poll_interval_seconds": 1},
            clock=clock,
        )
        settings = Settings.load(Path(first["settings_path"]))
        result = wait_for_messages(
            settings, first["participant"], timeout=2.5, clock=clock
        )
        self.assertEqual(result.code, ExitCode.TIMEOUT)
        self.assertEqual(pairing_state(settings, clock=clock), "waiting")

    def test_canonical_workspace_aliases_discover_the_same_invitation(self) -> None:
        first = join(self.root, self.task)
        alias = self.root / "workspace-alias"
        alias.symlink_to(self.root, target_is_directory=True)
        second = join(alias, self.task)
        self.assertEqual(first["goal"], second["goal"])
        self.assertEqual(first["board"], second["board"])
