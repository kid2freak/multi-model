#!/usr/bin/env python3
"""TypeSafe (Jev) judge: typed routing and pass/fail gates for the multi-model workflow.

Usage:
  judge.py --preset route     --state '{"request": "..."}'
  judge.py --preset findings  --state @state.json      # {"artifact": "...", "findings": [...]}
  judge.py --preset gate-leetcode|gate-general|gate-paper --state @state.json
  judge.py --preset gate-ue5-cpp --state @state.json   # {"task","acceptance","diff","build","tests","open_findings","networked"}
  judge.py --preset gate-ue5-blueprint --state @state.json   # {"task","acceptance","graph","artifact","export","open_findings",
                                                            #  "artifact_kind": explanation|review|change_plan|migration, ["build","tests"]}
  judge.py --preset gate-ue5-perf --state @state.json        # {"task","acceptance","artifact","baseline","after","open_findings",
                                                            #  "artifact_kind": diagnosis|optimization, ["diff","build","tests"]}
  judge.py --preset gate-ue5-render --state @state.json      # {"task","acceptance","artifact","render","open_findings", ["diff","build","tests"]}
  judge.py --questions @questions.json --state @state.json   # raw pass-through
  echo '{"request": "..."}' | judge.py --preset route --state -

Prints JSON: {"answers": {...}, "decision": {...}, "usage": {...}}.
Thresholds live in THRESHOLDS below and are applied in code, never by the model.
Question design follows the structured instructions/criteria style from devagrawal09/jev-review.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"

THRESHOLDS = {
    "route_min_confidence": 0.6,   # below this the router asks the user instead of guessing
    "finding_valid": 0.6,          # Noul >= this keeps a Grok finding
    "gate_pass": 0.75,             # every blocking Noul must reach this to pass
    "readiness_pass": 0.7,         # normalized Score (0..1) required to deliver
    "cpp_defect_max": 0.25,        # gate-ue5-cpp: inverted defect Nouls (yes == defect) block at/above this
    "bp_fidelity_max": 0.5,        # gate-ue5-blueprint: graph_misrepresented / migration_complete block at/above this
    "hypothesis_min": 0.6,         # gate-ue5-perf: normalized hypothesis_supported score required (2 of 3 levels)
}


# Shell profiles searched (in order) when TYPESAFE_API_KEY is not in the environment.
# macOS: ~/.zshrc. Windows/Git Bash: ~/.bashrc, ~/.bash_profile, ~/.profile.
_PROFILE_FILES = ("~/.zshrc", "~/.bashrc", "~/.bash_profile", "~/.profile")
_KEY_RE = re.compile(r'''^\s*(?:export\s+)?TYPESAFE_API_KEY\s*=\s*["']?([^"'\s#]+)''', re.M)


def _api_key():
    key = os.environ.get("TYPESAFE_API_KEY")
    if key:
        return key
    for name in _PROFILE_FILES:
        path = os.path.expanduser(name)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8", errors="ignore") as f:
            m = _KEY_RE.search(f.read())
        if m:
            return m.group(1)
    sys.exit("TYPESAFE_API_KEY not set (env, or `export TYPESAFE_API_KEY=...` in one of: "
             + ", ".join(_PROFILE_FILES) + ")")


def _load(arg):
    if arg == "-":
        return json.load(sys.stdin)
    if arg.startswith("@"):
        with open(arg[1:], encoding="utf-8") as f:   # explicit: Windows defaults to the ANSI code page
            return json.load(f)
    return json.loads(arg)


def ask(state, questions):
    body = json.dumps({"model": MODEL, "state": state, "questions": questions}).encode()
    req = urllib.request.Request(
        API_URL, data=body,
        headers={"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"},
    )
    last = None
    for attempt in range(3):   # transient network errors (local proxy hiccups, 10060 timeouts) are retried
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            sys.exit(f"TypeSafe HTTP {e.code}: {e.read().decode()[:500]}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            time.sleep(2 * (attempt + 1))
    sys.exit(f"TypeSafe unreachable after 3 attempts: {last}")


# ---------------------------------------------------------------- presets

def q_route(state):
    return {
        "route": {
            "type": "choice",
            "instructions": {
                "question": "Which workflow should handle `request`?",
                "focus": "The primary deliverable the user wants, not incidental mentions",
            },
            "criteria": {
                "leetcode": "Explaining, solving, or reviewing an algorithm / data-structure / coding-interview problem",
                "paper": "Reading, summarizing, reviewing, translating or writing an academic paper, thesis, or formal report",
                "ue5": "Work inside an Unreal Engine 5 project: gameplay C++ (UCLASS/UPROPERTY/AActor/UActorComponent/GAS), Blueprints, materials/shaders/rendering, or engine performance profiling; mentions of .uproject, UE5, Unreal, 蓝图, 虚幻",
                "general": "Anything else: scripts, plugins, tools, prose editing, planning, Q&A",
            },
        },
        "ue_mode": {
            "type": "choice",
            "instructions": {
                "question": "If `request` is Unreal Engine work, which ue5 sub-mode fits best? (Ignored for non-Unreal requests; pick cpp then.)",
                "focus": "The artifact the user wants changed or explained",
            },
            "criteria": {
                "cpp": "C++ classes, components, subsystems, gameplay logic, networking, tests",
                "blueprint": "Explaining, reviewing, changing or migrating a Blueprint graph",
                "render": "Materials, shaders (.usf/.ush), RDG passes, post-process, Nanite/Lumen/lighting settings",
                "perf": "Frame time, hitches, memory, load time: profiling, diagnosing, optimizing",
            },
        },
        "needs_web": {
            "type": "noul",
            "instructions": "Does answering `request` well require up-to-date or external information from the internet?",
            "criteria": {
                "true": "Depends on recent events, current library versions, published sources, or facts not derivable from the request",
                "false": "Can be answered from the request text plus general knowledge and local files",
            },
        },
        "needs_code_run": {
            "type": "noul",
            "instructions": "Would executing code materially improve confidence in the answer to `request`?",
        },
        "has_acceptance": {
            "type": "noul",
            "instructions": "Does `request` state a checkable acceptance criterion (expected output, test, format, length, audience)?",
        },
        "clarity": {
            "type": "score",
            "instructions": "How clearly specified is `request`?",
            "criteria": [
                "Too vague to start: goal or subject is missing",
                "Clear goal but a key detail (input, language, format, scope) is missing",
                "Fully actionable as written",
            ],
        },
    }


def q_findings(state):
    qs = {}
    for i, _ in enumerate(state.get("findings", [])):
        qs[f"valid_{i}"] = {
            "type": "noul",
            "instructions": {
                "question": f"Does `artifact` directly support that `findings[{i}]` describes a real defect?",
                "inspect": ["artifact", f"findings[{i}].issue", f"findings[{i}].counterexample"],
                "ignore": ["Style preferences", "Speculation not grounded in the artifact", "Issues the artifact already handles"],
            },
            "criteria": {
                "true": {
                    "what": "The artifact, as written, exhibits the described defect and the counterexample (if any) would actually fail",
                },
                "false": {
                    "what": "The finding is wrong, already handled by the artifact, purely stylistic, or its counterexample does not fail",
                },
            },
        }
        qs[f"severity_{i}"] = {
            "type": "score",
            "instructions": f"Assuming `findings[{i}]` is real, how severe is it for the stated purpose in `purpose`?",
            "criteria": [
                "Nit: cosmetic or preference only",
                "Minor: small inaccuracy, output still usable",
                "Major: misleads the reader or fails some realistic inputs",
                "Critical: core result wrong, unsafe, or fails common inputs",
            ],
        }
    return qs


def q_gate_leetcode(state):
    return {
        "complexity_correct": {
            "type": "noul",
            "instructions": {
                "question": "Is the time/space complexity stated in `explanation` correct for `code`?",
                "inspect": ["code", "explanation"],
            },
        },
        "code_matches_explanation": {
            "type": "noul",
            "instructions": "Does `explanation` accurately describe what `code` actually does, step by step, without contradicting it?",
        },
        "counterexamples_addressed": {
            "type": "noul",
            "instructions": {
                "question": "Are all items in `open_findings` resolved by the current `code` and `explanation`?",
                "note": "If `open_findings` is empty, answer yes.",
            },
        },
        "tests_pass": {
            "type": "noul",
            "instructions": "Does `test_results` show every test passing with no errors?",
        },
        "clarity": {
            "type": "score",
            "instructions": "How well would `explanation` teach a learner who has not seen this problem before?",
            "criteria": [
                "Confusing: jumps to code, no intuition",
                "Adequate: correct but terse, key insight not motivated",
                "Good: intuition, approach, walkthrough of an example, pitfalls",
                "Excellent: all of the above plus why alternatives are worse and how to recognise the pattern again",
            ],
        },
    }


def q_gate_general(state):
    return {
        "meets_acceptance": {
            "type": "noul",
            "instructions": {
                "question": "Does `artifact` satisfy every criterion in `acceptance` for `task`?",
                "inspect": ["task", "acceptance", "artifact"],
            },
        },
        "findings_resolved": {
            "type": "noul",
            "instructions": {
                "question": "Are all items in `open_findings` resolved in `artifact`?",
                "note": "If `open_findings` is empty, answer yes.",
            },
        },
        "scope_respected": {
            "type": "noul",
            "instructions": "Does `artifact` stay within the scope of `task`, without unrequested additions or omissions?",
        },
        "readiness": {
            "type": "score",
            "instructions": "How ready is `artifact` to hand to the user as the final answer to `task`?",
            "criteria": [
                "Not usable: wrong direction or major gaps",
                "Draft: right direction, needs another revision",
                "Usable: minor polish only",
                "Deliverable as-is",
            ],
        },
    }


def q_gate_paper(state):
    qs = {}
    for i, _ in enumerate(state.get("claims", [])):
        qs[f"supported_{i}"] = {
            "type": "noul",
            "instructions": {
                "question": f"Is `claims[{i}].text` actually supported by `claims[{i}].evidence`?",
                "focus": "Whether the cited evidence says what the claim says, with the same strength and scope",
            },
            "criteria": {
                "true": "The evidence states or clearly implies the claim at comparable strength",
                "false": "The evidence is absent, off-topic, weaker/narrower than the claim, or contradicts it",
            },
        }
    qs["overclaiming"] = {
        "type": "noul",
        "instructions": "Does `draft` assert conclusions stronger than what its cited evidence and stated methods justify?",
    }
    qs["logic_gaps"] = {
        "type": "noul",
        "instructions": "Does `draft` contain a step where a conclusion does not follow from the preceding argument or data?",
    }
    qs["rigor"] = {
        "type": "score",
        "instructions": "Rate the argumentative rigor of `draft`.",
        "criteria": [
            "Weak: assertions without support",
            "Uneven: some claims supported, others not",
            "Solid: claims supported, limitations acknowledged",
            "Rigorous: supported, limitations and alternatives addressed, scope precise",
        ],
    }
    return qs


def _q_cpp_defects():
    return {
        "reflection_correct": {
            "type": "noul",
            "instructions": {
                "question": "Does `diff` violate an Unreal reflection or GC rule?",
                "inspect": ["diff"],
                "criteria": {
                    "true": "Any of: a UObject-derived member (raw pointer or TObjectPtr) without UPROPERTY; a UCLASS/USTRUCT/UENUM without GENERATED_BODY; a UFUNCTION specifier that does not match its use (Server/Client/NetMulticast without _Implementation/_Validate, BlueprintCallable on an unsupported signature); UPROPERTY on an unsupported type; a delegate not declared with the delegate macros",
                    "false": "None of those violations is present",
                },
            },
        },
        "lifecycle_correct": {
            "type": "noul",
            "instructions": {
                "question": "Does `diff` contain a lifecycle or ownership mistake?",
                "inspect": ["diff"],
                "criteria": {
                    "true": "Any of: an overridden BeginPlay/EndPlay/Tick/InitializeComponent omits Super; a timer or delegate bound in BeginPlay/Initialize is never cleared in EndPlay/Uninitialize; a UObject pointer is dereferenced without a validity check; a constructor touches the World; CreateDefaultSubobject outside a constructor; Tick left enabled with an empty Tick",
                    "false": "None of those mistakes is present. Rules that do not apply (no timers, no Tick, no pointers) count as satisfied",
                },
            },
        },
        "no_editor_only_leak": {
            "type": "noul",
            "instructions": {
                "question": "Does `diff` use editor-only API (UnrealEd, editor subsystems, WITH_EDITOR-only headers) in a runtime code path without a #if WITH_EDITOR / WITH_EDITORONLY_DATA guard?",
                "inspect": ["diff"],
            },
        },
    }


def q_gate_ue5_cpp(state):
    qs = _q_cpp_defects()
    qs.update({
        "findings_resolved": {
            "type": "noul",
            "instructions": {
                "question": "Are all items in `open_findings` resolved in `diff`?",
                "note": "If `open_findings` is empty, answer yes.",
            },
        },
        "scope_respected": {
            "type": "noul",
            "instructions": "Does `diff` stay within the scope of `task` and `acceptance`, without unrequested additions or omissions?",
        },
        "test_adequacy": {
            "type": "score",
            "instructions": {
                "question": "How well do the Automation tests in `tests` (see `tests.tests[].path`) plus any tests in `diff` cover the behavior `task` asks for?",
                "inspect": ["task", "acceptance", "diff", "tests"],
            },
            "criteria": [
                "No test exercises the new/changed behavior",
                "Smoke test only: constructs the object or calls the main entry point once",
                "Main branches of the requested behavior are asserted",
                "Main branches plus edge cases (zero/negative/overflow, repeated calls, invalid input) are asserted",
            ],
        },
        "readiness": {
            "type": "score",
            "instructions": "How ready is `diff` to hand to the user as the final answer to `task`, given `build` and `tests`?",
            "criteria": [
                "Not usable: wrong direction or major gaps",
                "Draft: right direction, needs another revision",
                "Usable: minor polish only",
                "Deliverable as-is",
            ],
        },
    })
    if state.get("networked"):
        qs["replication_consistent"] = {
            "type": "noul",
            "instructions": {
                "question": "Does `diff` contain a network replication mistake?",
                "criteria": {
                    "true": "Any of: a Replicated UPROPERTY missing from GetLifetimeReplicatedProps; ReplicatedUsing naming a missing OnRep; an RPC without Server/Client/NetMulticast or with an unconsidered Reliable/Unreliable choice; a server-only mutation without an authority check; bReplicates/SetIsReplicatedByDefault not set on the owning actor/component",
                    "false": "None of those mistakes is present",
                },
            },
        }
    return qs


def q_gate_ue5_blueprint(state):
    kind = state.get("artifact_kind", "explanation")
    qs = {
        "graph_misrepresented": {
            "type": "noul",
            "instructions": {
                "question": "Does `artifact` misrepresent `graph`?",
                "inspect": ["graph", "artifact"],
                "focus": "Every event/function block in `graph`, each `→ Pin:` branch under it, and the argument sources of each call",
            },
            "criteria": {
                "true": "Any of: an event, function or named exec branch present in `graph` is missing from, merged into another, or described differently in `artifact`; an argument's source (variable, default value, sub-pin such as ReturnValue_Yaw, component) is stated wrongly; `artifact` describes nodes or behavior `graph` does not contain",
                "false": "Every block and branch of `graph` is accounted for in `artifact` with the right trigger, order, branches and argument sources; omissions are only of cosmetic detail (node positions, comment boxes, reroutes)",
            },
        },
        "findings_resolved": {
            "type": "noul",
            "instructions": {
                "question": "Are all items in `open_findings` resolved in `artifact`?",
                "note": "If `open_findings` is empty, answer yes.",
            },
        },
        "unrequested_scope": {
            "type": "noul",
            "instructions": {
                "question": "Does `artifact` contain material that `task` did not ask for?",
                "note": "Omissions are judged by graph_misrepresented, not here.",
            },
            "criteria": {
                "true": "Advice, refactors, extra features or commentary beyond what `task` and `acceptance` request",
                "false": "Everything in `artifact` serves a requested item",
            },
        },
        "tick_heavy_work": {
            "type": "noul",
            "instructions": {
                "question": "Does `graph` (or a change `artifact` proposes) do expensive work on a per-frame path?",
                "criteria": {
                    "true": "Event Tick, a Timeline Update pin, or a looping timer contains GetAllActorsOfClass/FindObject, Cast chains, SpawnActor, string building, or array copies",
                    "false": "No such work on a per-frame path, or the graph has no per-frame path",
                },
            },
        },
        "readiness": {
            "type": "score",
            "instructions": f"How ready is `artifact` (a {kind}) to hand to the user as the final answer to `task`?",
            "criteria": [
                "Not usable: wrong direction or major gaps",
                "Draft: right direction, needs another revision",
                "Usable: minor polish only",
                "Deliverable as-is",
            ],
        },
    }
    if kind == "migration":
        qs["migration_complete"] = {
            "type": "noul",
            "instructions": {
                "question": "Does the C++ in `artifact` drop or change any behavior of `graph`?",
                "criteria": {
                    "true": "An event/function/branch of `graph` has no C++ counterpart, a trigger differs (input trigger event, overlap, timeline), a variable loses its editability/replication, or a default value changes",
                    "false": "Every block of `graph` maps to C++ with the same trigger, branches, variables and defaults",
                },
            },
        }
    return qs


def _readiness(what):
    return {
        "type": "score",
        "instructions": f"How ready is `artifact` ({what}) to hand to the user as the final answer to `task`?",
        "criteria": [
            "Not usable: wrong direction or major gaps",
            "Draft: right direction, needs another revision",
            "Usable: minor polish only",
            "Deliverable as-is",
        ],
    }


def _findings_resolved(where):
    return {
        "type": "noul",
        "instructions": {
            "question": f"Are all items in `open_findings` resolved in `{where}`?",
            "note": "If `open_findings` is empty, answer yes.",
        },
    }


def _unrequested_scope():
    return {
        "type": "noul",
        "instructions": {"question": "Does `artifact` (and `diff`, if present) contain material that `task` did not ask for?"},
        "criteria": {
            "true": "Advice, refactors, extra features or commentary beyond what `task` and `acceptance` request",
            "false": "Everything serves a requested item",
        },
    }


def q_gate_ue5_perf(state):
    kind = state.get("artifact_kind", "diagnosis")
    qs = {
        "hypothesis_supported": {
            "type": "score",
            "instructions": {
                "question": "Is the bottleneck hypothesis in `artifact` supported by the profiler data in `baseline` (thread p50 times, `top_gamethread`, `ticks`, `hitches`) and, if present, `after.compare`?",
                "inspect": ["artifact", "baseline", "after"],
            },
            "criteria": [
                "Contradicted: the data points at a different thread or stat",
                "Unsupported: plausible but the cited numbers are not in the data",
                "Supported: the named thread/stat dominates in the data",
                "Supported and quantified: the artifact cites the actual numbers and the delta after the change",
            ],
        },
        "numbers_misquoted": {
            "type": "noul",
            "instructions": {
                "question": "Does `artifact` state a measurement that disagrees with `baseline` / `after` (frame or thread times, percentages, hitch counts)?",
                "criteria": {"true": "A quoted number or direction of change is not what the data says", "false": "Every quoted number matches the data within rounding"},
            },
        },
        "findings_resolved": _findings_resolved("artifact"),
        "unrequested_scope": _unrequested_scope(),
        "readiness": _readiness(f"a {kind}"),
    }
    if state.get("diff"):
        qs.update(_q_cpp_defects())
    return qs


def q_gate_ue5_render(state):
    qs = {
        "render_defect": {
            "type": "noul",
            "instructions": {
                "question": "Does `artifact` (or `diff`) contain a rendering mistake?",
                "inspect": ["artifact", "diff", "render"],
                "criteria": {
                    "true": "Any of: an RDG resource used without being registered or a pass whose parameters do not match its shader; a missing SHADER_PARAMETER_STRUCT / IMPLEMENT_GLOBAL_SHADER; an unbounded per-pixel loop or dynamic branching on a divergent value in a hot pass; a translucent/masked blend mode chosen where opaque was required (or the reverse), breaking sorting, Nanite, or Lumen; texture samples inside a loop; precision assumptions (half where float is needed); a material domain/shading model that cannot express the requested effect",
                    "false": "None of those mistakes is present",
                },
            },
        },
        "cost_unreported": {
            "type": "noul",
            "instructions": {
                "question": "Does `artifact` fail to report the shader cost change (pixel/vertex instruction counts, samplers, texture samples) that `render.compare` measured, or report numbers that disagree with it?",
                "criteria": {"true": "No cost statement, or a statement contradicting `render`", "false": "The cost change is stated and matches `render`, or no comparison was requested"},
            },
        },
        "findings_resolved": _findings_resolved("artifact"),
        "unrequested_scope": _unrequested_scope(),
        "readiness": _readiness("a rendering change or explanation"),
    }
    if state.get("diff"):
        qs.update(_q_cpp_defects())
    return qs


PRESETS = {
    "route": q_route,
    "findings": q_findings,
    "gate-leetcode": q_gate_leetcode,
    "gate-general": q_gate_general,
    "gate-paper": q_gate_paper,
    "gate-ue5-cpp": q_gate_ue5_cpp,
    "gate-ue5-blueprint": q_gate_ue5_blueprint,
    "gate-ue5-perf": q_gate_ue5_perf,
    "gate-ue5-render": q_gate_ue5_render,
}

# ---------------------------------------------------------------- decisions (policy in code)

def _norm_score(ans):
    levels = len(ans.get("legend", {})) or 2
    return ans["score"] / (levels - 1)


def decide(preset, answers, state, th):
    if preset == "route":
        r = answers["route"]
        return {
            "workflow": r["choice"] if r["confidence"] >= th["route_min_confidence"] else "ask_user",
            "route_confidence": r["confidence"],
            "needs_web": answers["needs_web"]["noul"] >= 0.5,
            "needs_code_run": answers["needs_code_run"]["noul"] >= 0.5,
            "has_acceptance": answers["has_acceptance"]["noul"] >= 0.5,
            "clarify_first": answers["clarity"]["score"] < 1.0,
            "ue_mode": answers["ue_mode"]["choice"] if r["choice"] == "ue5" else None,
        }
    if preset == "findings":
        kept, dropped = [], []
        for i, f in enumerate(state.get("findings", [])):
            p = answers[f"valid_{i}"]["noul"]
            sev = answers[f"severity_{i}"]
            item = {**f, "p_valid": p, "severity_score": round(sev["score"], 2),
                    "severity_label": sev["legend"][str(round(sev["score"]))].split(":")[0]}
            (kept if p >= th["finding_valid"] else dropped).append(item)
        kept.sort(key=lambda x: -x["severity_score"])
        return {"kept": kept, "dropped": dropped,
                "has_blocking": any(x["severity_score"] >= 2.0 for x in kept)}
    if preset.startswith("gate-"):
        blocking = {k: v["noul"] for k, v in answers.items() if v["type"] == "noul"}
        scores = {k: round(_norm_score(v), 2) for k, v in answers.items() if v["type"] == "score"}
        failed = [k for k, p in blocking.items() if p < th["gate_pass"]]
        low = [k for k, s in scores.items() if s < (th["hypothesis_min"] if k == "hypothesis_supported" else th["readiness_pass"])]
        # paper gate: per-claim checks are informative; only overclaiming/logic_gaps block (inverted: yes == bad)
        if preset == "gate-paper":
            bad = [k for k in ("overclaiming", "logic_gaps") if blocking.get(k, 0) >= 0.5]
            unsupported = [k for k, p in blocking.items() if k.startswith("supported_") and p < th["gate_pass"]]
            failed = bad + unsupported
        if preset.startswith("gate-ue5-"):
            # defect questions are inverted (yes == bad): fail when the model is at least unsure
            # inverted defect questions (yes == defect) block at their family threshold; soft ones only warn at p >= 0.5
            cpp_defects = ("reflection_correct", "lifecycle_correct", "no_editor_only_leak", "replication_consistent", "render_defect")
            bp_defects = ("graph_misrepresented", "migration_complete", "numbers_misquoted")
            soft_qs = ("unrequested_scope", "tick_heavy_work", "cost_unreported")
            bad = [k for k in cpp_defects if k in blocking and blocking[k] >= th["cpp_defect_max"]]
            bad += [k for k in bp_defects if k in blocking and blocking[k] >= th["bp_fidelity_max"]]
            ok = [k for k, p in blocking.items() if k in ("findings_resolved", "scope_respected") and p < th["gate_pass"]]
            failed = bad + ok
            warnings = [k for k in soft_qs if k in blocking and blocking[k] >= 0.5]
        evidence, warnings = {}, locals().get("warnings", [])
        if preset.startswith("gate-ue5-"):
            # Hard evidence is checked in code, never by the model: ue_build.sh / ue_test.sh / ue_bp_export.sh outputs in state.
            b, t, e = state.get("build"), state.get("tests"), state.get("export")
            if preset == "gate-ue5-cpp" or b is not None:
                evidence["build_ok"] = bool(b) and b.get("result") == "Succeeded" and not b.get("errors")
            if preset == "gate-ue5-cpp" or t is not None:
                evidence["tests_ok"] = bool(t) and t.get("result") == "Passed" and (t.get("total") or 0) > 0
            if preset == "gate-ue5-blueprint":
                assets = (e or {}).get("assets") or []
                evidence["bp_compiles"] = bool(assets) and all((a.get("compile") or {}).get("status") in ("BS_UP_TO_DATE", "BS_UP_TO_DATE_WITH_WARNINGS") for a in assets)
                evidence["export_complete"] = bool(assets) and all(not a.get("errors") and not (a.get("coverage") or {}).get("unreached") for a in assets)
            if preset == "gate-ue5-perf":
                base, after = state.get("baseline") or {}, state.get("after") or {}
                evidence["baseline_captured"] = base.get("result") == "Passed" and (base.get("frames_used") or 0) >= 100
                if state.get("artifact_kind", "diagnosis") == "optimization":
                    cmp_ = after.get("compare") or {}
                    evidence["same_conditions"] = bool(cmp_) and cmp_.get("same_conditions", False) and after.get("map") == base.get("map") and after.get("resolution") == base.get("resolution")
                    evidence["improvement_real"] = cmp_.get("verdict") == "better"
            if preset == "gate-ue5-render":
                r = state.get("render") or {}
                evidence["shader_compiles"] = r.get("result") == "Passed" and not r.get("shader_errors")
            failed += [k for k, ok in evidence.items() if not ok]
        return {"pass": not failed and not low, "failed_checks": failed, "low_scores": low,
                "checks": blocking, "scores": scores,
                **({"evidence": evidence} if evidence else {}), **({"warnings": warnings} if warnings else {})}
    return {}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # ensure_ascii=False output must not depend on the console code page
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preset", choices=PRESETS.keys())
    ap.add_argument("--questions", help="raw questions JSON, @file, or -")
    ap.add_argument("--state", required=True, help="state JSON, @file, or - for stdin")
    ap.add_argument("--threshold", action="append", default=[], metavar="NAME=VALUE")
    ap.add_argument("--raw", action="store_true", help="print the raw API response only")
    a = ap.parse_args()
    if not a.preset and not a.questions:
        ap.error("--preset or --questions required")

    th = dict(THRESHOLDS)
    for t in a.threshold:
        k, v = t.split("=", 1)
        th[k] = float(v)

    state = _load(a.state)
    questions = PRESETS[a.preset](state) if a.preset else _load(a.questions)
    if not questions:
        print(json.dumps({"answers": {}, "decision": {"kept": [], "dropped": [], "has_blocking": False}, "usage": {}}))
        return
    resp = ask(state, questions)
    if a.raw:
        print(json.dumps(resp, ensure_ascii=False, indent=2))
        return
    out = {"model": resp["model"], "answers": resp["answers"],
           "decision": decide(a.preset, resp["answers"], state, th) if a.preset else {},
           "usage": resp.get("usage")}
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
