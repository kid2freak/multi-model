#!/usr/bin/env python3
"""TypeSafe (Jev) judge: typed routing and pass/fail gates for the multi-model workflow.

Usage:
  judge.py --preset route     --state '{"request": "..."}'
  judge.py --preset findings  --state @state.json      # {"artifact": "...", "findings": [...]}
  judge.py --preset gate-leetcode|gate-general|gate-paper --state @state.json
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


def _api_key():
    key = os.environ.get("TYPESAFE_API_KEY")
    if key:
        return key
    zshrc = os.path.expanduser("~/.zshrc")
    if os.path.exists(zshrc):
        m = re.search(r'^export TYPESAFE_API_KEY=["\']?([^"\'\s]+)', open(zshrc).read(), re.M)
        if m:
            return m.group(1)
    sys.exit("TYPESAFE_API_KEY not set (env or ~/.zshrc)")


def _load(arg):
    if arg == "-":
        return json.load(sys.stdin)
    if arg.startswith("@"):
        with open(arg[1:]) as f:
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
                "general": "Anything else: scripts, plugins, tools, prose editing, planning, Q&A",
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


PRESETS = {
    "route": q_route,
    "findings": q_findings,
    "gate-leetcode": q_gate_leetcode,
    "gate-general": q_gate_general,
    "gate-paper": q_gate_paper,
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
        return {"pass": not failed and not low, "failed_checks": failed, "low_scores": low,
                "checks": blocking, "scores": scores}
    return {}


def main():
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
