"""Regression coverage for documented context checkpoint field validation."""
import importlib.util
import pathlib
import sys
import unittest


MODULE_PATH = pathlib.Path(__file__).parent / "bundle" / "context-state.py"
SPEC = importlib.util.spec_from_file_location("context_state", MODULE_PATH)
context_state = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = context_state
SPEC.loader.exec_module(context_state)


class ValidateFieldsBoundaryTests(unittest.TestCase):
    def test_unicode_payload_uses_documented_byte_limit(self):
        fields = {name: "" for name in context_state.FIELDS}
        fields.update(goal="g", next_step="n")
        payload_field = next(
            name for name in context_state.FIELDS if name not in {"goal", "next_step"}
        )
        fields[payload_field] = "é" * 31999  # 63,998 UTF-8 bytes; total is 64,000.

        self.assertEqual(context_state.validate_fields(fields), fields)

        fields[payload_field] += "é"
        with self.assertRaises(context_state.StateError):
            context_state.validate_fields(fields)

    def test_required_fields_reject_empty_or_whitespace_values(self):
        for field in ("goal", "next_step"):
            with self.subTest(field=field):
                fields = {"goal": "goal", "next_step": "next"}
                fields[field] = " \t\n"
                with self.assertRaises(context_state.StateError):
                    context_state.validate_fields(fields)


if __name__ == "__main__":
    unittest.main()
