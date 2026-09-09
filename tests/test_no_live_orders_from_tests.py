"""Test NIKDY nesmie zadat objednavku na zivej burze.

POVOD - realny incident 2026-09-06: pri pisani testu na position_monitor som
vytvoril fiktivny obchod (WTI-USD, velkost 1, TP 102.0). Test presiel vetvou
_check_and_reheal_bracket_legs, ktora zavolala strike_client.
place_take_profit_order. `config.py` robi load_dotenv() a v repozitari je `.env`
s OSTRYMI klucmi, takze objednavka sa NAOZAJ zadala na zivom ucte pouzivatela
(Take Profit Limit, Short, 1 OIL @ 102.00) a musel ju rucne zrusit.

Poucenie: nestaci "davat pozor" ani stubovat volania v kazdom teste zvlast -
staci jeden zabudnuty stub a objednavka odide. Preto je zamka v samotnom
strike_client._request: cez neho prejde KAZDA poziadavka, takze plati aj pre
endpointy, ktore este neexistuju, aj ked sa test spusti PRIAMO
(`python tests/test_x.py`) - prave tak ten incident vznikol, nastavenie
premennych v run_all.py by ho nezachytilo.

Tento test strazi tu zamku samotnu.
"""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys

os.environ["DATABASE_URL"] = "sqlite:///" + os.environ["TEMP"].replace("\\", "/") + "/nolive.db"
sys.path.insert(0, _ROOT)

import strike_client  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<58} {got!r:>9} (ocakavane {want!r})")


def blocked(fn, *a, **k):
    """True ak volanie zamka zastavila (a NEodoslala nic na siet)."""
    try:
        fn(*a, **k)
        return False
    except AssertionError as e:
        return "ZABLOKOVANE" in str(e)
    except Exception:
        # Ina vynimka = poziadavka sa pokusila odist (sietova/auth chyba).
        return False


print("1) Bezime v testovacom procese - zamka musi byt aktivna")
check("_in_test_process()", strike_client._in_test_process(), True)

print("\n2) Kazda MUTUJUCA operacia je zablokovana")
# Presne to volanie, ktore ten incident sposobilo, je prve.
check("place_take_profit_order (incident 6.9.)",
      blocked(strike_client.place_take_profit_order, "WTI-USD", "Short", 1.0, 102.0), True)
check("place_stop_order",
      blocked(strike_client.place_stop_order, "WTI-USD", "Short", 1.0, 99.0), True)
check("open_bracket_position",
      blocked(strike_client.open_bracket_position, "Long", 1.0, 10, 99.0, 102.0,
              "WTI-USD"), True)
check("cancel_all_orders", blocked(strike_client.cancel_all_orders, "WTI-USD"), True)
check("close_position_market",
      blocked(strike_client.close_position_market, "Long", 1.0, "WTI-USD"), True)
check("set_leverage", blocked(strike_client.set_leverage, "WTI-USD", 10), True)
check("set_margin_mode", blocked(strike_client.set_margin_mode, "WTI-USD", "cross"), True)

print("\n3) Zamka je na UROVNI _request, takze chyti aj buduce endpointy")
for method in ("POST", "PUT", "PATCH", "DELETE"):
    check(f"{method} na vymysleny endpoint",
          blocked(strike_client._request, method, "/v2/nieco/co/este/neexistuje", {"x": 1}), True)

print("\n4) GET sa neblokuje (citanie je neskodne, testy by inak zbytocne padali)")
# Nevolame realny GET (siet), overujeme len ze zamka doň nezasahuje.
try:
    strike_client._request("GET", "/v2/markets")
    reached_network = True
except AssertionError as e:
    reached_network = "ZABLOKOVANE" not in str(e)
except Exception:
    reached_network = True  # sietova/auth chyba = zamka ho pustila dalej
check("GET zamka nezastavi", reached_network, True)

print("\n5) Detekcia nezavisi na nazve suboru, ale na priecinku `tests`")
real_argv = sys.argv[0]
# POZOR na tvar tejto cesty: os.path.join("C:", "app", ...) da na Windows
# "C:app\..." - to je DRIVE-RELATIVNA cesta a _in_test_process ju cez abspath()
# dolozi aktualnym priecinkom. Ked sa suita spustila z tests/, vyslo z toho
# "...\tests\app\main.py" a tento test padol, hoci zamka bola v poriadku.
# os.path.abspath(os.sep) da skutocny koren ("C:\" resp. "/") na oboch OS.
_ABS_ROOT = os.path.abspath(os.sep)
try:
    sys.argv[0] = os.path.join(_ABS_ROOT, "app", "main.py")
    check("produkcny beh zamku NEaktivuje", strike_client._in_test_process(), False)
    sys.argv[0] = os.path.join(_ABS_ROOT, "app", "tests", "cokolvek.py")
    check("hocijaky subor v tests/ ju aktivuje", strike_client._in_test_process(), True)
    sys.argv[0] = os.path.join(_ABS_ROOT, "app", "main.py")
    os.environ["PYTEST_CURRENT_TEST"] = "x"
    check("pytest ju aktivuje tiez", strike_client._in_test_process(), True)
    del os.environ["PYTEST_CURRENT_TEST"]
finally:
    sys.argv[0] = real_argv

print("\n6) Vedomy unik existuje, ale testy ho nesmu zdedit z prostredia")
os.environ["STRIKE_CLIENT_ALLOW_MUTATIONS"] = "1"
check("s premennou sa zamka vypne", strike_client._in_test_process(), False)
del os.environ["STRIKE_CLIENT_ALLOW_MUTATIONS"]
check("bez nej je zas aktivna", strike_client._in_test_process(), True)
# run_all.py ju z prostredia testov ODSTRANUJE - over, ze to tam naozaj stoji.
run_all = open(os.path.join(_ROOT, "tests", "run_all.py"), encoding="utf-8").read()
check("run_all.py ju zahadzuje",
      'env.pop("STRIKE_CLIENT_ALLOW_MUTATIONS", None)' in run_all, True)
check("a nastavuje neplatne kluce", '"STRIKE_API_PRIVATE_KEY": ""' in run_all, True)

print("\nVYSLEDOK:", "OK" if ok else "CHYBA")
sys.exit(0 if ok else 1)
