#!/usr/bin/env python3
"""TypeSafe (Jev) judge: typed routing and pass/fail gates for the multi-model workflow.

Usage:
  judge.py --preset route     --state '{"request": "..."}'
  judge.py --preset findings  --state @state.json      # {"artifact": "...", "findings": [...]}
  judge.py --preset gate-leetcode|gate-general|gate-paper --state @state.json
  judge.py --preset gate-ue5-cpp --state @state.json   # {"task","acceptance","diff","build","tests","open_findings","networked"}
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
import urllib.error
import urllib.request

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"

THRESHOLDS = {
    "route_min_confidence": 0.6,   # below this the router asks the user instead of guessing
    "finding_valid": 0.6,          # Noul >= this keeps a Grok finding
    "gate_pass": 0.75,             # every blocking Noul must reach this to pass
    "readiness_pass": 0.7,         # normalized Score (0..1) required to deliver
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
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"TypeSafe HTTP {e.code}: {e.read().decode()[:500]}")


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


def q_gate_ue5_cpp(state):
    qs = {
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
    }
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


PRESETS = {
    "route": q_route,
    "findings": q_findings,
    "gate-leetcode": q_gate_leetcode,
    "gate-general": q_gate_general,
    "gate-paper": q_gate_paper,
    "gate-ue5-cpp": q_gate_ue5_cpp,
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
        low = [k for k, s in scores.items() if s < th["readiness_pass"]]
        # paper gate: per-claim checks are informative; only overclaiming/logic_gaps block (inverted: yes == bad)
        if preset == "gate-paper":
            bad = [k for k in ("overclaiming", "logic_gaps") if blocking.get(k, 0) >= 0.5]
            unsupported = [k for k, p in blocking.items() if k.startswith("supported_") and p < th["gate_pass"]]
            failed = bad + unsupported
        if preset.startswith("gate-ue5-"):
            # defect questions are inverted (yes == bad): fail when the model is at least unsure
            bad = [k for k in ("reflection_correct", "lifecycle_correct", "no_editor_only_leak", "replication_consistent")
                   if k in blocking and blocking[k] >= 1 - th["gate_pass"]]
            ok = [k for k, p in blocking.items() if k in ("findings_resolved", "scope_respected") and p < th["gate_pass"]]
            failed = bad + ok
        evidence = {}
        if preset.startswith("gate-ue5-"):
            # Hard evidence is checked in code, never by the model: ue_build.sh / ue_test.sh outputs in state.
            b, t = state.get("build") or {}, state.get("tests") or {}
            evidence["build_ok"] = b.get("result") == "Succeeded" and not b.get("errors")
            evidence["tests_ok"] = t.get("result") == "Passed" and (t.get("total") or 0) > 0
            failed += [k for k, ok in evidence.items() if not ok]
        return {"pass": not failed and not low, "failed_checks": failed, "low_scores": low,
                "checks": blocking, "scores": scores, **({"evidence": evidence} if evidence else {})}
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
