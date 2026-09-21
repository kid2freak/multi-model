# Eval log

Re-run any row by replaying the commands in `~/.multi-model/runs/<path>/`.

## 2026-09-20 — initial validation (grok-4.6 effort medium, jev-1.13.0)

| # | Line | Case | Planted defect | Grok | TypeSafe filter | Gate | Result |
|---|------|------|----------------|------|-----------------|------|--------|
| 1 | leetcode | 704 binary search | `while lo<hi` + `hi=mid` (misses last element) | 3 findings, all real, concrete counterexamples | kept 3 (p .95/.95/.79); **dropped 1 planted false positive** ("int32 overflow in Python", p .13) | FAIL (tests_pass .01, code_matches .13) | ✅ blocked |
| 1b | leetcode | 704 fixed | none | — | — | PASS (all ≥ .98, clarity 1.0) | ✅ negative control |
| 2 | leetcode | 146 LRU cache | none | verdict sound, 0 findings, 9s | n/a | PASS (≥ .92) | ✅ no false block |
| 3 | leetcode | 53 max subarray | `best = 0` (fails all-negative) | 2 findings, both real | kept 2 (p .95/.95) | — | ✅ subtle bug caught |
| 4 | general | ext-count CLI | `--top 0` accepted (spec says exit 2) | 2 findings: planted one **+ unplanned real one** (symlinked files counted) | kept 2 (p .93/.87) | after fix: PASS (readiness .91) | ✅ found a bug the author missed |
| 5 | paper | synthetic draft | over-claiming, invalid inference, mis-attributed citation | 8 findings incl. 2 unplanned (17× vs log₂ dimension mismatch; missing 表 1), 117s | kept 8 (p .66–.96) | gate-paper FAIL: overclaiming .98, logic_gaps .98; claim check flagged exactly the 3 unsupported claims (p .02/.03/.02), passed the 2 supported ones (.92/.84), borderline .69 on a claim that omits a qualifier | ✅ |
| 6 | paper | researcher role | — | provenance of Bentley "90%" quote: primary CACM 1983 source + page, 6 documented distortions in popular retellings; 145s, **$0.43** | — | — | ✅ but expensive |

Observations
- Filter separates real from fake: real findings p ≥ .66, planted fake p = .13 (threshold .6).
- Grok input is always ~24k tokens (CLI overhead); reviews cost $0.02–0.03. Researcher with web is ~20× that.
- Both "unplanned" catches (#4 symlink, #5 dimension mismatch) came from Grok, i.e. from the model that did not write the artifact — the cross-model effect the design bets on.
- Not yet measured: Claude-only baseline on the same cases; long real PDFs through the critic; Windows/UE5 line (out of scope on this Mac).

## 2026-09-21 — ue5 line, M1 (`cpp` sub-mode; Windows, UE 5.8.2, grok-4.6 effort medium, jev-1.13.0)

Project: `E:\ProgramSoftware\UE_Project\Kurodemo`. Runs: `~/.multi-model/runs/ue5/Kurodemo/m1-health*/`. Fixture kept in the run dir (`Public/`, `Private/`).

| # | Case | Planted defect | Build / Tests | Grok | TypeSafe filter | Gate (gate-ue5-cpp) | Result |
|---|------|----------------|---------------|------|-----------------|---------------------|--------|
| 7 | UHealthComponent v1 (ApplyDamage/Heal/OnDeath + 2 tests) | none intended | ✓ 12.5 s (8 actions) / ✓ 2/2, 16 s | 2 findings, **both real and unplanned**: `IsNearlyEqual` short-circuit swallows the final hit when Health < 1e-4 (concrete repro); tests never bind `OnDeath`, so "broadcast once" was unverified. 77 s, $0.026 | kept 2 (p .86/.77) | not run (blocking findings) | ✅ cross-model catch |
| 7b | v2: exact compare + 2 delegate-counting tests | none | ✓ 8.7 s / ✓ 4/4, 19 s | — | — | **PASS**: defect probs .16/.11/.03, findings_resolved .98, scope .89, test_adequacy 1.0, readiness .95; evidence build_ok/tests_ok true (code) | ✅ |
| 8 | v2 + `TObjectPtr<UObject> LastInstigator` **without UPROPERTY** | GC dangling reference; build and tests stay green | ✓ / ✓ 4/4 | critical (missing UPROPERTY, GC repro) + minor (unrequested scope). 63 s, $0.024 | kept 1 (p .82), dropped the scope nit (p .34) | **FAIL** on `reflection_correct` p_defect .44 ≥ .25 (gate run without fixing, to test the gate alone) | ✅ blocked twice over |

Observations
- The gate's Unreal defect questions are inverted (yes == defect) and fail at `p ≥ 1 - gate_pass` (.25); the first phrasing ("are the rules followed?" with a list of N/A rules) sat at .61 on correct code and would have blocked it — question design matters more than the threshold.
- Build/test evidence never goes through the model: `decision.evidence` is computed from `build.json` / `test.json` in `judge.py`.
- Whole cpp round on this machine: build ~10–50 s incremental, tests ~20 s, Grok ~60–80 s, TypeSafe ~2 s → ≈ 2–3 min per review round.
- Blueprint / render / perf sub-modes: not yet (M2/M3).

## 2026-09-21 — ue5 line, M2 (`blueprint` sub-mode; Windows, UE 5.8.2, grok-4.6 effort medium, jev-1.13.0)

Project: the disposable `BpProbe` (ThirdPerson template + shared content) at `~/.multi-model/runs/ue5/m0/BpProbe`. Runs: `~/.multi-model/runs/ue5/BpProbe/m2-*/`.

Export fidelity (`ue_bp_export.sh` → `ue_t3d_parse.py`), 6 template Blueprints: every graph compiled `BS_UP_TO_DATE`, exec coverage 22/22, 49/49, 9/9, 4/4, 16/16, 1/1 (no unreached exec node); the ThirdPerson character's 8 event chains are all rendered including `IA_Jump.Started → Jump / Completed → StopJumping` (Epic's own DSL had rendered 3/8 in M0). Timelines (`→ Update:`), Sequence, Branch (`→ true/false`), validated gets (`→ Is Valid`), macros (`IsValid → Is Valid`), casts (`AsCharacter`), split struct pins (`ReturnValue_Yaw`) and comment boxes all survive. Simulated clipboard text (flat node list, the Ctrl+C format) parses with the same code path.

| # | Case | Planted defect | Export | Grok (`ue_bp_reviewer`) | TypeSafe filter | Gate (gate-ue5-blueprint) | Result |
|---|------|----------------|--------|-------------------------|-----------------|---------------------------|--------|
| 9 | Explain BP_ThirdPersonCharacter, faithful v1 (1532 chars) | none | compile ✓, coverage 22/22 | `sound`, 0 findings, 62 s, $0.024 | — | **PASS**: graph_misrepresented .23, readiness .75, warnings: none (unrequested_scope .46 < .5) | ✅ |
| 9b | same, compact v2 (914 chars) | none | same | — | — | **PASS**: graph_misrepresented .24, readiness .71 | ✅ |
| 10 | Explanation that drops `Started/Completed`, invents `Event Tick`, calls Move's rotation "full Control Rotation" | 3 planted | same | `flawed`, 5 findings: the 3 planted **+ 2 unplanned real** (touch-jump events omitted; UserConstructionScript never mentioned). 49 s, $0.023 | kept 5/5 (p .81–.94) | **FAIL**: graph_misrepresented .96, readiness .22 | ✅ blocked |

Observations
- `graph_misrepresented` separates cleanly (.22–.24 vs .96) but the clean cases sit right above the cpp defect cutoff (.25), so the blueprint family got its own threshold `bp_fidelity_max = .5` in `THRESHOLDS`; cpp keeps `cpp_defect_max = .25` (the planted missing-UPROPERTY scored .35–.44 across runs — margin is thin, revisit in M4).
- A generic "scope respected?" question was noisy on explanations (.53–.68 on correct ones, both phrasings) — it is now `unrequested_scope`, warn-only (≥ .5), with omissions covered by `graph_misrepresented`.
- The gate is a compile + coverage check plus one fidelity question; Grok is the one that finds *which* line is wrong. Round cost ≈ 25 s export + 50–60 s Grok + 2 s TypeSafe.

## 2026-09-21 — ue5 line, M3 (`perf` + `render` sub-modes; Windows, UE 5.8.2 D3D12 SM6, Radeon 780M, grok-4.6 effort medium, jev-1.13.0)

Project: `BpProbe` (ThirdPerson template). Runs: `~/.multi-model/runs/ue5/BpProbe/m3-*/`.

Evidence capture: `ue_perf_capture.sh` = standalone `-game -windowed 1280x720 -csvCaptureFrames=600 -ExitAfterCsvProfiling -csvGpuStats`, 21–29 s per run, 480 usable frames after a 120-frame warm-up, 0 hitches; `r.ScreenPercentage 200` as a synthetic regression moved FrameTime p50 8.5 → 21.4 ms (code verdict `worse`, +152%), `r.ScreenPercentage 50` moved GPU −36% but FrameTime only −11% (inside the noise band → `same`; overall `mixed`). `ue_render_check.sh` = `-run=pythonscript -AllowCommandletRendering` (without that flag `MaterialEditingLibrary.get_statistics` returns all zeros): M_PrototypeGrid 232 ps / 148 vs / 2 samplers, M_SimpleGlow 158 / 339 / 2; 2.5 min on a cold DDC, 15–40 s warm. A material with a broken Custom-HLSL node is only logged by the engine as a *warning* ("Failed to compile Material … Default Material will be used") plus DXC error lines — both are now counted as errors.

| # | Mode | Case | Planted defect | Evidence (code) | Grok | TypeSafe filter | Gate | Result |
|---|------|------|----------------|-----------------|------|-----------------|------|--------|
| 11 | perf | "frame time 21 ms, find the bottleneck and fix it": GPU-bound diagnosis, ScreenPercentage 200→100, numbers quoted from perf.json | none | baseline_captured ✓ same_conditions ✓ improvement_real ✓ (FrameTime −60%) | `sound`, 95 s, $0.031 | — | hypothesis_supported .95, numbers_misquoted .12, **readiness .69–.70** (3 runs: .69/.70/.70) → borderline PASS | ⚠ passes at the edge; the "change" is a capture cvar, not a project fix, and the judge discounts it |
| 12 | perf | blames GameThread Tick, raises ScreenPercentage to 200, claims −7% and `verdict = better` | wrong thread, inverted change, invented numbers | improvement_real ✗ (FrameTime +152%) | `flawed`, 5 findings (all 3 planted + "disabling Tick changes behavior" + "global cvar when per-asset asked"), 58 s, $0.026 | kept 5/5 (.83–.89) | **FAIL**: numbers_misquoted .98, hypothesis .03, readiness .00 | ✅ blocked |
| 12b | perf | #11 with `baseline = null` | missing baseline | baseline_captured ✗ | — | — | **FAIL** on evidence | ✅ |
| 13 | render | explain M_SimpleGlow cost from render.json (v1) | none intended | shader_compiles ✓ | `flawed`, 3 findings: extrapolated the Opaque cost from a different material (real), omitted `num_pixel_texture_samples` (real), attributed the vertex cost to Two-Sided (unverifiable) — 30 s, $0.020 | kept 3/3 (.77–.85) | PASS anyway (readiness .88; findings were not in `open_findings`) | ✅ Grok catches what the gate cannot |
| 13b | render | v2 revised with hedges and "run the tool again" notes | — | ✓ | — | — | readiness **.65**, unrequested_scope .60 warn → FAIL | ✅ hedging penalized (documented in SKILL R5) |
| 13c | render | v3: facts + numbers only, no extrapolation | — | ✓ | — | — | **PASS** readiness .91, render_defect .11, cost_unreported .12 | ✅ |
| 14 | render | broken Custom-HLSL material presented as "compiles, 158→0 instructions" | compile failure hidden, invented numbers | shader_compiles ✗ (2 shader errors, `compile_failed`) | `flawed`, 5 findings, 63 s, $0.050 (56k input: render.json is big) | — | **FAIL**: render_defect .78, readiness .00 | ✅ blocked twice over |

Observations
- Perf and render gates lean on code-checked evidence (`improvement_real`, `same_conditions`, `shader_compiles`); the model questions add "did the text match the data" (`numbers_misquoted` .12 vs .98) and separate cleanly.
- `readiness` sits at .69–.70 for a correct-but-thin perf artifact; not tuned (threshold stays .7) — M4 should use a real code change as the positive perf case.
- `judge.py` now retries transient network errors 3× (the local proxy dropped two calls during this run).
- Cost per round: perf ≈ 25 s × 2 captures + 60–95 s Grok; render ≈ 15–40 s check + 30–60 s Grok; TypeSafe 2–3 s.
