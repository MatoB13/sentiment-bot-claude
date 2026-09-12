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

    # 2026-09-06 - druha vrstva ochrany pred zadanim objednavky z testu.
    # Prva (a dolezitejsia) je zamka v strike_client._request, ktora blokuje
    # kazdu ne-GET poziadavku v testovacom procese - ta plati aj pri priamom
    # `python tests/test_x.py`, cim ten realny incident vznikol. Toto je len
    # opasok navyse: aj keby sa zamka niekedy obisla, kluce su neplatne a burza
    # by poziadavku odmietla. STRIKE_CLIENT_ALLOW_MUTATIONS sa zahadzuje, aby
    # ju testy nemohli zdedit z prostredia.
    env = {**os.environ, "PYTHONIOENCODING": "utf-8",
           "STRIKE_API_PRIVATE_KEY": "",
           "STRIKE_API_PUBLIC_KEY": ""}
    env.pop("STRIKE_CLIENT_ALLOW_MUTATIONS", None)
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
            # Bez zachyteneho riadku (napr. natívny pad procesu bez tracebacku)
            # aspon navratovy kod a koniec vystupu - inak nie je co hladat.
            if not tail:
                print(f"         navratovy kod {r.returncode}")
                tail = (r.stdout + r.stderr).splitlines()
            for ln in tail[-4:]:
                print(f"         {ln.strip()[:110]}")

    print(f"\n  {len(names) - len(failed)}/{len(names)} preslo za {time.time() - t0:.0f}s")
    if failed:
        print("  ZLYHALI: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
