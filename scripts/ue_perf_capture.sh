#!/usr/bin/env bash
# Capture a CSV Profiler run of a map in standalone -game mode (real RHI, windowed) and summarize it.
#
# Usage: ue_perf_capture.sh <Project.uproject|dir> --map /Game/Path/Map [--frames N] [--warmup N] [--out DIR]
#                           [--res WxH] [--label NAME] [--compare baseline/perf.json] [--timeout SEC] [--extra "-flag ..."]
#   --frames   frames to capture from boot (default 900; the first --warmup frames, default 120, are engine init + map load)
#   --compare  a previous perf.json (the baseline); perf.json then carries a code-computed verdict
#   --extra    one extra command-line argument for the game; repeatable (e.g. --extra "-ExecCmds=r.ScreenPercentage 50")
# Writes DIR/capture.csv, DIR/perf.json, DIR/stdout.log, DIR/game.log, DIR/command.txt.
# Exit code: 0 capture ok, 1 no CSV produced, 124 timeout. Same-machine, same-map, same-resolution captures only are
# comparable; run the baseline and the candidate back to back with nothing else running.
# Default DIR: ~/.multi-model/runs/ue5/<project>/perf-<label>-<timestamp>/. Default timeout: $UE_TEST_TIMEOUT or 900.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

proj=""; map=""; frames=900; warmup=120; out=""; res="1280x720"; label="capture"; compare=""; timeout_s="${UE_TEST_TIMEOUT:-900}"; extra=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --map) map="$2"; shift 2;;
    --frames) frames="$2"; shift 2;;
    --warmup) warmup="$2"; shift 2;;
    --out) out="$2"; shift 2;;
    --res) res="$2"; shift 2;;
    --label) label="$2"; shift 2;;
    --compare) compare="$2"; shift 2;;
    --timeout) timeout_s="$2"; shift 2;;
    --extra) extra+=("$2"); shift 2;;
    -h|--help) sed -n '2,14p' "$0"; exit 0;;
    -*) echo "unknown arg: $1" >&2; exit 2;;
    *) proj="$1"; shift;;
  esac
done
[[ -n "$proj" && -n "$map" ]] || { echo "project and --map required" >&2; exit 2; }
eval "$("$HERE/ue_env.sh" "$proj")"
[[ -n "$out" ]] || out="$HOME/.multi-model/runs/ue5/$UE_PROJECT_NAME/perf-$label-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$out"
to_win() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }
resx="${res%x*}"; resy="${res#*x}"

# MSYS_NO_PATHCONV: Git Bash would rewrite the "/Game/..." map path into a Windows path
cmd=("$UE_EDITOR_CMD" "$UE_PROJECT" "$map" -game -windowed "-ResX=$resx" "-ResY=$resy" -unattended -nosplash -NoSound
     "-csvCaptureFrames=$frames" -ExitAfterCsvProfiling -csvGpuStats -stdout -FullStdOutLogOutput "-log=$(to_win "$out/game.log")")
[[ ${#extra[@]} -gt 0 ]] && cmd+=("${extra[@]}")
printf '%s ' "${cmd[@]}" > "$out/command.txt"
rc=0; t0=$SECONDS
MSYS_NO_PATHCONV=1 timeout --foreground "$timeout_s" "${cmd[@]}" > "$out/stdout.log" 2>&1 || rc=$?
wall=$((SECONDS - t0))

csv_win="$(grep -a -oE 'Writing CSV to file : .*\.csv' "$out/stdout.log" | tail -1 | sed 's/Writing CSV to file : //' | tr -d '\r')"
if [[ -z "$csv_win" ]]; then
  reason="no CSV written"; grep -a -E "Failed to enter|Fatal|Assertion" "$out/stdout.log" | head -3 >&2
  echo "{\"result\": \"$([[ $rc -eq 124 ]] && echo Timeout || echo Failed)\", \"reason\": \"$reason\", \"exit_code\": $rc, \"wall_seconds\": $wall}" | tee "$out/perf.json"
  exit $([[ $rc -eq 124 ]] && echo 124 || echo 1)
fi
cp "$(cygpath -u "$csv_win" 2>/dev/null || printf '%s' "$csv_win")" "$out/capture.csv"

args=("$(to_win "$out/capture.csv")" --warmup "$warmup" --json "$(to_win "$out/perf.json")")
[[ -n "$compare" ]] && args+=(--compare "$(to_win "$compare")")
"$PY" "$HERE/ue_csv_summary.py" "${args[@]}" > /dev/null
MSYS_NO_PATHCONV=1 "$PY" - "$(to_win "$out/perf.json")" "$label" "$map" "$res" "$wall" "$rc" <<'PY'
import json, sys
p, label, m, res, wall, rc = sys.argv[1:]
d = json.load(open(p, encoding="utf-8"))
d.update({"result": "Passed", "label": label, "map": m, "resolution": res, "wall_seconds": int(wall), "exit_code": int(rc)})
json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
brief = {k: d[k] for k in ("result", "label", "map", "frames", "frames_used", "hitches", "wall_seconds")}
brief["p50_ms"] = {k: v["p50"] for k, v in d["stats"].items() if k in ("FrameTime", "GameThreadTime", "RenderThreadTime", "GPUTime", "RHIThreadTime")}
if "compare" in d: brief["compare"] = {"verdict": d["compare"]["verdict"], "metrics": {k: v["verdict"] + f" ({v['delta_pct']}%)" for k, v in d["compare"]["metrics"].items()}}
sys.stdout.reconfigure(encoding="utf-8"); print(json.dumps(brief, ensure_ascii=False, indent=2))
PY
echo "$out/perf.json" >&2
