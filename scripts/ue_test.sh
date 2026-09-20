#!/usr/bin/env bash
# Run Unreal Automation tests headlessly (UnrealEditor-Cmd, -nullrhi) and write test.json.
#
# Usage: ue_test.sh <Project.uproject|dir> --filter "<test path prefix>" [--out DIR] [--timeout SEC] [--rhi]
#   --filter   Automation test path filter, e.g. "Kurodemo." or "System.Core.Math" (as in `Automation RunTests <filter>`)
#   --rhi      keep a real RHI (default -nullrhi; needed for rendering/screenshot tests)
# Writes DIR/automation.log, DIR/stdout.log, DIR/report/index.json (engine report) and DIR/test.json:
#   {result: "Passed"|"Failed"|"NoTests"|"Timeout"|"Crashed", filter, found, succeeded, failed, not_run, total,
#    duration, wall_seconds, exit_code, tests: [{path, state, duration, errors:[...], warnings}], report, log}
# Exit code: 0 Passed, 1 Failed/NoTests/Crashed, 124 Timeout. The engine's own exit code is NOT trusted:
# the verdict comes from report/index.json (M0: failing tests can still exit 0).
# Default DIR: ~/.multi-model/runs/ue5/<project>/test-<timestamp>/. Default timeout: $UE_TEST_TIMEOUT or 900.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

proj=""; filter=""; out=""; timeout_s="${UE_TEST_TIMEOUT:-900}"; rhi="-nullrhi"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --filter) filter="$2"; shift 2;;
    --out) out="$2"; shift 2;;
    --timeout) timeout_s="$2"; shift 2;;
    --rhi) rhi=""; shift;;
    -h|--help) sed -n '2,14p' "$0"; exit 0;;
    -*) echo "unknown arg: $1" >&2; exit 2;;
    *) proj="$1"; shift;;
  esac
done
[[ -n "$proj" && -n "$filter" ]] || { echo "project and --filter required" >&2; exit 2; }
eval "$("$HERE/ue_env.sh" "$proj")"
[[ -n "$out" ]] || out="$HOME/.multi-model/runs/ue5/$UE_PROJECT_NAME/test-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$out/report"
to_win() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }
report_win="$(to_win "$out/report")"; log_win="$(to_win "$out/automation.log")"

cmd=("$UE_EDITOR_CMD" "$UE_PROJECT" "-ExecCmds=Automation RunTests $filter; Quit"
     -unattended -nopause -nosplash -NoSound -stdout -FullStdOutLogOutput
     "-log=$log_win" "-ReportExportPath=$report_win" "-TestExit=Automation Test Queue Empty")
[[ -n "$rhi" ]] && cmd+=("$rhi")
printf '%s ' "${cmd[@]}" > "$out/command.txt"
rc=0; t0=$SECONDS
timeout --foreground "$timeout_s" "${cmd[@]}" > "$out/stdout.log" 2>&1 || rc=$?
wall=$((SECONDS - t0))

"$PY" - "$out" "$filter" "$rc" "$wall" "$timeout_s" <<'PY'
import json, os, re, sys
out, filt, rc, wall, tmo = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
stdout = open(os.path.join(out, "stdout.log"), "rb").read().decode("utf-8", errors="replace")
found = re.search(r"Found (\d+) automation tests based on", stdout)
found = int(found.group(1)) if found else None
idx = os.path.join(out, "report", "index.json")
rep = json.load(open(idx, encoding="utf-8-sig")) if os.path.exists(idx) else None
tests = []
if rep:
    for t in rep.get("tests", []):
        errs = [e.get("message", "") for e in t.get("entries", []) if str(e.get("event", {}).get("type", "")).lower() in ("error", "fatal")]
        if not errs:
            errs = [e.get("message", "") for e in t.get("entries", []) if "error" in json.dumps(e).lower()]
        tests.append({"path": t.get("fullTestPath"), "state": t.get("state"), "duration": t.get("duration"),
                      "errors": errs[:10], "warnings": t.get("warnings", 0)})
crashed = bool(re.search(r"=== Critical error: ===|Fatal error|Assertion failed", stdout))
if rc == 124 and not rep:
    result = "Timeout"
elif crashed and not rep:
    result = "Crashed"
elif not rep or (found == 0):
    result = "NoTests"
elif rep.get("failed", 0) > 0 or rep.get("notRun", 0) > 0 or any(t["state"] != "Success" for t in tests):
    result = "Failed"
else:
    result = "Passed"
json.dump({
    "result": result, "filter": filt, "found": found,
    "succeeded": rep.get("succeeded") if rep else 0, "failed": rep.get("failed") if rep else 0,
    "not_run": rep.get("notRun") if rep else 0, "total": len(tests),
    "duration": rep.get("totalDuration") if rep else None, "wall_seconds": wall, "exit_code": rc,
    "crashed": crashed, "tests": tests, "report": idx if rep else None,
    "log": os.path.join(out, "automation.log"), "command": open(os.path.join(out, "command.txt"), encoding="utf-8").read().strip(),
}, open(os.path.join(out, "test.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PY

result="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["result"])' "$out/test.json")"
echo "$out/test.json" >&2
cat "$out/test.json"
case "$result" in Passed) exit 0;; Timeout) exit 124;; *) exit 1;; esac
