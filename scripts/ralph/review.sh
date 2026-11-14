#!/usr/bin/env bash
# shellcheck disable=SC2016  # jq programs are single-quoted on purpose
# Independent review of one Ralph story (PRD 23.3-23.5). Reviewers that did not write the change, run in parallel:
#   1. a fresh read-only Claude session with the project's verifier role (.claude/agents/verifier.md: its
#      model and instructions), which reruns the checks. It is not started with --agent because, in
#      Claude Code 2.1.325, --agent drops --json-schema structured output;
#   2. Codex with gpt-6.1-sol in its read-only sandbox (PRD 23.4), which reviews the diff against the spec;
#   3. a second, differently focused Codex pass when the story sets "codexPasses": 2 (the numerical core).
# Writes reviews/<id>-claude[-rN].json, reviews/<id>-codex[-rN].json and reviews/<id>-codex-2[-rN].json.
# Each wraps the reviewer's answer (reviews/schema.json) with task, reviewer identity, input hashes and
# timestamps. A reviewer that fails or times out is recorded as NOT_RUN with the reason; then this exits 1.
#
# Usage: review.sh <story-id> <round> <base-sha> <head-sha>
# Env:   RALPH_PRD (story list; default scripts/ralph/prd.json), RALPH_LOG_DIR (default logs/agents/<date>),
#        RALPH_REVIEW_LIST (optional file; the written review paths are appended to it),
#        REVIEW_TIMEOUT per reviewer (default 2700 s, raised from PRD 23.4's 900 s at the team's request).
#        Codex always uses gpt-6.1-sol (team decision); there is no substitution.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(git -C "$DIR" rev-parse --show-toplevel)"
cd "$ROOT"
id="$1"; round="$2"; base="$3"; head="$4"
PRD="${RALPH_PRD:-$DIR/prd.json}"
PRD_REL="${PRD#"$ROOT"/}"
SCHEMA="reviews/schema.json"
CODEX_MODEL="gpt-6.1-sol"
REVIEW_TIMEOUT="${REVIEW_TIMEOUT:-2700}"
LOG_DIR="${RALPH_LOG_DIR:-logs/agents/$(date -u +%F)}"; RAW_DIR="$LOG_DIR/raw"; STAMP="$(date -u +%H%M%S)"
suffix=""; [ "$round" -gt 1 ] && suffix="-r$round"
WORK_SPEC=(':!scripts/ralph' ':!reviews' ':!logs')
mkdir -p "$RAW_DIR" reviews
unset CLAUDECODE CLAUDE_CODE_ENTRYPOINT CLAUDE_CODE_MESSAGING_SOCKET CLAUDE_CODE_MESSAGING_TOKEN \
  CLAUDE_CODE_SESSION_ID CLAUDE_CODE_CHILD_SESSION CLAUDE_CODE_SESSION_ATTENDED CLAUDE_PID \
  CLAUDE_EFFORT CLAUDE_CODE_EXECPATH 2>/dev/null || true
export BASH_DEFAULT_TIMEOUT_MS="${BASH_DEFAULT_TIMEOUT_MS:-900000}" BASH_MAX_TIMEOUT_MS="${BASH_MAX_TIMEOUT_MS:-3600000}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
with_timeout() { # macOS has no coreutils timeout by default
  local s="$1"; shift
  if command -v timeout >/dev/null; then timeout "$s" "$@"
  elif command -v gtimeout >/dev/null; then gtimeout "$s" "$@"
  else perl -e 'alarm shift; exec @ARGV or die "exec failed: $!\n"' "$s" "$@"; fi
}

story="$(jq --arg id "$id" '.userStories[] | select(.id==$id) | {id, title, description, prdRefs, files, acceptanceCriteria, checks}' "$PRD")"
[ -n "$story" ] || { echo "review.sh: no story $id in $PRD" >&2; exit 1; }
codex_passes="$(jq -r --arg id "$id" '.userStories[] | select(.id==$id) | .codexPasses // 1' "$PRD")"

# The story's own changes: paths touched by first-parent, non-merge commits in base..head, so merges of
# other branches into a batch branch are not reviewed as part of the story.
story_paths="$(git log --first-parent --no-merges --name-only --format= "$base..$head" -- . "${WORK_SPEC[@]}" | sed '/^$/d' | sort -u)"
story_commits="$(git log --first-parent --no-merges --format='%h %s' "$base..$head" -- . "${WORK_SPEC[@]}")"
# Inputs: every story path with its SHA-256 at head (null if deleted).
inputs="$(printf '%s\n' "$story_paths" | sed '/^$/d' | while IFS= read -r p; do
  if git cat-file -e "$head:$p" 2>/dev/null; then h="$(git show "$head:$p" | shasum -a 256 | cut -d' ' -f1)"; else h=""; fi
  jq -n --arg p "$p" --arg h "$h" '{path: $p, sha256: (if $h == "" then null else $h end)}'
done | jq -s --arg b "$base" --arg h "$head" '{base: $b, head: $h, files: .}')"

prior=""
if [ "$round" -gt 1 ]; then
  earlier=()
  for f in reviews/"$id"-*.json; do [ -e "$f" ] && earlier+=("$f"); done
  if [ "${#earlier[@]}" -gt 0 ]; then
    prior="Previous rounds and gate results, with the resolutions written by the author. Check every fix and every rebuttal:
$(jq -s --argjson r "$round" '[.[] | select(.round < $r or .reviewer.kind == "gate") | {reviewer: .reviewer.kind, round, findings}]' "${earlier[@]}")"
  fi
fi

prompt() { # <reviewer-specific instructions>
  cat <<EOF
Independent review of Terraluna Ralph story $id, review round $round (PRD section 23.3).
You did not write this change. Do not create, edit or delete files, install anything, or commit.

Story contract, from $PRD_REL:
$story

Change under review: the story's own commits (first-parent, non-merge) in $base..$head:
$story_commits
Paths they touch, which is the scope to review:
$story_paths
See the change with: git diff $base $head -- <those paths>. Merges of other branches are not part of this story.
Binding rules: the root CLAUDE.md and the PRD.md sections in prdRefs. PRD.md is about 1,700 lines;
find headings with grep -n '^#' PRD.md and read ranges with sed -n.

1. Read the diff and the changed files.
2. Judge every acceptance criterion PASS, FAIL or NOT_RUN from evidence you saw yourself
   (command output or path:line), never from commit messages, progress logs or code comments.
3. Look in particular for: tests that are no-ops, weakened, or do not exercise the stated behaviour;
   missing values coerced to zero; mock data reachable from a production path; invented results,
   gate passes or kickoff values in config/run.yaml; paths changed outside the story's files list;
   conflicts with the PRD or CLAUDE.md; and anything that does not work as written.
4. Severity: blocker = a criterion fails or a scientific or safety rule is broken; high = a likely
   defect, or a required behaviour is untested; medium = fix before merge; low = optional.
$1
$prior
Return only the structured result.
EOF
}

record() { # <label> <kind> <file> <model> <tool_version> <status> <reason> <started> <answer-json-or-empty>
  jq -n --arg task "$id" --argjson round "$round" --arg label "$1" --arg kind "$2" --arg model "$4" --arg ver "$5" \
    --arg status "$6" --arg reason "$7" --arg started "$8" --arg finished "$(now)" \
    --argjson inputs "$inputs" --argjson out "${9:-null}" '
    {task_id: $task, round: $round,
     reviewer: {kind: $kind, label: $label, model: $model, tool_version: $ver},
     inputs: $inputs, started_utc: $started, finished_utc: $finished,
     status: $status, not_run_reason: (if $reason == "" then null else $reason end),
     verdict: ($out.verdict // "NOT_RUN"), summary: ($out.summary // null), checks: ($out.checks // []),
     findings: (($out.findings // []) | to_entries
                | map(.value + {id: "\($label)-r\($round)-\(.key + 1)", resolution: null}))}' > "$3"
  echo "review.sh: wrote $3 ($6)"
  jq -c --arg ts "$(now)" '{ts: $ts, agent: (if .reviewer.kind == "claude" then "verifier" else .reviewer.label end), story: .task_id, event: "review", round, model: .reviewer.model, status, verdict, findings: (.findings | length)}' "$3" \
    >> "$LOG_DIR/$(if [ "$2" = claude ]; then echo verifier; else echo codex; fi).jsonl"
  [ -z "${RALPH_REVIEW_LIST:-}" ] || echo "$3" >> "$RALPH_REVIEW_LIST"
  [ "$6" = COMPLETED ] && echo ok > "$TMP/$1.status" || echo fail > "$TMP/$1.status"
}

# ---- Claude verifier: fresh process, verifier role, only reading tools and the story's checks ----
review_claude() {
  local out_file="reviews/$id-claude$suffix.json" started status reason answer model
  local AGENT_FILE=".claude/agents/verifier.md" VERIFIER_MODEL VERIFIER_ROLE SHAPE
  VERIFIER_MODEL="$(sed -n '2,/^---$/s/^model:[[:space:]]*//p' "$AGENT_FILE" | head -1)"
  VERIFIER_ROLE="$(awk 'f>=2 {print} /^---$/ {f++}' "$AGENT_FILE")"
  # Accept an answer only if it has the schema's shape (used when structured_output is absent).
  SHAPE='type == "object" and (.verdict | IN("PASS", "FAIL", "NOT_RUN")) and (.summary | type == "string")
    and (.checks | type == "array") and (.findings | type == "array")
    and all(.findings[]; (.severity | IN("blocker", "high", "medium", "low")) and (.claim | type == "string"))'
  started="$(now)"
  local allowed=(Read Grep Glob "Bash(git diff *)" "Bash(git log *)" "Bash(git show *)" "Bash(git status *)"
    "Bash(git rev-parse *)" "Bash(git ls-files *)" "Bash(ls *)" "Bash(cat *)" "Bash(head *)" "Bash(tail *)"
    "Bash(wc *)" "Bash(jq *)")
  while IFS= read -r c; do allowed+=("Bash($c)"); done < <(jq -r '.checks[]' <<<"$story")
  if prompt "Run every command in the story's checks list, each exactly as written and in its own Bash call. Do not chain commands with ;, &&, || or |, and do not add redirects or shell variables: the permission rules allow only those exact commands plus single read-only git, ls, cat, head, tail, wc and jq commands, and deny anything else. A failing check shows its exit code in the tool result. Use the Read and Grep tools to inspect files." \
     | with_timeout "$REVIEW_TIMEOUT" claude -p --model "${VERIFIER_MODEL:-sonnet}" --append-system-prompt "$VERIFIER_ROLE" \
         --permission-mode dontAsk --permission-prompts none \
         --allowedTools "${allowed[@]}" --disallowedTools Agent Workflow Edit Write NotebookEdit WebFetch WebSearch \
         --json-schema "$(cat "$SCHEMA")" --output-format stream-json --verbose > "$TMP/claude.jsonl" 2> "$TMP/claude.err"; then
    status=COMPLETED; reason=""
  else
    status=NOT_RUN; reason="claude exited $?: $(tail -c 600 "$TMP/claude.err" | tr '\n' ' ')"
  fi
  cp "$TMP/claude.jsonl" "$RAW_DIR/verifier-$id-r$round-$STAMP.jsonl"
  jq -R -c --arg ts "$(now)" --arg story "$id" 'fromjson? | select(.type == "assistant") | .message.content[]? | select(.type == "tool_use") | {ts: $ts, agent: "verifier", story: $story, event: "tool_use", tool: .name, input: ((.input.command // .input.file_path // .input.pattern // "") | tostring | .[0:400])}' "$TMP/claude.jsonl" >> "$LOG_DIR/verifier.jsonl" || true
  answer="$(jq -c 'select(.type == "result") | .structured_output // empty' "$TMP/claude.jsonl" 2>/dev/null | tail -1)"
  if [ -z "$answer" ]; then  # fall back to the final text, without code fences, if it has the schema's shape
    answer="$(jq -r 'select(.type == "result") | .result // empty' "$TMP/claude.jsonl" 2>/dev/null \
      | sed -e '/^```/d' | jq -c "select($SHAPE)" 2>/dev/null | tail -1 || true)"
  fi
  model="$(jq -r 'select(.type == "system" and .subtype == "init") | .model // empty' "$TMP/claude.jsonl" 2>/dev/null | head -1)"
  if [ "$status" = COMPLETED ] && [ -z "$answer" ]; then status=NOT_RUN; reason="no structured_output in the result"; fi
  record claude claude "$out_file" "${model:-unknown}" "$(claude --version 2>/dev/null | head -1)" "$status" "$reason" "$started" "$answer"
}

# ---- Codex: read-only sandbox, so it inspects code instead of running the checks ----
review_codex() { # <pass number>
  local k="$1" label="codex" out_file="reviews/$id-codex$suffix.json" started status reason answer focus
  focus="Your sandbox is read-only, so commands that write (uv sync, pnpm install, test caches) fail. Do not run the checks; mark criteria that need execution NOT_RUN with that reason, and review the code, tests and configuration by reading them."
  if [ "$k" -gt 1 ]; then
    label="codex$k"; out_file="reviews/$id-codex-$k$suffix.json"
    focus="$focus This is a second, independent Codex pass on the numerical core (PRD 23.4): re-derive every formula, sign and handedness convention, unit, tolerance and validity rule from the PRD yourself, then compare with the code and tests. Concentrate on numerical correctness, edge cases, invalid-data behaviour and test strength."
  fi
  started="$(now)"
  if prompt "$focus" \
     | with_timeout "$REVIEW_TIMEOUT" codex exec -m "$CODEX_MODEL" -s read-only --ephemeral --json \
         --output-schema "$SCHEMA" -o "$TMP/$label.json" - > "$TMP/$label.jsonl" 2> "$TMP/$label.err"; then
    status=COMPLETED; reason=""
  else
    status=NOT_RUN; reason="codex exited $?: $(tail -c 600 "$TMP/$label.err" | tr '\n' ' ')"
  fi
  cp "$TMP/$label.jsonl" "$RAW_DIR/$label-$id-r$round-$STAMP.jsonl"
  answer="$(jq -c . "$TMP/$label.json" 2>/dev/null || true)"
  if [ "$status" = COMPLETED ] && [ -z "$answer" ]; then status=NOT_RUN; reason="codex wrote no parseable answer"; fi
  record "$label" codex "$out_file" "$CODEX_MODEL" "$(codex --version 2>/dev/null | head -1)" "$status" "$reason" "$started" "$answer"
}

review_claude &
k=1
while [ "$k" -le "$codex_passes" ]; do review_codex "$k" & k=$((k + 1)); done
wait

ok=0
for s in "$TMP"/*.status; do [ "$(cat "$s")" = ok ] || ok=1; done
[ "$(find "$TMP" -name '*.status' | wc -l | tr -d ' ')" -eq "$((codex_passes + 1))" ] || ok=1
exit "$ok"
