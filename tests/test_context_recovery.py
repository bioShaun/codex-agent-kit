import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "bundle/context-state.py"
spec = importlib.util.spec_from_file_location("context_installer", ROOT / "install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class ContextRecoveryTests(unittest.TestCase):
    def setUp(self):
        work = ROOT / ".work"
        work.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="context-", dir=work)
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.fields = {"goal": "Keep the original goal", "constraints": "Do not alter unrelated files",
                       "next_step": "Run the focused check", "progress": "Implementation pending",
                       "decisions": "Use the existing API", "evidence": "checks.log", "jobs": "No running jobs"}

    def cli(self, *args, data=None, session="session-a"):
        self.assertTrue(HELPER.is_file(), "task state helper has not been implemented")
        env = dict(os.environ, CODEX_THREAD_ID=session)
        command_args = list(args) if args[0] == "hook" else [args[0], "--project", str(self.project), *args[1:]]
        return subprocess.run([sys.executable, str(HELPER), *command_args], cwd=self.project,
                              env=env, input=json.dumps(data) if data is not None else None,
                              text=True, capture_output=True)

    def init(self, task="task-a", session="session-a", fields=None):
        result = self.cli("init", "--task", task, "--from-file", "-",
                          data=self.fields if fields is None else fields, session=session)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def hook(self, session="session-a", source="compact", cwd=None):
        result = self.cli("hook", data={"hook_event_name": "SessionStart", "source": source,
                                        "session_id": session, "cwd": str(cwd or self.project)})
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_compact_restores_bound_task_and_latest_checkpoint(self):
        self.init()
        updated = {**self.fields, "next_step": "Resume at the latest checkpoint"}
        saved = self.cli("save", "--from-file", "-", data=updated)
        self.assertEqual(saved.returncode, 0, saved.stderr)
        payload = self.hook()
        context = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn(updated["next_step"], context)
        self.assertIn(self.fields["goal"], context)
        self.assertEqual(payload["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertNotIn("continue", payload)

    def test_sessions_do_not_read_other_tasks_and_subdirectories_resolve_project(self):
        self.init()
        self.init("task-b", "session-b", {**self.fields, "goal": "Separate task B"})
        context = self.hook("session-b")["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Separate task B", context)
        self.assertNotIn(self.fields["goal"], context)
        sub = self.project / "src"
        sub.mkdir()
        self.assertIn(self.fields["goal"], self.hook(cwd=sub)["hookSpecificOutput"]["additionalContext"])
        unbound = json.dumps(self.hook("new-session"), ensure_ascii=False)
        self.assertNotIn(self.fields["goal"], unbound)
        self.assertNotIn("Separate task B", unbound)

    def test_init_does_not_overwrite_and_takeover_is_explicit(self):
        self.init()
        self.assertNotEqual(self.cli("init", "--task", "task-a", "--from-file", "-", data=self.fields).returncode, 0)
        denied = self.cli("bind", "--task", "task-a", session="session-b")
        self.assertNotEqual(denied.returncode, 0)
        accepted = self.cli("bind", "--task", "task-a", "--takeover", session="session-b")
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertNotIn(self.fields["goal"], json.dumps(self.hook("session-a")))
        self.assertIn(self.fields["goal"], self.hook("session-b")["hookSpecificOutput"]["additionalContext"])
        self.assertNotEqual(self.cli("save", "--from-file", "-", data=self.fields).returncode, 0)

    def test_stale_corrupt_and_completed_state_are_not_injected(self):
        result = self.init()
        path = Path(result["state_path"])
        good = path.read_text()
        record = json.loads(good)
        record["updated_at"] = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        path.write_text(json.dumps(record))
        payload = self.hook()
        self.assertIn("stale", json.dumps(payload))
        self.assertNotIn(self.fields["goal"], json.dumps(payload))
        path.write_text("broken json")
        self.assertNotIn(self.fields["goal"], json.dumps(self.hook()))
        path.write_text(good)
        finished = self.cli("finish")
        self.assertEqual(finished.returncode, 0, finished.stderr)
        self.assertNotIn(self.fields["goal"], json.dumps(self.hook()))

    def test_escape_and_symlink_state_are_rejected(self):
        result = self.cli("init", "--task", "../escape", "--from-file", "-", data=self.fields)
        self.assertNotEqual(result.returncode, 0)
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (self.project / ".codex-task-state").symlink_to(outside, target_is_directory=True)
        self.assertNotEqual(self.cli("init", "--task", "task-a", "--from-file", "-", data=self.fields).returncode, 0)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertNotIn(self.fields["goal"], json.dumps(self.hook()))

    def test_copied_project_requires_explicit_adoption(self):
        result = self.init()
        path = Path(result["state_path"])
        record = json.loads(path.read_text())
        record["workspace"]["path"] = "/another/server/project"
        path.write_text(json.dumps(record))
        self.assertNotIn(self.fields["goal"], json.dumps(self.hook()))
        self.assertNotEqual(self.cli("bind", "--task", "task-a", "--takeover").returncode, 0)
        adopted = self.cli("bind", "--task", "task-a", "--takeover", "--adopt-project")
        self.assertEqual(adopted.returncode, 0, adopted.stderr)
        self.assertIn(self.fields["goal"], self.hook()["hookSpecificOutput"]["additionalContext"])

    def test_output_budget_preserves_goal_constraints_and_next_step(self):
        self.init(fields={**self.fields, "progress": "x" * 20000})
        context = self.hook()["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(context), 4000)
        for key in ("goal", "constraints", "next_step"):
            self.assertIn(self.fields[key], context)
        self.assertIn("state.json", context)

    def test_hook_is_read_only_and_ignores_unrelated_events(self):
        self.init()
        state = self.project / ".codex-task-state"
        before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in state.rglob("*") if p.is_file()}
        self.hook()
        after = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in state.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        result = self.cli("hook", data={"hook_event_name": "PreToolUse", "cwd": str(self.project)})
        self.assertEqual(json.loads(result.stdout), {})

    def test_hook_requires_event_session_identity_even_if_environment_has_one(self):
        self.init()
        result = self.cli("hook", data={"hook_event_name": "SessionStart", "source": "compact",
                                        "cwd": str(self.project)})
        payload = json.loads(result.stdout)
        self.assertIn("session_id", json.dumps(payload))
        self.assertNotIn(self.fields["goal"], json.dumps(payload))

    def test_binding_does_not_refresh_stale_semantic_state(self):
        result = self.init()
        path = Path(result["state_path"])
        record = json.loads(path.read_text())
        stamp = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        record["updated_at"] = stamp
        path.write_text(json.dumps(record))
        self.assertEqual(self.cli("bind", "--task", "task-a", "--takeover", session="session-b").returncode, 0)
        self.assertEqual(json.loads(path.read_text())["updated_at"], stamp)
        self.assertNotIn(self.fields["goal"], json.dumps(self.hook("session-b")))

    def test_concurrent_writer_lock_is_not_ignored(self):
        self.init()
        import fcntl
        with (self.project / ".codex-task-state/.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.cli("save", "--from-file", "-", data=self.fields)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("busy", result.stderr)


class ContextInstallTests(unittest.TestCase):
    def setUp(self):
        work = ROOT / ".work"
        work.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="hooks-", dir=work)
        self.addCleanup(self.temp.cleanup)
        self.target = Path(self.temp.name) / "codex home's directory"
        self.target.mkdir()
        self.foreign = {"matcher": "startup", "hooks": [{"type": "command", "command": "existing-hook"}]}

    def plan(self, enabled=None, overwrite=False):
        self.assertIn("context_recovery", __import__("inspect").signature(installer.plan).parameters,
                      "installer must support the optional context recovery module")
        return installer.plan(self.target, overwrite_local=overwrite, context_recovery=enabled)

    def apply(self, enabled=None, overwrite=False):
        desired, old = self.plan(enabled, overwrite)
        installer.apply(self.target, desired, old)
        return desired

    def test_preserves_other_hooks_quotes_paths_and_remembers_opt_in(self):
        original = {"description": "existing hooks", "hooks": {"SessionStart": [self.foreign]}}
        (self.target / "hooks.json").write_text(json.dumps(original))
        self.apply(True)
        hooks = json.loads((self.target / "hooks.json").read_text())
        self.assertEqual(hooks["hooks"]["SessionStart"][0], self.foreign)
        own = hooks["hooks"]["SessionStart"][1]["hooks"][0]
        argv = shlex.split(own["command"])
        self.assertEqual(argv[-2:], [str(self.target / "context-state.py"), "hook"])
        event = {"hook_event_name": "SessionStart", "source": "startup", "session_id": "fresh", "cwd": str(self.target)}
        result = subprocess.run(argv, input=json.dumps(event), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("context-state.py", json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"])
        desired, old = self.plan()
        self.assertEqual(desired, old)
        hooks["hooks"]["Stop"] = [{"hooks": [{"type": "command", "command": "other-stop"}]}]
        (self.target / "hooks.json").write_text(json.dumps(hooks))
        desired, old = self.plan()
        self.assertEqual(desired, old)
        self.apply(False)
        hooks = json.loads((self.target / "hooks.json").read_text())
        self.assertEqual(hooks["hooks"]["SessionStart"], [self.foreign])
        self.assertIn("Stop", hooks["hooks"])

    def test_owned_hook_drift_requires_explicit_overwrite(self):
        self.apply(True)
        path = self.target / "hooks.json"
        hooks = json.loads(path.read_text())
        hooks["hooks"]["SessionStart"][0]["hooks"][0]["timeout"] = 55
        path.write_text(json.dumps(hooks))
        with self.assertRaisesRegex(ValueError, "Local edit"):
            self.plan()
        self.apply(overwrite=True)
        self.assertEqual(json.loads(path.read_text())["hooks"]["SessionStart"][0]["hooks"][0]["timeout"], 10)

    def test_inline_hooks_merge_without_creating_second_hook_source(self):
        (self.target / "config.toml").write_text(installer.dump_toml({"hooks": {"SessionStart": [self.foreign]}}))
        self.apply(True)
        import tomllib
        hooks = tomllib.loads((self.target / "config.toml").read_text())["hooks"]["SessionStart"]
        self.assertEqual(hooks[0], self.foreign)
        self.assertEqual(len(hooks), 2)
        self.assertFalse((self.target / "hooks.json").exists())
        desired, old = self.plan()
        self.assertEqual(desired, old)
        self.apply(False)
        self.assertEqual(tomllib.loads((self.target / "config.toml").read_text())["hooks"]["SessionStart"], [self.foreign])

    def test_conflicting_sources_and_symlinks_are_refused_before_writes(self):
        own = {"hooks": [{"type": "command", "command": "old-context-hook",
                          "statusMessage": installer.CONTEXT_HOOK_MARKER}]}
        (self.target / "config.toml").write_text(installer.dump_toml({"hooks": {"SessionStart": [own]}}))
        (self.target / "hooks.json").write_text(json.dumps({"hooks": {"SessionStart": [own]}}))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.plan(True)
        self.assertFalse((self.target / "context-state.py").exists())
        (self.target / "config.toml").unlink()
        (self.target / "hooks.json").unlink()
        outside = Path(self.temp.name) / "outside.json"
        outside.write_text('{"hooks": {}}')
        (self.target / "hooks.json").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "Symlink"):
            self.plan(True)

    def test_foreign_hooks_in_both_sources_and_hook_trust_settings_are_preserved(self):
        import tomllib
        existing = {"hooks": {"SessionStart": [self.foreign], "trusted_marker": "keep-local-trust"}}
        (self.target / "config.toml").write_text(installer.dump_toml(existing))
        original = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "foreign-stop"}]}]}}
        (self.target / "hooks.json").write_text(json.dumps(original))
        self.apply(True)
        config = tomllib.loads((self.target / "config.toml").read_text())
        self.assertEqual(config["hooks"], existing["hooks"])
        hooks = json.loads((self.target / "hooks.json").read_text())
        self.assertEqual(hooks["hooks"]["Stop"], original["hooks"]["Stop"])
        self.assertEqual(len(hooks["hooks"]["SessionStart"]), 1)
        desired, old = self.plan()
        self.assertEqual(desired, old)

    def test_failed_hook_write_rolls_back_and_preserves_foreign_data(self):
        path = self.target / "hooks.json"
        original = json.dumps({"hooks": {"SessionStart": [self.foreign]}})
        path.write_text(original)
        desired, old = self.plan(True)
        real = installer.atomic_write
        def failing(p, data, mode):
            if p == path:
                raise OSError("injected hook write failure")
            return real(p, data, mode)
        with patch.object(installer, "atomic_write", side_effect=failing):
            with self.assertRaisesRegex(OSError, "injected"):
                installer.apply(self.target, desired, old)
        self.assertEqual(path.read_text(), original)
        self.assertFalse((self.target / "context-state.py").exists())

    def test_disable_after_removed_hook_file_preserves_spawn_drift_protection(self):
        self.apply(True)
        (self.target / "hooks.json").unlink()
        with self.assertRaisesRegex(ValueError, "managed PreToolUse hook removed"):
            self.apply(False)
        self.assertFalse((self.target / "hooks.json").exists())
        self.apply(False, overwrite=True)
        hooks = json.loads((self.target / "hooks.json").read_text())["hooks"]
        self.assertEqual(hooks["PreToolUse"][0]["matcher"], "^collaborationspawn_agent$")
        self.assertNotIn("SessionStart", hooks)
        state = json.loads((self.target / installer.STATE).read_text())
        self.assertFalse(state["context_recovery"]["enabled"])

    def run_check(self, executable: str) -> tuple[int, str]:
        argv = ["install.py", "--target", str(self.target), "--check"]
        stdout = io.StringIO()
        with patch.object(installer.sys, "executable", executable), patch.object(installer.sys, "argv", argv):
            with contextlib.redirect_stdout(stdout):
                code = installer.main()
        return code, stdout.getvalue()

    def context_handlers(self, document: dict) -> list:
        groups = document.get("hooks", {}).get("SessionStart", [])
        return [handler for group in groups for handler in group.get("hooks", [])
                if handler.get("statusMessage") == installer.CONTEXT_HOOK_MARKER]

    def test_context_hook_check_is_stable_across_interpreters(self):
        with patch.object(installer.sys, "executable", "/tmp/venv-a/bin/python"):
            self.apply(True)
        document = json.loads((self.target / "hooks.json").read_text())
        command = self.context_handlers(document)[0]["command"]
        argv = shlex.split(command)
        self.assertEqual(argv, ["python3", str(self.target / "context-state.py"), "hook"])
        with patch.object(installer.sys, "executable", "/tmp/venv-b/bin/python"):
            desired, old = self.plan()
        self.assertEqual(desired, old)
        code, output = self.run_check("/tmp/venv-b/bin/python")
        self.assertEqual(code, 0, output)
        self.assertIn("CHECK PASS", output)
        self.assertNotIn("UPDATE", output)

    def test_legacy_absolute_interpreter_registration_is_kept(self):
        self.apply(True)
        absolute = shlex.join([
            "/tmp/old-venv/bin/python", str(self.target / "context-state.py"), "hook"])
        hooks_path = self.target / "hooks.json"
        document = json.loads(hooks_path.read_text())
        self.context_handlers(document)[0]["command"] = absolute
        hooks_path.write_bytes((json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode())
        state_path = self.target / installer.STATE
        state = json.loads(state_path.read_text())
        state["context_recovery"]["registration"]["hooks"][0]["command"] = absolute
        state_path.write_bytes((json.dumps(state, indent=2) + "\n").encode())

        with patch.object(installer.sys, "executable", "/usr/local/bin/python3.13"):
            desired, old = self.plan()
        self.assertEqual(desired, old)
        code, output = self.run_check("/usr/local/bin/python3.13")
        self.assertEqual(code, 0, output)
        self.assertIn("CHECK PASS", output)
        kept = self.context_handlers(json.loads(hooks_path.read_text()))
        self.assertEqual([handler["command"] for handler in kept], [absolute])

        document = json.loads(hooks_path.read_text())
        self.context_handlers(document)[0]["timeout"] = 55
        hooks_path.write_bytes((json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode())
        with patch.object(installer.sys, "executable", "/usr/local/bin/python3.13"):
            with self.assertRaisesRegex(ValueError, "Local edit detected: context recovery hook"):
                self.plan()
        refused = self.context_handlers(json.loads(hooks_path.read_text()))
        self.assertEqual(len(refused), 1)
        self.assertEqual(refused[0]["command"], absolute)
        self.assertEqual(refused[0]["timeout"], 55)

        with patch.object(installer.sys, "executable", "/opt/other/bin/python"):
            self.apply(overwrite=True)
        restored = self.context_handlers(json.loads(hooks_path.read_text()))
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0]["command"], absolute)
        self.assertEqual(restored[0]["timeout"], 10)
        code, output = self.run_check("/opt/another/bin/python")
        self.assertEqual(code, 0, output)
        self.assertIn("CHECK PASS", output)

        self.apply(False)
        self.assertEqual(self.context_handlers(json.loads(hooks_path.read_text())), [])
        with patch.object(installer.sys, "executable", "/tmp/fresh-venv/bin/python"):
            self.apply(True)
        rebound = self.context_handlers(json.loads(hooks_path.read_text()))
        self.assertEqual(len(rebound), 1)
        self.assertEqual(shlex.split(rebound[0]["command"])[0], "python3")

    def test_cli_opt_in_preview_apply_check(self):
        command = [sys.executable, str(ROOT / "install.py"), "--target", str(self.target), "--with-context-recovery"]
        preview = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertFalse((self.target / "hooks.json").exists())
        applied = subprocess.run(command + ["--apply"], capture_output=True, text=True)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        checked = subprocess.run(command[:-1] + ["--check"], capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertIn("NOT CHECKED", checked.stdout)


if __name__ == "__main__":
    unittest.main()
