#!/usr/bin/env python3
"""Prepare, verify, and receive bounded independent-review packages."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import secrets
import shutil
import stat
import sys
import re
from pathlib import Path
from typing import Any

EXIT_ERROR = 2
REVISION = "1"
LIMITATION = (
    "This helper detects bound-file drift and contract errors; it cannot intercept callers "
    "that bypass it and does not prove host enforcement, reviewer independence, or acceptance."
)
CONTROL_NAMES = ("request.json", "snapshot.json", "brief.md", "spec.json")


class WorkflowError(ValueError):
    def __init__(self, message: str, *, receipt: str | None = None,
                 diagnostics: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.receipt = receipt
        self.diagnostics = diagnostics


STAT_FIELDS = ("dev", "ino", "size", "mtime_ns", "ctime_ns")


def stat_observation(value: os.stat_result) -> dict[str, int]:
    return {name: getattr(value, "st_" + name) for name in STAT_FIELDS}


def stability_diagnostics(operation: str, path: Path,
                          snapshots: dict[str, dict[str, int]]) -> dict[str, Any]:
    names = [name for name in ("pre_open", "path_before", "fd_before", "fd_after", "path_after")
             if name in snapshots]
    differences = {f"{left}_to_{right}": [field for field in STAT_FIELDS
                   if snapshots[left][field] != snapshots[right][field]]
                   for left, right in zip(names, names[1:])}
    return {"kind": "file_stability", "operation": operation, "path": str(path),
            "observations": snapshots,
            "changed_fields": {pair: fields for pair, fields in differences.items() if fields}}


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def load_json_bytes(data: bytes, label: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in items:
            if key in out:
                raise WorkflowError(f"duplicate JSON key in {label}: {key}")
            out[key] = value
        return out

    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                          parse_constant=lambda x: (_ for _ in ()).throw(WorkflowError(f"invalid JSON constant in {label}: {x}")))
    except UnicodeDecodeError as exc:
        raise WorkflowError(f"{label} is not UTF-8: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"invalid {label} JSON: {exc.msg}") from exc


def sibling(name: str) -> Path:
    return Path(__file__).resolve().with_name(name)


def canonical_path(value: str | os.PathLike[str]) -> Path:
    """Resolve an identity path, including existing symlinked parent components."""
    return Path(value).resolve(strict=False)


def regular_bytes(path: Path, label: str) -> bytes:
    try:
        before = path.stat()
    except OSError as exc:
        raise WorkflowError(f"cannot stat {label} {path}: {exc}") from exc
    if not stat.S_ISREG(before.st_mode):
        raise WorkflowError(f"{label} is not a regular file: {path}")
    snapshots = {"pre_open": stat_observation(before)}
    try:
        with path.open("rb") as handle:
            snapshots["fd_before"] = stat_observation(os.fstat(handle.fileno()))
            snapshots["path_before"] = stat_observation(path.stat())
            chunks: list[bytes] = []
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            snapshots["fd_after"] = stat_observation(os.fstat(handle.fileno()))
        snapshots["path_after"] = stat_observation(path.stat())
    except OSError as exc:
        diagnostics = stability_diagnostics("read", path, snapshots)
        raise WorkflowError(f"cannot read {label} {path}: {exc}", diagnostics=diagnostics) from exc
    diagnostics = stability_diagnostics("read", path, snapshots)
    if diagnostics["changed_fields"]:
        raise WorkflowError(f"{label} changed while being read: {path}", diagnostics=diagnostics)
    return b"".join(chunks)


def file_hash(path: Path, label: str) -> str:
    try:
        before = path.stat()
    except OSError as exc:
        raise WorkflowError(f"cannot stat {label} {path}: {exc}") from exc
    if not stat.S_ISREG(before.st_mode):
        raise WorkflowError(f"{label} is not a regular file: {path}")
    snapshots = {"pre_open": stat_observation(before)}
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            snapshots["fd_before"] = stat_observation(os.fstat(handle.fileno()))
            snapshots["path_before"] = stat_observation(path.stat())
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            snapshots["fd_after"] = stat_observation(os.fstat(handle.fileno()))
        snapshots["path_after"] = stat_observation(path.stat())
    except OSError as exc:
        diagnostics = stability_diagnostics("hash", path, snapshots)
        raise WorkflowError(f"cannot hash {label} {path}: {exc}", diagnostics=diagnostics) from exc
    diagnostics = stability_diagnostics("hash", path, snapshots)
    if diagnostics["changed_fields"]:
        raise WorkflowError(f"{label} changed while being hashed: {path}", diagnostics=diagnostics)
    return digest.hexdigest()


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_contract():
    path = sibling("review-contract.py")
    if not path.is_file():
        raise WorkflowError(f"missing sibling validator: {path}")
    spec = importlib.util.spec_from_file_location("review_contract", path)
    if spec is None or spec.loader is None:
        raise WorkflowError("cannot load sibling validator")
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def current_revisions() -> dict[str, str]:
    protocol = sibling("astra-planner.md")
    dependency = sibling("review-contract.py")
    return {
        "protocol_sha256": file_hash(protocol, "protocol"),
        "helper_sha256": file_hash(Path(__file__).resolve(), "workflow helper"),
        "helper_revision": REVISION,
        "dependency_sha256": file_hash(dependency, "validator dependency"),
    }


def resolve_input(value: Any, base: Path, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowError(f"{label} must be a nonempty path string")
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    return Path(os.path.abspath(path))


def path_list(value: Any, base: Path, label: str, nonempty: bool = True) -> list[Path]:
    if not isinstance(value, list) or (nonempty and not value):
        raise WorkflowError(f"{label} must be {'a nonempty ' if nonempty else ''}list")
    paths = [resolve_input(item, base, f"{label}[{i}]") for i, item in enumerate(value)]
    seen: dict[str, Path] = {}
    for path in paths:
        canonical = os.path.realpath(path)
        if canonical in seen:
            raise WorkflowError(f"ambiguous {label} aliases: {seen[canonical]} and {path}")
        seen[canonical] = path
    return paths


def require_regular(paths: list[Path], label: str) -> None:
    for path in paths:
        try:
            mode = path.stat().st_mode
        except OSError as exc:
            raise WorkflowError(f"cannot stat {label} {path}: {exc}") from exc
        if not stat.S_ISREG(mode):
            raise WorkflowError(f"{label} is not a regular file: {path}")


def member(path: Path, paths: list[Path]) -> bool:
    target = os.path.realpath(path)
    return any(os.path.realpath(item) == target for item in paths)


def validate_budget(value: Any, strict: bool) -> dict[str, Any] | None:
    if not strict:
        if value is not None:
            raise WorkflowError("budget is only valid for strict isolation")
        return None
    if not isinstance(value, dict) or set(value) != {"timeout_seconds", "review_seconds", "reason"}:
        raise WorkflowError("strict isolation requires budget with exactly timeout_seconds, review_seconds, reason")
    timeout, review, reason = value["timeout_seconds"], value["review_seconds"], value["reason"]
    if type(timeout) is not int or not 0 < timeout <= 86400:
        raise WorkflowError("budget.timeout_seconds must be an integer from 1 through 86400")
    if type(review) is not int or review <= 0 or review >= timeout or timeout - review < 60:
        raise WorkflowError("budget.review_seconds must be positive and leave at least 60 seconds parent reserve")
    if not isinstance(reason, str) or not reason.strip():
        raise WorkflowError("budget.reason must be a nonempty string")
    return dict(value)


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    revisions = current_revisions()
    if args.protocol_sha256 != revisions["protocol_sha256"]:
        raise WorkflowError("protocol SHA256 token is stale or incorrect; refresh status after reading the current protocol")
    spec_path = canonical_path(args.spec)
    raw_spec = regular_bytes(spec_path, "spec")
    value = load_json_bytes(raw_spec, "spec")
    if not isinstance(value, dict):
        raise WorkflowError("spec must be a JSON object")
    allowed = {"review_kind", "scope_id", "isolation_requirement", "instructions_file", "files",
               "required_files", "baseline_manifest", "prior_findings_file", "validation_evidence", "budget"}
    unknown = set(value) - allowed
    if unknown:
        raise WorkflowError("unknown spec fields: " + ", ".join(sorted(unknown)))
    for name in ("review_kind", "scope_id", "isolation_requirement", "instructions_file", "files", "required_files"):
        if name not in value:
            raise WorkflowError(f"missing spec field: {name}")
    kind = value["review_kind"]
    isolation = value["isolation_requirement"]
    if kind not in {"code", "final"}:
        raise WorkflowError("review_kind must be code or final")
    if not isinstance(value["scope_id"], str) or not value["scope_id"].strip():
        raise WorkflowError("scope_id must be a nonempty string")
    if isolation not in {"ordinary", "strict"}:
        raise WorkflowError("isolation_requirement must be ordinary or strict")
    budget = validate_budget(value.get("budget"), isolation == "strict")
    base = spec_path.parent
    files = path_list(value["files"], base, "files")
    required = path_list(value["required_files"], base, "required_files")
    instructions = resolve_input(value["instructions_file"], base, "instructions_file")
    if not member(instructions, files):
        files.append(instructions)
    if not member(instructions, required):
        required.append(instructions)
    evidence = path_list(value.get("validation_evidence", []), base, "validation_evidence", False)
    if kind == "final" and not evidence:
        raise WorkflowError("final review requires nonempty validation_evidence")
    prior = resolve_input(value["prior_findings_file"], base, "prior_findings_file") if "prior_findings_file" in value else None
    for label, path in [("validation_evidence", item) for item in evidence] + ([ ("prior_findings_file", prior) ] if prior else []):
        if not member(path, files) or not member(path, required):
            raise WorkflowError(f"{label} must be explicitly included in both files and required_files: {path}")
    for item in required:
        if not member(item, files):
            raise WorkflowError(f"required file is omitted from files: {item}")
    # Reject aliases introduced by automatically joining the instructions file.
    canonical: dict[str, Path] = {}
    for item in files:
        resolved = os.path.realpath(item)
        if resolved in canonical:
            raise WorkflowError(f"ambiguous files aliases: {canonical[resolved]} and {item}")
        canonical[resolved] = item
    require_regular(files, "source file")
    require_regular(required, "required file")
    instruction_bytes = regular_bytes(instructions, "instructions_file")
    try:
        instruction_text = instruction_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkflowError(f"instructions_file is not UTF-8: {exc}") from exc

    baseline_files: dict[str, str] = {}
    baseline_path = None
    baseline_hash = None
    if "baseline_manifest" in value:
        baseline_path = resolve_input(value["baseline_manifest"], base, "baseline_manifest")
        baseline_bytes = regular_bytes(baseline_path, "baseline_manifest")
        baseline_hash = sha_bytes(baseline_bytes)
        baseline = load_json_bytes(baseline_bytes, "baseline_manifest")
        if not isinstance(baseline, dict) or not isinstance(baseline.get("files"), dict):
            raise WorkflowError("baseline_manifest must contain a files object")
        for key, digest in baseline["files"].items():
            if (not isinstance(key, str) or not os.path.isabs(key) or not isinstance(digest, str)
                    or re.fullmatch(r"[0-9a-f]{64}", digest) is None):
                raise WorkflowError("baseline_manifest contains an invalid path or SHA256")
            baseline_files[key] = digest

    raw_output = Path(args.output)
    if raw_output.is_symlink():
        raise WorkflowError(f"output must not be a symlink: {raw_output}")
    output = canonical_path(raw_output)
    if output.exists() or output.is_symlink():
        raise WorkflowError(f"output already exists: {output}")
    if not output.parent.is_dir():
        raise WorkflowError(f"output parent is not a directory: {output.parent}")
    staging = output.parent / f".{output.name}.staging-{os.getpid()}-{secrets.token_hex(4)}"
    try:
        staging.mkdir(mode=0o700)
        (staging / "frozen").mkdir()
        snapshot = {"files": {str(path): (sha_bytes(instruction_bytes) if member(path, [instructions])
                                          else file_hash(path, "source file")) for path in files}}
        snapshot_data = json_bytes(snapshot)
        snapshot_id = "sha256:" + sha_bytes(snapshot_data)
        current = snapshot["files"]
        added = sorted(set(current) - set(baseline_files))
        removed = sorted(set(baseline_files) - set(current))
        changed = sorted(k for k in set(current) & set(baseline_files) if current[k] != baseline_files[k])
        unchanged = sorted(k for k in set(current) & set(baseline_files) if current[k] == baseline_files[k])
        brief_lines = [
            f"# Review brief: {value['scope_id']}", "",
            f"Review kind: {kind}", f"Isolation: {isolation}",
            f"Frozen files: {len(current)}; required files: {len(required)}.",
            f"Baseline delta: added {len(added)}, changed {len(changed)}, removed {len(removed)}, unchanged {len(unchanged)}.",
        ]
        for label, items in (("Added", added), ("Changed", changed), ("Removed", removed)):
            if items:
                brief_lines += [f"{label}: " + ", ".join(items[:50]) + (f" (+{len(items)-50} more)" if len(items) > 50 else "")]
        if prior:
            brief_lines.append(f"Prior findings: {prior}")
        if evidence:
            brief_lines.append("Validation evidence: " + ", ".join(map(str, evidence)))
        brief_lines += ["", "The delta is navigation only; acceptance still requires hashing every frozen file and reviewing scope semantics.", ""]
        brief_data = "\n".join(brief_lines).encode()
        final_brief = output / "brief.md"
        final_frozen = output / "frozen" / "instructions.bin"
        appendix = (
            "\n\nPrepared review workflow package:\n" + str(output) +
            "\nReview brief: " + str(final_brief) +
            "\nScope is limited to the explicit frozen manifest and required_files. Baseline diff is navigation only; hash every manifest file."
        )
        if budget:
            appendix += f"\nBudget: total {budget['timeout_seconds']} seconds; reviewer advisory {budget['review_seconds']} seconds; reason: {budget['reason']}."
        appendix += "\nSend this unchanged request to the original neutral review instructions; do not add verdict proposals or current gate outcomes."
        request = {
            "review_kind": kind, "scope_id": value["scope_id"], "snapshot_id": snapshot_id,
            "isolation_requirement": isolation, "instructions": instruction_text + appendix,
            "snapshot_manifest": str(output / "snapshot.json"), "required_files": [str(p) for p in required],
        }
        request_data = json_bytes(request)
        (staging / "snapshot.json").write_bytes(snapshot_data)
        (staging / "brief.md").write_bytes(brief_data)
        (staging / "spec.json").write_bytes(raw_spec)
        (staging / "frozen" / "instructions.bin").write_bytes(instruction_bytes)
        (staging / "request.json").write_bytes(request_data)
        workflow = {
            "schema_version": 1,
            "package_path": str(output),
            "control_sha256": {name: sha_bytes((staging / name).read_bytes()) for name in CONTROL_NAMES},
            "source_spec_path": str(spec_path),
            "source_spec_sha256": sha_bytes(raw_spec),
            "frozen_instructions_sha256": sha_bytes(instruction_bytes),
            "source_instructions_path": str(instructions),
            "baseline_manifest": str(baseline_path) if baseline_path else None,
            "baseline_manifest_sha256": baseline_hash,
            "prior_findings_file": str(prior) if prior else None,
            "validation_evidence": [str(p) for p in evidence],
            "budget": budget,
            **revisions,
        }
        (staging / "workflow.json").write_bytes(json_bytes(workflow))
        # The request deliberately names final package paths; validate against a transient mirror at those names only after publish.
        os.rename(staging, output)
        try:
            load_contract().validate_request(str(output / "request.json"))
        except Exception:
            shutil.rmtree(output, ignore_errors=True)
            raise
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {"prepared": True, "package": str(output), "request_path": str(output / "request.json"),
            "snapshot_id": snapshot_id, "budget": budget, "limitations": [LIMITATION]}


def check_package(package: Path) -> dict[str, Any]:
    package = canonical_path(package)
    if not package.is_dir():
        raise WorkflowError(f"package is not a directory: {package}")
    workflow = load_json_bytes(regular_bytes(package / "workflow.json", "workflow"), "workflow")
    if not isinstance(workflow, dict) or workflow.get("schema_version") != 1:
        raise WorkflowError("unsupported workflow schema")
    revisions = current_revisions()
    for key, actual in revisions.items():
        if workflow.get(key) != actual:
            raise WorkflowError(f"current {key} does not match prepared package")
    if workflow.get("package_path") != str(package):
        raise WorkflowError("package path does not match its binding")
    controls = workflow.get("control_sha256")
    if not isinstance(controls, dict) or set(controls) != set(CONTROL_NAMES):
        raise WorkflowError("workflow control hash set is invalid")
    for name in CONTROL_NAMES:
        if file_hash(package / name, name) != controls[name]:
            raise WorkflowError(f"control file hash mismatch: {name}")
    frozen_spec_bytes = regular_bytes(package / "spec.json", "saved spec")
    source_spec = Path(workflow.get("source_spec_path", ""))
    if sha_bytes(frozen_spec_bytes) != workflow.get("source_spec_sha256"):
        raise WorkflowError("saved spec hash does not match workflow binding")
    if file_hash(source_spec, "source spec") != workflow["source_spec_sha256"]:
        raise WorkflowError("source spec drifted from saved bytes")
    frozen_spec = load_json_bytes(frozen_spec_bytes, "saved spec")
    if not isinstance(frozen_spec, dict):
        raise WorkflowError("saved spec must be an object")
    frozen = package / "frozen" / "instructions.bin"
    if file_hash(frozen, "frozen instructions") != workflow.get("frozen_instructions_sha256"):
        raise WorkflowError("frozen instructions hash mismatch")
    source_instruction = Path(workflow.get("source_instructions_path", ""))
    if file_hash(source_instruction, "source instructions") != workflow["frozen_instructions_sha256"]:
        raise WorkflowError("source instructions drifted from frozen bytes")
    request_path = package / "request.json"
    request = load_contract().validate_request(str(request_path))
    snapshot = load_json_bytes(regular_bytes(package / "snapshot.json", "snapshot"), "snapshot")
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("files"), dict):
        raise WorkflowError("snapshot must contain a files object")
    for name, digest in snapshot["files"].items():
        if not isinstance(name, str) or not os.path.isabs(name) or not isinstance(digest, str):
            raise WorkflowError("snapshot entry is invalid")
        if file_hash(Path(name), "snapshot source") != digest:
            raise WorkflowError(f"snapshot source drift: {name}")
    spec_base = source_spec.parent
    for name in ("review_kind", "scope_id", "isolation_requirement"):
        if request[name] != frozen_spec.get(name):
            raise WorkflowError(f"request {name} does not match saved spec")
    expected_budget = validate_budget(frozen_spec.get("budget"), request["isolation_requirement"] == "strict")
    budget = workflow.get("budget")
    if budget != expected_budget:
        raise WorkflowError("workflow budget does not match saved spec")
    instructions = resolve_input(frozen_spec.get("instructions_file"), spec_base, "instructions_file")
    expected_files = path_list(frozen_spec.get("files"), spec_base, "files")
    expected_required = path_list(frozen_spec.get("required_files"), spec_base, "required_files")
    if not member(instructions, expected_files):
        expected_files.append(instructions)
    if not member(instructions, expected_required):
        expected_required.append(instructions)
    if set(snapshot["files"]) != {str(path) for path in expected_files}:
        raise WorkflowError("snapshot files do not match saved spec files")
    if request["required_files"] != [str(path) for path in expected_required]:
        raise WorkflowError("request required_files do not match saved spec")
    expected_evidence = [str(path) for path in path_list(frozen_spec.get("validation_evidence", []), spec_base,
                                                         "validation_evidence", False)]
    if workflow.get("validation_evidence") != expected_evidence:
        raise WorkflowError("workflow validation_evidence does not match saved spec")
    expected_prior = (str(resolve_input(frozen_spec["prior_findings_file"], spec_base, "prior_findings_file"))
                      if "prior_findings_file" in frozen_spec else None)
    if workflow.get("prior_findings_file") != expected_prior:
        raise WorkflowError("workflow prior_findings_file does not match saved spec")
    expected_baseline = (str(resolve_input(frozen_spec["baseline_manifest"], spec_base, "baseline_manifest"))
                         if "baseline_manifest" in frozen_spec else None)
    if workflow.get("baseline_manifest") != expected_baseline:
        raise WorkflowError("workflow baseline_manifest does not match saved spec")
    if expected_baseline:
        if file_hash(Path(expected_baseline), "baseline_manifest") != workflow.get("baseline_manifest_sha256"):
            raise WorkflowError("baseline_manifest drifted from its workflow binding")
    elif workflow.get("baseline_manifest_sha256") is not None:
        raise WorkflowError("workflow has a baseline hash without a saved baseline")
    return {"ready": True, "schema_valid": True, "request_path": str(request_path), "budget": budget,
            "scope_id": request["scope_id"], "snapshot_id": request["snapshot_id"],
            "review_kind": request["review_kind"], "acceptance_proof": False, "limitations": [LIMITATION]}


def trusted_request(package: Path) -> dict[str, Any]:
    """Load a request after control bindings pass, without assuming source freshness."""
    workflow = load_json_bytes(regular_bytes(package / "workflow.json", "workflow"), "workflow")
    if not isinstance(workflow, dict) or workflow.get("schema_version") != 1:
        raise WorkflowError("unsupported workflow schema")
    if workflow.get("package_path") != str(package):
        raise WorkflowError("package path does not match its binding")
    for key, actual in current_revisions().items():
        if workflow.get(key) != actual:
            raise WorkflowError(f"current {key} does not match prepared package")
    controls = workflow.get("control_sha256")
    if not isinstance(controls, dict) or set(controls) != set(CONTROL_NAMES):
        raise WorkflowError("workflow control hash set is invalid")
    for name in CONTROL_NAMES:
        if file_hash(package / name, name) != controls[name]:
            raise WorkflowError(f"control file hash mismatch: {name}")
    return load_contract().validate_request(str(package / "request.json"))


def safe_state_path(path: Path, package: Path, result: Path) -> None:
    if path.is_symlink():
        raise WorkflowError("state-output must not be a symlink")
    if not path.parent.is_dir():
        raise WorkflowError("state-output parent must be an existing directory")
    target_real = os.path.realpath(path)
    package_real = os.path.realpath(package)
    try:
        common = os.path.commonpath((target_real, package_real))
    except ValueError:
        common = None
    if common == package_real:
        raise WorkflowError("state-output must be outside the review package")
    protected = [package / name for name in (*CONTROL_NAMES, "workflow.json")]
    protected += [package / "frozen" / "instructions.bin", result]
    try:
        snapshot = load_json_bytes((package / "snapshot.json").read_bytes(), "snapshot")
        protected += [Path(p) for p in snapshot.get("files", {})]
    except Exception:
        pass
    try:
        workflow = load_json_bytes((package / "workflow.json").read_bytes(), "workflow")
        for name in ("source_spec_path", "baseline_manifest"):
            value = workflow.get(name)
            if isinstance(value, str) and value:
                protected.append(Path(value))
    except Exception:
        pass
    for item in protected:
        if target_real == os.path.realpath(item):
            raise WorkflowError(f"state-output aliases protected input: {item}")
        if path.exists():
            try:
                if os.path.samefile(path, item):
                    raise WorkflowError(f"state-output hardlinks protected input: {item}")
            except FileNotFoundError:
                pass
    if path.exists() and not path.is_file():
        raise WorkflowError("existing state-output is not a regular file")
    if path.exists() and path.stat().st_nlink > 1:
        raise WorkflowError("existing state-output has multiple hard links")


def atomic_state(path: Path, state: dict[str, Any]) -> None:
    temp = path.parent / f".{path.name}.new-{os.getpid()}-{secrets.token_hex(4)}"
    try:
        temp.write_bytes(json_bytes(state))
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def error_state(request: dict[str, Any], message: str, evidence: list[str]) -> dict[str, Any]:
    state = {
        "scope_id": request["scope_id"],
        "snapshot_id": request["snapshot_id"],
        "code_review": "BLOCKED",
        "validation": "BLOCKED",
        "final_acceptance": "BLOCKED",
        "review_kind": request["review_kind"],
        "review_verdict": None,
        "isolation_requirement": request["isolation_requirement"],
        "evidence_refs": [item for item in evidence if item.strip()],
        "contract_error": message,
    }
    expected = {"scope_id", "snapshot_id", "code_review", "validation", "final_acceptance",
                "review_kind", "review_verdict", "isolation_requirement", "evidence_refs", "contract_error"}
    if set(state) != expected or not state["contract_error"] or state["review_verdict"] is not None:
        raise WorkflowError("internal error-state shape violation")
    if any(value == "PASS" for value in (state["code_review"], state["validation"], state["final_acceptance"])):
        raise WorkflowError("internal error state contains a PASS claim")
    return state


def receive(args: argparse.Namespace) -> dict[str, Any]:
    package = canonical_path(args.package)
    result = canonical_path(args.result)
    state_path = Path(os.path.abspath(args.state_output))
    request = trusted_request(package)
    receipts = package / "receipts"
    if receipts.is_symlink():
        raise WorkflowError("package receipts path must not be a symlink")
    receipts.mkdir(exist_ok=True)
    if not receipts.is_dir():
        raise WorkflowError("package receipts path is not a directory")
    receipt = receipts / f"receipt-{os.getpid()}-{secrets.token_hex(8)}"
    receipt.mkdir()
    metadata: dict[str, Any] = {"package": str(package), "receipt": str(receipt),
                                "input_result_path": str(result),
                                "validation": args.validation, "evidence_refs": args.evidence,
                                "accept_final_requested": args.accept_final, "limitations": [LIMITATION]}
    parsed = None
    try:
        raw = regular_bytes(result, "result")
        preserved = receipt / "result.original"
        preserved.write_bytes(raw)
        metadata.update({"preserved_result_path": str(preserved), "result_sha256": sha_bytes(raw)})
        checked = check_package(package)
        request_path = Path(checked["request_path"])
        safe_state_path(state_path, package, result)
        parsed = load_contract().parse_result(request, str(preserved))
        if request["review_kind"] == "code":
            if args.accept_final:
                raise WorkflowError("code review cannot use --accept-final")
            code_review = parsed["review_verdict"]
            final_acceptance = "NOT_REQUESTED"
        else:
            code_review = args.code_review
            admissible = code_review in {"PASS", "NOT_REQUESTED"}
            final_acceptance = ("PASS" if args.accept_final and parsed["review_verdict"] == "PASS"
                                and args.validation == "PASS" and admissible and args.evidence else
                                ("BLOCKED" if parsed["review_verdict"] != "PASS" or args.accept_final else "NOT_REQUESTED"))
        state = {**parsed, "code_review": code_review, "validation": args.validation,
                 "final_acceptance": final_acceptance, "evidence_refs": args.evidence}
        candidate = receipt / "state.candidate.json"
        candidate.write_bytes(json_bytes(state))
        load_contract().validate_state(request, parsed, str(candidate))
        atomic_state(state_path, state)
        metadata.update({"request_path": str(request_path), "state_output": str(state_path),
                         "state_sha256": sha_bytes(json_bytes(state)), "contract_error": None})
        (receipt / "receipt.json").write_bytes(json_bytes(metadata))
        return {"received": True, "state_output": str(state_path), "receipt": str(receipt),
                "final_acceptance": final_acceptance, "acceptance_proof": False, "limitations": [LIMITATION]}
    except Exception as exc:
        metadata["contract_error"] = str(exc)
        diagnostics = getattr(exc, "diagnostics", None)
        if diagnostics is not None:
            metadata["diagnostics"] = diagnostics
        if parsed is not None:
            metadata["parsed_result"] = parsed
        (receipt / "receipt.json").write_bytes(json_bytes(metadata))
        try:
            safe_state_path(state_path, package, result)
            blocked = error_state(request, str(exc), args.evidence)
            candidate = receipt / "state.error.json"
            candidate.write_bytes(json_bytes(blocked))
            atomic_state(state_path, blocked)
            metadata.update({"blocked_state_written": True, "state_output": str(state_path),
                             "state_sha256": sha_bytes(json_bytes(blocked))})
            (receipt / "receipt.json").write_bytes(json_bytes(metadata))
        except Exception as state_exc:
            metadata["blocked_state_error"] = str(state_exc)
            (receipt / "receipt.json").write_bytes(json_bytes(metadata))
        raise WorkflowError(str(exc), receipt=str(receipt), diagnostics=diagnostics) from exc


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        print(json.dumps({"ready": False, "contract_error": f"invalid command line: {message}", "limitations": [LIMITATION]}, sort_keys=True))
        raise SystemExit(EXIT_ERROR)


def main(argv: list[str] | None = None) -> int:
    parser = Parser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    p = commands.add_parser("prepare")
    p.add_argument("--spec", required=True); p.add_argument("--output", required=True); p.add_argument("--protocol-sha256", required=True)
    p = commands.add_parser("check"); p.add_argument("package")
    p = commands.add_parser("receive"); p.add_argument("package"); p.add_argument("--result", required=True)
    p.add_argument("--validation", required=True, choices=("PASS", "FAIL", "BLOCKED", "NOT_RUN"))
    p.add_argument("--code-review", choices=("PASS", "REQUEST_CHANGES", "BLOCKED", "NOT_REQUESTED"), default="NOT_REQUESTED")
    p.add_argument("--evidence", action="append", default=[]); p.add_argument("--accept-final", action="store_true"); p.add_argument("--state-output", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "status":
            revisions = current_revisions()
            output = {**revisions, "refresh_requirement": "Read the current protocol, then pass this protocol_sha256 to prepare; the token detects stale callers but does not prove it was read.", "host_enforcement": False, "limitations": [LIMITATION]}
        elif args.command == "prepare": output = prepare(args)
        elif args.command == "check": output = check_package(Path(args.package))
        else: output = receive(args)
    except Exception as exc:
        error = {"ready": False, "schema_valid": False, "contract_error": str(exc),
                 "acceptance_proof": False, "limitations": [LIMITATION]}
        receipt = getattr(exc, "receipt", None)
        if receipt:
            error["receipt"] = receipt
        diagnostics = getattr(exc, "diagnostics", None)
        if diagnostics is not None:
            error["diagnostics"] = diagnostics
        print(json.dumps(error, sort_keys=True))
        return EXIT_ERROR
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
