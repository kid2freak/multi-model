#!/usr/bin/env bash
# Export Blueprints headlessly to T3D + pseudo-code (bp.md) + model (bp.json), and record whether they compile.
#
# Usage: ue_bp_export.sh <Project.uproject|dir> --asset /Game/Path/BP_Foo [--asset ...] [--out DIR]
#                        [--timeout SEC] [--no-compile] [--dsl]
#   --asset      object path without the trailing .BP_Foo (e.g. /Game/ThirdPerson/Blueprints/BP_ThirdPersonCharacter)
#   --no-compile skip the recompile check
#   --dsl        also write Epic's EditorToolset DSL view (lossy; needs UE 5.8 EditorToolset, enabled on the command line)
# Writes DIR/<Name>.t3d, DIR/<Name>.bp.json, DIR/<Name>.bp.md, DIR/export.json, DIR/stdout.log, DIR/editor.log.
# Exit code: 0 all assets exported (and compiled, unless --no-compile), 1 otherwise, 124 timeout.
# Runs UnrealEditor-Cmd with -run=pythonscript and -EnablePlugins=PythonScriptPlugin; the .uproject is not modified.
# Default DIR: ~/.multi-model/runs/ue5/<project>/bp-<timestamp>/. Default timeout: $UE_TEST_TIMEOUT or 900.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

proj=""; assets=(); out=""; timeout_s="${UE_TEST_TIMEOUT:-900}"; compile=true; dsl=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --asset) assets+=("$2"); shift 2;;
    --out) out="$2"; shift 2;;
    --timeout) timeout_s="$2"; shift 2;;
    --no-compile) compile=false; shift;;
    --dsl) dsl=true; shift;;
    -h|--help) sed -n '2,12p' "$0"; exit 0;;
    -*) echo "unknown arg: $1" >&2; exit 2;;
    *) proj="$1"; shift;;
  esac
done
[[ -n "$proj" && ${#assets[@]} -gt 0 ]] || { echo "project and at least one --asset required" >&2; exit 2; }
eval "$("$HERE/ue_env.sh" "$proj")"
[[ -n "$out" ]] || out="$HOME/.multi-model/runs/ue5/$UE_PROJECT_NAME/bp-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$out"
to_win() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }

# assets go through the environment, base64-encoded: Git Bash rewrites anything that looks like a POSIX path
# ("/Game/...") into a Windows path, both in arguments and in environment values, when launching native programs.
UE_BP_ASSETS_B64="$(printf '%s
' "${assets[@]}" | base64 -w0)" "$PY" - "$out" "$compile" "$dsl" <<'PY'
import base64, json, os, sys, subprocess
out, compile_, dsl = sys.argv[1:4]
assets = [l.strip() for l in base64.b64decode(os.environ["UE_BP_ASSETS_B64"]).decode().splitlines() if l.strip()]
def win(p):
    try: return subprocess.run(["cygpath", "-w", p], capture_output=True, text=True).stdout.strip() or p
    except Exception: return p
json.dump({"assets": assets, "out": win(out), "compile": compile_ == "true", "dsl": dsl == "true"},
          open(f"{out}/config.json", "w", encoding="utf-8"))
PY

plugins="PythonScriptPlugin"
[[ $dsl == true ]] && plugins="$plugins,ToolsetRegistry,EditorToolset"
cmd=("$UE_EDITOR_CMD" "$UE_PROJECT" -run=pythonscript "-script=$(to_win "$HERE/ue_bp_export.py")"
     "-EnablePlugins=$plugins" -unattended -nopause -nosplash -nullrhi -NoSound -stdout -FullStdOutLogOutput
     "-log=$(to_win "$out/editor.log")")
printf '%s ' "${cmd[@]}" > "$out/command.txt"
rc=0
UE_BP_EXPORT_CONFIG="$(to_win "$out/config.json")" timeout --foreground "$timeout_s" "${cmd[@]}" > "$out/stdout.log" 2>&1 || rc=$?

"$PY" - "$out" "$rc" "$compile" <<'PY'
import json, os, re, sys
out, rc, compile_ = sys.argv[1], int(sys.argv[2]), sys.argv[3] == "true"
p = os.path.join(out, "export.json")
if not os.path.exists(p):
    log = open(os.path.join(out, "stdout.log"), "rb").read().decode("utf-8", errors="replace")
    tail = [l for l in log.splitlines() if "LogPython" in l or "Error" in l][-15:]
    print(json.dumps({"result": "Timeout" if rc == 124 else "Crashed", "exit_code": rc, "log_tail": tail}, ensure_ascii=False, indent=2))
    sys.exit(124 if rc == 124 else 1)
d = json.load(open(p, encoding="utf-8"))
# attach the compiler's own messages for each asset from the editor log
log = open(os.path.join(out, "stdout.log"), "rb").read().decode("utf-8", errors="replace")
for a in d["assets"]:
    name = a["asset"].rsplit("/", 1)[-1]
    msgs = sorted({m.group(1).strip() for m in re.finditer(r"LogBlueprint: (?:Error|Warning): \[AssetLog\] [^\n]*?" + re.escape(name) + r"\.uasset: (\[Compiler\][^\n]*)", log)})
    a["compiler_messages"] = msgs
ok = all(not a.get("errors") and a.get("t3d") and (not compile_ or (a.get("compile") or {}).get("status") in ("BS_UP_TO_DATE", "BS_UP_TO_DATE_WITH_WARNINGS")) for a in d["assets"])
d["result"] = "Passed" if ok else "Failed"
d["exit_code"] = rc
json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps({k: d[k] for k in ("result", "exit_code")} | {"assets": [{k: a.get(k) for k in ("asset", "compile", "coverage", "graphs", "errors", "compiler_messages", "seconds")} for a in d["assets"]]}, ensure_ascii=False, indent=2))
sys.exit(0 if ok else 1)
PY
