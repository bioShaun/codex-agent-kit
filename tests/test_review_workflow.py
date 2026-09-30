import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "bundle" / "review-workflow.py"
STATE_FIELDS = {
    "scope_id",
    "snapshot_id",
    "code_review",
    "validation",
    "final_acceptance",
    "review_kind",
    "review_verdict",
    "isolation_requirement",
    "evidence_refs",
    "contract_error",
}


class ReviewWorkflowContractErrorTests(unittest.TestCase):
    def setUp(self):
        work = ROOT / ".work"
        work.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="review-", dir=work)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def workflow(self, *args):
        return subprocess.run(
            [sys.executable, str(WORKFLOW), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    def protocol_sha256(self):
        result = self.workflow("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)["protocol_sha256"]

    def prepare_package(self, kind):
        spec_dir = self.root / f"spec-{kind}"
        spec_dir.mkdir()
        (spec_dir / "instructions.md").write_text("Neutral review instructions.\n")
        spec = {
            "review_kind": kind,
            "scope_id": f"scope-{kind}",
            "isolation_requirement": "ordinary",
            "instructions_file": "instructions.md",
            "files": ["instructions.md"],
            "required_files": ["instructions.md"],
        }
        if kind == "final":
            (spec_dir / "validation.log").write_text("validator recorded a failing check\n")
            spec["files"] = ["instructions.md", "validation.log"]
            spec["required_files"] = ["instructions.md", "validation.log"]
            spec["validation_evidence"] = ["validation.log"]
        (spec_dir / "spec.json").write_text(json.dumps(spec))
        package = self.root / f"package-{kind}"
        prepared = self.workflow(
            "prepare",
            "--spec",
            str(spec_dir / "spec.json"),
            "--output",
            str(package),
            "--protocol-sha256",
            self.protocol_sha256(),
        )
        self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
        return package

    def receive_contract_error(self, package, *, validation, code_review, evidence):
        result_path = self.root / f"{package.name}-result.txt"
        # Bare BLOCKED is a contract error: the first line must be `STATUS (code|final)`.
        result_path.write_text("BLOCKED\nmissing review kind\n")
        state_path = self.root / f"{package.name}-state.json"
        completed = self.workflow(
            "receive",
            str(package),
            "--result",
            str(result_path),
            "--validation",
            validation,
            "--code-review",
            code_review,
            "--evidence",
            evidence,
            "--state-output",
            str(state_path),
        )
        self.assertEqual(completed.returncode, 2, completed.stdout + completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertFalse(payload["ready"])
        self.assertIn("invalid result first line", payload["contract_error"])
        self.assertTrue(state_path.is_file(), completed.stdout)
        state = json.loads(state_path.read_text())
        request = json.loads((package / "request.json").read_text())
        self.assertEqual(set(state), STATE_FIELDS)
        self.assertEqual(state["scope_id"], request["scope_id"])
        self.assertEqual(state["snapshot_id"], request["snapshot_id"])
        self.assertEqual(state["isolation_requirement"], request["isolation_requirement"])
        self.assertEqual(state["review_kind"], request["review_kind"])
        self.assertIsNone(state["review_verdict"])
        self.assertEqual(state["evidence_refs"], [evidence])
        self.assertIn("invalid result first line", state["contract_error"])
        return state

    def test_code_contract_error_blocks_only_code_review(self):
        package = self.prepare_package("code")
        state = self.receive_contract_error(
            package,
            validation="PASS",
            code_review="PASS",
            evidence="notes.log",
        )
        self.assertEqual(state["review_kind"], "code")
        self.assertEqual(state["code_review"], "BLOCKED")
        self.assertEqual(state["validation"], "PASS")
        self.assertEqual(state["final_acceptance"], "NOT_REQUESTED")

    def test_final_contract_error_blocks_only_final_acceptance(self):
        package = self.prepare_package("final")
        state = self.receive_contract_error(
            package,
            validation="FAIL",
            code_review="PASS",
            evidence="validation.log",
        )
        self.assertEqual(state["review_kind"], "final")
        self.assertEqual(state["final_acceptance"], "BLOCKED")
        self.assertEqual(state["code_review"], "PASS")
        self.assertEqual(state["validation"], "FAIL")


if __name__ == "__main__":
    unittest.main()
