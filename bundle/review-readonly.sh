#!/usr/bin/env bash
# Run a fresh Reviewer below a parent whose filesystem policy is read-only.
set -euo pipefail
if [[ ( $# -ne 1 && $# -ne 3 ) || ( ! -f "$1" && ! -d "$1" ) ]]; then
  printf 'Usage: bash %s REQUEST.json|PACKAGE_DIR [--artifacts NEW_DIR]\n' "$0" >&2
  exit 2
fi
if [[ $# -eq 3 && "$2" != '--artifacts' ]]; then
  printf 'review-readonly: expected --artifacts NEW_DIR\n' >&2
  exit 2
fi
if [[ -d "$1" && $# -ne 3 ]]; then
  printf 'review-readonly: prepared packages require --artifacts NEW_DIR to preserve raw results\n' >&2
  exit 2
fi
for dependency in python3; do
  if ! command -v "$dependency" >/dev/null 2>&1; then
    printf 'review-readonly: required command not found: %s\n' "$dependency" >&2
    exit 127
  fi
done
# Keep a stable request for both dispatch and result checks; scratch stays in cwd.
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
review_scratch="$(mktemp -d "$PWD/.astra-review.XXXXXXXX")"
trap 'rm -rf -- "$review_scratch"' EXIT
export TMPDIR="$review_scratch"
artifact_dir=''
if [[ $# -eq 3 ]]; then
  # No -p: refuse an existing output; its parent must already exist.
  mkdir -- "$3"
  artifact_dir="$(cd -- "$3" && pwd)"
fi
record_phase() {
  if [[ -n "$artifact_dir" ]]; then
    python3 -c 'import datetime,json,sys,time; f=open(sys.argv[1], "a"); f.write(json.dumps({"phase":sys.argv[2],"status":sys.argv[3],"utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),"monotonic_seconds":time.monotonic()})+"\n"); f.close()' "$artifact_dir/timing.jsonl" "$1" "${2:-}"
  fi
}
cleanup() {
  final_status=$?
  trap - EXIT
  record_phase finished "$final_status" || true
  rm -rf -- "$review_scratch"
  exit "$final_status"
}
trap cleanup EXIT
record_phase preflight_started
package_dir=''
request_source="$1"
review_timeout="${REVIEW_READONLY_TIMEOUT:-600}"
review_seconds='not specified; follow the TaskSpec advisory budget'
if [[ -d "$1" ]]; then
  package_dir="$(cd -- "$1" && pwd)"
  if python3 "$script_dir/review-workflow.py" check "$package_dir" > "$artifact_dir/check-before.json"; then
    cp -- "$artifact_dir/check-before.json" "$review_scratch/check.json"
  else
    check_status=$?
    cat -- "$artifact_dir/check-before.json" >&2
    exit "$check_status"
  fi
  python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if isinstance(d.get("budget"),dict) else "review-readonly: prepared strict package requires a budget")' "$review_scratch/check.json"
  request_source="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["request_path"])' "$review_scratch/check.json")"
  review_timeout="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["budget"]["timeout_seconds"])' "$review_scratch/check.json")"
  review_seconds="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["budget"]["review_seconds"])' "$review_scratch/check.json")"
  if [[ -n "${REVIEW_READONLY_TIMEOUT+x}" && "$REVIEW_READONLY_TIMEOUT" != "$review_timeout" ]]; then
    printf 'review-readonly: environment timeout conflicts with the frozen package budget; prepare a new package with a recorded reason\n' >&2
    exit 2
  fi
fi
cp -- "$request_source" "$review_scratch/request.json"
request_file="$review_scratch/request.json"
python3 "$script_dir/review-contract.py" preflight "$request_file" >&2
python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); sys.exit(0 if r["isolation_requirement"] == "strict" else "review-readonly: isolation_requirement must be strict")' "$request_file"
if ! command -v codex >/dev/null 2>&1; then
  printf 'review-readonly: required command not found: codex\n' >&2
  exit 127
fi
if [[ -n "$artifact_dir" ]]; then
  cp -- "$request_file" "$artifact_dir/request.json"
  final_file="$artifact_dir/final.txt"
else
  final_file="$review_scratch/final.txt"
fi
record_phase preflight_finished
# The parent is a relay: probe, spawn, wait, relay. Its own reasoning latency is
# charged against the same external deadline as the child's review, so it runs
# at low effort. The child keeps the effort fixed in its role file; verify from
# the child's turn_context, never from the parent's self-report.
parent_effort="${REVIEW_READONLY_PARENT_EFFORT:-low}"
record_phase model_started
set +e
{
  printf '%s\n' 'Run one independent review in the current project. You are a read-only Root.
Delegate the ReviewRequest below to agent_type astra_reviewer, task_name independent_review,
with fork_turns="none". Do not override the role model or effort. Use no other agents.
Provide the request as a self-contained task. The child must not delegate further.
Wait for the child to finish and return its verdict, evidence, and limitations faithfully.
Do not edit files or run implementation work. The external deadline in seconds is stated below, just before the ReviewRequest.
If the role cannot load, report BLOCKED with the actual error; do not impersonate it.
Confirm the parent and child actual runtime filesystem permissions with evidence. A role TOML
or behavioral refusal is not proof of read-only enforcement. Missing executable evidence is BLOCKED.
Budget: the deadline covers your own steps too. Prove your permission with one command that
needs no writable filesystem: python3 -c '"'"'...'"'"' with the probe inline. Do not use here-documents
or temp files; the sandbox has no writable TMPDIR and a here-document fails before the probe
runs. If the command itself fails to start, retry once in that inline form, then spawn at once.
The ReviewRequest is a JSON object: its metadata and instructions together are the task.
Pass the entire child TaskSpec from the ReviewRequest verbatim. Do not add your own probe result,
findings, hypotheses, or verdict proposals to the child'"'"'s task; your probe result belongs only
in your final answer. While waiting, only call wait; send no messages to the child. When the
child completes, relay its final message immediately in your final answer, followed by your own
probe evidence, without any further command, verification, or message.
Your first line must be the child'"'"'s exact first line, without Markdown or normalization.
The launcher checks the first line against the requested review_kind. A malformed result
or a missing result is a contract error; do not turn it into PASS. Do not infer acceptance
from the fact that this session completed.
'
  printf 'External deadline: %s seconds.\n' "$review_timeout"
  printf 'Child advisory assessment budget: %s seconds.\n\nReviewRequest:\n' "$review_seconds"
  cat -- "$request_file"
} | python3 "$script_dir/run-bounded.py" --timeout "$review_timeout" --grace 10 codex exec --sandbox read-only -c "model_reasoning_effort=\"$parent_effort\"" --json --output-last-message "$final_file" -
launcher_status=$?
set -e
record_phase model_finished "$launcher_status"
case "$launcher_status" in
  0)
    record_phase result_check_started
    if [[ -n "$package_dir" ]]; then
      if python3 "$script_dir/review-workflow.py" check "$package_dir" > "$artifact_dir/check-after.json"; then
        cat -- "$artifact_dir/check-after.json" >&2
      else
        check_status=$?
        cat -- "$artifact_dir/check-after.json" >&2
        exit "$check_status"
      fi
      if ! cmp -s -- "$request_file" "$package_dir/request.json"; then
        printf 'review-readonly: request changed during launch; do not accept\n' >&2
        exit 2
      fi
    fi
    set +e
    python3 "$script_dir/review-contract.py" result "$request_file" "$final_file" >&2
    contract_status=$?
    set -e
    if [[ "$contract_status" -ne 0 ]]; then
      printf 'review-readonly: contract_error; do not accept this review\n' >&2
      exit "$contract_status"
    fi
    record_phase result_check_finished "$contract_status"
    printf 'review-readonly: result format valid; verify verdict, source freshness, validation and both permission probes before acceptance\n' >&2
    ;;
  124) printf 'review-readonly: %s-second deadline exceeded\n' "$review_timeout" >&2 ;;
  137) printf 'review-readonly: process killed after grace period; confirm no residual process before continuing\n' >&2 ;;
  *) printf 'review-readonly: launch/session failed (exit %s)\n' "$launcher_status" >&2 ;;
esac
exit "$launcher_status"
