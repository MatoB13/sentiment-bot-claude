"""
Zavola Claude (Anthropic API) s TA kontextom pre dany asset (NAS100/NVDA/ADA/GOLD/WTI/NIGHT/BTC/HYPE/SKHYNIX).
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
import re
import time
from datetime import datetime, timezone

import requests

import config
import market_data

# Prechodne infra chyby (Cloudflare/Anthropic docasne nedostupne) - bezpecne
# opakovat, kedze Messages API call nema ziadne vedlajsie ucinky (nehybe
# peniazmi, neotvara poziciu). 529 je Anthropic-ove vlastne "overloaded_error".
# 429 (rate limit) pridane 2026-08-19 (crash-scenario audit) - pri hromadnom
# zatvoreni viacerych pozicii naraz (viz trade_cycle._DISPATCH_CONCURRENCY_LIMIT)
# sa moze spustit viacero suecasnych Claude volani, co pri narazeni na rate
# limit predtym cyklus rovno vzdalo bez pokusu o retry.
# Odstup 60s (nie povodnych 3s) - realny 529 vydrzal cez cele povodne ~6s okno
# (2026-07-31, XAU cyklus), minuta by mala prekryt bezny kratkodoby vypadok.
_RETRYABLE_STATUS = {429, 502, 503, 504, 520, 521, 522, 523, 524, 529}
_MAX_API_RETRIES = 2
_API_RETRY_DELAY_SECONDS = 60
# Zvysene z povodnych 300s (2026-08-20, po ADA timeout produkcnom naleze -
# effort=xhigh/max s extended thinking + viacerymi web_search volaniami obcas
# genuinne potrebuje viac nez 5 min na odpoved, nie len prechodnu sietovu chybu).
_REQUEST_TIMEOUT_SECONDS = 480

# Zdielane medzi DECISION_TOOL a POSITION_HEALTH_TOOL - Claudom priebezne
# udrziavany doplnok k rucne udrzovanemu macro_calendar.py (FOMC/CPI/NFP,
# overene z oficialnych zdrojov). Na rozdiel od tych (vsetky aktivne assety)
# sa udalost zaznacena TU spusti LEN pre asset, ktoreho cyklus ju zaznacil
# (viz trade_cycle._save_flagged_macro_event + watch_monitor._check_macro_events) -
# je typicky specificka pre tento konkretny nastroj (OPEC+ pre WTI, earnings
# pre NVDA, bezpecnostny deadline pre NIGHT a pod.), nie sirsi makro event.
_UPCOMING_MACRO_EVENT_PROPERTY = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Kratky nazov udalosti (napr. 'FOMC', 'OPEC+ stretnutie', 'NVDA Q3 earnings', 'White-hat bounty deadline').",
        },
        "datetime_utc": {
            "type": "string",
            "description": (
                "Presny datum a cas v UTC, ISO 8601 (napr. '2026-09-04T12:30:00Z'). Ak nepoznas "
                "presny cas, pouzi '00:00:00Z' daneho dna."
            ),
        },
        "scope": {
            "type": "string", "enum": ["this_asset", "all_assets"],
            "description": (
                "'this_asset' (default, pouzi ked si nie isty) = udalost je SPECIFICKA pre TENTO "
                "nastroj (OPEC+ pre ropu, earnings, bezpecnostny/regulacny deadline a pod.) - "
                "mimoriadny cyklus sa spusti LEN preň. 'all_assets' = SIROKY makro event relevantny "
                "pre VSETKY sledovane tickery (napr. FOMC rozhodnutie, CPI, NFP, alebo iny "
                "trh-siroky event) - mimoriadny cyklus sa spusti pre kazdy aktivny asset naraz, "
                "takze pouzi 'all_assets' len ked si tym skutocne isty."
            ),
        },
    },
    "required": ["name", "datetime_utc"],
    "description": (
        "VOLITELNE - vypln LEN ak si TENTO cyklus cez web_search zistil KONKRETNY presny "
        "datum/cas VYZNAMNEJ nadchadzajucej udalosti (nie bezny sum - MUSI byt skutocne "
        "vyznamna, pretoze spusti mimoriadny analyticky cyklus HNED pri jej case, teda realny "
        "naklad). Toto je jediny sposob, akym sa kalendar znamych udalosti (FOMC/CPI/NFP a inych "
        "vyznamnych terminov) priebezne udrziava - NIKTO ho rucne nedopĺňa, takze ak zistis presny "
        "datum dolezitej buducej udalosti, zaznac ju. Ak uz vies, ze si tuto konkretnu udalost "
        "(rovnaky nazov aj datum) v minulom cykle uz zaznacil, znova ju VYNECHAJ (zbytocna "
        "duplicita)."
    ),
}

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
                "description": (
                    "Max 3-4 vety, fakticky, bez floskul. Ak nastavis watch_price/watch_direction, "
                    "VZDY tu explicitne uved, co presne sledovana podmienka znamena a co by jej "
                    "potvrdenie spustilo (napr. konkretny smer obchodu) - nie len ze uroven/rozsah "
                    "'zostava v platnosti'."
                ),
            },
            "key_assumptions": {
                "type": "string",
                "description": "1-2 vety - kluc. fakty/ocakavania, na ktorych rozhodnutie stoji.",
            },
            "watch_price": {
                "type": "number",
                "description": (
                    "Volitelne - vypln v JEDNOM z TROCH nezavislych pripadov: "
                    "(1) direction=none A skutocny blokujuci dovod je konkretna CENOVA "
                    "uroven (retest/breakout), ktoru by cenovy pohyb sam vedel potvrdit. "
                    "NEVYPLNAJ v tomto pripade, ak je blokujuci dovod CASOVA UDALOST "
                    "(FOMC/CPI/NFP/PMI/earnings) - ziadny cenovy pohyb pred udalostou "
                    "neistotu nevyriesi, takze by to sposobilo zbytocne opakovane "
                    "mimoriadne cykly pri beznom trhovom sume. "
                    "(2) direction=long/short A vyplnas aj confidence_threshold_note "
                    "nizsie - sem daj presne tu istu cenu, ktoru si tam popisal. "
                    "(3) toto je vyhodnotenie PRAVE zatvorenej pozicie na SL/likvidaciu (viz sekcia "
                    "'Prave zatvorena pozicia' nizsie) A ocakavas, ze sa trh RYCHLO POHNE dalej "
                    "smerom, ktory by opodstatnoval skory re-entry - sem daj cenu, KTOREJ REALNE "
                    "PREKROCENIE by tento predpoklad potvrdilo (nie proste aktualnu cenu). V tomto "
                    "pripade je to JEDINY sposob, ako moze TENTO konkretny cyklus viest k novej pozicii "
                    "(tvoje direction/confidence z neho sa inak nikdy nevykona) - preto tu davaj naozaj "
                    "konkretnu, zmysluplnu uroven, nie mechanicke vyplnanie pri kazdom SL. Ak si nie si "
                    "isty alebo je situacia skor 'pockaj na dalsi bezny cyklus', toto pole vynechaj. "
                    "Vynechaj cely field, ak nie je relevantny ziaden z troch pripadov."
                ),
            },
            "watch_direction": {
                "type": "string", "enum": ["above", "below"],
                "description": (
                    "Volitelne, vzdy spolu s watch_price (rovnaky pripad (1)/(2)/(3) - "
                    "viz jeho popis)."
                ),
            },
            "watch_rationale": {
                "type": "string",
                "description": (
                    "POVINNE vzdy, ked vyplnas watch_price/watch_direction (alebo _2 variant) - "
                    "1-2 vety, PRECO teraz cakas namiesto vstupu (napr. 'cena je ~3 ATR nad "
                    "Bollingerom po vertikalnom pohybe, chase by mal zly risk/reward, cakam na "
                    "retest/potvrdenie'). Ked sa tato uroven neskor spusti, DOSTANES TENTO TEXT "
                    "SPAT v buducom cykle ako pripomienku vlastneho predchadzajuceho rozhodnutia - "
                    "ak vtedy zvolis iny smer/confidence nez teraz, budes musiet vyslovne "
                    "zdovodnit, co konkretne sa oproti tomuto dovodu cakania zmenilo. Pis to preto "
                    "tak, aby to bolo pouzitelne aj o par minut/hodin neskor, nie len formalitu."
                ),
            },
            "watch_price_2": {
                "type": "number",
                "description": (
                    "VOLITELNE, len pri direction=none (NIE pri pripade (2) confidence_threshold_note "
                    "nizsie - tam je vzdy len jedna relevantna cena) - DRUHA (opacna) sledovana "
                    "uroven pre genuinne obojstranne neisty/range-bound setup, kde by ROVNAKO "
                    "relevantne potvrdil AJ breakout hore AJ breakdown dole (napr. 'nad X by "
                    "potvrdilo long, pod Y by potvrdilo short'). NEPOUZIVAJ na dve ceny v tom "
                    "istom smere - na to staci jeden watch_price. Ak sledujes len jednu "
                    "uroven/smer, toto pole vynechaj."
                ),
            },
            "watch_direction_2": {
                "type": "string", "enum": ["above", "below"],
                "description": "Volitelne, vzdy spolu s watch_price_2 - musi byt OPACny smer nez watch_direction.",
            },
            "confidence_threshold_note": {
                "type": "string",
                "description": (
                    "VYPLŇ VZDY, ked direction=long alebo short A tvoja confidence vyjde v pasme "
                    "tesne pod prahom na otvorenie pozicie (presne cislicne pasmo pre tento cyklus "
                    "dostanes v user sprave). Napis, PRI AKEJ CENE by tvoja confidence z CISTO "
                    "TECHNICKEHO hladiska (potvrdeny breakout, uspesny retest, prekonanie "
                    "konkretnej urovne - NIKDY plynutim casu) prekrocila prah - a tu istu cenu daj "
                    "aj do watch_price/watch_direction, aby ju lacny poller sledoval a pri splneni "
                    "spustil mimoriadny cyklus (kde situaciu znova kompletne vyhodnotis od zaciatku, "
                    "nemechanicky sa nevykona tento povodny navrh). Je UPLNE V PORIADKU napisat, ze "
                    "v danej situacii takú cenu nevies odhadnut (napr. blokujuci dovod nie je "
                    "cenova uroven, ale cakanie na konkretnu spravu/event) - vtedy watch_price/"
                    "watch_direction nechaj prazdne. Mimo tohto pasma (confidence bezpecne nad "
                    "alebo zjavne pod prahom) toto pole uplne vynechaj."
                ),
            },
            "data_issue": {
                "type": "string",
                "description": (
                    "VOLITELNE - vypln LEN ak vstupne data pre tento cyklus vyzeraju podozrivo/"
                    "nekonzistentne (napr. zastarana/nulova cena, chybajuci alebo evidentne "
                    "chybny TA udaj, protichodny cross-market snapshot). Strucny popis problemu, "
                    "nezavisle od obchodneho rozhodnutia - zobrazi sa v historii signalov, aby sa "
                    "problem nestratil v strohom reasoning. Ak s datami nie je nic zvlastne, "
                    "toto pole VYNECHAJ."
                ),
            },
            "daily_reflection": {
                "type": "string",
                "description": (
                    "VYPLN LEN ak user sprava obsahuje sekciu 'Nove statistiky za vcerajsok' "
                    "(deje sa raz denne, pri prvom cykle po polnoci). IZOLOVANA poznamka LEN k "
                    "VCERAJSKU (nie priebezne zhrnutie - to je samostatne pole "
                    "summary_reflection nizsie). Strucne (2-4 vety) zhodnot dve veci: (1) ci "
                    "bola tvoja confidence kalibracia vcera primerana - najma ci signaly "
                    "zamietnute LEN kvoli confidence boli vacsinou spravne zamietnute (boli by "
                    "stratove) alebo naopak prilis prisne zamietnute (boli by ziskove); (2) ci "
                    "tvoje 'none' rozhodnutia boli opodstatnene, alebo ci si bol niekedy zbytocne "
                    "opatrny a v spatnom pohlade malo byt LONG/SHORT. Ak nemas take udaje k "
                    "dispozicii v tomto cykle, toto pole VYNECHAJ."
                ),
            },
            "summary_reflection": {
                "type": "string",
                "description": (
                    "VYPLN LEN ak user sprava obsahuje sekciu 'Nove statistiky za vcerajsok' "
                    "(rovnaky trigger ako daily_reflection, raz denne). Na rozdiel od "
                    "daily_reflection (izolovana poznamka LEN k vcerajsku) je toto "
                    "AKTUALIZOVANE PRIEBEZNE ZHRNUTIE, ktore sa realne prenasa do VSETKYCH "
                    "tvojich buducich cyklov (nahradza predchadzajucu verziu, nie je to denny "
                    "dennik). Dostanes v sekcii 'Priebezne zhrnutie doterajsich skusenosti' "
                    "existujucu verziu (ak uz existuje) - tvoja uloha je NAPISAT JEJ AKTUALIZOVANU "
                    "VERZIU zapracovanim vcerajsich novych udajov: potvrd vzory, ktore sa opakuju "
                    "cez viac dni (tie su dolezitejsie nez jednorazovy vysledok jedneho dna), "
                    "over/uprav zavery, ktore nove data vyvracaju, a zahod uz nepodstatne detaily. "
                    "DOLEZITE: drz to STRUCNE (cielovo 5-8 viet, max ~150 slov) - je to trvala "
                    "prevadzkova poznamka sebe samemu, nie narastajuci log. Ak zhrnutie este "
                    "neexistuje, napis prve len z vcerajsich udajov. Ak nemas udaje k dispozicii "
                    "v tomto cykle, toto pole VYNECHAJ."
                ),
            },
            "closed_trade_reflection": {
                "type": "string",
                "description": (
                    "VYPLN LEN ak user sprava obsahuje sekciu 'Práve zatvorená pozícia' "
                    "(mimoriadny cyklus spustený HNEĎ po TP/timeout/manuálnom zatvorení, alebo po "
                    "SL/likvidácii). 2-3 vety: bolo zatvorenie správne timeované, alebo mala pozícia "
                    "pokračovať dlhšie (napr. pri TP: bol cieľ nastavený príliš konzervatívne), alebo "
                    "malo prísť skôr? Pri SL/likvidácii: bol vstup/SL nastavený primerane, alebo niečo "
                    "(prehriaty RSI, chase breakoutu a pod.) vopred naznačovalo zvýšené riziko rýchleho "
                    "zvratu? Toto je NEZÁVISLÉ od tvojho direction/confidence rozhodnutia nižšie (to je "
                    "o tom, či TERAZ otvoriť novú pozíciu) - tu ide o SPÄTNÉ hodnotenie tej "
                    "PREDCHÁDZAJÚCEJ. Ak sekcia chýba, toto pole VYNECHAJ."
                ),
            },
            "sl_tp_calibration_verdict": {
                "type": "string",
                "description": (
                    "VYPLN LEN ak user správa obsahuje sekciu 'Vyhodnotenie SL/TP tejto pozície' "
                    "(rovnaký trigger ako closed_trade_reflection). Zaujmi VÝSLOVNÉ stanovisko k "
                    "presne JEDNEJ z troch možností: (1) zvolené SL/TP tejto pozície bolo správne - "
                    "zdôvodni PREČO technicky (volatilita/ATR režim, štruktúra S/R, typ vstupu), nie "
                    "len tým, že vyšiel zisk/strata; (2) mal sa radšej použiť niektorý z uvedených "
                    "kalibračných kandidátov - uveď PRESNE ktorý (#rank) a prečo; alebo (3) ani jeden "
                    "z kandidátov by nebol správny a zvolil by si ÚPLNE INÚ hodnotu - uveď konkrétne "
                    "% aj TECHNICKÉ zdôvodnenie (napr. na základe ATR/volatility režimu v čase vstupu, "
                    "vzdialenosti k najbližšej S/R úrovni, typu vstupu), NIE LEN odkaz na to, že v "
                    "backteste vyšla lepšie - backtest čísla sú vstup do úvahy, nie samotné "
                    "zdôvodnenie. Zohľadni aj históriu predošlých obchodov tohto tickera. 3-5 viet. "
                    "Ak sekcia chýba, toto pole VYNECHAJ."
                ),
            },
            "upcoming_macro_event": _UPCOMING_MACRO_EVENT_PROPERTY,
        },
        "required": ["direction", "confidence", "stop_loss_price", "take_profit_price",
                     "reasoning", "key_assumptions"],
    },
}

POSITION_HEALTH_TOOL = {
    "name": "submit_position_health_check",
    "description": (
        "Odovzdaj priebežné hodnotenie UŽ OTVORENEJ pozície (nie rozhodnutie o novom obchode - "
        "SL/TP na burze zostávajú nezmenené, toto je len opinion pre používateľa). Zavolaj tento "
        "nástroj VŽDY ako posledný krok, po dokončení prípadného web_search overenia predpokladov."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "recommendation": {
                "type": "string", "enum": ["hold", "consider_closing"],
                "description": (
                    "hold = pôvodné predpoklady držia, žiadny naliehavý dôvod na zásah. "
                    "consider_closing = predpoklady sa výrazne oslabili alebo sa objavilo nové "
                    "podstatné riziko - používateľ by mal zvážiť manuálne zatvorenie (rozhoduje "
                    "človek cez kill-switch, TY pozíciu nezatváraš)."
                ),
            },
            "expected_direction": {
                "type": "string", "enum": ["favorable", "unfavorable", "uncertain"],
                "description": (
                    "Očakávaš, že sa cena v najbližšom čase bude pohybovať V PROSPECH tejto "
                    "pozície (favorable), PROTI nej (unfavorable), alebo je to nejasné (uncertain)?"
                ),
            },
            "reasoning": {
                "type": "string",
                "description": (
                    "Max 3-4 vety: či pôvodné kľúčové predpoklady stále platia (alebo čo sa "
                    "zmenilo), a prečo očakávaš daný pohyb ceny. Fakticky, bez floskúl."
                ),
            },
            "key_assumptions": {
                "type": "string",
                "description": (
                    "1-2 vety - AKTUALIZOVANÉ kľúčové predpoklady pre túto pozíciu (nahrádzajú "
                    "predchádzajúce, prenesú sa do ďalšieho cyklu rovnako ako pri bežnom "
                    "obchodnom rozhodnutí)."
                ),
            },
            # Pridane 2026-08-17 (na ziadost pouzivatela) - EXPERIMENTALNE, LEN
            # LOGOVANIE, ZATIAL BEZ AKEJKOLVEK AKCIE. Ciel: nazbierat data na
            # kalibraciu ("ked je toto skore 80+, naozaj sa v spatnom pohlade
            # ukazalo zatvorenie ako spravne?"), skor nez sa tomuto skore
            # niekedy v buducnosti zveri skutocna moznost poziciu zatvorit.
            "close_confidence": {
                "type": "integer",
                "description": (
                    "LEN ak recommendation=consider_closing. Ako VELMI si istý (0-100), že "
                    "ZATVORENIE PRÁVE TERAZ je správne rozhodnutie - NIE to isté ako všeobecná "
                    "obchodná istota, ale konkrétne: keby si mal exekučnú právomoc, urobil by si "
                    "to hneď? 0-40 = skôr len opatrnosť/varovanie, sleduj ďalej. 40-70 = reálne "
                    "znepokojujúce, ale ešte nie jednoznačné. 70-100 = pôvodná téza je podľa teba "
                    "prakticky vyvrátená, čakanie na mechanický SL/TP už nedáva zmysel. Buď "
                    "úprimný a kalibrovaný - toto číslo sa teraz LEN zaznamenáva na spätné "
                    "vyhodnotenie, nespúšťa žiadnu akciu, takže nemá zmysel ho umelo tlačiť "
                    "hore ani dole."
                ),
            },
            "upcoming_macro_event": _UPCOMING_MACRO_EVENT_PROPERTY,
            # Pridane 2026-08-17 - retrospektiva sa doteraz generovala LEN
            # v beznom obchodnom cykle (submit_trade_decision), ktory sa vsak
            # vobec nevola, kym je pozicia otvorena a mechanicka kontrola
            # neeskaluje (viz trade_cycle._run_position_health_check - health
            # check je mechanicky-default, Claude sa vola len pri eskalacii).
            # Pri dlho drzanej pozicii tak vcerajsok mohol ostat nespracovany
            # aj viac dni. Rovnaky trigger/vyznam ako v DECISION_TOOL nizsie -
            # ak je pozicia otvorena cez polnoc, "pending retrospektiva" sama
            # osebe eskaluje na plny Claude cyklus (viz trade_cycle.py), aby sa
            # toto pole malo kedy vyplnit.
            "daily_reflection": {
                "type": "string",
                "description": (
                    "VYPLN LEN ak user sprava obsahuje sekciu 'Nove statistiky za vcerajsok' "
                    "(deje sa raz denne, pri prvom cykle po polnoci - aj ked je pozicia otvorena). "
                    "IZOLOVANA poznamka LEN k VCERAJSKU (nie priebezne zhrnutie - to je samostatne "
                    "pole summary_reflection nizsie). Strucne (2-4 vety) zhodnot dve veci: (1) ci "
                    "bola tvoja confidence kalibracia vcera primerana - najma ci signaly "
                    "zamietnute LEN kvoli confidence boli vacsinou spravne zamietnute (boli by "
                    "stratove) alebo naopak prilis prisne zamietnute (boli by ziskove); (2) ci "
                    "tvoje 'none' rozhodnutia boli opodstatnene, alebo ci si bol niekedy zbytocne "
                    "opatrny a v spatnom pohlade malo byt LONG/SHORT. Ak nemas take udaje k "
                    "dispozicii v tomto cykle, toto pole VYNECHAJ."
                ),
            },
            "summary_reflection": {
                "type": "string",
                "description": (
                    "VYPLN LEN ak user sprava obsahuje sekciu 'Nove statistiky za vcerajsok' "
                    "(rovnaky trigger ako daily_reflection, raz denne). Na rozdiel od "
                    "daily_reflection (izolovana poznamka LEN k vcerajsku) je toto "
                    "AKTUALIZOVANE PRIEBEZNE ZHRNUTIE, ktore sa realne prenasa do VSETKYCH "
                    "tvojich buducich cyklov (nahradza predchadzajucu verziu, nie je to denny "
                    "dennik). Dostanes v sekcii 'Priebezne zhrnutie doterajsich skusenosti' "
                    "existujucu verziu (ak uz existuje) - tvoja uloha je NAPISAT JEJ AKTUALIZOVANU "
                    "VERZIU zapracovanim vcerajsich novych udajov: potvrd vzory, ktore sa opakuju "
                    "cez viac dni (tie su dolezitejsie nez jednorazovy vysledok jedneho dna), "
                    "over/uprav zavery, ktore nove data vyvracaju, a zahod uz nepodstatne detaily. "
                    "DOLEZITE: drz to STRUCNE (cielovo 5-8 viet, max ~150 slov) - je to trvala "
                    "prevadzkova poznamka sebe samemu, nie narastajuci log. Ak zhrnutie este "
                    "neexistuje, napis prve len z vcerajsich udajov. Ak nemas udaje k dispozicii "
                    "v tomto cykle, toto pole VYNECHAJ."
                ),
            },
        },
        "required": ["recommendation", "expected_direction", "reasoning", "key_assumptions"],
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
  "none") - volatilita okolo takých eventov je nepredvídateľná aj pri jasnom technickom obraze.
- **Nepredvídateľné politické výroky (Trump/Truth Social)**: Príspevky amerického prezidenta na Truth
  Social vedia bez varovania pohnúť trhom (clá, obchodná politika, komentáre ku konkrétnym firmám) -
  nie sú viazané na kalendár ako bežné makro eventy. Cez web_search over, či sa v poslednom čase
  objavil takýto výrok s reálnym dopadom na {instrument}/tech sektor, alebo či je zvýšené riziko
  blízkeho oznámenia (napr. blížiaci sa termín rozhodnutia o clách) - v takom prípade zváž nižšiu
  confidence, rovnako ako pri inom Event Risk Gate scenári."""

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

_BTC_MACRO_RULES = """- **Spot ETF toky**: Čisté denné toky do/z spot BTC ETF (BlackRock IBIT, Fidelity FBTC a i.) sú od
  2024 jeden z najsilnejších krátkodobých driverov - veľké čisté odlevy signalizujú inštitucionálny
  predaj (medvedie), veľké prílevy nákupný tlak (býčie). Over cez web_search najnovšie čísla, ak sú
  dostupné.
- **Makro/Fed citlivosť (rastúca)**: BTC sa čoraz viac obchoduje ako makro risk-asset korelovaný s
  Nasdaq/reálnymi výnosmi, nie len ako "digitálne zlato" - rýchlo rastúce výnosy/dolár sú
  protivietor, klesajúce sú vietor v chrbát. Podobný mechanizmus ako pri equity trhoch, len s
  vyššou volatilitou a rýchlejšou reakciou.
- **BTC dominance / risk-on-off v rámci krypta**: Keď kapitál uteká z altcoinov do BTC ("flight to
  bitcoin dominance"), je to znak risk-off nálady v rámci krypta - BTC vtedy môže relatívne
  outperformovať aj pri celkovo slabom trhu. Opačne, rastúca "altcoin season" (klesajúca BTC
  dominance) je risk-on signál pre širší trh.
- **Inštitucionálna adopcia**: Správy o veľkých korporátnych nákupoch do treasury
  (MicroStrategy-style), penzijných/suverénnych fondoch vstupujúcich do BTC, sú strednodobý býčí
  naratív.
- **Regulácia**: SEC/CFTC rozhodnutia, ETF schválenia/zamietnutia, a postoj administratívy k
  regulácii (vrátane prípadnej "strategickej bitcoin rezervy" a iných vládnych krokov) sú kľúčové
  eventy.
- **Halving cyklus/miner ekonomika**: Pomaly sa meniaci pozadový faktor (posledný halving 2024) -
  relevantné skôr pre dlhodobý naratív než pre jednotlivý cyklus, spomeň len ak je aktuálne v
  správach.
- **Market Reaction Score**: rovnako dôležité ako inde - porovnaj obsah správy s reálnou cenovou
  reakciou BTC.
- **Event Risk Gate**: FOMC/CPI/PPI/NFP (kvôli rastúcej makro citlivosti) a významné regulačné/ETF
  udalosti sú kľúčové eventy pre BTC - pred takým eventom buď výrazne konzervatívnejší (nízka
  confidence alebo "none").
- **Nepredvídateľné politické výroky (Trump/Truth Social)**: Vyjadrenia k crypto politike (napr.
  strategická rezerva, regulačné kroky) vedia bez varovania pohnúť BTC aj celým krypto trhom - over
  cez web_search nedávne výroky, rovnako ako pri inom Event Risk Gate scenári."""

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
  očakávaniami) - pred takým eventom buď výrazne konzervatívnejší (nízka confidence alebo "none").
- **Po FOMC rozhodnutí (post-event, nie pred ním) - MOMENTUM, nie fade**: Interný backtest (2024-2026,
  overené na XAU aj EURUSD/GBPUSD/USDJPY, s kontrolou voči náhodným hodinám mimo eventov) ukázal
  konzistentný momentum efekt - prvá hodinová reakcia po FOMC statemente má tendenciu POKRAČOVAŤ (nie
  sa vrátiť) na horizonte 4-24h, win rate cca 58-74% naprieč oboma assetmi. Ak {instrument} po FOMC
  rozhodnutí jasne vyrazí jedným smerom a fundamentálny kontext (jastrabí/holubičí tón, dot-plot) tomu
  zodpovedá, zváž vyššiu confidence v smere prvého pohybu namiesto automatického vyčkávania na reverz.
  POZOR: toto NEPLATÍ pre CPI/NFP - tam bol post-event signál na zlate slabý a nekonzistentný (malá
  vzorka, win rate len okolo 50%), takže sa naň nespoliehaj, drž sa len konzervatívneho Event Risk Gate
  pravidla vyššie.
- **Nepredvídateľné politické výroky (Trump/Truth Social)**: Príspevky amerického prezidenta vedia
  okamžite zdvihnúť geopolitické/safe-haven riziko (sankcie, vojenské hrozby, obchodné vojny) bez
  akéhokoľvek kalendárneho varovania. Cez web_search over nedávne výroky s dopadom na
  geopolitiku/dolár, rovnako ako pri inom Event Risk Gate scenári."""

_ENERGY_MACRO_RULES = """- **Ponuka (OPEC+/produkcia)**: Rozhodnutia OPEC+ o ťažobných kvótach (zvýšenie/zníženie), compliance
  členov, a US produkcia (Baker Hughes rig count, shale output) sú hlavný strednodobý driver ponuky.
- **Zásoby (EIA/API)**: Týždenné US zásoby ropy/benzínu (EIA report v stredu, API v utorok predbežne)
  sú najčastejší krátkodobý katalyzátor - neočakávaný pokles zásob je býčí, nárast je medvedí.
- **Geopolitické riziko dodávok**: Eskalácia na Blízkom východe (najmä hrozby pre Hormuzský prieliv,
  cez ktorý prechádza časť svetovej ropy) je priamo býčí pre {instrument} nezávisle od ostatných
  faktorov - NA ROZDIEL od zlata, kde geopolitické riziko pôsobí cez safe-haven dopyt, tu ide o
  priamu hrozbu ponuky.
- **Dopyt (globálny rast/Čína)**: Slabé čínske/globálne PMI/rastové dáta signalizujú nižší dopyt
  (medvedie), silné dáta býčí signál.
- **Dolár (DXY)**: Ropa je cenená v USD - silnejší dolár je mierny protivietor, slabší dolár mierny
  vietor v chrbát, ale vzťah je slabší než pri zlate (ponuka/dopyt sú tu silnejšie faktory).
- **DÔLEŽITÉ - toto NIE JE safe-haven asset ako zlato**: Široký risk-off (padajúce akcie, rastúci VIX)
  často ZNIŽUJE dopyt po rope (obavy z recesie/slabšieho rastu) - teda risk-off je často MEDVEDÍ pre
  {instrument}, presný opak reakcie zlata na to isté prostredie. Vždy over, či risk-off signalizuje
  demand-destruction naratív (medvedie pre ropu) alebo čisto geopolitickú eskaláciu s hrozbou pre
  dodávky (býčie pre ropu) - tieto dva mechanizmy dávajú OPAČNÉ predikcie pre rovnaký "risk-off" nadpis.
- **Market Reaction Score**: rovnako dôležité ako inde - porovnaj obsah správy s reálnou cenovou
  reakciou {instrument}.
- **Event Risk Gate**: EIA zásoby (týždenne), OPEC+ stretnutia, a významné geopolitické udalosti na
  Blízkom východe sú kľúčové eventy pre {instrument} - pred/počas takého eventu buď výrazne
  konzervatívnejší (nízka confidence alebo "none").
- **Nepredvídateľné politické výroky (Trump/Truth Social)**: Príspevky amerického prezidenta k
  Iránu/sankciám/Blízkemu východu vedia bez varovania prudko pohnúť cenou ropy oboma smermi (napr.
  hrozba/odklad úderu, oznámenie/zrušenie sankcií) - nie sú viazané na kalendár ako EIA/OPEC+ termíny.
  Cez web_search over nedávne výroky s priamym dopadom na dodávky/geopolitiku Blízkeho východu,
  rovnako ako pri inom Event Risk Gate scenári."""

_HYPE_MACRO_RULES = """- **Buyback/Assistance Fund mechanizmus**: Hyperliquid protokol používa časť obchodných poplatkov
  na priebežný spätný odkup {instrument} cez tzv. Assistance Fund - toto je štrukturálny býčí
  mechanizmus priamo naviazaný na obchodný objem protokolu (nie len sentiment). Over cez web_search
  najnovšie čísla objemu odkupov, ak sú dostupné.
- **TVL/obchodný objem na Hyperliquid DEX**: Rastúci objem/TVL na samotnej burze = vyššie poplatkové
  príjmy = silnejší buyback tlak (fundamentálny driver, nie len naratív). Klesajúci objem je opačný
  signál.
- **HIP governance návrhy**: Hyperliquid Improvement Proposals (nové listingy, zmeny poplatkovej
  štruktúry, protokolové upgrady) sa hlasujú cez governance - významný schválený/zamietnutý HIP môže
  hýbať cenou.
- **Harmonogram token unlockov**: Pravidelné odomykanie tokenov tímu/investorov vytvára periodický
  predajný tlak - over cez web_search najbližšie plánované dátumy unlockov.
- **HyperEVM ekosystém**: Rast/pokles aktivity aplikácií nasadených na Hyperliquid vlastnej EVM
  vrstve je stredno dobý naratív o zdraví celého ekosystému, nie len samotnej burzy.
- **Konkurenčná dynamika medzi perp-DEX burzami**: Presuny trhového podielu voči iným perpetuál-DEX
  protokolom (dYdX, GMX, Aster, Backpack, Jupiter Perps a i.) sú relevantný kontext - Hyperliquid
  strácajúci dominanciu je medvedí signál nezávisle od širšieho krypto trhu.
- **Riziko exploitu/decentralizácie**: Hyperliquid mal v minulosti kontroverzie okolo koncentrácie
  validátorov a správania insurance vaultu pri veľkých pozíciách (napr. JELLY incident) - akýkoľvek
  nový hack/exploit alebo spor o decentralizáciu je vážne medvedie riziko pre dôveru v protokol.
- **Nízka korelácia s BTC/širším trhom**: Na rozdiel od bežných altcoinov sa {instrument} correlačne
  správa relatívne nezávisle od BTC (empiricky overené) - protokol-špecifické správy majú zvyčajne
  väčšiu váhu než všeobecný krypto sentiment, ale veľké trhové likvidačné kaskády/risk-off eventy
  ho stále vedia zasiahnuť cez celkovú obchodnú aktivitu na Hyperliquid.
- **Market Reaction Score**: rovnako dôležité ako inde - porovnaj obsah správy s reálnou cenovou
  reakciou {instrument}.
- **Event Risk Gate**: významné HIP hlasovania, blížiace sa token unlocky, protokolové upgrady, a
  akýkoľvek hack/exploit su kľúčové eventy - pred/počas takého eventu buď výrazne konzervatívnejší
  (nízka confidence alebo "none")."""

_SKHYNIX_MACRO_RULES = """- **HBM/AI-datacenter capex cyklus - najsilnejší driver**: {instrument} je hlavný dodávateľ HBM3E/
  HBM4 pamätí pre Nvidia AI GPU - dopyt je priamo naviazaný na Nvidia GPU objednávky/capex plány
  veľkých cloud firiem. Nvidia earnings/guidance (aj keď sa netýkajú priamo {instrument}) sú
  SILNÝ leading indicator - sleduj ich rovnako pozorne ako vlastné správy o {instrument}.
- **DRAM/NAND komoditný cenový cyklus**: Pamäťové čipy majú historicky výrazný boom-bust cyklus
  (spotové/kontraktové ceny DRAM/NAND) nezávislý od AI-špecifického HBM dopytu - over aktuálny
  trend cien pamätí, ak je dostupný.
- **Konkurencia v HBM pretekoch**: Samsung a Micron sú priami konkurenti v dodávkach pokročilej
  pamäte - správy o ich výťažnosti/kvalifikácii u Nvidie priamo ovplyvňujú trhový podiel a teda aj
  {instrument}.
- **US-Čína exportné obmedzenia na polovodiče**: Nové/sprísnené obmedzenia môžu byť protivietor
  (strata čínskeho trhu) AJ vietor v chrbát (presmerovanie dopytu mimo Číny, menšia konkurencia od
  čínskych výrobcov) - over konkrétny dopad danej správy, nie len automaticky negatívnu reakciu.
  Vyjadrenia americkej administratívy k obchodnej politike/clám na čipy vedia bez varovania pohnúť
  sentimentom - over cez web_search nedávne výroky s dopadom na polovodičový sektor.
- **Kórejský won (KRW) a Korea Exchange (KRX) seansa**: {instrument} sa obchoduje v KRW na KRX
  (seansa cca 00:00-06:30 UTC, MIMO amerického NYSE okna) - správy z amerického nočného/večerného
  US session (napr. Nvidia earnings zverejnené po US close) sa do ceny {instrument} často premietnu
  až pri JEHO ďalšom otvorení KRX seansy, nie okamžite. Zohľadni tento časový posun pri hodnotení,
  ako "čerstvá" je cenová reakcia na danú správu.
- **Vlastné štvrťročné výsledky/guidance**: Kapex plány a HBM produkčná kapacita oznámené pri
  vlastných earnings sú priamy signál.
- **Market Reaction Score**: rovnako dôležité ako inde - porovnaj obsah správy s reálnou cenovou
  reakciou {instrument}.
- **Event Risk Gate**: Nvidia earnings, vlastné earnings {instrument}, oznámenia US exportných
  obmedzení/ciel na polovodiče, a väčšie zmeny kontraktových cien HBM/DRAM sú kľúčové eventy - pred/
  počas takého eventu buď výrazne konzervatívnejší (nízka confidence alebo "none")."""

_AAOI_MACRO_RULES = """- **AI-datacenter capex cyklus - najsilnejší driver**: {instrument} vyrába optické transceivery
  (800G/1.6T generácie) pre AI datacentrá - dopyt je priamo naviazaný na capex plány hyperscalerov
  (Microsoft, Meta, Google, Amazon, Oracle) a nepriamo na Nvidia GPU objednávky (viac GPU klastrov =
  viac potreby na prepojenie sietí). Hyperscaler capex guidance/earnings sú SILNÝ leading indicator.
- **Malá trhová kapitalizácia = extrémna citlivosť na jednotlivé správy**: Na rozdiel od NVDA/SKHYNIX
  je {instrument} small-cap titul - výhra/prehra jednotlivého veľkého kontraktu, alebo vlastné
  quarterly earnings/guidance, historicky spôsobili jednodňové pohyby ceny rádovo 20-30%+. Ber to do
  úvahy pri confidence aj SL/TP úsudku - bežná volatilita tohto tickera je štrukturálne vyššia než
  pri väčších polovodičových menách.
- **Konkurencia**: Coherent, Lumentum, Fabrinet, a čínski výrobcovia (Innolight, Eoptolink) súperia o
  rovnaké hyperscaler kontrakty - správy o ich výťažnosti/kvalifikácii/cenovej vojne priamo ovplyvňujú
  trhový podiel {instrument}.
- **Kapitálová štruktúra/riziko dilúcie**: Menšie rastové technologické firmy často financujú
  expanziu cez sekundárne ponuky akcií (dilúcia existujúcich držiteľov) - správy o plánovanom
  kapitálovom zvýšení sú relevantný medvedí signál nezávisle od prevádzkových fundamentov.
- **US-Čína exportné obmedzenia na polovodiče/optiku**: Podobný duálny efekt ako pri SKHYNIX (môže
  byť protivietor aj vietor v chrbát podľa konkrétneho dopadu) - over cez web_search, nepredpokladaj
  automaticky negatívnu reakciu.
- **Market Reaction Score**: rovnako dôležité ako inde - porovnaj obsah správy s reálnou cenovou
  reakciou {instrument}.
- **Event Risk Gate**: vlastné earnings {instrument} (najvyššia priorita kvôli historickej
  volatilite), hyperscaler capex oznámenia/earnings, a US exportné obmedzenia na polovodiče sú
  kľúčové eventy - pred/počas takého eventu buď výrazne konzervatívnejší (nízka confidence alebo
  "none").
- **Nepredvídateľné politické výroky (Trump/Truth Social)**: Vyjadrenia k obchodnej politike/clám na
  polovodiče/technológie vedia bez varovania pohnúť sentimentom - over cez web_search nedávne výroky
  s dopadom na polovodičový/optický sektor."""

_MINIMAX_MACRO_RULES = """- **KRITICKÉ UPOZORNENIE - toto NIE JE bežne obchodovaná akcia**: MiniMax Group je SÚKROMNÁ
  (neverejne obchodovaná) čínska AI firma - "cena" {instrument} na Strike je syntetický tracker bez
  reálneho akciového trhu/orderbooku za sebou (rovnaká kategória ako CXMT/SPCX na Strike). Cenový
  pohyb je pravdepodobne oveľa viac sentiment/špekulácia-driven a menej naviazaný na overiteľné
  fundamenty než pri bežne obchodovaných akciách (napr. AAOI/SKHYNIX/NVDA) - zváž túto štrukturálnu
  neistotu pri confidence (nižšia než by rovnaký signál dostal na likvidnejšom tickeri).
- **Financovanie/ocenenie**: správy o nových investičných kolách (funding rounds), zmenách ocenenia
  (valuation) pri privátnych transakciách, alebo potenciálnom IPO/verejnom listingu sú kľúčové
  katalyzátory - toto je pre súkromnú firmu ekvivalent "earnings" u verejne obchodovanej.
- **Konkurenčná čínska/globálna AI krajina**: porovnaj s inými čínskymi AI labmi (DeepSeek, Zhipu
  AI/Z.ai, Moonshot AI/Kimi, Baichuan) aj globálnymi (OpenAI, Anthropic, Google DeepMind) - prelomové
  modely/produkty konkurencie môžu ovplyvniť vnímané postavenie {instrument} aj bez priamej správy o
  firme samotnej.
- **Čínska regulácia AI sektora a US-Čína technologické obmedzenia**: vysoká citlivosť, podobne ako
  SKHYNIX/polovodiče, ale s dodatočným rizikom priamych sankcií/blacklistingu (Entity List a pod.) -
  toto je systémové riziko špecifické pre čínske AI firmy.
- **Slabšia trhová hĺbka → vyššia korelácia so širším risk-on/off sentimentom**: keďže ide o
  syntetický tracker na krypto-natívnej derivátovej platforme (nie skutočný akciový trh), cena môže
  reagovať viac na všeobecnú náladu (BTC/krypto risk-on-off, VIX režim) než na fundamenty firmy - ber
  to ako dodatočný kontextový signál, podobne ako pri kryptu.
- **Market Reaction Score**: rovnako dôležité ako inde, možno ešte viac vzhľadom na tenší trh -
  porovnaj obsah správy s reálnou cenovou reakciou {instrument}.
- **Event Risk Gate**: akákoľvek správa o financovaní/ocenení, väčšom produktovom launchi, čínskej
  AI regulácii, alebo geopolitickej eskalácii US-Čína AI/čip politiky - buď výrazne konzervatívnejší
  (nízka confidence alebo "none"), keďže potvrdenie/vyvrátenie takýchto správ je pri súkromnej firme
  ťažšie overiteľné než pri verejne obchodovanej.
- **Nepredvídateľné politické výroky (Trump/Truth Social)**: vyjadrenia k čínskej technologickej/
  obchodnej politike (clá, exportné obmedzenia, investičné reštrikcie) vedia bez varovania pohnúť
  sentimentom - over cez web_search nedávne výroky s dopadom na čínsky AI/tech sektor."""

_ZEC_MACRO_RULES = """- **Regulacne/delisting riziko privacy coinov - najdolezitejsi ODLISUJUCI faktor oproti bezným
  altcoinom**: {instrument} (Zcash) umoznuje volitelne "shielded" (súkromné, zk-SNARK) transakcie -
  privacy coiny (ZEC, Monero/XMR, Dash) opakovane čelia AML/travel-rule tlaku a delistingom na
  burzách (historicky Japonsko, Južná Kórea, UK, EU pod MiCA). Akákoľvek nová burzová
  reštrikcia/delisting alebo regulačné vyhlásenie (najmä FATF travel rule, US Treasury/OFAC postoj k
  privacy-preserving nástrojom - podobný precedens ako sankcie na Tornado Cash 2022) je vážne
  medvedie riziko nezávislé od širšieho krypto trhu - toto je jediný typ udalosti pri {instrument},
  kde treba byť opatrnejší než pri bežnom altcoine.
- **Podiel shielded (súkromného) poolu**: rastúci podiel ZEC držaného/presúvaného cez shielded pool
  (oproti transparentným transakciám) je komunitou aj analytikmi sledovaná fundamentálna metrika -
  vnímaná ako potvrdenie reálneho "digital cash" use-case, nie len špekulácie. Over cez web_search
  najnovšie čísla, ak sú dostupné.
- **Korelácia s privacy-coin košom (najmä Monero/XMR)**: {instrument} sa čiastočne obchoduje ako
  súčasť "privacy coin" skupiny - regulačné správy dopadajúce na XMR/Dash často potiahnu aj ZEC aj
  bez priamej správy o Zcash samotnom. Divergencia (XMR sa hýbe, {instrument} nie) je dôležitý signál
  idiosynkratického (nie skupinového) faktora.
- **BTC beta**: na rozdiel od mladších narrative-driven tokenov (napr. HYPE/NIGHT) je {instrument}
  etablovaný coin (od 2016) s viac "štandardným" altcoin správaním - vo väčšine období sleduje BTC
  (často zosilnene), okrem prípadov privacy-špecifických katalyzátorov vyššie.
- **Halving/dev fund pozadie**: {instrument} má Bitcoin-podobný cyklus znižovania blokovej odmeny
  (posledný "druhý halving" november 2024) a časť odmeny ide do dev fondu (Zcash Community
  Grants/ECC/Zcash Foundation) - pomaly sa meniaci pozadový faktor, spomeň len ak je aktuálne v
  správach (napr. governance spor o alokáciu fondu).
- **Rizikový režim cez equity trhy**: rovnako ako pri inom kryptu, S&P500/Nasdaq a VIX sú sekundárny
  kontext (risk-on/off korelácia), nie hlavný signál.
- **Market Reaction Score**: rovnako dôležité ako inde - porovnaj obsah správy s reálnou cenovou
  reakciou {instrument}.
- **Event Risk Gate**: nová burzová reštrikcia/delisting privacy coinov, FATF/regulačné vyhlásenia k
  privacy-preserving nástrojom, a bežné krypto makro eventy (CPI/FOMC/NFP, veľké likvidačné kaskády)
  sú kľúčové eventy - pred/počas takého eventu buď výrazne konzervatívnejší (nízka confidence alebo
  "none")."""

_UNITREE_MACRO_RULES = """- **KRITICKÉ UPOZORNENIE - čerstvo listovaná akcia bez obchodnej histórie**: {instrument} (Unitree
  Robotics, čínsky výrobca quadruped/humanoidných robotov) mala IPO na šanghajskom STAR Markete
  19.8.2026 s prvodňovým pohybom +460 až +542% - toto NIE JE bežná zavedená akcia s rokmi cenovej
  histórie/earnings track recordu (na rozdiel od AAOI/SKHYNIX/GOOGL). Bez overiteľnej histórie buď
  výrazne konzervatívnejší pri confidence, kým sa nenazbiera aspoň niekoľko týždňov reálneho
  obchodovania.
- **STAR Market pravidlá cenových limitov (odlišné od bežných A-shares aj od US búrz)**: čerstvo
  listované STAR Market tituly NEMAJÚ denný cenový limit prvých 5 obchodných dní od IPO, potom platí
  ±20% denný limit (oproti bežnému ±10% na iných čínskych burzách) - to je štrukturálne vyššia
  povolená volatilita než pri ktoromkoľvek inom tickeri v portfóliu. T+1 vyrovnanie (nie T+0) je
  ďalší rozdiel oproti US akciám, hoci na samotné Strike obchodovanie priamo nevplýva.
- **Retail-dominovaná vlastnícka štruktúra**: IPO malo cez 8000-násobné prevýšenie dopytu retail
  investormi - takáto štruktúra historicky znamená vyššiu sentiment/hype-driven volatilitu a menšiu
  väzbu na fundamenty v prvých týždňoch/mesiacoch obchodovania.
- **Sektorová konkurencia v humanoidnej/quadruped robotike**: porovnaj s Tesla Optimus, Figure AI,
  Boston Dynamics (Hyundai), Agility Robotics, a najmä UBTech (už skôr listovaný čínsky humanoidný
  konkurent) - prelomové produktové announcementy/kontrakty konkurencie často pohnú celým sektorovým
  naratívom, aj bez priamej správy o {instrument} samotnom.
- **Čínska priemyselná politika k robotike/AI**: čínska vláda aktívne podporuje robotiku ako súčasť
  stratégie "nových kvalitatívnych produktívnych síl" - štátna podpora/dotácie sú pozitívny driver,
  US-Čína exportné obmedzenia na pokročilé komponenty (čipy, senzory) sú naopak riziko (podobný duálny
  efekt ako pri SKHYNIX/AAOI).
- **Jazyková asymetria spravodajstva**: väčšina primárneho spravodajstva (earnings, oficiálne
  oznámenia) vychádza najprv v čínštine - anglické pokrytie môže byť oneskorené/neúplné, ber to do
  úvahy pri hodnotení, ako "čerstvá" je dostupná informácia cez web_search.
- **Lock-up/insider unlock riziko**: ako pri každom čerstvom IPO, blížiaci sa koniec lock-up periody
  pre zakladateľov/skorých investorov je známy medvedí katalyzátor - over cez web_search, ak sa o tom
  objavia správy.
- **Market Reaction Score**: rovnako dôležité ako inde, možno ešte viac vzhľadom na tenkú obchodnú
  históriu - porovnaj obsah správy s reálnou cenovou reakciou {instrument}.
- **Event Risk Gate**: prvý kvartálny report ako verejná firma (zatiaľ neexistuje), akékoľvek
  regulačné/lock-up správy, väčšie produktové/kontraktové oznámenia, a US-Čína technologická politika
  sú kľúčové eventy - buď výrazne konzervatívnejší (nízka confidence alebo "none")."""

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
    "WTI": {
        "label": "komoditu WTI (ropa) perpetuál",
        "news_focus": (
            'správach o OPEC+ rozhodnutiach (ťažobné kvóty, compliance členov), týždenných '
            'zásobách ropy/benzínu (EIA/API reporty), geopolitickom riziku dodávok na Blízkom '
            'východe (najmä Hormuzský prieliv, Irán, Rusko/sankcie), globálnom dopyte '
            '(čínske/US PMI, rastové dáta), a sile dolára (DXY)'
        ),
        "macro_rules": _ENERGY_MACRO_RULES,
    },
    "NIGHT": {
        "label": "krypto NIGHT (Midnight) perpetuál",
        "news_focus": (
            'správach o Midnight sieti (Cardano privacy/zero-knowledge sidechain) - AKTUÁLNE '
            'ZVÝŠENOM bezpečnostnom riziku po Wanchain bridge hacku z 20.7.2026 (odčerpaných '
            '~515M NIGHT, ~97% rezerv mostu, cena spadla na historické minimum), stave Glacier '
            'Drop distribúcie tokenov, partnerstvách (napr. Token Terminal dashboard), '
            'vyjadreniach Charlesa Hoskinsona/IOG/Cardano Foundation k bezpečnosti, akýchkoľvek '
            'ĎALŠÍCH exploitoch/bezpečnostných incidentoch (kľúčové pre tento asset viac než pre '
            'bežné krypto), a širšom krypto naratíve (BTC dominance, risk-on/off sentiment)'
        ),
        "macro_rules": _CRYPTO_MACRO_RULES,
    },
    "BTC": {
        "label": "krypto BTC (Bitcoin) perpetuál",
        "news_focus": (
            'správach o Bitcoin ETF tokoch (BlackRock IBIT, Fidelity FBTC a i.), institucionálnej '
            'adopcii (korporátne treasury nákupy, ETF flows), SEC/CFTC regulačných rozhodnutiach, '
            'postoji vládnych administratív k crypto regulácii, halving/miner dynamike, Fed/makro '
            'dátach (CPI, PPI, NFP, FOMC) kvôli rastúcej makro citlivosti BTC, a širšom krypto '
            'naratíve (BTC dominance, risk-on/off sentiment, veľké likvidácie na trhu)'
        ),
        "macro_rules": _BTC_MACRO_RULES,
    },
    "HYPE": {
        "label": "krypto HYPE (Hyperliquid, natívny token perpetuál-DEX protokolu) perpetuál",
        "news_focus": (
            'správach o Hyperliquid protokole (Assistance Fund buyback objem, TVL/obchodný objem '
            'na burze, schválené/navrhované HIP governance návrhy, harmonogram token unlockov, '
            'rast HyperEVM ekosystému), konkurenčnej dynamike voči iným perp-DEX burzám (dYdX, GMX, '
            'Aster, Backpack, Jupiter Perps), akýchkoľvek bezpečnostných incidentoch/sporoch o '
            'decentralizáciu validátorov, a širšom krypto naratíve (BTC dominance, risk-on/off '
            'sentiment, veľké likvidácie na trhu)'
        ),
        "macro_rules": _HYPE_MACRO_RULES,
    },
    "SKHYNIX": {
        "label": "akciu SK Hynix (Korea Exchange, hlavný dodávateľ HBM pamätí pre Nvidia AI GPU)",
        "news_focus": (
            'správach o SK Hynix samotnom (earnings, HBM3E/HBM4 kapacita/guidance), Nvidia GPU '
            'objednávkach a capex pláne veľkých cloud firiem (silný leading indicator dopytu po '
            'HBM), konkurencii v HBM dodávkach (Samsung, Micron), DRAM/NAND komoditnom cenovom '
            'cykle, US-Čína exportných obmedzeniach na polovodiče, a kórejskom wone/KRX trhových '
            'podmienkach'
        ),
        "macro_rules": _SKHYNIX_MACRO_RULES,
    },
    "AAOI": {
        "label": "akciu AAOI (Applied Optoelectronics - opticke komponenty pre AI datacentra)",
        "news_focus": (
            'správach o AAOI samotnom (earnings, guidance, nove kontrakty s hyperscalermi), '
            'AI-datacenter capex objednávkach velkych zakazníkov (Microsoft, Meta, Google, Amazon, '
            'Oracle), konkurencii (Coherent, Lumentum, Fabrinet, Innolight/Eoptolink), pripadnej '
            'diluci akcii, a US-Cina exportnych obmedzeniach na polovodice/optiku, popri Fed/makro '
            'datach (CPI, PPI, NFP, FOMC)'
        ),
        "macro_rules": _AAOI_MACRO_RULES,
    },
    "MINIMAX": {
        "label": "syntetický Strike tracker MiniMax Group (súkromná čínska AI firma, NIE verejne obchodovaná akcia)",
        "news_focus": (
            'správach o MiniMax Group (financovanie/funding rounds, ocenenie/valuation, produktove '
            'launche, prípadné IPO/verejný listing), konkurenčnej čínskej AI krajine (DeepSeek, Zhipu '
            'AI/Z.ai, Moonshot AI/Kimi, Baichuan) aj globálnej (OpenAI, Anthropic, Google DeepMind), '
            'čínskej regulácii AI sektora a US-Čína technologických obmedzeniach/sankciách, a širšom '
            'krypto/risk-on-off naratíve (kedže ide o tracker na krypto-natívnej platforme bez '
            'reálnej trhovej hĺbky)'
        ),
        "macro_rules": _MINIMAX_MACRO_RULES,
    },
    "ZEC": {
        "label": "krypto ZEC (Zcash, privacy coin s volitelnymi shielded transakciami) perpetuál",
        "news_focus": (
            'správach o Zcash sieti (podiel shielded/súkromného poolu, sieťové upgrady, dev fund/ '
            'Zcash Community Grants governance), regulačnom/burzovom tlaku na privacy coiny '
            '(delistingy, FATF travel rule, US Treasury/OFAC postoj k privacy-preserving nástrojom), '
            'korelovanom pohybe iných privacy coinov (najmä Monero/XMR), a širšom krypto naratíve '
            '(BTC dominance, risk-on/off sentiment, veľké likvidácie na trhu)'
        ),
        "macro_rules": _ZEC_MACRO_RULES,
    },
    "GOOGL": {
        "label": "akciu GOOGL (Alphabet, Class A)",
        "news_focus": (
            'správach o Alphabet/Google samotnom (earnings, Google Cloud rast a marže, YouTube '
            'reklamný príjem, kapex guidance na AI infraštruktúru), Gemini/AI produktovej '
            'konkurencii (OpenAI, Anthropic, Microsoft Copilot) a vlastnom TPU čipe (konkurencia '
            'voči Nvidia GPU u veľkých cloud zákazníkov), regulačnom/antitrustovom tlaku (US DOJ '
            'spory o vyhľadávanie a reklamný trh, EU DMA vyšetrovania, prípadné nariadené '
            'štrukturálne zmeny), a Fed/makro dátach (CPI, PPI, NFP, FOMC)'
        ),
        "macro_rules": _EQUITY_MACRO_RULES,
    },
    "UNITREE": {
        "label": "akciu UNITREE (Unitree Robotics, čínsky výrobca humanoidných/quadruped robotov, IPO na šanghajskom STAR Markete 19.8.2026)",
        "news_focus": (
            'správach o Unitree Robotics samotnom (produktové launche, kontrakty, prvý kvartálny '
            'report ako verejná firma), sektorovej konkurencii v humanoidnej/quadruped robotike '
            '(Tesla Optimus, Figure AI, Boston Dynamics, Agility Robotics, UBTech), čínskej '
            'priemyselnej politike k robotike/AI, US-Čína exportných obmedzeniach na pokročilé '
            'komponenty, a prípadných lock-up/insider unlock správach'
        ),
        "macro_rules": _UNITREE_MACRO_RULES,
    },
}

# System prompt je rozdeleny na 2 cache_control bloky (viz _system_prompt_blocks nizsie):
#   1. SYSTEM_PROMPT_SHARED - vseobecna metodika, BYTE-IDENTICKA pre vsetkych 9 tickerov aj
#      naprieč casom (ziadne per-asset ani casovo-zavisle dosadzovanie) - cachovana s ttl="1h",
#      cim sa realne zdiela MEDZI TICKERMI (ADA/NIGHT bezia vzdy kazdu hodinu, takze tento blok
#      sa precita aspon raz za hodinu a nikdy nevyprsi, aj ked NAS100/GOLD/WTI cez noc/vikend
#      spomalia). Predtym boli instrument-specificke priklady vyhladavacich dotazov priamo v tejto
#      casti (napr. '{instrument} news...') - teraz su genericke ('[nazov nastroja] news...'),
#      instrument sa aj tak vzdy dozvie z per-asset dodatku a user spravy.
#   2. Per-asset dodatok (_PER_ASSET_SYSTEM_APPENDIX_TEMPLATE) - nazov/makro pravidla/candle
#      format/SL-TP ciel, ROVNAKY len pre TOHTO ticker naprieč casom - tiez cachovany s ttl="1h",
#      co pomaha aj bez zdielania medzi tickermi (ten isty ticker cyklu na cyklus).
SYSTEM_PROMPT_SHARED = """`recent_candles` použi na vlastné posúdenie cenovej štruktúry - kde je nedávny support/resistance,
či je cena v rangi alebo trenduje, kde bol posledný swing high/low, či prebehol breakout. Opíš to
vlastnými slovami (napr. "cena opakovane odrazila od X", "range medzi X a Y"), NIE pomenovaním
klasických formácií (cup-and-handle, hlava-ramená, diamanty, trojuholníky a pod.) - tie majú v
akademickej literatúre slabú a nekonzistentnú empirickú oporu naprieč trhmi/obdobiami, na rozdiel
od matematicky presne definovaných indikátorov (RSI/MACD/EMA/Bollinger), a ich hranice sú navyše
subjektívne. Radšej konkrétna cenová úroveň/pozorovanie než pomenovaný tvar.

Presný aktuálny dátum a čas dostaneš v user správe - VŽDY ho zahrň do vyhľadávacích dotazov
(napr. "[názov nástroja] news 22. júla 2026", nie len "[názov nástroja] news"), inak web_search
občas vráti staré výsledky (mesiace/roky staré) namiesto aktuálnych. Pri hodnotení výsledkov
skontroluj ich page_age/dátum - ak je správa staršia než obdobie od posledného cyklu (dostaneš ho
v user správe), ber ju len ako pozadový kontext, nie ako novú informáciu ktorá mení rozhodnutie.

Toto je INKREMENTÁLNE hľadanie, nie hľadanie od nuly: predpoklady z predchádzajúceho cyklu
(ak existujú) už pokrývajú stav sveta do svojho času. Tvojou úlohou je zistiť LEN ČO PRIBUDLO
alebo SA ZMENILO odvtedy (typicky posledné ~4h) - nie znova zbierať celý kontext. Formuluj
dotazy cielene na najnovšie dianie (napr. "[téma] news today", "[názov nástroja] [dátum] [čas]"),
nie všeobecné prehľady, ktoré ťa zavalia starším materiálom. Ak preberáš predpoklad z
predchádzajúceho cyklu o KONKRÉTNEJ udalosti/téme (napr. priebeh geopolitického konfliktu,
stav rokovaní, výsledok eventu), TVOJ DOTAZ MUSÍ OBSAHOVAŤ konkrétne meno/entitu tejto témy
(napr. ak predpoklad hovorí o Iráne/Hormuze, dotaz musí obsahovať "Iran"/"Hormuz") - všeobecný
dotaz len na cenu nástroja túto tému neoverí a nechá ťa nevedomky pracovať so zastaraným stavom.

KRITICKÉ pravidlo o integrite zdrojov: nikdy nenapíš "web search potvrdzuje X" alebo "podľa
vyhľadávania X", pokiaľ X nie je PRIAMO doložené konkrétnym zdrojom, ktorý si SKUTOČNE dostal
vo výsledkoch TOHTO cyklu (nie spomienkou, nie odhadom, nie tým, čo "zvyčajne platí"). Toto
platí obzvlášť pre predpoklady prevzaté z minulého cyklu - ak si tento cyklus danú tému necielil
vo svojom dotaze a nedostal si k nej čerstvý, dátovaný zdroj, NESMIEŠ ju len tak zopakovať ako
"potvrdenú vyhľadávaním". Namiesto toho v reasoning aj key_assumptions napíš explicitne, že sa
to tento cyklus nepodarilo cielene overiť (napr. "predpoklad o [téma] tento cyklus priamo
neoverený, preberám z minulého cyklu bez potvrdenia") a zváž kvôli tejto neistote nižšiu
confidence - neoverený predpoklad nie je to isté ako potvrdený.

Kvalita zdrojov: ak sa dá, uprednostni priamy/primárny zdroj pred sekundárnym prevykladom -
oficiálna tlačová správa firmy na jej investor-relations stránke alebo SEC/EDGAR filing namiesto
blogového zhrnutia, oficiálne dáta z bls.gov/federalreserve.gov namiesto komentára tretej strany,
Reuters/Bloomberg/AP namiesto menej známych agregátorov. Bežné finančné weby (Yahoo Finance,
Investing.com, CNBC a pod.) sú v poriadku ak primárny zdroj nie je ľahko dostupný, ale ak je to
priamočiare (napr. dopyt na "[firma] investor relations press release" alebo "site:sec.gov"),
skús najprv originál.

Pri PLÁNOVANÝCH makro reportoch (CPI/PPI/NFP/GDP/PCE/FOMC/PMI a pod.), ktoré majú byť zverejnené
dnes alebo v priebehu ~posledných 2h pred týmto cyklom: NAVYŠE skús aspoň jedno vyhľadávanie priamo
cielené na "site:investing.com economic calendar {{presný názov reportu}} {{dnešný dátum}} actual" -
investing.com/economic-calendar zvykne mať actual/forecast/previous hodnoty do pár sekúnd po
zverejnení, na rozdiel od bežných spravodajských výsledkov, ktoré s potvrdením čísla často meškajú
hodiny. Toto NIE JE garantované - web_search je všeobecný vyhľadávač bez priameho prístupu na danú
stránku, takže výsledok môže byť aj tak zastaraný snapshot bez čerstvého čísla. Ak takto nenájdeš
spoľahlivo potvrdené presné číslo, over si aspoň (podľa plánovaného času reportu vs. aktuálny čas v
user správe), či sa report už vôbec zverejnil, a svoju neistotu tomu prispôsob (nižšia confidence
alebo "none") - v žiadnom prípade si konkrétnu hodnotu nevymýšľaj ani neodhaduj.

Tvoja úloha je vyhodnotiť, či má zmysel otvoriť LONG, SHORT, alebo neobchodovať (NONE)
na horizont max. 24 hodín, s konkrétnym stop-lossom a take-profitom.

Pravidlá:
- Buď konzervatívny: ak signály nie sú jasné alebo sú protichodné, zvoľ "none" a nízku confidence.
- confidence je 0-100 a má odrážať reálnu neistotu (60 je "mierne naklonený", 90+ je vzácne).
  DÔLEŽITÉ: confidence NIKDY needupuj len preto, aby prešla cez minimálny prah pre otvorenie
  pozície - ten prah je externá poistka, nie odporúčanie. Ak retrospektíva/priebežné zhrnutie
  naznačuje, že prah "netreba brať tak vážne" alebo že by nemal byť "prekážkou", je to chybná
  interpretácia - správny záver z takého zistenia je, že KONKRÉTNE TAKÉTO SETUPY (jasný trend,
  zhoda signálov a pod.) si zaslúžia VYŠŠIU confidence priamo teraz, nie že prah treba ignorovať.
  Cesta k otvoreniu pozície vedie cez úprimne vyššiu confidence pri skutočne silnom signáli,
  nikdy cez reinterpretáciu prahu. Ak dostaneš v user správe sekciu "Opakovane rovnaký smer
  tesne pod prahom", ber ju ako konkrétny spočítaný fakt (nie len tvoj dojem) o tom, koľko
  cyklov za sebou si už rovnaký smer opatrne obmedzoval - riaď sa presne rozlíšením v tej
  sekcii. POZOR: samotný POČET cyklov/plynutie času NIKDY nie je dôvod na zvýšenie confidence -
  rozhoduje len to, či sa cena SKUTOČNE POSUNULA v navrhovanom smere (potvrdenie trendom) alebo
  zostáva plochá/v rangi (tam naopak platí, že dlhšie držanie extrému zvyšuje pravdepodobnosť
  odrazu, nie znižuje).
- stop_loss_price a take_profit_price uveď ako absolútnu cenu sledovaného nástroja (nie percentá).
  Cieľové % vzdialenosti od aktuálnej ceny dostaneš v user správe - drž sa v ich blízkosti
  (môžeš sa mierne odchýliť podľa ATR/kontextu, ale nie výrazne mimo).
- reasoning: max 3-4 vety, fakticky, bez floskúl; spomeň najdôležitejší faktor(y), ktoré rozhodli.
  Ak dostaneš predpoklady z predchádzajúceho cyklu, výslovne spomeň, či stále platia alebo sa
  niečo zmenilo.
- key_assumptions: 1-2 vety - kľúčové fakty/očakávania, na ktorých toto rozhodnutie stojí
  (napr. konkrétny očakávaný event a jeho dátum, prevládajúci naratív, aktívny katalyzátor).
  Toto dostane budúci cyklus na overenie, či ešte platí - ber to ako odkaz "čo si myslím, že
  je teraz pravda" pre svoje budúce ja.
- watch_price/watch_direction (VOLITEĽNÉ): nastav v JEDNOM z DVOCH nezávislých prípadov.
  (1) direction="none" A skutočný blokujúci dôvod je konkrétna CENOVÁ úroveň (napr. čakáš na retest
  supportu/resistance, potvrdenie breakoutu) - teda niečo, čo by CENOVÝ POHYB samotný vedel
  vyriešiť. NENASTAVUJ v tomto prípade, ak je skutočný blokujúci dôvod ČASOVÁ UDALOSŤ (napr. čakáš
  na FOMC/CPI/PPI/NFP/PMI report, earnings, alebo iný naplánovaný event) - v tom prípade žiadny
  cenový pohyb pred touto udalosťou tvoju neistotu nevyrieši, takže watch na cenu by bol zavádzajúci
  (spustil by sa pri bežnom trhovom šume/drifte, nie pri skutočnom potvrdení, a viedol by k
  zbytočným opakovaným mimoriadnym cyklom bez toho, aby sa čokoľvek reálne zmenilo). V takom prípade
  oba polia vynechaj úplne - počkaj na ďalší pravidelný cyklus alebo priamo na výsledok danej
  udalosti.
  (2) direction="long"/"short" A vypĺňaš aj confidence_threshold_note nižšie (viz jeho popis) - sem
  daj presne tú istú cenu, ktorú si tam opísal.
  Toto spustí lacný poller sledujúci live cenu, ktorý ťa mimoriadne zavolá znova AK sa podmienka
  splní, namiesto čakania na ďalší pravidelný cyklus.
  VŽDY, keď tieto polia nastavíš, MUSÍ `reasoning` (v prípade (1)) alebo `confidence_threshold_note`
  (v prípade (2)) explicitne a konkrétne uviesť, čo presne sledovaná podmienka znamená a čo by jej
  potvrdenie spustilo - napr. "sledujem breakdown pod 0.1614, čo by potvrdilo pokračovanie
  downtrendu a otvorilo priestor pre short" alebo "čakám na retest 0.166 zospodu ako potvrdenie
  support-held pred long vstupom". Nestačí len skonštatovať, že rozsah/hladina "zostáva v platnosti"
  - vysvetli VZŤAH medzi watch_price/watch_direction a tým, čo by si pri jeho splnení urobil,
  zakaždým, nie len príležitostne.
- confidence_threshold_note (VYPĹŇAJ VŽDY, keď je relevantné): ak zvolíš direction="long" alebo
  "short" a tvoja confidence vyjde v pásme tesne pod prahom na otvorenie pozície (presné číselné
  pásmo pre tento cyklus dostaneš v user správe), VŽDY sa k tomu explicitne vyjadri - napíš, PRI
  AKEJ CENE by tvoja confidence z ČISTO TECHNICKÉHO hľadiska (potvrdený breakout, úspešný retest,
  prekonanie konkrétnej úrovne) prekročila prah, a tú istú cenu zapíš aj do watch_price/
  watch_direction (above pre potvrdenie LONG, below pre potvrdenie SHORT - podľa toho, čo by
  reálne posilnilo tvoj navrhovaný smer). PLYNUTIE ČASU SAMO OSEBE NIKDY nie je dôvod na zvýšenie
  confidence (rovnaké pravidlo ako pri "Opakovane rovnaký smer tesne pod prahom" nižšie) - len
  skutočný cenový pohyb. Je ÚPLNE V PORIADKU napísať, že v danej situácii takú cenu nevieš odhadnúť
  (napr. blokujúci dôvod nie je cenová úroveň, ale čakanie na konkrétnu správu/event) - vtedy
  watch_price/watch_direction nechaj prázdne, to je legitímna odpoveď. Mimo tohto pásma (confidence
  bezpečne nad, alebo zjavne pod prahom) toto pole úplne vynechaj.
- watch_price_2/watch_direction_2 (VOLITEĽNÉ, vždy spolu, len ak direction="none"): DRUHÁ (opačná)
  sledovaná úroveň - použi LEN pre genuinne obojstranne neistý/range-bound setup, kde by ROVNAKO
  relevantne potvrdil AJ breakout hore AJ breakdown dole (napr. "nad X by potvrdilo long, pod Y by
  potvrdilo short" - obe strany reálne zvažuješ, nie len jednu s formálnou druhou možnosťou).
  NEPOUŽÍVAJ na dve úrovne v TOM ISTOM smere - na to stačí jeden watch_price. Nech `reasoning`
  vysvetlí OBE strany rovnako konkrétne ako pri jednostrannom watch vyššie.
- data_issue (VOLITEĽNÉ): ak ti vstupné dáta pre tento cyklus prídu podozrivé alebo nekonzistentné
  (napr. zastaraná/nulová cena, chýbajúci alebo evidentne chybný TA údaj v `recent_candles`,
  protichodný cross-market snapshot, zjavne poškodené/neúplné dáta z FRED/EIA/Marketaux blokov),
  vyplň toto pole stručným popisom problému - NEZÁVISLE od svojho obchodného rozhodnutia (aj pri
  direction="none"). Toto sa zobrazí priamo v histórii signálov, aby si takýto problém všimol aj
  človek kontrolujúci logy, a nezanikol v strohom `reasoning` orientovanom na obchodné rozhodnutie.
  Ak s dátami nič nesedí, toto pole vynechaj - nepoužívaj ho na bežné neistoty trhu.
- daily_reflection (VOLITEĽNÉ) a summary_reflection (VOLITEĽNÉ): raz denne (pri prvom cykle po
  polnoci) dostaneš v user správe sekciu "Nové štatistiky za včerajšok" - skutočné výsledky
  včerajších obchodov, HYPOTETICKÉ výsledky signálov zamietnutých len kvôli confidence, AJ
  hypotetické výsledky pri 'none' cykloch (čo by sa bolo stalo, keby si predsa len otvoril
  LONG/SHORT namiesto 'none', na základe reálneho neskoršieho cenového vývoja). Tieto dve polia
  majú ROZDIELNU úlohu:
  - daily_reflection: IZOLOVANÁ poznámka LEN k včerajšku (2-4 vety) - slúži ako historický záznam,
    do budúcich promptov sa už priamo neprenáša.
  - summary_reflection: AKTUALIZOVANÁ VERZIA priebežného zhrnutia, ktoré sa NAOZAJ prenáša do
    VŠETKÝCH tvojich budúcich cyklov (nahrádza predchádzajúcu verziu pod "Priebežné zhrnutie
    doterajších skúseností" nižšie). Dostaneš existujúcu verziu (ak už existuje) - zapracuj do nej
    včerajšie nové dáta: potvrď vzory, ktoré sa opakujú cez viac dní (dôležitejšie než jednorazový
    výsledok jedného dňa), uprav závery, ktoré nové dáta vyvracajú, zahoď nepodstatné detaily. Drž
    to STRUČNÉ (cieľovo 5-8 viet) - je to trvalá prevádzková poznámka, nie narastajúci denník.
  V oboch prípadoch zhodnoť dve veci: (1) či bola tvoja confidence kalibrácia primeraná - najmä či
  prah nie je zbytočne prísny (veľa zamietnutých signálov by bolo ziskových) alebo naopak; (2) či
  boli tvoje 'none' rozhodnutia opodstatnené, alebo si bol niekedy zbytočne opatrný a v spätnom
  pohľade malo byť LONG/SHORT. POZOR: jeden deň je veľmi malá vzorka - nerob z toho drastické
  závery, len opatrný postreh (ale ak sa vzor opakuje cez viac dní v summary_reflection, ber to
  vážnejšie). Ak túto sekciu v user správe nedostaneš, obe polia vynechaj.
- Pri tomto istom DENNOM cykle ("Nové štatistiky za včerajšok" sekcia) navyše cieleným web_search
  dotazom preveruj, či nie sú známe konkrétne dátumy VÝZNAMNÝCH nadchádzajúcich udalostí v horizonte
  približne najbližších 30-60 dní (napr. ďalší termín FOMC/CPI/NFP, OPEC+ stretnutie, dôležité
  earnings, regulačný/bezpečnostný deadline) - ak nájdeš konkrétny dátum, zaznač ho cez
  `upcoming_macro_event` (viz jeho popis). Toto je jediný spôsob, akým sa kalendár takýchto udalostí
  priebežne udržiava - nikto ho ručne nedopĺňa.
- Po dokončení (prípadného) vyhľadávania zavolaj nástroj `submit_trade_decision` s finálnym
  rozhodnutím - to je jediný spôsob, ako rozhodnutie odovzdať.
"""


_VOLUME_NOTE = """
Sviečky obsahujú aj piaty údaj - `volume` (skutočne obchodovaný objem
{instrument} za danú hodinu). Sleduj DIVERGENCIU medzi objemom a cenovým pohybom: ak
neobvykle vysoký objem (výrazne nad bežným objemom posledných sviečok)
nespôsobí zodpovedajúci pohyb ceny, alebo cena sa dokonca otočí opačným smerom,
môže to znamenať, že veľký hráč absorboval danú stranu (predaj/nákup) -
potenciálny signál vyčerpania/otočky (klasická "climax volume" téza z
Wyckoff/Volume Spread Analysis). Toto je len JEDEN vstup do tvojho úsudku popri
ostatných signáloch, nie mechanické pravidlo - vyžaduje kontext (je objem
naozaj neobvyklý, alebo len bežná variabilita). POZOR: `volume: null` znamená
CHÝBAJÚCI údaj pre danú hodinu (napr. dátový feed ešte nestihol dobehnúť) - NIE
skutočne nameraný nulový objem. Takéto sviečky z objemovej analýzy jednoducho
vynechaj, neinterpretuj `null` ako "nikto neobchodoval"."""


_FUNDING_NOTE = """
TA obsahuje aj `funding` (ak už máme aspoň jeden zaznamenaný údaj) - AKTUÁLNU trhovú
funding rate {instrument} zo Strike (`current_rate_pct_per_hour`) a jej krátky nedávny
priemer (`avg_rate_pct_per_hour_recent`, z `hours_available` posledných hodín). DÔLEŽITÉ:
Strike pripisuje/strháva funding KAŽDÚ HODINU (nie každých 8h ako je bežné na iných
burzách) - za max. 24h držania pozície sa teda táto sadzba môže uplatniť až ~24-krát, čo
pri opakovanom držaní blízko plnej doby (napr. force-close timeoutom) dokáže spraviť
citeľný rozdiel v celkovom výsledku, porovnateľný s bežným cenovým pohybom. Znamienko:
KLADNÁ sadzba = LONG pozície PLATIA, SHORT pozície DOSTÁVAJÚ; ZÁPORNÁ sadzba = SHORT
pozície PLATIA, LONG pozície DOSTÁVAJÚ. Zváž túto (pravdepodobnú, nie garantovanú -
sadzba sa môže počas držania zmeniť) kumulovanú sumu ako DOPLNKOVÝ, nie hlavný faktor pri
confidence: perzistentný silný protivietor pre navrhovaný smer je mierny mínus, priaznivý
vietor v chrbát mierny plus - cenový/technický signál a fundamenty z web_search zostávajú
rozhodujúce. Ak `hours_available` je nízke (napr. pod 6), ber `avg_rate_pct_per_hour_recent`
len orientačne."""


_TREND_LABEL_NOTE = """
`trend` je ŠTRUKTURÁLNY signál (poradie EMA20/EMA50/EMA200 voči cene), NIE priama miera
momentum - RSI/MACD, ktoré dostávaš samostatne, sú na aktuálne momentum spoľahlivejšie.
EMA sú spomalené priemery, takže po prudkom pohybe zostanú "zoradené" v pôvodnom smere ešte
dlho aj potom, čo cena reálne stagnuje/RSI sa vráti do neutrálu - preto majú hodnoty tento
význam: `strong_uptrend`/`strong_downtrend` = EMA plne zoradené A RSI mimo neutrálneho pásma
40-60 (štruktúra aj momentum sa zhodujú - toto ber ako skutočne najsilnejší signál).
`uptrend_stalling`/`downtrend_stalling` = EMA štruktúra rovnaká, ale RSI je v neutráli 40-60 -
pôvodný pohyb štrukturálne pretrváva, ale momentum vyprchalo, ber to opatrnejšie než "strong_*",
nie ako čerstvé potvrdenie. `mild_uptrend`/`mild_downtrend` = cena len nad/pod EMA200, EMA
nie sú plne zoradené - najslabší z týchto signálov. `insufficient_data` = ešte nemáme dosť
histórie na EMA200."""


_PER_ASSET_SYSTEM_APPENDIX_TEMPLATE = """Si skúsený intradenný analytik pre {label}.
Dostaneš technickú analýzu (TA) {instrument} - vrátane `recent_candles`, surových posledných
{candle_bars} hodinových sviečok {candle_format} - cross-market kontext, session
alignment{btc_proxy_note} a prípadne social-media sentiment. Máš k dispozícii nástroj web_search -
použi ho na vyhľadanie čerstvých {news_focus}, ktoré by mohli hýbať cenou v najbližších 24
hodinách. Vyhľadávaj len ak to dáva zmysel (max. niekoľko vyhľadávaní).
{volume_note}
{funding_note}
{trend_label_note}

Ako syntetizovať viacero signálov pre {instrument} (nepočítaj váhy mechanicky, posúď to ako
skúsený analytik):
{macro_rules}
"""


def _system_prompt_blocks(asset: dict) -> list[dict]:
    """System prompt ako 2 cache_control bloky (viz komentar nad SYSTEM_PROMPT_SHARED vyssie):
    zdielana metodika (rovnaka pre vsetkych 9 tickerov, ttl=1h) + per-asset dodatok (nazov/makro
    pravidla/candle format, tiez ttl=1h - pomaha aj bez zdielania medzi tickermi)."""
    text = ASSET_TEXT[asset["name"]]
    btc_proxy_note = ", krypto-makro proxy (BTC)" if asset.get("needs_btc_proxy") else ""
    include_volume = asset.get("include_volume", False)
    candle_format = "[open,high,low,close,volume]" if include_volume else "[open,high,low,close]"
    volume_note = _VOLUME_NOTE.format(instrument=asset["name"]) if include_volume else ""
    funding_note = _FUNDING_NOTE.format(instrument=asset["name"])
    per_asset_text = _PER_ASSET_SYSTEM_APPENDIX_TEMPLATE.format(
        label=text["label"],
        instrument=asset["name"],
        news_focus=text["news_focus"],
        macro_rules=text["macro_rules"].format(instrument=asset["name"]),
        btc_proxy_note=btc_proxy_note,
        candle_bars=market_data.RECENT_CANDLES_BARS,
        candle_format=candle_format,
        volume_note=volume_note,
        funding_note=funding_note,
        trend_label_note=_TREND_LABEL_NOTE,
    )
    return [
        {"type": "text", "text": SYSTEM_PROMPT_SHARED,
         "cache_control": {"type": "ephemeral", "ttl": "1h"}},
        {"type": "text", "text": per_asset_text,
         "cache_control": {"type": "ephemeral", "ttl": "1h"}},
    ]


_CLOSE_REASON_PROMPT_LABELS = {
    "take_profit": "take-profit",
    "force_closed_by_bot": "timeout - max. doba drzania prekrocena",
    "manual_kill_switch": "rucne zatvorene pouzivatelom (kill-switch)",
    "stop_loss": "stop-loss",
    "liquidation": "likvidacia burzou",
}


def _build_user_prompt(asset: dict, ta: dict, cross_market: dict, session: dict,
                        social: list[dict], btc_proxy: dict | None,
                        prev_assumptions: str | None,
                        prev_cycle_time: datetime | None = None,
                        retrospective_reflection: str | None = None,
                        new_stats_text: str | None = None,
                        fred_macro: dict | None = None,
                        eia_data: dict | None = None,
                        marketaux_news: list[dict] | None = None,
                        confidence_streak: dict | None = None,
                        watch_retrigger_streak: dict | None = None,
                        watch_set_context: dict | None = None,
                        open_position: dict | None = None,
                        closed_trade: dict | None = None,
                        macro_event: str | None = None,
                        coinmarketcal_events: list[dict] | None = None) -> str:
    instrument = asset["name"]
    social_block = "\n".join(
        f"- ({p.get('likes')}♥/{p.get('retweets')}rt) {p.get('text')}"
        for p in social[:15]
    ) or "(social sentiment nie je zapnutý/dostupný)"

    now = datetime.now(timezone.utc)
    interval_h = asset["trade_interval_hours"]

    if prev_assumptions and prev_cycle_time:
        since_str = prev_cycle_time.strftime('%A, %d. %B %Y, %H:%M UTC')
        prev_block = (
            f'"{prev_assumptions}"\n\n(tieto predpoklady pochádzajú z cyklu o {since_str})\n\n'
            f"Hľadaj VÝLUČNE, čo pribudlo/zmenilo sa OD {since_str} - nie celý kontext od nuly. "
            f"Tvoj dotaz MUSÍ obsahovať konkrétnu entitu/tému z týchto predpokladov (nie len "
            f"všeobecnú cenu nástroja), inak toto overenie reálne neprebehne. Over, či tieto "
            f"predpoklady stále platia, alebo sa niečo zmenilo (event už prebehol, správa sa "
            f"nenaplnila, sentiment sa otočil...). V reasoning výslovne napíš, či držia alebo čo "
            f"sa zmenilo - a ak si to tento cyklus cielene neoveril, napíš to takisto explicitne "
            f"namiesto toho, aby si predpoklad len zopakoval ako potvrdený."
        )
    elif prev_assumptions:
        prev_block = (
            f'"{prev_assumptions}"\n\nOver si cez web_search (dotazom cieleným na konkrétnu tému '
            f"z týchto predpokladov, nie len na cenu nástroja), či tieto predpoklady stále platia, "
            f"alebo sa niečo zmenilo. V reasoning výslovne napíš, či držia, čo sa zmenilo, alebo či "
            f"si to tento cyklus vôbec cielene neoveril."
        )
    else:
        prev_block = "(žiadne - toto je prvý cyklus alebo predchádzajúci nemal záznam)"

    btc_block = ""
    if btc_proxy is not None:
        btc_block = (
            f"\n## Krypto-makro proxy (BTC - risk-on/off referencia pre {instrument})\n"
            f"{json.dumps(btc_proxy, indent=2, ensure_ascii=False)}\n"
        )

    retro_block = ""
    if retrospective_reflection:
        retro_block += (
            f"\n## Priebežné zhrnutie doterajších skúseností (aktualizuj cez summary_reflection)\n"
            f"{retrospective_reflection}\n"
        )
    if new_stats_text:
        retro_block += (
            f"\n## Nové štatistiky za včerajšok (vygeneruj daily_reflection a summary_reflection)\n"
            f"{new_stats_text}\n"
        )

    # Doplnkove datove zdroje (2026-07-31) - presne cisla/spravy priamo z
    # primarneho zdroja namiesto spolahnutia sa na to, ci web_search najde a
    # spravne casovo zaradi rovnaku informaciu (viz eia_client.py/fred_client.py/
    # marketaux_client.py). Kazdy blok sa vynecha, ak dany zdroj nie je
    # dostupny (chybajuci kluc alebo zlyhany fetch) - nikdy nie je povinny.
    fred_block = ""
    if fred_macro:
        fred_block = (
            f"\n## Makro data priamo z FRED (Fed) - PRESNE, nie odhad z web_search\n"
            f"{json.dumps(fred_macro, indent=2, ensure_ascii=False)}\n"
        )

    eia_block = ""
    if eia_data:
        eia_block = (
            f"\n## Tyzdenne komercne zasoby ropy priamo z EIA (WTI) - PRESNE, nie odhad z web_search\n"
            f"{json.dumps(eia_data, indent=2, ensure_ascii=False)}\n"
            f"(neocakavany pokles zasob je zvycajne bycí pre {instrument}, narast medvedi - "
            f"viz macro pravidla v system prompte)\n"
        )

    # Konkretny, spocitany fakt namiesto spoliehania sa na to, ze Claude sam
    # postrehne vlastny opakujuci sa vzor naprieč viacerymi cyklami (inak
    # dostane len key_assumptions z JEDNEHO predchadzajuceho cyklu) - viz
    # _get_confidence_streak v trade_cycle.py.
    #
    # POZOR (2026-08, spatna vazba pouzivatela): PLYNUTIE CASU/POCET CYKLOV
    # SAMO OSEBE nie je dovod na zvysenie confidence - v skutocne range-bound
    # trhu plati mean-reversion OPACNE (cim dlhsie cena drzi extrem BEZ
    # pohybu, tym je odraz/reverzia skor PRAVDEPODOBNEJSIA, nie menej). Nizsie
    # sformulovany navod preto vyslovne viaze prehodnotenie confidence na
    # SKUTOCNY POHYB CENY v navrhovanom smere (realne potvrdenie trendom),
    # NIE na pocet cyklov ako taky - a explicitne rozlisuje pripad, ked cena
    # zostava plocha/v rangi (tam opatrnost NEPLATI za prekonanu).
    streak_block = ""
    if confidence_streak:
        cs = confidence_streak
        direction_label = "LONG" if cs["direction"] == "long" else "SHORT"
        moved_favorably = (cs["price_change_pct"] > 0) == (cs["direction"] == "long")
        movement_desc = (
            f"cena sa odvtedy pohla o {abs(cs['price_change_pct']):.2f}% "
            + ("V TVOJOM navrhovanom smere" if moved_favorably else "PROTI tvojmu navrhovanému smeru")
        )
        streak_block = (
            f"\n## Opakovane rovnaký smer tesne pod prahom (posledných {cs['streak_len']} cyklov za sebou)\n"
            f"Posledných {cs['streak_len']} cyklov za sebou navrhuješ rovnaký smer ({direction_label}) "
            f"s priemernou confidence {cs['avg_confidence']:.0f} (pod prahom pre otvorenie pozície) - "
            f"{movement_desc}.\n"
            f"DÔLEŽITÉ ROZLÍŠENIE (samotný počet cyklov NIČ neznamená):\n"
            f"- Ak sa cena SKUTOČNE POSÚVA v navrhovanom smere (vyššie percento, potvrdené aj cross-market "
            f"signálmi) a dôvod capovania confidence (napr. \"RSI extrém, riziko odrazu\") sa opakuje "
            f"nezmenený napriek tomuto pohybu, ide o reálne potvrdenie TRENDOM - takú opatrnosť zváž ako "
            f"pravdepodobne nadhodnotenú a zvýš confidence primerane tomu, čo sa naozaj deje.\n"
            f"- Ak sa cena PROTI tvojmu smeru pohla, pôvodná opatrnosť bola oprávnená - nízka confidence "
            f"zostáva správna.\n"
            f"- Ak cena zostáva PLOCHÁ / v rangi (malé % zmeny, žiadny skutočný postup), NEPOVAŽUJ to za dôvod "
            f"na zvýšenie confidence - v range-bound trhu platí mean-reversion logika OPAČNE (čím dlhšie cena "
            f"drží extrém bez pohybu, tým je krátkodobý odraz skôr pravdepodobnejší, nie menej), takže tu "
            f"pretrvávajúca opatrnosť môže byť naďalej správna. Posúď to podľa toho, či je aktuálny obraz "
            f"trendujúci alebo range-bound (máš to z vlastnej TA), nie podľa počtu cyklov.\n"
        )

    # 2026-08-19 (na ziadost pouzivatela, po HYPE zacykleni) - analogicke k
    # streak_block vyssie, ale pre INY pripad: opakovane splnena watch_price/
    # watch_direction podmienka (direction='none' pri kazdom z nich), bez toho,
    # aby sa Claude niekedy dozvedel, ze uz je to Nty raz za sebou - kazdy
    # mimoriadny cyklus vyzeral ako cerstvy, izolovany pohlad, takze sa
    # opakovane nastavila nova tesna watch uroven, ktoru bezny pohyb hned
    # prekonal. Viz trade_cycle._get_watch_retrigger_streak.
    watch_retrigger_block = ""
    if watch_retrigger_streak:
        wrs = watch_retrigger_streak
        entries_desc = "\n".join(
            f"  {i + 1}. watch {e['watch_direction']} {e['watch_price']} (nastavené pri cene {e['live_price']}) "
            f"→ vtedy si zvolil direction='{e['direction']}', confidence={e['confidence']}"
            for i, e in enumerate(wrs["entries"])
        )
        watch_retrigger_block = (
            f"\n## Opakovane spustenie watch podmienky bez otvorenia pozície (posledných {wrs['count']} "
            f"mimoriadnych cyklov za sebou)\n"
            f"Toto je už {wrs['count']}. mimoriadny cyklus za sebou vyvolaný splnením watch_price/"
            f"watch_direction podmienky, ktorú si SÁM nastavil v predchádzajúcom cykle - a ani raz si "
            f"zatiaľ neotvoril pozíciu:\n{entries_desc}\n"
            f"To znamená, že tvoje doterajšie watch úrovne boli buď príliš blízko aktuálnej ceny (bežný "
            f"pokračujúci pohyb/šum ich prekonal skôr, než reálne potvrdili niečo NOVÉ), alebo že aj "
            f"napriek opakovanému potvrdeniu smeru zostávaš nerozhodný. Toto kolo sa rozhodni inak než "
            f"doteraz:\n"
            f"- Ak sa cena od PRVÉHO watch triggeru vyššie skutočne posunula v smere, ktorý si sledoval, a "
            f"tvoja analýza to podporuje, ZVÁŽ SKUTOČNÉ OTVORENIE POZÍCIE teraz namiesto ďalšieho čakania - "
            f"opakované čakanie na 'ešte jedno potvrdenie' pri už potvrdenom pohybe je presne ten vzor, "
            f"ktorý viedol k tomuto zacykleniu.\n"
            f"- Ak stále nie si dostatočne istý, NENASTAVUJ novú watch úroveň blízko aktuálnej ceny - buď ju "
            f"úplne vynechaj (počkaj na bežný interval), alebo ju nastav výrazne ďalej (minimálne 2-3x ATR "
            f"od aktuálnej ceny), aby ju nespustil ten istý bežný pohyb/šum znova o pár minút.\n"
        )
        if wrs["count"] >= 3:
            watch_retrigger_block += (
                "POZOR: ak aj teraz nastavíš novú watch úroveň, systém ju MECHANICKY zmaže (dosiahnutý "
                "limit opakovaní) - tvoje direction/confidence/SL/TP rozhodnutie sa aj tak uloží, len "
                "watch_price/watch_direction sa tento cyklus nepoužije.\n"
            )

    # 2026-08-21 (na ziadost pouzivatela, po ZEC 09:33->09:34 rozpore: "zlý "
    # risk/reward, nechasoval by som" -> o minútu LONG bez zmienky o zmene
    # názoru) - na rozdiel od watch_retrigger_block vyssie (ktory vyzaduje
    # STREAK >=1 PREDCHADZAJUCICH watch-triggered cyklov) tento blok sa
    # zobrazi VZDY, ked je TENTO beh watch-triggered, bez ohladu na to, ci
    # cyklus, ktory watch nastavil, bol sam watch-triggered (post-close review
    # nikdy nie je - viz trade_cycle._get_watch_set_context vs
    # _get_watch_retrigger_streak). Ukazuje VLASTNE zdovodnenie cakania
    # (watch_rationale), nie len cisla ako watch_retrigger_block.
    watch_set_context_block = ""
    if watch_set_context:
        wsc = watch_set_context
        elapsed_min = None
        if wsc.get("created_at"):
            elapsed_min = round((datetime.now(timezone.utc) - wsc["created_at"]).total_seconds() / 60)
        rationale_line = (
            f"s odôvodnením čakania: \"{wsc['watch_rationale']}\""
            if wsc.get("watch_rationale") else "(bez zaznamenaného odôvodnenia)"
        )
        watch_set_context_block = (
            f"\n## Toto rozhodnutie bolo vyvolané TVOJOU VLASTNOU watch podmienkou"
            f"{f' (pred {elapsed_min} min)' if elapsed_min is not None else ''}\n"
            f"V predchádzajúcom cykle si pri cene {wsc.get('live_price')} zvolil "
            f"direction='{wsc.get('direction')}' (confidence={wsc.get('confidence')}) a nastavil watch "
            f"{wsc.get('watch_direction')} {wsc.get('watch_price')} {rationale_line}.\n"
            f"Cena teraz túto úroveň dosiahla/prekročila. Ak TERAZ voliš iný smer/confidence než vtedy, "
            f"v reasoningu VÝSLOVNE napíš, čo konkrétne sa oproti tomuto dôvodu čakania zmenilo (nová "
            f"cenová akcia, potvrdenie/vyvrátenie signálu a pod.) - nezopakuj len novú analýzu bez "
            f"odkazu na predchádzajúce rozhodnutie.\n"
        )

    marketaux_block = ""
    if marketaux_news:
        # 2026-08-19 (na ziadost pouzivatela) - predtym LEN titulok (Claude
        # nevidel, o com clanok skutocne je) - Marketaux uz aj tak posiela
        # snippet v tej istej odpovedi (ziadny extra request/naklad), teraz
        # sa aj skutocne posiela do promptu. Vek clanku ("pred Xh") sa uz
        # pocita v marketaux_client.py (freshness filter) - explicitne sa
        # vypisuje AJ tu (nie len surovy ISO timestamp), aby Claude nemusel
        # sam pocitat rozdiel voci aktualnemu datumu - riziko, ze si niekedy
        # nevsimne a X-hodinovu udalost bude povazovat za cerstvu aktualitu.
        def _article_line(a):
            age = a.get("age_hours")
            age_label = f"pred {age:.0f}h" if age is not None else (a.get("published_at") or "?")
            line = (f"- [{age_label}] {a.get('title')} "
                    f"(zdroj: {a.get('source')}, sentiment: {a.get('sentiment_score')})")
            snippet = a.get("snippet")
            if snippet:
                line += f"\n  {snippet}"
            return line
        articles = "\n".join(_article_line(a) for a in marketaux_news)
        marketaux_block = (
            f"\n## Najnovšie financne spravy so sentiment skore (Marketaux, NIE web_search)\n"
            f"{articles}\n"
            f"(sentiment skore je -1 az +1 na urovni konkretneho clanku, priamo od Marketaux, "
            f"nie tvoj vlastny odhad; text pod kazdym titulkom je kratky vytah z clanku, nie plny text; "
            f"vsetky clanky su uz vopred filtrovane na mladsie nez {config.MARKETAUX_MAX_ARTICLE_AGE_HOURS:.0f}h)\n"
        )

    # CoinMarketCal (2026-08-19, na ziadost pouzivatela) - strukturovany zdroj
    # nadchadzajucich krypto-projektovych udalosti (burzove listingy,
    # hlasovania, protokolove upgrady, token unlocky), doplnajuci existujuci
    # Event Risk Gate (ktory doteraz spolieha VYHRADNE na Claude-ov vlastny
    # web_search) - viz coinmarketcal_client.py. Len pre ADA/ZEC/HYPE/NIGHT
    # (jedine nase krypto tickery, ktore su na CoinMarketCal Free plane
    # pokryte - viz assets.py coinmarketcal_slug).
    coinmarketcal_block = ""
    if coinmarketcal_events:
        events_txt = "\n".join(
            f"- {e['title']} ({e['date_start'].strftime('%d %b')}"
            + (f" → {e['date_end'].strftime('%d %b')}" if e.get("date_end") else "")
            + (", odhadovaný dátum" if e.get("is_estimated") else "")
            + ")"
            for e in coinmarketcal_events
        )
        coinmarketcal_block = (
            f"\n## Nadchádzajúce projektové udalosti {instrument} (CoinMarketCal, štruktúrovaný "
            f"zdroj, NIE web_search)\n"
            f"{events_txt}\n"
            f"(overené udalosti priamo z API - zohľadni ich pri Event Risk Gate úvahe nižšie "
            f"popri/namiesto vlastného web_search)\n"
        )

    header = f"""## Aktuálny dátum a čas
{now.strftime('%A, %d. %B %Y, %H:%M')} UTC ({now.isoformat()})
Tento cyklus beží každých {interval_h}h - zaujímajú ťa hlavne udalosti/správy za posledných
~{interval_h} hodín, staršie ber len ako pozadový kontext (nie ako novú informáciu).

## Technická analýza {instrument}
{json.dumps(ta, indent=2, ensure_ascii=False)}

## Cross-market kontext (S&P500, Russell 2000, SOX, VIX, DXY, US10Y/US13W výnosy, ropa, zlato)
{json.dumps(cross_market, indent=2, ensure_ascii=False)}
{fred_block}{eia_block}
## Session alignment (Ázia -> Európa -> US futures)
{json.dumps(session, indent=2, ensure_ascii=False)}
{btc_block}
## Social media sentiment
{social_block}
{marketaux_block}{coinmarketcal_block}

## Kľúčové predpoklady z predchádzajúceho cyklu (~{interval_h}h dozadu)
{prev_block}
{streak_block}
{watch_retrigger_block}
{watch_set_context_block}
{retro_block}"""

    macro_event_block = ""
    if macro_event:
        macro_event_block = f"""## Práve zverejnená makro udalosť: {macro_event}
Toto je mimoriadny cyklus spustený HNEĎ po plánovanom čase zverejnenia {macro_event} (nie bežný
interval). Ako PRVÝ krok cez web_search over presné aktuálne číslo/výsledok a ako naň trh
zareagoval - dotaz MUSÍ obsahovať "{macro_event}" a dnešný dátum. Až potom pokračuj bežným
vyhodnotením.

"""

    if open_position:
        op = open_position
        direction_label = "LONG" if (op["direction"] or "").lower() == "long" else "SHORT"
        sign = "+" if op["unrealized_pnl_usd"] >= 0 else ""
        position_block = f"""## OTVORENÁ POZÍCIA (toto NIE JE rozhodnutie o novom obchode - hodnotíš EXISTUJÚCU pozíciu)
Smer: {direction_label} | Vstup: {op['entry_price']} | Aktuálna cena: {op['live_price']}
Stop-loss: {op['stop_loss_price']} | Take-profit: {op['take_profit_price']} | Leverage: {op['leverage']}x
Otvorená: {op['opened_at_str']} ({op['hours_held']:.1f}h dozadu)
Nerealizované PnL: {sign}${op['unrealized_pnl_usd']:.2f} ({sign}{op['unrealized_pnl_pct']:.2f}% z marže)

Zhodnoť, či pôvodné kľúčové predpoklady (vyššie) stále platia, alebo sa niečo podstatné zmenilo -
over si to cez web_search rovnako ako pri bežnom cykle (dotaz cielený na konkrétnu tému z
predpokladov, nie len na cenu nástroja). Na základe toho posúď, či očakávaš, že sa cena bude naďalej
vyvíjať V PROSPECH tejto pozície alebo PROTI nej, a či by mal používateľ zvážiť jej manuálne
zatvorenie. SL/TP na burze zostávajú bez zmeny bez ohľadu na tvoju odpoveď - zatvorenie NEVYKONÁVAŠ
TY, len odporúčaš človeku, ktorý sa rozhodne sám."""
        return f"{header}\n{macro_event_block}{position_block}\n"

    closed_trade_block = ""
    if closed_trade:
        ct = closed_trade
        sign = "+" if ct["pnl_usd"] >= 0 else ""
        reason_label = _CLOSE_REASON_PROMPT_LABELS.get(ct["close_reason"], ct["close_reason"])
        # 2026-08-18 (na ziadost pouzivatela) - SL/likvidacia TERAZ TIEZ spustaju
        # tento mimoriadny cyklus (viz position_monitor._EVALUATION_ONLY_CLOSE_REASONS),
        # ale VYHRADNE na vyhodnotenie - trade_cycle.run_cycle_for_asset
        # STRUKTURALNE zahodi direction/confidence z tohto behu bez ohladu na
        # to, co Claude navrhne (ziadne otvorenie pozicie z tohto volania).
        # Claude to musi vediet VOPRED (nie len fakt, ze sa to potom zahodi) -
        # inak by mohol citit tlak "musim navrhnut dalsi obchod hned teraz",
        # co je presne ten revenge-trading impulz, ktoremu sa chceme vyhnut.
        if ct.get("evaluation_only"):
            action_note = (
                "Toto je mimoriadny cyklus spustený HNEĎ po zatvorení tejto pozície na SL/likvidáciou "
                "(nie bežný interval). Cez closed_trade_reflection zhodnoť, či bol vstup/SL nastavený "
                "primerane, alebo či niečo (vstup pri prehriatom RSI, chase breakoutu a pod.) vopred "
                "naznačovalo zvýšené riziko rýchleho zvratu. DÔLEŽITÉ: tvoje direction/confidence "
                "rozhodnutie nižšie sa v TOMTO behu NEVYKONÁ - žiadna nová pozícia sa z neho priamo "
                "neotvorí, aj keby confidence prešla prahom. Je to zámerné (aby okamžitý re-entry po "
                "stop-oute nebol poznačený túžbou 'dohnať stratu') - bot môže znova vstúpiť pri "
                "najbližšom bežnom cykle na základe čerstvej analýzy. Nástroj polia "
                "direction/confidence/SL/TP aj tak vyžaduje, tak ich vyplň ako svoj aktuálny názor - "
                "berie sa len ako záznam, nie príkaz.\n\n"
                "VÝNIMKA - rýchly re-entry PODMIENENÝ potvrdením cenou: ak ide o prudký pohyb "
                "(crash/run-up) a očakávaš, že sa trh RÝCHLO pohne ďalej smerom, ktorý by opodstatnil "
                "skorší re-entry než bežný interval, použi pole watch_price/watch_direction nižšie "
                "(prípad (3) v jeho popise) - nastav cenovú úroveň, ktorej REÁLNE prekročenie by tento "
                "predpoklad potvrdilo. Systém ju bude kontrolovať každých pár sekúnd počas najbližších "
                "minút, a ak sa splní, spustí sa ČERSTVÝ plný cyklus (s aktuálnymi dátami, nie len "
                "touto úvahou) - to je JEDINÝ spôsob, ako môže tento post-SL cyklus reálne viesť k "
                "novej pozícii skôr než bežný interval. Použi to len ak je to naozaj opodstatnené, nie "
                "mechanicky pri každom SL zatvorení."
            )
        else:
            action_note = (
                "Toto je mimoriadny cyklus spustený HNEĎ po zatvorení tejto pozície (nie bežný interval). "
                "Najprv cez closed_trade_reflection zhodnoť, či bolo zatvorenie správne timeované. Potom "
                "NEZÁVISLE posúď AKTUÁLNU trhovú situáciu (rovnako ako pri bežnom cykle) a rozhodni, či "
                "teraz otvoriť novú pozíciu - pokračujúcu v rovnakom smere (ak trend drží) alebo opačnú "
                "(ak sa obraz otočil), alebo počkať (none)."
            )
        # 2026-08-19 (na ziadost pouzivatela) - SL/TP+kalibracia vyhodnotenie
        # TEJTO konkretnej pozicie, nezavisle od typu zatvorenia (na rozdiel od
        # action_note vyssie, ktory sa lisi SL/likvidacia vs ostatne). Data
        # pripravene v position_monitor._build_review_context - ak chybaju
        # (napr. ticker este nema dost obchodov na kalibraciu), tento blok sa
        # jednoducho vynecha, closed_trade_reflection funguje aj bez neho.
        sltp_eval_block = ""
        if ct.get("sl_pct_chosen") is not None:
            lines = [
                "## Vyhodnotenie SL/TP tejto pozície",
                f"Zvolené SL/TP tejto pozície: SL {ct['sl_pct_chosen']:.3f}% / TP {ct['tp_pct_chosen']:.3f}%",
            ]
            if ct.get("default_sl_pct") is not None:
                lines.append(
                    f"(aktuálny default pre {instrument}: SL {ct['default_sl_pct']:.3f}% / "
                    f"TP {ct['default_tp_pct']:.3f}%)"
                )

            history = ct.get("history") or []
            if history:
                lines.append(f"\nPosledných {len(history)} predošlých uzavretých obchodov {instrument} "
                              f"(najnovší prvý):")
                for h in history:
                    hsign = "+" if (h["pnl_usd"] or 0) >= 0 else ""
                    sl_str = f"{h['sl_pct']:.2f}%" if h.get("sl_pct") is not None else "?"
                    tp_str = f"{h['tp_pct']:.2f}%" if h.get("tp_pct") is not None else "?"
                    lines.append(f"- {hsign}${h['pnl_usd']:.2f} ({h['close_reason']}), SL/TP {sl_str}/{tp_str}")

            candidates = ct.get("calibration_candidates") or []
            if candidates:
                lines.append(
                    f"\nTOP-{len(candidates)} kandidáti z priebežného grid-search rebríčka "
                    f"(z VLASTNÝCH obchodov {instrument}, n={candidates[0]['trade_count']} - viz "
                    f"tab \"Kalibrácia SL/TP\" v dashboarde):"
                )
                for c in candidates:
                    atr_sl = f"{c['atr_sl_pct']:.3f}%" if c.get("atr_sl_pct") is not None else "?"
                    atr_tp = f"{c['atr_tp_pct']:.3f}%" if c.get("atr_tp_pct") is not None else "?"
                    line = (
                        f"- #{c['rank']}: ATR-kalibrované SL {atr_sl}/TP {atr_tp} -> backtest PnL "
                        f"${c['total_pnl']:.2f}, win rate {c['win_rate']*100:.0f}%"
                    )
                    if c.get("sr_sl_pct") is not None:
                        sr_pnl_str = "N/A" if c.get("sr_total_pnl") is None else f"${c['sr_total_pnl']:.2f}"
                        sr_wr_str = "N/A" if c.get("sr_win_rate") is None else f"{c['sr_win_rate']*100:.0f}%"
                        line += (
                            f"; S/R-prichytené SL {c['sr_sl_pct']:.3f}%/TP {c['sr_tp_pct']:.3f}% -> "
                            f"PnL {sr_pnl_str}, win rate {sr_wr_str}"
                        )
                    lines.append(line)
                lines.append(
                    "\nNa základe všetkého vyššie (vlastné SL/TP tejto pozície vs. default, história "
                    "tickera, kalibrační kandidáti aj S/R kontext) vyplň sl_tp_calibration_verdict - "
                    "zauji výslovné stanovisko, či bolo zvolené SL/TP správne, či mal byť použitý "
                    "niektorý z uvedených kandidátov, alebo by si zvolil úplne inú hodnotu s vlastným "
                    "TECHNICKÝM zdôvodnením (nie len odkazom na to, čo vyšlo lepšie v backteste)."
                )
            sltp_eval_block = "\n" + "\n".join(lines) + "\n"

        closed_trade_block = f"""## Práve zatvorená pozícia (dôvod: {reason_label})
Smer: {(ct['direction'] or '').upper()} | Vstup: {ct['entry_price']} | Výstup: {ct['exit_price']}
Držaná: {ct['hours_held']:.1f}h | PnL: {sign}${ct['pnl_usd']:.2f}

{action_note}
{sltp_eval_block}
"""

    threshold_low = asset["min_confidence"] - config.WATCH_CONFIDENCE_MARGIN
    threshold_high = asset["min_confidence"] - 1
    threshold_block = f"""
## Prah na otvorenie pozície
Minimálna confidence na otvorenie pozície pre {instrument} je aktuálne {asset['min_confidence']}.
Ak tento cyklus zvolíš direction=long alebo short a tvoja confidence vyjde v rozmedzí
{threshold_low:.0f}-{threshold_high:.0f} (tesne pod prahom), VŽDY vyplň confidence_threshold_note
(presné pravidlo, čo tam napísať, je v system prompte).
"""

    return f"""{header}
{macro_event_block}{closed_trade_block}## Cielove SL/TP vzdialenosti
Stop-loss cca {asset['sl_pct']}% od aktuálnej ceny, take-profit cca {asset['tp_pct']}%
(pri LONG: stop_loss_price = last_price * (1 - {asset['sl_pct']}/100), take_profit_price =
last_price * (1 + {asset['tp_pct']}/100); pri SHORT opačne). Môžeš sa mierne odchýliť podľa
ATR/kontextu, ale nie výrazne mimo tento rozsah.
{threshold_block}
Ak je to relevantné, over si cez web_search aktuálne správy k {instrument}/súvisiacim témam a
nadchádzajúce makro eventy (CPI/FOMC/NFP/earnings) za posledných ~{interval_h}h / najbližších 24h -
nezabudni do query zahrnúť aktuálny dátum. Potom vyhodnoť situáciu a vráť rozhodnutie podľa
formátu zo system promptu.
"""


def analyze(asset: dict, ta: dict, cross_market: dict, session: dict, social: list[dict],
            btc_proxy: dict | None = None,
            prev_assumptions: str | None = None,
            prev_cycle_time: datetime | None = None,
            retrospective_reflection: str | None = None,
            new_stats_text: str | None = None,
            fred_macro: dict | None = None,
            eia_data: dict | None = None,
            marketaux_news: list[dict] | None = None,
            confidence_streak: dict | None = None,
            closed_trade: dict | None = None,
            macro_event: str | None = None,
            coinmarketcal_events: list[dict] | None = None,
            watch_retrigger_streak: dict | None = None,
            watch_set_context: dict | None = None) -> tuple[dict, list[dict], dict]:
    """Vrati (decision, web_search_log, usage). web_search_log je zoznam
    {"query": str, "sources": [{"title", "url", "page_age"}]} pre kazde
    vyhladavanie, ktore Claude spravil - sluzi na audit (co realne citas,
    aby sa dalo neskor rozhodnut o whitelist/blacklist domen).

    asset: profil z assets.py (name/asset_class/sl_pct/tp_pct/... - urcuje system
    prompt aj cielove SL/TP % v user prompte).
    prev_assumptions: kluc_assumptions z minuleho cyklu TOHTO assetu (ak existuje) -
    Claude ho dostane na explicitne overenie, ci este plati.
    prev_cycle_time: kedy prev_assumptions vznikli - umoznuje formulovat hladanie
    ako presny inkrement ("co pribudlo OD X"), nie vagne "za poslednych ~4h".
    retrospective_reflection: aktualne RollingRetrospective.summary pre tento asset
    (priebezne aktualizovane zhrnutie, NIE len posledny den) - prenasa sa do vsetkych
    cyklov, kym ho Claude neaktualizuje pri dalsom prvom cykle dna.
    new_stats_text: ak toto je prvy cyklus po polnoci a vcerajsok este nebol
    zapracovany do summary, sem sa vlozi cerstvo spocitany text (viz retrospective.py)
    - Claude ma za ulohu na jeho zaklade vygenerovat daily_reflection (izolovany
    zaznam) AJ summary_reflection (aktualizovane zhrnutie), ktore trade_cycle.py
    nasledne ulozi (prve do DailyRetrospective, druhe do RollingRetrospective)."""
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY nie je nastavený")

    system_blocks = _system_prompt_blocks(asset)
    user_prompt = _build_user_prompt(asset, ta, cross_market, session, social,
                                      btc_proxy, prev_assumptions, prev_cycle_time,
                                      retrospective_reflection, new_stats_text,
                                      fred_macro, eia_data, marketaux_news,
                                      confidence_streak, watch_retrigger_streak, watch_set_context,
                                      open_position=None,
                                      closed_trade=closed_trade, macro_event=macro_event,
                                      coinmarketcal_events=coinmarketcal_events)
    decision, web_search_log, usage = _call_claude(asset, system_blocks, user_prompt,
                                                     DECISION_TOOL, "submit_trade_decision")
    _validate_decision(decision)
    return decision, web_search_log, usage


def analyze_position_health(asset: dict, open_position: dict, ta: dict, cross_market: dict,
                             session: dict, social: list[dict],
                             btc_proxy: dict | None = None,
                             prev_assumptions: str | None = None,
                             prev_cycle_time: datetime | None = None,
                             retrospective_reflection: str | None = None,
                             fred_macro: dict | None = None,
                             eia_data: dict | None = None,
                             marketaux_news: list[dict] | None = None,
                             macro_event: str | None = None,
                             new_stats_text: str | None = None,
                             coinmarketcal_events: list[dict] | None = None) -> tuple[dict, list[dict], dict]:
    """Ako analyze(), ale pre UZ OTVORENU poziciu (viz
    trade_cycle._run_position_health_check) - namiesto rozhodnutia o novom
    obchode (direction/SL/TP) sa Claude vyjadri, ci povodne predpoklady este
    platia a ci by mal pouzivatel zvazit rucne zatvorenie (submit_position_health_check,
    nie submit_trade_decision). open_position: dict s direction/entry_price/
    live_price/stop_loss_price/take_profit_price/leverage/opened_at_str/
    hours_held/unrealized_pnl_usd/unrealized_pnl_pct - viz volajuci.
    new_stats_text: ako v analyze() - ak je vcerajsok (UTC) este nespracovany,
    trade_cycle.py to sem vlozi aj ked je pozicia otvorena (viz 2026-08-17 -
    predtym sa retrospektiva pri otvorenej pozicii nikdy negenerovala)."""
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY nie je nastavený")

    system_blocks = _system_prompt_blocks(asset)
    user_prompt = _build_user_prompt(asset, ta, cross_market, session, social,
                                      btc_proxy, prev_assumptions, prev_cycle_time,
                                      retrospective_reflection, new_stats_text,
                                      fred_macro, eia_data, marketaux_news,
                                      confidence_streak=None, open_position=open_position,
                                      macro_event=macro_event, coinmarketcal_events=coinmarketcal_events)
    decision, web_search_log, usage = _call_claude(asset, system_blocks, user_prompt,
                                                     POSITION_HEALTH_TOOL, "submit_position_health_check")
    _validate_health_decision(decision)
    return decision, web_search_log, usage


# 2026-08-21 (na ziadost pouzivatela, po NAS100 SL incidente) - realny produkcny
# nalez: v ojedinelom cykle Claude namiesto cisteho tool_use JSON vratil
# reasoning pole obsahujuce VLASTNY dalsi text vo formate
# "...prozny text.</reasoning>\n<parameter name=\"key_assumptions\">...</parameter>
# ...\n<parameter name=\"close_confidence\">50" - key_assumptions aj
# close_confidence tak v DB ostali NULL, hoci Claude ich hodnotu realne
# vygeneroval (len nespravne, ako sucast reasoning stringu namiesto vlastnych
# JSON poli). Toto je KRITICKE presne pre close_confidence, kedze naprogramovane
# rozhodnutie automaticky zatvorit poziciu (viz trade_cycle._run_position_health_check)
# stoji cele na tomto cisle - fail-safe by ho bez tejto opravy proste preskocil
# (chybajuce pole nikdy nespusti akciu), ale radsej sa pokusime hodnotu naozaj
# zachranit, aby fail-safe nebol jedinou ochranou.
# Uzatvaracia znacka nasledujuca po hodnote nie je konzistentna - v realnom
# zachytenom pripade to bolo </key_assumptions> (podla nazvu pola), nie
# univerzalne </parameter> - preto lookahead prijme HOCIJAKU uzatvaraciu
# znacku, dalsi <parameter, alebo koniec textu.
_MALFORMED_FIELD_RE = re.compile(
    r'<parameter name="(\w+)">(.*?)(?=(?:</\w+>)|(?:<parameter name=")|$)',
    re.DOTALL,
)
_TRAILING_TAG_RE = re.compile(r"</\w+>\s*$")


def _recover_malformed_fields(decision: dict, asset_name: str) -> dict:
    """Ak niektore z ocakavanych textovych poli (reasoning) obsahuje stopy
    poskodenej tool-call odpovede (viz komentar vyssie), skusi z neho
    dodatocne vytiahnut key_assumptions/close_confidence (LEN ak uz nie su
    v decision inak vyplnene - nikdy neprepisuje spravne prisle pole).
    Nema vplyv na normalne (nepoškodene) odpovede - tie ziadny <parameter
    znacku neobsahuju, regex nenajde zhodu, decision sa vrati bezo zmeny."""
    reasoning = decision.get("reasoning")
    if not reasoning or "<parameter name=" not in reasoning:
        return decision

    print(f"[claude_analyst] [{asset_name}] POZOR: reasoning obsahuje stopy poskodenej "
          "tool-call odpovede (viz _recover_malformed_fields) - skusam zachranit polia.")
    clean_reasoning = reasoning.split("<parameter name=")[0].split("</reasoning>")[0].strip()
    if clean_reasoning:
        decision["reasoning"] = clean_reasoning

    for name, value in _MALFORMED_FIELD_RE.findall(reasoning):
        value = _TRAILING_TAG_RE.sub("", value).strip()
        if decision.get(name):
            continue  # spravne prislo pole sa nikdy neprepisuje
        if name == "close_confidence":
            try:
                decision[name] = int(value)
            except ValueError:
                pass
        elif value:
            decision[name] = value
    return decision


def _call_claude(asset: dict, system_blocks: list[dict], user_prompt: str,
                  tool: dict, tool_name: str) -> tuple[dict, list[dict], dict]:
    """Spolocna request/retry/pause_turn loop pre analyze() aj analyze_position_health()
    - lisia sa len v tom, ktory nastroj (DECISION_TOOL vs POSITION_HEALTH_TOOL) Claude
    dostane a ako znie user prompt (viz volajuci). Vracia (decision, web_search_log, usage) -
    usage je súčet tokenov cez VŠETKY volania v tomto cykle (aj pri pause_turn
    pokračovaní nižšie), na trvalé uloženie do CycleLog (viz db.py, 2026-08-15)."""
    # cache_control na systemovom prompte aj user sprave: ak Claude narazi na
    # pause_turn (casto sa stava pri viacerych web_search volaniach), musime
    # poslat celu doterajsiu konverzaciu znova - bez cachovania by sa system
    # prompt + user sprava platili nanovo na plnu cenu pri kazdom pokracovani.
    # system_blocks samotne maju VLASTNY ttl=1h cache_control (viz
    # _system_prompt_blocks) - ten zdielany blok tak zostava teply naprieč
    # vsetkymi 6 tickermi (ADA/NIGHT bezia vzdy kazdu hodinu).
    messages = [{"role": "user",
                 "content": [{"type": "text", "text": user_prompt,
                               "cache_control": {"type": "ephemeral"}}]}]
    web_search_log: list[dict] = []
    total_usage = {"input_tokens": 0, "cache_creation_input_tokens": 0,
                   "cache_read_input_tokens": 0, "output_tokens": 0}

    # Volitelny per-asset effort test (viz config.ADA_EFFORT/assets.py) - "" (default)
    # = output_config sa neposle vobec (API default "high"). Pri xhigh/max sa thinking
    # rozpocet moze vyrazne zvacsit (Anthropic odporuca max_tokens >= 64000), povodnych
    # 8192 by pri hlbsom uvazovani mohlo orezat odpoved este PRED tool-use blokom
    # (submit_trade_decision by sa vobec nezavolal) - preto pri xhigh/max zvysujeme strop.
    effort = asset.get("effort")
    max_tokens = 8192
    if effort in ("xhigh", "max"):
        max_tokens = 24000

    # server-side web_search moze pri velmi dlhom hladani vratit stop_reason=pause_turn -
    # v takom pripade treba poslat konverzaciu znova a nechat ju dokoncit (max 1 pokracovanie).
    for _ in range(2):
        payload = {
            "model": config.CLAUDE_MODEL,
            "max_tokens": max_tokens,
            "system": system_blocks,
            "tools": [
                {"type": "web_search_20260209", "name": "web_search", "max_uses": 7},
                tool,
            ],
            "messages": messages,
        }
        if effort:
            payload["output_config"] = {"effort": effort}

        for attempt in range(_MAX_API_RETRIES + 1):
            # 2026-08-20 produkcny nalez (ADA post-close review na TP zatvoreni
            # #57 - Read timed out, ZIADNY retry, reflexia navzdy stratena):
            # requests.post() mimo try/except znamenalo, ze retry loop nizsie
            # (na retryable STATUS KOD) sa nikdy nedostal ku slovu, ak spojenie
            # zlyhalo/vyprsalo skor, nez prislo VOBEC nejake HTTP telo - vynimka
            # (ReadTimeout/ConnectionError) prebublala rovno von. ADA bezi na
            # effort=xhigh (extended thinking + web_search), co obcas genuinne
            # potrebuje viac nez povodnych 300s. Preto: (1) timeout zvyseny na
            # _REQUEST_TIMEOUT_SECONDS (viac priestoru pre genuinne pomalu
            # odpoved), (2) network-level vynimka teraz TIEZ prechadza rovnakym
            # retry mechanizmom ako retryable status kody (rovnaky pocet
            # pokusov/pauza, ziadny novy tuning parameter).
            try:
                resp = requests.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": config.ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json=payload,
                    timeout=_REQUEST_TIMEOUT_SECONDS,
                )
            except requests.exceptions.RequestException as e:
                if attempt < _MAX_API_RETRIES:
                    print(f"[claude_analyst] [{asset['name']}] POST /v1/messages zlyhalo "
                          f"({e.__class__.__name__}: {e}) - skusam znova o "
                          f"{_API_RETRY_DELAY_SECONDS}s ({attempt + 1}/{_MAX_API_RETRIES})...")
                    time.sleep(_API_RETRY_DELAY_SECONDS)
                    continue
                raise
            if resp.status_code in _RETRYABLE_STATUS and attempt < _MAX_API_RETRIES:
                print(f"[claude_analyst] [{asset['name']}] POST /v1/messages -> {resp.status_code} "
                      f"(prechodna chyba), skusam znova o {_API_RETRY_DELAY_SECONDS}s "
                      f"({attempt + 1}/{_MAX_API_RETRIES})...")
                time.sleep(_API_RETRY_DELAY_SECONDS)
                continue
            break
        resp.raise_for_status()
        data = resp.json()
        content_blocks = data.get("content", [])
        web_search_log.extend(_extract_web_search_log(content_blocks))
        usage = data.get("usage", {})
        for key in total_usage:
            total_usage[key] += usage.get(key) or 0
        print(f"[claude_analyst] [{asset['name']}] usage: input={usage.get('input_tokens')} "
              f"cache_write={usage.get('cache_creation_input_tokens')} "
              f"cache_read={usage.get('cache_read_input_tokens')} output={usage.get('output_tokens')} "
              f"effort={effort or 'default'} stop_reason={data.get('stop_reason')}")

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
             if b.get("type") == "tool_use" and b.get("name") == tool_name),
            None,
        )
        if decision_block is None:
            raise RuntimeError(
                f"Claude nezavolal {tool_name} (stop_reason={data.get('stop_reason')}, "
                f"content_types={[b.get('type') for b in content_blocks]})"
            )
        usage_record = {
            "input_tokens": total_usage["input_tokens"],
            "cache_write_tokens": total_usage["cache_creation_input_tokens"],
            "cache_read_tokens": total_usage["cache_read_input_tokens"],
            "output_tokens": total_usage["output_tokens"],
            "effort": effort or None,
        }
        return _recover_malformed_fields(decision_block["input"], asset["name"]), web_search_log, usage_record

    raise RuntimeError("Claude neposkytol finalnu odpoved po pause_turn pokracovani")


def _extract_web_search_log(content_blocks: list) -> list[dict]:
    """Sparuje kazde web_search volanie (server_tool_use) s jeho vysledkami
    (web_search_tool_result), aby sme vedeli presne, ake query a ake zdroje
    (title/url/page_age) Claude pouzil. Obsah stranok samotny nevidime -
    Strike/Anthropic ho posiela sifrovany (encrypted_content), citame len metadata.

    Ak vyhladavanie ZLYHA (napr. rate limit na strane Anthropic), "content" nie
    je zoznam vysledkov ale dict/chyba - predtym sme to tichy zapisali ako
    prazdny zaznam bez naznaku PRECO (presne to sposobilo neistotu pri NAS100
    cykle 2026-07-24 11:49, kde vsetkych 5 pokusov skoncilo bez zdrojov). Teraz
    zapiseme aj "error" pole a vypiseme to do konzoly, aby sa to dalo diagnostikovat."""
    log = []
    pending_query = None
    pending_raw_input = None
    for block in content_blocks:
        if block.get("type") == "server_tool_use" and block.get("name") == "web_search":
            pending_raw_input = block.get("input", {})
            pending_query = pending_raw_input.get("query")
        elif block.get("type") == "web_search_tool_result":
            content = block.get("content")
            entry = {"query": pending_query}
            if isinstance(content, list):
                entry["sources"] = [
                    {"title": r.get("title"), "url": r.get("url"), "page_age": r.get("page_age")}
                    for r in content if r.get("type") == "web_search_result"
                ]
            else:
                entry["sources"] = []
                entry["error"] = (
                    content.get("error_code") if isinstance(content, dict)
                    else f"unexpected_content_shape:{type(content).__name__}"
                )
                # Surovy input celeho server_tool_use bloku (nie len "query") -
                # 2026-08-16 nalez: BTC cyklus mal 7x error_code=invalid_tool_input
                # so VSETKYMI query=None, co znamena, ze uz samotny input od Claudeho
                # bol nejakym sposobom nekompletny/zly - bez tohto surloveho zaznamu
                # sa nedalo zistit CO presne bolo v tom vstupe zle.
                entry["raw_input"] = pending_raw_input
                print(f"[claude_analyst] web_search zlyhalo: {entry['error']} "
                      f"(query={pending_query!r}, raw_input={pending_raw_input!r})")
            log.append(entry)
            pending_query = None
            pending_raw_input = None
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

    # watch_price/watch_direction su volitelne (direction="none", ALEBO
    # direction=long/short + confidence_threshold_note - viz DECISION_TOOL) -
    # ak ich model vratil, over aspon zakladny tvar, ale nechyb, ak chybaju
    # uplne (staré/nechcene cykly ich nemusia mat).
    watch_direction = decision.get("watch_direction")
    if watch_direction is not None and watch_direction not in ("above", "below"):
        raise ValueError(f"Neplatny watch_direction: {watch_direction!r}")
    watch_price = decision.get("watch_price")
    if watch_price is not None and not isinstance(watch_price, (int, float)):
        raise ValueError(f"Neplatny watch_price: {watch_price!r}")

    watch_direction_2 = decision.get("watch_direction_2")
    if watch_direction_2 is not None and watch_direction_2 not in ("above", "below"):
        raise ValueError(f"Neplatny watch_direction_2: {watch_direction_2!r}")
    watch_price_2 = decision.get("watch_price_2")
    if watch_price_2 is not None and not isinstance(watch_price_2, (int, float)):
        raise ValueError(f"Neplatny watch_price_2: {watch_price_2!r}")

    confidence_threshold_note = decision.get("confidence_threshold_note")
    if confidence_threshold_note is not None and not isinstance(confidence_threshold_note, str):
        raise ValueError(f"Neplatny confidence_threshold_note: {confidence_threshold_note!r}")


def _validate_health_decision(decision: dict) -> None:
    # key_assumptions VEDOME NIE JE tu required (na rozdiel od DECISION_TOOL) -
    # 2026-08 produkcny incident: Claude ho pri jednom position health cykle
    # vynechal, co predtym zahodilo CELY cyklus (aj recommendation/reasoning,
    # ktore inak vratil spravne) len kvoli jednemu chybajucemu doplnkovemu
    # polu. trade_cycle._run_position_health_check pri chybajucej hodnote
    # jednoducho ponecha predchadzajuce predpoklady bez zmeny.
    required = {"recommendation", "expected_direction", "reasoning"}
    missing = required - decision.keys()
    if missing:
        raise ValueError(f"Chýbajúce polia v position health rozhodnutí: {missing}")
    if decision["recommendation"] not in ("hold", "consider_closing"):
        raise ValueError(f"Neplatné recommendation: {decision['recommendation']}")
    if decision["expected_direction"] not in ("favorable", "unfavorable", "uncertain"):
        raise ValueError(f"Neplatný expected_direction: {decision['expected_direction']}")
