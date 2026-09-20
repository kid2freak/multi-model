---
name: paper-review
description: Multi-model academic paper / report pipeline. Use when the user wants to read, summarize, review, critique, translate, write or revise an academic paper, thesis chapter, or formal report ("帮我读这篇论文", "审一下我的初稿", "总结这篇 paper", "写文献综述", "review my draft"). Claude (via the academic-research-skills plugin) does reading/writing/panel review, Grok gives an independent whole-document critique and second-source literature search, TypeSafe checks claim-evidence support and gates delivery.
user-invocable: true
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, WebSearch, WebFetch
---

# paper-review

Pipeline: **ingest → (research) → ARS produce/review → Grok critic → TypeSafe filter → claim check → revise → gate → deliver**.
Scripts: `~/.claude/skills/multi-model/scripts/` (`judge.py`, `grok_review.sh`, `pdf_text.py`).
Third-party base: plugin `academic-research-skills` (ARS) — skills `deep-research`, `academic-paper`, `academic-paper-reviewer`, `academic-pipeline`; commands `/ars-*`. Use ARS for the Claude-side work; this skill only adds the other two models around it. Never edit ARS files.
Workdir: `~/.multi-model/runs/paper/<slug>/`.

## 0. Ingest and pick a mode
- PDF → `python3 scripts/pdf_text.py paper.pdf > paper.txt`; .md/.docx/.txt → text as-is (docx via the `docx` skill).
- Modes, pick from the request:
  - **read**: user wants to understand someone else's paper → deliverable is a structured reading report.
  - **review**: user's own draft → deliverable is a review + revision roadmap.
  - **write/revise**: user wants text produced → deliverable is the draft.
- Write `task.md`: mode, audience, language, length, venue/format constraints, and 3–6 acceptance bullets.

## 1. Research (write mode, or when `needs_web`)
Two independent sources:
- Claude: ARS `deep-research` (quick mode unless the user asks for systematic).
- Grok: `scripts/grok_review.sh --role researcher --context "<research question, incl. year range>" > research_grok.json` (≈ $0.4 and 2–3 min per call — one call per task, put every sub-question into that one prompt).
Merge: keep sources both found, or single-source items with a primary URL + date. Record disagreements in `task.md`; they become "limitations" or open questions, never silently resolved.

## 2. Produce (Claude via ARS)
- read → follow `academic-paper` reading-report structure (背景/问题 → 方法 → 主要结果 → 贡献 → 局限 → 与相关工作的关系 → 对我的启发). Save `artifact.md`.
- review → run `academic-paper-reviewer` (full mode) on `paper.txt`; save its report as `artifact.md`.
- write/revise → `academic-paper` in the fitting mode; save `artifact.md`.

## 3. Critic (Grok, whole document)
```bash
scripts/grok_review.sh --role critic --context @task.md --file paper.txt [--file artifact.md] > findings.json
```
- read mode: review `artifact.md` **against** `paper.txt` (pass both) — Grok checks whether the report misrepresents the paper.
- review mode: pass `paper.txt` only — Grok is an independent 6th reviewer with no sight of ARS's panel; disagreements between the two are the most valuable output.
- write mode: pass `artifact.md` plus the sources list.
Grok 4.6 has 500k context; pass the whole paper, do not chunk.

## 4. Filter (TypeSafe)
`state_findings.json` = `{"purpose": "<mode + one-line goal>", "artifact": "<artifact.md or paper.txt excerpt around each finding>", "findings": <findings>}` → `judge.py --preset findings` → `filtered.json`. Work from `decision.kept`.

## 5. Claim check (TypeSafe)
Extract from the deliverable every factual/empirical claim that carries a citation or a number (≤ 25; if more, take the ones in abstract, results, conclusion). For each: `{"text": <claim>, "citation": <ref>, "evidence": <the actual passage from paper.txt or the fetched source, ≤ 600 chars>}`. No evidence retrievable → `evidence: "NOT FOUND"`.
`state_paper.json` = `{"claims": [...], "draft": <artifact.md>}` → `python3 scripts/judge.py --preset gate-paper --state @state_paper.json > gate.json`.

## 6. Revise (Claude)
Fix kept Grok findings and every `supported_i` below threshold (rewrite the claim to match its evidence, or find real evidence, or delete it). `overclaiming` / `logic_gaps` ≥ 0.5 → rewrite those passages. Re-run step 5 after revising.

## 7. Gate
`gate.json decision.pass` → deliver. Else back to step 3 (max 2 Grok rounds), then deliver with "⚠ 未通过门禁" listing unsupported claims verbatim.

## 8. Deliver
`artifact.md`, then:
- review mode only: a short "ARS 面板 vs Grok 分歧" table (issue · ARS says · Grok says · my take).
- audit line: `审查：Grok 提出 N 条 → TypeSafe 保留 K 条 → 引用核验 S/T 条通过 → 门禁 通过/未通过（rigor X）→ 成本 $Y`.

## Rules
- Never let a claim survive with `evidence: "NOT FOUND"` in write/revise mode; in read mode, flag it as "原文未找到依据".
- Do not paraphrase Grok's findings back to the user as your own; attribute them.
- ARS is CC BY-NC: personal/academic use only.
