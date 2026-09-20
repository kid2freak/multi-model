#!/usr/bin/env bash
# Build a UE project target with UnrealBuildTool and turn the log into build.json.
#
# Usage: ue_build.sh <Project.uproject|dir> [--target Editor|Game] [--config Development|DebugGame|Shipping]
#                    [--out DIR] [--timeout SEC] [--force-rescan]
# Writes DIR/build.log (raw) and DIR/build.json:
#   {result: "Succeeded"|"Failed"|"Timeout", reason, exit_code, seconds, actions, up_to_date,
#    parallel_limited_to, errors: [{file,line,col,kind,code,msg}], warnings: [...], command, log}
# Exit code: 0 on Succeeded, 1 on Failed, 124 on timeout, 2 on usage error.
# Stale-makefile guards (both re-run once with -NoUBTMakefiles): "Target is up to date" while a Source file is
# newer than the last .target file; or C1083 (a deleted source file still listed). Both happen when files are
# created/deleted within the same second as the previous build.
# Default DIR: ~/.multi-model/runs/ue5/<project>/build-<timestamp>/. Default timeout: $UE_BUILD_TIMEOUT or 600.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

proj=""; target_kind="Editor"; config="Development"; out=""; timeout_s="${UE_BUILD_TIMEOUT:-600}"; force=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) target_kind="$2"; shift 2;;
    --config) config="$2"; shift 2;;
    --out) out="$2"; shift 2;;
    --timeout) timeout_s="$2"; shift 2;;
    --force-rescan) force=1; shift;;
    -h|--help) sed -n '2,12p' "$0"; exit 0;;
    -*) echo "unknown arg: $1" >&2; exit 2;;
    *) proj="$1"; shift;;
  esac
done
[[ -n "$proj" ]] || { echo "project required" >&2; exit 2; }
eval "$("$HERE/ue_env.sh" "$proj")"

case "$target_kind" in
  Editor) target="$UE_EDITOR_TARGET";;
  Game)   target="$UE_PROJECT_NAME";;
  *)      target="$target_kind";;   # explicit target name
esac
[[ -n "$out" ]] || out="$HOME/.multi-model/runs/ue5/$UE_PROJECT_NAME/build-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$out"
log="$out/build.log"

run_build() {   # $1 = extra flags
  local extra="$1" rc=0
  local cmd=("$UE_BUILD_BAT" "$target" Win64 "$config" "-Project=$UE_PROJECT" -WaitMutex -NoHotReload)
  [[ -n "$extra" ]] && cmd+=("$extra")
  printf '%s ' "${cmd[@]}" > "$out/command.txt"
  local t0=$SECONDS
  timeout --foreground "$timeout_s" "${cmd[@]}" > "$log" 2>&1 || rc=$?
  WALL=$((SECONDS - t0)); RC=$rc
}

needs_rescan() {   # up-to-date result but a Source file is newer than the target metadata
  local tfile="$UE_PROJECT_DIR/Binaries/Win64/$target.target"
  [[ -f "$tfile" ]] || return 1
  [[ -n "$(find "$UE_PROJECT_DIR/Source" -type f \( -name '*.cpp' -o -name '*.h' -o -name '*.cs' \) -newer "$tfile" -print -quit)" ]]
}

extra=""; [[ $force -eq 1 ]] && extra="-NoUBTMakefiles"
run_build "$extra"
if [[ $force -eq 0 ]]; then
  if [[ $RC -eq 0 ]] && grep -q "Target is up to date" "$log" && needs_rescan; then
    echo "up to date but Source is newer than $target.target; re-running with -NoUBTMakefiles" >&2
    cp "$log" "$out/build.first.log"; run_build "-NoUBTMakefiles"
  elif [[ $RC -ne 0 ]] && grep -E "^\[[0-9]+/[0-9]+\] Compile .* ([A-Za-z0-9_.-]+\.cpp)$" "$log" | awk '{print $NF}' | while read -r f; do
        [[ -z "$(find "$UE_PROJECT_DIR/Source" -name "$f" -print -quit)" ]] && echo missing; done | grep -q missing; then   # stale makefile still lists a deleted .cpp
    echo "C1083 (missing source file) - re-running with -NoUBTMakefiles" >&2
    cp "$log" "$out/build.first.log"; run_build "-NoUBTMakefiles"
  fi
fi

"$PY" - "$log" "$RC" "$WALL" "$timeout_s" "$out/command.txt" "$out/build.json" <<'PY'
import json, re, sys
log, rc, wall, tmo, cmdf, outf = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5], sys.argv[6]
raw = open(log, "rb").read()
def dec(b):
    try: return b.decode("utf-8")
    except UnicodeDecodeError: return b.decode("cp936", errors="replace")   # MSVC prints GBK on zh-CN Windows
lines = [dec(l) for l in raw.splitlines()]
diag = re.compile(r'^(?P<file>.+?)\((?P<line>\d+)(?:,(?P<col>\d+))?\)\s*:\s*(?P<kind>error|warning)\s+(?P<code>[A-Z]+\d+)\s*:\s*(?P<msg>.*)$')
lnk = re.compile(r'^(?P<file>\S+?)\s*:\s*(?P<kind>fatal error|error|warning)\s+(?P<code>[A-Z]+\d+)\s*:\s*(?P<msg>.*)$')   # LNKxxxx, c1xx: fatal error C1083
errors, warnings = [], []
for ln in lines:
    m = diag.match(ln.strip()) or lnk.match(ln.strip())
    if not m: continue
    d = m.groupdict(); d["line"] = int(d.get("line") or 0); d["col"] = int(d.get("col") or 0)
    (warnings if d["kind"] == "warning" else errors).append(d)
res = re.search(r'^Result:\s*(Succeeded|Failed)(?:\s*\((\w+)\))?', "\n".join(lines), re.M)
secs = re.search(r'Total execution time:\s*([\d.]+)', "\n".join(lines))
acts = re.search(r'executor to run (\d+) action', "\n".join(lines))
par = re.search(r'limiting max parallel actions to (\d+)', "\n".join(lines))
if rc == 124 and not res:
    result, reason = "Timeout", f"exceeded {tmo}s"
elif res:
    result, reason = res.group(1), res.group(2) or ""
else:
    result, reason = "Failed", f"no Result line (exit {rc})"
if result == "Succeeded" and errors:   # never trust a green Result with error lines in the log
    result, reason = "Failed", "error lines present"
# de-duplicate (UBT echoes MSVC lines twice in some modes)
seen = set(); uniq = []
for e in errors:
    k = (e["file"], e["line"], e["code"]);
    if k not in seen: seen.add(k); uniq.append(e)
json.dump({
    "result": result, "reason": reason, "exit_code": rc, "seconds": float(secs.group(1)) if secs else wall,
    "wall_seconds": wall, "actions": int(acts.group(1)) if acts else 0,
    "up_to_date": "Target is up to date" in "\n".join(lines),
    "parallel_limited_to": int(par.group(1)) if par else None,
    "errors": uniq, "warnings": warnings[:50], "warning_count": len(warnings),
    "command": open(cmdf, encoding="utf-8").read().strip(), "log": log,
}, open(outf, "w", encoding="utf-8"), ensure_ascii=False, indent=2)   # explicit UTF-8: stdout would be cp936 on zh-CN Windows
PY

result="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["result"])' "$out/build.json")"
echo "$out/build.json" >&2
cat "$out/build.json"
case "$result" in Succeeded) exit 0;; Timeout) exit 124;; *) exit 1;; esac
