---
name: multi-model
description: Entry point for the multi-model workflow (Claude produces, Grok adversarially reviews, TypeSafe routes and gates). Use when the user asks to run "多模型工作流" / "multi-model" on a task, or for any request that should be routed between leetcode-explain, paper-review and general. Also read when writing or debugging those skills.
user-invocable: true
allowed-tools: Bash, Read, Write, Edit
---

# multi-model

Roles (never swap them):
- **Claude** — orchestrates and produces (code, prose, plans). Runs every script below via Bash.
- **Grok 4.6** (`scripts/grok_review.sh`) — adversarial reviewer with a different training lineage; second research source with its own web search. Assumes the work is wrong until proven otherwise.
- **TypeSafe Jev** (`scripts/judge.py`) — typed judgments only: routes requests, filters Grok's false positives, and decides pass/fail with thresholds set in code.
- **Tavily** — not configured yet; when it is, it becomes Claude-side retrieval in `general` and `paper-review`.

## Routing
```bash
python3 ~/.claude/skills/multi-model/scripts/judge.py --preset route --state '{"request": "<user request verbatim>"}'
```
`decision.workflow` → `leetcode` → skill `leetcode-explain`; `paper` → `paper-review`; `general` → `general`; `ask_user` → ask one clarifying question with the two most likely workflows.
`clarify_first == true` → ask once before starting. Carry `needs_web` and `needs_code_run` into the chosen skill.

## Skills
- `skills/leetcode-explain/SKILL.md`
- `skills/general/SKILL.md`
- `skills/paper-review/SKILL.md`

## Shared contract
Every skill ends with an audit line: `审查：Grok 提出 N 条 → TypeSafe 保留 K 条 → 门禁 通过/未通过 → 成本 $X`. Artifacts live under `~/.multi-model/runs/<workflow>/<slug>/`. Thresholds are in `judge.py THRESHOLDS`; change them there, not in prompts.
