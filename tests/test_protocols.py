import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("protocol_installer", ROOT / "install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class ProtocolBundleTests(unittest.TestCase):
    def setUp(self):
        work = ROOT / ".work"
        work.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="protocol-", dir=work)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.target = self.base / "codex home"

    def install(self):
        desired, old = installer.plan(self.target)
        installer.apply(self.target, desired, old)

    def run_workflow(self, *args):
        return subprocess.run([sys.executable, str(self.target / "review-workflow.py"), *args],
                              capture_output=True, text=True)

    def test_install_protocol_bundle_and_local_drift(self):
        self.install()
        manifest = self.target / "protocols/manifest.json"
        self.assertTrue(manifest.is_file(), "installer must deploy the split protocol manifest")
        files = json.loads(manifest.read_text())["files"]
        for name in files:
            text = (self.target / name).read_text()
            self.assertNotIn("@@CODEX_HOME", text)
        self.assertIn(str(self.target), (self.target / "protocols/review.md").read_text())
        leaf = self.target / "protocols/delegation.md"
        leaf.write_text(leaf.read_text() + "\nLocal machine edit\n")
        with self.assertRaisesRegex(ValueError, "Local edit detected"):
            installer.plan(self.target)
        desired, old = installer.plan(self.target, overwrite_local=True)
        backup = installer.apply(self.target, desired, old)
        self.assertIn("Local machine edit", (backup / "protocols/delegation.md").read_text())
        desired, old = installer.plan(self.target)
        self.assertEqual(desired, old)

    def test_every_protocol_edit_invalidates_prepared_review(self):
        self.install()
        manifest = self.target / "protocols/manifest.json"
        self.assertTrue(manifest.is_file(), "review protocol must include the installed modules")
        status = self.run_workflow("status")
        self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
        token = json.loads(status.stdout)["protocol_sha256"]
        source = self.base / "source"
        source.mkdir()
        (source / "instructions.md").write_text("Review the bounded fixture.\n")
        request = {"review_kind": "code", "scope_id": "protocol-regression",
                   "isolation_requirement": "ordinary", "instructions_file": "instructions.md",
                   "files": ["instructions.md"], "required_files": ["instructions.md"]}
        spec_path = source / "spec.json"
        spec_path.write_text(json.dumps(request))
        package = self.base / "package"
        result = self.run_workflow("prepare", "--spec", str(spec_path), "--output", str(package),
                                   "--protocol-sha256", token)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.run_workflow("check", str(package)).returncode, 0)
        files = json.loads(manifest.read_text())["files"] + ["protocols/manifest.json"]
        for name in files:
            with self.subTest(file=name):
                path = self.target / name
                saved = path.read_bytes()
                path.write_bytes(saved + b"\n")
                try:
                    changed = self.run_workflow("status")
                    self.assertEqual(changed.returncode, 0, changed.stdout)
                    self.assertNotEqual(json.loads(changed.stdout)["protocol_sha256"], token)
                    checked = self.run_workflow("check", str(package))
                    self.assertEqual(checked.returncode, 2, checked.stdout)
                    self.assertIn("protocol_sha256", json.loads(checked.stdout)["contract_error"])
                    rejected = self.run_workflow("prepare", "--spec", str(spec_path),
                                                "--output", str(self.base / "stale"),
                                                "--protocol-sha256", token)
                    self.assertEqual(rejected.returncode, 2, rejected.stdout)
                    self.assertIn("stale", json.loads(rejected.stdout)["contract_error"])
                    self.assertFalse((self.base / "stale").exists())
                finally:
                    path.write_bytes(saved)
        missing = self.target / "protocols/review.md"
        missing.rename(self.target / "review.saved")
        failed = self.run_workflow("status")
        self.assertEqual(failed.returncode, 2, failed.stdout)
        self.assertIn("protocols/review.md", json.loads(failed.stdout)["contract_error"])

    def test_upgrade_preserves_machine_settings_and_installs_modules(self):
        self.target.mkdir()
        old = b"Old monolithic protocol\n"
        (self.target / "astra-planner.md").write_bytes(old)
        (self.target / installer.STATE).write_text(json.dumps({"version": 1, "files": {
            "astra-planner.md": installer.digest(old)}}))
        (self.target / "config.toml").write_text('model = "machine-specific"\n')
        self.install()
        self.assertTrue((self.target / "protocols/review.md").is_file(),
                        "upgrading the monolithic kit must install the review module")
        self.assertIn("machine-specific", (self.target / "config.toml").read_text())
        self.assertEqual(self.run_workflow("status").returncode, 0)

    def test_manifest_rejects_escaping_paths_and_missing_modules_before_writes(self):
        bundle = self.base / "bundle"
        shutil.copytree(ROOT / "bundle", bundle)
        manifest = bundle / "protocols/manifest.json"
        self.assertTrue(manifest.is_file(), "bundle must declare the complete protocol file set")
        saved = manifest.read_text()
        from unittest.mock import patch
        for names in (["astra-planner.md", "../outside.md"],
                      ["astra-planner.md", "protocols/missing.md"],
                      ["astra-planner.md", "astra-planner.md"]):
            with self.subTest(files=names):
                manifest.write_text(json.dumps({"version": 1, "files": names}))
                with patch.object(installer, "BUNDLE", bundle):
                    with self.assertRaises((ValueError, OSError)):
                        installer.plan(self.target)
                self.assertFalse(self.target.exists())
        manifest.write_text(saved)


if __name__ == "__main__":
    unittest.main()
