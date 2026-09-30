#!/usr/bin/env python3
"""Validate the machine-readable boundary around an external review."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any


EXIT_CONTRACT_ERROR = 2
HASH_RE = re.compile(r"[0-9a-f]{64}")
VERDICT_RE = re.compile(r"(PASS|REQUEST_CHANGES|BLOCKED) \((code|final)\)")
REQUEST_KEYS = {
    "review_kind",
    "scope_id",
    "snapshot_id",
    "isolation_requirement",
    "instructions",
    "snapshot_manifest",
    "required_files",
}
STATE_KEYS = {
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
LIMITATION = (
    "Snapshot coverage does not establish content freshness; Root and the reviewer "
    "must still check frozen contents and semantic scope."
)
STATE_LIMITATION = (
    "schema_valid is not acceptance proof and does not establish evidence semantics "
    "or probe success."
)


class ContractError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ContractError(f"invalid JSON constant: {value}")


def _load_json_bytes(data: bytes, label: str) -> Any:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError(f"{label} is not UTF-8: {exc}") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except ContractError:
        raise
    except json.JSONDecodeError as exc:
        raise ContractError(f"invalid {label} JSON: {exc.msg}") from exc


def _read_bytes(path: str, label: str, allow_stdin: bool = False) -> bytes:
    if allow_stdin and path == "-":
        return sys.stdin.buffer.read()
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise ContractError(f"cannot read {label} {path!r}: {exc}") from exc


def _nonempty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{name} must be a nonempty string")
    return value


def _absolute_path(value: Any, name: str) -> str:
    value = _nonempty_string(value, name)
    if not os.path.isabs(value):
        raise ContractError(f"{name} must be an absolute path")
    return value


def _resolved(path: str) -> str:
    return os.path.realpath(os.path.abspath(path))


def _validate_manifest(request: dict[str, Any]) -> None:
    manifest_path = _absolute_path(request["snapshot_manifest"], "snapshot_manifest")
    required = request["required_files"]
    if not isinstance(required, list) or not required:
        raise ContractError("required_files must be a nonempty list")

    required_resolved: dict[str, str] = {}
    required_literal: set[str] = set()
    for index, item in enumerate(required):
        path = _absolute_path(item, f"required_files[{index}]")
        if path in required_literal:
            raise ContractError(f"duplicate required_files path: {path}")
        required_literal.add(path)
        canonical = _resolved(path)
        if canonical in required_resolved:
            raise ContractError(
                "ambiguous required_files aliases resolve to the same path: "
                f"{required_resolved[canonical]} and {path}"
            )
        required_resolved[canonical] = path

    manifest_bytes = _read_bytes(manifest_path, "snapshot_manifest")
    expected_snapshot = "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
    if request["snapshot_id"] != expected_snapshot:
        raise ContractError("snapshot_id does not match the SHA256 of manifest bytes")

    manifest = _load_json_bytes(manifest_bytes, "snapshot_manifest")
    if not isinstance(manifest, dict):
        raise ContractError("snapshot_manifest must be a JSON object")
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise ContractError("snapshot_manifest.files must be an object")

    manifest_resolved: dict[str, str] = {}
    for path, digest in files.items():
        absolute = _absolute_path(path, "snapshot_manifest.files key")
        if not isinstance(digest, str) or HASH_RE.fullmatch(digest) is None:
            raise ContractError(f"invalid lowercase SHA256 for manifest file: {absolute}")
        canonical = _resolved(absolute)
        if canonical in manifest_resolved:
            raise ContractError(
                "ambiguous manifest aliases resolve to the same path: "
                f"{manifest_resolved[canonical]} and {absolute}"
            )
        manifest_resolved[canonical] = absolute

    for canonical, original in required_resolved.items():
        if canonical not in manifest_resolved:
            raise ContractError(f"required file is not covered by manifest: {original}")
        try:
            mode = os.stat(original).st_mode
        except OSError as exc:
            raise ContractError(f"required file does not exist: {original}: {exc}") from exc
        if not stat.S_ISREG(mode):
            raise ContractError(f"required path is not a regular file: {original}")


def validate_request(path: str) -> dict[str, Any]:
    request = _load_json_bytes(_read_bytes(path, "request", allow_stdin=True), "request")
    if not isinstance(request, dict):
        raise ContractError("request must be a JSON object")
    unknown = set(request) - REQUEST_KEYS
    if unknown:
        raise ContractError(f"unknown request fields: {', '.join(sorted(unknown))}")

    required = {
        "review_kind",
        "scope_id",
        "snapshot_id",
        "isolation_requirement",
        "instructions",
    }
    missing = required - set(request)
    if missing:
        raise ContractError(f"missing request fields: {', '.join(sorted(missing))}")
    review_kind = _nonempty_string(request["review_kind"], "review_kind")
    if review_kind not in {"code", "final"}:
        raise ContractError("review_kind must be code or final")
    _nonempty_string(request["scope_id"], "scope_id")
    _nonempty_string(request["snapshot_id"], "snapshot_id")
    isolation = _nonempty_string(request["isolation_requirement"], "isolation_requirement")
    if isolation not in {"ordinary", "strict"}:
        raise ContractError("isolation_requirement must be ordinary or strict")
    _nonempty_string(request["instructions"], "instructions")

    has_manifest = "snapshot_manifest" in request
    has_required = "required_files" in request
    if has_manifest != has_required:
        raise ContractError("snapshot_manifest and required_files must appear together")
    if review_kind == "final" and isolation == "strict" and not has_manifest:
        raise ContractError("strict final review requires snapshot_manifest and required_files")
    if has_manifest:
        _validate_manifest(request)
    return request


def parse_result(request: dict[str, Any], result_path: str) -> dict[str, Any]:
    result_bytes = _read_bytes(result_path, "result")
    try:
        result_text = result_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError(f"result is not UTF-8: {exc}") from exc
    first_line = result_text.splitlines()[0] if result_text.splitlines() else ""
    match = VERDICT_RE.fullmatch(first_line)
    if match is None:
        raise ContractError(f"invalid result first line: {first_line!r}")
    verdict, kind = match.groups()
    if kind != request["review_kind"]:
        raise ContractError(
            f"result review kind {kind!r} does not match requested kind "
            f"{request['review_kind']!r}"
        )
    return {
        "scope_id": request["scope_id"],
        "snapshot_id": request["snapshot_id"],
        "isolation_requirement": request["isolation_requirement"],
        "review_kind": kind,
        "review_verdict": verdict,
        "contract_error": None,
    }


def validate_state(request: dict[str, Any], result: dict[str, Any], path: str) -> dict[str, Any]:
    state = _load_json_bytes(_read_bytes(path, "state"), "state")
    if not isinstance(state, dict):
        raise ContractError("state must be a JSON object")
    if set(state) != STATE_KEYS:
        missing = sorted(STATE_KEYS - set(state))
        extra = sorted(set(state) - STATE_KEYS)
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if extra:
            details.append("unexpected: " + ", ".join(extra))
        raise ContractError("state must contain exactly the standard fields (" + "; ".join(details) + ")")

    for name in ("scope_id", "snapshot_id", "review_kind", "review_verdict", "isolation_requirement"):
        if state[name] != result[name]:
            raise ContractError(f"state {name} does not match validated review result")
    if state["contract_error"] is not None:
        raise ContractError("state contract_error must be null for a valid result")

    code_review = state["code_review"]
    validation = state["validation"]
    final_acceptance = state["final_acceptance"]
    if type(code_review) is not str or code_review not in {"PASS", "REQUEST_CHANGES", "BLOCKED", "NOT_REQUESTED"}:
        raise ContractError("code_review has an invalid value")
    if type(validation) is not str or validation not in {"PASS", "FAIL", "BLOCKED", "NOT_RUN"}:
        raise ContractError("validation has an invalid value")
    if type(final_acceptance) is not str or final_acceptance not in {"PASS", "BLOCKED", "NOT_REQUESTED"}:
        raise ContractError("final_acceptance has an invalid value")
    evidence = state["evidence_refs"]
    if not isinstance(evidence, list) or any(not isinstance(item, str) or not item.strip() for item in evidence):
        raise ContractError("evidence_refs must be a list of nonempty strings")

    if "PASS" in (code_review, validation, final_acceptance) and not evidence:
        raise ContractError("PASS state requires nonempty evidence_refs")

    kind = result["review_kind"]
    verdict = result["review_verdict"]
    if kind == "code":
        if code_review != verdict:
            raise ContractError("code review state must equal the parsed code verdict")
        if final_acceptance == "PASS":
            raise ContractError("a code PASS cannot imply final_acceptance PASS")
    else:
        if verdict != "PASS" and final_acceptance != "BLOCKED":
            raise ContractError("a non-PASS final verdict requires final_acceptance BLOCKED")
    if final_acceptance == "PASS":
        if not (
            kind == "final"
            and verdict == "PASS"
            and validation == "PASS"
            and code_review in {"PASS", "NOT_REQUESTED"}
            and evidence
        ):
            raise ContractError("final_acceptance PASS prerequisites are not satisfied")

    return {
        "schema_valid": True,
        "acceptance_proof": False,
        "scope_id": state["scope_id"],
        "snapshot_id": state["snapshot_id"],
        "review_kind": kind,
        "review_verdict": verdict,
        "contract_error": None,
        "limitations": [LIMITATION, STATE_LIMITATION],
    }


def _error_output(exc: Exception, request: dict[str, Any] | None = None) -> dict[str, Any]:
    output: dict[str, Any] = {
        "schema_valid": False,
        "contract_error": str(exc),
        "limitations": [LIMITATION],
    }
    if request is not None:
        for name in ("scope_id", "snapshot_id", "isolation_requirement", "review_kind"):
            value = request.get(name)
            if isinstance(value, str):
                output[name] = value
    return output


class ContractArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        print(json.dumps(_error_output(ContractError(f"invalid command line: {message}")), sort_keys=True))
        raise SystemExit(EXIT_CONTRACT_ERROR)


def main(argv: list[str] | None = None) -> int:
    parser = ContractArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    preflight_parser = subparsers.add_parser("preflight")
    preflight_parser.add_argument("request")
    result_parser = subparsers.add_parser("result")
    result_parser.add_argument("request")
    result_parser.add_argument("result")
    state_parser = subparsers.add_parser("state")
    state_parser.add_argument("request")
    state_parser.add_argument("result")
    state_parser.add_argument("state")
    args = parser.parse_args(argv)

    request: dict[str, Any] | None = None
    try:
        request = validate_request(args.request)
        if args.command == "preflight":
            output = {
                "schema_valid": True,
                "scope_id": request["scope_id"],
                "snapshot_id": request["snapshot_id"],
                "isolation_requirement": request["isolation_requirement"],
                "review_kind": request["review_kind"],
                "contract_error": None,
                "limitations": [LIMITATION],
            }
        else:
            result = parse_result(request, args.result)
            if args.command == "result":
                output = dict(result)
                output["schema_valid"] = True
                output["acceptance_proof"] = False
                output["limitations"] = [LIMITATION]
            else:
                output = validate_state(request, result, args.state)
    except (ValueError, OSError) as exc:
        print(json.dumps(_error_output(exc, request), sort_keys=True))
        return EXIT_CONTRACT_ERROR

    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
