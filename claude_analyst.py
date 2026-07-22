"""
Zavola Claude (Anthropic API) s TA kontextom. Claude si sam (podla potreby) vyhlada
cerstve spravy cez vstavany server-side web_search nastroj (ziadny NewsAPI kluc netreba)
a vrati strukturovane rozhodnutie.
"""
import json

import requests

import config

SYSTEM_PROMPT = """Si skúsený intradenný analytik pre index NAS100 (Nasdaq-100).
Dostaneš technickú analýzu (TA) NAS100, cross-market kontext, session alignment a prípadne
social-media sentiment. Máš k dispozícii nástroj web_search - použi ho na vyhľadanie čerstvých
správ o Nasdaq-100 firmách (Apple, Microsoft, Nvidia, Amazon, Alphabet, Meta, Broadcom, Tesla...),
Fed/makro dátach (CPI, PPI, NFP, FOMC), alebo geopolitike, ktoré by mohli hýbať trhom v najbližších
24 hodinách. Vyhľadávaj len ak to dáva zmysel (max. niekoľko vyhľadávaní).

Tvoja úloha je vyhodnotiť, či má zmysel otvoriť LONG, SHORT, alebo neobchodovať (NONE)
na horizont max. 24 hodín, s konkrétnym stop-lossom a take-profitom.

Ako syntetizovať viacero signálov (nepočítaj váhy mechanicky, posúď to ako skúsený analytik):
- **Cross-market konfirmácia**: Ak S&P500, Russell 2000 aj SOX (semikondukcia) potvrdzujú
  smer NAS100, zvyšuje to istotu. Divergencia (napr. SOX klesá kým NAS100 rastie) je varovanie.
- **VIX režim**: Rastúci VIX = risk-off nálada, najmä ak NAS100 zároveň rastie (divergencia =
  krehký rally). Nízky/klesajúci VIX podporuje trendové pokračovanie.
- **Dlhopisy (US10Y/US13W)**: Rýchlo rastúce výnosy zvyknú tlačiť na rastové/tech akcie (NAS100
  je citlivé na reálne výnosy) - ber to ako protivietor pre LONG ak výnosy prudko rastú.
- **Ropa/zlato**: Prudký nárast oboch naraz často signalizuje geopolitické riziko/inflačné obavy.
- **Session alignment**: Zhoda smeru Ázia → Európa → US futures zvyšuje istotu; nezhoda znižuje.
- **Market Reaction Score**: Kľúčové - ak sú správy pozitívne ale cena/futures nereagujú rastom
  (alebo naopak), to hovorí viac než samotná správa. Vždy porovnaj obsah správ s reálnou cenovou
  reakciou.
- **Event Risk Gate**: Ak cez web_search zistíš, že sa v najbližších hodinách očakáva veľký
  makro report (CPI, FOMC rozhodnutie, NFP) alebo kľúčové earnings megacap firiem, buď výrazne
  konzervatívnejší (nízka confidence alebo "none") - volatilita okolo takých eventov je
  nepredvídateľná aj pri jasnom technickom obraze.

Pravidlá:
- Buď konzervatívny: ak signály nie sú jasné alebo sú protichodné, zvoľ "none" a nízku confidence.
- confidence je 0-100 a má odrážať reálnu neistotu (60 je "mierne naklonený", 90+ je vzácne).
- stop_loss_price a take_profit_price uveď ako absolútnu cenu NAS100 (nie percentá),
  vychádzajúc z last_price a ATR (napr. SL cca 1-1.5x ATR, TP s pomerom risk:reward aspoň 1:1.5).
- reasoning: max 3-4 vety, fakticky, bez floskúl; spomeň najdôležitejší faktor(y), ktoré rozhodli.
- Po prípadnom vyhľadávaní odpovedz VÝLUČNE JSON objektom, žiadny iný text, žiadne markdown bloky.

Formát:
{"direction": "long|short|none", "confidence": 0-100, "stop_loss_price": number, "take_profit_price": number, "reasoning": "string"}
"""


def _build_user_prompt(ta: dict, cross_market: dict, session: dict, social: list[dict]) -> str:
    social_block = "\n".join(
        f"- ({p.get('likes')}♥/{p.get('retweets')}rt) {p.get('text')}"
        for p in social[:15]
    ) or "(social sentiment nie je zapnutý/dostupný)"

    return f"""## Technická analýza NAS100
{json.dumps(ta, indent=2, ensure_ascii=False)}

## Cross-market kontext (S&P500, Russell 2000, SOX, VIX, DXY, US10Y/US13W výnosy, ropa, zlato)
{json.dumps(cross_market, indent=2, ensure_ascii=False)}

## Session alignment (Ázia -> Európa -> US futures)
{json.dumps(session, indent=2, ensure_ascii=False)}

## Social media sentiment
{social_block}

Ak je to relevantné, over si cez web_search aktuálne správy k NAS100/megacap firmám a
nadchádzajúce makro eventy (CPI/FOMC/NFP/earnings) za posledných ~12 hodín / najbližších 24h.
Potom vyhodnoť situáciu a vráť rozhodnutie podľa formátu zo system promptu.
"""


def analyze(ta: dict, cross_market: dict, session: dict, social: list[dict]) -> dict:
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY nie je nastavený")

    messages = [{"role": "user", "content": _build_user_prompt(ta, cross_market, session, social)}]

    # server-side web_search moze pri velmi dlhom hladani vratit stop_reason=pause_turn -
    # v takom pripade treba poslat konverzaciu znova a nechat ju dokoncit (max 1 pokracovanie).
    for _ in range(2):
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": config.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": config.CLAUDE_MODEL,
                "max_tokens": 4096,
                "system": SYSTEM_PROMPT,
                "tools": [{"type": "web_search_20260209", "name": "web_search", "max_uses": 5}],
                "messages": messages,
            },
            timeout=300,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("stop_reason") == "pause_turn":
            messages = messages + [{"role": "assistant", "content": data["content"]}]
            continue

        text = "".join(block.get("text", "") for block in data.get("content", []))
        decision = _parse_json(text)
        _validate_decision(decision)
        return decision

    raise RuntimeError("Claude neposkytol finalnu odpoved po pause_turn pokracovani")


def _parse_json(text: str) -> dict:
    text = text.strip()
    start = text.find("{")
    if start == -1:
        raise ValueError(f"Claude nevrátil validný JSON: {text!r}")
    candidate = text[start:]

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # Model obcas vynecha zatvaraciu zlozenu zatvorku na konci objektu - dohodneme ju.
    if not candidate.endswith("}"):
        try:
            return json.loads(candidate + "}")
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Claude nevrátil validný JSON: {text!r}")


def _validate_decision(decision: dict) -> None:
    required = {"direction", "confidence", "stop_loss_price", "take_profit_price", "reasoning"}
    missing = required - decision.keys()
    if missing:
        raise ValueError(f"Chýbajúce polia v rozhodnutí: {missing}")
    if decision["direction"] not in ("long", "short", "none"):
        raise ValueError(f"Neplatný smer: {decision['direction']}")
    if not (0 <= decision["confidence"] <= 100):
        raise ValueError(f"Neplatná confidence: {decision['confidence']}")
