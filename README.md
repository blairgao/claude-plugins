# claude-plugins

Claude Code plugins by Blair Gao.

## review-tools

- **`review`**: pre-PR review of the current branch. Runs `/code-review` plus a fresh-context reviewer, `/security-review`, diff reduction, cleanup and end-to-end validation with proof, pulls intent from linked Linear tickets, and writes `REVIEW.md` with a 1–5 risk score and verdict.
  `review [base-branch] [--fix] [--effort low|medium|high|xhigh|max] [--full]`
- **`review-eval`**: scores the reviewer against verified incident-causing changes and known-clean PRs, and tracks noise and review-cycle convergence.
  `review-eval add <revert|fix> | add-clean <PR> | mine | run | report`

Based on how the Claude Code team does code review.

### Install

```
/plugin marketplace add blairgao/claude-plugins
/plugin install review-tools@blairgao
```

Plugin skills are namespaced, so run them as `/review-tools:review` and `/review-tools:review-eval`.

To have Claude Code offer the plugin automatically in a repo, add to that repo's `.claude/settings.json`:

```json
{
  "extraKnownMarketplaces": {
    "blairgao": { "source": { "source": "github", "repo": "blairgao/claude-plugins" } }
  },
  "enabledPlugins": { "review-tools@blairgao": true }
}
```

### Requirements

- `git`, `python3`; `gh` for PR lookups.
- The Linear connector in Claude Code for ticket context (optional; without it, intent comes from commits).

### Data

- Eval cases: `.claude/review-eval/cases/` in each repo. Commit these so the team shares the corpus.
- Run logs, results and the review ledger: `$(git rev-parse --git-common-dir)/review-eval/`. Local only.

### Updating

Bump `version` in `review-tools/.claude-plugin/plugin.json`, push, then teammates run `/plugin marketplace update blairgao`.
