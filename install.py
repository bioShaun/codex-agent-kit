#!/usr/bin/env python3
"""Install the versioned agent kit; preview by default, no third-party dependencies."""
from __future__ import annotations

import argparse
import copy
from datetime import date, datetime, time, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import sys
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parent
BUNDLE = ROOT / "bundle"
STATE = ".codex-agent-kit.json"
BEGIN = "<!-- codex-agent-kit:begin -->"
END = "<!-- codex-agent-kit:end -->"
HELPERS = ("review-workflow.py", "review-contract.py", "review-readonly.sh",
           "run-bounded.py", "work-package-metrics.py")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_file(path: Path) -> bytes | None:
    # Never follow a destination symlink, including dangling links or parent links.
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError(f"Symlink destination refused: {part}")
    if path.exists():
        if not path.is_file():
            raise ValueError(f"Not a regular file: {path}")
        return path.read_bytes()
    return None


def merge(base: dict, updates: dict) -> None:
    for key, value in updates.items():
        if isinstance(value, dict):
            if key not in base:
                base[key] = {}
            if not isinstance(base[key], dict):
                raise ValueError(f"Expected a TOML table for managed key: {key}")
            merge(base[key], value)
        else:
            base[key] = value


def toml_value(value) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(x) for x in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{toml_value(k)} = {toml_value(v)}"
                                  for k, v in sorted(value.items())) + " }"
    raise ValueError(f"Unsupported TOML value type: {type(value).__name__}")


def dump_toml(data: dict) -> str:
    lines = []

    def table(values, parts):
        if parts:
            lines.append("[" + ".".join(toml_value(p) for p in parts) + "]")
        for key, value in values.items():
            if not isinstance(value, dict):
                lines.append(f"{toml_value(key)} = {toml_value(value)}")
        lines.append("")
        for key, value in values.items():
            if isinstance(value, dict):
                table(value, parts + [key])

    table(data, [])
    text = "\n".join(lines)
    # Compare canonical serialized values, including NaN, which is unequal to itself.
    if toml_value(tomllib.loads(text)) != toml_value(data):
        raise ValueError("TOML round-trip mismatch; no configuration written")
    return text


def instruction_text(old: str, target: Path) -> str:
    block = (f"{BEGIN}\n## Native Codex delegation\n\n"
             f"Codex Root 在委派实现、验证或独立审查前，读取 `{target}/astra-planner.md`，"
             "并使用 `astra_*` 角色；当前上下文已有时复用。协议变化或上下文压缩丢失关键规则时补读。"
             "普通问答不触发完整委派流程。子 agent 不读该协议，按 Root 的自包含 TaskSpec 执行。"
             "目标项目的 AGENTS.md 提供项目约束。\n"
             f"{END}")
    if BEGIN in old or END in old:
        if old.count(BEGIN) != 1 or old.count(END) != 1 or old.index(BEGIN) > old.index(END):
            raise ValueError("Malformed managed instruction block; reconcile manually")
        start, stop = old.index(BEGIN), old.index(END) + len(END)
        return old[:start] + block + old[stop:]
    return old + ("\n\n" if old else "") + block + "\n"


def plan(target: Path, overwrite_local: bool = False, skip_instructions: bool = False) -> tuple[dict, dict]:
    desired = {}
    observed = {}

    def capture(rel: str) -> bytes | None:
        # The same bytes must drive both planning and apply's concurrent-edit check.
        if rel not in observed:
            observed[rel] = read_file(target / rel)
        return observed[rel]

    roles = sorted((BUNDLE / "agents").glob("*.toml"))
    if not roles:
        raise ValueError("Bundle has no roles")
    settings = tomllib.loads((BUNDLE / "config.toml").read_text())
    registrations = settings.setdefault("agents", {})
    for role in roles:
        data = role.read_bytes()
        parsed = tomllib.loads(data.decode())
        if parsed.get("name") != role.stem or not parsed.get("model"):
            raise ValueError(f"Role name/model missing or mismatched: {role.name}")
        rel = f"agents/{role.name}"
        desired[rel] = data
        registrations[role.stem] = {"config_file": f"./{rel}"}
    for name in HELPERS:
        data = (BUNDLE / name).read_bytes()
        if name.endswith(".py"):
            compile(data, name, "exec")
        desired[name] = data
    desired["astra-planner.md"] = (BUNDLE / "astra-planner.md").read_text().replace(
        "@@CODEX_HOME_SHELL@@", shlex.quote(str(target))).replace(
        "@@CODEX_HOME@@", str(target)).encode()

    state_bytes = capture(STATE)
    previous = json.loads(state_bytes) if state_bytes else {"files": {}}
    for rel, data in desired.items():
        current = capture(rel)
        prior_hash = previous["files"].get(rel)
        if prior_hash and (current is None or digest(current) != prior_hash) and current != data:
            if not overwrite_local:
                raise ValueError(f"Local edit detected: {rel}; commit it to the repository first, "
                                 "or use --overwrite-local with --apply (backup retained)")

    old_config = capture("config.toml") or b""
    parsed_config = tomllib.loads(old_config.decode())
    merged_config = copy.deepcopy(parsed_config)
    merge(merged_config, settings)
    desired["config.toml"] = (old_config if toml_value(merged_config) == toml_value(parsed_config)
                              else dump_toml(merged_config).encode())

    instruction_name = None
    if not skip_instructions:
        override = capture("AGENTS.override.md")
        instruction_name = "AGENTS.override.md" if override and override.strip() else "AGENTS.md"
        old_instructions = (capture(instruction_name) or b"").decode()
        desired[instruction_name] = instruction_text(old_instructions, target).encode()
    # Do not track the entire config or global instructions: unrelated local edits are allowed.
    managed = {rel: digest(data) for rel, data in desired.items()
               if rel not in ("config.toml", instruction_name)}
    desired[STATE] = (json.dumps({"version": 1, "files": managed}, indent=2) + "\n").encode()
    old = {rel: capture(rel) for rel in desired}
    return desired, old


def mkdir_checked(path: Path, mode: int = 0o700) -> None:
    if path.is_symlink():
        raise ValueError(f"Symlink directory refused: {path}")
    if path.exists():
        if not path.is_dir():
            raise ValueError(f"Not a directory: {path}")
        return
    # Create one level at a time, checking the parent before each mkdir.
    mkdir_checked(path.parent, mode)
    if not path.parent.is_dir():
        raise ValueError(f"Missing parent: {path.parent}")
    path.mkdir(mode=mode)


def atomic_write(path: Path, data: bytes, mode: int) -> None:
    read_file(path)
    mkdir_checked(path.parent)
    fd, name = tempfile.mkstemp(prefix=".agent-kit-", dir=path.parent)
    staged = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        staged.chmod(mode)
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def apply(target: Path, desired: dict, old: dict) -> Path | None:
    changes = {rel: data for rel, data in desired.items() if data != old[rel]}
    if not changes:
        return None
    mkdir_checked(target)
    backup_parent = target / "backups"
    mkdir_checked(backup_parent)
    backup = backup_parent / datetime.now(timezone.utc).strftime("codex-agent-kit-%Y%m%dT%H%M%S%fZ")
    backup.mkdir(mode=0o700)
    modes = {rel: stat.S_IMODE((target / rel).stat().st_mode)
             if old[rel] is not None else 0o600 for rel in changes}
    # All backups complete before the first destination write. Backups may contain private config.
    for rel in changes:
        if old[rel] is not None:
            atomic_write(backup / rel, old[rel], 0o600)
    manifest = {rel: {"before": digest(old[rel]) if old[rel] is not None else None,
                      "after": digest(changes[rel]), "mode": modes[rel]} for rel in changes}
    atomic_write(backup / "manifest.json", (json.dumps(manifest, indent=2) + "\n").encode(), 0o600)
    written = []
    try:
        for rel, data in changes.items():
            destination = target / rel
            if read_file(destination) != old[rel]:
                raise ValueError(f"Concurrent edit detected: {rel}")
            atomic_write(destination, data, modes[rel])
            written.append(rel)
        if any(read_file(target / rel) != data for rel, data in desired.items()):
            raise ValueError("Post-install verification failed")
    except Exception:
        for rel in reversed(written):
            if read_file(target / rel) != changes[rel]:
                print(f"Preserved concurrent edit: {rel}", file=sys.stderr)
                continue
            if old[rel] is None:
                (target / rel).unlink()
            else:
                atomic_write(target / rel, old[rel], modes[rel])
        print(f"Installation failed; recovery backup: {backup}", file=sys.stderr)
        raise
    return backup


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply", action="store_true", help="write changes after preflight and backup")
    group.add_argument("--check", action="store_true", help="exit 1 if installation needs updating")
    parser.add_argument("--target", type=Path, help="default: CODEX_HOME or ~/.codex")
    parser.add_argument("--overwrite-local", action="store_true", help="replace edited managed files after backup")
    parser.add_argument("--skip-instructions", action="store_true", help="leave global instructions untouched; maintain the planner entry yourself")
    args = parser.parse_args()
    target = (args.target or Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")).expanduser().absolute()
    if "\n" in str(target) or "\r" in str(target):
        parser.error("Target path cannot contain newlines")
    try:
        desired, old = plan(target, args.overwrite_local, args.skip_instructions)
        changed = [rel for rel in desired if desired[rel] != old[rel]]
        print(f"Target: {target}")
        if args.skip_instructions:
            print("Global instruction entry excluded; maintain the astra-planner.md entry yourself.")
        for rel in changed:
            print(f"{'UPDATE' if old[rel] is not None else 'CREATE'} {rel}")
        if args.check:
            print(f"{'CHECK FAIL' if changed else 'CHECK PASS'}: {len(changed)} files need updating")
            return 1 if changed else 0
        if not args.apply:
            print(f"PREVIEW: {len(changed)} files would change. Run with --apply to install.")
            return 0
        backup = apply(target, desired, old)
        print(f"APPLY PASS: {len(changed)} files changed and read back")
        if backup:
            print(f"Backup: {backup}")
        print("Start a new Codex session to verify role loading and model access on this machine.")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        # Do not print config contents or TOML parser context, which can contain secrets.
        message = "Invalid TOML; no installation performed" if isinstance(error, tomllib.TOMLDecodeError) else str(error)
        print(f"ERROR: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
