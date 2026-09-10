"""429 z Anthropic API: viac trpezlivosti a hlavne ZNAMA PRICINA.

POVOD (produkcia 2026-09-10): XAU (03:18 UTC) a WTI (05:28) padli po troch 429
za sebou, ZHIPU (06:07) presiel az na treti pokus. Kvoty organizacie aj
workspace mali v konzole vrchol 1 % - pricina bola teda mimo nich, lenze
_post_messages volal raise_for_status(), ktore telo odpovede aj rate-limit
hlavicky zahodi. Zostalo len "429 Client Error: Too Many Requests" a nedalo sa
zistit, ktory limit sme trafili.

Test NIKDY nevola skutocne API - requests.post aj time.sleep su nahradene.
"""
import os as _os
_ROOT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
import os
import sys

os.environ["DATABASE_URL"] = "sqlite:///" + os.environ["TEMP"].replace("\\", "/") + "/a429.db"
sys.path.insert(0, _ROOT)

import requests  # noqa: E402

import claude_analyst as ca  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK ' if good else 'CHYBA'} {label:<60} {got!r} (ocakavane {want!r})")


class FakeResp:
    def __init__(self, status, body=None, headers=None, text=None):
        self.status_code = status
        self._body = body
        self.headers = requests.structures.CaseInsensitiveDict(headers or {})
        self.text = text if text is not None else ("" if body is None else str(body))

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


RL_BODY = {"type": "error", "request_id": "req_abc",
           "error": {"type": "rate_limit_error",
                     "message": "This request would exceed your rate limit."}}
RL_HEADERS = {
    "retry-after": "12",
    "request-id": "req_abc",
    "anthropic-ratelimit-requests-limit": "10000",
    "anthropic-ratelimit-requests-remaining": "9995",
    "anthropic-ratelimit-requests-reset": "2026-09-10T06:08:00Z",
    "anthropic-ratelimit-input-tokens-limit": "10000000",
    "anthropic-ratelimit-input-tokens-remaining": "0",
    # Druh, ktory nepozname - NESMIE sa stratit, o to tu ide.
    "anthropic-ratelimit-mystery-thing": "42",
}


def rl():
    return FakeResp(429, RL_BODY, RL_HEADERS)


OK_RESP = FakeResp(200, {"content": []})

sleeps = []
queue = []


def fake_post(*a, **k):
    item = queue.pop(0)
    if isinstance(item, Exception):
        raise item
    return item


ca.requests.post = fake_post
ca.time.sleep = lambda s: sleeps.append(s)


def run(responses):
    sleeps.clear()
    queue[:] = list(responses)
    try:
        return ca._post_messages({"x": 1}, "TEST"), None
    except Exception as e:  # noqa: BLE001
        return None, e


print("1) ZHIPU scenar - 429, 429, 429 a potom prejde")
resp, err = run([rl(), rl(), rl(), OK_RESP])
check("styri pokusy staci na uspech", resp is OK_RESP, True)
check("ziadna vynimka", err, None)
check("trikrat sa cakalo", len(sleeps), 3)

print("\n2) Stvrty 429 za sebou uz konci chybou - a ta nesie PRICINU")
resp, err = run([rl(), rl(), rl(), rl()])
check("vynimka je AnthropicAPIError", type(err).__name__, "AnthropicAPIError")
check("je to stale requests.HTTPError", isinstance(err, requests.HTTPError), True)
msg = str(err)
check("text nesie typ chyby z tela", "rate_limit_error" in msg, True)
check("text nesie spravu z tela", "would exceed your rate limit" in msg, True)
check("text nesie retry-after", "retry-after=12s" in msg, True)
check("text nesie request-id", "req_abc" in msg, True)
check("text nesie input-tokens zostava/limit", "input-tokens 0/10000000" in msg, True)
check("text nesie requests zostava/limit", "requests 9995/10000" in msg, True)
check("NEZNAMY druh limitu sa nestratil", "mystery-thing=42" in msg, True)
check("povie pocet pokusov", "po 4 pokusoch" in msg, True)
check("response je pripojena", err.response.status_code, 429)
# Presne takto sa to dostane do banneru pri health checku (trade_cycle.py).
check("health_check_failed riadok nesie pricinu",
      "rate_limit_error" in f"health_check_failed: {err}", True)

print("\n3) Cakanie: retry-after len PREDLZUJE, nikdy neskracuje")
check("retry-after 12 s -> 60 s (spodna hranica)", _w := ca._retry_wait_seconds(rl()), 60)
long_ra = FakeResp(429, RL_BODY, {**RL_HEADERS, "retry-after": "150"})
check("retry-after 150 s -> 150 s", ca._retry_wait_seconds(long_ra), 150)
huge_ra = FakeResp(429, RL_BODY, {**RL_HEADERS, "retry-after": "99999"})
check("retry-after 99999 s -> strop 180 s", ca._retry_wait_seconds(huge_ra), 180)
no_ra = FakeResp(429, RL_BODY, {})
check("bez retry-after -> doterajsich 60 s", ca._retry_wait_seconds(no_ra), 60)
bad_ra = FakeResp(429, RL_BODY, {"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"})
check("datumovy tvar sa ignoruje (60 s)", ca._retry_wait_seconds(bad_ra), 60)
resp, err = run([long_ra, OK_RESP])
check("slucka naozaj pouzila 150 s", sleeps, [150])

print("\n4) Ostatne prechodne chyby maju pocet pokusov BEZ ZMENY")
r503 = FakeResp(503, None, {}, text="<html>503 Service Temporarily Unavailable</html>")
resp, err = run([r503, r503, r503])
check("503 konci po 3 pokusoch (ako predtym)", type(err).__name__, "AnthropicAPIError")
check("cakalo sa dvakrat", len(sleeps), 2)
check("HTML telo sa ulozi ako text", "503 Service Temporarily Unavailable" in str(err), True)

print("\n5) Chyba, ktora prechodna nie je, sa neopakuje - ale uz povie preco")
r400 = FakeResp(400, {"type": "error", "error": {"type": "invalid_request_error",
                                                 "message": "max_tokens: too large"}})
resp, err = run([r400])
check("400 bez opakovania", sleeps, [])
check("text nesie dovod 400", "invalid_request_error: max_tokens: too large" in str(err), True)
check("jednotne cislo pri jednom pokuse", "po 1 pokuse" in str(err), True)

print("\n6) Sietova chyba ide po starom (3 pokusy, potom povodna vynimka)")
boom = requests.exceptions.ConnectionError("reset")
resp, err = run([boom, boom, boom])
check("po 3 pokusoch vyleti ConnectionError", type(err).__name__, "ConnectionError")
check("cakalo sa dvakrat po 60 s", sleeps, [60, 60])
resp, err = run([boom, OK_RESP])
check("sietova chyba a potom uspech", resp is OK_RESP, True)

print("\n7) Uspech na prvy pokus sa nemeni vobec")
resp, err = run([OK_RESP])
check("vrati odpoved", resp is OK_RESP, True)
check("ziadne cakanie", sleeps, [])

print("\nVYSLEDOK:", "OK" if ok else "CHYBA")
sys.exit(0 if ok else 1)
