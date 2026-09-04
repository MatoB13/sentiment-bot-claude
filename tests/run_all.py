"""Spusti vsetky testy v tomto priecinku a vypise suhrn.

    python tests/run_all.py            # vsetky
    python tests/run_all.py triage tp  # len tie, ktorych meno obsahuje "triage" alebo "tp"

Kazdy test je samostatny skript (ziadny pytest) - bezi vo vlastnom procese s
vlastnou sqlite DB v TEMP, takze sa navzajom neovplyvnuju a nikdy sa nedotknu
produkcnej Postgres DB. Uspech = navratovy kod 0.

POZOR: testy stavaju na tom, ze `DATABASE_URL` ukazuje na sqlite - kazdy si ho
nastavuje sam PRED importom db.py. Nespustaj ich s nastavenym DATABASE_PUBLIC_URL
v prostredi, ak nechces, aby niektore (test_prompt) citali produkcnu DB.
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    patterns = [a.lower() for a in sys.argv[1:]]
    names = sorted(f[:-3] for f in os.listdir(HERE)
                   if f.startswith("test_") and f.endswith(".py"))
    if patterns:
        names = [n for n in names if any(p in n.lower() for p in patterns)]
    if not names:
        print("Ziadny test nesedi na zadany vzor.")
        return 1

    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    failed, t0 = [], time.time()
    for name in names:
        started = time.time()
        # encoding/errors explicitne: bez toho pouzije Windows cp1252 a spadne na
        # diakritike vo vypise testu (UnicodeDecodeError uprostred behu).
        r = subprocess.run([sys.executable, os.path.join(HERE, name + ".py")],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, timeout=300)
        took = time.time() - started
        if r.returncode == 0:
            print(f"  OK     {name:<28} {took:5.1f}s")
        else:
            failed.append(name)
            print(f"  ZLYHAL {name:<28} {took:5.1f}s")
            tail = [ln for ln in (r.stdout + r.stderr).splitlines()
                    if "CHYBA" in ln or "Error" in ln or "assert" in ln.lower()]
            for ln in tail[-4:]:
                print(f"         {ln.strip()[:110]}")

    print(f"\n  {len(names) - len(failed)}/{len(names)} preslo za {time.time() - t0:.0f}s")
    if failed:
        print("  ZLYHALI: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
