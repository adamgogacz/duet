# Copyright 2026 Adam Gogacz
# SPDX-License-Identifier: Apache-2.0

"""Exercise gates, cancellation, stale consent, and adversarial closing histories."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from actions import Connection, retry, send
from board import scan
from completion import complete, state
from delivery import receive, wait_for_messages
from enrollment import find_run, finish
from messages import Kind
from settings import DuetError, ExitCode, encoded
from test_actions import InteractionFixture
from test_delivery import FakeClock
from workflow import gate, obligations, reconciled_conflicts


class WorkflowTests(InteractionFixture):
    """Use real isolated journals and explicit consent; no live collaboration writes."""

    def act(self, index: int, **kwargs: object) -> tuple[dict, ExitCode]:
        """Keep semantic tests independent of disk speed."""
        return complete(self.peers[index], clock=FakeClock(), **kwargs)

    def offer(self, **kwargs: object) -> dict:
        """Propose against the current criterion count with explicit evidence."""
        self.handled(0)
        criteria = gate(self.peers[0].settings, scan(self.peers[0].settings)).criteria
        result, _ = self.act(
            0,
            propose=True,
            text="Whole requested outcome assessed",
            artifacts=[self.report],
            evidence=["Inspected evidence for this criterion" for _ in criteria],
            **kwargs,
        )
        self.assertIn("proposal", result)
        return result

    def close(self, offer: dict) -> dict:
        """Both peers explicitly consent, then observe bilateral closing."""
        self.handled(1)
        self.act(1, agree=offer["proposal"])
        result, _ = self.act(0)
        return result

    def request(self) -> tuple[dict, dict]:
        """Record an optional accepted investigation beyond the opening."""
        request = send(
            self.peers[0],
            Kind.REQUEST,
            text="Investigate the optional alternative",
            scope=["alternative"],
            criteria=["Evidence and a disposition"],
        )
        accepted = send(self.peers[1], Kind.ACCEPT, reply_to=request["id"])
        return request, accepted

    def reconcile(self, replies: list[str], *, agree: bool = True) -> dict:
        """Explicitly replace conflicting terms without erasing any commitment."""
        proposal = send(
            self.peers[0],
            Kind.PROPOSE,
            scope=["conflict-resolution"],
            causes=replies,
            text="Replace the conflicting terms; retain user criteria and dispose of old commitments explicitly",
        )
        if agree:
            return send(self.peers[1], Kind.AGREE, reply_to=proposal["id"])
        return proposal

    def conflicting_decision(self) -> tuple[dict, list[str]]:
        """Create a genuine contradictory ordinary decision through the public interface."""
        proposal = send(
            self.peers[0], Kind.PROPOSE, scope=["approach"], text="Use approach A"
        )
        replies = [
            send(
                self.peers[1],
                kind,
                reply_to=proposal["id"],
                text="Conflicting decision",
            )["id"]
            for kind in (Kind.AGREE, Kind.OBJECT)
        ]
        return proposal, replies

    def test_explicit_reconciliation_needs_full_evidence_and_peer_assent(self) -> None:
        self.plan()
        decision, replies = self.conflicting_decision()
        with self.assertRaisesRegex(DuetError, "every reply"):
            self.reconcile(replies[:1])
        proposed = self.reconcile(replies, agree=False)
        self.assertNotIn(
            decision["id"], reconciled_conflicts(scan(self.peers[0].settings))
        )
        agreement = send(self.peers[1], Kind.AGREE, reply_to=proposed["id"])
        snapshot = scan(self.peers[0].settings)
        self.assertIn(decision["id"], snapshot.conflicts)
        self.assertEqual(
            reconciled_conflicts(snapshot)[decision["id"]], agreement["id"]
        )
        self.assertEqual(self.close(self.offer())["status"], "complete")

    def test_reconciliation_does_not_erase_an_accepted_task(self) -> None:
        self.plan()
        request, accepted = self.request()
        declined = send(
            self.peers[1],
            Kind.DECLINE,
            reply_to=request["id"],
            text="Contradicting acceptance",
        )
        self.reconcile([accepted["id"], declined["id"]])
        self.handled(0)
        blocked, _ = self.act(
            0, propose=True, text="Cannot waive old ownership", evidence=["Assessment"]
        )
        self.assertEqual(blocked["status"], "blocked")
        send(
            self.peers[1],
            Kind.RELEASE,
            reply_to=accepted["id"],
            text="Stop this superseded optional commitment",
        )
        self.assertEqual(
            obligations(scan(self.peers[0].settings))[0]["state"], "reconciled"
        )
        self.assertEqual(self.close(self.offer())["status"], "complete")

    def test_reconciliation_does_not_choose_a_conflicting_verification(self) -> None:
        self.plan()
        _, accepted = self.request()
        submitted = send(
            self.peers[1],
            Kind.SUBMIT,
            reply_to=accepted["id"],
            text="Result",
            evidence=["Checked"],
        )
        verified = send(
            self.peers[0],
            Kind.VERIFY,
            reply_to=submitted["id"],
            evidence=["Initial check"],
        )
        rejected = send(
            self.peers[0],
            Kind.REJECT,
            reply_to=submitted["id"],
            text="Contrary check",
            criteria=["Correctness"],
        )
        self.reconcile([verified["id"], rejected["id"]])
        self.assertTrue(
            obligations(scan(self.peers[0].settings))[0]["blocks_completion"]
        )
        send(
            self.peers[1],
            Kind.RELEASE,
            reply_to=accepted["id"],
            text="Abandon ambiguous submission; any replacement uses a new accepted task",
        )
        self.assertEqual(self.close(self.offer())["status"], "complete")

    def test_competing_reconciliations_need_a_shared_replacement(self) -> None:
        self.plan()
        decision, replies = self.conflicting_decision()
        first = self.reconcile(replies, agree=False)
        second = send(
            self.peers[1],
            Kind.PROPOSE,
            scope=["conflict-resolution"],
            causes=replies,
            text="Other replacement",
        )
        agreements = [
            send(self.peers[1], Kind.AGREE, reply_to=first["id"]),
            send(self.peers[0], Kind.AGREE, reply_to=second["id"]),
        ]
        self.assertNotIn(
            decision["id"], reconciled_conflicts(scan(self.peers[0].settings))
        )
        self.reconcile(replies + [item["id"] for item in agreements])
        self.assertEqual(self.close(self.offer())["status"], "complete")

    def test_a_new_conflicting_reply_invalidates_prior_reconciliation(self) -> None:
        self.plan()
        decision, replies = self.conflicting_decision()
        self.reconcile(replies)
        send(
            self.peers[0],
            Kind.WITHDRAW,
            reply_to=decision["id"],
            text="New contradictory control",
        )
        self.assertNotIn(
            decision["id"], reconciled_conflicts(scan(self.peers[0].settings))
        )

    def test_unrelated_ack_errors_are_not_hidden_by_cutoff_recovery(self) -> None:
        from unittest.mock import patch

        self.plan()
        offered = self.offer()
        send(self.peers[1], Kind.AGREE, reply_to=offered["proposal"])
        with (
            patch(
                "completion.acknowledge", side_effect=DuetError("invalid checkpoint")
            ),
            self.assertRaisesRegex(DuetError, "invalid checkpoint"),
        ):
            self.act(0)

    def test_superseded_root_cannot_be_agreed_after_accepted_counterplan(self) -> None:
        for peer in self.peers:
            send(peer, Kind.READY, text="Ready")
        terms = {
            "scope": ["work-division"],
            "criteria": ["Evidence-based report"],
            "stop_condition": "Stop incomplete if bounded checks fail",
        }
        root = send(self.peers[0], Kind.PROPOSE, text="Initial terms", **terms)
        counter = send(self.peers[1], Kind.PROPOSE, text="Replacement terms", **terms)
        accepted = send(self.peers[0], Kind.AGREE, reply_to=counter["id"])
        with self.assertRaisesRegex(DuetError, "superseded"):
            send(self.peers[1], Kind.AGREE, reply_to=root["id"])
        self.assertEqual(
            gate(self.peers[0].settings, scan(self.peers[0].settings)).agreement.id,
            accepted["id"],
        )

    def test_frontier_pins_long_history_within_the_message_byte_limit(self) -> None:
        agreement = self.plan()
        settings_path = self.peers[0].settings_path
        values = json.loads(settings_path.read_text())
        values["max_message_bytes"] = 3072
        settings_path.write_text(json.dumps(values))
        previous = agreement["id"]
        for index in range(65):
            previous = send(
                self.peers[index % 2],
                text=f"Evidence update {index}",
                causes=[previous],
            )["id"]
        snapshot = scan(self.peers[0].settings)
        offered = self.offer()
        from startup import ancestry

        actual = scan(self.peers[0].settings).history[offered["proposal"]]
        self.assertLess(len(actual.raw), values["max_message_bytes"])
        self.assertEqual(
            ancestry(actual.causes, snapshot.history), set(snapshot.history)
        )
        self.assertEqual(self.close(offered)["status"], "complete")

    def test_corrected_answers_do_not_create_a_completion_dead_end(self) -> None:
        self.plan()
        question = send(self.peers[0], Kind.ASK, text="Which value is supported?")
        for answer in (
            "Initial value",
            "Corrected value, with the earlier claim withdrawn",
        ):
            send(
                self.peers[1],
                Kind.ANSWER,
                reply_to=question["id"],
                text=answer,
                evidence=["Source inspected"],
            )
        self.assertFalse(scan(self.peers[0].settings).conflicts)
        self.assertEqual(self.close(self.offer())["status"], "complete")

    def test_crossed_question_withdrawal_and_answer_do_not_compete_for_consent(
        self,
    ) -> None:
        self.plan()
        question = send(self.peers[0], Kind.ASK, text="Optional clarification")
        send(
            self.peers[0],
            Kind.WITHDRAW,
            reply_to=question["id"],
            text="No longer needed",
        )
        send(
            self.peers[1],
            Kind.ANSWER,
            reply_to=question["id"],
            text="The answer was already in flight",
            evidence=["Inspected source"],
        )
        self.assertFalse(scan(self.peers[0].settings).conflicts)
        self.assertEqual(self.close(self.offer())["status"], "complete")

    def test_reviewer_must_handle_pre_offer_findings_before_consensus(self) -> None:
        self.plan()
        finding = send(
            self.peers[0], text="Consequential finding before the final offer"
        )
        offered = self.offer()
        result, code = self.act(1, agree=offered["proposal"])
        self.assertEqual(code, ExitCode.ATTENTION)
        self.assertEqual(result["status"], "peer-attention")
        self.assertIn(finding["id"], [item["id"] for item in result["messages"]])
        self.assertFalse(
            any(
                item.kind == Kind.AGREE and item.reply_to == offered["proposal"]
                for item in scan(self.peers[0].settings).messages
            )
        )
        self.handled(1)
        self.assertEqual(self.close(offered)["status"], "complete")

    def test_terminal_commit_during_closing_ack_returns_successful_terminal_state(
        self,
    ) -> None:
        from unittest.mock import patch

        from completion import acknowledge as actual_ack

        self.plan()
        offered = self.offer()
        approval = send(self.peers[1], Kind.AGREE, reply_to=offered["proposal"])
        self.act(0)

        def peer_commits_then_ack(*args: object, **kwargs: object) -> dict:
            peer = self.peers[1]
            finish(
                peer.settings,
                peer.participant,
                peer.session,
                approval["id"],
                clock=FakeClock(),
                timeout=0.1,
            )
            return actual_ack(*args, **kwargs)

        with patch("completion.acknowledge", side_effect=peer_commits_then_ack):
            result, code = self.act(0)
        self.assertEqual(code, ExitCode.OK)
        self.assertEqual(result["status"], "complete")

    def test_gate_needs_nonempty_criteria_and_stop_condition(self) -> None:
        for peer in self.peers:
            send(peer, Kind.READY, text="Ready to negotiate")
        for values in [
            {},
            {"criteria": []},
            {"criteria": ["Report the evidence"], "stop_condition": ""},
        ]:
            with self.subTest(values=values), self.assertRaises(DuetError):
                send(
                    self.peers[0],
                    Kind.PROPOSE,
                    scope=["work-division"],
                    text="Split the work",
                    **values,
                )
        self.assertFalse(
            any(
                message.kind == Kind.PROPOSE
                for message in scan(self.peers[0].settings).messages
            )
        )

    def test_task_messages_and_captures_wait_for_accepted_gate(self) -> None:
        for peer in self.peers:
            send(peer, Kind.READY, text="Ready to negotiate")
        with self.assertRaisesRegex(DuetError, "agree completion criteria"):
            send(
                self.peers[0],
                Kind.REQUEST,
                text="Start substantive work",
                scope=["work"],
                criteria=["Checked"],
            )
        with self.assertRaisesRegex(DuetError, "agree completion criteria"):
            send(self.peers[0], text="Substantive result", artifacts=[self.report])
        self.assertFalse((self.peers[0].settings.board / "artifacts").exists())
        # Negotiation questions remain possible before substantive work.
        send(
            self.peers[0],
            Kind.ASK,
            text="Which completion conditions preserve the user's request?",
        )

    def test_missing_evidence_does_not_create_offer_or_capture(self) -> None:
        self.plan()
        with self.assertRaisesRegex(DuetError, "one evidence statement"):
            self.act(0, propose=True, text="Done", artifacts=[self.report])
        self.assertFalse((self.peers[0].settings.board / "artifacts").exists())

    def test_unfinished_accepted_task_blocks_success(self) -> None:
        self.plan()
        _, accepted = self.request()
        self.handled(0)
        result, code = self.act(
            0, propose=True, text="Done", evidence=["Claimed evidence"]
        )
        self.assertEqual(code, ExitCode.ATTENTION)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("accepted", [item["state"] for item in result["blockers"]])
        self.assertFalse(
            find_run(self.peers[0].settings.board, self.peers[0].settings.goal).terminal
        )
        send(
            self.peers[1],
            Kind.RELEASE,
            reply_to=accepted["id"],
            text="Both chose to abandon this optional alternative",
        )
        result = self.close(self.offer())
        self.assertEqual(result["status"], "complete")

    def test_requester_cannot_withdraw_an_accepted_commitment(self) -> None:
        self.plan()
        request, _ = self.request()
        with self.assertRaisesRegex(DuetError, "released by its owner"):
            send(
                self.peers[0],
                Kind.WITHDRAW,
                reply_to=request["id"],
                text="Silently revoke the active work",
            )

    def test_optional_unaccepted_request_can_be_withdrawn(self) -> None:
        self.plan()
        request = send(
            self.peers[0],
            Kind.REQUEST,
            text="Optional idea",
            scope=["alternative"],
            criteria=["Check feasibility"],
        )
        send(
            self.peers[0],
            Kind.WITHDRAW,
            reply_to=request["id"],
            text="Alternative no longer needed",
        )
        self.assertEqual(self.close(self.offer())["status"], "complete")

    def test_reject_then_release_abandons_work_without_verifying_it(self) -> None:
        self.plan()
        _, accepted = self.request()
        submitted = send(
            self.peers[1],
            Kind.SUBMIT,
            reply_to=accepted["id"],
            text="Initial result",
            evidence=["Initial checks"],
        )
        send(
            self.peers[0],
            Kind.REJECT,
            reply_to=submitted["id"],
            text="This alternative is infeasible",
            criteria=["Feasibility"],
        )
        send(
            self.peers[1],
            Kind.RELEASE,
            reply_to=accepted["id"],
            text="Stop this optional branch after rejection",
        )
        snapshot = scan(self.peers[0].settings)
        self.assertFalse(snapshot.conflicts)
        self.assertEqual(obligations(snapshot)[0]["state"], "released")
        self.assertEqual(self.close(self.offer())["status"], "complete")

    def test_completion_wait_requires_explicit_release_even_after_answer(self) -> None:
        self.plan()
        question = send(self.peers[0], Kind.ASK, text="Provide required evidence")
        waiting = send(
            self.peers[0],
            Kind.AWAIT,
            text="Cannot complete before verification",
            body={
                "blocks": ["goal-completion"],
                "deadline": None,
                "condition": {
                    "kind": "answer",
                    "from": "peer-b",
                    "subject": question["message"]["subject"],
                    "target": question["id"],
                },
            },
        )
        answered = send(
            self.peers[1],
            Kind.ANSWER,
            reply_to=question["id"],
            text="Verification evidence",
            evidence=["Source checked independently"],
        )
        self.handled(0)
        result, _ = self.act(0, propose=True, text="Done", evidence=["Checks complete"])
        self.assertEqual(result["status"], "blocked")
        send(
            self.peers[0],
            Kind.RESUME,
            reply_to=waiting["id"],
            evidence=["The answer satisfies the required verification"],
            causes=[answered["id"]],
        )
        self.assertEqual(self.close(self.offer())["status"], "complete")

    def test_incomplete_stop_is_not_success_and_retains_unmet_obligations(self) -> None:
        self.plan()
        self.request()
        offered = self.offer(outcome="incomplete")
        result = self.close(offered)
        self.assertEqual(result["status"], "incomplete")
        self.assertTrue(result["terminal"])
        self.assertFalse(result["complete"])
        self.assertFalse(
            find_run(self.peers[0].settings.board, self.peers[0].settings.goal).complete
        )

    def test_gate_amendment_invalidates_old_offer_after_acknowledgment(self) -> None:
        original = self.plan()
        offered = self.offer()
        amendment = send(
            self.peers[0],
            Kind.PROPOSE,
            scope=["work-division"],
            text="Add the required independent check",
            criteria=["Original result", "Independent reproduction"],
            stop_condition="Stop incomplete if either check cannot be established",
        )
        agreed = send(self.peers[1], Kind.AGREE, reply_to=amendment["id"])
        self.handled(0)
        result, code = self.act(1, agree=offered["proposal"])
        self.assertEqual(code, ExitCode.ATTENTION)
        self.assertEqual(result["status"], "revision-required")
        self.assertNotEqual(result["gate"]["agreement"], original["id"])
        self.assertEqual(result["gate"]["agreement"], agreed["id"])
        with self.assertRaisesRegex(DuetError, "stale gate"):
            send(self.peers[1], Kind.AGREE, reply_to=offered["proposal"])

    def test_late_information_cannot_be_disposed_of_by_acknowledging_it(self) -> None:
        self.plan()
        offered = self.offer()
        self.act(1, agree=offered["proposal"])
        late = send(
            self.peers[0], text="New consequential evidence contradicts a conclusion"
        )
        self.handled(1)
        result, code = self.act(1)
        self.assertEqual(code, ExitCode.ATTENTION)
        self.assertEqual(result["status"], "revision-required")
        # Provisional finish does not retire the reviewer.
        correction, _ = self.act(
            1,
            request_changes=offered["proposal"],
            text="Reassess the contradictory evidence",
        )
        self.assertEqual(correction["status"], "changes-requested")
        self.assertIn(
            late["id"],
            [message.id for message in scan(self.peers[0].settings).messages],
        )
        self.assertFalse(
            find_run(self.peers[0].settings.board, self.peers[0].settings.goal).terminal
        )

    def test_fresh_approval_does_not_reuse_previous_provisional_finish(self) -> None:
        self.plan()
        offered = self.offer()
        self.act(1, agree=offered["proposal"])
        send(self.peers[0], text="New evidence needs renewed assent")
        revised = self.offer()
        approval = send(self.peers[1], Kind.AGREE, reply_to=revised["proposal"])
        result, code = finish(
            self.peers[0].settings,
            "peer-a",
            self.peers[0].session,
            approval["id"],
            timeout=0.1,
            clock=FakeClock(),
        )
        self.assertEqual(code, ExitCode.TIMEOUT)
        self.assertFalse(result["complete"])
        run = find_run(self.peers[0].settings.board, self.peers[0].settings.goal)
        self.assertFalse(
            next(
                member.finished
                for member in run.members
                if member.participant == "peer-b"
            )
        )
        self.assertEqual(self.act(1)[0]["status"], "complete")

    def test_edit_during_closing_needs_new_revision_and_fresh_assent(self) -> None:
        self.plan()
        offered = self.offer()
        self.act(1, agree=offered["proposal"])
        self.report.write_text("Corrected report with new evidence.\n")
        self.assertEqual(self.act(0)[0]["status"], "revision-required")
        revised, _ = self.act(
            0,
            revise=True,
            text="Corrected whole-goal claim",
            artifacts=[self.report],
            evidence=["Reviewed the changed evidence"],
        )
        self.assertEqual(self.close(revised)["status"], "complete")

    def test_changes_request_wakes_a_correlated_waiter(self) -> None:
        self.plan()
        offered = self.offer()
        clock = FakeClock(
            on_sleep=lambda: self.act(
                1,
                request_changes=offered["proposal"],
                text="Correct one misleading sentence before approval",
            )
        )
        result = wait_for_messages(
            self.peers[0].settings,
            "peer-a",
            reply_to=offered["proposal"],
            timeout=20,
            clock=clock,
        )
        self.assertEqual(result.code, ExitCode.REJECTED)
        self.assertLess(clock.elapsed, 20)
        self.handled(0)
        revised, _ = self.act(
            0,
            revise=True,
            text="Corrected wording",
            evidence=["Corrected statement checked"],
            artifacts=[self.report],
        )
        self.assertEqual(self.close(revised)["status"], "complete")
        self.assertNotIn(
            Kind.WITHDRAW,
            [message.kind for message in scan(self.peers[0].settings).messages],
        )

    def test_terminal_session_rejects_new_messages_but_exact_retry_is_safe(
        self,
    ) -> None:
        self.plan()
        self.close(self.offer())
        with self.assertRaisesRegex(DuetError, "active enrolled writer"):
            send(self.peers[1], text="New work after termination")
        result = retry(self.peers[1])
        self.assertTrue(result["duplicate"])
        # Historical approval concerns the immutable copy, not future edits to the origin.
        self.report.write_text("Unapproved later edit.\n")
        self.assertEqual(self.act(0)[0]["status"], "complete")

    def test_publication_and_terminal_finish_have_one_serialized_cutoff(self) -> None:
        self.plan()
        offered = self.offer()
        self.act(1, agree=offered["proposal"])
        run = find_run(self.peers[0].settings.board, self.peers[0].settings.goal)
        barrier = threading.Barrier(2)

        def update() -> bool:
            barrier.wait(timeout=5)
            try:
                send(self.peers[1], text="Consequential late evidence")
                return True
            except DuetError:
                return False

        def close() -> None:
            barrier.wait(timeout=5)
            try:
                finish(
                    self.peers[0].settings,
                    "peer-a",
                    self.peers[0].session,
                    run.agreement,
                    timeout=0.1,
                    clock=FakeClock(),
                )
            except DuetError:
                pass

        with ThreadPoolExecutor(max_workers=2) as pool:
            publishing = pool.submit(update)
            finishing = pool.submit(close)
            published = publishing.result(timeout=10)
            finishing.result(timeout=10)
        terminal = find_run(
            self.peers[0].settings.board, self.peers[0].settings.goal
        ).terminal
        self.assertFalse(published and terminal)

    def test_short_handles_are_explicit_and_isolated(self) -> None:
        tokens = [peer.save_alias() for peer in self.peers]
        self.assertNotEqual(*tokens)
        self.assertTrue(all(len(token) == 37 for token in tokens))
        for token, peer in zip(tokens, self.peers):
            self.assertEqual(Connection.load(token), peer)
        path = self.root / "duet" / "connections" / f"{tokens[0]}.json"
        path.write_text(
            json.dumps(
                {
                    "settings_path": str(self.peers[1].settings_path),
                    "participant": self.peers[1].participant,
                    "session": self.peers[1].session,
                }
            )
        )
        with self.assertRaisesRegex(DuetError, "does not match"):
            Connection.load(tokens[0])

    def test_alias_cache_failure_does_not_strand_a_join(self) -> None:
        (self.root / "duet" / "connections").write_text("Not a directory")
        import subprocess
        import sys

        workspace = self.root / "another-workspace"
        workspace.mkdir()
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(Path(__file__).resolve().parents[1] / "scripts/duet.py"),
                "join",
                "--workspace",
                str(workspace),
            ],
            input="Review this project",
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["connection"], output["connection_path"])
        self.assertEqual(Connection.load(output["connection"]).participant, "peer-a")

    def test_crossed_gate_amendments_can_be_reconciled_explicitly(self) -> None:
        self.plan()
        amendments = [
            send(
                peer,
                Kind.PROPOSE,
                text=f"Alternative gate from {peer.participant}",
                scope=["work-division"],
                criteria=["A checked result"],
                stop_condition="Stop incomplete if the bounded evidence cannot support the result",
            )
            for peer in self.peers
        ]
        send(self.peers[1], Kind.AGREE, reply_to=amendments[0]["id"])
        send(self.peers[0], Kind.AGREE, reply_to=amendments[1]["id"])
        self.assertEqual(state(self.peers[0])["status"], "gate-conflict")
        reconciled = send(
            self.peers[0],
            Kind.PROPOSE,
            text="Reconcile both alternatives under these full conditions",
            scope=["work-division"],
            criteria=["A checked result"],
            stop_condition="Stop incomplete after the bounded checks if evidence is unavailable",
        )
        send(self.peers[1], Kind.AGREE, reply_to=reconciled["id"])
        self.assertEqual(self.close(self.offer())["status"], "complete")

    def test_suspended_old_waiter_returns_only_the_actual_terminal_revision(
        self,
    ) -> None:
        self.plan()
        old = self.offer()
        send(self.peers[1], Kind.AGREE, reply_to=old["proposal"])

        def replace_while_waiting() -> None:
            self.report.write_text("A new reviewed report revision\n")
            revised, _ = self.act(
                0,
                revise=True,
                text="New final claim",
                artifacts=[self.report],
                evidence=["New revision independently checked"],
            )
            self.handled(1)
            self.act(1, agree=revised["proposal"])
            self.act(0)

        result, code = complete(
            self.peers[0], clock=FakeClock(on_sleep=replace_while_waiting), timeout=2
        )
        self.assertEqual(code, ExitCode.OK)
        actual = state(self.peers[0])
        self.assertEqual(result["agreement"], actual["agreement"])
        self.assertEqual(result["artifacts"], actual["artifacts"])

    def test_recorded_uncertain_effect_blocks_success_and_is_visible(self) -> None:
        from board import save_checkpoint

        self.plan()
        self.handled(0)
        peer = self.peers[0]
        checkpoint = dict(receive(peer.settings, peer.participant).checkpoint)
        checkpoint["external_actions"] = [
            {
                "id": "operation",
                "status": "uncertain",
                "summary": "Authorized operation result needs reconciliation",
            }
        ]
        save_checkpoint(
            peer.settings, peer.participant, peer.session, encoded(checkpoint)
        )
        result, code = self.act(
            0, propose=True, text="Claimed success", evidence=["Claimed checks"]
        )
        self.assertEqual(code, ExitCode.ATTENTION)
        self.assertEqual(result["status"], "blocked")
        self.assertIn(
            "uncertain", [item["state"] for item in result["recognized_obligations"]]
        )

    def test_changed_effect_context_invalidates_old_consent(self) -> None:
        from board import save_checkpoint

        self.plan()
        old = self.offer()
        self.act(1, agree=old["proposal"])
        peer = self.peers[1]
        checkpoint = dict(receive(peer.settings, peer.participant).checkpoint)
        checkpoint["external_actions"] = [
            {
                "id": "operation",
                "status": "resolved",
                "summary": "Newly reconciled actual result",
            }
        ]
        save_checkpoint(
            peer.settings, peer.participant, peer.session, encoded(checkpoint)
        )
        self.assertEqual(self.act(0)[0]["status"], "revision-required")

    def test_changed_caller_temp_root_requires_retained_full_path(self) -> None:
        import os
        from unittest.mock import patch

        token = self.peers[0].save_alias()
        other = self.root / "different-caller-temp"
        other.mkdir()
        with patch.dict(os.environ, {"TMPDIR": str(other)}):
            with self.assertRaises(OSError):
                Connection.load(token)
            self.assertEqual(Connection.load(self.handles[0]), self.peers[0])

    def test_terminal_capture_survives_later_processing_cache_loss(self) -> None:
        from board import checkpoint_path, save_checkpoint

        self.plan()
        self.handled(0)
        peer = self.peers[0]
        checkpoint = dict(receive(peer.settings, peer.participant).checkpoint)
        checkpoint["external_actions"] = [
            {
                "id": "verified-operation",
                "status": "resolved",
                "summary": "Authorized effect reconciled before the offer",
            }
        ]
        save_checkpoint(
            peer.settings, peer.participant, peer.session, encoded(checkpoint)
        )
        self.close(self.offer())
        checkpoint_path(peer.settings, peer.participant).unlink()
        self.assertEqual(self.act(0)[0]["status"], "complete")

    def test_incomplete_proposal_without_peer_consent_is_not_terminal(self) -> None:
        self.plan()
        offered = self.offer(outcome="incomplete")
        self.assertEqual(offered["status"], "awaiting-peer-agreement")
        result, code = self.act(0)
        self.assertEqual(code, ExitCode.TIMEOUT)
        self.assertFalse(result["terminal"])
        self.assertFalse(result["complete"])

    def test_interrupted_revision_preparation_cannot_renew_consent(self) -> None:
        from unittest.mock import patch

        from completion import send as actual_send

        self.plan()
        self.offer()

        def fail_new_offer(
            connection: Connection, kind: Kind, **kwargs: object
        ) -> dict:
            if kind == Kind.PROPOSE:
                raise OSError("Interrupted after withdrawing the old offer")
            return actual_send(connection, kind, **kwargs)

        with (
            patch("completion.send", side_effect=fail_new_offer),
            self.assertRaises(OSError),
        ):
            self.act(
                0,
                revise=True,
                text="Corrected report",
                artifacts=[self.report],
                evidence=["Rechecked"],
            )
        replay = retry(self.peers[0])
        self.assertEqual(replay["message"]["kind"], "withdraw")
        self.assertEqual(state(self.peers[0])["status"], "revision-required")
        self.assertFalse(
            find_run(self.peers[0].settings.board, self.peers[0].settings.goal).terminal
        )
        self.assertEqual(self.close(self.offer())["status"], "complete")

    def test_lost_result_after_revised_offer_commit_retries_exact_offer(self) -> None:
        from unittest.mock import patch

        from completion import send as actual_send

        self.plan()
        self.offer()
        committed = []

        def lose_result(connection: Connection, kind: Kind, **kwargs: object) -> dict:
            result = actual_send(connection, kind, **kwargs)
            if kind == Kind.PROPOSE:
                committed.append(result["id"])
                raise OSError("Result lost after the new offer committed")
            return result

        with (
            patch("completion.send", side_effect=lose_result),
            self.assertRaises(OSError),
        ):
            self.act(
                0,
                revise=True,
                text="Corrected claim",
                artifacts=[self.report],
                evidence=["Rechecked"],
            )
        replay = retry(self.peers[0])
        self.assertEqual(replay["id"], committed[0])
        self.assertEqual(
            sum(
                message.id == committed[0]
                for message in scan(self.peers[0].settings).messages
            ),
            1,
        )
        self.assertEqual(self.close({"proposal": committed[0]})["status"], "complete")

    def test_generated_late_update_ack_and_finish_orders_preserve_safety(self) -> None:
        from itertools import permutations

        for order in permutations(("update", "ack", "finish")):
            with self.subTest(order=order):
                fixture = WorkflowTests("runTest")
                fixture.setUp()
                try:
                    fixture.plan()
                    offered = fixture.offer()
                    fixture.act(1, agree=offered["proposal"])
                    published = False
                    for action in order:
                        if action == "update":
                            try:
                                send(
                                    fixture.peers[1],
                                    text="Consequential evidence changed",
                                )
                                published = True
                            except DuetError:
                                pass
                        elif action == "ack":
                            try:
                                fixture.handled(0)
                            except DuetError:
                                pass
                        else:
                            fixture.act(0)
                    run = find_run(
                        fixture.peers[0].settings.board, fixture.peers[0].settings.goal
                    )
                    self.assertFalse(published and run.terminal)
                finally:
                    fixture.doCleanups()

    def test_cli_short_handle_status_and_compact_receipt(self) -> None:
        import subprocess
        import sys

        self.plan()
        token = self.peers[0].save_alias()
        script = str(Path(__file__).resolve().parents[1] / "scripts/duet.py")
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                script,
                "send",
                "--connection",
                token,
                "--text",
                "A short ordinary update",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertIn("id", receipt)
        self.assertNotIn("message", receipt)
        self.assertNotIn(self.peers[0].session, result.stdout)
        status = subprocess.run(
            [sys.executable, "-B", script, "status", "--connection", token],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(json.loads(status.stdout)["status"], "working")

    def test_recorded_effect_update_and_terminal_finish_share_the_cutoff(self) -> None:
        from board import save_checkpoint

        self.plan()
        offered = self.offer()
        self.act(1, agree=offered["proposal"])
        peer = self.peers[1]
        data = dict(receive(peer.settings, peer.participant).checkpoint)
        data["external_actions"] = [
            {
                "id": "late-effect",
                "status": "uncertain",
                "summary": "Recorded uncertainty before closing",
            }
        ]
        run = find_run(peer.settings.board, peer.settings.goal)
        barrier = threading.Barrier(2)

        def record() -> bool:
            barrier.wait(timeout=5)
            try:
                save_checkpoint(
                    peer.settings, peer.participant, peer.session, encoded(data)
                )
                return True
            except DuetError:
                return False

        def finish_old() -> None:
            barrier.wait(timeout=5)
            try:
                finish(
                    self.peers[0].settings,
                    "peer-a",
                    self.peers[0].session,
                    run.agreement,
                    timeout=0.1,
                    clock=FakeClock(),
                )
            except DuetError:
                pass

        with ThreadPoolExecutor(max_workers=2) as pool:
            recorded = pool.submit(record)
            closing = pool.submit(finish_old)
            changed = recorded.result(timeout=10)
            closing.result(timeout=10)
        self.assertFalse(
            changed and find_run(peer.settings.board, peer.settings.goal).terminal
        )

    def test_released_task_cannot_resume_through_an_old_rejection(self) -> None:
        self.plan()
        _, accepted = self.request()
        submitted = send(
            self.peers[1],
            Kind.SUBMIT,
            reply_to=accepted["id"],
            text="Optional branch result",
            evidence=["Initial evidence"],
        )
        rejected = send(
            self.peers[0],
            Kind.REJECT,
            reply_to=submitted["id"],
            text="Branch rejected",
            criteria=["Feasibility"],
        )
        send(
            self.peers[1],
            Kind.RELEASE,
            reply_to=accepted["id"],
            text="Stop the rejected optional branch",
        )
        with self.assertRaisesRegex(DuetError, "released"):
            send(
                self.peers[1],
                Kind.SUBMIT,
                reply_to=rejected["id"],
                text="Silently resume abandoned work",
                evidence=["New evidence"],
            )

    def test_bad_revision_inputs_do_not_withdraw_the_previous_offer(self) -> None:
        self.plan()
        offered = self.offer()
        with self.assertRaises(OSError):
            self.act(
                0,
                revise=True,
                text="Revision",
                artifacts=[self.root / "missing-report.md"],
                evidence=["Checked"],
            )
        self.assertEqual(state(self.peers[0])["proposal"], offered["proposal"])
        self.assertEqual(state(self.peers[0])["status"], "awaiting-peer-agreement")
        with self.assertRaisesRegex(DuetError, "meaningful"):
            self.act(
                0, revise=True, text="Revision", artifacts=[self.report], evidence=[""]
            )
        self.assertFalse(
            any(
                message.kind == Kind.WITHDRAW
                for message in scan(self.peers[0].settings).messages
            )
        )

    def test_late_information_wakes_completion_wait_without_becoming_approval(
        self,
    ) -> None:
        self.plan()
        offered = self.offer()
        clock = FakeClock(
            on_sleep=lambda: send(
                self.peers[1], text="New finding reached after the offer"
            )
        )
        result = wait_for_messages(
            self.peers[0].settings,
            "peer-a",
            reply_to=offered["proposal"],
            timeout=480,
            clock=clock,
        )
        self.assertEqual(result.code, ExitCode.ATTENTION)
        self.assertEqual(result.status, "completion-context-changed")
        self.assertFalse(result.matched)
        self.assertLess(clock.elapsed, 480)

    def test_changed_origin_wakes_completion_wait_before_stale_approval(self) -> None:
        self.plan()
        offered = self.offer()
        clock = FakeClock(
            on_sleep=lambda: self.report.write_text("Changed captured origin\n")
        )
        result = wait_for_messages(
            self.peers[0].settings,
            "peer-a",
            reply_to=offered["proposal"],
            timeout=480,
            clock=clock,
        )
        self.assertEqual(result.code, ExitCode.ATTENTION)
        self.assertEqual(result.status, "completion-context-changed")
        self.assertLess(clock.elapsed, 480)

    def test_unrelated_agreement_cannot_whitewash_a_control_conflict(self) -> None:
        self.plan()
        decision, replies = self.conflicting_decision()
        unrelated = send(
            self.peers[0],
            Kind.PROPOSE,
            scope=["formatting"],
            causes=replies,
            text="Only choose a report format",
        )
        send(self.peers[1], Kind.AGREE, reply_to=unrelated["id"])
        self.assertNotIn(
            decision["id"], reconciled_conflicts(scan(self.peers[0].settings))
        )
        self.handled(0)
        result, _ = self.act(
            0, propose=True, text="Still blocked", evidence=["Not a resolution"]
        )
        self.assertEqual(result["status"], "blocked")

    def test_conflicted_reconciliation_never_revives_its_old_approval(self) -> None:
        self.plan()
        decision, replies = self.conflicting_decision()
        replacement = self.reconcile(replies, agree=False)
        approved = send(self.peers[1], Kind.AGREE, reply_to=replacement["id"])
        rejected = send(
            self.peers[1],
            Kind.OBJECT,
            reply_to=replacement["id"],
            text="Replacement itself is disputed",
        )
        self.assertNotIn(
            decision["id"], reconciled_conflicts(scan(self.peers[0].settings))
        )
        current = self.reconcile(replies + [approved["id"], rejected["id"]])
        actual = reconciled_conflicts(scan(self.peers[0].settings))
        self.assertEqual(actual[decision["id"]], current["id"])
        self.assertEqual(actual[replacement["id"]], current["id"])
        self.assertEqual(self.close(self.offer())["status"], "complete")

    def test_frontier_keeps_independent_branches(self) -> None:
        from startup import ancestry

        accepted = self.plan()
        branches = [
            send(
                peer,
                text=f"Independent evidence from {peer.participant}",
                causes=[accepted["id"]],
            )
            for peer in self.peers
        ]
        before = scan(self.peers[0].settings)
        offered = self.offer()
        message = scan(self.peers[0].settings).history[offered["proposal"]]
        self.assertTrue({branch["id"] for branch in branches} <= set(message.causes))
        self.assertEqual(ancestry(message.causes, before.history), set(before.history))
        self.assertEqual(self.close(offered)["status"], "complete")

    def test_gate_amendment_does_not_clear_an_uncertain_execution_incident(
        self,
    ) -> None:
        from board import save_checkpoint

        self.plan()
        self.handled(0)
        peer = self.peers[0]
        data = dict(receive(peer.settings, peer.participant).checkpoint)
        data["external_actions"] = [
            {
                "id": "coverage-write",
                "status": "uncertain",
                "summary": "Cleanup did not establish whether a prior coverage file was overwritten",
            }
        ]
        save_checkpoint(peer.settings, peer.participant, peer.session, encoded(data))
        amendment = send(
            peer,
            Kind.PROPOSE,
            scope=["work-division"],
            criteria=["Write the final report"],
            stop_condition="Stop incomplete if mandatory conditions remain unmet",
            text="Changed reporting process; this cannot erase the execution incident",
        )
        send(self.peers[1], Kind.AGREE, reply_to=amendment["id"])
        self.handled(0)
        result, code = self.act(
            0, propose=True, text="Report finished", evidence=["Report exists"]
        )
        self.assertEqual(code, ExitCode.ATTENTION)
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(
            any(
                item["kind"] == "recorded-effect" and item["state"] == "uncertain"
                for item in result["blockers"]
            )
        )
        self.assertEqual(
            result["user_request"], "Review the project and agree a report."
        )
