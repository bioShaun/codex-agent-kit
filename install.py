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
           "run-bounded.py", "work-package-metrics.py", "context-state.py")

# Codex 0.160.1 multi_agent_v2 reports tool_name collaborationspawn_agent.
# Matchers ^Agent$ and ^spawn_agent$ were tested and did not fire (issue #4),
# so they are not installed and are not treated as aliases.
HOOK_SCRIPT = "hooks/force_fork_turns_none.py"
HOOKS_JSON = "hooks.json"
HOOK_GROUP_STATE = "fork_turns_none"
SPAWN_MATCHER = "^collaborationspawn_agent$"
HOOK_TIMEOUT_SECONDS = 10


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


def hook_command(target: Path) -> str:
    # `python3` is resolved on the Codex host when the hook runs, as in the
    # documented command examples. Do not freeze the installer interpreter.
    script = (target / HOOK_SCRIPT).absolute()
    return "python3 " + shlex.quote(str(script))


def desired_hook_group(target: Path) -> dict:
    # Leave async unset. Background hooks are not allowed to rewrite tool input.
    return {
        "matcher": SPAWN_MATCHER,
        "hooks": [
            {
                "type": "command",
                "command": hook_command(target),
                "timeout": HOOK_TIMEOUT_SECONDS,
                "statusMessage": "Forcing fork_turns=none",
            }
        ],
    }


def canonical_json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def command_is_managed(command: object) -> bool:
    # Substring match survives shell quoting. The script basename is unique in this kit.
    return isinstance(command, str) and Path(HOOK_SCRIPT).name in command


def is_managed_group(group: object) -> bool:
    if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
        return False
    return any(isinstance(item, dict) and command_is_managed(item.get("command"))
               for item in group["hooks"])


def is_pure_managed_group(group: object) -> bool:
    hooks = group.get("hooks") if isinstance(group, dict) else None
    if not isinstance(hooks, list) or not hooks:
        return False
    return all(isinstance(item, dict) and command_is_managed(item.get("command")) for item in hooks)


def pre_tool_use_entries(hooks_table: object) -> list:
    if not isinstance(hooks_table, dict):
        raise ValueError("Cannot safely merge hooks: hooks value is not a table")
    if "PreToolUse" not in hooks_table:
        return []
    pre = hooks_table["PreToolUse"]
    if not isinstance(pre, list):
        raise ValueError("Cannot safely merge hooks: PreToolUse is not a list")
    return pre


def managed_indexes(pre: list) -> list[int]:
    return [index for index, group in enumerate(pre) if is_managed_group(group)]


def reconcile_pre_tool_use(pre: list, group: dict, prior_hash: str | None,
                           overwrite_local: bool) -> list | None:
    """Return a new PreToolUse list, or None when the on-disk group already matches."""
    indexes = managed_indexes(pre)
    if len(indexes) > 1:
        raise ValueError(
            "Cannot safely merge hooks: multiple managed PreToolUse entries; reconcile manually")
    if not indexes:
        if prior_hash and not overwrite_local:
            raise ValueError(
                "Local edit detected: managed PreToolUse hook removed; "
                "commit it to the repository first, or use --overwrite-local with --apply "
                "(backup retained)")
        return [*pre, group]
    current = pre[indexes[0]]
    if not is_pure_managed_group(current):
        # Replacing the group would drop the user's other handlers, including with --overwrite-local.
        raise ValueError(
            "Cannot safely merge hooks: managed command shares a matcher group with other "
            "handlers; reconcile manually")
    if canonical_json(current) == canonical_json(group):
        return None
    if prior_hash == digest(canonical_json(current)) or overwrite_local:
        updated = list(pre)
        updated[indexes[0]] = group
        return updated
    raise ValueError(
        "Local edit detected: managed PreToolUse hook; "
        "commit it to the repository first, or use --overwrite-local with --apply "
        "(backup retained)")


def load_hooks_document(raw: bytes | None) -> dict | None:
    if raw is None:
        return None
    try:
        document = json.loads(raw)
    except (json.JSONDecodeError, UnicodeError):
        raise ValueError("Invalid hooks.json; no installation performed")
    if not isinstance(document, dict):
        raise ValueError("Cannot safely merge hooks: hooks.json must be a JSON object")
    if "hooks" in document and not isinstance(document["hooks"], dict):
        raise ValueError("Cannot safely merge hooks: hooks.json hooks value is not an object")
    if isinstance(document.get("hooks"), dict):
        pre_tool_use_entries(document["hooks"])
    return document


def prior_hook_hash(previous: dict) -> str | None:
    stored = previous.get("hook_groups") or {}
    if not isinstance(stored, dict):
        raise ValueError("Invalid installer state; no installation performed")
    value = stored.get(HOOK_GROUP_STATE)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Invalid installer state; no installation performed")
    return value


def plan_hook_installation(target: Path, parsed_config: dict, merged_config: dict, capture,
                           previous: dict, overwrite_local: bool) -> tuple[bytes | None, dict | None]:
    """Choose one hook file and merge only this kit's PreToolUse group into it.

    Codex loads hooks.json and inline [hooks] together and warns when a single
    layer has both. Prefer hooks.json for a layer that does not already have
    hook config. If inline [hooks] is already the only source, merge there so
    this install does not create the second source. If the managed command
    already lives in one of the two files, keep editing that file. Never delete
    or relocate user hooks. A mixed matcher group is refused even with
    --overwrite-local, because replacing it would drop the other handlers.
    """
    group = desired_hook_group(target)
    prior = prior_hook_hash(previous)
    raw = capture(HOOKS_JSON)
    document = load_hooks_document(raw)
    inline = "hooks" in parsed_config
    if inline:
        if not isinstance(parsed_config["hooks"], dict):
            raise ValueError("Cannot safely merge hooks: config.toml hooks value is not a table")
        toml_pre = pre_tool_use_entries(parsed_config["hooks"])
    else:
        toml_pre = []
    if document is not None and isinstance(document.get("hooks"), dict):
        json_pre = pre_tool_use_entries(document["hooks"])
    else:
        json_pre = []
    json_hits = managed_indexes(json_pre)
    toml_hits = managed_indexes(toml_pre) if inline else []
    if json_hits and toml_hits:
        raise ValueError(
            "Cannot safely merge hooks: managed entry exists in both hooks.json and config.toml; "
            "reconcile manually")
    if json_hits:
        destination = HOOKS_JSON
    elif toml_hits:
        destination = "config.toml"
    elif document is not None:
        destination = HOOKS_JSON
    elif inline:
        destination = "config.toml"
    else:
        destination = HOOKS_JSON

    hooks_json_bytes = raw
    hooked_config = None
    if destination == HOOKS_JSON:
        base = copy.deepcopy(document) if document is not None else {}
        hooks = base.get("hooks")
        if not isinstance(hooks, dict):
            hooks = {}
            base["hooks"] = hooks
        updated = reconcile_pre_tool_use(
            pre_tool_use_entries(hooks), group, prior, overwrite_local)
        if updated is not None:
            hooks["PreToolUse"] = updated
            hooks_json_bytes = (json.dumps(base, indent=2, ensure_ascii=False) + "\n").encode()
    else:
        updated = reconcile_pre_tool_use(list(toml_pre), group, prior, overwrite_local)
        if updated is not None:
            hooked_config = copy.deepcopy(merged_config)
            hooks = hooked_config.get("hooks")
            if not isinstance(hooks, dict):
                hooks = {}
                hooked_config["hooks"] = hooks
            hooks["PreToolUse"] = updated
    return hooks_json_bytes, hooked_config


def instruction_text(old: str, target: Path) -> str:
    block = (f"{BEGIN}\n## Native Codex delegation\n\n"
             f"Codex Root 在委派实现、验证或独立审查前，读取 `{target}/astra-planner.md`，"
             "按入口只读取本轮所需的专项协议，并使用 `astra_*` 角色；当前上下文已有时复用。协议变化或上下文压缩丢失关键规则时补读。"
             "普通问答不触发完整委派流程。子 agent 不读该协议，按 Root 的自包含 TaskSpec 执行。"
             "目标项目的 AGENTS.md 提供项目约束。\n"
             f"{END}")
    if BEGIN in old or END in old:
        if old.count(BEGIN) != 1 or old.count(END) != 1 or old.index(BEGIN) > old.index(END):
            raise ValueError("Malformed managed instruction block; reconcile manually")
        start, stop = old.index(BEGIN), old.index(END) + len(END)
        return old[:start] + block + old[stop:]
    return old + ("\n\n" if old else "") + block + "\n"


CONTEXT_HOOK_MARKER = "codex-agent-kit: context recovery"


def context_hooks(document: dict, registration: dict | None, previous: dict,
                  overwrite: bool) -> bool:
    if not isinstance(document, dict) or not isinstance(document.get("hooks", {}), dict):
        raise ValueError("Invalid hooks document")
    before = copy.deepcopy(document)
    hooks = document.setdefault("hooks", {})
    groups = hooks.get("SessionStart", [])
    if not isinstance(groups, list):
        raise ValueError("Invalid SessionStart hooks")
    found = []
    preserved = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            raise ValueError("Invalid hook group")
        remaining = []
        for handler in group["hooks"]:
            if not isinstance(handler, dict):
                raise ValueError("Invalid hook handler")
            if handler.get("statusMessage") == CONTEXT_HOOK_MARKER:
                found.append({**group, "hooks": [handler]})
            else:
                remaining.append(handler)
        if remaining or not group["hooks"]:
            preserved.append({**group, "hooks": remaining})
    if len(found) > 1:
        raise ValueError("Duplicate context recovery handlers; reconcile manually")
    expected = previous.get("registration") if previous.get("enabled") else None
    actual = found[0] if found else None
    if expected is not None and actual != expected and actual != registration and not overwrite:
        raise ValueError("Local edit detected: context recovery hook; use --overwrite-local to replace after backup")
    if expected is None and actual is not None and actual != registration and not overwrite:
        raise ValueError("Local edit detected: conflicting context recovery hook")
    if registration is not None:
        # Keep the original position and group bytes for a true no-op update.
        if actual == registration:
            return False
        preserved.append(registration)
    if "SessionStart" in hooks or registration is not None:
        hooks["SessionStart"] = preserved
    return document != before


def plan(target: Path, overwrite_local: bool = False, skip_instructions: bool = False,
         context_recovery: bool | None = None) -> tuple[dict, dict]:
    desired = {}
    observed = {}

    def capture(rel: str) -> bytes | None:
        # The same bytes must drive both planning and apply's concurrent-edit check.
        if rel not in observed:
            observed[rel] = read_file(target / rel)
        return observed[rel]

    state_bytes = capture(STATE)
    previous = json.loads(state_bytes) if state_bytes else {"files": {}}
    prior_context = previous.get("context_recovery", {})
    context_enabled = prior_context.get("enabled", False) if context_recovery is None else context_recovery
    context_state = {"enabled": context_enabled}
    roles = sorted((BUNDLE / "agents").glob("*.toml"))
    if not roles:
        raise ValueError("Bundle has no roles")
    settings = tomllib.loads((BUNDLE / "config.toml").read_text())
    if "hooks" in settings:
        raise ValueError("Bundle config.toml must not contain hooks; install.py merges managed hooks")
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
    manifest_name = "protocols/manifest.json"
    manifest_data = (BUNDLE / manifest_name).read_bytes()
    manifest = json.loads(manifest_data)
    protocol_files = manifest.get("files") if isinstance(manifest, dict) else None
    if (not isinstance(manifest, dict) or manifest.get("version") != 1
            or not isinstance(protocol_files, list) or not protocol_files
            or any(not isinstance(name, str) for name in protocol_files)
            or len(protocol_files) != len(set(protocol_files))
            or "astra-planner.md" not in protocol_files):
        raise ValueError("Invalid protocol manifest")
    desired[manifest_name] = manifest_data
    for name in protocol_files:
        parts = name.split("/")
        if name != "astra-planner.md" and not (
                len(parts) == 2 and parts[0] == "protocols"
                and parts[1] not in (".", "..") and parts[1].endswith(".md")
                and "\\" not in name):
            raise ValueError(f"Invalid protocol path: {name}")
        desired[name] = (BUNDLE / name).read_text().replace(
            "@@CODEX_HOME_SHELL@@", shlex.quote(str(target))).replace(
            "@@CODEX_HOME@@", str(target)).encode()

    desired["templates/task-state.json"] = (BUNDLE / "templates/task-state.json").read_bytes()
    hook_script = (BUNDLE / HOOK_SCRIPT).read_bytes()
    compile(hook_script, HOOK_SCRIPT, "exec")
    desired[HOOK_SCRIPT] = hook_script
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
    if context_enabled or prior_context.get("enabled", False):
        hook_bytes = capture("hooks.json")
        inline_hooks = merged_config.get("hooks", {})
        json_document = json.loads(hook_bytes or b'{"hooks": {}}')
        if not isinstance(inline_hooks, dict) or not isinstance(json_document, dict):
            raise ValueError("Invalid hooks configuration")
        json_hooks = json_document.get("hooks", {})
        if not isinstance(json_hooks, dict):
            raise ValueError("Invalid hooks document")

        def owns_handler(events):
            groups = events.get("SessionStart", [])
            if not isinstance(groups, list):
                raise ValueError("Invalid SessionStart hooks")
            return any(isinstance(group, dict) and isinstance(group.get("hooks"), list)
                       and any(isinstance(handler, dict) and handler.get("statusMessage") == CONTEXT_HOOK_MARKER
                               for handler in group["hooks"]) for group in groups)

        owners = [name for name, events in (("config.toml", inline_hooks), ("hooks.json", json_hooks))
                  if owns_handler(events)]
        if len(owners) > 1:
            raise ValueError("Duplicate context recovery handlers in both hook sources; reconcile manually")
        inline_events = any(key in inline_hooks for key in (
            "SessionStart", "SessionEnd", "PreCompact", "PostCompact", "PreToolUse", "PostToolUse",
            "PermissionRequest", "UserPromptSubmit", "SubagentStart", "SubagentStop", "Stop", "Interrupt"))
        if prior_context.get("enabled"):
            source = prior_context.get("source")
            if source not in ("config.toml", "hooks.json") or (owners and owners[0] != source):
                raise ValueError("Context recovery hook source changed; reconcile the previous registration first")
        else:
            source = owners[0] if owners else ("config.toml" if inline_events and hook_bytes is None else "hooks.json")
        inline = source == "config.toml"
        document = {"hooks": inline_hooks} if inline else json_document
        command = shlex.join([sys.executable, str(target / "context-state.py"), "hook"])
        registration = {"matcher": "^(startup|resume|compact)$", "hooks": [{
            "type": "command", "command": command, "timeout": 10,
            "statusMessage": CONTEXT_HOOK_MARKER, "additionalContextLimit": 5000}]}
        changed = context_hooks(document, registration if context_enabled else None,
                                prior_context, overwrite_local)
        if inline:
            merged_config["hooks"] = document["hooks"]
        elif changed or hook_bytes is not None:
            desired["hooks.json"] = ((json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode()
                                     if changed else hook_bytes)
        if context_enabled:
            merged_config.setdefault("features", {})["hooks"] = True
            context_state.update(source=source, registration=registration)
    # Merge into the context hook's planned document, retaining the original
    # capture for apply's concurrent-edit checks. Both modules share these files.
    def capture_planned(rel: str) -> bytes | None:
        return desired[rel] if rel in desired else capture(rel)

    hooks_json_bytes, hooked_config = plan_hook_installation(
        target, merged_config, merged_config, capture_planned, previous, overwrite_local)
    if hooked_config is not None:
        merged_config = hooked_config
    if hooks_json_bytes is not None:
        desired[HOOKS_JSON] = hooks_json_bytes
    desired["config.toml"] = (old_config if toml_value(merged_config) == toml_value(parsed_config)
                              else dump_toml(merged_config).encode())

    instruction_name = None
    if not skip_instructions:
        override = capture("AGENTS.override.md")
        instruction_name = "AGENTS.override.md" if override and override.strip() else "AGENTS.md"
        old_instructions = (capture(instruction_name) or b"").decode()
        desired[instruction_name] = instruction_text(old_instructions, target).encode()
    # Shared configuration is tracked by managed registration, not whole-file hashes.
    managed = {rel: digest(data) for rel, data in desired.items()
               if rel not in ("config.toml", "hooks.json", instruction_name)}
    state = {"version": 1, "files": managed, "context_recovery": context_state,
             "hook_groups": {HOOK_GROUP_STATE: digest(canonical_json(desired_hook_group(target)))}}
    desired[STATE] = (json.dumps(state, indent=2) + "\n").encode()
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
    context = parser.add_mutually_exclusive_group()
    context.add_argument("--with-context-recovery", dest="context_recovery", action="store_const", const=True,
                         help="enable task-state recovery hooks; future updates remember this choice")
    context.add_argument("--without-context-recovery", dest="context_recovery", action="store_const", const=False,
                         help="remove only the kit recovery hook, retaining scripts and task data")
    parser.set_defaults(context_recovery=None)
    args = parser.parse_args()
    target = (args.target or Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")).expanduser().absolute()
    if "\n" in str(target) or "\r" in str(target):
        parser.error("Target path cannot contain newlines")
    try:
        desired, old = plan(target, args.overwrite_local, args.skip_instructions, args.context_recovery)
        changed = [rel for rel in desired if desired[rel] != old[rel]]
        print(f"Target: {target}")
        if args.skip_instructions:
            print("Global instruction entry excluded; maintain the astra-planner.md entry yourself.")
        for rel in changed:
            print(f"{'UPDATE' if old[rel] is not None else 'CREATE'} {rel}")
        if json.loads(desired[STATE])["context_recovery"]["enabled"]:
            print("Context recovery: ENABLED; runtime hook trust and event execution: NOT CHECKED (review /hooks in Codex)")
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
