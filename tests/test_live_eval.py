# Copyright 2026 Adam Gogacz
# SPDX-License-Identifier: Apache-2.0

"""Test the live-eval adapter without making paid model calls."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evals/live"))
SPEC = importlib.util.spec_from_file_location(
    "duet_live_eval", ROOT / "evals/live/run_claude.py"
)
LIVE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = LIVE
SPEC.loader.exec_module(LIVE)


class LiveEvalTests(unittest.TestCase):
    """Protect blinding, native goal selection, scoped permissions, and honest grading."""

    def setUp(self) -> None:
        """Use isolated scratch storage, never real projects or profiles."""
        configured = os.environ.get("TMPDIR")
        directory = (
            configured
            if configured and Path(configured).is_dir()
            else tempfile.gettempdir()
        )
        self.scratch = tempfile.TemporaryDirectory(
            prefix="duet-live-tests-", dir=directory
        )
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.settings = LIVE.TrialSettings.load(ROOT / "evals/live/settings.json")
        self.case = {
            "id": "fixture",
            "instructions": "Read-only test instructions",
            "prompt": "/goal Use Duet to assess the fixture and agree a report",
            "files": {"inputs/example.py": "value = 1\n"},
            "expected_outcome": "complete",
            "rubric": {"hidden_gold": "UNIQUE_ANSWER_MUST_NOT_REACH_WORKSPACE"},
        }

    def test_preparation_copies_complete_skill_but_not_gold(self) -> None:
        manifest = LIVE.prepare(self.case, self.root / "run", self.settings)
        workspace = Path(manifest["workspace"])
        self.assertTrue(
            (workspace / ".claude/skills/duet/scripts/workflow.py").is_file()
        )
        for path in workspace.rglob("*"):
            if path.is_file():
                self.assertNotIn(self.case["rubric"]["hidden_gold"], path.read_text())
        self.assertEqual(
            LIVE.fingerprint(workspace / ".claude/skills/duet"),
            manifest["skill_fingerprint"],
        )
        self.assertIn("harness_fingerprint", manifest)
        self.assertIn("scenario_fingerprint", manifest)

    def test_native_command_pins_sonnet_high_goal_and_never_bypasses(self) -> None:
        manifest = LIVE.prepare(self.case, self.root / "run", self.settings)
        args = LIVE.command(self.case, manifest, self.settings, "session")
        self.assertEqual(args[args.index("-p") + 1], self.case["prompt"])
        self.assertEqual(args[args.index("--model") + 1], "claude-sonnet-5-5")
        self.assertEqual(args[args.index("--effort") + 1], "high")
        self.assertEqual(args[args.index("--permission-mode") + 1], "dontAsk")
        self.assertNotIn("--dangerously-skip-permissions", args)
        self.assertNotIn("--bare", args)
        self.assertIn("project", args[args.index("--setting-sources") + 1])
        config = json.loads(Path(manifest["config"]).read_text())
        self.assertIn("Agent", config["permissions"]["deny"])
        self.assertFalse(config["disableAllHooks"])
        prompt = args[args.index("--append-system-prompt") + 1]
        self.assertNotIn("before the gate", prompt)
        self.assertNotIn("denied variable-wrapper", prompt)
        self.assertNotIn("Skill path:", prompt)
        script = Path(manifest["workspace"]) / ".claude/skills/duet/scripts/duet.py"
        for form in (str(script), f'"{script}"', f"'{script}'"):
            self.assertIn(f"Bash(python3 -B {form} *)", config["permissions"]["allow"])

    def test_prior_skill_snapshot_can_be_evaluated_without_mutating_it(self) -> None:
        snapshot = self.root / "snapshot"
        snapshot.mkdir()
        (snapshot / "SKILL.md").write_text("Prior instructions\n")
        (snapshot / "LICENSE").write_text("Fixture license\n")
        (snapshot / "scripts").mkdir()
        (snapshot / "references").mkdir()
        manifest = LIVE.prepare(self.case, self.root / "run", self.settings, snapshot)
        installed = Path(manifest["workspace"]) / ".claude/skills/duet"
        self.assertEqual((installed / "SKILL.md").read_text(), "Prior instructions\n")
        self.assertEqual(manifest["skill_source"], str(snapshot))

    def test_mixed_models_are_selected_and_graded_in_launch_order(self) -> None:
        from dataclasses import replace

        settings = replace(
            self.settings, peer_models=["sonnet-fixture", "haiku-fixture"]
        )
        manifest = LIVE.prepare(self.case, self.root / "mixed", settings)
        peers = []
        for index, model in enumerate(settings.peer_models):
            args = LIVE.command(
                self.case, manifest, settings.for_peer(index), f"session-{index}"
            )
            self.assertEqual(args[args.index("--model") + 1], model)
            self.assertEqual(args[args.index("--effort") + 1], "high")
            peers.append(
                {
                    "model": model,
                    "goal_set_observed": False,
                    "result_subtype": "success",
                    "is_error": False,
                    "exit_code": 0,
                }
            )
        result = LIVE.grade(self.case, manifest, peers, False)
        self.assertTrue(result["protocol_checks"]["requested_model_observed"])
        peers[1]["model"] = peers[0]["model"]
        result = LIVE.grade(self.case, manifest, peers, False)
        self.assertFalse(result["protocol_checks"]["requested_model_observed"])

    def test_mixed_model_settings_reject_partial_or_malformed_pairs(self) -> None:
        path = self.root / "mixed-settings.json"
        values = json.loads((ROOT / "evals/live/settings.json").read_text())
        for models in ([], ["one"], ["one", ""], ["one", 2], "one,two"):
            path.write_text(json.dumps({**values, "peer_models": models}))
            with (
                self.subTest(models=models),
                self.assertRaisesRegex(ValueError, "peer_models"),
            ):
                LIVE.TrialSettings.load(path)

    def test_agreement_first_regression_does_not_supply_detailed_done_criteria(
        self,
    ) -> None:
        cases = json.loads((ROOT / "evals/live/scenarios.json").read_text())[
            "scenarios"
        ]
        case = next(
            item for item in cases if item["id"] == "agreement-first-broad-review"
        )
        self.assertNotIn("allow_discovery", case)
        self.assertIn("before you proceed with work", case["prompt"])
        self.assertIn(
            "No detailed review-quality completion criteria", case["instructions"]
        )
        self.assertIn("inputs/metadata.json", case["files"])

    def test_helpers_are_opt_in_and_have_a_read_only_native_allowlist(self) -> None:
        case = {**self.case, "allow_helpers": True}
        manifest = LIVE.prepare(case, self.root / "run", self.settings)
        args = LIVE.command(case, manifest, self.settings, "session")
        definitions = json.loads(args[args.index("--agents") + 1])
        self.assertEqual(args[args.index("--agent") + 1], LIVE.EVALUATOR_NAME)
        self.assertIn("--forward-subagent-text", args)
        self.assertEqual(definitions[LIVE.EVALUATOR_NAME]["prompt"], "")
        self.assertIn(
            f"Agent({LIVE.HELPER_NAME})", definitions[LIVE.EVALUATOR_NAME]["tools"]
        )
        self.assertEqual(
            definitions[LIVE.HELPER_NAME]["tools"], ["Read", "Grep", "Glob"]
        )
        self.assertEqual(definitions[LIVE.HELPER_NAME]["model"], self.settings.model)
        self.assertEqual(definitions[LIVE.HELPER_NAME]["effort"], "high")
        config = json.loads(Path(manifest["config"]).read_text())
        self.assertNotIn("Agent", config["permissions"]["deny"])
        self.assertNotIn("Task", config["permissions"]["deny"])

    def test_nested_calls_require_complete_audit_and_known_read_only_lineage(
        self,
    ) -> None:
        from dataclasses import replace

        from observations import (
            NativeCall,
            audit_complete,
            helper_lineage,
            input_digest,
        )

        launcher = NativeCall(
            "session", "launch", "Agent", {"subagent_type": LIVE.HELPER_NAME}
        )
        child = NativeCall(
            "session", "read", "Read", {"file_path": "inputs/example.py"}, "launch"
        )
        calls = [launcher, child]
        audits = [
            {
                "session_id": call.session,
                "tool_use_id": call.id,
                "tool": call.tool,
                "input_digest": input_digest(call.arguments, "/workspace"),
                "cwd": "/workspace",
                "time_ns": 1,
                **(
                    {"agent_id": "reader-one", "agent_type": LIVE.HELPER_NAME}
                    if call.parent
                    else {}
                ),
            }
            for call in calls
        ]
        self.assertTrue(audit_complete(calls, audits))
        self.assertTrue(helper_lineage(calls, audits, LIVE.HELPER_NAME))
        self.assertFalse(audit_complete(calls, audits[:1]))
        self.assertFalse(audit_complete(calls, [*audits, audits[-1]]))
        for changed in (
            replace(child, parent="unknown"),
            replace(child, session="other-peer"),
            replace(child, parent=None),
        ):
            self.assertFalse(
                helper_lineage([launcher, changed], audits, LIVE.HELPER_NAME)
            )
        for field, value in (("agent_type", "Explore"), ("agent_id", None)):
            self.assertFalse(
                helper_lineage(
                    calls, [audits[0], {**audits[1], field: value}], LIVE.HELPER_NAME
                )
            )
        for tool in ("Bash", "Write", "Agent"):
            with self.subTest(tool=tool):
                self.assertFalse(
                    helper_lineage(
                        [launcher, replace(child, tool=tool)], audits, LIVE.HELPER_NAME
                    )
                )

    def test_helper_prompts_exclude_only_validated_peer_capabilities(self) -> None:
        from dataclasses import replace

        from observations import NativeCall, helper_credentials_isolated

        workspace = self.root / "workspace"
        script = workspace / ".claude/skills/duet/scripts/duet.py"
        member = {"participant": "peer-a", "session": "secret-actor-token"}
        joined = {
            **member,
            "dry_run": False,
            "connection": "secret-short-handle",
            "connection_path": str(self.root / "board/connections/peer-a.json"),
        }
        join = NativeCall(
            "native-a", "join", "Bash", {"command": f"python3 -B {script} join"}
        )
        delegate = NativeCall(
            "native-a", "launch", "Agent", {"prompt": "Inspect inputs/example.py"}
        )
        streams = [
            [
                {
                    "type": "user",
                    "session_id": "native-a",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "join",
                                "content": json.dumps(joined),
                            }
                        ]
                    },
                }
            ]
        ]

        def isolated(prompt: str, recorded_members: list[dict[str, str]]) -> bool:
            """Check a helper prompt without exposing capability values in diagnostics."""
            return helper_credentials_isolated(
                streams,
                [join, replace(delegate, arguments={"prompt": prompt})],
                recorded_members,
                script,
                workspace,
            )

        self.assertTrue(isolated("Inspect inputs/example.py", [member]))
        for capability in (
            member["session"],
            joined["connection"],
            joined["connection_path"],
        ):
            self.assertFalse(
                isolated(f"Use {capability} to inspect the task", [member])
            )
        # A mismatched join result is not an authoritative source of capabilities.
        self.assertTrue(isolated(joined["connection"], []))

    def test_helper_file_scope_excludes_connections_and_unscoped_searches(self) -> None:
        from observations import NativeCall, helper_task_path

        workspace = self.root / "workspace"
        allowed = [
            ("Read", {"file_path": "inputs/example.py"}),
            ("Read", {"file_path": "TASK.md"}),
            ("Read", {"file_path": "case_checks.py"}),
            ("Grep", {"path": "inputs", "pattern": "value"}),
            ("Glob", {"pattern": "inputs/**/*.py"}),
            ("Glob", {"path": str(workspace / "inputs"), "pattern": "**/*.py"}),
        ]
        forbidden = [
            ("Read", {"file_path": str(self.root / "board/connections/peer-a.json")}),
            ("Read", {"file_path": "inputs/connections/peer-a.json"}),
            ("Grep", {"path": "inputs/connections", "pattern": "token"}),
            ("Glob", {"path": str(self.root), "pattern": "board/connections/*"}),
            ("Glob", {"pattern": "inputs/connections/*"}),
            ("Read", {"file_path": "inputs/../../credentials.json"}),
            ("Grep", {"path": ".", "pattern": "token"}),
            ("Glob", {"pattern": "**/*"}),
        ]
        for tool, arguments in allowed:
            self.assertTrue(
                helper_task_path(
                    NativeCall("native", "call", tool, arguments, "launch"), workspace
                )
            )
        for tool, arguments in forbidden:
            self.assertFalse(
                helper_task_path(
                    NativeCall("native", "call", tool, arguments, "launch"), workspace
                )
            )

    def test_blocked_coordination_does_not_reward_a_solo_report(self) -> None:
        case = {
            **self.case,
            "blocked_coordination": True,
            "expected_outcome": "unfinished",
        }
        manifest = LIVE.prepare(case, self.root / "run", self.settings)
        config = json.loads(Path(manifest["config"]).read_text())
        script = Path(manifest["workspace"]) / ".claude/skills/duet/scripts/duet.py"
        for form in (str(script), f'"{script}"', f"'{script}'"):
            self.assertIn(
                f"Bash(python3 -B {form} send *)", config["permissions"]["deny"]
            )
        workspace = Path(manifest["workspace"])
        peers = [
            {
                "goal_set_observed": True,
                "model": self.settings.model,
                "result_subtype": "success",
                "is_error": False,
                "exit_code": 0,
                "permission_denials": 1,
            }
            for _ in range(2)
        ]
        result = LIVE.grade(case, manifest, peers, False)
        self.assertTrue(result["protocol_checks"]["expected_report_state"])
        self.assertTrue(result["protocol_checks"]["expected_collaboration_outcome"])
        (workspace / "output/report.md").write_text("Solo findings before the gate")
        result = LIVE.grade(case, manifest, peers, False)
        self.assertFalse(result["protocol_checks"]["expected_report_state"])
        self.assertFalse(result["protocol_pass"])

    def test_terminal_ledger_does_not_approve_changed_or_uncaptured_report(
        self,
    ) -> None:
        board = self.root / "board"
        report = self.root / "report.md"
        report.write_text("Reviewed bytes\n")
        checksum = hashlib.sha256(report.read_bytes()).hexdigest()
        capture = board / "artifacts/goal/artifact-one" / checksum
        capture.mkdir(parents=True)
        (capture / "content").write_bytes(report.read_bytes())
        (capture / "record.json").write_text(json.dumps({"origin": str(report)}))
        messages = [
            {
                "id": "offer",
                "kind": "propose",
                "body": {
                    "artifacts": [
                        {"id": "artifact-one", "revision": f"sha256:{checksum}"}
                    ]
                },
            },
            {"id": "approval", "kind": "agree", "reply_to": "offer"},
        ]
        run = {"agreement": "approval", "goal": "goal"}
        self.assertTrue(LIVE.approved_report_matches(board, run, messages, report))
        report.write_text("Unreviewed changes\n")
        self.assertFalse(LIVE.approved_report_matches(board, run, messages, report))
        report.write_text("Reviewed bytes\n")
        (capture / "content").write_text("Corrupt capture\n")
        self.assertFalse(LIVE.approved_report_matches(board, run, messages, report))

    def test_log_parser_handles_system_strings_and_native_goal_feedback(self) -> None:
        path = self.root / "stream.jsonl"
        rows = [
            {
                "type": "system",
                "subtype": "init",
                "model": "claude-sonnet-5-5",
                "slash_commands": ["goal", "duet"],
            },
            {
                "type": "system",
                "subtype": "permission_denied",
                "message": "A string rather than an assistant message",
            },
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Goal set: fixture"}]},
            },
            {
                "type": "user",
                "message": {"content": "Stop hook feedback: not yet complete"},
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "total_cost_usd": 0.1,
            },
        ]
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        summary = LIVE.stream_summary(path)
        self.assertTrue(summary["goal_set_observed"])
        self.assertTrue(summary["duet_discovery_available"])
        self.assertEqual(summary["goal_continuation_feedback_count"], 1)
        self.assertEqual(summary["reported_cost_usd"], 0.1)

    def test_gate_proposal_mentioning_check_script_is_not_executing_it(self) -> None:
        planning = {
            "command": "python3 -B /workspace/.claude/skills/duet/scripts/duet.py send --criteria 'Run case_checks.py after agreement'"
        }
        checking = {"command": "python3 -B /workspace/case_checks.py"}
        self.assertFalse(LIVE.task_tool(planning))
        self.assertTrue(LIVE.task_tool(checking))
        self.assertTrue(LIVE.task_tool({"file_path": "/workspace/inputs/example.py"}))
        self.assertTrue(LIVE.task_tool({"file_path": "output/report.md"}))
        self.assertFalse(LIVE.task_tool({"file_path": "/workspace/TASK.md"}))

    def test_unscoped_searches_cannot_hide_pre_gate_task_access(self) -> None:
        self.assertTrue(LIVE.task_tool({"tool": "Grep", "pattern": "secret"}))
        self.assertTrue(
            LIVE.task_tool({"tool": "Grep", "file_path": ".", "pattern": "value"})
        )
        self.assertFalse(LIVE.task_tool({"tool": "Glob", "pattern": "**/*"}))
        self.assertFalse(LIVE.task_tool({"tool": "Glob", "pattern": "inputs/**/*.py"}))
        self.assertFalse(
            LIVE.task_tool(
                {
                    "tool": "Grep",
                    "file_path": "/workspace/.claude/skills/duet",
                    "pattern": "gate",
                }
            )
        )
        self.assertFalse(LIVE.task_tool({"tool": "Glob", "pattern": "**/AGENTS.md"}))

    def test_audit_coverage_rejects_omissions_duplicates_and_changed_payloads(
        self,
    ) -> None:
        from observations import NativeCall, audit_complete, input_digest

        call = NativeCall(
            "session", "call-one", "Read", {"file_path": "inputs/example.py"}
        )
        observed = {
            "session_id": "session",
            "tool_use_id": "call-one",
            "tool": "Read",
            "input_digest": input_digest(call.arguments, "/workspace"),
            "cwd": "/workspace",
            "time_ns": 1,
        }
        self.assertTrue(audit_complete([call], [observed]))
        self.assertFalse(audit_complete([call], []))
        self.assertFalse(audit_complete([call], [{**observed, "cwd": 42}]))
        self.assertFalse(audit_complete([call], [observed, observed]))
        self.assertFalse(
            audit_complete([call], [{**observed, "input_digest": "wrong"}])
        )
        self.assertFalse(
            audit_complete([call], [observed, {"error": "oversized input"}])
        )

    def test_passive_hook_records_oversized_and_malformed_inputs(self) -> None:
        audit = self.root / "audit"
        for raw in ("x" * 65, "not-json"):
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(ROOT / "evals/live/audit_hook.py"),
                    str(audit),
                    "64",
                ],
                input=raw,
                text=True,
                capture_output=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
        errors = LIVE.records(audit / "errors.jsonl")
        self.assertEqual(len(errors), 2)
        self.assertTrue(all(item.get("error") for item in errors))

    def test_staged_read_is_attributed_to_reviewer_before_its_challenge(self) -> None:
        from observations import staged_evidence_checks

        offers = [{"id": "first", "from": "peer-a"}, {"id": "last", "from": "peer-a"}]
        messages = [
            {"id": "challenge", "kind": "object", "reply_to": "first", "from": "peer-b"}
        ]
        times = {"first": 10, "challenge": 20, "last": 30}
        read = {
            "tool": "Read",
            "file_path": "inputs/late.md",
            "time_ns": 15,
            "session_id": "native-b",
        }
        participants = {"native-a": "peer-a", "native-b": "peer-b"}
        checks = staged_evidence_checks(
            [read], participants, offers, messages, times, "inputs/late.md"
        )
        self.assertTrue(all(checks.values()))
        checks = staged_evidence_checks(
            [{**read, "session_id": "native-a"}],
            participants,
            offers,
            messages,
            times,
            "inputs/late.md",
        )
        self.assertFalse(checks["non_proposer_read_late_evidence"])
        checks = staged_evidence_checks(
            [{**read, "time_ns": 25}],
            participants,
            offers,
            messages,
            times,
            "inputs/late.md",
        )
        self.assertFalse(checks["reviewer_read_before_challenge"])

    def test_corrupt_complete_records_cannot_silently_disappear(self) -> None:
        path = self.root / "audit.jsonl"
        path.write_text('{"valid": true}\n{"partial":')
        self.assertEqual(LIVE.records(path), [{"valid": True}])
        path.write_text('{"valid": true}\nCORRUPTED\n')
        with self.assertRaises(ValueError):
            LIVE.records(path)

    def test_regrade_rejects_changed_reference_answers(self) -> None:
        run = self.root / "run"
        LIVE.prepare(self.case, run, self.settings)
        changed = {**self.case, "expected_outcome": "incomplete"}
        with self.assertRaisesRegex(ValueError, "Scenario or rubric changed"):
            LIVE.regrade(run, changed)

    def test_fixture_cannot_write_outside_workspace(self) -> None:
        self.case["files"] = {"../escape.py": "bad"}
        with self.assertRaisesRegex(ValueError, "fixture path"):
            LIVE.prepare(self.case, self.root / "run", self.settings)
        self.assertFalse((self.root / "run/escape.py").exists())

    def test_unbounded_or_invalid_cost_settings_are_rejected(self) -> None:
        path = self.root / "settings.json"
        values = json.loads((ROOT / "evals/live/settings.json").read_text())
        for field, value in [
            ("wall_seconds", 0),
            ("per_agent_budget_usd", -1),
            ("poll_seconds", True),
        ]:
            bad = {**values, field: value}
            path.write_text(json.dumps(bad))
            with self.subTest(field=field), self.assertRaises(ValueError):
                LIVE.TrialSettings.load(path)

    def test_native_replay_is_idempotent_but_changed_tool_input_is_not(self) -> None:
        from observations import native_calls

        row = {
            "type": "assistant",
            "session_id": "native-a",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call",
                        "name": "Read",
                        "input": {"file_path": "TASK.md"},
                    }
                ]
            },
        }
        self.assertEqual(len(native_calls([[row, row]])), 1)
        changed = json.loads(json.dumps(row))
        changed["message"]["content"][0]["input"]["file_path"] = "inputs/example.py"
        with self.assertRaisesRegex(ValueError, "changed"):
            native_calls([[row, changed]])
        changed = {**row, "parent_tool_use_id": "helper-launch"}
        self.assertEqual(native_calls([[changed]])[0].parent, "helper-launch")
        with self.assertRaisesRegex(ValueError, "changed"):
            native_calls([[row, changed]])

    def test_roles_come_from_valid_join_results_not_process_launch_order(self) -> None:
        from observations import NativeCall, joined_participants

        script = Path("/workspace/.claude/skills/duet/scripts/duet.py")
        calls = [
            NativeCall(
                "native-first",
                "join-one",
                "Bash",
                {"command": f"python3 -B {script} join"},
            ),
            NativeCall(
                "native-second",
                "join-two",
                "Bash",
                {"command": f"python3 -B {script} join"},
            ),
        ]
        members = [
            {"participant": "peer-a", "session": "actor-a"},
            {"participant": "peer-b", "session": "actor-b"},
        ]
        streams = [
            [
                {
                    "type": "user",
                    "session_id": call.session,
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": call.id,
                                "content": json.dumps({**member, "dry_run": False}),
                            }
                        ]
                    },
                }
            ]
            for call, member in zip(calls, reversed(members), strict=True)
        ]
        self.assertEqual(
            joined_participants(streams, calls, members, script),
            {"native-first": "peer-b", "native-second": "peer-a"},
        )
        wrong = [{"participant": "peer-a", "session": "impostor"}]
        self.assertEqual(joined_participants(streams, calls, wrong, script), {})
        from dataclasses import replace

        self.assertEqual(
            joined_participants(
                streams,
                [replace(call, parent="helper-launch") for call in calls],
                members,
                script,
            ),
            {},
        )

    def test_hook_snapshot_and_digest_are_independent_of_later_source_edits(
        self,
    ) -> None:
        manifest = LIVE.prepare(self.case, self.root / "run", self.settings)
        harness = self.root / "run/harness"
        config = json.loads(Path(manifest["config"]).read_text())
        command = config["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        self.assertIn(str(harness / "audit_hook.py"), command)
        self.assertEqual(LIVE.fingerprint(harness), manifest["harness_files"])

    def test_relative_and_host_normalized_paths_have_the_same_audit_digest(
        self,
    ) -> None:
        from observations import input_digest

        relative = {"file_path": "inputs/example.py"}
        absolute = {"file_path": "/workspace/inputs/example.py"}
        self.assertEqual(
            input_digest(relative, "/workspace"), input_digest(absolute, "/workspace")
        )
        self.assertNotEqual(
            input_digest(relative, "/workspace"),
            input_digest({"file_path": "inputs/other.py"}, "/workspace"),
        )

    def test_discovery_allows_only_owned_ready_reads_not_early_execution(self) -> None:
        from timing import work_timing_checks

        participants = {"native-a": "peer-a", "native-b": "peer-b"}
        ready = {"peer-a": 10, "peer-b": 12}
        reads = [
            {
                "tool": "Read",
                "file_path": "inputs/code.py",
                "session_id": session,
                "time_ns": 15,
            }
            for session in participants
        ]
        checks = work_timing_checks(
            [
                *reads,
                {
                    "tool": "Glob",
                    "file_path": "inputs",
                    "pattern": "*",
                    "session_id": "native-a",
                    "time_ns": 1,
                },
            ],
            20,
            ready,
            participants,
            allow_discovery=True,
            blocked=False,
            require_discovery=True,
        )
        self.assertTrue(all(checks.values()))
        self.assertFalse(
            work_timing_checks(
                reads, 20, ready, participants, allow_discovery=False, blocked=False
            )["observable_task_timing_valid"]
        )
        for invalid in (
            {**reads[0], "time_ns": 9},
            {**reads[0], "tool": "Write"},
            {
                **reads[0],
                "tool": "Bash",
                "file_path": None,
                "command": "python3 -B /workspace/case_checks.py",
            },
            {**reads[0], "session_id": "unknown"},
        ):
            self.assertFalse(
                work_timing_checks(
                    [*reads, invalid],
                    20,
                    ready,
                    participants,
                    allow_discovery=True,
                    blocked=False,
                )["observable_task_timing_valid"]
            )
        one = work_timing_checks(
            reads[:1],
            20,
            ready,
            participants,
            allow_discovery=True,
            blocked=False,
            require_discovery=True,
        )
        self.assertFalse(one["both_peers_inspected_before_gate"])
        self.assertFalse(
            work_timing_checks(
                reads, None, ready, participants, allow_discovery=True, blocked=True
            )["observable_task_timing_valid"]
        )

    def test_input_inventory_includes_ignored_outputs_bytecode_and_new_directories(
        self,
    ) -> None:
        inputs = self.root / "inputs"
        inputs.mkdir()
        (inputs / ".gitignore").write_text(".coverage\nhtmlcov/\n__pycache__/\n")
        (inputs / ".coverage").write_text("prior state")
        before = LIVE.input_inventory(inputs)
        (inputs / ".coverage").write_text("replacement")
        self.assertNotEqual(LIVE.input_inventory(inputs), before)
        for relative in ("htmlcov/index.html", "__pycache__/sample.pyc"):
            baseline = LIVE.input_inventory(inputs)
            target = inputs / relative
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(b"generated")
            self.assertNotEqual(LIVE.input_inventory(inputs), baseline)
        baseline = LIVE.input_inventory(inputs)
        (inputs / "empty-generated-dir").mkdir()
        self.assertNotEqual(LIVE.input_inventory(inputs), baseline)

    def test_terminal_handoff_cannot_repeat_investigation_or_edit_approved_report(
        self,
    ) -> None:
        from timing import work_stopped_at_terminal

        before = {"tool": "Read", "file_path": "inputs/code.py", "time_ns": 10}
        report = {"tool": "Read", "file_path": "output/report.md", "time_ns": 30}
        self.assertTrue(work_stopped_at_terminal([before, report], 20))
        for late in (
            {**before, "time_ns": 30},
            {**before, "file_path": "inputs/output/code.py", "time_ns": 30},
            {**report, "tool": "Edit"},
            {
                "tool": "Bash",
                "command": "python3 -B /workspace/case_checks.py",
                "time_ns": 30,
            },
        ):
            with self.subTest(late=late):
                self.assertFalse(work_stopped_at_terminal([before, late], 20))

    def test_disposable_additions_never_waive_preservation_of_existing_inputs(
        self,
    ) -> None:
        baseline = {
            ".": "directory",
            ".coverage": "sha256:prior",
            "code.py": "sha256:source",
        }
        runtime = {
            **baseline,
            ".review-cache": "directory",
            ".review-cache/run/status.json": "sha256:new",
        }
        self.assertTrue(LIVE.input_tree_preserved(baseline, runtime, [".review-cache"]))
        self.assertFalse(LIVE.input_tree_preserved(baseline, runtime, []))
        for changed in (
            {**runtime, ".coverage": "sha256:replacement"},
            {**runtime, "code.py": "sha256:modified"},
            {k: v for k, v in runtime.items() if k != ".coverage"},
            {**runtime, "unexpected.py": "sha256:new"},
        ):
            self.assertFalse(
                LIVE.input_tree_preserved(baseline, changed, [".review-cache"])
            )
        for invalid in (".", "../escape", "/absolute", ".coverage"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                LIVE.input_tree_preserved(baseline, runtime, [invalid])

    def test_practical_review_fixture_creates_only_disposable_operational_files(
        self,
    ) -> None:
        cases = json.loads((ROOT / "evals/live/scenarios.json").read_text())[
            "scenarios"
        ]
        case = next(
            item for item in cases if item["id"] == "practical-read-only-review"
        )
        manifest = LIVE.prepare(case, self.root / "practical", self.settings)
        workspace = Path(manifest["workspace"])
        result = subprocess.run(
            [sys.executable, "-B", str(workspace / "case_checks.py")],
            cwd=workspace,
            env={**os.environ, "TMPDIR": manifest["temporary"]},
            check=False,
            capture_output=True,
            text=True,
            timeout=self.settings.wall_seconds,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        observed = json.loads(result.stdout)
        self.assertEqual(observed["before"]["mean_1_2"], 1.5)
        self.assertEqual(observed["after"]["mean_1_2"], 1)
        self.assertTrue(
            LIVE.input_tree_preserved(
                manifest["input_tree_inventory"],
                LIVE.input_inventory(workspace / "inputs"),
                case["allowed_input_additions"],
            )
        )
        self.assertTrue(list((workspace / "inputs/.review-cache").rglob("status.json")))
        self.assertTrue(list(Path(manifest["temporary"]).rglob("client-state.json")))

    def test_file_scope_preserves_parent_directory_breaches_as_failures(self) -> None:
        from observations import file_tools_scoped

        workspace = self.root / "workspace"
        temporary = self.root / "temporary"
        read = {"tool": "Read", "file_path": "inputs/code.py", "cwd": str(workspace)}
        self.assertTrue(file_tools_scoped([read], workspace, temporary))
        self.assertTrue(
            file_tools_scoped(
                [{"tool": "Glob", "pattern": "**/*", "cwd": str(workspace)}],
                workspace,
                temporary,
            )
        )
        self.assertFalse(
            file_tools_scoped(
                [
                    {
                        "tool": "Glob",
                        "file_path": str(self.root),
                        "pattern": "*",
                        "cwd": str(workspace),
                    }
                ],
                workspace,
                temporary,
            )
        )
        self.assertFalse(
            file_tools_scoped(
                [{**read, "file_path": "../private.txt"}], workspace, temporary
            )
        )
        self.assertFalse(
            file_tools_scoped([{**read, "tool": "Write"}], workspace, temporary)
        )
        self.assertTrue(
            file_tools_scoped(
                [{**read, "tool": "Edit", "file_path": "output/report.md"}],
                workspace,
                temporary,
            )
        )

    def test_owned_cleanup_fixture_preserves_unrelated_scratch_and_deliverables(
        self,
    ) -> None:
        """Exercise real creation/removal and reject receipts that claim other ownership."""
        cases = json.loads((ROOT / "evals/live/scenarios.json").read_text())[
            "scenarios"
        ]
        case = next(
            item
            for item in cases
            if item["id"] == "draft-only-review-with-owned-cleanup"
        )
        manifest = LIVE.prepare(case, self.root / "cleanup", self.settings)
        workspace, temporary = Path(manifest["workspace"]), Path(manifest["temporary"])
        unrelated = temporary / "other-session.txt"
        unrelated.write_text("keep other work")
        board_marker = temporary / "duet" / "retained-record.json"
        board_marker.parent.mkdir()
        board_marker.write_text("durable collaboration state")
        result = subprocess.run(
            [sys.executable, "-B", str(workspace / "case_checks.py")],
            cwd=workspace,
            env={**os.environ, "TMPDIR": str(temporary)},
            check=False,
            capture_output=True,
            text=True,
            timeout=self.settings.wall_seconds,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = case["scratch_cleanup_receipt"]
        self.assertTrue(LIVE.scratch_cleanup_verified(workspace, temporary, receipt))
        self.assertEqual(unrelated.read_text(), "keep other work")
        self.assertEqual(board_marker.read_text(), "durable collaboration state")
        self.assertTrue((workspace / receipt).is_file())
        self.assertEqual(
            LIVE.input_inventory(workspace / "inputs"), manifest["input_tree_inventory"]
        )
        recorded = json.loads((workspace / receipt).read_text())
        self.assertFalse(Path(recorded["scratch_root"]).exists())
        for replacement in (
            {**recorded, "created": False},
            {**recorded, "removed": False},
            {**recorded, "scratch_root": str(temporary)},
            {**recorded, "scratch_root": str(self.root / "not-owned")},
            {**recorded, "scratch_root": str(unrelated)},
        ):
            (workspace / receipt).write_text(json.dumps(replacement))
            self.assertFalse(
                LIVE.scratch_cleanup_verified(workspace, temporary, receipt)
            )
