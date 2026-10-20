# Copyright 2026 Adam Gogacz
# SPDX-License-Identifier: Apache-2.0

"""Exercise low-overhead setup and acknowledgments without weakening recovery."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from board import publish
from configure import configure
from delivery import acknowledge, receive
from settings import DuetError, Settings, encoded
from test_duet import BoardFixture


class RoutineTests(BoardFixture):
    """Keep routine coordination autonomous while preserving exact processed IDs."""

    def test_existing_configuration_without_new_optional_default_is_reused(
        self,
    ) -> None:
        configured = configure(self.root, "legacy-task", ("alpha", "beta"))
        path = Path(configured["settings_path"])
        data = json.loads(path.read_text())
        data.pop("max_artifact_bytes")
        path.write_text(json.dumps(data))
        saved = path.read_bytes()
        repeated = configure(self.root, "legacy-task", ("alpha", "beta"))
        self.assertFalse(repeated["created"])
        self.assertEqual(path.read_bytes(), saved)

    def test_existing_configuration_symlink_is_refused(self) -> None:
        configured = configure(self.root, "linked-task", ("alpha", "beta"))
        path = Path(configured["settings_path"])
        target = self.root / "retained-config.json"
        path.rename(target)
        path.symlink_to(target)
        with self.assertRaises((DuetError, OSError)):
            configure(self.root, "linked-task", ("alpha", "beta"))

    def test_setup_is_deterministic_and_does_not_touch_the_workspace(self) -> None:
        workspace = self.root / "workspace"
        workspace.mkdir()
        first = configure(workspace, "task", ("beta", "alpha"))
        second = configure(workspace, "task", ("alpha", "beta"))
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["settings_path"], second["settings_path"])
        self.assertEqual(first["initial_proposer"], "alpha")
        self.assertFalse(list(workspace.iterdir()))
        settings = Settings.load(Path(first["settings_path"]))
        self.assertTrue(settings.board.is_relative_to(Path(os.environ["TMPDIR"])))
        self.assertEqual(settings.board.parent, self.root / "duet")
        self.assertRegex(settings.board.name, r"^workspace-[0-9a-f]{64}$")
        self.assertEqual(settings.context_revision, "initial")
        self.assertEqual(settings.participants, ("alpha", "beta"))

    def test_project_board_separates_tasks_and_disambiguates_same_named_workspaces(
        self,
    ) -> None:
        first_root = self.root / "first" / "project"
        other_root = self.root / "second" / "project"
        first_root.mkdir(parents=True)
        other_root.mkdir(parents=True)
        first = configure(first_root, "task-a", ("alpha", "beta"))
        second = configure(first_root, "task-b", ("alpha", "beta"))
        other = configure(other_root, "task-a", ("alpha", "beta"))
        self.assertEqual(first["board"], second["board"])
        self.assertNotEqual(first["settings_path"], second["settings_path"])
        self.assertNotEqual(first["board"], other["board"])
        for configured in (first, second):
            settings = Settings.load(Path(configured["settings_path"]))
            message = json.loads(
                self.message("same-id", "inform", body={"text": settings.goal})
            )
            message["goal"] = settings.goal
            publish(settings, "alpha", "session-alpha", encoded(message))
            self.assertEqual(
                receive(settings, "alpha").snapshot.messages[0].body["text"],
                settings.goal,
            )

    def test_simultaneous_setup_creates_one_shared_configuration(self) -> None:
        def setup(_: int) -> dict:
            return configure(self.root, "task", ("alpha", "beta"))

        with ThreadPoolExecutor() as pool:
            results = list(pool.map(setup, range(8)))
        self.assertEqual(sum(result["created"] for result in results), 1)
        self.assertEqual(len({result["settings_path"] for result in results}), 1)

    def test_changed_setup_options_cannot_overwrite_an_existing_task(self) -> None:
        result = configure(
            self.root, "task", ("alpha", "beta"), options={"wait_timeout_seconds": 10}
        )
        path = Path(result["settings_path"])
        original = path.read_bytes()
        with self.assertRaises(DuetError):
            configure(
                self.root,
                "task",
                ("alpha", "beta"),
                options={"wait_timeout_seconds": 20},
            )
        self.assertEqual(path.read_bytes(), original)

    def test_setup_dry_run_and_python_temp_fallback(self) -> None:
        result = configure(self.root, "task", ("alpha", "beta"), dry_run=True)
        self.assertFalse(Path(result["board"]).exists())
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("settings.tempfile.gettempdir", return_value=str(self.root)),
        ):
            fallback = configure(self.root, "task", ("alpha", "beta"))
        self.assertTrue(Path(fallback["board"]).is_relative_to(self.root / "duet"))

    def test_nonexistent_tmpdir_uses_python_temp_lookup(self) -> None:
        with (
            patch.dict(os.environ, {"TMPDIR": str(self.root / "missing")}),
            patch(
                "settings.tempfile.gettempdir", return_value=str(self.root)
            ) as lookup,
        ):
            result = configure(self.root, "fallback-task", ("alpha", "beta"))
        lookup.assert_called()
        self.assertEqual(result["temp_directory"], str(self.root))
        self.assertTrue(Path(result["settings_path"]).is_file())

    def test_existing_tmpdir_does_not_use_the_fallback(self) -> None:
        with patch("settings.tempfile.gettempdir") as lookup:
            result = configure(self.root, "explicit-temp-task", ("alpha", "beta"))
        lookup.assert_not_called()
        self.assertEqual(result["temp_directory"], str(self.root))

    def test_setup_rejects_identity_overrides_and_invalid_options(self) -> None:
        for options in ({"goal": "other"}, {"max_messages": 0}, {"unknown": True}):
            with self.subTest(options=options), self.assertRaises(DuetError):
                configure(self.root, "task", ("alpha", "beta"), options=options)

    def test_minimal_settings_use_declared_defaults_and_explicit_values_win(
        self,
    ) -> None:
        required = {
            "goal": "task",
            "context_revision": "initial",
            "participants": ["alpha", "beta"],
            "board": str(self.root / "board"),
        }
        default = Settings.from_data(required)
        configured = Settings.from_data(
            {**required, "wait_timeout_seconds": 7, "max_messages": 12}
        )
        self.assertGreater(default.wait_timeout_seconds, 0)
        self.assertEqual(configured.wait_timeout_seconds, 7)
        self.assertEqual(configured.max_messages, 12)

    def test_setup_cli_reports_a_ready_to_use_config_path(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "duet.py"),
                "setup",
                "--workspace",
                str(self.root),
                "--goal",
                "task",
                "--participants",
                "alpha",
                "beta",
            ],
            capture_output=True,
            check=False,
            timeout=self.settings.wait_timeout_seconds,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        configured = json.loads(result.stdout)
        received = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "duet.py"),
                "receive",
                "--config",
                configured["settings_path"],
                "--participant",
                "alpha",
            ],
            capture_output=True,
            check=False,
            timeout=self.settings.wait_timeout_seconds,
        )
        self.assertEqual(received.returncode, 0, received.stderr)
        self.assertEqual(
            json.loads(received.stdout)["startup"]["phase"], "awaiting-readiness"
        )

    def test_ack_records_only_explicit_ids_and_does_not_consume_later_arrivals(
        self,
    ) -> None:
        self.ready()
        self.send(
            self.message(
                "first", "inform", sender="beta", body={"text": "Handled finding"}
            )
        )
        self.send(
            self.message(
                "later", "inform", sender="beta", body={"text": "Arrived afterwards"}
            )
        )
        first = self.cli("ack", extra=("--messages", "beta-ready", "first"))
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(
            json.loads(first.stdout)["acknowledged"], ["beta-ready", "first"]
        )
        self.assertEqual(
            [message.id for message in receive(self.settings, "alpha").unread],
            ["later"],
        )
        repeated = self.cli("ack", extra=("--messages", "first"))
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertEqual(json.loads(repeated.stdout)["processed_count"], 2)

    def test_ack_preserves_active_notes_and_uncertain_external_actions(self) -> None:
        self.ready()
        checkpoint = self.checkpoint()
        checkpoint["processed"] = ["beta-ready"]
        checkpoint["outgoing"] = ["alpha-ready"]
        checkpoint["active"] = [
            {
                "subject": {"type": "goal", "id": "goal-1"},
                "summary": "Recover this commitment",
                "causes": ["beta-ready"],
            }
        ]
        checkpoint["external_actions"] = [
            {
                "id": "interrupted-action",
                "status": "uncertain",
                "summary": "Inspect outcome before retry",
            }
        ]
        saved = self.cli("checkpoint", raw=encoded(checkpoint))
        self.assertEqual(saved.returncode, 0, saved.stderr)
        self.send(
            self.message(
                "finding", "inform", sender="beta", body={"text": "New finding"}
            )
        )
        acknowledge(self.settings, "alpha", "session-alpha", ["finding"])
        recovered = receive(self.settings, "alpha").checkpoint
        self.assertEqual(recovered["active"], checkpoint["active"])
        self.assertEqual(recovered["external_actions"], checkpoint["external_actions"])

    def test_ack_dry_run_unknown_ids_and_corrupt_checkpoint(self) -> None:
        self.ready()
        acknowledge(
            self.settings, "alpha", "session-alpha", ["beta-ready"], dry_run=True
        )
        self.assertIsNone(receive(self.settings, "alpha").checkpoint)
        with self.assertRaisesRegex(DuetError, "available valid"):
            acknowledge(self.settings, "alpha", "session-alpha", ["not-delivered"])
        path = self.settings.board / "checkpoints" / "goal-1" / "alpha.json"
        path.parent.mkdir(parents=True)
        path.write_text('{"goal":"wrong"}')
        before = path.read_bytes()
        with self.assertRaisesRegex(DuetError, "recover"):
            acknowledge(self.settings, "alpha", "session-alpha", ["beta-ready"])
        self.assertEqual(path.read_bytes(), before)
