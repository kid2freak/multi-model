#!/usr/bin/env bash
# Resolve an Unreal project's engine and tool paths. Prints shell assignments; `eval "$(ue_env.sh <proj>)"`.
#
# Usage: ue_env.sh <path/to/Project.uproject | project dir>
# Output (Windows-style paths for tools, POSIX for the shell):
#   UE_PROJECT          E:\...\Proj.uproject     UE_PROJECT_DIR   /e/.../Proj
#   UE_PROJECT_NAME     Proj                     UE_EDITOR_TARGET ProjEditor
#   UE_ENGINE_VERSION   5.8                      UE_ENGINE_ROOT   E:\...\UE_5.8
#   UE_BUILD_BAT / UE_EDITOR_CMD / UE_RUNUAT      full Windows paths
#   PY                  python3 or python (whichever really runs; Windows Store stub is skipped)
# Engine lookup order: $UE_ENGINE_ROOT (override) > LauncherInstalled.dat (Launcher builds, by
# EngineAssociation "5.8") > HKCU\SOFTWARE\Epic Games\Unreal Engine\Builds (source builds, by GUID).
set -euo pipefail

arg="${1:-}"
[[ -n "$arg" ]] || { sed -n '2,12p' "$0" >&2; exit 2; }
if [[ -d "$arg" ]]; then
  set -- "$arg"/*.uproject
  [[ -f "$1" ]] || { echo "no .uproject in $arg" >&2; exit 2; }
  arg="$1"
fi
[[ -f "$arg" ]] || { echo "no such file: $arg" >&2; exit 2; }

PY=""
for c in python3 python; do
  if "$c" -c "import sys" >/dev/null 2>&1; then PY="$c"; break; fi
done
[[ -n "$PY" ]] || { echo "no working python on PATH" >&2; exit 2; }

to_win() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }
to_posix() { if command -v cygpath >/dev/null 2>&1; then cygpath -u "$1"; else printf '%s' "$1"; fi; }

proj_posix="$(cd "$(dirname "$arg")" && pwd)/$(basename "$arg")"
proj_dir="$(dirname "$proj_posix")"
name="$(basename "$arg" .uproject)"
assoc="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8-sig")).get("EngineAssociation",""))' "$proj_posix")"

engine_root=""
if [[ -n "${UE_ENGINE_ROOT:-}" ]]; then
  engine_root="$UE_ENGINE_ROOT"
else
  dat="$(to_posix "${ProgramData:-C:\\ProgramData}")/Epic/UnrealEngineLauncher/LauncherInstalled.dat"
  if [[ -f "$dat" && -n "$assoc" ]]; then
    engine_root="$("$PY" - "$dat" "$assoc" <<'PY'
import json, sys
dat, assoc = sys.argv[1:]
for it in json.load(open(dat, encoding="utf-8-sig")).get("InstallationList", []):
    if it.get("AppName") == f"UE_{assoc}":
        print(it["InstallLocation"]); break
PY
)"
  fi
  if [[ -z "$engine_root" && -n "$assoc" ]] && command -v reg.exe >/dev/null 2>&1; then
    engine_root="$(reg.exe query 'HKCU\SOFTWARE\Epic Games\Unreal Engine\Builds' /v "$assoc" 2>/dev/null | awk -F'REG_SZ' '/REG_SZ/{gsub(/^[ \t]+|[ \t\r]+$/,"",$2); print $2}')"
  fi
fi
[[ -n "$engine_root" ]] || { echo "cannot resolve engine for EngineAssociation='$assoc' (set UE_ENGINE_ROOT)" >&2; exit 3; }
engine_posix="$(to_posix "$engine_root")"
[[ -f "$engine_posix/Engine/Build/BatchFiles/Build.bat" ]] || { echo "not an engine root: $engine_root" >&2; exit 3; }

engine_win="$(to_win "$engine_posix")"
cat <<EOF
UE_PROJECT='$(to_win "$proj_posix")'
UE_PROJECT_POSIX='$proj_posix'
UE_PROJECT_DIR='$proj_dir'
UE_PROJECT_NAME='$name'
UE_EDITOR_TARGET='${name}Editor'
UE_ENGINE_VERSION='$assoc'
UE_ENGINE_ROOT='$engine_win'
UE_ENGINE_POSIX='$engine_posix'
UE_BUILD_BAT='$engine_posix/Engine/Build/BatchFiles/Build.bat'
UE_EDITOR_CMD='$engine_posix/Engine/Binaries/Win64/UnrealEditor-Cmd.exe'
UE_RUNUAT='$engine_posix/Engine/Build/BatchFiles/RunUAT.bat'
PY='$PY'
EOF
