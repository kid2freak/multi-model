#!/usr/bin/env bash
# Grok (grok-4.6 via grok CLI headless) as adversarial reviewer / second-source researcher.
#
# Usage:
#   grok_review.sh --role challenger|critic|reviewer|researcher [--file F]... [--context TEXT|@FILE]
#                  [--effort low|medium|high] [--web] [--raw]
#
# Files are concatenated into the prompt with headers. Prints the structured JSON answer only
# (see roles/*.schema.json); --raw prints the full grok CLI envelope.
# Roles live in roles/<role>.md; add a new role by adding a file.
# Measured 2026-09-20 (grok-4.6, effort medium): review roles 10-120s, ~$0.02-0.03/call;
# researcher (web search on) ~150s, ~$0.4/call -> call it once per task, not per revision.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GROK_BIN="${GROK_BIN:-$HOME/.grok/bin/grok}"
MODEL="${GROK_MODEL:-grok-4.6}"

role=""; files=(); context=""; effort="medium"; web=0; raw=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --role) role="$2"; shift 2;;
    --file) files+=("$2"); shift 2;;
    --context) context="$2"; shift 2;;
    --effort) effort="$2"; shift 2;;
    --web) web=1; shift;;
    --raw) raw=1; shift;;
    -h|--help) sed -n '2,12p' "$0"; exit 0;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
[[ -z "$role" ]] && { echo "--role required" >&2; exit 2; }
role_file="$HERE/roles/$role.md"
[[ -f "$role_file" ]] || { echo "unknown role: $role (no $role_file)" >&2; exit 2; }
[[ "$context" == @* ]] && context="$(cat "${context:1}")"

case "$role" in
  researcher) schema="$HERE/roles/research.schema.json"; web=1;;
  *)          schema="$HERE/roles/review.schema.json";;
esac

prompt="$(cat "$role_file")"
prompt+=$'\n\nAnswer in the same language as the material under review. Respond ONLY with JSON matching the required schema.\n'
[[ -n "$context" ]] && prompt+=$'\n## Context\n'"$context"$'\n'
for f in ${files[@]+"${files[@]}"}; do   # bash 3.2: empty array is "unbound" under set -u
  [[ -f "$f" ]] || { echo "no such file: $f" >&2; exit 2; }
  prompt+=$'\n## File: '"$f"$'\n```\n'"$(cat "$f")"$'\n```\n'
done

args=(--model "$MODEL" -p "$prompt" --json-schema "$(cat "$schema")" --always-approve
      --effort "$effort" --no-plan --no-subagents)
if [[ $web -eq 1 ]]; then args+=(--tools "web_search,web_fetch"); else args+=(--disable-web-search --tools ""); fi

out="$("$GROK_BIN" "${args[@]}" 2>/tmp/grok_review.err)" || { echo "grok failed:" >&2; cat /tmp/grok_review.err >&2; exit 1; }
if [[ $raw -eq 1 ]]; then echo "$out"; exit 0; fi
python3 - "$out" <<'PY'
import json, sys
d = json.loads(sys.argv[1])
so = d.get("structuredOutput")
if so is None:
    # fall back to parsing the text body
    try: so = json.loads(d.get("text", ""))
    except Exception: sys.exit("no structuredOutput in grok response:\n" + json.dumps(d)[:800])
so["_meta"] = {"model": next(iter(d.get("modelUsage", {"?": 0}))), "cost_usd": d.get("total_cost_usd"),
               "input_tokens": d.get("usage", {}).get("input_tokens"), "output_tokens": d.get("usage", {}).get("output_tokens")}
print(json.dumps(so, ensure_ascii=False, indent=2))
PY
