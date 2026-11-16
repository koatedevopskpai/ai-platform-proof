"""Tests for scripts/verify.py.

The probe layer produced three separate false reports while the tool was being built — each one a
plausible-looking result that was wrong. These tests exist so the same class of failure cannot
return silently:

    verify.py reported the wrong thing, not a crash.

Each case below corresponds to one of those incidents, so a regression fails a named test rather
than quietly reducing coverage.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("verify", REPO / "scripts" / "verify.py")
verify = importlib.util.module_from_spec(_SPEC)
# Register before exec: @dataclass resolves annotations through sys.modules, and Python 3.14
# raises AttributeError on an unregistered module.
sys.modules[_SPEC.name] = verify
_SPEC.loader.exec_module(verify)


class LocalCommandTests(unittest.TestCase):
    """`local_command` must produce something the OS can actually execute."""

    def test_empty_command_is_returned_unchanged(self):
        self.assertEqual(verify.local_command([]), [])

    def test_unresolvable_command_is_returned_unchanged(self):
        # The caller reports "not found"; this function must not raise.
        self.assertEqual(
            verify.local_command(["definitely-not-a-real-binary-xyz", "--flag"]),
            ["definitely-not-a-real-binary-xyz", "--flag"],
        )

    def test_absolute_path_stays_executable(self):
        got = verify.local_command([sys.executable, "--version"])
        self.assertTrue(Path(got[0]).exists())
        self.assertEqual(got[1:], ["--version"])

    @unittest.skipUnless(os.name == "nt", "Windows shim behaviour")
    def test_cmd_shim_is_wrapped_in_cmd_c(self):
        # Incident 3: npm.CMD exists and works in a shell, but CreateProcess cannot
        # execute a batch file directly, so subprocess.run(["npm", ...]) raised
        # FileNotFoundError and the step was skipped as "not found".
        with tempfile.TemporaryDirectory() as tmp:
            shim = Path(tmp) / "faux-tool.cmd"
            shim.write_text("@echo off\r\necho 1.0\r\n", encoding="utf-8")
            got = verify.local_command([str(shim), "--version"])
            self.assertEqual(got[:2], ["cmd", "/c"])
            self.assertEqual(got[2], str(shim))
            self.assertEqual(got[3:], ["--version"])

    def test_no_shim_wrapping_on_posix(self):
        if os.name == "nt":
            self.skipTest("POSIX only")
        self.assertEqual(verify.local_command([sys.executable])[0], shutil.which(sys.executable))


class ProbeTests(unittest.TestCase):
    """A probe answers one question: can this step run here?"""

    def test_no_probe_means_available(self):
        self.assertEqual(verify.probe_ok(None), (True, ""))

    def test_passing_probe(self):
        ok, reason = verify.probe_ok([sys.executable, "--version"])
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_failing_probe_is_not_available(self):
        ok, reason = verify.probe_ok([sys.executable, "-c", "import sys; sys.exit(3)"])
        self.assertFalse(ok)
        self.assertIn("failed", reason)

    def test_missing_command_is_not_available(self):
        ok, reason = verify.probe_ok(["definitely-not-a-real-binary-xyz"])
        self.assertFalse(ok)
        self.assertIn("not found", reason)

    def test_probe_distinguishes_failure_from_absence(self):
        # Incident 2: a host interpreter that imports pytest but not SQLAlchemy was
        # reported as a test FAIL rather than an environment skip. Both outcomes are
        # "not available", but the reason must say which happened.
        _, failing = verify.probe_ok([sys.executable, "-c", "raise ImportError('boom')"])
        _, missing = verify.probe_ok(["definitely-not-a-real-binary-xyz"])
        self.assertNotEqual(failing, missing)


class StepDefinitionTests(unittest.TestCase):
    """Structural checks, so a malformed step cannot be added silently."""

    def test_step_names_are_unique(self):
        names = [s.name for s in verify.STEPS]
        self.assertEqual(len(names), len(set(names)))

    def test_every_step_has_a_way_to_run(self):
        for step in verify.STEPS:
            with self.subTest(step=step.name):
                self.assertTrue(step.local_cmds, f"{step.name} has no local command")
                if step.docker_image:
                    self.assertTrue(step.docker_cmd, f"{step.name} has an image but no command")

    def test_retrieval_step_declares_its_database_requirement(self):
        retrieval = next(s for s in verify.STEPS if s.name == "retrieval")
        self.assertTrue(retrieval.needs_db)

    def test_docker_paths_use_forward_slashes(self):
        # A Windows backslash path in -v breaks the container mount.
        self.assertNotIn("\\", verify.DOCKER_REPO)


class CliContractTests(unittest.TestCase):
    """The command-line surface behaves as documented.

    These tests must never invoke a step that runs this suite — `--only contracts` would
    re-enter `unittest discover`, spawn verify.py again, and recurse indefinitely. `--list`
    exists so the surface can be tested without executing anything.
    """

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(REPO / "scripts" / "verify.py"), *args],
            capture_output=True, text=True, cwd=REPO, timeout=120,
        )

    def test_unknown_step_is_rejected(self):
        result = self._run("--only", "no-such-step")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Unknown step", result.stdout)

    def test_available_steps_are_listed_on_rejection(self):
        result = self._run("--only", "no-such-step")
        self.assertIn("contracts", result.stdout)

    def test_list_prints_every_step(self):
        result = self._run("--list")
        self.assertEqual(result.returncode, 0)
        for step in verify.STEPS:
            with self.subTest(step=step.name):
                self.assertIn(step.name, result.stdout)

    def test_list_executes_nothing(self):
        # Guards the recursion hazard described in the class docstring: --list must be
        # fast and side-effect free.
        result = self._run("--list")
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("verify  mode=", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
