#!/usr/bin/env bash
# Render-mode evidence: compile shaders headlessly (real RHI via -AllowCommandletRendering), record material
# statistics and every shader-compiler error/warning, optionally compared with a baseline.
#
# Usage: ue_render_check.sh <Project.uproject|dir> [--asset /Game/Path/M_Foo]... [--compare baseline/render.json]
#                           [--out DIR] [--timeout SEC] [--no-recompile]
#   --asset      Material / MaterialInstance / MaterialFunction object paths (no --asset: only the global/startup
#                shader compile is checked, which is what matters for .usf/.ush edits)
#   --compare    a previous render.json (baseline); render.json then carries per-material deltas and a verdict
# Writes DIR/materials.json (raw), DIR/render.json, DIR/stdout.log, DIR/editor.log, DIR/command.txt.
# Exit code: 0 no shader errors and all assets loaded, 1 otherwise, 124 timeout. First run on a project can take
# minutes (shader compilation fills the DDC); later runs are fast.
# Default DIR: ~/.multi-model/runs/ue5/<project>/render-<timestamp>/. Default timeout: $UE_TEST_TIMEOUT or 900.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

proj=""; assets=(); out=""; compare=""; timeout_s="${UE_TEST_TIMEOUT:-900}"; recompile=true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --asset) assets+=("$2"); shift 2;;
    --out) out="$2"; shift 2;;
    --compare) compare="$2"; shift 2;;
    --timeout) timeout_s="$2"; shift 2;;
    --no-recompile) recompile=false; shift;;
    -h|--help) sed -n '2,14p' "$0"; exit 0;;
    -*) echo "unknown arg: $1" >&2; exit 2;;
    *) proj="$1"; shift;;
  esac
done
[[ -n "$proj" ]] || { echo "project required" >&2; exit 2; }
eval "$("$HERE/ue_env.sh" "$proj")"
[[ -n "$out" ]] || out="$HOME/.multi-model/runs/ue5/$UE_PROJECT_NAME/render-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$out"
to_win() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }

# asset paths base64-encoded through the environment: Git Bash rewrites "/Game/..." values otherwise
UE_ASSETS_B64="$( { [[ ${#assets[@]} -gt 0 ]] && printf '%s\n' "${assets[@]}"; } | base64 -w0)" "$PY" - "$out" "$recompile" <<'PY'
import base64, json, os, sys, subprocess
out, recompile = sys.argv[1:3]
assets = [l.strip() for l in base64.b64decode(os.environ.get("UE_ASSETS_B64", "")).decode().splitlines() if l.strip()]
def win(p):
    try: return subprocess.run(["cygpath", "-w", p], capture_output=True, text=True).stdout.strip() or p
    except Exception: return p
json.dump({"assets": assets, "out": win(out), "recompile": recompile == "true"}, open(f"{out}/config.json", "w", encoding="utf-8"))
PY

cmd=("$UE_EDITOR_CMD" "$UE_PROJECT" -run=pythonscript "-script=$(to_win "$HERE/ue_material_stats.py")"
     -EnablePlugins=PythonScriptPlugin -AllowCommandletRendering -unattended -nopause -nosplash -NoSound
     -stdout -FullStdOutLogOutput "-log=$(to_win "$out/editor.log")")
printf '%s ' "${cmd[@]}" > "$out/command.txt"
rc=0; t0=$SECONDS
UE_MAT_CONFIG="$(to_win "$out/config.json")" timeout --foreground "$timeout_s" "${cmd[@]}" > "$out/stdout.log" 2>&1 || rc=$?
wall=$((SECONDS - t0))

args=("$(to_win "$out")" "$rc" "$wall")
[[ -n "$compare" ]] && args+=("$(to_win "$compare")")
MSYS_NO_PATHCONV=1 "$PY" - "${args[@]}" <<'PY'
import json, os, re, sys
out, rc, wall = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
compare = sys.argv[4] if len(sys.argv) > 4 else None
log = open(os.path.join(out, "stdout.log"), "rb").read().decode("utf-8", errors="replace")
msgs = []
for m in re.finditer(r"^(?:\[[^\]]*\]\[\s*\d+\])?(LogShaderCompilers|LogMaterial|LogShaders): (Error|Warning|Fatal): (.*)$", log, re.M):
    txt = m.group(3).strip()
    if "Failed to load" in txt and ".dll" in txt:
        continue
    msgs.append({"category": m.group(1), "level": m.group(2), "message": txt[:400]})
hlsl = sorted({l.strip()[:400] for l in log.splitlines()
               if re.search(r"error X\d{4}|\.(?:usf|ush)\(\d+,\d+(?:-\d+)?\): error|\.(?:usf|ush):\d+:\d+: error:", l)})   # FXC and DXC styles
failed_mats = sorted({m.group(1) for m in re.finditer(r"Failed to compile Material (\S+?)\.\w+ for platform", log)})
raw_p = os.path.join(out, "materials.json")
raw = json.load(open(raw_p, encoding="utf-8")) if os.path.exists(raw_p) else {"assets": []}
rhi = re.search(r"RHI (\S+) with Feature Level (\S+) is supported", log)
crashed = bool(re.search(r"=== Critical error: ===|Fatal error|Assertion failed", log))
errors = [m for m in msgs if m["level"] in ("Error", "Fatal") or m["message"].startswith("Failed to compile Material")]
errors += [{"category": "HLSL", "level": "Error", "message": h} for h in hlsl]
for a in raw["assets"]:
    st = a.get("statistics") or {}
    a["compile_failed"] = a["asset"] in failed_mats or st.get("num_samplers", 0) < 0
    if a["compile_failed"]:
        a.setdefault("errors", []).append("shader compilation failed (see shader_errors)")
asset_ok = all(not a.get("errors") for a in raw["assets"])
result = "Timeout" if rc == 124 and not raw["assets"] else "Crashed" if crashed else ("Passed" if not errors and asset_ok else "Failed")
d = {"result": result, "exit_code": rc, "wall_seconds": wall, "rhi": f"{rhi.group(1)} {rhi.group(2)}" if rhi else None,
     "shader_errors": errors, "shader_warnings": [m for m in msgs if m["level"] == "Warning"][:50],
     "materials": raw["assets"], "log": os.path.join(out, "stdout.log")}
if compare:
    base = json.load(open(compare, encoding="utf-8"))
    bmap = {a["asset"]: a for a in base.get("materials", [])}
    deltas, verdicts = [], []
    for a in raw["assets"]:
        b = bmap.get(a["asset"])
        if not b or "statistics" not in a or "statistics" not in b:
            continue
        row = {"asset": a["asset"]}
        for k in ("num_pixel_shader_instructions", "num_vertex_shader_instructions", "num_samplers", "num_pixel_texture_samples", "num_vertex_texture_samples"):
            if k in a["statistics"] and k in b["statistics"]:
                row[k] = {"base": b["statistics"][k], "after": a["statistics"][k], "delta": a["statistics"][k] - b["statistics"][k]}
        ps = row.get("num_pixel_shader_instructions", {}).get("delta", 0); ts = row.get("num_pixel_texture_samples", {}).get("delta", 0)
        row["verdict"] = "broken" if a.get("compile_failed") else "worse" if ps > 0 or ts > 0 else "better" if ps < 0 or ts < 0 else "same"
        verdicts.append(row["verdict"]); deltas.append(row)
    d["compare"] = {"baseline": compare, "materials": deltas,
                    "verdict": "broken" if "broken" in verdicts else "mixed" if "worse" in verdicts and "better" in verdicts else "worse" if "worse" in verdicts else "better" if "better" in verdicts else "same"}
json.dump(d, open(os.path.join(out, "render.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
brief = {"result": result, "rhi": d["rhi"], "wall_seconds": wall, "shader_errors": len(errors), "shader_warnings": len(d["shader_warnings"]),
         "materials": [{"asset": a["asset"].rsplit("/", 1)[-1], "ps_instr": (a.get("statistics") or {}).get("num_pixel_shader_instructions"),
                        "vs_instr": (a.get("statistics") or {}).get("num_vertex_shader_instructions"), "samplers": (a.get("statistics") or {}).get("num_samplers"),
                        "errors": a.get("errors")} for a in raw["assets"]]}
if "compare" in d: brief["compare"] = d["compare"]["verdict"]
sys.stdout.reconfigure(encoding="utf-8"); print(json.dumps(brief, ensure_ascii=False, indent=2))
sys.exit(0 if result == "Passed" else 124 if result == "Timeout" else 1)
PY
echo "$out/render.json" >&2
