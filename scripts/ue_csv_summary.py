#!/usr/bin/env python3
"""Summarize an Unreal CSV Profiler capture, optionally against a baseline. Pure Python.

Usage:
  ue_csv_summary.py capture.csv [--warmup N] [--json perf.json] [--compare baseline.perf.json] [--top K]

perf.json:
  {"file", "frames", "frames_used", "warmup", "hitches", "stats": {<metric>: {mean, p50, p95, max, iqr}},
   "top_gamethread": [{stat, mean_ms}], "ticks": {...}, "meta": {...},
   "compare": {"baseline": path, "metrics": {<metric>: {base_p50, p50, delta_ms, delta_pct, noise_ms, verdict}},
               "verdict": "better|worse|same|mixed"}}
Warm-up: the first N frames (default 120; boot capture includes engine init and map load) are dropped, and any
remaining frame with FrameTime > 4x the median is counted as a hitch and excluded from the percentiles.
Compare rule (applied in code, not by a model): a metric changed if |p50_after - p50_base| > noise, where
noise = max(1.5 * IQR_base, 0.03 * p50_base). "better" = FrameTime and every thread time not worse and at least
one better; "worse" = FrameTime or any thread time worse; "mixed" = some better some worse; else "same".
"""
import argparse
import csv
import json
import statistics as st
import sys

KEY = ["FrameTime", "GameThreadTime", "RenderThreadTime", "GPUTime", "RHIThreadTime"]
EXTRA = ["RHI/DrawCalls", "RHI/PrimitivesDrawn", "Basic/TicksQueued", "MemoryFreeMB", "Shaders/ShaderMemoryMB"]


def pct(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    k = (len(s) - 1) * p
    f, c = int(k), min(int(k) + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def stats(vals):
    if not vals:
        return None
    return {"mean": round(st.mean(vals), 3), "p50": round(pct(vals, .5), 3), "p95": round(pct(vals, .95), 3),
            "max": round(max(vals), 3), "min": round(min(vals), 3), "iqr": round(pct(vals, .75) - pct(vals, .25), 3), "n": len(vals)}


def load(path):
    csv.field_size_limit(10 ** 9)
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        rows = list(csv.reader(f))
    header = rows[0]
    data, meta = [], {}
    for r in rows[1:]:
        if not r:
            continue
        if r[0].startswith("["):          # trailing metadata: [Key],value,[Key],value...
            for i in range(0, len(r) - 1, 2):
                if r[i].startswith("["):
                    meta[r[i].strip("[]")] = r[i + 1]
            continue
        if r[0] == header[0]:             # repeated header at the end
            continue
        data.append(r)
    return header, data, meta


def column(header, data, name):
    if name not in header:
        return None
    i = header.index(name)
    out = []
    for r in data:
        v = r[i] if i < len(r) else ""
        out.append(float(v) if v not in ("", None) else 0.0)
    return out


def summarize(path, warmup=120, top=12):
    header, data, meta = load(path)
    frames = len(data)
    ft = column(header, data, "FrameTime") or []
    used_idx = list(range(min(warmup, frames), frames))
    med = st.median([ft[i] for i in used_idx]) if used_idx else 0
    hitch_idx = [i for i in used_idx if med and ft[i] > 4 * med]
    keep = [i for i in used_idx if i not in set(hitch_idx)]
    out = {"file": path, "frames": frames, "warmup": min(warmup, frames), "frames_used": len(keep),
           "hitches": {"count": len(hitch_idx), "worst_ms": round(max((ft[i] for i in hitch_idx), default=0), 2)},
           "stats": {}, "top_gamethread": [], "ticks": {}, "meta": {k: meta[k] for k in ("Platform", "Config", "BuildVersion", "EngineVersion", "Commandline", "DeviceProfile") if k in meta}}
    for name in KEY + EXTRA:
        col = column(header, data, name)
        if col is not None:
            out["stats"][name] = stats([col[i] for i in keep])
    excl = []
    for name in header:
        if name.startswith("Exclusive/GameThread/"):
            col = column(header, data, name)
            m = st.mean([col[i] for i in keep]) if keep else 0
            if m > 0.01:
                excl.append({"stat": name[len("Exclusive/GameThread/"):], "mean_ms": round(m, 3)})
    out["top_gamethread"] = sorted(excl, key=lambda x: -x["mean_ms"])[:top]
    for name in header:
        if name.startswith("Ticks/"):
            col = column(header, data, name)
            out["ticks"][name[6:]] = round(st.mean([col[i] for i in keep]) if keep else 0, 1)
    return out


def compare(after, base):
    metrics = {}
    verdicts = []
    for name in KEY:
        a, b = after["stats"].get(name), base["stats"].get(name)
        if not a or not b:
            continue
        noise = max(1.5 * b["iqr"], 0.03 * b["p50"])
        delta = a["p50"] - b["p50"]
        v = "same" if abs(delta) <= noise else ("better" if delta < 0 else "worse")
        metrics[name] = {"base_p50": b["p50"], "p50": a["p50"], "delta_ms": round(delta, 3),
                         "delta_pct": round(100 * delta / b["p50"], 1) if b["p50"] else None, "noise_ms": round(noise, 3), "verdict": v}
        verdicts.append(v)
    if "worse" in verdicts and "better" in verdicts:
        overall = "mixed"
    elif "worse" in verdicts:
        overall = "worse"
    elif "better" in verdicts:
        overall = "better"
    else:
        overall = "same"
    same_conditions = all(after["meta"].get(k) == base["meta"].get(k) for k in ("Platform", "Config", "DeviceProfile"))
    return {"baseline": base["file"], "metrics": metrics, "verdict": overall, "same_conditions": same_conditions,
            "frames_used": [base["frames_used"], after["frames_used"]]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--warmup", type=int, default=120)
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--json")
    ap.add_argument("--compare", help="baseline perf.json")
    a = ap.parse_args()
    out = summarize(a.csv, a.warmup, a.top)
    if a.compare:
        with open(a.compare, encoding="utf-8") as f:
            out["compare"] = compare(out, json.load(f))
    text = json.dumps(out, ensure_ascii=False, indent=1)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            f.write(text)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
