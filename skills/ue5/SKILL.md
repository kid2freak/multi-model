---
name: ue5
description: Multi-model pipeline for Unreal Engine 5 work in a .uproject — C++ gameplay code/components/subsystems (cpp), Blueprint explanation/review/migration (blueprint), materials/shaders/rendering (render), and profiling/optimization (perf) ("给这个 Actor 加个组件", "解释这个蓝图", "为什么掉帧", "UE5", "虚幻"). Claude produces, the real engine build + Automation tests verify, Grok adversarially reviews, TypeSafe filters and gates. Windows only (Launcher build of UE 5.8).
user-invocable: true
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# ue5

Pipeline: **clarify → locate project → produce → build → test → Grok review → TypeSafe filter → revise → TypeSafe gate → deliver**.
Scripts: `~/.claude/skills/multi-model/scripts/` — `ue_env.sh`, `ue_build.sh`, `ue_test.sh`, `ue_bp_export.sh` (+ `ue_bp_export.py`, `ue_t3d_parse.py`), `judge.py`, `grok_review.sh`.
Workdir: `~/.multi-model/runs/ue5/<Project>/<slug>/` (`task.md`, `diff.patch`, `build.json`, `test.json`, `bp/`, `findings.json`, `filtered.json`, `gate.json`). The user's project files are the artifact; never copy the project into the workdir.

Status: **`cpp` (M1) and `blueprint` (M2) sub-modes are implemented.** `render` and `perf` are planned (`PLAN-ue5.md` M3); if the router picks one of them, say so and run the `cpp` pipeline for the C++ parts, or fall back to `general` for pure explanation.

Python: use `$PY` from `ue_env.sh` (on this Windows box `python3` is the Store stub; `python` is the real one).

## 0. Locate
```bash
eval "$(~/.claude/skills/multi-model/scripts/ue_env.sh <path/to/Project.uproject or dir>)"   # UE_PROJECT, UE_ENGINE_ROOT, UE_EDITOR_TARGET, PY, ...
```
No `.uproject` given → look for one in the cwd, then ask. `EngineAssociation` must resolve to an installed engine (`ue_env.sh` prints why if not). Note the git status of the project before touching it (`git -C "$UE_PROJECT_DIR" status --short`); the diff you deliver is against that.

## 1. Clarify (TypeSafe + Claude)
Use the router output (`ue_mode`, `clarify_first`, `has_acceptance`, `needs_web`). If the router was not run:
`$PY scripts/judge.py --preset route --state '{"request": "<verbatim>"}'`.
- `clarify_first` → ask **one** question covering every missing detail (which project/module, which class, replicated or not, editor or runtime).
- Write `task.md`: the task, `acceptance` (3–6 checkable bullets), `networked: true|false`, and the **test filter** the work must satisfy (e.g. `Kurodemo.Health.`). If the user gave no acceptance criteria, write them and state them in one line before producing.

## 2. Produce (Claude)
- Code goes under `<Project>/Source/<Module>/`. Follow the module's `.Build.cs`; add dependencies there, not with `#include` hacks.
- Every behavior change ships with an Automation test in the same module under `Tests/` using `IMPLEMENT_SIMPLE_AUTOMATION_TEST` inside `#if WITH_DEV_AUTOMATION_TESTS`, flags `EAutomationTestFlags::EditorContext | EAutomationTestFlags::ProductFilter`, test path `<Project>.<Feature>.<Case>`. Pure-logic tests use `NewObject<>()` (no world); world-dependent tests are out of scope for M1.
- Runtime code must not include editor modules; guard editor-only bits with `#if WITH_EDITOR`.
- Save the diff: `git -C "$UE_PROJECT_DIR" diff > diff.patch; git -C "$UE_PROJECT_DIR" status --short --untracked-files=all >> diff.patch` and append the full text of new files (untracked files are not in `git diff`).

## 3. Build (hard evidence)
```bash
~/.claude/skills/multi-model/scripts/ue_build.sh "$UE_PROJECT" --out <workdir>/build > /dev/null; cp <workdir>/build/build.json .
```
Exit 1 → fix every entry in `build.json.errors` (file/line/code/msg, already decoded from GBK) and rebuild. Exit 124 → the build exceeded `UE_BUILD_TIMEOUT` (600 s); do not raise the timeout silently — tell the user. Incremental builds here take ~1 min; `parallel_limited_to` < 4 means the machine is low on RAM.

## 4. Test (hard evidence)
```bash
~/.claude/skills/multi-model/scripts/ue_test.sh "$UE_PROJECT" --filter "<filter from task.md>" --out <workdir>/test > /dev/null; cp <workdir>/test/test.json .
```
`result` must be `Passed` with `total > 0`. `NoTests` means the filter matched nothing (typo in the test path, or the test file was not compiled) — that is a failure, not a pass. ~25 s overhead for editor startup with `-nullrhi`; add `--rhi` only for rendering tests.

## 5. Review (Grok)
```bash
scripts/grok_review.sh --role ue_reviewer --context @task.md --file diff.patch --file build.json --file test.json > findings.json
```
Large diffs: pass the changed files individually instead of the patch, plus `task.md`.

## 6. Filter (TypeSafe)
`state_findings.json` = `{"purpose": "<one-line task>", "artifact": "<diff.patch text>", "findings": <findings.json .findings>}`
`$PY scripts/judge.py --preset findings --state @state_findings.json > filtered.json` — work from `decision.kept` only.

## 7. Revise (Claude)
Fix each kept finding, then **re-run steps 3–4** (a fix that does not compile is not a fix). Disagreements go to `open_findings` for the gate.

## 8. Gate (TypeSafe)
`state_gate.json` = `{"task": <task.md text>, "acceptance": [...], "diff": <diff.patch text>, "build": <build.json>, "tests": <test.json>, "open_findings": [...], "networked": true|false}`
`$PY scripts/judge.py --preset gate-ue5-cpp --state @state_gate.json > gate.json`
- `decision.evidence.build_ok` / `tests_ok` are computed in code from the reports; the model never decides those.
- `pass` → deliver. Else fix `failed_checks` / `low_scores`, back to step 3. Maximum **2** Grok rounds; then deliver with a labelled "⚠ 未通过门禁" section.

## B. Blueprint sub-mode (`ue_mode == blueprint`)
The artifact is text about a graph (explanation, review, change plan) or a C++ migration; the graph itself is exported from the real asset, never guessed from screenshots or memory.

B1. **Export** the Blueprint(s) named in the task (object path without the `.BP_Foo` suffix):
```bash
~/.claude/skills/multi-model/scripts/ue_bp_export.sh "$UE_PROJECT" --asset /Game/Path/BP_Foo [--asset ...] --out <workdir>/bp
```
Produces `<Name>.bp.md` (pseudo-code: one block per event/function, every exec pin followed, `→ Pin:` branches, `# comment` boxes, components and variables in the header), `<Name>.bp.json` (nodes/pins/links), `<Name>.t3d`, and `export.json` with `compile.status` (must be `BS_UP_TO_DATE`) and `coverage.unreached` (must be empty). ~20 s editor startup + <1 s per asset. If the user pasted graph text copied from the editor (Ctrl+C) instead, run `$PY scripts/ue_t3d_parse.py pasted.txt --md graph.md --json graph.json` — same format, no editor needed, but then there is no compile evidence (say so).
Read `bp.md` before writing anything; it is the ground truth (`graph` in the gate).

B2. **Produce**: explanation/review/change plan → `artifact.md`, written against `bp.md` block by block (every event, every named exec pin such as `Started/Completed`, every argument source). Migration → C++ via the cpp steps 2–4 above (build + tests are then included in the gate state).
B3. **Review**: `scripts/grok_review.sh --role ue_bp_reviewer --context @task.md --file <workdir>/bp/<Name>.bp.md --file artifact.md > findings.json` (add `diff.patch`, `build.json`, `test.json` for migrations).
B4. **Filter** as in step 6, with `artifact` = the artifact text followed by a `---- graph ----` separator and the `bp.md` text (so the judge can check findings against the graph).
B5. **Gate**: `state_gate.json` = `{"task", "acceptance", "graph": <bp.md text>, "artifact": <artifact text>, "export": <export.json>, "open_findings": [...], "artifact_kind": "explanation|review|change_plan|migration", ["build", "tests" for migrations]}`
`$PY scripts/judge.py --preset gate-ue5-blueprint --state @state_gate.json > gate.json`
Blocking: `graph_misrepresented` (≥ `bp_fidelity_max`), `migration_complete`, `findings_resolved`, `readiness`, and the code-checked `evidence.bp_compiles` / `evidence.export_complete`. `unrequested_scope` / `tick_heavy_work` only produce `warnings` — mention them in the delivery, do not loop on them.
B6. Deliver with the audit line; cite the export (`bp.md` path) so the user can check the pseudo-code themselves. Changes to a graph are delivered as an exact node-level change list (event → pin → node → argument); this pipeline never edits `.uasset` files.

## 9. Deliver
The diff (file list + what changed), the exact build/test commands the user can replay (from `build.json.command` / `test.json.command`), then the audit line:
`审查：Grok 提出 N 条 → TypeSafe 保留 K 条（丢弃 D 条误报）→ 编译 ✓ 47s / 测试 ✓ 3/3 → 门禁 通过/未通过（readiness X）→ 成本 $Y`.

## Rules
- Never edit the `.uproject`, `Config/*.ini`, or anything under `Content/` unless the task is about them; plugins needed only for verification go on the command line (`-EnablePlugins=`).
- Never run the GUI editor; everything goes through `UnrealEditor-Cmd` / `Build.bat`.
- Thresholds live in `judge.py THRESHOLDS`; do not encode pass/fail rules in prompts.
- Grok unavailable → deliver with build + tests + TypeSafe gate only and say "Grok 未参与".
