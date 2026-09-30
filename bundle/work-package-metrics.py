#!/usr/bin/env python3
"""Opt-in, read-only metrics for explicitly selected Codex rollout turns."""

import argparse
from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path
import sys


MAX_ROLLOUT_BYTES = 64 * 1024 * 1024
MAX_THREADS = 100
MAX_TURNS = 1000
COUNTS = ("input_tokens", "cached_input_tokens", "uncached_input_tokens", "output_tokens")


class MetricsError(Exception):
    pass


def strict_json(contents, label):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise MetricsError(f"duplicate JSON key {key!r} in {label}")
            result[key] = value
        return result

    def invalid_constant(value):
        raise MetricsError(f"nonstandard JSON constant {value} in {label}")

    try:
        return json.loads(contents, object_pairs_hook=unique_pairs,
                          parse_constant=invalid_constant)
    except (ValueError, UnicodeError) as exc:
        raise MetricsError(f"invalid JSON in {label}") from exc


def require(condition, message):
    if not condition:
        raise MetricsError(message)


def timestamp(value, label):
    require(isinstance(value, str), f"{label} must be an ISO 8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MetricsError(f"{label} is not an ISO 8601 timestamp") from exc
    require(parsed.tzinfo is not None and parsed.utcoffset() is not None,
            f"{label} must have a timezone")
    return parsed


def text(value, label):
    require(isinstance(value, str) and bool(value.strip()), f"{label} must be nonempty text")
    return value


def token_counts(usage):
    require(isinstance(usage, dict), "usage must be an object")
    for key in ("input_tokens", "cached_input_tokens", "output_tokens"):
        require(type(usage.get(key)) is int and usage[key] >= 0, f"invalid usage.{key}")
    inp, cached, out = (usage[key] for key in
                        ("input_tokens", "cached_input_tokens", "output_tokens"))
    require(cached <= inp, "cached input exceeds input")
    for key in ("reasoning_output_tokens", "cache_write_input_tokens", "total_tokens"):
        if key in usage:
            require(type(usage[key]) is int and usage[key] >= 0, f"invalid usage.{key}")
    if "reasoning_output_tokens" in usage:
        require(usage["reasoning_output_tokens"] <= out, "reasoning output exceeds output")
    if "total_tokens" in usage:
        require(usage["total_tokens"] == inp + out, "inconsistent usage.total_tokens")
    return dict(zip(COUNTS, (inp, cached, inp - cached, out)))


def read_rollout(path, selected):
    require(path.is_file(), f"rollout missing: {path}")
    require(path.stat().st_size <= MAX_ROLLOUT_BYTES, f"rollout exceeds {MAX_ROLLOUT_BYTES} bytes: {path}")
    identity, provenance, parent_id = None, None, None
    starts, completes, models, records, usage_times, contexts = {}, {}, {}, {}, [], []
    latest_model_event = {}
    foreign_usage = 0
    with path.open(encoding="utf-8") as source:
        for number, line in enumerate(source, 1):
            event = strict_json(line, f"{path}:{number}")
            require(isinstance(event, dict), f"invalid event in {path}:{number}")
            kind, payload = event.get("type"), event.get("payload")
            if kind == "session_meta":
                require(isinstance(payload, dict), f"invalid session_meta in {path}:{number}")
                ident = text(payload.get("id"), "session_meta.id")
                require(identity is None, f"multiple session_meta events in {path}")
                identity = ident
                marker = payload.get("parent_thread_id")
                source = payload.get("source")
                spawn = (source.get("subagent", {}).get("thread_spawn", {})
                         if isinstance(source, dict) and isinstance(source.get("subagent"), dict) else {})
                source_parent = spawn.get("parent_thread_id") if isinstance(spawn, dict) else None
                if marker is not None:
                    text(marker, "parent_thread_id")
                if source_parent is not None:
                    text(source_parent, "source parent_thread_id")
                require(not (marker and source_parent and marker != source_parent),
                        f"contradictory parent markers in {path}")
                require(marker != identity and source_parent != identity, "thread cannot parent itself")
                parent_id = marker or source_parent
                provenance = ("child" if marker or source_parent else
                              "root" if "parent_thread_id" in payload and isinstance(source, str) else
                              "unknown")
            if kind not in ("event_msg", "turn_context", "token_usage_record"):
                continue
            require(isinstance(payload, dict), f"invalid payload in {path}:{number}")
            turn = payload.get("turn_id")
            require(turn is None or isinstance(turn, str), f"invalid turn_id in {path}:{number}")
            if turn not in selected:
                continue
            when = timestamp(event.get("timestamp"), f"{path}:{number} timestamp")
            if kind == "turn_context":
                require(turn not in latest_model_event or when >= latest_model_event[turn],
                        f"turn context out of event time order: {turn}")
                latest_model_event[turn] = when
                models[turn] = text(payload.get("model"), "turn_context.model")
                contexts.append((turn, when))
            elif kind == "event_msg" and payload.get("type") in ("task_started", "task_complete"):
                target = starts if payload["type"] == "task_started" else completes
                require(turn not in target, f"duplicate {payload['type']} for {turn}")
                target[turn] = when
            elif kind == "token_usage_record":
                require(identity is not None, f"usage before session_meta in {path}:{number}")
                text(payload.get("thread_id"), "token_usage_record.thread_id")
                if payload.get("thread_id") != identity:
                    foreign_usage += 1
                    continue
                response = text(payload.get("response_id"), "response_id")
                counts = token_counts(payload.get("usage"))
                key = (identity, response)
                value = (turn, models.get(turn), counts)
                if key in records:
                    require(records[key][0] == turn and records[key][2] == counts,
                            f"conflicting duplicate response {response}")
                else:
                    require(turn not in latest_model_event or when >= latest_model_event[turn],
                            f"response precedes preceding model context: {response}")
                    latest_model_event[turn] = when
                    records[key] = value
                usage_times.append((turn, when, response))
    require(identity is not None, f"missing session_meta.id: {path}")
    for turn in starts.keys() & completes.keys():
        require(starts[turn] <= completes[turn], f"turn completes before start: {turn}")
    for turn, when in contexts:
        require(turn not in starts or when >= starts[turn], f"turn context before turn start: {turn}")
        require(turn not in completes or when <= completes[turn],
                f"turn context after turn complete: {turn}")
    for turn, when, response in usage_times:
        require(turn not in starts or when >= starts[turn],
                f"usage response before turn start: {response}")
        require(turn not in completes or when <= completes[turn],
                f"usage response after turn complete: {response}")
    return identity, provenance, parent_id, starts, completes, records, usage_times, contexts, foreign_usage


def sum_counts(items):
    return {key: sum(item[key] for item in items) for key in COUNTS}


def run(spec_path):
    require(spec_path.is_file() and spec_path.stat().st_size <= 1024 * 1024,
            "spec missing or exceeds 1 MiB")
    try:
        spec = strict_json(spec_path.read_text(encoding="utf-8"), str(spec_path))
    except (OSError, ValueError, UnicodeError) as exc:
        raise MetricsError(f"cannot read JSON spec: {spec_path}") from exc
    require(isinstance(spec, dict), "spec must be an object")
    result = {key: text(spec.get(key), key) for key in ("work_id", "project", "task_type")}
    start = timestamp(spec.get("started_at"), "started_at")
    finish = timestamp(spec["finished_at"], "finished_at") if spec.get("finished_at") is not None else None
    require(finish is None or finish >= start, "finished_at precedes started_at")
    outcome = spec.get("outcome")
    require(outcome in ("PASS", "FAIL", "BLOCKED", "IN_PROGRESS", "UNKNOWN"), "invalid outcome")
    require(finish is None or outcome != "IN_PROGRESS", "finished task cannot be IN_PROGRESS")
    if "rework_count" in spec:
        require(type(spec["rework_count"]) is int and spec["rework_count"] >= 0,
                "rework_count must be a nonnegative integer")
    if "routing_notes" in spec:
        require(isinstance(spec["routing_notes"], str), "routing_notes must be text")
    threads = spec.get("threads")
    require(isinstance(threads, list) and 1 <= len(threads) <= MAX_THREADS, "threads must be a nonempty bounded array")
    rows, seen, all_records, roles, parents = [], set(), {}, {}, {}
    foreign_total = 0
    unknown_provenance = []
    for entry in threads:
        require(isinstance(entry, dict), "thread entry must be an object")
        role = entry.get("role")
        require(role in ("root", "child"), "role must be root or child")
        path = (spec_path.parent / text(entry.get("rollout"), "rollout")).resolve()
        turns = entry.get("turn_ids")
        require(isinstance(turns, list) and 1 <= len(turns) <= MAX_TURNS, "turn_ids must be a nonempty bounded array")
        require(all(isinstance(t, str) and t.strip() for t in turns), "invalid turn_id")
        require(len(set(turns)) == len(turns), "duplicate turn selection")
        identity, provenance, parent_id, starts, completes, records, usage_times, contexts, foreign_usage = read_rollout(path, set(turns))
        foreign_total += foreign_usage
        require(provenance == "unknown" or provenance == role,
                f"role {role} contradicts source provenance {provenance}: {identity}")
        if provenance == "unknown" and identity not in unknown_provenance:
            unknown_provenance.append(identity)
        for turn, when, response in usage_times:
            require(when >= start, f"usage response before work start: {response}")
            require(finish is None or when <= finish,
                    f"usage response after work finish: {response}")
        for turn, when in contexts:
            require(when >= start and (finish is None or when <= finish),
                    f"turn context outside work bounds: {turn}")
        require(identity not in roles or roles[identity] == role,
                f"contradictory roles for thread {identity}")
        require(identity not in parents or parents[identity] == parent_id,
                f"contradictory parents for thread {identity}")
        roles[identity] = role
        parents[identity] = parent_id
        for turn in turns:
            require((identity, turn) not in seen, f"duplicate selected thread and turn: {identity} {turn}")
            seen.add((identity, turn))
            if turn in starts:
                require(starts[turn] >= start, f"selected turn starts before work: {turn}")
                require(finish is None or starts[turn] <= finish, f"selected turn starts after work: {turn}")
            if turn in completes:
                require(turn in starts, f"turn completes without starting: {turn}")
                require(finish is None or completes[turn] <= finish, f"selected turn completes after work: {turn}")
        for key, value in records.items():
            if key in all_records:
                require(all_records[key] == value, f"conflicting duplicate response: {key[1]}")
            else:
                all_records[key] = value
        rows.append({"thread_id": identity, "role": role, "parent_thread_id": parent_id,
                     "turn_ids": turns,
                     "starts": starts, "completes": completes, "records": records})
    require(sum(row["role"] == "root" for row in rows) == 1, "exactly one root thread entry required")
    root_id = next(row["thread_id"] for row in rows if row["role"] == "root")
    for identity in parents:
        trail = set()
        current = identity
        while current != root_id and parents[current] is not None:
            require(current not in trail, f"cycle in selected parent links: {identity}")
            trail.add(current)
            parent = parents[current]
            require(parent in parents, f"selected parent missing for thread {current}: {parent}")
            current = parent
        require(current == root_id or current in unknown_provenance,
                f"thread {identity} cannot reach selected Root {root_id}")
    by_turn = defaultdict(list)
    for (identity, _), (turn, model, counts) in all_records.items():
        by_turn[(identity, turn)].append((model or "<unknown>", counts))
    missing, incomplete, no_usage, unknown_model = [], [], [], []
    grouped = defaultdict(list)
    thread_outputs = []
    for row in rows:
        known = []
        by_model = defaultdict(list)
        for turn in row["turn_ids"]:
            label = {"thread_id": row["thread_id"], "turn_id": turn}
            if turn not in row["starts"]:
                missing.append(label)
            elif turn not in row["completes"]:
                incomplete.append(label)
            usage = by_turn[(row["thread_id"], turn)]
            if not usage:
                no_usage.append(label)
            for model, counts in usage:
                known.append(counts)
                grouped[(row["role"], model)].append(counts)
                by_model[model].append(counts)
                if model == "<unknown>" and label not in unknown_model:
                    unknown_model.append(label)
        thread_outputs.append({"thread_id": row["thread_id"], "role": row["role"],
                               "parent_thread_id": row["parent_thread_id"],
                               "selected_turns": len(row["turn_ids"]),
                               "by_model": [{"model": model, "known_usage": sum_counts(counts),
                                             "responses": len(counts)} for model, counts in sorted(by_model.items())],
                               "known_usage": sum_counts(known),
                               "tokens": None if any(t["thread_id"] == row["thread_id"] for t in missing + incomplete + no_usage) else sum_counts(known)})
    warnings = []
    if missing or incomplete or no_usage or unknown_model or foreign_total or unknown_provenance:
        warnings.append("Selected telemetry is partial; known_usage sums only recorded responses.")
    if foreign_total:
        warnings.append("Foreign-thread usage on selected turns was excluded to prevent mirrored child double counting.")
    if unknown_provenance:
        warnings.append("Some source formats did not identify parentage; declared roles could not be verified.")
    warnings.append("Selection is declared by the spec; related agents or turns omitted from it cannot be detected.")
    result.update({"outcome_declared": outcome, "rework_count_declared": spec.get("rework_count"),
                   "routing_notes_declared": spec.get("routing_notes"),
                   "started_at": start.isoformat(), "finished_at": finish.isoformat() if finish else None,
                   "elapsed_seconds": (finish - start).total_seconds() if finish else None,
                   "threads": thread_outputs,
                   "by_role_model": [{"role": role, "model": model, "known_usage": sum_counts(counts),
                                      "responses": len(counts)} for (role, model), counts in sorted(grouped.items())],
                   "known_usage": sum_counts([counts for counts_list in grouped.values() for counts in counts_list]),
                   "tokens": None if missing or incomplete or no_usage else sum_counts([counts for counts_list in grouped.values() for counts in counts_list]),
                   "missing_turns": missing, "incomplete_turns": incomplete,
                   "turns_without_usage": no_usage, "turns_with_unknown_model": unknown_model,
                   "unknown_provenance_thread_ids": unknown_provenance,
                   "foreign_thread_usage_records_excluded": foreign_total,
                   "warnings": warnings})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True, type=Path, help="JSON manifest; rollout paths are relative to this file")
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.spec.resolve()), ensure_ascii=False, sort_keys=True))
    except (MetricsError, OSError, UnicodeError) as exc:
        print(json.dumps({"error": {"type": "invalid_input", "message": str(exc)}}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
