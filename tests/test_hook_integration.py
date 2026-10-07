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
