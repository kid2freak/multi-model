---
name: general
description: Multi-model general pipeline for anything that is not a LeetCode problem or an academic paper — small tools/scripts/plugins, prose editing, reports, plans, technical Q&A ("帮我写个脚本", "润色这段", "做一个小插件", "给我一个方案"). Claude produces, Grok adversarially reviews against the task, TypeSafe filters false alarms and gates delivery. Parent pipeline that leetcode-explain and paper-review specialise.
user-invocable: true
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# general

Pipeline: **clarify → (research) → produce → Grok review → TypeSafe filter → revise → TypeSafe gate → deliver**.
Scripts: `~/.claude/skills/multi-model/scripts/` (`judge.py`, `grok_review.sh`).
Workdir: `~/.multi-model/runs/general/<slug>/` (`task.md`, `artifact.*`, `findings.json`, `filtered.json`, `gate.json`). When the deliverable is a file in the user's project, the project file is the artifact; keep only the review JSON in the workdir.

## 1. Clarify (TypeSafe + Claude)
Use the router output (`clarify_first`, `has_acceptance`, `needs_web`, `needs_code_run`). If the router was not run, run it now:
`python3 scripts/judge.py --preset route --state '{"request": "<verbatim>"}'`.
- `clarify_first` → ask **one** question covering every missing detail, then proceed.
- `has_acceptance == false` → write the acceptance criteria yourself in `task.md` (3–6 checkable bullets: expected output, inputs handled, constraints, format, audience) and state them to the user in one line before producing. They are what Grok and the gate judge against.

## 2. Research (only if `needs_web`)
Two independent sources, run both:
- Claude: your own WebSearch/WebFetch (Tavily once configured).
- Grok: `scripts/grok_review.sh --role researcher --context "<question>" > research_grok.json` (≈ $0.4, 2–3 min; one call per task).
Use only facts both sources agree on, or that come with a primary-source URL and date. Note disagreements in `task.md`.

## 3. Produce (Claude)
Do the work exactly as scoped in `task.md`. `needs_code_run` → run it and save the output next to the artifact (`run_output.txt`). Do not add features that are not in the acceptance criteria.

## 4. Review (Grok)
```bash
scripts/grok_review.sh --role reviewer --context @task.md --file <artifact> [--file run_output.txt] > findings.json
```
Multiple files → several `--file` flags. Very large artifacts (> ~2000 lines): pass only the changed files plus `task.md`.

## 5. Filter (TypeSafe)
`state_findings.json` = `{"purpose": "<one-line task>", "artifact": "<artifact text or diff>", "findings": <findings.json .findings>}`
`python3 scripts/judge.py --preset findings --state @state_findings.json > filtered.json`
Work from `decision.kept` only.

## 6. Revise (Claude)
Fix each kept finding. If you disagree after checking, leave it in `open_findings` for the gate. Re-run code if applicable.

## 7. Gate (TypeSafe)
`state_gate.json` = `{"task": <task.md>, "acceptance": [...], "artifact": "<final text>", "open_findings": [...]}`
`python3 scripts/judge.py --preset gate-general --state @state_gate.json > gate.json`
- `pass` → deliver.
- else fix `failed_checks` / `low_scores`, go back to step 4. Maximum **2** Grok rounds; then deliver with a labelled "⚠ 未通过门禁" section.

## 8. Deliver
The artifact (or a link to the file), then the audit line:
`审查：Grok 提出 N 条 → TypeSafe 保留 K 条（丢弃 D 条误报）→ 门禁 通过/未通过（readiness X）→ 成本 $Y`.

## Rules
- Acceptance criteria are written **before** producing, never after.
- Skip step 4/7 only for trivially small answers (a one-line fact, a rename) — say "未走审查" when you do.
- Grok unavailable → deliver with the TypeSafe gate only and say "Grok 未参与".
