#!/usr/bin/env python3
"""Run a Python LeetCode solution against JSON test cases and print a plain-text report.

Usage: run_cases.py solution.py cases.json [--func NAME]
cases.json: [{"args": [...], "expected": ...}, ...]   (args are positional)
The function under test is --func, else the single top-level function, else `Solution().<method>` if a class exists.
Order-insensitive comparison is used when expected is a list and "unordered": true is set on the case.
Exit code 0 when all pass, 1 otherwise. Report goes to stdout (feed it to judge.py as test_results).
"""
import importlib.util, inspect, json, sys, traceback

def load_fn(path, name):
    spec = importlib.util.spec_from_file_location("solution", path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    if name:
        return getattr(m, name) if hasattr(m, name) else getattr(m.Solution(), name)
    fns = [f for n, f in inspect.getmembers(m, inspect.isfunction) if f.__module__ == "solution"]
    if len(fns) == 1:
        return fns[0]
    if hasattr(m, "Solution"):
        meths = [n for n, _ in inspect.getmembers(m.Solution, inspect.isfunction) if not n.startswith("_")]
        if len(meths) == 1:
            return getattr(m.Solution(), meths[0])
    sys.exit("cannot pick function; pass --func NAME")

def main():
    a = sys.argv[1:]
    name = None
    if "--func" in a:
        i = a.index("--func"); name = a[i + 1]; del a[i:i + 2]
    sol, cases = a
    fn = load_fn(sol, name)
    cases = json.load(open(cases))
    ok = 0
    for c in cases:
        try:
            got = fn(*[json.loads(json.dumps(x)) for x in c["args"]])  # deep copy: solutions may mutate
            exp = c["expected"]
            same = sorted(map(json.dumps, got)) == sorted(map(json.dumps, exp)) if c.get("unordered") else got == exp
            ok += same
            print(f"{'OK  ' if same else 'FAIL'} {fn.__name__}{tuple(c['args'])} -> {got!r} expected {exp!r}")
        except Exception:
            print(f"ERR  {fn.__name__}{tuple(c['args'])} raised:\n{traceback.format_exc().strip().splitlines()[-1]}")
    print(f"{ok}/{len(cases)} passed")
    sys.exit(0 if ok == len(cases) else 1)

if __name__ == "__main__":
    main()
