"""Runs INSIDE the Unreal Editor Python (via ue_bp_export.sh): export Blueprints to T3D, parse them with
ue_t3d_parse.py, record compile status and components. Not meant to be run with a normal python.

Config (JSON path in env UE_BP_EXPORT_CONFIG):
  {"assets": ["/Game/Path/BP_Foo", ...], "out": "C:/.../dir", "compile": true, "dsl": false}
Writes per asset: <Name>.t3d, <Name>.bp.json, <Name>.bp.md; plus export.json summarizing everything.
"""
import json
import os
import re
import sys
import time
import traceback

import unreal

cfg = json.load(open(os.environ["UE_BP_EXPORT_CONFIG"], encoding="utf-8"))
OUT = cfg["out"]
os.makedirs(OUT, exist_ok=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ue_t3d_parse  # noqa: E402

log = unreal.log
summary = {"assets": [], "engine": unreal.SystemLibrary.get_engine_version(), "started": time.strftime("%Y-%m-%dT%H:%M:%S")}

def status_name(bp):
    try:
        st = bp.get_editor_property("status")
        m = re.search(r"\.([A-Z_]+)", str(st))     # "<BlueprintStatus.BS_UP_TO_DATE: 3>" -> BS_UP_TO_DATE
        return m.group(1) if m else str(st)
    except Exception as e:  # pragma: no cover
        return f"? ({e})"


def components_of(bp):
    """[{name, class, parent}] from the SimpleConstructionScript via SubobjectDataSubsystem (5.1+)."""
    out = []
    sds = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
    handles = sds.k2_gather_subobject_data_for_blueprint(bp)
    lib = unreal.SubobjectDataBlueprintFunctionLibrary
    names = {}
    for h in handles:
        d = sds.k2_find_subobject_data_from_handle(h)
        obj = lib.get_object(d)
        if obj is None:
            continue
        name = str(lib.get_variable_name(d))
        names[str(h)] = name
        parent_h = lib.get_parent_handle(d) if hasattr(lib, "get_parent_handle") else None
        out.append({"name": name, "class": obj.get_class().get_name(),
                    "parent": names.get(str(parent_h), "") if parent_h else "",
                    "is_actor": isinstance(obj, unreal.Actor)})
    seen, uniq = set(), []
    for c in out:
        if c["is_actor"] or c["name"] in seen:
            continue
        seen.add(c["name"]); uniq.append({k: v for k, v in c.items() if k != "is_actor"})
    return uniq


def export_one(path):
    rec = {"asset": path, "t3d": None, "json": None, "md": None, "compile": None, "components": None, "errors": []}
    t0 = time.time()
    bp = unreal.EditorAssetLibrary.load_asset(path)
    if bp is None or not isinstance(bp, unreal.Blueprint):
        rec["errors"].append(f"not a Blueprint or failed to load: {path}")
        return rec
    name = bp.get_name()
    rec["status_on_load"] = status_name(bp)

    # 1. T3D export (before compiling, so no compiler intermediates leak into the file)
    task = unreal.AssetExportTask()
    task.object = bp
    task.filename = os.path.join(OUT, f"{name}.t3d")
    task.automated = True
    task.replace_identical = True
    task.prompt = False
    task.exporter = unreal.ObjectExporterT3D()
    ok = unreal.Exporter.run_asset_export_task(task)
    if not ok or not os.path.exists(task.filename):
        rec["errors"].append("T3D export failed: " + "; ".join(str(e) for e in task.errors))
        return rec
    rec["t3d"] = task.filename

    # 2. components (SCS is not in the T3D; gathered from the SubobjectDataSubsystem instead)
    components = None
    try:
        components = components_of(bp)
        rec["components"] = components
    except Exception:
        rec["errors"].append("components: " + traceback.format_exc().splitlines()[-1])

    # 3. parse -> bp.json + bp.md
    try:
        text = open(task.filename, encoding="utf-8", errors="replace").read()
        model = ue_t3d_parse.build_model(ue_t3d_parse.read_objects(text))
        if components is not None:
            model["components"] = components
        md = ue_t3d_parse.render_all(model)
        rec["json"] = os.path.join(OUT, f"{name}.bp.json")
        rec["md"] = os.path.join(OUT, f"{name}.bp.md")
        rec["coverage"] = model["coverage"]
        rec["graphs"] = [{"name": g["name"], "kind": g["kind"], "nodes": len(g["nodes"])} for g in model["graphs"]]
    except Exception:
        rec["errors"].append("parse failed: " + traceback.format_exc())
        model, md = None, ""

    # 4. compile (the blueprint-mode hard check)
    if cfg.get("compile", True):
        try:
            unreal.BlueprintEditorLibrary.compile_blueprint(bp)
            rec["compile"] = {"status": status_name(bp)}
        except Exception:
            rec["compile"] = {"status": "EXCEPTION", "error": traceback.format_exc().splitlines()[-1]}

    # 5. optional: Epic's DSL view (EditorToolset must be enabled; lossy for multi-exec events, see PLAN-ue5 M0)
    if cfg.get("dsl"):
        try:
            from editor_toolset.toolsets.blueprint import BlueprintTools
            dsl = {}
            for g in BlueprintTools.list_graphs(bp):
                dsl[g.get_name()] = BlueprintTools.read_graph_dsl(g)
            rec["dsl"] = os.path.join(OUT, f"{name}.dsl.json")
            json.dump(dsl, open(rec["dsl"], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        except Exception:
            rec["errors"].append("dsl: " + traceback.format_exc().splitlines()[-1])

    if model is not None:
        model["compile"] = rec["compile"]
        model["asset"] = path
        json.dump(model, open(rec["json"], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        header = f"<!-- asset: {path} | compile: {rec['compile']['status'] if rec['compile'] else 'skipped'} | components: {len(rec['components'] or [])} -->\n"
        comp = ""
        if rec["components"]:
            comp = "Components: " + ", ".join(f"{c['name']}:{c['class']}" + (f"←{c['parent']}" if c["parent"] else "") for c in rec["components"]) + "\n\n"
        open(rec["md"], "w", encoding="utf-8").write(header + comp + md)
    rec["seconds"] = round(time.time() - t0, 2)
    return rec


for asset in cfg["assets"]:
    try:
        summary["assets"].append(export_one(asset))
    except Exception:
        summary["assets"].append({"asset": asset, "errors": [traceback.format_exc()]})
    log(f"[ue_bp_export] {asset}: {summary['assets'][-1].get('errors') or 'ok'}")

json.dump(summary, open(os.path.join(OUT, "export.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
log("[ue_bp_export] done -> " + OUT)
