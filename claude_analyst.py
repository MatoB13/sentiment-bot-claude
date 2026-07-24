"""
Zavola Claude (Anthropic API) s TA kontextom pre dany asset (NAS100/NVDA/ADA).
Claude si sam (podla potreby) vyhlada cerstve spravy cez vstavany server-side
web_search nastroj (ziadny NewsAPI kluc netreba) a vrati strukturovane
rozhodnutie. System aj user prompt su parametrizovane podla assets.py profilu -
rovnaky syntetizacny ramec (cross-market/VIX/session/event-risk-gate) ako pri
NAS100, len s inym news-focusom a (pre krypto) inou vahou makro signalov.

Rozhodnutie sa ziska cez tool-use (submit_trade_decision), nie parsovanim
volneho textu ako JSON - Anthropic API garantuje, ze tool input je synakticky
validny podla schemy, cim odpada cela trieda bugov s pokazenym volnym JSON
textom (markdown fence, zle escapovane znaky, bludiace znaky a pod. - vsetko
sa to v praxi stalo, kym sme parsovali text rucne)."""
import json
from datetime import datetime, timezone

import requests

import config
import market_data

DECISION_TOOL = {
    "name": "submit_trade_decision",
    "description": (
        "Odovzdaj finalne obchodne rozhodnutie. Zavolaj tento nastroj VZDY ako "
        "posledny krok analyzy, po dokonceni pripadneho web_search prieskumu - "
        "je to jediny sposob, ako rozhodnutie odovzdat."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "direction": {
                "type": "string", "enum": ["long", "short", "none"],
                "description": "Obchodny smer.",
            },
            "confidence": {
                "type": "integer", "minimum": 0, "maximum": 100,
                "description": "0-100, realna neistota (60 = mierne naklonený, 90+ vzacne).",
            },
            "stop_loss_price": {
                "type": "number",
                "description": "Absolutna cena stop-lossu (nie percenta).",
            },
            "take_profit_price": {
                "type": "number",
                "description": "Absolutna cena take-profitu (nie percenta).",
            },
            "reasoning": {
                "type": "string",
                "description": "Max 3-4 vety, fakticky, bez floskul.",
            },
            "key_assumptions": {
                "type": "string",
                "description": "1-2 vety - kluc. fakty/ocakavania, na ktorych rozhodnutie stoji.",
            },
            "watch_price": {
                "type": "number",
                "description": (
                    "Volitelne - len ked direction=none a vidis konkretnu uroven na "
                    "sledovanie. Vynechaj cely field, ak nie je relevantny."
                ),
            },
            "watch_direction": {
                "type": "string", "enum": ["above", "below"],
                "description": "Volitelne, vzdy spolu s watch_price.",
            },
        },
        "required": ["direction", "confidence", "stop_loss_price", "take_profit_price",
                     "reasoning", "key_assumptions"],
    },
}

_EQUITY_MACRO_RULES = """- **Cross-market konfirmácia**: Ak S&P500, Russell 2000 aj SOX (semikondukcia) potvrdzujú
  smer {instrument}, zvyšuje to istotu. Divergencia (napr. SOX klesá kým {instrument} rastie) je varovanie.
- **VIX režim**: Rastúci VIX = risk-off nálada, najmä ak {instrument} zároveň rastie (divergencia =
  krehký rally). Nízky/klesajúci VIX podporuje trendové pokračovanie.
- **Dlhopisy (US10Y/US13W)**: Rýchlo rastúce výnosy zvyknú tlačiť na rastové/tech akcie ({instrument}
  je citlivé na reálne výnosy) - ber to ako protivietor pre LONG ak výnosy prudko rastú.
- **Ropa/zlato**: Prudký nárast oboch naraz často signalizuje geopolitické riziko/inflačné obavy.
- **Session alignment**: Zhoda smeru Ázia → Európa → US futures zvyšuje istotu; nezhoda znižuje.
- **Market Reaction Score**: Kľúčové - ak sú správy pozitívne ale cena/futures nereagujú rastom
  (alebo naopak), to hovorí viac než samotná správa. Vždy porovnaj obsah správ s reálnou cenovou
  reakciou.
- **Event Risk Gate**: Ak cez web_search zistíš, že sa v najbližších hodinách očakáva veľký
  makro report (CPI, FOMC rozhodnutie, NFP) alebo kľúčové earnings megacap firiem (vrátane {instrument}
  samotného, ak je to jednotlivá akcia), buď výrazne konzervatívnejší (nízka confidence alebo
  "none") - volatilita okolo takých eventov je nepredvídateľná aj pri jasnom technickom obraze."""

_CRYPTO_MACRO_RULES = """- **BTC beta**: {instrument} sa dlhodobo správa ako vysoko-beta krypto asset voči BTC - ak BTC
  prudko rastie/klesá, {instrument} to zvykne nasledovať (často zosilnene). Divergencia (BTC
  stabilný, {instrument} sa sám prudko hýbe) znamená idiosynkratický katalyzátor, nie širší trh -
  vtedy je dôležitejšie špecifické spravodajstvo než BTC proxy dáta.
- **Rizikový režim cez equity trhy**: S&P500/Nasdaq a VIX sú sekundárny, ale relevantný kontext -
  krypto sa obchoduje čiastočne ako risk-on/off asset korelovaný s akciami, najmä pri veľkých
  makro eventoch (Fed, CPI). Neber to ako hlavný signál, len ako potvrdenie/varovanie.
- **Dolár/dlhopisy (DXY/výnosy)**: rýchlo rastúci dolár a výnosy sú všeobecný protivietor pre
  rizikové aktíva vrátane krypta, ale vplyv je slabší a pomalší než pri akciách.
- **Session alignment**: menej relevantné pre krypto (obchoduje sa 24/7) - ber len ako slabý
  kontext risk-on/off nálady z Ázie/Európy/US, nie ako priamy signál pre {instrument}.
- **Market Reaction Score**: Kľúčové - ak sú správy pozitívne ale cena/BTC nereaguje rastom
  (alebo naopak), to hovorí viac než samotná správa.
- **Event Risk Gate**: Ak cez web_search zistíš významný krypto-špecifický event (SEC rozhodnutie,
  veľký protokolový upgrade/hardfork, hack/exploit v ekosystéme, veľká burzová likvidačná kaskáda)
  alebo makro event (CPI/FOMC/NFP), buď výrazne konzervatívnejší (nízka confidence alebo "none")."""

_COMMODITY_MACRO_RULES = """- **Reálne výnosy (US10Y) a DXY sú hlavný hýbateľ**: Rýchlo rastúce výnosy/dolár sú protivietor pre
  {instrument} (vyššia opportunity cost držania neúročeného aktíva), klesajúce výnosy/dolár sú vietor
  v chrbát. Toto je zvyčajne silnejší signál než čokoľvek iné v cross-market bloku.
- **VIX režim - OPAČNÝ vzťah než pri akciách/kryptu**: Rastúci VIX (risk-off) je zvyčajne BÝČÍ signál
  pre {instrument} (safe-haven dopyt) - presný opak toho, ako VIX pôsobí na rizikové aktíva. Ak VIX
  rastie ale {instrument} nereaguje rastom, je to dôležitá divergencia - over, či risk-off nie je
  spôsobený práve rastúcimi výnosmi (to je pre {instrument} protichodný signál k safe-haven dopytu).
- **Cross-market kontext ako diagnostika POVAHY risk-off, nie priamy signál**: Ak S&P500/Nasdaq
  padajú súčasne s rastúcim VIX A rastúcimi výnosmi, over či ide o "flight to safety" (býčie pre
  {instrument}) alebo "risk-off kvôli vyšším sadzbám" (protichodné signály - vyššie výnosy tlačia
  dole, strach tlačí hore, čistý efekt nejasný, buď opatrnejší).
- **Geopolitické riziko**: Eskalácia (vojenský konflikt, sankcie, obchodné vojny) je zvyčajne býčí
  katalyzátor pre {instrument} nezávisle od ostatných faktorov.
- **Centrálne banky/inštitucionálny dopyt**: Správy o veľkých nákupoch zlata centrálnymi bankami
  (najmä PBOC a iné EM centrálne banky diverzifikujúce od USD) sú strednodobý býčí naratív.
- **Market Reaction Score**: rovnako dôležité ako inde - porovnaj obsah správy s reálnou cenovou
  reakciou {instrument}.
- **Event Risk Gate**: FOMC/CPI/PPI/NFP sú KĽÚČOVÉ eventy pre {instrument} (priamo hýbu výnosmi/DXY
  očakávaniami) - pred takým eventom buď výrazne konzervatívnejší (nízka confidence alebo "none")."""

ASSET_TEXT = {
    "NAS100": {
        "label": "index NAS100 (Nasdaq-100)",
        "news_focus": (
            'správy o Nasdaq-100 firmách (Apple, Microsoft, Nvidia, Amazon, Alphabet, Meta, '
            'Broadcom, Tesla...), Fed/makro dátach (CPI, PPI, NFP, FOMC), alebo geopolitike'
        ),
        "macro_rules": _EQUITY_MACRO_RULES,
    },
    "NVDA": {
        "label": "akciu NVDA (Nvidia)",
        "news_focus": (
            'správy o Nvidii samotnej (earnings, guidance, produktové announcementy), '
            'AI-capex objednávkach veľkých zákazníkov (Microsoft, Meta, Google, Amazon, OpenAI), '
            'exportných reštrikciách na Čínu, konkurencii (AMD, Broadcom custom silicon, Google '
            'TPU) a dodávateľskom reťazci (TSMC, SK Hynix, Samsung), popri Fed/makro dátach '
            '(CPI, PPI, NFP, FOMC)'
        ),
        "macro_rules": _EQUITY_MACRO_RULES,
    },
    "ADA": {
        "label": "krypto ADA (Cardano) perpetuál",
        "news_focus": (
            'správy o Cardano ekosystéme (governance/Voltaire hlasovania, protokolové upgrady, '
            'DeFi TVL/aktivita na Strike Finance/Minswap/Liqwid), ETF/regulačných správach '
            '(SEC filings, spot ETF rozhodnutia), burzových listingoch/delistingoch, a širšom '
            'krypto naratíve (BTC dominance, risk-on/off sentiment, veľké likvidácie na trhu)'
        ),
        "macro_rules": _CRYPTO_MACRO_RULES,
    },
    "GOLD": {
        "label": "komoditu GOLD (zlato) perpetuál",
        "news_focus": (
            'správach o Fed politike a očakávaniach sadzieb (FOMC, CPI, PPI, NFP, dot-plot '
            'komentáre), sile dolára (DXY), reálnych výnosoch (US10Y mínus infláčné očakávania), '
            'geopolitickom riziku (vojnové konflikty, sankcie), a nákupoch zlata centrálnymi '
            'bankami (najmä PBOC a iné EM centrálne banky)'
        ),
        "macro_rules": _COMMODITY_MACRO_RULES,
    },
}

SYSTEM_PROMPT_TEMPLATE = """Si skúsený intradenný analytik pre {label}.
Dostaneš technickú analýzu (TA) {instrument} - vrátane `recent_candles`, surových posledných
{candle_bars} hodinových sviečok [open,high,low,close] - cross-market kontext, session
alignment{btc_proxy_note} a prípadne social-media sentiment. Máš k dispozícii nástroj web_search -
použi ho na vyhľadanie čerstvých {news_focus}, ktoré by mohli hýbať cenou v najbližších 24
hodinách. Vyhľadávaj len ak to dáva zmysel (max. niekoľko vyhľadávaní).

`recent_candles` použi na vlastné posúdenie cenovej štruktúry - kde je nedávny support/resistance,
či je cena v rangi alebo trenduje, kde bol posledný swing high/low, či prebehol breakout. Opíš to
vlastnými slovami (napr. "cena opakovane odrazila od X", "range medzi X a Y"), NIE pomenovaním
klasických formácií (cup-and-handle, hlava-ramená, diamanty, trojuholníky a pod.) - tie majú v
akademickej literatúre slabú a nekonzistentnú empirickú oporu naprieč trhmi/obdobiami, na rozdiel
od matematicky presne definovaných indikátorov (RSI/MACD/EMA/Bollinger), a ich hranice sú navyše
subjektívne. Radšej konkrétna cenová úroveň/pozorovanie než pomenovaný tvar.

Presný aktuálny dátum a čas dostaneš v user správe - VŽDY ho zahrň do vyhľadávacích dotazov
(napr. "{instrument} news July 22 2026", nie len "{instrument} news"), inak web_search občas vráti
staré výsledky (mesiace/roky staré) namiesto aktuálnych. Pri hodnotení výsledkov skontroluj ich
page_age/dátum - ak je správa staršia než obdobie od posledného cyklu (dostaneš ho v user
správe), ber ju len ako pozadový kontext, nie ako novú informáciu ktorá mení rozhodnutie.

Toto je INKREMENTÁLNE hľadanie, nie hľadanie od nuly: predpoklady z predchádzajúceho cyklu
(ak existujú) už pokrývajú stav sveta do svojho času. Tvojou úlohou je zistiť LEN ČO PRIBUDLO
alebo SA ZMENILO odvtedy (typicky posledné ~4h) - nie znova zbierať celý kontext. Formuluj
dotazy cielene na najnovšie dianie (napr. "[téma] news today", "{instrument} [dátum] [čas]"),
nie všeobecné prehľady, ktoré ťa zavalia starším materiálom.

Kvalita zdrojov: ak sa dá, uprednostni priamy/primárny zdroj pred sekundárnym prevykladom -
oficiálna tlačová správa firmy na jej investor-relations stránke alebo SEC/EDGAR filing namiesto
blogového zhrnutia, oficiálne dáta z bls.gov/federalreserve.gov namiesto komentára tretej strany,
Reuters/Bloomberg/AP namiesto menej známych agregátorov. Bežné finančné weby (Yahoo Finance,
Investing.com, CNBC a pod.) sú v poriadku ak primárny zdroj nie je ľahko dostupný, ale ak je to
priamočiare (napr. dopyt na "[firma] investor relations press release" alebo "site:sec.gov"),
skús najprv originál.

Tvoja úloha je vyhodnotiť, či má zmysel otvoriť LONG, SHORT, alebo neobchodovať (NONE)
na horizont max. 24 hodín, s konkrétnym stop-lossom a take-profitom.

Ako syntetizovať viacero signálov (nepočítaj váhy mechanicky, posúď to ako skúsený analytik):
{macro_rules}

Pravidlá:
- Buď konzervatívny: ak signály nie sú jasné alebo sú protichodné, zvoľ "none" a nízku confidence.
- confidence je 0-100 a má odrážať reálnu neistotu (60 je "mierne naklonený", 90+ je vzácne).
- stop_loss_price a take_profit_price uveď ako absolútnu cenu {instrument} (nie percentá).
  Cieľové % vzdialenosti od aktuálnej ceny dostaneš v user správe - drž sa v ich blízkosti
  (môžeš sa mierne odchýliť podľa ATR/kontextu, ale nie výrazne mimo).
- reasoning: max 3-4 vety, fakticky, bez floskúl; spomeň najdôležitejší faktor(y), ktoré rozhodli.
  Ak dostaneš predpoklady z predchádzajúceho cyklu, výslovne spomeň, či stále platia alebo sa
  niečo zmenilo.
- key_assumptions: 1-2 vety - kľúčové fakty/očakávania, na ktorých toto rozhodnutie stojí
  (napr. konkrétny očakávaný event a jeho dátum, prevládajúci naratív, aktívny katalyzátor).
  Toto dostane budúci cyklus na overenie, či ešte platí - ber to ako odkaz "čo si myslím, že
  je teraz pravda" pre svoje budúce ja.
- watch_price/watch_direction (VOLITEĽNÉ, len ak direction="none"): ak vidíš konkrétnu cenovú
  úroveň, ktorej potvrdenie/prekonanie by čoskoro (rádovo minúty až pár hodín, nie celý ďalší
  {interval_hours}h cyklus) zmenilo rozhodnutie - najmä keď je confidence blízko prahu na
  obchodovanie - vráť watch_price (číslo, presná cena {instrument}) a watch_direction ("above" ak
  čakáš na potvrdenie NAD touto cenou, "below" ak POD ňou). Toto spustí lacný poller sledujúci
  live cenu, ktorý ťa mimoriadne zavolá znova AK sa podmienka splní, namiesto čakania na ďalší
  pravidelný cyklus. Ak takú úroveň nevidíš, alebo je direction="long"/"short" (pozícia sa už
  otvára), vynechaj oba tieto polia úplne.
- Po dokončení (prípadného) vyhľadávania zavolaj nástroj `submit_trade_decision` s finálnym
  rozhodnutím - to je jediný spôsob, ako rozhodnutie odovzdať.
"""


def _system_prompt(asset: dict) -> str:
    text = ASSET_TEXT[asset["name"]]
    btc_proxy_note = ", krypto-makro proxy (BTC)" if asset.get("needs_btc_proxy") else ""
    return SYSTEM_PROMPT_TEMPLATE.format(
        label=text["label"],
        instrument=asset["name"],
        news_focus=text["news_focus"],
        macro_rules=text["macro_rules"].format(instrument=asset["name"]),
        btc_proxy_note=btc_proxy_note,
        candle_bars=market_data.RECENT_CANDLES_BARS,
        interval_hours=config.TRADE_INTERVAL_HOURS,
    )


def _build_user_prompt(asset: dict, ta: dict, cross_market: dict, session: dict,
                        social: list[dict], btc_proxy: dict | None,
                        prev_assumptions: str | None,
                        prev_cycle_time: datetime | None = None) -> str:
    instrument = asset["name"]
    social_block = "\n".join(
        f"- ({p.get('likes')}♥/{p.get('retweets')}rt) {p.get('text')}"
        for p in social[:15]
    ) or "(social sentiment nie je zapnutý/dostupný)"

    now = datetime.now(timezone.utc)
    interval_h = config.TRADE_INTERVAL_HOURS

    if prev_assumptions and prev_cycle_time:
        since_str = prev_cycle_time.strftime('%A, %d. %B %Y, %H:%M UTC')
        prev_block = (
            f'"{prev_assumptions}"\n\n(tieto predpoklady pochádzajú z cyklu o {since_str})\n\n'
            f"Hľadaj VÝLUČNE, čo pribudlo/zmenilo sa OD {since_str} - nie celý kontext od nuly. "
            f"Over, či tieto predpoklady stále platia, alebo sa niečo zmenilo (event už prebehol, "
            f"správa sa nenaplnila, sentiment sa otočil...). V reasoning výslovne napíš, či držia "
            f"alebo čo sa zmenilo."
        )
    elif prev_assumptions:
        prev_block = (
            f'"{prev_assumptions}"\n\nOver si cez web_search, či tieto predpoklady stále platia, '
            f"alebo sa niečo zmenilo. V reasoning výslovne napíš, či držia alebo čo sa zmenilo."
        )
    else:
        prev_block = "(žiadne - toto je prvý cyklus alebo predchádzajúci nemal záznam)"

    btc_block = ""
    if btc_proxy is not None:
        btc_block = (
            f"\n## Krypto-makro proxy (BTC - risk-on/off referencia pre {instrument})\n"
            f"{json.dumps(btc_proxy, indent=2, ensure_ascii=False)}\n"
        )

    return f"""## Aktuálny dátum a čas
{now.strftime('%A, %d. %B %Y, %H:%M')} UTC ({now.isoformat()})
Tento cyklus beží každých {interval_h}h - zaujímajú ťa hlavne udalosti/správy za posledných
~{interval_h} hodín, staršie ber len ako pozadový kontext (nie ako novú informáciu).

## Technická analýza {instrument}
{json.dumps(ta, indent=2, ensure_ascii=False)}

## Cross-market kontext (S&P500, Russell 2000, SOX, VIX, DXY, US10Y/US13W výnosy, ropa, zlato)
{json.dumps(cross_market, indent=2, ensure_ascii=False)}

## Session alignment (Ázia -> Európa -> US futures)
{json.dumps(session, indent=2, ensure_ascii=False)}
{btc_block}
## Social media sentiment
{social_block}

## Kľúčové predpoklady z predchádzajúceho cyklu (~{interval_h}h dozadu)
{prev_block}

## Cielove SL/TP vzdialenosti
Stop-loss cca {asset['sl_pct']}% od aktuálnej ceny, take-profit cca {asset['tp_pct']}%
(pri LONG: stop_loss_price = last_price * (1 - {asset['sl_pct']}/100), take_profit_price =
last_price * (1 + {asset['tp_pct']}/100); pri SHORT opačne). Môžeš sa mierne odchýliť podľa
ATR/kontextu, ale nie výrazne mimo tento rozsah.

Ak je to relevantné, over si cez web_search aktuálne správy k {instrument}/súvisiacim témam a
nadchádzajúce makro eventy (CPI/FOMC/NFP/earnings) za posledných ~{interval_h}h / najbližších 24h -
nezabudni do query zahrnúť aktuálny dátum. Potom vyhodnoť situáciu a vráť rozhodnutie podľa
formátu zo system promptu.
"""


def analyze(asset: dict, ta: dict, cross_market: dict, session: dict, social: list[dict],
            btc_proxy: dict | None = None,
            prev_assumptions: str | None = None,
            prev_cycle_time: datetime | None = None) -> tuple[dict, list[dict]]:
    """Vrati (decision, web_search_log). web_search_log je zoznam
    {"query": str, "sources": [{"title", "url", "page_age"}]} pre kazde
    vyhladavanie, ktore Claude spravil - sluzi na audit (co realne citas,
    aby sa dalo neskor rozhodnut o whitelist/blacklist domen).

    asset: profil z assets.py (name/asset_class/sl_pct/tp_pct/... - urcuje system
    prompt aj cielove SL/TP % v user prompte).
    prev_assumptions: kluc_assumptions z minuleho cyklu TOHTO assetu (ak existuje) -
    Claude ho dostane na explicitne overenie, ci este plati.
    prev_cycle_time: kedy prev_assumptions vznikli - umoznuje formulovat hladanie
    ako presny inkrement ("co pribudlo OD X"), nie vagne "za poslednych ~4h"."""
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY nie je nastavený")

    system_prompt = _system_prompt(asset)

    # cache_control na systemovom prompte aj user sprave: ak Claude narazi na
    # pause_turn (casto sa stava pri viacerych web_search volaniach), musime
    # poslat celu doterajsiu konverzaciu znova - bez cachovania by sa system
    # prompt + user sprava platili nanovo na plnu cenu pri kazdom pokracovani.
    messages = [{"role": "user",
                 "content": [{"type": "text",
                               "text": _build_user_prompt(asset, ta, cross_market, session, social,
                                                           btc_proxy, prev_assumptions, prev_cycle_time),
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
                "max_tokens": 8192,
                "system": [{"type": "text", "text": system_prompt,
                            "cache_control": {"type": "ephemeral"}}],
                "tools": [
                    {"type": "web_search_20260209", "name": "web_search", "max_uses": 5},
                    DECISION_TOOL,
                ],
                "messages": messages,
            },
            timeout=300,
        )
        resp.raise_for_status()
        data = resp.json()
        content_blocks = data.get("content", [])
        web_search_log.extend(_extract_web_search_log(content_blocks))
        usage = data.get("usage", {})
        print(f"[claude_analyst] [{asset['name']}] usage: input={usage.get('input_tokens')} "
              f"cache_write={usage.get('cache_creation_input_tokens')} "
              f"cache_read={usage.get('cache_read_input_tokens')} output={usage.get('output_tokens')} "
              f"stop_reason={data.get('stop_reason')}")

        if data.get("stop_reason") == "pause_turn":
            # NEZNACIME cache_control na tento blok: cyklus ma tvrdy strop 2 volania
            # (range(2) nizsie), takze pokracovanie o par riadkov nizsie je VZDY
            # posledne - ziadne 3. volanie uz nikdy nepride precitat si tento zapis
            # spat. Oznacenie by teda len zaplatilo cache-write prirazku (~25%) na
            # casto velky blok (web_search vysledky) bez akejkolvek sance na navratnost.
            messages = messages + [{"role": "assistant", "content": content_blocks}]
            continue

        decision_block = next(
            (b for b in content_blocks
             if b.get("type") == "tool_use" and b.get("name") == "submit_trade_decision"),
            None,
        )
        if decision_block is None:
            raise RuntimeError(
                f"Claude nezavolal submit_trade_decision (stop_reason={data.get('stop_reason')}, "
                f"content_types={[b.get('type') for b in content_blocks]})"
            )
        decision = decision_block["input"]
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


def _validate_decision(decision: dict) -> None:
    required = {"direction", "confidence", "stop_loss_price", "take_profit_price", "reasoning"}
    missing = required - decision.keys()
    if missing:
        raise ValueError(f"Chýbajúce polia v rozhodnutí: {missing}")
    if decision["direction"] not in ("long", "short", "none"):
        raise ValueError(f"Neplatný smer: {decision['direction']}")
    if not (0 <= decision["confidence"] <= 100):
        raise ValueError(f"Neplatná confidence: {decision['confidence']}")

    # watch_price/watch_direction su volitelne (len pri direction="none") - ak
    # ich model vratil, over aspon zakladny tvar, ale nechyb, ak chybaju uplne
    # (staré/nechcene cykly ich nemusia mat).
    watch_direction = decision.get("watch_direction")
    if watch_direction is not None and watch_direction not in ("above", "below"):
        raise ValueError(f"Neplatny watch_direction: {watch_direction!r}")
    watch_price = decision.get("watch_price")
    if watch_price is not None and not isinstance(watch_price, (int, float)):
        raise ValueError(f"Neplatny watch_price: {watch_price!r}")
