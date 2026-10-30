---
name: review-eval
description: Evaluate and improve the /review skill. Builds a corpus of verified incident-causing changes and known-clean PRs, replays /review on them to measure recall and harmful false positives, and reports noise and convergence from the /review ledger (how authors handled findings, cycles-to-pass, late findings). Run after changing the /review skill, or on a schedule.
argument-hint: "add <revert|fix commit or PR> | add-clean <PR or commit range> | mine | run [case-id...] | report"
disable-model-invocation: true
---

# Review eval

Incidents are the eval. Every change to the /review skill is scored against the whole collection of real bugs it should have caught, and against PRs known to be clean. The priorities, in order: catch real bugs; then reduce noise and review cycles. Better recall bought with more noise doesn't count as a win.

Arguments: `$ARGUMENTS`

Paths (per repo):
- `CASES=<repo root>/.claude/review-eval/cases/`: the corpus. Meant to be committed so the team shares it.
- `EVAL=$(git rev-parse --git-common-dir)/review-eval/`: local runs, matches, results and the ledger written by /review. Never committed.
- `SKILL_DIR`: this skill's base directory (shown when the skill loads). Works wherever the skills are installed: `~/.claude/skills/`, a repo's `.claude/skills/`, or a plugin.
- `SCORE=$SKILL_DIR/score.py`
- `REVIEW_SKILL=$SKILL_DIR/../review/SKILL.md` (absolute path; the /review skill sits next to this one)
- `SKILL_HASH`: first 8 hex chars of `shasum $REVIEW_SKILL`.

Never push, change branches in the user's working tree, or write to Linear.

## Case format

Incident case, `$CASES/inc-<nnn>.json`:
```json
{
  "id": "inc-001",
  "kind": "incident",
  "base": "<sha before the bad change>",
  "head": "<sha of the bad change>",
  "fix_ref": "<revert or fix-forward sha / PR>",
  "source": "<PR URL, Linear incident, Slack thread>",
  "bugs": [{"file": "src/x.ts", "lines": [40, 52], "class": "missing null check", "description": "what fails and when"}],
  "verified_by": "<person who knows this code>",
  "added": "YYYY-MM-DD"
}
```
Clean case, `$CASES/clean-<nnn>.json`: `{"id", "kind": "clean", "base", "head", "source", "notes", "verified_by", "added"}`. A clean case is a merged change that shipped with no revert, fix-forward or incident for a reasonable period.

## `add <revert|fix commit or PR>`

1. Resolve the fix. For a revert, `git show` the message ("This reverts commit X") to get the bad commit. For a fix-forward or PR (`gh pr view`), find the change that introduced the bug: `git log -L` / `git blame` on the lines the fix touches, then confirm by reading.
2. Set `head` = the bad commit (the merge or squash commit of the bad PR), `base` = its first parent.
3. Propose the bug locations from the fix diff, *at `head`* (line numbers in the bad version, not the fixed one), with class and description.
4. **Human verification is required.** Show the proposal and ask the user to confirm or correct each location, and who verified it. Don't write the case until they confirm. A case with an unverified location poisons the eval.
5. Write the next free `inc-<nnn>.json`.

## `add-clean <PR or commit range>`

Resolve `base`/`head`. Check `git log` after it for reverts or fixes touching the same lines, and Linear for related bug or incident tickets. If there are any, say so and ask before adding. Then write `clean-<nnn>.json`.

## `mine`

Propose candidates; don't write cases.
- `git log --grep='^Revert' --since=<6 months>` and commits whose message says fix/hotfix/regression and references an earlier commit or PR.
- Linear (read only): issues labeled bug/incident/regression with linked PRs or commits.
- For clean cases: merged PRs older than 30 days with no later revert or fix on their lines. Mix sizes.

Present a table (candidate, why, likely bug location) and offer to run `add` on the ones the user picks.

## `run [case-id...]`

1. Load all cases (or the given IDs). If there are none, say so and suggest `mine`.
2. For each case, create an isolated worktree at `head`: `git worktree add --detach $EVAL/wt/<id> <head>`.
3. Run /review in eval mode on each case, up to 4 cases at a time, each in its own Agent with fresh context:
   > Work only inside `$EVAL/wt/<id>`. Read `<absolute $REVIEW_SKILL>` and follow its **Eval mode** with base `<base>`, writing results to `$EVAL/runs/<id>.json`. Don't read anything under `.claude/review-eval/` or `$EVAL` other than your output path, and don't look at commits after `<head>`.

   Never pass the case's bug list, `fix_ref` or `source` to these agents. They must not know where the bug is.
4. Remove the worktrees (`git worktree remove --force $EVAL/wt/<id>`) once each run file exists.
5. `python3 $SCORE match --cases $CASES --runs $EVAL/runs --out $EVAL/matches.json`
6. **Judge.** Spawn one Agent that hasn't seen the runs, give it `$EVAL/matches.json`, and have it set every `verdict: null`:
   - incident entries: `caught` if some candidate describes *this* bug (same failure, not just the same file), else `missed`. A near-location finding about a different problem is still `missed`.
   - clean entries: `harmful` if acting on the finding would have made the code worse or broken it; `harmless` if it's a reasonable but unneeded note.

   Spot-check a few verdicts yourself before continuing.
7. `python3 $SCORE report --cases $CASES --runs $EVAL/runs --matches $EVAL/matches.json --ledger $EVAL/ledger.jsonl --results-dir $EVAL/results --skill-hash $SKILL_HASH`
8. Present the report (below).

## `report`

Ledger metrics only, between full runs: `python3 $SCORE report --cases $CASES --runs $EVAL/runs --ledger $EVAL/ledger.jsonl --results-dir $EVAL/ledger-reports --skill-hash $SKILL_HASH` (no `--matches`). Use the separate `ledger-reports` dir so full-run comparisons in `results/` stay clean. Present only the noise and convergence sections.

## Presenting results

Lead with the comparison to the previous run, if there is one: recall, harmful false-positive rate, and Important findings per clean PR, each as before → after, plus any newly missed incidents. Then:

- **Bug detection**: recall overall and by bug class; list missed incidents.
- **Noise**: harmful findings on clean PRs; from the ledger, how authors handled findings per label (fixed / rejected / intentional / didn't matter) and the accepted share.
- **Convergence**: median and p95 cycles-to-pass, branches that went past 3 cycles, and the share of cycle-2+ findings that were late (on code unchanged since the earlier pass).

Call it a **regression** if recall dropped, a previously caught incident is now missed, or harmful findings rose. Any of these outweighs gains elsewhere.

## Improving the reviewer

For each missed incident and each harmful or frequently rejected finding, ask what wasn't in the reviewer's context. Propose the smallest fix at the source: a line in the /review skill, a CLAUDE.md convention, an owner REVIEW.md guideline, a lint rule, or a new check. Show the proposed edits and apply them only after the user agrees. Then rerun `run` and compare. Rescore every time the /review skill, its tools or the model change.
