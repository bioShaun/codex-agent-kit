import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
