#!/usr/bin/env python3
"""Parse Unreal Blueprint T3D text into a graph model and readable pseudo-code. Pure Python, no `unreal` module.

Accepts both T3D flavours (same node syntax, see FEdGraphUtilities::ExportNodesToText):
  * a whole-asset export (ObjectExporterT3D / ue_bp_export.py): Blueprint > EdGraph > K2Node_* objects
  * clipboard text copied from a graph in the editor (Ctrl+C): a flat list of K2Node_* objects

Usage:
  ue_t3d_parse.py <file.t3d|-> [--json bp.json] [--md bp.md] [--graph NAME]...
Prints the pseudo-code to stdout unless --md is given. bp.json is the structured model:
  {"source": "asset|clipboard", "blueprint": {...}, "graphs": [{"name", "kind", "nodes": [...], "comments": [...],
   "orphans": [...]}], "coverage": {"exec_nodes", "reached", "unreached": [...]}}
Every node keeps its pins (name, direction, category, default, links) so nothing the exporter wrote is lost;
the pseudo-code follows EVERY exec output pin (Triggered/Started/Completed/LoopBody/...), not just `then`.
"""
import argparse
import json
import re
import sys
from collections import OrderedDict

# ----------------------------------------------------------------------------- low-level T3D reader

_BEGIN = re.compile(r'^\s*Begin Object(?:\s+Class=(?P<cls>\S+))?\s+Name="(?P<name>[^"]+)"(?:\s+ExportPath="(?P<path>[^"]*)")?')
_END = re.compile(r'^\s*End Object\s*$')
_PROP = re.compile(r'^\s*(?P<key>[A-Za-z_][A-Za-z0-9_]*(?:\(\d+\))?(?:\.[A-Za-z_][A-Za-z0-9_]*)*)=(?P<val>.*)$')
_PIN = re.compile(r'^\s*CustomProperties Pin \((?P<body>.*)\)\s*$')


class Obj:
    __slots__ = ("cls", "name", "path", "props", "pins", "children", "parent")

    def __init__(self, cls, name, path, parent):
        self.cls, self.name, self.path, self.parent = cls, name, path, parent
        self.props = OrderedDict()   # key -> raw value string (arrays as key(i))
        self.pins = []               # raw pin dicts
        self.children = []

    def short_class(self):
        c = self.cls or ""
        return c.rsplit(".", 1)[-1].strip("'\"")


def read_objects(text):
    """Return top-level objects; declaration and definition blocks of the same (parent, name) are merged."""
    root = Obj(None, "<root>", None, None)
    stack = [root]
    index = {}   # (id(parent), name) -> Obj
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        m = _BEGIN.match(line)
        if m:
            parent = stack[-1]
            key = (id(parent), m.group("name"))
            obj = index.get(key)
            if obj is None:
                obj = Obj(m.group("cls"), m.group("name"), m.group("path"), parent)
                parent.children.append(obj)
                index[key] = obj
            else:
                obj.cls = obj.cls or m.group("cls")
                obj.path = obj.path or m.group("path")
            stack.append(obj)
            continue
        if _END.match(line):
            if len(stack) > 1:
                stack.pop()
            continue
        cur = stack[-1]
        if cur is root:
            continue
        pm = _PIN.match(line)
        if pm:
            cur.pins.append(parse_kv_list(pm.group("body")))
            continue
        km = _PROP.match(line)
        if km:
            cur.props[km.group("key")] = km.group("val")
    return root.children


def parse_kv_list(body):
    """Parse `a=1,b="x, y",c=(d=2,e=(f=3)),LinkedTo=(K2Node_X GUID,K2Node_Y GUID,)` into a dict (nested dicts/lists kept as strings
    except LinkedTo, which becomes a list of (node, pinid))."""
    out = OrderedDict()
    i, n = 0, len(body)
    while i < n:
        while i < n and body[i] in ", ":
            i += 1
        j = i
        while j < n and body[j] != "=":
            j += 1
        key = body[i:j].strip()
        i = j + 1
        if i >= n or not key:
            break
        if body[i] == '"':
            j = i + 1
            buf = []
            while j < n:
                if body[j] == "\\" and j + 1 < n:
                    buf.append(body[j + 1]); j += 2; continue
                if body[j] == '"':
                    break
                buf.append(body[j]); j += 1
            out[key] = "".join(buf)
            i = j + 1
        elif body[i] == "(":
            depth, j = 0, i
            while j < n:
                if body[j] == "(":
                    depth += 1
                elif body[j] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                elif body[j] == '"':      # skip quoted strings inside
                    j += 1
                    while j < n and body[j] != '"':
                        j += 2 if body[j] == "\\" else 1
                j += 1
            out[key] = body[i:j + 1]
            i = j + 1
        else:
            j = i
            depth = 0
            while j < n and (body[j] != "," or depth > 0):
                if body[j] == "(":
                    depth += 1
                elif body[j] == ")":
                    depth -= 1
                j += 1
            out[key] = body[i:j].strip()
            i = j
    if "LinkedTo" in out:
        items = out["LinkedTo"].strip("()").split(",")
        links = []
        for it in items:
            it = it.strip()
            if not it:
                continue
            parts = it.split()
            links.append({"node": parts[0], "pin": parts[1] if len(parts) > 1 else ""})
        out["LinkedTo"] = links
    return out


def unq(v):
    """Strip surrounding quotes from a raw property value."""
    if v is None:
        return None
    v = v.strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1]
    return v.replace('\\"', '"').replace("\\'", "'").replace("\\n", "\n")


def obj_short(ref):
    """`/Script/EnhancedInput.InputAction'/Game/Input/Actions/IA_Move.IA_Move'` -> IA_Move ; `Class'/Script/Engine.Character'` -> Character."""
    if not ref:
        return ""
    ref = unq(ref)
    m = re.search(r"'([^']*)'", ref)
    inner = m.group(1) if m else ref
    inner = inner.rstrip("'")
    tail = inner.rsplit("/", 1)[-1]
    tail = tail.rsplit(".", 1)[-1]
    tail = tail.rsplit(":", 1)[-1]
    return tail.strip("'\"") or ref


def struct_get(raw, key):
    """Read key=... from a `(a=1,b="x")` struct string."""
    if not raw:
        return None
    d = parse_kv_list(raw.strip()[1:-1] if raw.strip().startswith("(") else raw)
    return d.get(key)


# ----------------------------------------------------------------------------- graph model

EXEC = "exec"
SKIP_CLASSES = {"EdGraphNode_Comment"}


def build_pin(p):
    cat = p.get("PinType.PinCategory", "")
    sub = p.get("PinType.PinSubCategory", "")
    subobj = obj_short(p.get("PinType.PinSubCategoryObject")) if p.get("PinType.PinSubCategoryObject") not in (None, "None") else ""
    return {
        "id": p.get("PinId", ""),
        "name": p.get("PinName", ""),
        "friendly": p.get("PinFriendlyName", ""),
        "dir": "out" if p.get("Direction") == "EGPD_Output" else "in",
        "category": cat,
        "subcategory": sub,
        "type": subobj or sub or cat,
        "container": p.get("PinType.ContainerType", ""),
        "default": p.get("DefaultValue", ""),
        "default_object": obj_short(p.get("DefaultObject")) if p.get("DefaultObject") not in (None, "None") else "",
        "links": p.get("LinkedTo", []),
        "hidden": p.get("bHidden", "").lower() == "true",
    }


def node_title(o):
    """Human title for a node from its class and reference properties."""
    c = o.short_class()
    P = o.props
    if c in ("K2Node_CallFunction", "K2Node_CallParentFunction", "K2Node_CommutativeAssociativeBinaryOperator", "K2Node_PromotableOperator", "K2Node_CallArrayFunction"):
        name = unq(struct_get(P.get("FunctionReference"), "MemberName")) or "?"
        parent = struct_get(P.get("FunctionReference"), "MemberParent")
        selfctx = (struct_get(P.get("FunctionReference"), "bSelfContext") or "").lower() == "true"
        owner = "" if selfctx or not parent else obj_short(parent) + "::"
        if c == "K2Node_CallParentFunction":
            return f"Parent::{name}"
        return f"{owner}{name}"
    if c == "K2Node_Event":
        name = unq(struct_get(P.get("EventReference"), "MemberName")) or "?"
        return f"Event {name.replace('Receive', '', 1) if name.startswith('Receive') else name}"
    if c == "K2Node_CustomEvent":
        return f"CustomEvent {unq(P.get('CustomFunctionName')) or '?'}"
    if c == "K2Node_EnhancedInputAction":
        return f"InputAction {obj_short(P.get('InputAction')) or '?'}"
    if c == "K2Node_InputAction":
        return f"InputAction(legacy) {unq(P.get('InputActionName')) or '?'}"
    if c in ("K2Node_InputKey", "K2Node_InputAxisEvent", "K2Node_InputAxisKeyEvent", "K2Node_InputTouch"):
        return f"{c[7:]} {unq(P.get('InputKey') or P.get('InputAxisName') or P.get('AxisKey')) or ''}".strip()
    if c == "K2Node_ComponentBoundEvent":
        return f"Event {unq(P.get('ComponentPropertyName'))}.{unq(P.get('DelegatePropertyName'))}"
    if c == "K2Node_ActorBoundEvent":
        return f"Event {obj_short(P.get('EventOwner'))}.{unq(P.get('DelegatePropertyName'))}"
    if c == "K2Node_FunctionEntry":
        return f"Function {o.parent.name if o.parent else '?'}"
    if c == "K2Node_FunctionResult":
        return "Return"
    if c == "K2Node_VariableGet":
        return f"Get {unq(struct_get(P.get('VariableReference'), 'MemberName')) or '?'}"
    if c == "K2Node_VariableSet":
        return f"Set {unq(struct_get(P.get('VariableReference'), 'MemberName')) or '?'}"
    if c == "K2Node_IfThenElse":
        return "Branch"
    if c == "K2Node_ExecutionSequence":
        return "Sequence"
    if c == "K2Node_MacroInstance":
        return f"Macro {obj_short(struct_get(P.get('MacroGraphReference'), 'MacroGraph')) or '?'}"
    if c == "K2Node_DynamicCast":
        return f"Cast<{obj_short(P.get('TargetType')) or '?'}>"
    if c == "K2Node_ClassDynamicCast":
        return f"ClassCast<{obj_short(P.get('TargetType')) or '?'}>"
    if c == "K2Node_Knot":
        return "(reroute)"
    if c == "K2Node_Self":
        return "self"
    if c in ("K2Node_MakeStruct", "K2Node_BreakStruct", "K2Node_SetFieldsInStruct"):
        return f"{c[7:]} {obj_short(P.get('StructType')) or ''}".strip()
    if c == "K2Node_SpawnActorFromClass":
        return "SpawnActor"
    if c == "K2Node_CreateWidget":
        return "CreateWidget"
    if c == "K2Node_Timeline":
        return f"Timeline {unq(P.get('TimelineName')) or ''}".strip()
    if c in ("K2Node_AddDelegate", "K2Node_RemoveDelegate", "K2Node_CallDelegate", "K2Node_ClearDelegate", "K2Node_AssignDelegate"):
        return f"{c[7:]} {unq(struct_get(P.get('DelegateReference'), 'MemberName')) or ''}".strip()
    if c == "K2Node_CreateDelegate":
        return f"Delegate {unq(P.get('SelectedFunctionName')) or ''}".strip()
    if c.startswith("K2Node_Switch"):
        return f"Switch({c[13:] or 'value'})"
    if c == "K2Node_Select":
        return "Select"
    if c == "K2Node_GetSubsystem":
        return f"GetSubsystem<{obj_short(P.get('CustomClass')) or '?'}>"
    if c == "K2Node_MathExpression":
        return f"MathExpression({unq(P.get('Expression')) or ''})"
    if c == "K2Node_Literal":
        return f"Literal {obj_short(P.get('ObjectRef')) or ''}".strip()
    if c == "K2Node_GetArrayItem":
        return "ArrayGet"
    if c == "K2Node_LatentAbilityCall" or c == "K2Node_LatentGameplayTaskCall":
        return f"{c[7:]} {unq(struct_get(P.get('ProxyFactoryFunctionName') or P.get('FunctionReference'), 'MemberName')) or unq(P.get('ProxyFactoryFunctionName')) or ''}".strip()
    if c == "K2Node_AsyncAction" or c == "K2Node_BaseAsyncTask":
        return f"Async {unq(P.get('ProxyFactoryFunctionName')) or ''}".strip()
    return c[7:] if c.startswith("K2Node_") else c


def build_model(objects):
    """Turn raw objects into the graph model. Handles asset exports (Blueprint > EdGraph > nodes) and clipboard dumps (nodes only)."""
    blueprints = [o for o in objects if o.short_class() == "Blueprint" or any(ch.short_class() == "EdGraph" for ch in o.children)]
    graphs_raw = []
    bp_info = {}
    source = "clipboard"
    if blueprints:
        source = "asset"
        bp = blueprints[0]
        P = bp.props
        bp_info = {
            "name": bp.name,
            "parent_class": obj_short(P.get("ParentClass")),
            "description": unq(P.get("BlueprintDescription")) or "",
            "interfaces": [obj_short(struct_get(v, "Interface")) for k, v in P.items() if k.startswith("ImplementedInterfaces(")],
            "variables": [],
            "event_graphs": [obj_short(v) for k, v in P.items() if k.startswith("UbergraphPages(")],
            "function_graphs": [obj_short(v) for k, v in P.items() if k.startswith("FunctionGraphs(")],
            "macro_graphs": [obj_short(v) for k, v in P.items() if k.startswith("MacroGraphs(")],
            "components_exported": any(ch.short_class() == "SimpleConstructionScript" for ch in bp.children),
        }
        for k, v in P.items():
            if k.startswith("NewVariables("):
                vt = struct_get(v, "VarType")
                bp_info["variables"].append({
                    "name": unq(struct_get(v, "VarName")),
                    "type": obj_short(struct_get(vt, "PinSubCategoryObject")) if vt and struct_get(vt, "PinSubCategoryObject") not in (None, "None") else (unq(struct_get(vt, "PinSubCategory")) or unq(struct_get(vt, "PinCategory")) if vt else "?"),
                    "container": unq(struct_get(vt, "ContainerType")) if vt else "",
                    "replicated": "Replicated" in (struct_get(v, "PropertyFlags") or ""),
                    "default": unq(struct_get(v, "DefaultValue")),
                })
        bp_info["delegate_graphs"] = [obj_short(v) for k, v in P.items() if k.startswith("DelegateSignatureGraphs(")]
        listed = set(bp_info["event_graphs"] + bp_info["function_graphs"] + bp_info["macro_graphs"] + bp_info["delegate_graphs"])
        # compiler-generated intermediates (ExecuteUbergraph_*) are subobjects too; keep only graphs the Blueprint lists
        graphs_raw = [ch for ch in bp.children if ch.short_class() == "EdGraph" and (not listed or ch.name in listed)]
        bp_info["skipped_graphs"] = [ch.name for ch in bp.children if ch.short_class() == "EdGraph" and listed and ch.name not in listed]
    else:
        # clipboard: synthesize one graph containing every top-level node
        g = Obj("/Script/Engine.EdGraph", "Clipboard", None, None)
        g.children = [o for o in objects]
        for o in g.children:
            o.parent = g
        graphs_raw = [g]

    graphs = []
    for g in graphs_raw:
        members = {obj_short(v) for k, v in g.props.items() if k.startswith("Nodes(")}
        nodes, comments, orphans = [], [], []
        for o in g.children:
            c = o.short_class()
            if members and o.name not in members:
                orphans.append({"id": o.name, "class": c})   # exported by outer, not in the graph's Nodes array
                continue
            if c in SKIP_CLASSES:
                comments.append({
                    "id": o.name, "text": unq(o.props.get("NodeComment")) or "",
                    "x": int(o.props.get("NodePosX", 0) or 0), "y": int(o.props.get("NodePosY", 0) or 0),
                    "w": int(o.props.get("NodeWidth", 0) or 0), "h": int(o.props.get("NodeHeight", 0) or 0),
                })
                continue
            if not c.startswith("K2Node") and not o.pins:
                continue
            pins = [build_pin(p) for p in o.pins]
            nodes.append({
                "id": o.name, "class": c, "title": node_title(o),
                "comment": unq(o.props.get("NodeComment")) or "",
                "x": int(o.props.get("NodePosX", 0) or 0), "y": int(o.props.get("NodePosY", 0) or 0),
                "pure": not any(p["category"] == EXEC for p in pins),
                "pins": pins,
                "error": unq(o.props.get("ErrorMsg")) if o.props.get("ErrorType") else None,
            })
        kind = ("event" if g.name in bp_info.get("event_graphs", []) else "function" if g.name in bp_info.get("function_graphs", [])
                else "macro" if g.name in bp_info.get("macro_graphs", []) else "delegate" if g.name in bp_info.get("delegate_graphs", [])
                else ("clipboard" if source == "clipboard" else "unknown"))
        graphs.append({"name": g.name, "kind": kind, "nodes": nodes, "comments": comments, "orphans": orphans})
    return {"source": source, "blueprint": bp_info, "graphs": graphs}


# ----------------------------------------------------------------------------- pseudo-code

class Renderer:
    def __init__(self, graph):
        self.g = graph
        self.nodes = {n["id"]: n for n in graph["nodes"]}
        self.pin_index = {(n["id"], p["id"]): p for n in graph["nodes"] for p in n["pins"]}
        self.reached = set()
        self.lines = []

    # ---- data expressions
    def expr(self, node, pin, depth=0):
        """Expression feeding an input pin: linked source (pure node inlined) or the default value."""
        if pin["links"]:
            link = pin["links"][0]
            src = self.nodes.get(link["node"])
            spin = self.pin_index.get((link["node"], link["pin"]))
            if src is None:
                return f"<{link['node']}?>"
            if src["class"] == "K2Node_Knot":
                inp = next((p for p in src["pins"] if p["dir"] == "in"), None)
                return self.expr(src, inp, depth) if inp else "?"
            if depth > 12:
                return f"{src['title']}…"
            label = self.value_of(src, spin, depth)
            return label
        if pin["default_object"]:
            return pin["default_object"]
        d = pin["default"]
        if d == "" and pin["category"] in ("object", "class", "interface", "softobject", "softclass"):
            return "self" if pin["subcategory"] == "self" or pin["name"] == "self" else "None"
        if pin["category"] == "string" or pin["category"] == "text" or pin["category"] == "name":
            return json.dumps(d) if d != "" else '""'
        return d if d != "" else "?"

    def value_of(self, src, spin, depth):
        """How to name the value produced by output pin `spin` of `src`."""
        c = src["class"]
        outs = [p for p in src["pins"] if p["dir"] == "out" and p["category"] != EXEC and not p["hidden"]]
        pin_suffix = ""
        if spin is not None and len(outs) > 1 and spin["name"] not in ("ReturnValue", "Output", "OutputPin"):
            pin_suffix = f".{spin['name']}"
        if c == "K2Node_VariableGet":
            selfp = next((p for p in src["pins"] if p["dir"] == "in" and p["name"] == "self"), None)
            target = self.expr(src, selfp, depth + 1) + "." if selfp and selfp["links"] else ""
            return target + src["title"][4:] + pin_suffix          # [Target.]VariableName
        if c == "K2Node_Self":
            return "self"
        if c == "K2Node_Literal":
            return src["title"]
        if src["pure"]:
            args = self.args(src, depth + 1)
            return f"{src['title']}({args}){pin_suffix}"
        if c in ("K2Node_DynamicCast", "K2Node_ClassDynamicCast") and spin is not None:
            return spin["name"]                              # AsCharacter
        # impure node output referenced later (e.g. Cast result, event parameter, function entry param)
        if c in ("K2Node_FunctionEntry", "K2Node_Event", "K2Node_CustomEvent", "K2Node_EnhancedInputAction", "K2Node_ComponentBoundEvent", "K2Node_InputAction", "K2Node_ActorBoundEvent") or c.startswith("K2Node_Input"):
            return spin["name"] if spin else "?"
        return f"{self.short(src)}{pin_suffix}"

    def short(self, node):
        return node["title"].split("(")[0]

    def args(self, node, depth=0):
        parts = []
        for p in node["pins"]:
            if p["dir"] != "in" or p["category"] == EXEC or p["hidden"]:
                continue
            if p["name"] == "self" and not p["links"]:
                continue
            v = self.expr(node, p, depth)
            if v == "?" and not p["links"]:
                continue
            parts.append(f"{p['name']}={v}")
        return ", ".join(parts)

    # ---- exec walk
    def walk(self, node_id, indent, visited):
        node = self.nodes.get(node_id)
        if node is None:
            self.lines.append("  " * indent + f"<missing node {node_id}>")
            return
        if node_id in visited:
            self.lines.append("  " * indent + f"↺ back to {self.short(node)}")
            return
        visited = visited | {node_id}
        self.reached.add(node_id)
        outs = [p for p in node["pins"] if p["dir"] == "out" and p["category"] == EXEC]
        if node["class"] == "K2Node_Knot":            # exec reroute: pass through silently
            for p in outs:
                for l in p["links"]:
                    self.walk(l["node"], indent, visited)
            return
        data_outs = [p for p in node["pins"] if p["dir"] == "out" and p["category"] != EXEC and not p["hidden"] and p["links"]]
        if node["class"] == "K2Node_VariableSet":
            var = node["title"][4:]
            valp = next((p for p in node["pins"] if p["dir"] == "in" and p["category"] != EXEC and p["name"] != "self"), None)
            selfp = next((p for p in node["pins"] if p["dir"] == "in" and p["name"] == "self"), None)
            target = self.expr(node, selfp) + "." if selfp and selfp["links"] else ""
            line = f"{target}{var} = {self.expr(node, valp) if valp else '?'}"
            data_outs = []
        elif node["class"] == "K2Node_VariableGet":
            line = f"{node['title']} (validated)"
        else:
            line = f"{node['title']}({self.args(node)})"
        if data_outs and node["class"] not in ("K2Node_IfThenElse", "K2Node_ExecutionSequence"):
            line += "  → " + ", ".join(p["name"] for p in data_outs)
        if node["comment"]:
            line += f"   # {node['comment']}"
        if node.get("error"):
            line += f"   !! {node['error']}"
        self.lines.append("  " * indent + line)
        # single default continuation
        named = [p for p in outs if p["name"] not in ("then", "")]
        then = [p for p in outs if p["name"] in ("then", "")]
        if node["class"] == "K2Node_IfThenElse":
            named, then = outs, []
        for p in then:
            for l in p["links"]:
                self.walk(l["node"], indent, visited)
        labels = {"K2Node_IfThenElse": {"then": "true", "else": "false"},
                  "K2Node_VariableGet": {"then": "Is Valid", "else": "Is Not Valid"}}.get(node["class"], {})
        for p in named:
            if not p["links"]:
                continue
            self.lines.append("  " * indent + f"→ {labels.get(p['name'], p['name'])}:")
            for l in p["links"]:
                self.walk(l["node"], indent + 1, visited)

    def render(self):
        g = self.g
        entries = [n for n in g["nodes"] if any(p["dir"] == "out" and p["category"] == EXEC for p in n["pins"])
                   and not any(p["dir"] == "in" and p["category"] == EXEC for p in n["pins"])]
        entries.sort(key=lambda n: (n["y"], n["x"]))
        self.lines.append(f"## Graph {g['name']} ({g['kind']}; {len(g['nodes'])} nodes, {len(g['comments'])} comments)")
        for e in entries:
            box = self.comment_for(e)
            if box:
                self.lines.append(f"# {box}")
            params = [p["name"] for p in e["pins"] if p["dir"] == "out" and p["category"] != EXEC and not p["hidden"] and p["links"]]
            head = f"{e['title']}" + (f" [{', '.join(params)}]" if params else "")
            if e["comment"]:
                head += f"   # {e['comment']}"
            self.lines.append(head + ":")
            outs = [p for p in e["pins"] if p["dir"] == "out" and p["category"] == EXEC]
            self.reached.add(e["id"])
            any_link = False
            for p in outs:
                if not p["links"]:
                    continue
                any_link = True
                if len(outs) > 1 or p["name"] not in ("then", ""):
                    self.lines.append(f"  → {p['name']}:")
                    for l in p["links"]:
                        self.walk(l["node"], 2, {e["id"]})
                else:
                    for l in p["links"]:
                        self.walk(l["node"], 1, {e["id"]})
            if not any_link:
                self.lines.append("  (no exec connections)")
            self.lines.append("")
        exec_nodes = [n for n in g["nodes"] if any(p["category"] == EXEC for p in n["pins"])]
        unreached = [n for n in exec_nodes if n["id"] not in self.reached]
        if unreached:
            self.lines.append("### Unreached exec nodes (disconnected or orphaned in the graph):")
            for n in unreached:
                self.lines.append(f"- {n['title']} [{n['id']}]")
            self.lines.append("")
        if g["orphans"]:
            self.lines.append(f"(+{len(g['orphans'])} stale objects under this graph not in its Nodes array, ignored)")
            self.lines.append("")
        return "\n".join(self.lines), {"exec_nodes": len(exec_nodes), "reached": len(exec_nodes) - len(unreached),
                                       "unreached": [n["id"] for n in unreached]}

    def comment_for(self, node):
        best = None
        for c in self.g["comments"]:
            if c["x"] <= node["x"] <= c["x"] + c["w"] and c["y"] <= node["y"] <= c["y"] + c["h"]:
                if best is None or c["w"] * c["h"] < best["w"] * best["h"]:
                    best = c
        return best["text"] if best else ""


def render_all(model, only=None):
    out = []
    bp = model.get("blueprint") or {}
    if bp:
        out.append(f"# Blueprint {bp.get('name')} : {bp.get('parent_class') or '?'}")
        if bp.get("description"):
            out.append(f"_{bp['description']}_")
        if bp.get("interfaces"):
            out.append("Interfaces: " + ", ".join(bp["interfaces"]))
        if bp.get("variables"):
            out.append("Variables: " + ", ".join(f"{v['name']}:{v['type']}{'[]' if v['container'] == 'Array' else ''}{' (Replicated)' if v['replicated'] else ''}" for v in bp["variables"]))
        comps = model.get("components")
        if comps:
            out.append("Components: " + ", ".join(f"{c['name']}:{c['class']}" + (f"←{c['parent']}" if c.get("parent") else "") for c in comps))
        elif not bp.get("components_exported"):
            out.append("(Components / SimpleConstructionScript are not part of this export.)")
        out.append("")
    coverage = {"exec_nodes": 0, "reached": 0, "unreached": []}
    for g in model["graphs"]:
        if only and g["name"] not in only:
            continue
        text, cov = Renderer(g).render()
        out.append(text)
        coverage["exec_nodes"] += cov["exec_nodes"]
        coverage["reached"] += cov["reached"]
        coverage["unreached"] += [f"{g['name']}:{i}" for i in cov["unreached"]]
    model["coverage"] = coverage
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", help="T3D file or - for stdin")
    ap.add_argument("--json", help="write the structured model here")
    ap.add_argument("--md", help="write the pseudo-code here instead of stdout")
    ap.add_argument("--graph", action="append", help="only these graph names")
    a = ap.parse_args()
    text = sys.stdin.read() if a.file == "-" else open(a.file, encoding="utf-8", errors="replace").read()
    model = build_model(read_objects(text))
    md = render_all(model, a.graph)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(model, f, ensure_ascii=False, indent=1)
    if a.md:
        with open(a.md, "w", encoding="utf-8") as f:
            f.write(md)
    else:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        print(md)


if __name__ == "__main__":
    main()
