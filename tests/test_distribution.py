# Copyright 2026 Adam Gogacz
# SPDX-License-Identifier: Apache-2.0

"""Verify the documented portable bundle works outside its source checkout."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from settings import temporary_directory


class DistributionTests(unittest.TestCase):
    """Install only documented runtime resources, without package installation."""

    def setUp(self) -> None:
        """Keep copied code, consuming projects, and both temp roots isolated."""
        self.scratch = tempfile.TemporaryDirectory(
            prefix="duet-distribution-tests-", dir=temporary_directory()
        )
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.installed = self.root / "installed" / "duet"
        self.installed.mkdir(parents=True)
        for name in ("SKILL.md", "LICENSE"):
            shutil.copy2(ROOT / name, self.installed / name)
        for name in ("scripts", "references"):
            shutil.copytree(
                ROOT / name,
                self.installed / name,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        self.project = self.root / "project"
        self.project.mkdir()
        self.temps = (self.root / "client-a-temp", self.root / "client-b-temp")
        for folder in self.temps:
            folder.mkdir()

    def command(
        self, *args: str, peer: int = 0, text: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run the installed utility with no source-path or user-package fallback."""
        return subprocess.run(
            [
                sys.executable,
                "-B",
                "-S",
                str(self.installed / "scripts" / "duet.py"),
                *args,
            ],
            cwd=self.project,
            env={
                **os.environ,
                "TMPDIR": str(self.temps[peer]),
                "PYTHONPATH": "",
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            input=text,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )

    def result(self, *args: str, **kwargs: Any) -> dict[str, Any]:
        """Require usable JSON from a successful installed command."""
        outcome = self.command(*args, **kwargs)
        self.assertEqual(outcome.returncode, 0, outcome.stderr)
        return json.loads(outcome.stdout)

    def test_minimum_copy_pairs_and_exchanges_messages_outside_checkout(self) -> None:
        request = "Review this independent installation and agree a report."
        peers = [
            self.result(
                "join", "--workspace", str(self.project), peer=index, text=request
            )
            for index in range(2)
        ]
        self.assertEqual([peer["status"] for peer in peers], ["waiting", "paired"])
        self.assertEqual(peers[0]["board"], peers[1]["board"])
        self.assertTrue(Path(peers[0]["board"]).is_relative_to(self.temps[0]))
        for index, peer in enumerate(peers):
            self.result(
                "send",
                "--connection",
                peer["connection_path"],
                "--kind",
                "ready",
                "--text",
                "Ready through the standalone installed bundle.",
                peer=index,
            )
        received = self.result(
            "receive", "--connection", peers[0]["connection_path"], "--unread"
        )
        self.assertEqual(received["collaboration"]["state"], "paired")
        self.assertEqual(len(received["messages"]), 1)
        self.assertEqual(received["messages"][0]["from"], "peer-b")
        self.assertFalse(list(self.installed.rglob("__pycache__")))

    def test_installed_help_has_no_dependency_on_repository_tools(self) -> None:
        outcome = self.command("--help")
        self.assertEqual(outcome.returncode, 0, outcome.stderr)
        self.assertIn("join", outcome.stdout)
        self.assertFalse((self.installed / "tests").exists())
        self.assertFalse((self.installed / "docs").exists())
        self.assertFalse((self.installed / ".git").exists())

    def test_installed_skill_is_licensed_and_self_contained(self) -> None:
        """Validate shipped metadata, license, and bundled references."""
        manifest = (self.installed / "SKILL.md").read_text()
        self.assertIn("name: duet\n", manifest)
        self.assertIn("license: Apache-2.0\n", manifest)
        self.assertIn("Apache License", (self.installed / "LICENSE").read_text())
        for target in re.findall(r"\]\(([^)]+)\)", manifest):
            if not target.startswith(("https://", "http://", "#")):
                self.assertTrue((self.installed / target).is_file(), target)

    def test_minimum_copy_completes_gate_bound_review_with_short_handles(self) -> None:
        """Exercise the public workflow from a copy with distinct caller temp roots."""
        from concurrent.futures import ThreadPoolExecutor

        peers = [
            self.result(
                "join",
                "--workspace",
                str(self.project),
                peer=index,
                text="Review the fixture and agree on an evidence-based report",
            )
            for index in range(2)
        ]
        for index, peer in enumerate(peers):
            self.result(
                "send",
                "--connection",
                peer["connection"],
                "--kind",
                "ready",
                "--text",
                "Ready to negotiate the fixture gate",
                peer=index,
            )
        plan = self.result(
            "send",
            "--connection",
            peers[0]["connection"],
            "--kind",
            "propose",
            "--scope",
            "work-division",
            "--criteria",
            "Deliver the checked fixture report",
            "--stop-condition",
            "Stop incomplete if the bounded fixture checks cannot establish the result",
            "--text",
            "A writes the report; B independently checks it",
            peer=0,
        )
        self.result(
            "send",
            "--connection",
            peers[1]["connection"],
            "--kind",
            "agree",
            "--reply-to",
            plan["id"],
            peer=1,
        )
        report = self.temps[0] / "report.md"
        report.write_text("Fixture report with checked evidence\n")
        proposed = self.command(
            "complete",
            "--connection",
            peers[0]["connection"],
            "--propose",
            "--artifact",
            str(report),
            "--evidence",
            "Both inspected the fixture evidence",
            "--text",
            "Whole fixture review complete",
            "--timeout-seconds",
            "0.01",
            peer=0,
        )
        self.assertEqual(proposed.returncode, 2, proposed.stderr)
        offer = json.loads(proposed.stdout)["proposal"]

        def close(index: int) -> subprocess.CompletedProcess[str]:
            args = [
                "complete",
                "--connection",
                peers[index]["connection"],
                "--timeout-seconds",
                "3",
            ]
            if index:
                args.extend(["--agree", offer])
            return self.command(*args, peer=index)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(close, range(2)))
        for result in results:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "complete")
