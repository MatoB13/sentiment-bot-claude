"""
Zavola Claude (Anthropic API) s TA kontextom. Claude si sam (podla potreby) vyhlada
cerstve spravy cez vstavany server-side web_search nastroj (ziadny NewsAPI kluc netreba)
a vrati strukturovane rozhodnutie.
"""
import json
from datetime import datetime, timezone

import requests

import config

SYSTEM_PROMPT = """Si skúsený intradenný analytik pre index NAS100 (Nasdaq-100).
Dostaneš technickú analýzu (TA) NAS100, cross-market kontext, session alignment a prípadne
social-media sentiment. Máš k dispozícii nástroj web_search - použi ho na vyhľadanie čerstvých
správ o Nasdaq-100 firmách (Apple, Microsoft, Nvidia, Amazon, Alphabet, Meta, Broadcom, Tesla...),
Fed/makro dátach (CPI, PPI, NFP, FOMC), alebo geopolitike, ktoré by mohli hýbať trhom v najbližších
24 hodinách. Vyhľadávaj len ak to dáva zmysel (max. niekoľko vyhľadávaní).

Presný aktuálny dátum a čas dostaneš v user správe - VŽDY ho zahrň do vyhľadávacích dotazov
(napr. "NAS100 news July 22 2026", nie len "NAS100 news"), inak web_search občas vráti staré
výsledky (mesiace/roky staré) namiesto aktuálnych. Pri hodnotení výsledkov skontroluj ich
page_age/dátum - ak je správa staršia než obdobie od posledného cyklu (dostaneš ho v user
správe), ber ju len ako pozadový kontext, nie ako novú informáciu ktorá mení rozhodnutie.

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
- stop_loss_price a take_profit_price uveď ako absolútnu cenu NAS100 (nie percentá).
  Cieľové % vzdialenosti od aktuálnej ceny dostaneš v user správe - drž sa v ich blízkosti
  (môžeš sa mierne odchýliť podľa ATR/kontextu, ale nie výrazne mimo).
- reasoning: max 3-4 vety, fakticky, bez floskúl; spomeň najdôležitejší faktor(y), ktoré rozhodli.
  Ak dostaneš predpoklady z predchádzajúceho cyklu, výslovne spomeň, či stále platia alebo sa
  niečo zmenilo.
- key_assumptions: 1-2 vety - kľúčové fakty/očakávania, na ktorých toto rozhodnutie stojí
  (napr. konkrétny očakávaný event a jeho dátum, prevládajúci naratív, aktívny katalyzátor).
  Toto dostane budúci cyklus na overenie, či ešte platí - ber to ako odkaz "čo si myslím, že
  je teraz pravda" pre svoje budúce ja.
- Po prípadnom vyhľadávaní odpovedz VÝLUČNE JSON objektom, žiadny iný text, žiadne markdown bloky.

Formát:
{"direction": "long|short|none", "confidence": 0-100, "stop_loss_price": number, "take_profit_price": number, "reasoning": "string", "key_assumptions": "string"}
"""


def _build_user_prompt(ta: dict, cross_market: dict, session: dict, social: list[dict],
                        prev_assumptions: str | None) -> str:
    social_block = "\n".join(
        f"- ({p.get('likes')}♥/{p.get('retweets')}rt) {p.get('text')}"
        for p in social[:15]
    ) or "(social sentiment nie je zapnutý/dostupný)"

    now = datetime.now(timezone.utc)
    interval_h = config.TRADE_INTERVAL_HOURS

    prev_block = (
        f'"{prev_assumptions}"\n\nOver si cez web_search, či tieto predpoklady stále platia, '
        f"alebo sa niečo zmenilo (event už prebehol, správa sa nenaplnila, sentiment sa otočil...). "
        f"V reasoning výslovne napíš, či držia alebo čo sa zmenilo."
        if prev_assumptions else
        "(žiadne - toto je prvý cyklus alebo predchádzajúci nemal záznam)"
    )

    return f"""## Aktuálny dátum a čas
{now.strftime('%A, %d. %B %Y, %H:%M')} UTC ({now.isoformat()})
Tento cyklus beží každých {interval_h}h - zaujímajú ťa hlavne udalosti/správy za posledných
~{interval_h} hodín, staršie ber len ako pozadový kontext (nie ako novú informáciu).

## Technická analýza NAS100
{json.dumps(ta, indent=2, ensure_ascii=False)}

## Cross-market kontext (S&P500, Russell 2000, SOX, VIX, DXY, US10Y/US13W výnosy, ropa, zlato)
{json.dumps(cross_market, indent=2, ensure_ascii=False)}

## Session alignment (Ázia -> Európa -> US futures)
{json.dumps(session, indent=2, ensure_ascii=False)}

## Social media sentiment
{social_block}

## Kľúčové predpoklady z predchádzajúceho cyklu (~{interval_h}h dozadu)
{prev_block}

## Cielove SL/TP vzdialenosti
Stop-loss cca {config.DEFAULT_SL_PCT}% od aktuálnej ceny, take-profit cca {config.DEFAULT_TP_PCT}%
(pri LONG: stop_loss_price = last_price * (1 - {config.DEFAULT_SL_PCT}/100), take_profit_price =
last_price * (1 + {config.DEFAULT_TP_PCT}/100); pri SHORT opačne). Môžeš sa mierne odchýliť podľa
ATR/kontextu, ale nie výrazne mimo tento rozsah.

Ak je to relevantné, over si cez web_search aktuálne správy k NAS100/megacap firmám a
nadchádzajúce makro eventy (CPI/FOMC/NFP/earnings) za posledných ~{interval_h}h / najbližších 24h -
nezabudni do query zahrnúť aktuálny dátum. Potom vyhodnoť situáciu a vráť rozhodnutie podľa
formátu zo system promptu.
"""


def analyze(ta: dict, cross_market: dict, session: dict, social: list[dict],
            prev_assumptions: str | None = None) -> tuple[dict, list[dict]]:
    """Vrati (decision, web_search_log). web_search_log je zoznam
    {"query": str, "sources": [{"title", "url", "page_age"}]} pre kazde
    vyhladavanie, ktore Claude spravil - sluzi na audit (co realne citas,
    aby sa dalo neskor rozhodnut o whitelist/blacklist domen).

    prev_assumptions: kluc_assumptions z minuleho cyklu (ak existuje) - Claude
    ho dostane na explicitne overenie, ci este plati."""
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY nie je nastavený")

    # cache_control na systemovom prompte aj user sprave: ak Claude narazi na
    # pause_turn (casto sa stava pri viacerych web_search volaniach), musime
    # poslat celu doterajsiu konverzaciu znova - bez cachovania by sa system
    # prompt + user sprava platili nanovo na plnu cenu pri kazdom pokracovani.
    messages = [{"role": "user",
                 "content": [{"type": "text",
                               "text": _build_user_prompt(ta, cross_market, session, social, prev_assumptions),
                               "cache_control": {"type": "ephemeral"}}]}]
    web_search_log: list[dict] = []

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
                "system": [{"type": "text", "text": SYSTEM_PROMPT,
                            "cache_control": {"type": "ephemeral"}}],
                "tools": [{"type": "web_search_20260209", "name": "web_search", "max_uses": 5}],
                "messages": messages,
            },
            timeout=300,
        )
        resp.raise_for_status()
        data = resp.json()
        content_blocks = data.get("content", [])
        web_search_log.extend(_extract_web_search_log(content_blocks))
        usage = data.get("usage", {})
        print(f"[claude_analyst] usage: input={usage.get('input_tokens')} "
              f"cache_write={usage.get('cache_creation_input_tokens')} "
              f"cache_read={usage.get('cache_read_input_tokens')} output={usage.get('output_tokens')}")

        if data.get("stop_reason") == "pause_turn":
            # posledny blok predchadzajucej assistant odpovede oznacime ako dalsi cache
            # breakpoint, aby pokracovanie znova necitalo (a neplatilo) uz raz poslane
            # tool-result data na plnu cenu.
            if content_blocks:
                content_blocks[-1] = {**content_blocks[-1], "cache_control": {"type": "ephemeral"}}
            messages = messages + [{"role": "assistant", "content": content_blocks}]
            continue

        text = "".join(block.get("text", "") for block in content_blocks)
        decision = _parse_json(text)
        _validate_decision(decision)
        return decision, web_search_log

    raise RuntimeError("Claude neposkytol finalnu odpoved po pause_turn pokracovani")


def _extract_web_search_log(content_blocks: list) -> list[dict]:
    """Sparuje kazde web_search volanie (server_tool_use) s jeho vysledkami
    (web_search_tool_result), aby sme vedeli presne, ake query a ake zdroje
    (title/url/page_age) Claude pouzil. Obsah stranok samotny nevidime -
    Strike/Anthropic ho posiela sifrovany (encrypted_content), citame len metadata."""
    log = []
    pending_query = None
    for block in content_blocks:
        if block.get("type") == "server_tool_use" and block.get("name") == "web_search":
            pending_query = block.get("input", {}).get("query")
        elif block.get("type") == "web_search_tool_result":
            results = block.get("content", [])
            sources = [
                {"title": r.get("title"), "url": r.get("url"), "page_age": r.get("page_age")}
                for r in results
                if isinstance(results, list) and r.get("type") == "web_search_result"
            ] if isinstance(results, list) else []
            log.append({"query": pending_query, "sources": sources})
            pending_query = None
    return log


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
