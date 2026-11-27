import importlib.util
import json
from pathlib import Path
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("combined_installer", ROOT / "install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class CombinedHookTests(unittest.TestCase):
    def setUp(self):
        work = ROOT / ".work"
        work.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="combined-hooks-", dir=work)
        self.addCleanup(self.temp.cleanup)
        self.target = Path(self.temp.name) / "codex-home"

    def install(self, enabled=None):
        desired, old = installer.plan(self.target, context_recovery=enabled)
        installer.apply(self.target, desired, old)
        return desired, old

    def assert_spawn(self, document):
        groups = document.get("hooks", {}).get("PreToolUse", [])
        managed = [group for group in groups if group.get("matcher") == "^collaborationspawn_agent$"]
        self.assertEqual(len(managed), 1, "spawn guardrail must survive context recovery installation")
        self.assertIn("force_fork_turns_none.py", managed[0]["hooks"][0]["command"])
        reviewer_groups = document.get("hooks", {}).get("SubagentStart", [])
        reviewer = [group for group in reviewer_groups if group.get("matcher") == "^astra_reviewer$"]
        self.assertEqual(len(reviewer), 1, "reviewer start hook must survive context recovery installation")
        self.assertIn("reviewer_start_contract.py", reviewer[0]["hooks"][0]["command"])

    def test_json_hooks_coexist_repeat_and_disable_independently(self):
        self.install(True)
        document = json.loads((self.target / "hooks.json").read_text())
        self.assert_spawn(document)
        self.assertEqual(len(document["hooks"]["SessionStart"]), 1)
        desired, old = installer.plan(self.target)
        self.assertEqual(desired, old)
        self.install(False)
        document = json.loads((self.target / "hooks.json").read_text())
        self.assert_spawn(document)
        self.assertFalse(document["hooks"]["SessionStart"])

    def test_inline_hooks_keep_both_managed_groups_and_user_handler(self):
        self.target.mkdir()
        config = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo retained"}]}]}}
        (self.target / "config.toml").write_text(installer.dump_toml(config))
        self.install(True)
        self.assertFalse((self.target / "hooks.json").exists())
        parsed = tomllib.loads((self.target / "config.toml").read_text())
        self.assert_spawn(parsed)
        self.assertEqual(parsed["hooks"]["Stop"], config["hooks"]["Stop"])
        self.assertEqual(len(parsed["hooks"]["SessionStart"]), 1)
        self.assertEqual(*installer.plan(self.target))

    def test_metadata_only_json_keeps_existing_inline_event_source(self):
        self.target.mkdir()
        metadata = '{"description":"user settings"}'
        (self.target / "hooks.json").write_text(metadata)
        (self.target / "config.toml").write_text("[[hooks.Stop]]\nhooks = []\n")
        self.install(True)
        self.assertEqual((self.target / "hooks.json").read_text(), metadata)
        config = tomllib.loads((self.target / "config.toml").read_text())
        self.assert_spawn(config)
        self.assertIn("SessionStart", config["hooks"])
        self.assertEqual(*installer.plan(self.target))

    def test_user_command_mentioning_kit_script_is_preserved(self):
        self.target.mkdir()
        command = {"type": "command", "command": "echo force_fork_turns_none.py.old"}
        group = {"matcher": "Bash", "hooks": [command]}
        path = self.target / "hooks.json"
        path.write_text(json.dumps({"hooks": {"PreToolUse": [group]}}))
        self.install()
        document = json.loads(path.read_text())
        self.assert_spawn(document)
        self.assertIn(group, document["hooks"]["PreToolUse"])

    def test_overwrite_replaces_edited_invocation_without_registering_twice(self):
        self.install()
        path = self.target / "hooks.json"
        document = json.loads(path.read_text())
        handler = document["hooks"]["PreToolUse"][0]["hooks"][0]
        handler.pop("statusMessage")
        handler["command"] = handler["command"].replace("python3 ", "python3 -B ", 1)
        path.write_text(json.dumps(document))
        with self.assertRaisesRegex(ValueError, "Local edit detected"):
            installer.plan(self.target)
        desired, old = installer.plan(self.target, overwrite_local=True)
        installer.apply(self.target, desired, old)
        self.assert_spawn(json.loads(path.read_text()))
        self.assertEqual(*installer.plan(self.target))

    def test_manual_python_invocation_is_detected_before_first_install(self):
        self.target.mkdir()
        group = installer.desired_hook_group(self.target)
        handler = group["hooks"][0]
        handler.pop("statusMessage")
        handler["command"] = handler["command"].replace("python3 ", "python3 -B ", 1)
        handler["command"] = handler["command"].replace("/hooks/", "/hooks/./")
        (self.target / "hooks.json").write_text(json.dumps({"hooks": {"PreToolUse": [group]}}))
        with self.assertRaisesRegex(ValueError, "Local edit detected"):
            installer.plan(self.target)
        desired, old = installer.plan(self.target, overwrite_local=True)
        installer.apply(self.target, desired, old)
        self.assert_spawn(json.loads((self.target / "hooks.json").read_text()))

    def test_wrapper_in_second_source_is_reported_alongside_standard_registration(self):
        self.install()
        group = installer.desired_hook_group(self.target)
        handler = group["hooks"][0]
        handler.pop("statusMessage")
        handler["command"] = "env " + handler["command"]
        config = self.target / "config.toml"
        content = tomllib.loads(config.read_text())
        content["hooks"] = {"PreToolUse": [group]}
        config.write_text(installer.dump_toml(content))
        for overwrite in (False, True):
            with self.assertRaisesRegex(ValueError, "reconcile manually"):
                installer.plan(self.target, overwrite_local=overwrite)

    def test_overwrite_preserves_custom_wrapper_even_with_installed_status_message(self):
        self.install()
        path = self.target / "hooks.json"
        document = json.loads(path.read_text())
        handler = document["hooks"]["PreToolUse"][0]["hooks"][0]
        document["hooks"]["PreToolUse"][0]["matcher"] = "^(collaborationspawn_agent)$"
        original = handler["command"]
        for command in (original + " && echo user-action", "true&&/usr/bin/" + original,
                        "/opt/team-hooks/" + original):
            with self.subTest(command=command):
                handler["command"] = command
                path.write_text(json.dumps(document))
                with self.assertRaisesRegex(ValueError, "reconcile manually"):
                    installer.plan(self.target, overwrite_local=True)

    def test_upgrade_context_only_install_preserves_registration(self):
        self.install(True)
        path = self.target / "hooks.json"
        document = json.loads(path.read_text())
        context = document["hooks"]["SessionStart"]
        document["hooks"].pop("PreToolUse", None)
        path.write_text(json.dumps(document))
        state_path = self.target / installer.STATE
        state = json.loads(state_path.read_text())
        state.pop("hook_groups", None)
        state["files"].pop("hooks/force_fork_turns_none.py", None)
        state_path.write_text(json.dumps(state))
        (self.target / "hooks/force_fork_turns_none.py").unlink(missing_ok=True)
        self.install()
        document = json.loads(path.read_text())
        self.assert_spawn(document)
        self.assertEqual(document["hooks"]["SessionStart"], context)


if __name__ == "__main__":
    unittest.main()
