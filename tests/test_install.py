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
import tomllib
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("installer", ROOT / "install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(ROOT.is_dir())
        work = ROOT / ".work"
        work.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="test-", dir=work)
        self.addCleanup(self.temp.cleanup)
        self.target = Path(self.temp.name) / "codex home"

    def install(self):
        desired, old = installer.plan(self.target)
        return installer.apply(self.target, desired, old)

    def test_cli_preview_does_not_create_target_then_install_check(self):
        command = [sys.executable, str(ROOT / "install.py"), "--target", str(self.target)]
        preview = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertFalse(self.target.exists())
        missing = subprocess.run(command + ["--check"], capture_output=True, text=True)
        self.assertEqual(missing.returncode, 1)
        installed = subprocess.run(command + ["--apply"], capture_output=True, text=True)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        checked = subprocess.run(command + ["--check"], capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertIn("CHECK PASS", checked.stdout)

    def test_install_preserves_machine_settings_and_other_roles(self):
        self.target.mkdir()
        original = '''# local comment retained in backup
model = "my-root-model"
model_provider = "local-provider"
sandbox_mode = "read-only"
approval_policy = "never"
[mcp_servers.local]
command = "example-tool"
env = { SECRET = "private-value" }
[agents.personal]
config_file = "./personal.toml"
[projects."/my/project"]
trust_level = "trusted"
'''
        (self.target / "config.toml").write_text(original)
        (self.target / "auth.json").write_text("credential-not-for-the-kit")
        (self.target / "AGENTS.md").write_text("My personal rules\n")
        before = tomllib.loads(original)
        backup = self.install()
        after = tomllib.loads((self.target / "config.toml").read_text())
        for key in ("model", "model_provider", "sandbox_mode", "approval_policy", "mcp_servers", "projects"):
            self.assertEqual(before[key], after[key])
        self.assertEqual(before["agents"]["personal"], after["agents"]["personal"])
        self.assertEqual((backup / "config.toml").read_text(), original)
        self.assertEqual((self.target / "auth.json").read_text(), "credential-not-for-the-kit")
        self.assertFalse((backup / "auth.json").exists())
        self.assertTrue((self.target / "AGENTS.md").read_text().startswith("My personal rules\n"))
        self.assertEqual(backup.stat().st_mode & 0o777, 0o700)
        self.assertEqual((backup / "config.toml").stat().st_mode & 0o777, 0o600)

    def test_repeat_install_is_noop_and_preserves_unrelated_edits(self):
        self.install()
        p = self.target / "config.toml"
        p.write_text("# new machine comment\n" + p.read_text())
        first = p.read_bytes()
        before = p.stat().st_mtime_ns
        self.assertIsNone(self.install())
        self.assertEqual(p.stat().st_mtime_ns, before)
        self.assertEqual(p.read_bytes(), first)
        self.assertEqual((self.target / "AGENTS.md").read_text().count(installer.BEGIN), 1)

    def test_uses_effective_nonempty_override_without_replacing_personal_text(self):
        self.target.mkdir()
        regular = self.target / "AGENTS.md"
        override = self.target / "AGENTS.override.md"
        regular.write_text("normal rules")
        override.write_text("override rules")
        self.install()
        self.assertEqual(regular.read_text(), "normal rules")
        self.assertTrue(override.read_text().startswith("override rules"))
        self.assertIn(installer.BEGIN, override.read_text())

    def test_local_drift_refused_and_explicit_override_backed_up(self):
        self.install()
        p = self.target / "agents/astra_worker.toml"
        p.write_text(p.read_text().replace("gpt-6.1-sol", "my-local-model"))
        edited = p.read_bytes()
        with self.assertRaisesRegex(ValueError, "Local edit detected"):
            installer.plan(self.target)
        desired, old = installer.plan(self.target, overwrite_local=True)
        backup = installer.apply(self.target, desired, old)
        self.assertEqual((backup / "agents/astra_worker.toml").read_bytes(), edited)
        self.assertEqual(p.read_bytes(), desired["agents/astra_worker.toml"])

    def test_shared_instruction_symlink_can_be_explicitly_excluded(self):
        self.target.mkdir()
        shared = Path(self.temp.name) / "shared-instructions.md"
        shared.write_text("Shared instructions maintained elsewhere")
        (self.target / "AGENTS.md").symlink_to(shared)
        desired, old = installer.plan(self.target, skip_instructions=True)
        installer.apply(self.target, desired, old)
        self.assertEqual(shared.read_text(), "Shared instructions maintained elsewhere")
        self.assertTrue((self.target / "AGENTS.md").is_symlink())
        desired, old = installer.plan(self.target, skip_instructions=True)
        self.assertEqual(desired, old)

    def test_cloud_bundle_update_propagates_to_existing_install(self):
        self.install()
        import shutil
        bundle = Path(self.temp.name) / "new-bundle"
        shutil.copytree(ROOT / "bundle", bundle)
        p = bundle / "agents/astra_worker.toml"
        p.write_text(p.read_text().replace('model_reasoning_effort = "medium"', 'model_reasoning_effort = "high"'))
        with patch.object(installer, "BUNDLE", bundle):
            self.install()
            self.assertIsNone(self.install())
        installed = tomllib.loads((self.target / "agents/astra_worker.toml").read_text())
        self.assertEqual(installed["model_reasoning_effort"], "high")

    def test_invalid_toml_does_not_write(self):
        self.target.mkdir()
        p = self.target / "config.toml"
        p.write_text("[[[ malformed")
        with self.assertRaises(tomllib.TOMLDecodeError):
            installer.plan(self.target)
        self.assertEqual(list(self.target.iterdir()), [p])

    def test_symlink_target_and_parent_are_rejected(self):
        self.target.mkdir()
        outside = Path(self.temp.name) / "outside.toml"
        outside.write_text('model = "untouched"')
        (self.target / "config.toml").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "Symlink"):
            installer.plan(self.target)
        self.assertEqual(outside.read_text(), 'model = "untouched"')
        alias = Path(self.temp.name) / "alias"
        alias.symlink_to(self.target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Symlink"):
            installer.plan(alias / "child")

    def test_concurrent_edit_is_preserved(self):
        self.target.mkdir()
        desired, old = installer.plan(self.target)
        (self.target / "config.toml").write_text('model = "concurrent"')
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(ValueError, "Concurrent edit"):
                installer.apply(self.target, desired, old)
        self.assertEqual((self.target / "config.toml").read_text(), 'model = "concurrent"')
        self.assertFalse((self.target / "agents/astra_worker.toml").exists())

    def test_write_failure_restores_original_files(self):
        self.target.mkdir()
        role = self.target / "agents/astra_explorer.toml"
        role.parent.mkdir()
        role.write_text("old role bytes")
        role.chmod(0o640)
        desired, old = installer.plan(self.target)
        real_write = installer.atomic_write

        def failing_write(path, data, mode):
            if path == self.target / "agents/astra_worker.toml":
                raise OSError("simulated failure")
            return real_write(path, data, mode)

        with patch.object(installer, "atomic_write", side_effect=failing_write):
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaisesRegex(OSError, "simulated"):
                    installer.apply(self.target, desired, old)
        self.assertEqual(role.read_text(), "old role bytes")
        self.assertEqual(role.stat().st_mode & 0o777, 0o640)
        self.assertFalse((self.target / "agents/astra_locator.toml").exists())

    def test_edit_during_planning_is_not_adopted_as_a_safe_baseline(self):
        self.install()
        for rel in ("config.toml", "agents/astra_worker.toml", "AGENTS.md"):
            with self.subTest(rel=rel):
                p = self.target / rel
                original = p.read_bytes()
                edited = original + b"\n# concurrent user edit\n"
                real_read = installer.read_file
                changed = False

                def race(path):
                    nonlocal changed
                    data = real_read(path)
                    if path == p and not changed:
                        p.write_bytes(edited)
                        changed = True
                    return data

                with patch.object(installer, "read_file", side_effect=race):
                    desired, old = installer.plan(self.target)
                self.assertEqual(old[rel], original)
                # Request a normal update so apply also verifies unchanged files at the end.
                update = "agents/astra_explorer.toml"
                desired[update] += b"\n# repository update\n"
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaisesRegex(ValueError, "verification failed|Concurrent edit"):
                        installer.apply(self.target, desired, old)
                self.assertEqual(p.read_bytes(), edited)
                self.assertEqual((self.target / update).read_bytes(), old[update])
                p.write_bytes(original)

    def test_nested_toml_types_roundtrip(self):
        data = tomllib.loads('''
day = 2026-09-30
when = 2026-09-30T10:00:00+08:00
clock = 10:20:30.123
nan_value = nan
positive_inf = inf
negative_inf = -inf
text = "line\\nquote \\" and tab\\t"
[hooks]
events = [{ matcher = "Bash", hooks = [{ type = "command", command = "echo hi" }] }]
["a.b"."quoted key"]
empty = {}
'''.replace('quote \\"', 'quote \\\"'))
        rendered = installer.dump_toml(data)
        self.assertEqual(installer.toml_value(tomllib.loads(rendered)), installer.toml_value(data))

    def test_codex_home_environment_and_explicit_target_precedence(self):
        other = Path(self.temp.name) / "other"
        env = dict(os.environ, CODEX_HOME=str(self.target))
        command = [sys.executable, str(ROOT / "install.py")]
        result = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertIn(f"Target: {self.target}", result.stdout)
        result = subprocess.run(command + ["--target", str(other)], env=env, capture_output=True, text=True)
        self.assertIn(f"Target: {other}", result.stdout)
        self.assertFalse(self.target.exists())
        self.assertFalse(other.exists())

    def test_installed_workflow_status_and_shell_parse(self):
        self.install()
        result = subprocess.run([sys.executable, str(self.target / "review-workflow.py"), "status"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertIn("protocol_sha256", data)
        result = subprocess.run(["bash", "-n", str(self.target / "review-readonly.sh")], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        planner = (self.target / "astra-planner.md").read_text()
        self.assertNotIn("@@CODEX_HOME", planner)
        self.assertIn(f"python3 '{self.target}'/review-workflow.py", planner)

    def hook_group(self, document: dict) -> dict:
        groups = [group for group in document["hooks"]["PreToolUse"]
                  if group.get("matcher") == installer.SPAWN_MATCHER]
        self.assertEqual(len(groups), 1)
        return groups[0]

    def assert_managed_command(self, command: str):
        parts = shlex.split(command)
        self.assertEqual(parts[0], "python3")
        self.assertEqual(Path(parts[1]), (self.target / installer.HOOK_SCRIPT).absolute())

    def test_fresh_install_writes_spawn_hook(self):
        desired, old = installer.plan(self.target)
        self.assertIn(installer.HOOK_SCRIPT, desired)
        self.assertIn(installer.HOOKS_JSON, desired)
        self.assertIsNone(old[installer.HOOKS_JSON])
        self.assertFalse(self.target.exists())
        self.install()
        script = self.target / installer.HOOK_SCRIPT
        self.assertEqual(script.read_bytes(), (ROOT / "bundle" / installer.HOOK_SCRIPT).read_bytes())
        document = json.loads((self.target / "hooks.json").read_text())
        group = self.hook_group(document)
        self.assertEqual(group["matcher"], "^collaborationspawn_agent$")
        hook = group["hooks"][0]
        self.assertEqual(hook["type"], "command")
        self.assertEqual(hook["timeout"], 10)
        self.assertNotIn("async", hook)
        self.assert_managed_command(hook["command"])
        self.assertNotIn("collaborationspawn_agent", (self.target / "config.toml").read_text())
        state = json.loads((self.target / installer.STATE).read_text())
        self.assertIn(installer.HOOK_SCRIPT, state["files"])
        self.assertNotIn("hooks.json", state["files"])
        self.assertIn(installer.HOOK_GROUP_STATE, state["hook_groups"])
        proc = subprocess.run(
            [sys.executable, str(script)],
            input=json.dumps({
                "hook_event_name": "PreToolUse",
                "tool_name": "collaborationspawn_agent",
                "tool_input": {"agent_type": "astra_locator", "task_name": "installed", "message": "hi"},
            }).encode(),
            capture_output=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        updated = json.loads(proc.stdout)["hookSpecificOutput"]["updatedInput"]
        self.assertEqual(updated["fork_turns"], "none")
        self.assertEqual(updated["message"], "hi")

    def test_hook_install_is_idempotent(self):
        self.install()
        hooks = self.target / "hooks.json"
        script = self.target / installer.HOOK_SCRIPT
        hooks_bytes = hooks.read_bytes()
        script_mtime = script.stat().st_mtime_ns
        hooks_mtime = hooks.stat().st_mtime_ns
        self.assertIsNone(self.install())
        self.assertEqual(hooks.read_bytes(), hooks_bytes)
        self.assertEqual(hooks.stat().st_mtime_ns, hooks_mtime)
        self.assertEqual(script.stat().st_mtime_ns, script_mtime)

    def test_existing_user_hooks_are_preserved(self):
        self.target.mkdir()
        original = {
            "description": "本地 hooks",
            "hooks": {
                "PreToolUse": [
                    {"matcher": "^Bash$", "hooks": [{"type": "command", "command": "echo user-bash"}]}
                ],
                "Stop": [
                    {"hooks": [{"type": "command", "command": "echo 停止"}]}
                ],
            },
        }
        path = self.target / "hooks.json"
        path.write_text(json.dumps(original, ensure_ascii=False, indent=2) + "\n")
        self.install()
        document = json.loads(path.read_text())
        self.assertEqual(document["description"], "本地 hooks")
        self.assertEqual(document["hooks"]["Stop"], original["hooks"]["Stop"])
        self.assertEqual(document["hooks"]["PreToolUse"][0], original["hooks"]["PreToolUse"][0])
        self.assertEqual(self.hook_group(document)["matcher"], "^collaborationspawn_agent$")
        self.assertIsNone(self.install())
        added = json.loads(path.read_text())
        added["hooks"]["PostToolUse"] = [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo after"}]}
        ]
        edited = json.dumps(added, ensure_ascii=False, indent=2) + "\n"
        path.write_text(edited)
        self.assertIsNone(self.install())
        self.assertEqual(path.read_text(), edited)
        self.assertEqual(json.loads(path.read_text())["hooks"]["PostToolUse"][0]["hooks"][0]["command"],
                         "echo after")

    def test_local_hook_script_and_config_edits_are_refused(self):
        self.install()
        script = self.target / installer.HOOK_SCRIPT
        script.write_text(script.read_text() + "\n# local\n")
        with self.assertRaisesRegex(ValueError, "Local edit detected"):
            installer.plan(self.target)
        script.write_bytes((ROOT / "bundle" / installer.HOOK_SCRIPT).read_bytes())
        path = self.target / "hooks.json"
        document = json.loads(path.read_text())
        document["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"] = 1
        document["hooks"]["Stop"] = [{"hooks": [{"type": "command", "command": "echo keep-me"}]}]
        path.write_text(json.dumps(document, indent=2) + "\n")
        edited = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "Local edit detected"):
            installer.plan(self.target)
        self.assertEqual(path.read_bytes(), edited)
        desired, old = installer.plan(self.target, overwrite_local=True)
        backup = installer.apply(self.target, desired, old)
        self.assertEqual((backup / "hooks.json").read_bytes(), edited)
        restored = json.loads(path.read_text())
        self.assertEqual(restored["hooks"]["Stop"][0]["hooks"][0]["command"], "echo keep-me")
        self.assertEqual(self.hook_group(restored)["hooks"][0]["timeout"], 10)
        self.assert_managed_command(self.hook_group(restored)["hooks"][0]["command"])

    def test_mixed_handler_group_is_not_replaced(self):
        self.install()
        path = self.target / "hooks.json"
        document = json.loads(path.read_text())
        document["hooks"]["PreToolUse"][0]["hooks"].append(
            {"type": "command", "command": "echo user-in-same-group"})
        path.write_text(json.dumps(document) + "\n")
        original = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "reconcile manually"):
            installer.plan(self.target, overwrite_local=True)
        self.assertEqual(path.read_bytes(), original)

    def test_inline_hooks_receive_the_managed_group_without_hooks_json(self):
        self.target.mkdir()
        original = '''model = "my-root-model"

[[hooks.Stop]]

[[hooks.Stop.hooks]]
type = "command"
command = "echo inline-stop"

[[hooks.PreToolUse]]
matcher = "^Bash$"

[[hooks.PreToolUse.hooks]]
type = "command"
command = "echo inline-bash"
'''
        (self.target / "config.toml").write_text(original)
        backup = self.install()
        self.assertFalse((self.target / "hooks.json").exists())
        self.assertEqual((backup / "config.toml").read_text(), original)
        parsed = tomllib.loads((self.target / "config.toml").read_text())
        self.assertEqual(parsed["model"], "my-root-model")
        self.assertEqual(parsed["hooks"]["Stop"][0]["hooks"][0]["command"], "echo inline-stop")
        pre = parsed["hooks"]["PreToolUse"]
        self.assertEqual(pre[0]["matcher"], "^Bash$")
        self.assertEqual(pre[0]["hooks"][0]["command"], "echo inline-bash")
        self.assertEqual(pre[1]["matcher"], "^collaborationspawn_agent$")
        self.assert_managed_command(pre[1]["hooks"][0]["command"])
        config_mtime = (self.target / "config.toml").stat().st_mtime_ns
        self.assertIsNone(self.install())
        self.assertEqual((self.target / "config.toml").stat().st_mtime_ns, config_mtime)
        self.assertFalse((self.target / "hooks.json").exists())

    def test_hooks_json_and_inline_hooks_are_not_both_rewritten(self):
        self.target.mkdir()
        config = '''model = "keep-me"

[[hooks.PreToolUse]]
matcher = "^Bash$"

[[hooks.PreToolUse.hooks]]
type = "command"
command = "echo inline-user"
'''
        (self.target / "config.toml").write_text(config)
        (self.target / "hooks.json").write_text(json.dumps({
            "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo json-user"}]}]}
        }, indent=2) + "\n")
        self.install()
        parsed = tomllib.loads((self.target / "config.toml").read_text())
        self.assertEqual(parsed["model"], "keep-me")
        self.assertEqual(len(parsed["hooks"]["PreToolUse"]), 1)
        self.assertEqual(parsed["hooks"]["PreToolUse"][0]["hooks"][0]["command"], "echo inline-user")
        document = json.loads((self.target / "hooks.json").read_text())
        self.assertEqual(document["hooks"]["Stop"][0]["hooks"][0]["command"], "echo json-user")
        self.assertEqual(self.hook_group(document)["matcher"], "^collaborationspawn_agent$")

    def test_invalid_hooks_json_does_not_write(self):
        self.target.mkdir()
        path = self.target / "hooks.json"
        path.write_text("{not json")
        with self.assertRaisesRegex(ValueError, "Invalid hooks.json"):
            installer.plan(self.target)
        self.assertEqual([item.name for item in self.target.iterdir()], ["hooks.json"])
        self.assertEqual(path.read_text(), "{not json")
        path.write_text("[]")
        with self.assertRaisesRegex(ValueError, "hooks.json must be a JSON object"):
            installer.plan(self.target)
        self.assertEqual(path.read_text(), "[]")

    def test_hooks_json_and_hooks_directory_symlinks_are_refused(self):
        self.target.mkdir()
        outside = Path(self.temp.name) / "outside-hooks.json"
        outside.write_text("{}")
        (self.target / "hooks.json").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "Symlink"):
            installer.plan(self.target)
        self.assertEqual(outside.read_text(), "{}")
        (self.target / "hooks.json").unlink()
        outside_dir = Path(self.temp.name) / "outside-hooks"
        outside_dir.mkdir()
        marker = outside_dir / "force_fork_turns_none.py"
        marker.write_text("untouched")
        (self.target / "hooks").symlink_to(outside_dir, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Symlink"):
            installer.plan(self.target)
        self.assertEqual(marker.read_text(), "untouched")


if __name__ == "__main__":
    unittest.main()
