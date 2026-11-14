#!/usr/bin/env bash
# shellcheck disable=SC2016  # jq programs are single-quoted on purpose
# Terraluna Ralph loop, adapted from snarktank/ralph (MIT, see LICENSE.ralph; upstream commit 6c53cb0).
#
# Each story goes through: author iteration (fresh `claude -p`) -> two independent read-only
# reviews (review.sh) -> author resolves every finding -> deterministic gate run by this script.
# Only the gate sets passes=true. Completion is read from prd.json, never from model output.
# Routine problems go back to the author with a note in the story; integrity problems stop the loop.
# The loop commits its own bookkeeping (reviews, gate results, compact logs) with Conventional Commits.
#
# Usage:  [RALPH_BATCH=<name>] scripts/ralph/ralph.sh [max_author_iterations]
#   RALPH_BATCH selects scripts/ralph/batches/<name>/{prd.json,progress.txt}; parallel batches run in their own
#   git worktrees and branches (see new-batch.sh). Without it the loop uses scripts/ralph/prd.json (the T0 batch).
# Env (generous defaults):
#   RALPH_MAX_ITERATIONS=200   author iterations per run (the argument overrides it)
#   RALPH_MAX_REVIEW_ROUNDS=4  review rounds per story before a human looks
#   RALPH_MAX_BOUNCES=8        returns to the author per story for routine problems
#   RALPH_MAX_IDLE=10          consecutive author iterations with no commit and no state change
#   RALPH_REVIEW_ATTEMPTS=3    attempts per review round before a human looks
#   RALPH_PERMISSION_MODE=auto auto (classifier-reviewed, prompts denied) or bypass
#   REVIEW_TIMEOUT is passed to review.sh (Codex always runs gpt-6.1-sol)
# Exit:   0 every story passed the gate · 1 iteration limit reached · 2 a human is needed
# Works with macOS /bin/bash 3.2.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(git -C "$DIR" rev-parse --show-toplevel)"
MAX="${1:-${RALPH_MAX_ITERATIONS:-200}}"
MAX_ROUNDS="${RALPH_MAX_REVIEW_ROUNDS:-4}"
MAX_BOUNCES="${RALPH_MAX_BOUNCES:-8}"
MAX_IDLE="${RALPH_MAX_IDLE:-10}"
REVIEW_ATTEMPTS="${RALPH_REVIEW_ATTEMPTS:-3}"
cd "$ROOT"
BATCH="${RALPH_BATCH:-}"
if [ -n "$BATCH" ]; then STATE_DIR="$DIR/batches/$BATCH"; else STATE_DIR="$DIR"; fi
PRD="$STATE_DIR/prd.json"; PROGRESS="$STATE_DIR/progress.txt"
PRD_REL="${PRD#"$ROOT"/}"; PROGRESS_REL="${PROGRESS#"$ROOT"/}"
[ -f "$PRD" ] || { echo "ralph: no story list at $PRD_REL" >&2; exit 2; }

# Each iteration is a clean top-level session, even when this loop is started from inside Claude Code.
unset CLAUDECODE CLAUDE_CODE_ENTRYPOINT CLAUDE_CODE_MESSAGING_SOCKET CLAUDE_CODE_MESSAGING_TOKEN \
  CLAUDE_CODE_SESSION_ID CLAUDE_CODE_CHILD_SESSION CLAUDE_CODE_SESSION_ATTENDED CLAUDE_PID \
  CLAUDE_EFFORT CLAUDE_CODE_EXECPATH 2>/dev/null || true
# Long installs and builds must not be cut off by the Bash tool's default timeout.
export BASH_DEFAULT_TIMEOUT_MS="${BASH_DEFAULT_TIMEOUT_MS:-900000}" BASH_MAX_TIMEOUT_MS="${BASH_MAX_TIMEOUT_MS:-3600000}"

# Paths that hold loop state rather than story work.
STATE_SPEC=(":!$PRD_REL" ":!$PROGRESS_REL" ':!reviews' ':!logs/agents')
WORK_SPEC=(':!scripts/ralph' ':!reviews' ':!logs')
TRAILER="Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"

# Guard rails for author iterations. Deny rules stop the usual invocation forms; they are not a
# security boundary, so the prompt repeats them and this script checks the outcome after each run.
# Subagents (the Agent tool) are allowed: authors may fan out bounded, file-owned subtasks.
DENY=(Workflow
  "Bash(git push *)" "Bash(git merge *)" "Bash(git rebase *)" "Bash(git reset --hard *)"
  "Bash(git switch *)" "Bash(git checkout *)" "Bash(git add -A*)" "Bash(git add .)" "Bash(git commit -a*)"
  "Bash(git commit --amend*)" "Bash(git commit --no-verify*)" "Bash(git commit -n *)" "Bash(git config *)"
  "Bash(gh *)" "Bash(vercel *)" "Bash(codex *)" "Bash(claude *)" "Bash(make publish*)" "Bash(hf *)"
  "Read(./.env)" "Edit(./PRD.md)" "Edit(./CLAUDE.md)" "Edit(./AGENTS.md)" "Edit(./.claude/**)"
  "Edit(./reference-library/**)" "Edit(./reviews/schema.json)" "Edit(./commitlint.config.mjs)" "Edit(./.husky/**)"
  "Edit(./scripts/ralph/ralph.sh)" "Edit(./scripts/ralph/review.sh)" "Edit(./scripts/ralph/prompt.md)")

case "${RALPH_PERMISSION_MODE:-auto}" in
  auto)   PERM=(--permission-mode auto --permission-prompts none) ;;
  bypass) PERM=(--dangerously-skip-permissions --permission-prompts none) ;;
  *) echo "ralph: RALPH_PERMISSION_MODE must be auto or bypass" >&2; exit 2 ;;
esac

# Compact terminal view of the stream-json transcript.
VIEW='if .type=="assistant" and .parent_tool_use_id==null then (.message.content[]? |
  if .type=="text" then .text+"\n"
  elif .type=="tool_use" then "  > \(.name) \((.input.command // .input.file_path // .input.pattern // "") | tostring | .[0:160])\n"
  else empty end)
elif .type=="result" then "=== \(.subtype // "?") · turns \(.num_turns // "?") · $\(.total_cost_usd // "?") · denied \((.permission_denials // []) | length)\n"
else empty end'
# Committed run record (PRD 23.7): commands and outcomes only. Full transcripts stay in raw/ (git-ignored).
COMPACT='fromjson? | if .type=="assistant" then (.message.content[]? | select(.type=="tool_use")
    | {ts: $ts, agent: $agent, story: $story, event: "tool_use", tool: .name,
       input: ((.input.command // .input.file_path // .input.pattern // .input.url // "") | tostring | .[0:400])})
  elif .type=="user" then (.message.content[]? | select(type=="object" and .type=="tool_result")
    | {ts: $ts, agent: $agent, story: $story, event: "tool_result", is_error: (.is_error // false)})
  elif .type=="result" then {ts: $ts, agent: $agent, story: $story, event: "result", subtype, num_turns,
    total_cost_usd, permission_denials: ((.permission_denials // []) | length)}
  else empty end'

now()   { date -u +%Y-%m-%dT%H:%M:%SZ; }
human() { echo >&2; echo "ralph${BATCH:+[$BATCH]}: HUMAN NEEDED: $*" >&2; exit 2; }
sha()   { shasum -a 256 | cut -d' ' -f1; }
field() { jq -r --arg id "$1" ".userStories[] | select(.id==\$id) | $2" "$PRD"; }
edit()  { local tmp; tmp="$(mktemp)"; jq "$@" "$PRD" > "$tmp" && mv "$tmp" "$PRD"; }
# Update one story's review object: setreview <id> <jq expression on .review> [jq args...]
setreview() { local id="$1" expr="$2"; shift 2; edit --arg id "$id" "$@" "(.userStories[] | select(.id==\$id) | .review) |= ($expr)"; }
contract()  { jq -S '[.userStories[] | {id, priority, owner, prdRefs, files, acceptanceCriteria, checks}]' "$PRD" | sha; }
dirty()     { git status --porcelain -- . "${STATE_SPEC[@]}"; }
lc()        { tr '[:upper:]' '[:lower:]' <<<"$1"; }
logdir()    { local d; d="$ROOT/logs/agents/$(date -u +%F)${BATCH:+/$BATCH}"; mkdir -p "$d/raw"; echo "$d"; }

commit_state() { # <message> <path>... : commit loop bookkeeping with explicit paths (hook checks the message)
  local msg="$1" p; shift
  for p in "$@"; do [ -e "$p" ] && git add -- "$p"; done
  git diff --cached --quiet && return 0
  git commit -q -m "$msg" -m "$TRAILER" || human "commit of loop state failed: $msg"
}
state_paths() { echo "$PRD" "$ROOT/reviews" "$ROOT/logs/agents"; }

note() { # <id> <text>: append a dated line to the story's notes (the author reads them first)
  edit --arg id "$1" --arg t "ralph $(now): $2" '(.userStories[] | select(.id==$id) | .notes) |= (if . == "" then $t else . + "\n" + $t end)'
}
bounce() { # <id> <state> <reason>: return the story to the author with a note
  local id="$1" to="$2" reason="$3" b
  b="$(( $(field "$id" '.review.bounces // 0') + 1 ))"
  [ "$b" -le "$MAX_BOUNCES" ] || human "$id was returned to the author $MAX_BOUNCES times; last reason: $reason"
  note "$id" "$reason"
  setreview "$id" '.state=$s | .bounces=$b' --arg s "$to" --argjson b "$b"
  echo "ralph: returning $id to the author ($to): $reason"
  # shellcheck disable=SC2046
  commit_state "chore(ralph): return $(lc "$id") to the author" $(state_paths)
}
commits_ok() { # <base>: every commit since base follows Conventional Commits
  pnpm exec commitlint --from "$1" --to HEAD >/dev/null 2>&1 \
    || { pnpm exec commitlint --from "$1" --to HEAD >&2 || true; human "commits since $1 fail commitlint"; }
}

# ---------- preflight ----------
for t in git jq claude codex uv pnpm node perl shasum; do
  command -v "$t" >/dev/null || human "missing tool: $t"
done
BRANCH="$(jq -r .branchName "$PRD")"
[ "$(git branch --show-current)" = "$BRANCH" ] || human "check out $BRANCH first: git switch -c $BRANCH"
[ -e .husky/_/commit-msg ] || pnpm install --frozen-lockfile >/dev/null 2>&1 || true
{ [ "$(git config core.hooksPath || true)" = ".husky/_" ] && [ -e .husky/_/commit-msg ]; } \
  || human "commit hooks are not installed; run pnpm install"
if [ -n "$(dirty)" ]; then dirty >&2; human "the working tree has changes outside Ralph's state; commit or restore them first"; fi
CONTRACT="$(contract)"

# ---------- steps ----------
author() { # <id> <n>
  local id="$1" n="$2" owner d stamp
  owner="$(field "$id" .owner)"; d="$(logdir)"; stamp="$(date -u +%H%M%S)"
  echo; echo "=== ${BATCH:-t0} author iteration $n/$MAX · $id · $(field "$id" .review.state) · as $owner · $(now) ==="
  { printf 'Batch: %s\nStory list: %s\nProgress log: %s\n\n' "${BATCH:-t0-bootstrap}" "$PRD_REL" "$PROGRESS_REL"
    cat "$DIR/prompt.md"; } \
    | claude -p "${PERM[@]}" --disallowedTools "${DENY[@]}" --output-format stream-json --verbose | tee "$d/raw/ralph-$owner-$stamp.jsonl" | jq -rj --unbuffered "$VIEW" || true
  jq -R -c --arg ts "$(now)" --arg agent "ralph-$owner" --arg story "$id" "$COMPACT" \
    "$d/raw/ralph-$owner-$stamp.jsonl" >> "$d/ralph-$owner.jsonl" || true
}

# Paths changed by the story's own commits since <base>: first-parent, non-merge commits only, so an
# orchestrator merge of another branch (for example T0 into a batch) never counts against the story.
story_paths() { git log --first-parent --no-merges --name-only --format= "$1..HEAD" -- . "${WORK_SPEC[@]}" | sed '/^$/d' | sort -u; }

outside_paths() { # <id> <base>: changed paths outside the story's files patterns
  local id="$1" base="$2" p g ok
  while IFS= read -r p; do
    ok=0
    while IFS= read -r g; do
      # shellcheck disable=SC2053  # $g is a glob pattern on purpose
      if [[ "$p" == $g ]]; then ok=1; break; fi
    done < <(field "$id" '.files[]')
    [ "$ok" = 1 ] || printf ' %s' "$p"
  done < <(story_paths "$base")
}

review() { # <id>
  local id="$1" base round head outside attempt=1 list f files digests
  base="$(field "$id" .review.base)"; round="$(( $(field "$id" .review.round) + 1 ))"; head="$(git rev-parse HEAD)"
  if [ -n "$(dirty)" ]; then bounce "$id" pending "uncommitted changes were left; commit them or restore them"; return; fi
  if [ -z "$(story_paths "$base")" ]; then bounce "$id" pending "review was requested but nothing is committed since $base"; return; fi
  outside="$(outside_paths "$id" "$base")"
  if [ -n "$outside" ]; then
    bounce "$id" pending "paths outside the story's files list changed:$outside. Restore them to their content at $base in a new commit (git restore --source $base -- <path>, or git rm for new files)"
    return
  fi
  commits_ok "$base"
  echo; echo "=== review round $round · $id · $base..$head · $(now) ==="
  list="$(mktemp)"
  until RALPH_PRD="$PRD" RALPH_LOG_DIR="$(logdir)" RALPH_REVIEW_LIST="$list" "$DIR/review.sh" "$id" "$round" "$base" "$head"; do
    [ "$attempt" -lt "$REVIEW_ATTEMPTS" ] || human "$id: review round $round failed $attempt times; see reviews/$id-*.json"
    attempt=$((attempt + 1)); : > "$list"; echo "ralph: review attempt $attempt for $id in 120 s"; sleep 120
  done
  files="[]"; digests="{}"
  while IFS= read -r f; do
    files="$(jq -c --arg f "$f" '. + [$f]' <<<"$files")"
    digests="$(jq -c --arg f "$f" --arg d "$(jq -S 'del(.findings[].resolution)' "$f" | sha)" '. + {($f): $d}' <<<"$digests")"
  done < "$list"
  [ "$(jq length <<<"$files")" -ge 2 ] || human "$id: review round $round produced fewer than two review files"
  setreview "$id" '.state="reviewed" | .round=$r | .head=$h | .files=$files | .digests=$digests' \
    --argjson r "$round" --arg h "$head" --argjson files "$files" --argjson digests "$digests"
  # shellcheck disable=SC2046
  commit_state "chore(ralph): record round $round reviews for $(lc "$id")" $(state_paths)
}

rereview() { # <id> <reason>: ask for another review round, within the round limit
  local id="$1" round
  round="$(field "$id" .review.round)"
  [ "$round" -lt "$MAX_ROUNDS" ] || human "$id: $2 after $round review rounds"
  echo "ralph: $id: $2; requesting review round $((round + 1))"
  setreview "$id" '.state="requested"'
  # shellcheck disable=SC2046
  commit_state "chore(ralph): request review round $((round + 1)) for $(lc "$id")" $(state_paths)
}

gate_failure() { # <id> <command> <output-file>: record a failing check as a blocker for the author
  local id="$1" round k f
  round="$(field "$id" .review.round)"; k=1
  while [ -e "reviews/$id-gate-r$round-$k.json" ]; do k=$((k + 1)); done
  f="reviews/$id-gate-r$round-$k.json"
  jq -n --arg task "$id" --argjson round "$round" --arg cmd "$2" --arg out "$(tail -n 80 "$3")" \
    --arg ts "$(now)" --arg head "$(git rev-parse HEAD)" --arg fid "gate-r$round-$k" '
    {task_id: $task, round: $round, reviewer: {kind: "gate", model: null, tool_version: "scripts/ralph/ralph.sh"},
     inputs: {base: null, head: $head, files: []}, started_utc: $ts, finished_utc: $ts,
     status: "COMPLETED", not_run_reason: null, verdict: "FAIL", summary: "A story check failed at the gate.",
     checks: [{criterion: "story check", command: $cmd, outcome: "FAIL", evidence: $out}],
     findings: [{id: $fid, severity: "blocker", location: $cmd, claim: "This check exits non-zero at the gate.",
                 evidence: $out, resolution: null}]}' > "$f"
  setreview "$id" '.state="reviewed" | .files += [$f] | .digests += {($f): $d}' \
    --arg f "$f" --arg d "$(jq -S 'del(.findings[].resolution)' "$f" | sha)"
  echo "ralph: $id gate check failed: $2 (recorded in $f)"
  # shellcheck disable=SC2046
  commit_state "chore(ralph): record failing gate check for $(lc "$id")" $(state_paths)
}

gate() { # <id>
  local id="$1" round f c serious outside out
  round="$(field "$id" .review.round)"
  if [ -n "$(dirty)" ]; then bounce "$id" reviewed "uncommitted changes were left at the gate; commit them or restore them"; return; fi
  outside="$(outside_paths "$id" "$(field "$id" .review.base)")"
  if [ -n "$outside" ]; then bounce "$id" reviewed "paths outside the story's files list changed:$outside"; return; fi
  commits_ok "$(field "$id" .review.base)"
  for f in $(field "$id" '.review.files[]'); do
    [ -f "$f" ] || human "$f is missing"
    [ "$(jq -S 'del(.findings[].resolution)' "$f" | sha)" = "$(field "$id" ".review.digests[\"$f\"]")" ] \
      || human "$f: reviewer content changed after the review"
    if jq -e 'any(.findings[]; .resolution.action == "escalated")' "$f" >/dev/null; then
      human "$id: a finding in $f is escalated: $(jq -r '[.findings[] | select(.resolution.action == "escalated") | "\(.id): \(.resolution.reason)"] | join("; ")' "$f")"
    fi
    if ! jq -e 'all(.findings[]; .resolution != null and (.resolution.action | IN("fixed", "rebutted")))' "$f" >/dev/null; then
      bounce "$id" reviewed "$f has findings without a resolution"; return
    fi
    for c in $(jq -r '.findings[] | select(.resolution.action == "fixed") | .resolution.commit' "$f"); do
      if ! git merge-base --is-ancestor "$c" HEAD 2>/dev/null; then
        bounce "$id" reviewed "$f names fix commit '$c', which is not on this branch"; return
      fi
    done
  done
  for f in $(field "$id" '.review.files[]'); do
    if jq -e '.verdict == "FAIL" and (.findings | length) == 0' "$f" >/dev/null; then rereview "$id" "$f has verdict FAIL without findings"; return; fi
    if jq -e '.reviewer.kind == "claude" and .verdict == "NOT_RUN"' "$f" >/dev/null; then rereview "$id" "the verifier could not run the checks"; return; fi
  done
  # shellcheck disable=SC2046  # review file paths contain no spaces
  serious="$(jq -s '[.[].findings[] | select(.severity == "blocker" or .severity == "high")] | length' $(field "$id" '.review.files[]'))"
  if [ "$serious" -gt 0 ]; then rereview "$id" "$serious blocker/high findings were resolved"; return; fi
  echo; echo "=== gate · $id · $(now) ==="
  out="$(mktemp)"
  while IFS= read -r c; do
    echo "gate> $c"
    if ! bash -c "$c" > "$out" 2>&1; then tail -n 40 "$out"; gate_failure "$id" "$c" "$out"; return; fi
  done < <(field "$id" '.checks[]')
  # Checks must be repeatable: a check that rewrites tracked files or leaves new ones breaks the next gate.
  if [ -n "$(dirty)" ]; then
    { echo "The story's checks left these changes in the working tree:"; dirty; } > "$out"
    cat "$out"; gate_failure "$id" "story checks must leave the working tree unchanged" "$out"; return
  fi
  edit --arg id "$id" '(.userStories[] | select(.id==$id)) |= (.passes = true | .review.state = "passed")'
  echo "ralph: $id passed review and the gate"
  # shellcheck disable=SC2046
  commit_state "chore(ralph): mark $(lc "$id") as passed" $(state_paths)
}

# ---------- loop ----------
n=0; idle=0
while :; do
  [ "$(git branch --show-current)" = "$BRANCH" ] || human "the branch changed away from $BRANCH"
  [ "$(contract)" = "$CONTRACT" ] || human "a story's id, priority, files, criteria or checks changed during the run"
  jq -e 'all(.userStories[]; (.passes == true) == (.review.state == "passed"))' "$PRD" >/dev/null \
    || human "a story's passes flag was set outside the gate"
  id="$(jq -r '[.userStories[] | select(.passes != true)] | sort_by(.priority) | .[0].id // empty' "$PRD")"
  if [ -z "$id" ]; then
    # shellcheck disable=SC2046
    commit_state "chore(ralph): record final loop state" $(state_paths)
    echo; echo "ralph: every story passed review and the gate ($(now))"; exit 0
  fi
  state="$(field "$id" .review.state)"
  case "$state" in
    pending|reviewed)
      # waitsFor ["<batch>:<STORY>", ...]: every listed story must have passed and be merged into this branch.
      missing=""
      while IFS= read -r w; do
        [ -n "$w" ] || continue
        git log -1 --format=%H --grep="^chore(ralph): mark $(lc "${w#*:}") as passed$" HEAD | grep -q . || missing="$missing $w"
      done < <(field "$id" '(.waitsFor // [])[]')
      if [ -n "$missing" ]; then
        note "$id" "waiting for these stories to pass and be merged into $BRANCH:$missing"
        setreview "$id" '.state="blocked"'
        # shellcheck disable=SC2046
        commit_state "chore(ralph): record that $(lc "$id") waits for merges" $(state_paths)
        human "$id waits for merges:$missing"
      fi
      if [ "$n" -ge "$MAX" ]; then echo "ralph: reached $MAX author iterations; state is in $PRD" >&2; exit 1; fi
      n=$((n + 1))
      if [ "$state" = pending ] && [ "$(field "$id" .review.base)" = null ]; then
        setreview "$id" '.base=$b' --arg b "$(git rev-parse HEAD)"
      fi
      before="$(git rev-parse HEAD)"
      author "$id" "$n"
      # Progress means a new review state, or an author commit that changed work (not only loop state).
      if [ "$state" = "$(field "$id" .review.state)" ] \
         && [ -z "$(git log --no-merges --format=%H "$before..HEAD" -- . "${WORK_SPEC[@]}")" ]; then
        idle=$((idle + 1))
        [ "$idle" -le "$MAX_IDLE" ] || human "$MAX_IDLE author iterations in a row changed no work and no state"
        echo "ralph: no progress in this iteration ($idle in a row); waiting $((idle * 60)) s"; sleep $((idle * 60))
      else
        idle=0; sleep 2
      fi ;;
    blocked)
      # shellcheck disable=SC2046
      commit_state "chore(ralph): record that $(lc "$id") is blocked" $(state_paths)
      human "$id is blocked: $(field "$id" '.notes' | tail -n 3)" ;;
    requested) review "$id" ;;
    resolved)  gate "$id" ;;
    *) human "$id has review.state '$state'" ;;
  esac
done
