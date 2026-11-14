#!/usr/bin/env bash
# Create a git worktree and branch ralph/<batch> for a parallel Ralph batch, then launch-ready setup:
# merge the loop tooling (ralph/tooling) if it is missing, add scripts/ralph/batches/<batch>/prd.json from a
# draft, seed its progress log with the Codebase Patterns learned so far, install the commit hooks, and commit.
#
# Usage: scripts/ralph/new-batch.sh <batch> <base-ref> <draft-prd.json>
# Then:  cd <printed worktree> && RALPH_BATCH=<batch> scripts/ralph/ralph.sh
set -euo pipefail

batch="$1"; base="$2"; draft="$3"
case "$batch" in *[!a-z0-9-]*|"") echo "new-batch: batch names are lower-case letters, digits and dashes" >&2; exit 2 ;; esac
MAIN="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")"
WT="${RALPH_WORKTREES:-$MAIN-worktrees}/$batch"
TRAILER="Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
jq -e --arg b "ralph/$batch" '.branchName == $b and (.userStories | length) > 0' "$draft" >/dev/null \
  || { echo "new-batch: $draft is not a story list for ralph/$batch" >&2; exit 2; }

git -C "$MAIN" worktree add -q -b "ralph/$batch" "$WT" "$base"
cd "$WT"
if ! git merge-base --is-ancestor ralph/tooling HEAD; then
  git merge -q --no-edit -m "Merge branch 'ralph/tooling' into ralph/$batch" ralph/tooling
fi
pnpm install --frozen-lockfile >/dev/null

dest="scripts/ralph/batches/$batch"
mkdir -p "$dest"
jq '.' "$draft" > "$dest/prd.json"
{
  echo "# Ralph progress: $batch"
  echo
  echo "Append-only. Each iteration adds one entry at the end. Keep general, reusable facts in Codebase Patterns."
  echo
  # Carry over what earlier iterations learned (the T0 log at the base commit), if any.
  if git cat-file -e "$base:scripts/ralph/progress.txt" 2>/dev/null; then
    git show "$base:scripts/ralph/progress.txt" | awk '/^## Codebase Patterns/ {p=1} p && /^---$/ {exit} p'
  else
    echo "## Codebase Patterns"
  fi
  echo "---"
} > "$dest/progress.txt"
git add -- "$dest/prd.json" "$dest/progress.txt"
git commit -q -m "chore(ralph): add $batch batch" \
  -m "$(jq -r '"\(.userStories | length) stories: " + ([.userStories[].id] | join(", ")) + ". " + .description' "$dest/prd.json")" \
  -m "$TRAILER"
echo "$WT"
