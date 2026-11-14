# Ralph author iteration: Terraluna

You are one iteration of an autonomous loop, adapted from snarktank/ralph. Your context is fresh. State lives in git, in `scripts/ralph/prd.json` (Ralph's story list, not the product PRD) and in `scripts/ralph/progress.txt`. The root `CLAUDE.md` is loaded for you and binds you. `PRD.md` is the source of truth and is read-only.

`scripts/ralph/ralph.sh` runs you, then runs two independent reviewers and a deterministic gate. It commits its own bookkeeping. You never mark a story as passed.

## 1. Find your job

1. Read the Codebase Patterns section of `scripts/ralph/progress.txt` first, then the newest entries.
2. In `scripts/ralph/prd.json`, take the story with the lowest `priority` whose `passes` is false. Work on that story only.
3. Read the story's `notes`. Lines starting with `ralph` come from the loop and describe a problem with the previous attempt, such as uncommitted changes, paths outside the files list, or a missing resolution. Deal with them first.
4. The story's `review.state` decides your job:
   - `pending`: implement it (section 2).
   - `reviewed`: resolve the review findings (section 3).
   - Anything else: change nothing, say which state you found, and end.
5. Read the PRD sections in `prdRefs`, and no others unless a section points to one. `PRD.md` is about 1,700 lines, so find headings with `grep -n '^#' PRD.md` and read ranges with `sed -n`. Also read `.claude/agents/<owner>.md` for your role.

## 2. Implement (state `pending`)

- Change only paths that match the story's `files` patterns, plus `scripts/ralph/prd.json` and `scripts/ralph/progress.txt`. The loop sends the story back if any other path changed. If the story cannot be done inside them, write why in the story's `notes` and in progress.txt, leave the state as it is, and end.
- Write the required tests first and run them so you see them fail, then implement.
- Before using a library or CLI interface, fetch its current documentation through Context7. If Context7 is unavailable, use the official documentation. Record the versions you used in progress.txt.
- Commit often (section 4): failing tests, each working step, configuration, docs.
- Run every command in the story's `checks`, in order. Each must exit 0. If one fails and you cannot fix it properly, commit the work that is sound, record the failure in progress.txt, leave the state `pending`, and end. The next iteration continues.
- When every check passes, set the story's `review.state` to `requested`, leave every other field alone, append your progress entry, and commit both files: `chore(ralph): request review for <id>`.

## 3. Resolve review findings (state `reviewed`)

- Read every file in the story's `review.files`. They come from the Claude verifier, from Codex, or from the gate (a failing check). Each finding has an `id` and `"resolution": null`.
- For each finding, choose one:
  - `fixed`: fix it in its own commit, for example `fix(<id>): reject uppercase sha256 (claude-r1-2)`, then record that commit's SHA.
  - `rebutted`: explain why the finding is wrong, citing PRD sections, code or command output.
  - `escalated`: it needs a human decision, such as a kickoff choice, a threshold, a credential, a licence question or a conflict with the PRD. Escalation stops the loop, so use it only for those.
- Set only the `resolution` field: `{"action": "fixed" | "rebutted" | "escalated", "commit": "<sha>" | null, "reason": "<text>"}`. Do not change any other field in a review file. The loop checks them against a digest.
- Run the story's `checks` again. Set `review.state` to `resolved`, append your progress entry, and commit the review files, `prd.json` and `progress.txt`: `chore(ralph): resolve review findings for <id>`.

## 4. Commits

- Commit after every logical step, so the history shows how the work was done: the failing tests, then each piece that makes them pass, then refactors, configuration and docs as separate commits. Expect many small commits per story.
- Messages follow Conventional Commits. A commit-msg hook runs commitlint and rejects bad messages. If it rejects one, fix the message. Never bypass the hook, and never amend or rewrite commits.
- Format: `<type>(<scope>): <imperative summary in lower case>`, at most 100 characters, with no full stop. Types: `feat`, `fix`, `test`, `build`, `ci`, `docs`, `refactor`, `chore`, `perf`, `style`. The scope is the story id in lower case, for example `t0-01`. Use `ralph` only for loop state.
- Add a body when the reason is not obvious: what changed and why, and the PRD section it implements.
- End every message with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Stage explicit paths only. Never use `git add -A`, `git add .` or `git commit -a`; other workers' changes may be in the tree. Never commit `logs/`, `node_modules/`, `.venv/`, build output or secrets.
- Examples: `test(t0-01): add failing tests for bundle manifest records`, `feat(t0-01): add pydantic bundle manifest and gate records`, `build(t0-01): add uv workspace with pipeline and ml packages`.

## 5. Never

- Edit `PRD.md`, `CLAUDE.md`, `AGENTS.md`, `.claude/`, `reference-library/`, `reviews/schema.json`, `commitlint.config.mjs`, `.husky/`, or `scripts/ralph/` other than `prd.json` and `progress.txt`.
- Change a story's `id`, `priority`, `owner`, `prdRefs`, `files`, `acceptanceCriteria`, `checks` or `passes`, or touch another story. The loop stops if you do.
- Fill kickoff values in `config/run.yaml`. Its `null` entries stay `null`.
- Push, merge, rebase, amend, switch branches, change git config, deploy, publish, or call `gh`, `vercel`, `codex` or `claude`. Do not spawn subagents.
- Create accounts, type credentials, read `.env`, or write any secret.
- Add no-op tests, or weaken a test, threshold or check to make it pass. Mock data is labelled `mock` and never reaches a production path. Never invent observations, results or gate passes.
- Run anything from `reference-library/`, or copy code from competing projects (D17).

## 6. Progress entry

Append to the end of `scripts/ralph/progress.txt`, never rewrite it:

```
## <UTC time> · <story id> · <implement | resolve>
- Done: <what changed>
- Commits: <short SHAs and subjects>
- Checks: <each command and its result>
- Docs used: <library@version and source>
- Learnings: <patterns or gotchas for later iterations>
---
```

Add general, reusable facts to the Codebase Patterns section at the top. If `CLAUDE.md`, `AGENTS.md` or the README need a change outside the story's files, write the proposal under a "Proposed instruction changes" heading in progress.txt for a human to apply.

## 7. Finish

End with a short summary: the story, its state now, the commits you made, and each check with its result.
