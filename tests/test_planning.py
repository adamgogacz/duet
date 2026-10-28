# Copyright 2026 Adam Gogacz
# SPDX-License-Identifier: Apache-2.0

"""Exercise read-only gate comparisons without treating text changes as proof."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from actions import send
from completion import complete, state
from delivery import receive
from messages import Kind
from test_actions import InteractionFixture
from test_delivery import FakeClock


class PlanningTests(InteractionFixture):
    """Inspect real isolated histories while leaving acknowledgments explicit."""

    def ready(self) -> None:
        """Independently announce both retained peers before negotiation."""
        for peer in self.peers:
            send(peer, Kind.READY, text="Ready to review completion conditions")

    def propose(self, index: int = 0, **kwargs: object) -> dict:
        """Create full gate terms with optional explicit causal bases."""
        terms = {
            "criteria": ["Review the project", "Support the joint report"],
            "stop_condition": "Stop incomplete after the bounded review pass",
            "text": "Both review independently; A integrates and proposes completion",
            **kwargs,
        }
        return send(self.peers[index], Kind.PROPOSE, scope=["work-division"], **terms)

    def accepted(self) -> tuple[dict, dict]:
        """Establish one explicit bilateral gate and retain both record IDs."""
        self.ready()
        proposal = self.propose()
        agreement = send(self.peers[1], Kind.AGREE, reply_to=proposal["id"])
        return proposal, agreement

    def pending(self, index: int = 0) -> dict[str, dict]:
        """Index pending gate reviews by immutable proposal ID."""
        return {
            item["proposal"]: item
            for item in state(self.peers[index])["gate_review"]["pending"]
        }

    def journal(self) -> dict[Path, bytes]:
        """Snapshot bytes and file membership for every shared journal file."""
        board = self.peers[0].settings.board
        return {
            path.relative_to(board): path.read_bytes()
            for path in board.rglob("*")
            if path.is_file()
        }

    def test_state_preserves_original_request_before_and_after_gate(self) -> None:
        request = "Review the project and agree a report."
        self.assertEqual(state(self.peers[0])["user_request"], request)
        proposal, agreement = self.accepted()
        result = state(self.peers[1])
        self.assertEqual(result["user_request"], request)
        self.assertEqual(result["gate"]["agreement"], agreement["id"])
        self.assertNotIn("gate_review", result)
        self.assertNotEqual(proposal["message"]["body"]["proposal"], request)

    def test_initial_pending_gate_has_no_invented_base(self) -> None:
        self.ready()
        proposed = self.propose()
        entry = self.pending()[proposed["id"]]
        self.assertEqual(entry["base_proposals"], [])
        self.assertFalse(entry["based_on_current_gate"])
        delta = entry["changes_from_bases"][0]
        self.assertIsNone(delta["base_proposal"])
        self.assertEqual(
            delta["criteria_added"], proposed["message"]["body"]["criteria"]
        )
        self.assertEqual(delta["criteria_removed"], [])
        self.assertNotIn("changes_from_current_gate", entry)

    def test_pending_amendment_exposes_exact_added_removed_and_text_changes(
        self,
    ) -> None:
        original, _ = self.accepted()
        amended = self.propose(
            criteria=["Support the joint report", "Reproduce important findings"],
            stop_condition="Stop incomplete when the reproduction budget is exhausted",
            text="B integrates; both review the resulting evidence",
        )
        entry = self.pending()[amended["id"]]
        self.assertEqual(entry["base_proposals"], [original["id"]])
        self.assertTrue(entry["based_on_current_gate"])
        delta = entry["changes_from_bases"][0]
        self.assertEqual(delta["base_proposal"], original["id"])
        self.assertEqual(delta["criteria_added"], ["Reproduce important findings"])
        self.assertEqual(delta["criteria_removed"], ["Review the project"])
        self.assertFalse(delta["criteria_reordered"])
        self.assertIn(
            "-Stop incomplete after the bounded review pass",
            delta["stop_condition_diff"],
        )
        self.assertIn(
            "+B integrates; both review the resulting evidence", delta["plan_diff"]
        )

    def test_reordering_preserves_duplicate_criteria_without_additions_or_removals(
        self,
    ) -> None:
        self.ready()
        original = self.propose(criteria=["Check code", "Check evidence", "Check code"])
        send(self.peers[1], Kind.AGREE, reply_to=original["id"])
        amended = self.propose(criteria=["Check code", "Check code", "Check evidence"])
        delta = self.pending()[amended["id"]]["changes_from_bases"][0]
        self.assertTrue(delta["criteria_reordered"])
        self.assertEqual(delta["criteria_added"], [])
        self.assertEqual(delta["criteria_removed"], [])
        self.assertEqual(delta["plan_diff"], [])
        self.assertEqual(delta["stop_condition_diff"], [])

    def test_counterplan_uses_nearest_actual_proposal_base(self) -> None:
        self.ready()
        root = self.propose(criteria=["Initial evidence"])
        counter = self.propose(1, criteria=["Independent evidence"])
        replacement = self.propose(
            criteria=["Independent evidence", "Reproduction"], causes=[counter["id"]]
        )
        entry = self.pending()[replacement["id"]]
        self.assertEqual(entry["base_proposals"], [counter["id"]])
        self.assertNotIn(root["id"], entry["base_proposals"])
        self.assertEqual(
            entry["changes_from_bases"][0]["criteria_added"], ["Reproduction"]
        )

    def test_stale_amendment_compares_its_actual_base_and_current_gate(self) -> None:
        original, _ = self.accepted()
        stale = self.propose(1, criteria=["Review the project", "Check compatibility"])
        replacement = self.propose(criteria=["Current stronger evidence"])
        accepted = send(self.peers[1], Kind.AGREE, reply_to=replacement["id"])
        result = state(self.peers[0])
        self.assertEqual(result["gate"]["agreement"], accepted["id"])
        entry = self.pending()[stale["id"]]
        self.assertFalse(entry["based_on_current_gate"])
        self.assertEqual(entry["base_proposals"], [original["id"]])
        self.assertEqual(
            entry["changes_from_bases"][0]["criteria_removed"],
            ["Support the joint report"],
        )
        current = entry["changes_from_current_gate"]
        self.assertEqual(current["base_proposal"], replacement["id"])
        self.assertEqual(current["criteria_removed"], ["Current stronger evidence"])

    def test_multiple_causal_bases_are_all_compared(self) -> None:
        self.accepted()
        alternatives = [
            self.propose(index, criteria=[f"Evidence alternative {index}"])
            for index in range(2)
        ]
        reconciled = self.propose(
            criteria=["Combined evidence"],
            causes=[item["id"] for item in alternatives],
        )
        entry = self.pending()[reconciled["id"]]
        expected = {item["id"] for item in alternatives}
        self.assertEqual(set(entry["base_proposals"]), expected)
        self.assertEqual(
            {item["base_proposal"] for item in entry["changes_from_bases"]}, expected
        )

    def test_competing_accepted_gates_remain_visible_without_choosing_a_winner(
        self,
    ) -> None:
        self.accepted()
        alternatives = [
            self.propose(index, criteria=[f"Alternative {index}"]) for index in range(2)
        ]
        agreements = [
            send(self.peers[1 - index], Kind.AGREE, reply_to=proposal["id"])
            for index, proposal in enumerate(alternatives)
        ]
        result = state(self.peers[0])
        self.assertEqual(result["status"], "gate-conflict")
        self.assertNotIn("gate", result)
        self.assertEqual(
            set(result["gate_review"]["competing_agreements"]),
            {item["id"] for item in agreements},
        )
        self.assertEqual(result["gate_review"]["pending"], [])

    def test_viewing_state_never_acknowledges_or_changes_journal_bytes(self) -> None:
        self.accepted()
        self.propose(criteria=["Additional independent evidence"])
        unread = [
            [item.id for item in receive(peer.settings, peer.participant).unread]
            for peer in self.peers
        ]
        self.assertTrue(any(unread))
        before = self.journal()
        for _ in range(2):
            for index, peer in enumerate(self.peers):
                result = state(peer)
                self.assertEqual(result["unread_count"], len(unread[index]))
        self.assertEqual(self.journal(), before)
        self.assertEqual(
            [
                [item.id for item in receive(peer.settings, peer.participant).unread]
                for peer in self.peers
            ],
            unread,
        )

    def test_equivalent_wording_changes_still_require_semantic_review(self) -> None:
        self.accepted()
        amended = self.propose(
            criteria=["Review the project", "Provide support for the joint report"]
        )
        result = state(self.peers[0])
        delta = self.pending()[amended["id"]]["changes_from_bases"][0]
        self.assertEqual(delta["criteria_removed"], ["Support the joint report"])
        self.assertEqual(
            delta["criteria_added"], ["Provide support for the joint report"]
        )
        self.assertIn("semantic review", result["gate_review"]["review_note"])
        self.assertIn("user_request", result["gate_review"]["review_note"])
        self.assertFalse(result["complete"])

    def test_unchanged_criteria_do_not_establish_requirement_satisfaction(self) -> None:
        self.accepted()
        amended = self.propose()
        result = state(self.peers[0])
        delta = self.pending()[amended["id"]]["changes_from_bases"][0]
        self.assertEqual(delta["criteria_added"], [])
        self.assertEqual(delta["criteria_removed"], [])
        self.assertFalse(delta["criteria_reordered"])
        self.assertFalse(result["complete"])
        self.assertIn(
            "cannot waive user requirements", result["gate_review"]["review_note"]
        )

    def test_terminal_state_retains_original_request(self) -> None:
        self.plan()
        self.handled(0)
        offered, _ = complete(
            self.peers[0],
            clock=FakeClock(),
            propose=True,
            text="Both reviewed the requested outcome",
            artifacts=[self.report],
            evidence=["Both reviewed the report against the requested project scope"],
        )
        self.handled(1)
        complete(self.peers[1], clock=FakeClock(), agree=offered["proposal"])
        complete(self.peers[0], clock=FakeClock())
        result = state(self.peers[0])
        self.assertEqual(result["status"], "complete")
        self.assertEqual(
            result["user_request"], "Review the project and agree a report."
        )

    def test_counter_amendment_shows_net_changes_against_accepted_gate(self) -> None:
        accepted, _ = self.accepted()
        amendment = self.propose(
            criteria=[
                "Review the project",
                "Support the joint report",
                "Run a bounded reproduction",
            ]
        )
        counter = self.propose(
            1,
            criteria=[
                "Review the project",
                "Support the joint report",
                "Run a bounded reproduction",
            ],
            text="Counterplan changes only the writer",
            causes=[amendment["id"]],
        )
        entry = self.pending()[counter["id"]]
        self.assertTrue(entry["based_on_current_gate"])
        self.assertEqual(entry["base_proposals"], [amendment["id"]])
        self.assertEqual(entry["changes_from_bases"][0]["criteria_added"], [])
        net = entry["changes_from_current_gate"]
        self.assertEqual(net["base_proposal"], accepted["id"])
        self.assertEqual(net["criteria_added"], ["Run a bounded reproduction"])
