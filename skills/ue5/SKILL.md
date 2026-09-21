---
name: ue5
description: Multi-model pipeline for Unreal Engine 5 work in a .uproject — C++ gameplay code/components/subsystems (cpp), Blueprint explanation/review/migration (blueprint), materials/shaders/rendering (render), and profiling/optimization (perf) ("给这个 Actor 加个组件", "解释这个蓝图", "为什么掉帧", "UE5", "虚幻"). Claude produces, the real engine build + Automation tests verify, Grok adversarially reviews, TypeSafe filters and gates. Windows only (Launcher build of UE 5.8).
user-invocable: true
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# ue5

Pipeline: **clarify → locate project → produce → build → test → Grok review → TypeSafe filter → revise → TypeSafe gate → deliver**.
Scripts: `~/.claude/skills/multi-model/scripts/` — `ue_env.sh`, `ue_build.sh`, `ue_test.sh`, `ue_bp_export.sh` (+ `ue_bp_export.py`, `ue_t3d_parse.py`), `ue_perf_capture.sh` (+ `ue_csv_summary.py`), `ue_render_check.sh` (+ `ue_material_stats.py`), `judge.py`, `grok_review.sh`.
Workdir: `~/.multi-model/runs/ue5/<Project>/<slug>/` (`task.md`, `diff.patch`, `build.json`, `test.json`, `bp/`, `findings.json`, `filtered.json`, `gate.json`). The user's project files are the artifact; never copy the project into the workdir.

Status: all four sub-modes are implemented — `cpp` (M1, steps 2–8), `blueprint` (M2, section B), `perf` (M3, section P), `render` (M3, section R). Evaluation and threshold tuning continue in M4/M5 (`PLAN-ue5.md`).

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

## P. Perf sub-mode (`ue_mode == perf`)
The artifact is a diagnosis (bottleneck hypothesis backed by numbers) or an optimization (diagnosis + change + before/after). Every number comes from a CSV Profiler capture made by the script; never from `stat unit` screenshots or memory.

P1. **Baseline** (before touching anything):
```bash
~/.claude/skills/multi-model/scripts/ue_perf_capture.sh "$UE_PROJECT" --map /Game/Path/Map --label baseline --out <workdir>/perf-baseline
```
Standalone `-game`, real RHI, windowed 1280x720, 900 frames from boot; the first 120 (engine init + map load) are dropped, hitches (> 4× median) are counted separately. `perf.json` has p50/p95/max per thread (`FrameTime`, `GameThreadTime`, `RenderThreadTime`, `GPUTime`, `RHIThreadTime`), `top_gamethread` exclusive stats, `ticks` per class, draw calls, memory. ~25 s. Read the bottleneck off the data: FrameTime ≈ RenderThreadTime ≈ GPUTime ⇒ GPU-bound; GameThreadTime ≈ FrameTime with `EventWait` small ⇒ game-thread-bound; `hitches.count > 0` ⇒ look at `top_gamethread` spikes (GC, streaming, spawning).
P2. **Produce**: `artifact.md` = hypothesis (which thread/stat, with the p50 numbers), the change (C++ via the cpp steps 2–4 when code changes; asset/cvar/setting changes otherwise, stated exactly), expected effect. `artifact_kind: diagnosis` if the task only asks why.
P3. **After** (same map, same resolution, back to back, nothing else running):
```bash
~/.claude/skills/multi-model/scripts/ue_perf_capture.sh "$UE_PROJECT" --map /Game/Path/Map --label after --compare <workdir>/perf-baseline/perf.json --out <workdir>/perf-after
```
`perf.json.compare.metrics[*].verdict` is computed in code (`better/worse/same` vs a noise band of max(1.5×IQR, 3%)); quote those verdicts and the p50 deltas in `artifact.md`, do not restate the numbers by hand.
P4. **Review**: `scripts/grok_review.sh --role ue_perf_analyst --context @task.md --file artifact.md --file perf-baseline/perf.json --file perf-after/perf.json [--file diff.patch] > findings.json`.
P5. **Filter** as in step 6 (`artifact` = artifact text + both perf.json texts).
P6. **Gate**: `state_gate.json` = `{"task", "acceptance", "artifact", "baseline": <baseline perf.json>, "after": <after perf.json or null>, "open_findings", "artifact_kind": "diagnosis|optimization", ["diff", "build", "tests"]}`
`$PY scripts/judge.py --preset gate-ue5-perf --state @state_gate.json > gate.json`
Code-checked evidence: `baseline_captured` (≥ 100 usable frames); for optimizations also `same_conditions` and `improvement_real` (`compare.verdict == better`). Model: `hypothesis_supported` (score ≥ `hypothesis_min`), `numbers_misquoted` (blocking), cpp defect questions when a `diff` is present, `readiness`. If `improvement_real` is false the change did not beat the noise — say so; do not re-run captures until one looks better.

## R. Render sub-mode (`ue_mode == render`)
The artifact is a material/shader/RDG change (diff and/or exact asset-level change list) or an explanation of rendering cost. Evidence is a real shader compile.

R1. **Check** (before and, for changes, after):
```bash
~/.claude/skills/multi-model/scripts/ue_render_check.sh "$UE_PROJECT" --asset /Game/Path/M_Foo [--asset ...] [--compare <baseline>/render.json] --out <workdir>/render
```
Headless editor commandlet with `-AllowCommandletRendering` (D3D12 SM6 here): recompiles each material, records `statistics` (pixel/vertex instruction counts, samplers, texture samples), material domain/blend mode/shading model, and every `LogShaderCompilers`/`LogMaterial` error — including "Failed to compile Material" (the engine logs it as a *warning*; the script counts it as an error and marks the asset `compile_failed`). No `--asset` → only the startup/global shader compile is checked (for `.usf`/`.ush` edits; pair with `ue_build.sh` for C++ shader bindings). First run on a project can take minutes (DDC fill); afterwards ~15–40 s. `render.json.result` must be `Passed`; with `--compare`, `compare.materials[*].verdict` is `better/worse/same/broken` by pixel instructions and texture samples.
R2. **Produce**: `artifact.md` states the change, the measured cost before/after from `render.json` (quote, do not estimate), and side effects of any blend-mode/shading-model/domain change (Nanite, Lumen, translucency sorting, depth). C++ shader/RDG code goes through the cpp steps 2–4.
R3. **Review**: `scripts/grok_review.sh --role ue_reviewer --context @task.md --file artifact.md --file render/render.json [--file diff.patch --file build.json] > findings.json`.
R4. **Filter** as in step 6. R5. **Gate**: `state_gate.json` = `{"task", "acceptance", "artifact", "render": <render.json>, "open_findings", ["diff", "build", "tests"]}` → `--preset gate-ue5-render`. Code-checked: `shader_compiles`. Blocking model questions: `render_defect`, `findings_resolved`, `readiness`; `cost_unreported` and `unrequested_scope` only warn. Hedging and process notes in the artifact lower `readiness` — state facts and numbers, not what you would do next.

## 9. Deliver
The diff (file list + what changed), the exact build/test commands the user can replay (from `build.json.command` / `test.json.command`), then the audit line:
`审查：Grok 提出 N 条 → TypeSafe 保留 K 条（丢弃 D 条误报）→ 编译 ✓ 47s / 测试 ✓ 3/3 → 门禁 通过/未通过（readiness X）→ 成本 $Y`.

## Rules
- Never edit the `.uproject`, `Config/*.ini`, or anything under `Content/` unless the task is about them; plugins needed only for verification go on the command line (`-EnablePlugins=`).
- Never run the GUI editor; everything goes through `UnrealEditor-Cmd` / `Build.bat`.
- Thresholds live in `judge.py THRESHOLDS`; do not encode pass/fail rules in prompts.
- Grok unavailable → deliver with build + tests + TypeSafe gate only and say "Grok 未参与".
