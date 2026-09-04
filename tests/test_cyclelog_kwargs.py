"""Staticky test: ziadne volanie CycleLog(...) nesmie dat ten isty kwarg dvakrat.

POVOD (produkcny pad 4.9.2026, NEAR + BTC): commit 969b1ea pridal odlozeny
verdikt tak, ze `reviewed_trade_id=...` zostal napisany explicitne a HNED POD NIM
pribudol `**({"reviewed_trade_id": ...} if ... else {})`. Python tieto dva
NESPOJI - vyhodi TypeError "got multiple values for keyword argument".

Preco to bolo drahe: pad prisiel AZ PO zaplatenej Claude analyze a PRED zapisom
CycleLog. Bez zapisu ostal ticker navzdy "due", takze scheduler ho spustal znova
kazdych 5 minut - NEAR bezal 8.8 h v kruhu, kazdy beh za plnu cenu, ziadny zaznam
na dashboarde. Presne preto to nechytil ziadny bezny test: cyklus "prebiehal",
len nikdy nedobehol.

Test nespusta ziadny cyklus - cita zdrojak cez AST, takze je zadarmo a rychly.
Podmienene kwargy sa uz musia riesit vypoctom hodnoty PRED volanim (viz
trade_cycle.py, premenna reviewed_trade_id), nie dvojitym zapisom.
"""
import ast
import os
import sys

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<64} {got!r:>8} (ocakavane {want!r})")


def call_name(node):
    """CycleLog(...) aj db.CycleLog(...)."""
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def dup_kwargs(node):
    """Kwargy, ktore su v jednom volani zadane viac nez raz.

    Pozera aj do `**{...}` s doslovnymi klucmi - prave tam sa duplicita skryla.
    `**premenna` sa preskoci (obsah sa staticky zistit neda)."""
    seen, dups = [], []
    for kw in node.keywords:
        names = []
        if kw.arg is not None:
            names = [kw.arg]
        elif isinstance(kw.value, ast.Dict):
            names = [k.value for k in kw.value.keys
                     if isinstance(k, ast.Constant) and isinstance(k.value, str)]
        elif isinstance(kw.value, ast.IfExp):
            # **({"x": ...} if cond else {}) - presne vzor, ktory nas polozil
            for branch in (kw.value.body, kw.value.orelse):
                if isinstance(branch, ast.Dict):
                    names += [k.value for k in branch.keys
                              if isinstance(k, ast.Constant) and isinstance(k.value, str)]
        for n in names:
            if n in seen and n not in dups:
                dups.append(n)
            seen.append(n)
    return dups


TARGETS = ("CycleLog", "Trade", "PriceBar", "RiskOverride", "AtrCalibration",
           "MacroEvent", "PendingRetrospective")

print("1) Duplicitne kwargy v konstruktoroch DB modelov")
total = 0
found = {}
for fname in sorted(os.listdir(_ROOT)):
    if not fname.endswith(".py"):
        continue
    src = open(os.path.join(_ROOT, fname), encoding="utf-8").read()
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        check(f"{fname} sa da parsovat", False, True)
        print(f"       {e}")
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and call_name(node) in TARGETS:
            total += 1
            d = dup_kwargs(node)
            if d:
                found[f"{fname}:{node.lineno}"] = d

check("prehladanych volani konstruktorov", total > 0, True)
for where, names in found.items():
    print(f"       {where} -> {names}")
check("volani s duplicitnym kwargom", len(found), 0)

print("\n2) Regresia 969b1ea: reviewed_trade_id v trade_cycle.py")
tc = open(os.path.join(_ROOT, "trade_cycle.py"), encoding="utf-8").read()
tree = ast.parse(tc)
hits = [n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and call_name(n) == "CycleLog"
        and "reviewed_trade_id" in dup_kwargs(n)]
check("CycleLog s dvojitym reviewed_trade_id", len(hits), 0)

print("\nVYSLEDOK:", "OK" if ok else "CHYBA")
sys.exit(0 if ok else 1)
