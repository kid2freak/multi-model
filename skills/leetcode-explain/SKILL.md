---
name: leetcode-explain
description: Multi-model LeetCode / algorithm explanation. Use when the user asks to explain, solve, or walk through a LeetCode / 力扣 / algorithm / coding-interview problem ("讲一下 LeetCode 146", "explain two sum", "这道题怎么做", "帮我看看这个解法对不对"). Claude solves and explains, Grok adversarially hunts counterexamples, TypeSafe filters false alarms and gates delivery.
user-invocable: true
allowed-tools: Bash, Read, Write, Edit
---

# leetcode-explain

Pipeline: **solve → test → Grok challenge → TypeSafe filter → revise → TypeSafe gate → deliver**.
Scripts: `~/.claude/skills/multi-model/scripts/` (`judge.py`, `grok_review.sh`, `run_cases.py`).
Teaching style and output structure come from `~/.claude/skills/algo-sensei/` (tutor mode + `templates/solutions/solution-template.md`); read them once per session.

## 0. Setup
- Language: what the user asked for; default Python.
- Workdir: `~/.multi-model/runs/leetcode/<problem-slug>/` — keep every artifact there (`solution.<ext>`, `explanation.md`, `cases.json`, `tests.txt`, `findings.json`, `filtered.json`, `gate.json`).
- Write `problem.md`: number, title, restated statement, constraints, examples (from the user or your knowledge; if the statement is ambiguous, ask once before solving).

## 1. Solve (Claude)
Write `solution.<ext>` — clean, commented at non-obvious lines only.
Write `explanation.md` with these sections in order (Chinese unless the user writes in another language):
直觉 → 方法（编号步骤）→ 走一遍例子（真实中间状态）→ 代码逐段说明 → 复杂度（说明为什么）→ 易错点 → 为什么不用其它方法 → 如何再认出这个模式（含 2–3 道相似题）.

## 2. Test (Claude)
Write `cases.json` with ≥ 8 cases: given examples + empty/minimal input + boundaries + duplicates/negatives where relevant + largest realistic size.
Python: `python3 scripts/run_cases.py solution.py cases.json > tests.txt`.
Other languages: write a minimal harness yourself and capture its output to `tests.txt` in the same `OK/FAIL ... N/M passed` shape.
If anything fails, fix it now — do not spend a Grok call on code you already know is broken.

## 3. Challenge (Grok)
```bash
scripts/grok_review.sh --role challenger --file solution.<ext> --file explanation.md --context @problem.md > findings.json
```
Grok is told to be hostile; expect some false alarms. Never act on `findings.json` directly.

## 4. Filter (TypeSafe)
Build `state_findings.json` = `{"purpose": "teach a learner <problem> correctly", "artifact": {"code": ..., "explanation": ...}, "findings": <findings.json .findings>}` and run
`python3 scripts/judge.py --preset findings --state @state_findings.json > filtered.json`.
Work only from `decision.kept` (sorted by severity). `decision.dropped` are Grok false positives — mention them in the audit line, do not fix them.

## 5. Revise (Claude)
For each kept finding: fix code and/or explanation, add a test case that reproduces it to `cases.json`, re-run step 2. Kept findings you disagree with after checking: keep them in `open_findings` for the gate to arbitrate — do not silently drop them.

## 6. Gate (TypeSafe)
`state_gate.json` = `{"problem", "code", "explanation", "test_results": <tests.txt>, "open_findings": [{"issue", "counterexample"} for unresolved kept findings]}`
`python3 scripts/judge.py --preset gate-leetcode --state @state_gate.json > gate.json`
- `decision.pass == true` → step 7.
- Otherwise fix what `failed_checks` / `low_scores` name and go back to step 3 (new Grok round). Maximum **2** Grok rounds total; after that deliver anyway with a clearly labelled "⚠ 未通过门禁" section listing the failed checks.

## 7. Deliver
Print `explanation.md` with the code inline, then one audit line:
`审查：Grok 提出 N 条 → TypeSafe 保留 K 条（丢弃 D 条误报）→ 门禁 通过/未通过（关键分数）→ 成本 $X`.
Keep the workdir; do not paste `findings.json` or raw probabilities into the answer.

## Rules
- Never skip step 3 or 6, even for Easy problems — cheap problems are where the process is validated.
- Never hand-edit `findings.json`, `filtered.json`, `gate.json`.
- If `grok_review.sh` fails (auth/network), say so, deliver with only the TypeSafe gate, and label the audit line "Grok 未参与".
