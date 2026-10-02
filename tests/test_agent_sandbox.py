import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "bundle" / "agents"


class AgentSandboxTests(unittest.TestCase):
    def test_read_only_reviewers_set_sandbox_mode(self):
        matched = []
        for path in sorted(AGENTS.glob("*.toml")):
            data = tomllib.loads(path.read_text())
            name = str(data.get("name", ""))
            if "reviewer" not in name.lower():
                continue
            blob = f"{data.get('description', '')}\n{data.get('developer_instructions', '')}"
            if "read-only" not in blob.lower():
                continue
            matched.append(path.name)
            self.assertEqual(
                data.get("sandbox_mode"),
                "read-only",
                f"{path.name} claims read-only but sandbox_mode is {data.get('sandbox_mode')!r}",
            )
        self.assertGreaterEqual(len(matched), 3)
