"""Runs INSIDE the Unreal Editor Python (via ue_render_check.sh): recompile materials and record shader statistics.
Needs a real RHI (no -nullrhi): shader compilation is what produces the evidence.

Config (JSON path in env UE_MAT_CONFIG): {"assets": ["/Game/Path/M_Foo", ...], "out": "C:/.../dir", "recompile": true}
Writes <out>/materials.json: {"assets": [{asset, class, parent, domain, blend_mode, shading_model, two_sided,
  statistics: {num_pixel_shader_instructions, num_vertex_shader_instructions, num_samplers, num_pixel_texture_samples,
               num_vertex_texture_samples, num_virtual_texture_samples, num_uv_scalars, num_interpolator_scalars},
  expressions: N, errors: [...], seconds}]}
"""
import json
import os
import re
import time
import traceback

import unreal

cfg = json.load(open(os.environ["UE_MAT_CONFIG"], encoding="utf-8"))
OUT = cfg["out"]
os.makedirs(OUT, exist_ok=True)
MEL = unreal.MaterialEditingLibrary
log = unreal.log


def enum_name(v):
    m = re.search(r"\.([A-Z_0-9]+)", str(v))
    return m.group(1) if m else str(v)


def stats_of(mat):
    s = MEL.get_statistics(mat)
    out = {}
    for k in ("num_pixel_shader_instructions", "num_vertex_shader_instructions", "num_samplers", "num_pixel_texture_samples",
              "num_vertex_texture_samples", "num_virtual_texture_samples", "num_uv_scalars", "num_interpolator_scalars"):
        try:
            out[k] = int(s.get_editor_property(k))
        except Exception:
            pass
    return out


def one(path):
    rec = {"asset": path, "errors": []}
    t0 = time.time()
    obj = unreal.EditorAssetLibrary.load_asset(path)
    if obj is None:
        rec["errors"].append("failed to load")
        return rec
    rec["class"] = obj.get_class().get_name()
    if isinstance(obj, unreal.MaterialInstance):
        parent = obj.get_editor_property("parent")
        rec["parent"] = parent.get_path_name() if parent else None
        base = obj.get_base_material() if hasattr(obj, "get_base_material") else parent
        mat = base
        if cfg.get("recompile", True) and isinstance(obj, unreal.MaterialInstanceConstant):
            try:
                MEL.update_material_instance(obj)
            except Exception:
                rec["errors"].append("update_material_instance: " + traceback.format_exc().splitlines()[-1])
    elif isinstance(obj, unreal.Material):
        mat = obj
        if cfg.get("recompile", True):
            try:
                MEL.recompile_material(mat)
            except Exception:
                rec["errors"].append("recompile_material: " + traceback.format_exc().splitlines()[-1])
        try:
            rec["expressions"] = int(MEL.get_num_material_expressions(mat))
        except Exception:
            pass
    elif isinstance(obj, unreal.MaterialFunction):
        rec["kind"] = "MaterialFunction"
        rec["seconds"] = round(time.time() - t0, 2)
        return rec
    else:
        rec["errors"].append(f"not a material: {rec['class']}")
        return rec
    try:
        for k in ("material_domain", "blend_mode", "shading_model", "two_sided"):
            v = mat.get_editor_property(k)
            rec[k] = enum_name(v) if not isinstance(v, bool) else v
    except Exception:
        pass
    try:
        rec["statistics"] = stats_of(obj if isinstance(obj, unreal.Material) else obj)
    except Exception:
        rec["errors"].append("get_statistics: " + traceback.format_exc().splitlines()[-1])
    rec["seconds"] = round(time.time() - t0, 2)
    return rec


res = {"assets": [], "engine": unreal.SystemLibrary.get_engine_version(), "rhi": str(unreal.SystemLibrary.get_platform_user_name()) and ""}
for a in cfg["assets"]:
    try:
        res["assets"].append(one(a))
    except Exception:
        res["assets"].append({"asset": a, "errors": [traceback.format_exc()]})
    log(f"[ue_material_stats] {a}: {res['assets'][-1].get('errors') or res['assets'][-1].get('statistics')}")
json.dump(res, open(os.path.join(OUT, "materials.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
log("[ue_material_stats] done -> " + OUT)
