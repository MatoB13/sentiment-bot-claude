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
        "je to jediny sposob, ako rozhodnutie odovzdat. DOLEZITE: kazde pole "
        "(reasoning, key_assumptions, watch_price, watch_rationale, ...) odovzdaj "
        "VYHRADNE ako svoj vlastny samostatny kluc v tomto tool volani - nikdy "
        "nepis obsah dalsich poli ako text/XML znacky vnutri ineho pola (napr. "
        "vnutri reasoning)."
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
                "description": (
                    # 2026-08-29 (na ziadost pouzivatela, po zisteni ze confidence za CELU
                    # historiu 4268 cyklov NIKDY neprekrocilo 70 - povodny text "60 = mierne
                    # naklonený, 90+ vzacne" bol ukotvenie/anchoring, ktore stlacilo cely
                    # pouzivany rozsah do uzkeho pasma tesne nad prahom, bez realnej
                    # rozlisovacej sily) - vysvetluje KONCEPT kalibracie namiesto konkretnych
                    # cisel (akekolvek cislo tu napiseme riskuje stat sa novym kotevnym bodom).
                    # 2026-09-04 (holisticka kontrola) - povodne tu stalo "(NIE len
                    # 'prekracujem prah na otvorenie')". Cislo to neprezradilo, ale
                    # samotna veta priznavala, ze nejaka hranica existuje - presne to,
                    # co sa balikom A malo skryt. Posledny zvysok, ktory test
                    # test_prompt_no_threshold vtedy nezachytil (hladal ine formulacie).
                    "0-100 = kalibrovaná pravdepodobnosť, že tento smer/setup vyjde. "
                    "Kalibrované znamená: ak by si rovnaké "
                    "číslo priradil opakovane naprieč mnohými nezávislými rozhodnutiami, malo "
                    "by približne zodpovedať skutočnému podielu tých, čo naozaj vyjdú. Použi "
                    # 2026-09-04 - povodne tu stalo "nedrz sa umelo blizko prahu na
                    # otvorenie". Odkedy sa prah v prompte NEUVADZA (viz threshold_block
                    # v _build_user_prompt), by ta veta sama prezradila, ze nejaka
                    # hranica existuje - a tym by kotvu, ktoru sme prave odstranili,
                    # znova naznacila. Zvysok formulacie zostava nezmeneny.
                    #
                    # Pri direction=none sa ZAMERNE nic nedopĺňa: spravanie uz je take,
                    # ake ma byt (cim vyssia confidence, tym skor Claude nastavi watch -
                    # namerane 63 % v pasme 20-29 vs 91 % v pasme 40-49 na 4403 cykloch),
                    # takze opisovat to v prompte by nanajvys uskodilo.
                    "CELÝ rozsah 0-100 podľa reálnej presvedčivosti dôkazov - nevyhýbaj sa "
                    "vysokým ani nízkym hodnotám, ak si nimi skutočne istý."
                ),
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
                    "Volitelne (vzdy s watch_direction + watch_rationale). Cenova uroven, pri ktorej "
                    "ta lacny poller mimoriadne zavola znova - situaciu tam vyhodnotis od zaciatku, "
                    "tento navrh sa nevykona. Tri pripady: (1) direction=none a blokujuci dovod je "
                    "CENOVA uroven (retest/breakout) - NIE casova udalost (FOMC/CPI/earnings), tam "
                    "cenovy pohyb neistotu nevyriesi a zobudi ta len sum; (2) direction=long/short so "
                    "strednym presvedcenim - rovnaka cena ako v confidence_threshold_note; "
                    "(3) vyhodnotenie prave zatvorenej pozicie na SL/likvidaciu, ked cakas RYCHLE "
                    "pokracovanie - uroven, ktorej REALNE prekrocenie by opodstatnilo skory re-entry "
                    "(jediny sposob, ako z toho cyklu vznikne pozicia; nie mechanicky pri kazdom SL). "
                    "Inak pole vynechaj."
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
                    "VYPLŇ, ked direction=long alebo short, ale tvoje presvedcenie je len "
                    "STREDNE - setup vidis, nie je presvedcivy. Napis, PRI AKEJ CENE by tvoje "
                    "presvedcenie z CISTO TECHNICKEHO hladiska (potvrdeny breakout, uspesny retest, "
                    "prekonanie konkretnej urovne - NIKDY plynutim casu) vyrazne stuplo - a tu istu "
                    "cenu daj aj do watch_price/watch_direction, aby ju lacny poller sledoval a pri "
                    "splneni spustil mimoriadny cyklus (kde situaciu znova kompletne vyhodnotis od "
                    "zaciatku). Je UPLNE V PORIADKU napisat, ze taku cenu nevies odhadnut (blokujuci "
                    "dovod je udalost, nie uroven) - vtedy watch_price/watch_direction nechaj prazdne. "
                    "Pri silnom aj zjavne slabom presvedceni toto pole vynechaj."
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
                    "VCERAJSKU - do buducich promptov sa neprenasa, sluzi pouzivatelovi v "
                    "dashboarde. Strucne (2-4 vety) zhodnot dve veci: (1) ci "
                    "sedeli tvoje vcerajsie confidence cisla s vysledkami - vysli setupy s vyssim "
                    "cislom castejsie nez tie s nizsim? Ak nie, v com bol odhad systematicky vedla "
                    "(prilis isty pri chase vstupoch, prilis neisty pri potvrdenom trende...). "
                    "Hodnot LEN presnost svojich odhadov, nie to, ktore z nich sa vykonali - "
                    "o tom nerozhodujes; (2) ci "
                    "tvoje 'none' rozhodnutia boli opodstatnene, alebo ci si bol niekedy zbytocne "
                    "opatrny a v spatnom pohlade malo byt LONG/SHORT. Ak nemas take udaje k "
                    "dispozicii v tomto cykle, toto pole VYNECHAJ."
                ),
            },
            "closed_trade_reflection": {
                "type": "string",
                "description": (
                    "VYPLN LEN ak user sprava obsahuje sekciu 'Zatvorená pozícia' "
                    "(mimoriadny cyklus spustený po TP/timeout/manuálnom zatvorení, alebo po "
                    "SL/likvidácii - pozri v tej sekcii ČAS zatvorenia, môže byť aj staršie, nie vždy "
                    "'práve teraz'). 2-3 vety: bolo zatvorenie správne timeované, alebo mala pozícia "
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
        "SL/TP na burze zostávajú nezmenené TVOJÍM VOLANÍM SAMOTNÝM). Zvyčajne je to len opinion "
        "pre používateľa, ALE pri recommendation=consider_closing a dostatočne vysokom "
        "close_confidence systém pozíciu AUTOMATICKY zatvorí trhovým príkazom - ber to teda vážne, "
        "nie ako neutrálne logovanie. Zavolaj tento "
        "nástroj VŽDY ako posledný krok, po dokončení prípadného web_search overenia predpokladov. "
        "DOLEŽITÉ: každé pole (reasoning, key_assumptions, close_confidence, ...) odovzdaj "
        "VÝHRADNE ako svoj vlastný samostatný kľúč v tomto tool volaní - nikdy nepíš obsah "
        "ďalších polí ako text/XML značky vnútri iného poľa (napr. vnútri reasoning)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "recommendation": {
                "type": "string", "enum": ["hold", "consider_closing"],
                "description": (
                    "hold = pôvodné predpoklady držia, žiadny naliehavý dôvod na zásah. "
                    "consider_closing = DVA rovnocenné dôvody, oba platia rovnako (nie je to len "
                    "o riziku): (1) predpoklady sa výrazne oslabili alebo sa objavilo nové podstatné "
                    "riziko, ALEBO (2) pôvodná téza sa v podstate UŽ NAPLNILA a momentum viditeľne "
                    "slabne/stagnuje (napr. cena už dosiahla alebo takmer dosiahla TP a odvtedy dlhšie "
                    "nepostupuje ďalej, prípadne sa už čiastočne vrátila späť) - vtedy zváž zamknutie "
                    "zisku, nečakaj mechanicky na presný TP zásah len preto, že sa ešte technicky "
                    "nevykonal. Nizsie (close_confidence) POZOR: toto reálne spúšťa automatické "
                    "zatvorenie pri dostatočnej istote, nie je to len opinion pre používateľa."
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
            # Pridane 2026-08-17, PLNE ZIVE od 2026-08-21 (viz
            # trade_cycle._maybe_ai_early_close + config.AI_EARLY_CLOSE_CONFIDENCE_THRESHOLD,
            # default 50) - NIE JE to uz len logovanie, popis nizsie to musi
            # odrazat presne, inak Claude tomuto cislu neopravnene neprikladal
            # vahu (2026-08-26 produkcny nalez, ZEC - zastarany popis tvrdil
            # opak reality).
            # 2026-08-31 (na ziadost pouzivatela, nalez 3 z revizie promptov) -
            # POCAS drzania pozicie sa dovtedy NEDALO nastavit cenovy watch:
            # tieto polia v schema chybali A watch_monitor.check_watch_triggers
            # symboly s otvorenou poziciou zamerne preskakoval ("if open_trade:
            # continue"). Claude teda nemal ako povedat "drz, ale ak prerazi pod
            # X, pozri sa na to znova" - musel bud zatvorit hned, alebo cakat na
            # dalsi planovany cyklus (po 2026-08-31 az 2 hodiny).
            #
            # Watch nastaveny TU vedie na dalsi HEALTH CHECK, nie na otvaraci
            # cyklus (pozicia je stale otvorena, takze run_cycle_for_asset
            # smeruje spat sem). Sluzi teda na prehodnotenie TEZY, nie na vstup.
            "watch_price": {
                "type": "number",
                "description": (
                    "VOLITEĽNÉ (vždy spolu s watch_direction). Konkrétna cenová úroveň, pri "
                    "ktorej chceš túto pozíciu prehodnotiť ESTE PRED ďalším plánovaným cyklom - "
                    "typicky úroveň, ktorej prekonanie by tvoju pôvodnú tézu vyvrátilo (napr. "
                    "prelomenie supportu, o ktorý sa long opiera) alebo naopak potvrdilo. "
                    "Lacný poller sleduje živú cenu a pri splnení ťa mimoriadne zavolá znova na "
                    "ďalší health check - NIE na otvorenie novej pozície. "
                    "NENASTAVUJ, ak je dôvod na prehodnotenie ČASOVÁ udalosť (report, earnings) - "
                    "tam žiadny cenový pohyb tvoju neistotu nevyrieši. NENASTAVUJ ani vtedy, ak "
                    "úroveň leží za SL/TP - tam pozíciu aj tak zavrie burza sama."
                ),
            },
            "watch_direction": {
                "type": "string", "enum": ["above", "below"],
                "description": (
                    "VOLITEĽNÉ (vždy spolu s watch_price). Ktorým smerom musí cena prejsť cez "
                    "watch_price, aby sa spustil mimoriadny health check."
                ),
            },
            "watch_rationale": {
                "type": "string",
                "description": (
                    "POVINNÉ, ak vypĺňaš watch_price. Jedna veta: čo presne by dosiahnutie tejto "
                    "úrovne znamenalo a čo by si vtedy zvážil (napr. \"pod 0.0181 padá support, "
                    "o ktorý sa short opiera - vtedy by som zvážil zatvorenie so ziskom\"). "
                    "Nestačí skonštatovať úroveň - vysvetli VZŤAH k téze pozície."
                ),
            },
            "data_issue": {
                "type": "string",
                "description": (
                    "VOLITEĽNÉ: ak ti vstupné dáta pre tento cyklus prídu podozrivé alebo "
                    "nekonzistentné (zastaraná/nulová cena, evidentne chybný TA údaj, poškodené "
                    "dáta z externých zdrojov), stručne to popíš - NEZÁVISLE od svojho hodnotenia "
                    "pozície. Zobrazí sa v histórii signálov, aby si to všimol aj človek. "
                    "Na bežné neistoty trhu toto pole NEPOUŽÍVAJ."
                ),
            },
            "close_confidence": {
                "type": "integer",
                "description": (
                    # 2026-08-29 (na ziadost pouzivatela, rovnaky fix ako "confidence" vyssie -
                    # empiricky overene, ze za 58 zaznamenanych hodnot toto pole NIKDY
                    # neprekrocilo 68, hoci povodny text definoval "70-100" pasmo ako
                    # najsilnejsi signal - konkretne cislene pasma pravdepodobne posobili
                    # rovnako ukotvujuco ako pri confidence poli) - nahradene konceptom
                    # kalibracie, ziadne konkretne cisla ako referencne body.
                    "LEN ak recommendation=consider_closing. Kalibrovaná pravdepodobnosť (0-100), "
                    "že ZATVORENIE PRÁVE TERAZ je správne rozhodnutie - NIE to isté ako všeobecná "
                    "obchodná istota, ale konkrétne: keby si mal exekučnú právomoc, urobil by si "
                    "to hneď? Kalibrované znamená: ak by si rovnaké číslo priradil opakovane "
                    "naprieč mnohými nezávislými prípadmi, malo by približne zodpovedať "
                    "skutočnému podielu tých, kde bolo zatvorenie naozaj správne rozhodnutie. "
                    "Použi CELÝ rozsah 0-100 podľa reálnej sily dôkazov o vyvrátení pôvodnej "
                    "tézy - nedrž sa umelo v strednom pásme ani sa nevyhýbaj vysokým hodnotám, "
                    "ak je téza podľa teba skutočne prakticky vyvrátená alebo už naplnená a "
                    "momentum stagnuje. DÔLEŽITÉ: toto číslo REÁLNE SPÚŠŤA akciu - nad hranicou, "
                    "ktorú ti zámerne neuvádzame (nehádaj ju), systém pozíciu AUTOMATICKY zatvorí "
                    "trhovým príkazom bez ďalšieho čakania na používateľa. Buď preto úprimný a "
                    "kalibrovaný, nie umelo opatrný ani prehnane istý - toto sa NEPOUŽÍVA len "
                    "na spätné hodnotenie."
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
                    "IZOLOVANA poznamka LEN k VCERAJSKU - do buducich promptov sa neprenasa, sluzi "
                    "pouzivatelovi v dashboarde. Strucne (2-4 vety) zhodnot dve veci: (1) ci "
                    "sedeli tvoje vcerajsie confidence cisla s vysledkami - vysli setupy s vyssim "
                    "cislom castejsie nez tie s nizsim? Ak nie, v com bol odhad systematicky vedla "
                    "(prilis isty pri chase vstupoch, prilis neisty pri potvrdenom trende...). "
                    "Hodnot LEN presnost svojich odhadov, nie to, ktore z nich sa vykonali - "
                    "o tom nerozhodujes; (2) ci "
                    "tvoje 'none' rozhodnutia boli opodstatnene, alebo ci si bol niekedy zbytocne "
                    "opatrny a v spatnom pohlade malo byt LONG/SHORT. Ak nemas take udaje k "
                    "dispozicii v tomto cykle, toto pole VYNECHAJ."
                ),
            },
        },
        "required": ["recommendation", "expected_direction", "reasoning", "key_assumptions"],
    },
}

# 2026-09-04 (bod 6 auditu) - LACNY SKEN pred plnym cyklom. Zamerne NEDAVA
# moznost rozhodnut o obchode: jedina otazka je, ci sa nieco zmenilo natolko,
# aby stalo za drahu plnu analyzu (web_search, cely prompt, minuty).
TRIAGE_TOOL = {
    "name": "submit_triage",
    "description": (
        "Odovzdaj verdikt rychleho skenu. Zavolaj VZDY, je to jediny sposob, ako "
        "odpoved odovzdat. NEROZHODUJES o obchode - len o tom, ci sa oplati pozriet "
        "sa na tento nastroj dokladne (s citanim sprav)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "worth_full_look": {
                "type": "boolean",
                "description": (
                    "true = od posledneho dokladneho pohladu sa stalo nieco, co stoji za "
                    "plnu analyzu (vyrazny cenovy pohyb, prerazenie urovne, nezvycajny objem, "
                    "obrat cross-marketu, nove headliny, bliziaca sa makro udalost, zmena "
                    "technickeho obrazu oproti predpokladom). false = obraz je v podstate "
                    "rovnaky ako vtedy."
                ),
            },
            "attention": {
                "type": "integer", "minimum": 0, "maximum": 100,
                "description": (
                    "0-100, ako VELA sa deje. 0 = uplne ticho, 100 = dramaticky pohyb/udalost. "
                    "Nezavisle od worth_full_look (sluzi na neskorsie ladenie prahu) - vypln vzdy."
                ),
            },
            "reason": {
                "type": "string",
                "description": "1-2 vety, PRECO ano/nie. Konkretne (cislo, uroven, nadpis), nie vseobecne.",
            },
            "watch_price": {
                "type": "number",
                "description": (
                    "VOLITELNE (vzdy s watch_direction + watch_rationale) - cenova uroven, "
                    "ktorej dosiahnutie by stalo za dokladny pohlad. Lacny poller ju sleduje "
                    "kazdu minutu. Nastav ju TYPICKY vtedy, ked hlasis worth_full_look=false, "
                    "ale vidis uroven, pri ktorej by sa to zmenilo. Uroven daj za hranicu bezneho "
                    "sumu (orientacne aspon ~1 ATR od aktualnej ceny), nie tesne pri cene."
                ),
            },
            "watch_direction": {"type": "string", "enum": ["above", "below"],
                                 "description": "Volitelne, vzdy s watch_price."},
            "watch_rationale": {
                "type": "string",
                "description": "POVINNE, ak vyplnas watch_price. Jedna veta: co by dosiahnutie tej urovne znamenalo.",
            },
            "data_issue": {
                "type": "string",
                "description": (
                    "VOLITELNE - len ak su vstupne data zjavne pokazene (nulova/zastarana cena, "
                    "nezmyselne TA cisla). Nie na bezne neistoty trhu."
                ),
            },
        },
        "required": ["worth_full_look", "attention", "reason"],
    },
}


TRIAGE_SYSTEM_PROMPT = """Si rychly filter pre obchodneho analytika. Tvoja uloha NIE JE rozhodnut o obchode -
je rozhodnut, ci sa od jeho posledneho DOKLADNEHO pohladu stalo nieco, co stoji za plnu analyzu.
Plna analyza je draha: cita cerstve spravy cez web_search a trva minuty. Ty spravy NEVIDIS -
mas len ceny, indikatory, cross-market a titulky.

Povedz ANO, ked: cena sa vyrazne pohla alebo prerazila uroven, objem je nezvycajny, cross-market
sa otocil (VIX/vynosy/BTC), titulky naznacuju novu udalost, blizi sa makro udalost, alebo sa
technicky obraz zmenil oproti predpokladom z posledneho pohladu.
Povedz NIE, ked je obraz v podstate rovnaky: cena v pasme bez prerazenia, priemerny objem,
ziadne nove titulky, indikatory bez zmeny rezimu.

Nie si opatrny ani odvazny - si presny. Falosne ANO stoji peniaze, falosne NIE znamena zmeskanu
prilezitost. Ci je pohyb vyznamny, posudzuj VOCI `atr14` daneho nastroja (pohyb radovo mensi nez
ATR je bezny sum), nie voci absolutnemu cislu.

Ked hlasis NIE, ale vidis konkretnu uroven, pri ktorej by sa to zmenilo, nastav watch_price -
lacny poller ju sleduje kazdu minutu a vtedy sa spusti plny cyklus. To je najlepsi vysledok skenu:
usetrena analyza teraz + istota, ze skutocny pohyb neujde.

Odpovedaj VYHRADNE volanim nastroja submit_triage."""


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

_ZHIPU_MACRO_RULES = """- **KRITICKÉ UPOZORNENIE - toto NIE JE bežne obchodovaná akcia**: Zhipu AI (Z.ai) je SÚKROMNÁ
  (neverejne obchodovaná) čínska AI firma - "cena" {instrument} na Strike je syntetický tracker bez
  reálneho akciového trhu/orderbooku za sebou (rovnaká kategória ako MINIMAX). Cenový pohyb je
  pravdepodobne oveľa viac sentiment/špekulácia-driven a menej naviazaný na overiteľné fundamenty než
  pri bežne obchodovaných akciách - zváž túto štrukturálnu neistotu pri confidence (nižšia než by
  rovnaký signál dostal na likvidnejšom tickeri).
- **Financovanie/ocenenie/IPO príprava**: Zhipu AI (predtým Zhipu Huazhang, tvorca GLM/ChatGLM
  modelov) je jedna zo štyroch čínskych "AI Tiger" startupov - správy o nových investičných kolách,
  zmenách ocenenia pri privátnych transakciách, alebo príprave na IPO (STAR Market/Hong Kong) sú
  kľúčové katalyzátory - pre súkromnú firmu ekvivalent "earnings" u verejne obchodovanej.
- **Konkurenčná čínska/globálna AI krajina**: porovnaj s inými čínskymi AI labmi (DeepSeek, MiniMax,
  Moonshot AI/Kimi, Baichuan, 01.AI) aj globálnymi (OpenAI, Anthropic, Google DeepMind) - nové GLM
  modelové vydania FIRMOU SAMOTNOU aj prelomové modely/produkty konkurencie môžu ovplyvniť vnímané
  postavenie {instrument} aj bez priamej firemnej správy.
- **Čínska regulácia AI sektora a US-Čína technologické obmedzenia**: vysoká citlivosť, podobne ako
  SKHYNIX/polovodiče, ale s dodatočným rizikom priamych sankcií/blacklistingu (Entity List a pod.) -
  toto je systémové riziko špecifické pre čínske AI firmy (rovnaké ako pri MINIMAX).
- **Slabšia trhová hĺbka → vyššia korelácia so širším risk-on/off sentimentom**: keďže ide o
  syntetický tracker na krypto-natívnej derivátovej platforme (nie skutočný akciový trh), cena môže
  reagovať viac na všeobecnú náladu (BTC/krypto risk-on-off, VIX režim) než na fundamenty firmy - ber
  to ako dodatočný kontextový signál, podobne ako pri kryptu.
- **Market Reaction Score**: rovnako dôležité ako inde, možno ešte viac vzhľadom na tenší trh -
  porovnaj obsah správy s reálnou cenovou reakciou {instrument}.
- **Event Risk Gate**: akákoľvek správa o financovaní/ocenení, väčšom produktovom/modelovom launchi,
  čínskej AI regulácii, alebo geopolitickej eskalácii US-Čína AI/čip politiky - buď výrazne
  konzervatívnejší (nízka confidence alebo "none"), keďže potvrdenie/vyvrátenie takýchto správ je pri
  súkromnej firme ťažšie overiteľné než pri verejne obchodovanej.
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

_CRCL_MACRO_RULES = """- **Vydavateľ USDC stablecoinu - biznis model priamo naviazaný na úrokové sadzby**: {instrument}
  (Circle Internet Group) generuje väčšinu príjmu z úrokového výnosu na rezervách kryjúcich USDC
  (primárne krátkodobé US treasuries) - Fed rozhodnutia o sadzbách majú PRIAMY, mechanický dopad na
  ziskovosť (znižovanie sadzieb = klesajúci rezervný výnos = nižšie zisky, nie len všeobecný makro
  sentiment ako pri väčšine akcií). Toto je najdôležitejší odlišujúci faktor oproti bežnej tech akcii.
  Preto FOMC rozhodnutia/dot-plot majú tu väčšiu váhu než pri GOOGL/AAPL.
- **Regulácia stablecoinov (GENIUS Act a nadväzujúca legislatíva/implementácia)**: US stablecoin
  regulačný rámec (podpísaný 2025) priamo určuje konkurenčné prostredie a compliance náklady -
  over cez web_search najnovší stav implementácie, prípadné dodatočné nariadenia (Fed/OCC/štátne
  bankové regulátory), a akékoľvek zmeny v požiadavkách na rezervy/audit.
- **Konkurencia v stablecoinovom priestore**: Tether (USDT, dominantný podľa market capu, ale menej
  transparentný), PayPal USD, bankové konzorciové stablecoiny (napr. JPMorgan/veľké banky), a
  potenciálne budúce Fed digitálne iniciatívy - zmeny v trhovom podiele USDC voči USDT sú priamy
  fundamentálny signál.
- **Korelácia s krypto trhom (nie len BTC/ETH cenou, ale objemom/adopciou)**: rastúci celkový market
  cap krypto trhu a obchodný objem = viac USDC v obehu = vyššie rezervy = vyšší úrokový príjem -
  {instrument} teda reaguje na ZDRAVIE krypto ekosystému ako celku (adopcia, DeFi TVL, burzové
  objemy), nie len na cenový pohyb konkrétnej mince. Overená korelácia (2026-08-30, 30-dňové dáta):
  BTC 0.55, NVDA 0.60 - momentum tech aj krypto risk-on sentiment obe hýbu cenou.
- **Bankové/fintech partnerstvá a expanzia**: nové partnerstvá s bankami/platobnými sieťami
  (Visa/Mastercard integrácie, cezhraničné platby) rozširujú use-case USDC mimo čisto krypto
  trading - pozitívny fundamentálny driver nezávislý od krypto cenových pohybov.
- **Krátka obchodná história (IPO jún 2025)**: na rozdiel od GOOGL/AAPL/NVDA nemá {instrument}
  dlhoročný track record cez rôzne trhové cykly - buď opatrnejší pri extrapolácii dlhodobých vzorov
  z krátkej histórie, hoci už nejde o čerstvé IPO ako UNITREE/ZHIPU.
- **Market Reaction Score**: rovnako dôležité ako inde - porovnaj obsah správy s reálnou cenovou
  reakciou {instrument}.
- **Event Risk Gate**: FOMC rozhodnutia (kvôli priamemu dopadu na rezervný výnos), nová stablecoinová
  regulácia/legislatívny vývoj, veľké krypto trhové udalosti (výrazný pokles/rast celkového market
  capu, veľké burzové výpadky), a bežné makro dáta (CPI/NFP) - buď pri nich výrazne konzervatívnejší
  (nízka confidence alebo "none").
- **Nepredvídateľné politické výroky (Trump/Truth Social)**: vyjadrenia ku krypto/stablecoinovej
  politike vedia bez varovania pohnúť sentimentom - over cez web_search nedávne výroky s dopadom na
  stablecoinový/krypto regulačný sektor."""


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
    "ZHIPU": {
        "label": "syntetický Strike tracker Zhipu AI/Z.ai (súkromná čínska AI firma, NIE verejne obchodovaná akcia)",
        "news_focus": (
            'správach o Zhipu AI/Z.ai (financovanie/funding rounds, ocenenie/valuation, GLM/ChatGLM '
            'modelové vydania, prípadné IPO/verejný listing), konkurenčnej čínskej AI krajine (DeepSeek, '
            'MiniMax, Moonshot AI/Kimi, Baichuan, 01.AI) aj globálnej (OpenAI, Anthropic, Google '
            'DeepMind), čínskej regulácii AI sektora a US-Čína technologických obmedzeniach/sankciách, '
            'a širšom krypto/risk-on-off naratíve (kedže ide o tracker na krypto-natívnej platforme bez '
            'reálnej trhovej hĺbky)'
        ),
        "macro_rules": _ZHIPU_MACRO_RULES,
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
    "CRCL": {
        "label": "akciu CRCL (Circle Internet Group, vydavateľ USDC stablecoinu, NYSE IPO jún 2025)",
        "news_focus": (
            'správach o Circle Internet Group samotnom (earnings, rast USDC market capu/obehu, '
            'úrokový výnos z rezerv, nové partnerstvá s bankami/platobnými sieťami), regulácii '
            'stablecoinov (GENIUS Act implementácia, Fed/OCC nariadenia), konkurencii v '
            'stablecoinovom priestore (Tether/USDT, PayPal USD, bankové konzorciové stablecoiny), '
            'širšom krypto trhovom zdraví (market cap, DeFi TVL, burzové objemy), a Fed/makro '
            'dátach (CPI, PPI, NFP, FOMC - zvýšená váha kvôli priamemu dopadu sadzieb na rezervný výnos)'
        ),
        "macro_rules": _CRCL_MACRO_RULES,
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
    "AAPL": {
        "label": "akciu AAPL (Apple)",
        "news_focus": (
            'správach o Apple samotnom (iPhone predaje/guidance podľa regiónu, Services segment '
            'rast a marže, Apple Intelligence/AI stratégia a jej prijatie oproti konkurencii, '
            'kapex a nové produkty), dodávateľskom reťazci (Foxconn, TSMC, čínska výroba, '
            'diverzifikácia mimo Číny - India/Vietnam), regulačnom/antitrustovom tlaku (App Store '
            'provízie, EU DMA vynucovanie, US DOJ spory), a Fed/makro dátach (CPI, PPI, NFP, FOMC)'
        ),
        "macro_rules": _EQUITY_MACRO_RULES,
    },
    "NEAR": {
        "label": "krypto NEAR (Near Protocol, L1 smart-contract platforma s 2026 AI-infra naratívom) perpetuál",
        "news_focus": (
            'správach o Near Protocol ekosystéme (SPICE protokolový upgrad - separácia konsenzu/'
            'exekúcie, NEAR Intents cross-chain naratv, developer/ekosystémové granty), governance '
            'kontroverziách (tokenomika presadená napriek zlyhanému hlasovaniu komunity - overuj, '
            'či sa neopakuje podobný precedens), regulačnom/burzovom prostredí (SEC postoj ku krypto '
            'L1 tokenom, listingy/delistingy), a širšom krypto naratíve (BTC dominance, risk-on/off '
            'sentiment, AI-blockchain sektorový sentiment, veľké likvidácie na trhu)'
        ),
        "macro_rules": _CRYPTO_MACRO_RULES,
    },
    "PUMP": {
        "label": "krypto PUMP (Pump.fun, Solana launchpad pre meme tokeny) perpetuál",
        "news_focus": (
            'správach o Pump.fun samotnom (objem a počet nových tokenov na platforme, podiel na '
            'trhu voči konkurenčným launchpadom, revenue a spätné odkupy tokenu, zmeny v poplatkoch '
            'alebo mechanike vydávania), tokenomike a odomykaní (ICO z júla 2025, vesting/unlock '
            'harmonogramy, pohyby tímových a investorských peňaženiek), regulačnom tlaku (žaloby '
            'a vyšetrovania okolo meme-coin launchpadov, postoj SEC k tokenom s revenue-share '
            'charakterom), stave Solana ekosystému (sieťová aktivita, výpadky, poplatky, DEX objemy) '
            'a v širšom krypto naratíve (BTC dominancia, risk-on/off, veľké likvidácie). '
            'POZOR: "pump" je zároveň bežné slovo aj názov manipulatívnej schémy ("pump and dump") - '
            'pri vyhľadávaní vždy over, či výsledok hovorí naozaj o Pump.fun a nie o niečom inom'
        ),
        "macro_rules": _CRYPTO_MACRO_RULES,
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
- Ak signály nie sú jasné alebo sú protichodné, zvoľ "none".
- confidence je 0-100 kalibrovaná pravdepodobnosť, že tento smer vyjde - presné pravidlo je v
  popise poľa `confidence` v nástroji. Je to tvoj odhad, nie páka: žiadnu hranicu, ktorú by si mal
  "prekročiť", nepoznáš a nemáš ju hádať. Ak je presvedčenie len stredné, správna odpoveď je
  úprimné stredné číslo + watch úroveň, nie číslo posunuté nahor. Ak dostaneš sekciu "Opakovane
  rovnaký smer bez otvorenia pozície", je to spočítaný fakt o tom, koľko cyklov za sebou navrhuješ
  ten istý smer - samotný POČET cyklov ani plynutie času NIE JE dôvod na vyššiu confidence;
  rozhoduje len, či sa cena skutočne posunula navrhovaným smerom, alebo zostáva plochá (vtedy
  dlhšie držanie extrému skôr zvyšuje pravdepodobnosť odrazu).
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
- watch_price/watch_direction (VOLITEĽNÉ) - presné pravidlá sú v popise polí v nástroji. Kľúčové:
  nastavuj len na CENOVÚ úroveň, ktorú by cenový pohyb sám vedel potvrdiť (retest, breakout), NIKDY
  keď je blokujúci dôvod časová udalosť (report/earnings) - tam ťa zobudí len šum. Vždy, keď watch
  nastavíš, MUSÍ `watch_rationale` konkrétne povedať, čo splnenie úrovne znamená a čo by si potom
  urobil (napr. "breakdown pod 0.1614 = pokračovanie downtrendu, zvážil by som short") - nie len
  "úroveň platí". Obojstranný watch (watch_price_2) len pri genuinne obojstranne neistom setupe.
- confidence_threshold_note: ak zvolíš direction="long"/"short", ale tvoje presvedčenie je len
  STREDNÉ (setup vidíš, nie je presvedčivý), napíš, PRI AKEJ CENE by tvoje presvedčenie z ČISTO
  TECHNICKÉHO hľadiska (potvrdený breakout, úspešný retest, prekonanie konkrétnej úrovne) výrazne
  stúplo, a tú istú cenu zapíš aj do watch_price/watch_direction (above pre potvrdenie LONG, below
  pre SHORT). PLYNUTIE ČASU SAMO OSEBE NIKDY nie je dôvod na vyššie presvedčenie - len skutočný
  cenový pohyb. Je ÚPLNE V PORIADKU napísať, že takú cenu nevieš odhadnúť (blokujúci dôvod je
  udalosť, nie úroveň) - vtedy watch nechaj prázdny. Pri silnom aj zjavne slabom presvedčení pole
  vynechaj.
- data_issue (VOLITEĽNÉ): len na zjavne pokazené vstupy (nulová/zastaraná cena, nezmyselné TA
  čísla), nezávisle od rozhodnutia - nie na bežnú neistotu trhu.
- daily_reflection (VOLITEĽNÉ): raz denne dostaneš "Nové štatistiky za včerajšok" - napíš k nim
  izolovanú poznámku (2-4 vety, ide len do dashboardu, nie do ďalších promptov): (1) či tvoje
  confidence čísla sedeli s výsledkami (vyšší odhad = častejší úspech?) - hodnoť LEN presnosť
  svojich odhadov, nie to, ktoré z nich sa vykonali; (2) či boli 'none' rozhodnutia opodstatnené. Jeden deň je malá
  vzorka. Bez tej sekcie pole vynechaj. Tvoju dlhodobú výkonnosť dostávaš ako SPOČÍTANÉ fakty v
  user správe - to je opis toho, čo sa naozaj dialo, nie pravidlo, ktoré máš aplikovať.
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
Sviečky majú piaty údaj `volume`. Posledná sviečka je VŽDY prebiehajúca hodina, preto má
`volume=null` - jej tempo a projekciu nájdeš v `current_candle_volume` (`volume_so_far`,
`projected_full_hour_volume`, `projected_vs_avg20_ratio`; pri `pace_reliable=false` je príliš skoro
v hodine na projekciu). `null` inde = chýbajúci údaj, NIE nulový objem - takú sviečku z objemovej
úvahy vynechaj. `last_candle_volume_vs_avg20_ratio` = posledná DOKONČENÁ hodina voči priemeru 20
pred ňou (spoľahlivé, ale až hodinu staré).
Ako s tým pracovať: (a) prerazenie / watch úroveň potvrdená na PODPRIEMERNOM objeme (ratio pod ~1)
je slabé potvrdenie, najmä ak je cena natiahnutá alebo ide o pokračovanie bez retestu - zváž nižšiu
confidence; (b) neobvykle vysoký objem bez zodpovedajúceho pohybu ceny (alebo s obratom) môže
znamenať absorpciu/vyčerpanie - jeden vstup do úsudku, nie pravidlo; (c) na prebiehajúci prielom
použi projekciu a povedz, že je to odhad z N minút."""


# 2026-09-01 (z otazky pouzivatela, ci sa da watch nastavit aj na objem):
# Strike objem nevracia vobec a externe zdroje pokryvaju len cast tickerov,
# ale bid/ask VELKOSTI a index_price v tej istej odpovedi ano. Doteraz sa
# z nich bral len spread. Tieto tri polia su surove fakty, nie odporucanie -
# zamerne sa nehovori "pri zapornej nerovnovahe shortuj", lebo to overene nie je.













_TA_GLOSSARY_NOTE = """
Význam TA polí (hodinové, ak nie je uvedené inak):
- `trend` = ŠTRUKTÚRA (poradie EMA20/50/200 voči cene), nie momentum. `strong_up/downtrend` = EMA
  zoradené A RSI mimo 40-60 (štruktúra aj momentum sa zhodujú - najsilnejší signál); `*_stalling` =
  EMA zoradené, ale RSI v neutráli - pohyb štrukturálne trvá, momentum vyprchalo, neber ako čerstvé
  potvrdenie; `mild_*` = len nad/pod EMA200 - najslabšie; `insufficient_data` = málo histórie.
- `adx14`/`trend_strength`: `weak_no_trend` (<20) = konsolidácia, žiadny trend na surfovanie;
  `developing` (20-25); `trending` (>=25) = potvrdený. Portfólio malo 69 % win rate pri potvrdenom
  trende a 26 % (oboma smermi) v plochom období - pri `weak_no_trend`/`developing` vyžaduj silnejšie
  potvrdenie než "cena prekonala úroveň", alebo nižšiu confidence.
- `h4_context`/`daily_context`: to isté na 4h a denných sviečkach (`trend`, `rsi14`, `adx14`,
  `trend_strength`, `momentum_state`). Zhoda s hodinovým = silnejšie potvrdenie; rozpor = hodinový
  signál ber opatrnejšie - S VÝNIMKOU: keď je vyšší timeframe `overbought`/`oversold` a hodinový
  ukazuje obrat opačným smerom (najmä na objeme), je to vzor vyčerpania - PODPORA hodinového
  signálu, nie protiváha. Vyšší timeframe má prednosť len keď sám nie je v extréme.
- `spread_pct`: aktuálny bid/ask spread v % - široký (tenké/syntetické tickery) = riziko sklzu,
  mierny dôvod na opatrnosť.
- `funding`: aktuálna Strike funding rate (% za HODINU; kladná = long platí, short dostáva). Drobný
  doplnok - za 36 dní spolu -7 $.
- `long_short_ratio` (len tickery s Binance futures): podiel účtov long vs short, nie objem. Extrém
  (ratio nad ~2.5 alebo pod ~0.4) = riziko squeeze proti davu; blízko 1 = nič."""


_RANGE_NOTE = """
`price_range` (ak je prítomný) - mechanické meranie z vlastných hodinových dát: pásmo sa uzná, len
ak sa cena viackrát dotkla oboch okrajov, šírka je stabilná a dosť široká. NIE je to detekcia
režimu - nehovorí, či trh trenduje alebo sa vracia do stredu.
Polia: `in_range`, `range_high`, `range_low`, `position_in_range` (0 = dno, 1 = vrchol), `at_edge`
("vrchol"/"dno"/null), `range_width_pct`.
- `in_range=false` -> pole ti nehovorí nič, rozhoduj podľa ostatného.
- `in_range=true` a `at_edge` vrchol/dno -> vstup V SMERE pohybu tu v backteste (12 146
  príležitostí, s poplatkami) systematicky prehráva; vstup PROTI pohybu (na vrchole short, na dne
  long) je v horšom prípade rovnako zlý, v lepšom výrazne lepší. Ak chceš ísť v smere pohybu, MUSÍŠ
  v reasoning uviesť konkrétny dôvod mimo samotnej ceny (čerstvý katalyzátor, prerazenie na
  nadpriemernom objeme) - "silný trend" ani "vysoký ADX" taký dôvod NIE SÚ, presne tie sprevádzali
  stratové vstupy.
- `in_range=true`, `at_edge=null` (stred) -> priamy vstup nemá oporu; nastav watch na okraj pásma a
  do watch_rationale napíš, že tam zvážiš vstup proti pohybu.
- Pri otvorenej pozícii: ak si vstúpil ako fade a cena došla k protiľahlému okraju, je to legitímny
  (zriedkavý) dôvod zvážiť consider_closing."""


_PER_ASSET_SYSTEM_APPENDIX_TEMPLATE = """Si skúsený intradenný analytik pre {label}.
Dostaneš technickú analýzu (TA) {instrument} - vrátane `recent_candles`, surových posledných
{candle_bars} hodinových sviečok {candle_format} - cross-market kontext, session
alignment{btc_proxy_note} a prípadne social-media sentiment. Máš k dispozícii nástroj web_search -
použi ho na vyhľadanie čerstvých {news_focus}, ktoré by mohli hýbať cenou v najbližších 24
hodinách. Vyhľadávaj len ak to dáva zmysel (max. niekoľko vyhľadávaní).
{volume_note}
{ta_glossary_note}
{range_note}

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
    volume_note = _VOLUME_NOTE if include_volume else ""
    per_asset_text = _PER_ASSET_SYSTEM_APPENDIX_TEMPLATE.format(
        label=text["label"],
        instrument=asset["name"],
        news_focus=text["news_focus"],
        macro_rules=text["macro_rules"].format(instrument=asset["name"]),
        btc_proxy_note=btc_proxy_note,
        candle_bars=market_data.RECENT_CANDLES_BARS,
        candle_format=candle_format,
        volume_note=volume_note,
        ta_glossary_note=_TA_GLOSSARY_NOTE,
        range_note=_RANGE_NOTE,
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
    # 2026-08-21 (produkcny nalez, GOOGL) - predtym chybal zaznam, takze Claude
    # dostal len surovy retazec "ai_early_close" bez vysvetlenia + action_note
    # nizsie ho vobec neodlisoval od SL/likvidacie (viz jej komentar) - reflexia
    # tak vobec neriesila TO, co sa realne stalo (vlastne predcasne rozhodnutie
    # zatvorit), len vseobecnu SL-timing otazku.
    "ai_early_close": "TY SÁM si túto pozíciu predčasne zatvoril (vysoká istota consider_closing)",
}


# 2026-09-04 (audit, balik B) - do promptu idu z TA len polia, ktore maju v
# systemovom prompte vysvetlenie. Mikrostruktura (book_imbalance/book_depth_usd/
# premium_pct - poznamka sama hovorila "bez overenej hodnoty") a diagnosticke
# polia price_range (failed_conditions a spol. - vyvolavali falosne data_issue)
# zostavaju v CycleLog.ta pre spatnu analyzu, Claude ich uz nevidi.
_TA_PROMPT_DROP = {"book_imbalance", "book_depth_usd", "premium_pct"}
_PRICE_RANGE_PROMPT_KEEP = {"in_range", "range_high", "range_low", "position_in_range",
                            "at_edge", "range_width_pct"}


def _ta_for_prompt(ta: dict | None) -> dict | None:
    if not ta:
        return ta
    out = {k: v for k, v in ta.items() if k not in _TA_PROMPT_DROP}
    pr = out.get("price_range")
    if isinstance(pr, dict):
        out["price_range"] = {k: v for k, v in pr.items() if k in _PRICE_RANGE_PROMPT_KEEP}
    return out


def _build_user_prompt(asset: dict, ta: dict, cross_market: dict, session: dict,
                        social: list[dict], btc_proxy: dict | None,
                        prev_assumptions: str | None,
                        prev_cycle_time: datetime | None = None,
                        performance_facts: str | None = None,
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
                        pre_macro_events: list[dict] | None = None,
                        schedule: dict | None = None,
                        recent_close: dict | None = None,
                        close_verdict: dict | None = None,
                        coinmarketcal_events: list[dict] | None = None,
                        recent_trades_context: list[dict] | None = None,
                        portfolio_exposure: list[dict] | None = None) -> str:
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

    # 2026-09-04 (bod 4 auditu) - namiesto rolling retrospektivy (Claudov volny
    # text o sebe samom) spocitane fakty, viz performance_facts.py.
    retro_block = ""
    if performance_facts:
        retro_block += "\n" + performance_facts
    if new_stats_text:
        retro_block += (
            f"\n## Nové štatistiky za včerajšok (vygeneruj daily_reflection)\n"
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
            f"\n## Opakovane rovnaký smer bez otvorenia pozície (posledných {cs['streak_len']} cyklov za sebou)\n"
            f"Posledných {cs['streak_len']} cyklov za sebou navrhuješ rovnaký smer ({direction_label}) "
            f"bez toho, aby sa pozícia otvorila - {movement_desc}.\n"
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
            # CycleLog.created_at je Column(DateTime) BEZ timezone=True - Postgres
            # ho teda uklada/vracia ako tz-naive, aj ked bol pri zapise vytvoreny s
            # tzinfo=utc (SQLAlchemy tzinfo ticho zahodi). Bez tejto normalizacie
            # tu padalo "can't subtract offset-naive and offset-aware datetimes" -
            # realny produkcny nalez (viackrat naprieč roznymi tickermi 2026-08-21).
            wsc_created_at = wsc["created_at"]
            if wsc_created_at.tzinfo is None:
                wsc_created_at = wsc_created_at.replace(tzinfo=timezone.utc)
            elapsed_min = round((datetime.now(timezone.utc) - wsc_created_at).total_seconds() / 60)
        # 2026-09-04 produkcny nalez (ADA #186) - doteraz sa vypisovala LEN
        # uroven c. 1 a k nej watch_rationale. Lenze rationale je JEDNO volne
        # pole pre oba pary: pri ADA popisovalo uroven "below 0.217" (prielom
        # nadol => potvrdil by short), ale zobrazilo sa pri urovni
        # "above 0.22503". Claude tak videl PRIELOM NAHOR oblepeny textom, ktory
        # argumentoval PRE SHORT - a short aj otvoril, 37 minut po tom, co v
        # predchadzajucom cykle sam napisal, ze fade short proti ADX 51 nedava
        # zmysel. Teraz sa vypisu OBE urovne a explicitne sa oznaci, ktora
        # padla, aby sa odovodnenie nedalo priradit k nespravnej strane.
        levels = []
        crossed = []
        live_now = (ta or {}).get("last_price")
        for px, dr, tag in ((wsc.get("watch_price"), wsc.get("watch_direction"), "1"),
                            (wsc.get("watch_price_2"), wsc.get("watch_direction_2"), "2")):
            if px is None or not dr:
                continue
            hit = ""
            if live_now is not None:
                if (dr == "above" and live_now >= px) or (dr == "below" and live_now <= px):
                    hit = "  <-- TÁTO ÚROVEŇ PADLA"
                    crossed.append(f"{dr} {px}")
            levels.append(f"  ({tag}) {dr} {px}{hit}")
        rationale_line = (
            f"Tvoje vtedajšie odôvodnenie (POZOR - je spoločné pre obe úrovne, over si, "
            f"ktorej sa naozaj týka): \"{wsc['watch_rationale']}\""
            if wsc.get("watch_rationale") else "(bez zaznamenaného odôvodnenia)"
        )
        crossed_line = (
            f"Padla úroveň: {', '.join(crossed)}.\n" if crossed
            else "Ktorá úroveň padla, urči z aktuálnej ceny vyššie.\n"
        )
        # 2026-09-04 (audit) - HLBKA PRERAZENIA ako fakt. Doteraz Claude videl
        # len "uroven padla" a knot od pohybu nerozoznal (otvoril z knotu rovnako
        # casto ako z pohybu, ktory drzal - 18 % vs 45 % win). Cislo prahu sa tu
        # ZAMERNE neuvadza, inak by podla neho posuval samotne watch urovne - viz
        # config.WATCH_BREAK_MIN_ATR a trade_cycle._watch_break_too_shallow.
        break_line = ""
        brk = wsc.get("break")
        if brk and brk.get("depth_atr") is not None:
            depth = brk["depth_atr"]
            if depth >= 0:
                where = (f"Hĺbka prerazenia: cena je {depth:.2f} ATR(14h) za úrovňou "
                         f"{brk['direction']} {brk['level']}.")
            else:
                where = (f"POZOR: cena sa už vrátila SPÄŤ cez úroveň {brk['direction']} "
                         f"{brk['level']} ({abs(depth):.2f} ATR dovnútra) - úroveň padla len knôtom.")
            break_line = (
                f"{where} Vstup V SMERE prerazenia systém mechanicky zamietne, ak je prerazenie "
                f"plytké (typický knôt, pár desatín ATR alebo návrat dnu) - vtedy je správna "
                f"odpoveď none + watch úroveň ďalej od ceny, nie vstup.\n"
            )
        watch_set_context_block = (
            f"\n## Toto rozhodnutie bolo vyvolané TVOJOU VLASTNOU watch podmienkou"
            f"{f' (pred {elapsed_min} min)' if elapsed_min is not None else ''}\n"
            f"V predchádzajúcom cykle si pri cene {wsc.get('live_price')} zvolil "
            f"direction='{wsc.get('direction')}' (confidence={wsc.get('confidence')}) "
            f"a nastavil tieto sledované úrovne:\n"
            + "\n".join(levels) + "\n"
            + crossed_line
            + break_line
            + f"{rationale_line}\n"
            f"Ak TERAZ voliš iný smer/confidence než vtedy, v reasoningu VÝSLOVNE napíš, čo konkrétne "
            f"sa oproti tomuto dôvodu čakania zmenilo (nová cenová akcia, potvrdenie/vyvrátenie "
            f"signálu a pod.) - nezopakuj len novú analýzu bez odkazu na predchádzajúce rozhodnutie.\n"
            f"OSOBITNE POZOR, ak chceš ísť PROTI smeru, ktorý táto úroveň mala potvrdiť (napr. úroveň "
            f"bola nastavená ako potvrdenie prielomu nahor, cena ju prekonala, a ty by si teraz "
            f"shortoval): to je legitímne LEN vtedy, ak vieš pomenovať konkrétny dôvod, prečo prielom "
            f"nie je platný - a ten dôvod musí byť NIEČO NOVÉ, nie posunutie tej istej úrovne vyššie. "
            f"Ak by si len prehlásil rovnaký signál za \"zatiaľ nepotvrdený\" a postavil sa proti nemu, "
            f"správnejšia odpoveď je direction=\"none\" a nová watch úroveň.\n"
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

    # 2026-08-26 (na ziadost pouzivatela, po portfolio-wide audite chase-breakout
    # strat) - namiesto mechanickeho "streak" pocitadla (rovnaky problem ako
    # confidence_streak vyssie, len pre SKUTOCNE obchody: prosty pocet stat by
    # rovnako trestal aj nesuvisiace udalosti za sebou) davame surovy material -
    # posledne uzavrete obchody na tomto tickeri VRATANE ich closed_trade_reflection/
    # sl_tp_calibration_verdict (ak uz existuju) - a NECHAME Claude-a SAMEHO
    # posudit, ci medzi nimi vidi opakujuci sa vzor. Viz trade_cycle._get_recent_closed_trades_context.
    recent_trades_block = ""
    if recent_trades_context:
        rt_lines = ["\n## Posledné obchody na tomto tickeri (od najstaršieho po najnovší)"]
        for i, rt in enumerate(recent_trades_context, 1):
            direction_label = "LONG" if (rt.get("direction") or "").lower() == "long" else "SHORT"
            conf_str = f"conf {rt['confidence']}" if rt.get("confidence") is not None else "conf ?"
            pnl = rt.get("pnl_usd")
            pnl_str = f"${pnl:+.2f}" if pnl is not None else "PnL zatiaľ neznáme"
            hours_ago = rt.get("hours_ago")
            ago_str = f", pred {hours_ago:.1f}h" if hours_ago is not None else ""
            reason = rt.get("close_reason") or "?"
            # 2026-08-31 - typ vstupu podla cenoveho pasma v case vstupu, aby sa
            # dal odlisit fade na okraji od bezneho momentum vstupu (viz price_range.py)
            entry_type = rt.get("entry_type")
            et_str = f" [{entry_type}]" if entry_type else ""
            rt_lines.append(
                f"{i}. {direction_label} ({conf_str}){et_str} → {reason}, {pnl_str}{ago_str}")
            if rt.get("reflection"):
                rt_lines.append(f"   Hodnotenie: {rt['reflection']}")
            if rt.get("sl_tp_verdict"):
                rt_lines.append(f"   SL/TP verdikt: {rt['sl_tp_verdict']}")
        rt_lines.append(
            "Toto je surový prehľad, NIE hotový verdikt - posúď SÁM, či medzi týmito obchodmi vidíš "
            "opakujúci sa vzor (napr. rovnaký typ vstupu, rovnaká chyba v načasovaní), alebo ide o "
            "nesúvisiace udalosti. Samotný počet strát za sebou nič neznamená bez spoločnej príčiny."
        )
        recent_trades_block = "\n".join(rt_lines) + "\n"

    # 2026-08-29 (na ziadost pouzivatela) - risk_manager doteraz bral do uvahy
    # LEN otvorenu poziciu na TOMTO ISTOM tickeri, o ostatnych sucasne
    # otvorenych poziciach (a ich korelacii s tymto tickerom) Claude nevedel
    # vobec nic. Viz trade_cycle._get_portfolio_exposure_context.
    portfolio_exposure_block = ""
    if portfolio_exposure:
        pe_lines = [f"\n## Aktuálne otvorené pozície na INÝCH tickeroch (a ich korelácia s {instrument})"]
        for pe in portfolio_exposure:
            direction_label = "LONG" if (pe.get("direction") or "").lower() == "long" else "SHORT"
            corr = pe.get("correlation")
            corr_str = f"korelácia {corr:+.2f}" if corr is not None else "korelácia neznáma (nedosť prekrývajúcich sa dát)"
            pe_lines.append(f"- {pe['symbol']} {direction_label} (marža ${pe['margin_usd']:.0f}) - {corr_str}")
        pe_lines.append(
            "Korelácia sa počíta z hodinových výnosov posledných 30 dní (rovnaká metodika ako "
            "korelačná matica v dashboarde) - hodnota blízko +1/-1 znamená, že tento ticker sa hýbe "
            "takmer rovnako/opačne ako už otvorená pozícia (pridáva koncentrovanú, nie nezávislú "
            "expozíciu), blízko 0 znamená skutočnú diverzifikáciu. Zváž to pri confidence/veľkosti "
            "novej pozície - toto je informačný fakt o CELKOVOM riziku portfólia, nie dôvod na "
            "automatické zamietnutie.\n"
        )
        portfolio_exposure_block = "\n".join(pe_lines) + "\n"

    # 2026-09-03 (z otazky pouzivatela) - KEDY BUDE DALSI PLANOVANY BEH.
    #
    # Doteraz bolo v prompte len "tento cyklus bezi kazdych Xh", z coho sa dalo
    # odvodit nanajvys "o X hodin od teraz". To je pri MIMORIADNOM cykle
    # zavadzajuce: slotova mriezka je ukotvena, takze po watch triggeri o 19:07
    # nenasledoval dalsi beh o 21:07, ale uz o 20:30. Claude teda nemal ako
    # posudit, ako dlho bude "slepy", ked watch uroven nenastavi - a rozhodoval
    # sa o nej bez tejto informacie.
    #
    # Namerane za 10 dni: po cykle BEZ watchu bol median odstupu do dalsieho
    # behu 73 min, p90 az 446 min a v 15 % pripadov nad 3 hodiny.
    #
    # interval_h nizsie je teraz REALNE platny interval (trading/off-hours/
    # vikend), nie vzdy trade_interval_hours - stary text tvrdil obchodnu
    # hodnotu aj cez vikend.
    interval_h = (schedule or {}).get("interval_hours") or interval_h
    schedule_line = ""
    if schedule and schedule.get("next_run"):
        nr = schedule["next_run"]
        mins = (nr - now).total_seconds() / 60
        if mins > 0:
            when = f"o {mins:.0f} min" if mins < 120 else f"o {mins/60:.1f} h"
            schedule_line = (
                f"\nNajbližší PLÁNOVANÝ beh: {nr.strftime('%H:%M')} UTC, teda {when}. "
                f"Ak si nenastavíš watch úroveň (a nepríde makroudalosť), dovtedy sa na trh "
                f"nepozrieš - watch je jediný spôsob, ako sa dostať k obrazovke skôr. "
                f"Je to odhad: mriežka sa posúva pri zmene intervalu "
                f"(trading/off-hours/víkend) a po mimoriadnom behu môže byť ďalší plánovaný "
                f"odložený.\n"
            )

    header = f"""## Aktuálny dátum a čas
{now.strftime('%A, %d. %B %Y, %H:%M')} UTC ({now.isoformat()})
Tento cyklus beží každých {interval_h}h - zaujímajú ťa hlavne udalosti/správy za posledných
~{interval_h} hodín, staršie ber len ako pozadový kontext (nie ako novú informáciu).
{schedule_line}

## Technická analýza {instrument}
{json.dumps(_ta_for_prompt(ta), indent=2, ensure_ascii=False)}

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
{recent_trades_block}{portfolio_exposure_block}
{retro_block}"""

    # 2026-09-02 (navrh pouzivatela) - makro udalost, ktora nastane PRED
    # najblizsim planovanym behom tohto tickera (viz
    # trade_cycle._events_before_next_run). Toto je posledny cyklus, v ktorom
    # sa da nieco nastavit: predtym sa taka udalost riesila az spatne
    # hromadnym mimoriadnym cyklom pre vsetky tickery naraz, ktory za 30 dni
    # stal $22.73 a obchod z neho vzisiel v 1 % pripadov (rovnako ako z
    # bezneho behu). Watch-triggered cyklus otvara obchod v 11,5 % - preto sa
    # teraz plati az za reakciu na pohyb, nie za "pozretie sa".
    pre_macro_block = ""
    if pre_macro_events:
        lines = []
        for e in pre_macro_events:
            dt = e["datetime_utc"]
            hours = (dt - now).total_seconds() / 3600
            lines.append(f"- **{e['name']}** o {dt.strftime('%H:%M')} UTC "
                         f"(o {hours:.1f}h od teraz)")
        events_text = "\n".join(lines)
        plural = "udalosťami" if len(pre_macro_events) > 1 else "udalosťou"
        pre_macro_block = f"""## Toto je POSLEDNÝ plánovaný cyklus pred makro {plural}
{events_text}

Ďalší plánovaný beh {instrument} príde AŽ PO nej. Bot v čase udalosti sám od seba mimoriadny
cyklus nespustí, ak tu necháš živú watch úroveň - namiesto toho ťa lacný poller zobudí až
vtedy, keď sa cena naozaj pohne tam, kam ukážeš.

Preto v tomto cykle VŽDY nastav watch úrovne, aj keď ideš do direction=none - a nastav ich
OBOJSTRANNE (watch_price/watch_direction aj watch_price_2/watch_direction_2). Smer reakcie na
makro číslo nie je vopred známy; jednostranná úroveň by zachytila len polovicu možných
scenárov. Úrovne umiestni tam, kde by ťa pohyb naozaj zaujímal - teda za hranicou bežného šumu
(orientačne aspoň ~1 ATR od aktuálnej ceny), nie tesne pri cene, kde ťa zobudí každý tick. Ak
watch necháš prázdny, bot v čase udalosti spustí plný (platený) cyklus ako poistku.

Tento blok NERUŠÍ Event Risk Gate zo system promptu - ak ti pravidlá hovoria pred touto
udalosťou nevstupovať, stále platia. Hovorí len to, že po tomto cykle už ďalšia PLÁNOVANÁ
príležitosť pred udalosťou nebude.

"""

    # 2026-09-04 produkcny nalez (ADA #177) - ODLOZENY VERDIKT.
    # Post-close reflexia sa pisala v medianu 2.2 min po zatvoreni (91 % do
    # 5 min), teda v okne, kde sa este nic nestihlo stat - a skoro vzdy vysla
    # ako "dobre timeovane". ADA long zatvoreny o 02:16 dostal o 02:18 verdikt
    # "dobre timeovane", pricom o dve hodiny cena zasiahla TP. Reflexia ide do
    # rolling retrospektivy, takze sa bot ucil z nepravdiveho zaveru.
    close_verdict_block = ""
    if close_verdict:
        cv = close_verdict
        pnl = cv.get("pnl_usd")
        pnl_txt = (f"{'+' if pnl and pnl >= 0 else ''}${pnl:.2f}"
                   if pnl is not None else "?")
        close_verdict_block = f"""
## Spätné zhodnotenie staršieho zatvorenia (obchod #{cv['trade_id']})
Toto NIE JE o aktuálnom rozhodnutí - je to samostatná otázka s odstupom.

Pred {cv['hours_ago']:.1f} h si zatvoril {cv['direction'].upper()} pozíciu:
vstup {cv['entry_price']}, výstup {cv['exit_price']}, dôvod {cv['close_reason']}, PnL {pnl_txt}.
Pôvodné SL {cv.get('stop_loss_price')}, TP {cv.get('take_profit_price')}.

ČO SA S CENOU STALO OD VTEDY (to si vtedy vedieť nemohol):
- najpriaznivejšia cena pre tú pozíciu: {cv['best_since']}  (to je {cv['missed_pct']}% od tvojho výstupu)
- najnepriaznivejšia: {cv['worst_since']}
- teraz: {cv['price_now']}

Cez `closed_trade_reflection` napíš verdikt: bolo zatvorenie v tomto svetle správne, predčasné,
alebo naopak neskoré? Ak by pozícia pri držaní dosiahla TP alebo naopak spadla na SL, povedz to
priamo. Toto je jediné miesto, kde sa dá načasovanie výstupu posúdiť poctivo - reflexia písaná
pár minút po zatvorení to vedieť nemôže, tak sa jej nedrž.

"""

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
        is_long_pos = direction_label == "LONG"
        sign = "+" if op["unrealized_pnl_usd"] >= 0 else ""
        # 2026-08-26 produkcny nalez (ZEC) - explicitny, mechanicky vypocitany
        # fakt namiesto spoliehania sa na to, ze si Claude vsimne "cena tu uz
        # bola" sam zo surovych sviecok (viz trade_cycle._run_position_health_check
        # pre vypocet). Bez tohto pri stojacej teze lahko preveazi potvrdzovaci
        # vzor ("korekcia pokracuje") nad tym, ze sa uz vlastne zastavila.
        best_price_line = ""
        if op.get("best_price_since_open") is not None and op.get("best_price_hours_ago") is not None:
            best = op["best_price_since_open"]
            live = op["live_price"]
            # LONG: best = najvyssia cena (vrchol), nevyhodny pohyb = pokles OD nej -> (best-live)/best.
            # SHORT: best = najnizsia cena (dno), nevyhodny pohyb = rast OD nej -> (live-best)/best.
            pullback_pct = ((best - live) / best * 100) if is_long_pos else ((live - best) / best * 100)
            pullback_note = (
                f"odvtedy sa cena vrátila o {pullback_pct:.1f}% späť (nevýhodným smerom, TOTO NIE JE "
                "pokračovanie pôvodného pohybu)"
                if pullback_pct > 0.05 else
                "cena je stále prakticky na tejto úrovni (žiadny návrat späť zatiaľ)"
            )
            best_price_line = (
                f"Najpriaznivejšia cena od otvorenia: {best} (dosiahnutá pred "
                f"{op['best_price_hours_ago']:.1f}h) - {pullback_note}.\n"
            )
        # 2026-08-27 (ADA #90 incident) - ak tento cyklus vznikol tak, ze
        # mechanicky cooldown medzi eskaláciami bol OBIDENÝ kvôli ďalšiemu
        # zhoršeniu P&L (viz trade_cycle._run_position_health_check), Claude by
        # inak nemal ako vedieť, že ide o mimoriadny re-check pri zhoršujúcej sa
        # strate, nie bežnú hodinovú kontrolu - explicitný fakt namiesto ticha.
        cooldown_bypass_line = ""
        if op.get("cooldown_bypass_reason"):
            cooldown_bypass_line = (
                f"POZOR - mimoriadny re-check: {op['cooldown_bypass_reason']}. Pozícia sa medzi "
                "poslednou a touto kontrolou ďalej zhoršila, preto sa bežný cooldown medzi "
                "eskaláciami obišiel.\n"
            )
        # 2026-09-04 (audit, A4) - cislo prahu sa uz NEUVADZA, rovnako ako pri
        # vstupe (namerana kotva: close_confidence za 58 hodnot nikdy nad 68).
        position_block = f"""## OTVORENÁ POZÍCIA (toto NIE JE rozhodnutie o novom obchode - hodnotíš EXISTUJÚCU pozíciu)
Smer: {direction_label} | Vstup: {op['entry_price']} | Aktuálna cena: {op['live_price']}
Stop-loss: {op['stop_loss_price']} | Take-profit: {op['take_profit_price']} | Leverage: {op['leverage']}x
Otvorená: {op['opened_at_str']} ({op['hours_held']:.1f}h dozadu)
Nerealizované PnL: {sign}${op['unrealized_pnl_usd']:.2f} ({sign}{op['unrealized_pnl_pct']:.2f}% z marže)
{best_price_line}{cooldown_bypass_line}
Zhodnoť, či pôvodné kľúčové predpoklady (vyššie) stále platia, alebo sa niečo podstatné zmenilo -
over si to cez web_search rovnako ako pri bežnom cykle (dotaz cielený na konkrétnu tému z
predpokladov, nie len na cenu nástroja). Na základe toho posúď, či očakávaš, že sa cena bude naďalej
vyvíjať V PROSPECH tejto pozície alebo PROTI nej.

ČO TVOJA ODPOVEĎ SKUTOČNE SPÔSOBÍ:
- SL/TP na burze NEMENÍŠ - tie zostávajú presne tam, kde sú, bez ohľadu na tvoju odpoveď.
- Ale ak zvolíš recommendation="consider_closing" a close_confidence je dosť vysoká (hranicu ti
  zámerne neuvádzame - píš úprimný odhad, nie číslo na výsledok), bot pozíciu ZATVORÍ SÁM
  trhovým príkazom, okamžite a bez potvrdenia
  človekom. NIE JE to len názor do logu. Podľa toho zváž, akú istotu tam napíšeš - podhodnotené
  číslo znamená, že pozícia zostane otvorená aj vtedy, keď si myslíš, že by nemala.
- Máš aj TRETIU možnosť medzi "držím ďalej" a "zatváram teraz": watch_price + watch_direction.
  Lacný poller sleduje živú cenu každú minútu a keď tvoju úroveň dosiahne, zavolá ťa na
  MIMORIADNY health check tejto istej pozície - teda skôr, než by prišiel ďalší plánovaný.
  Použi to vtedy, keď je tvoja neistota naviazaná na konkrétnu cenovú úroveň ("držím, ale ak
  padne pod support X, téza padá s ním"), nie na plynutie času. Nastavená úroveň platí, kým ju
  v niektorom ďalšom health checku nezmeníš alebo nevynecháš - vynechanie ju zruší."""
        # POZOR: {recent_trades_block} sa NEPRIDAVA znova - uz je sucastou {header}
        # vyssie (spolocne pre oba vetvy tejto funkcie). Predchadzajuca verzia ho
        # sem pridavala druhykrat (duplicitne, zbytocne tokeny) - opravene 2026-08-27.
        return f"{header}\n{pre_macro_block}{close_verdict_block}{macro_event_block}{position_block}\n"

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
        if ct.get("evaluation_only") and ct.get("close_reason") == "ai_early_close":
            # 2026-08-21 (na ziadost pouzivatela, po GOOGL naleze) - predtym sa
            # tato vetva vobec neodlisovala od SL/likvidacie nizsie, takze
            # dostal SL-timing/entry-quality otazku namiesto tej relevantnej -
            # ci bolo SPRAVNE, ze si sam poziciu zatvoril na zaklade
            # consider_closing s vysokou istotou (viz trade_cycle._maybe_ai_early_close).
            action_note = (
                "Toto je mimoriadny cyklus spustený HNEĎ po tom, čo si TY SÁM zatvoril túto pozíciu "
                "trhovým príkazom - nie SL/TP/timeout, ale TVOJE VLASTNÉ rozhodnutie v predchádzajúcom "
                "position health checku, kde si odporučil consider_closing s dostatočne vysokou istotou "
                "(close_confidence), že to systém automaticky vykonal (nie len ako opinion pre "
                "používateľa - viz config.AI_EARLY_CLOSE_CONFIDENCE_THRESHOLD). Cez closed_trade_reflection "
                "zaznamenaj, na čom tvoja téza stála a ČO KONKRÉTNE by ju potvrdilo alebo vyvrátilo "
                "(napr. \"ak cena do 4h prekoná X, zatvorenie bolo predčasné\"). "
                "NETVRĎ, či bolo zatvorenie dobre načasované - od zatvorenia ubehlo pár minút a "
                "cena sa ešte nestihla nikam pohnúť, takže taký verdikt by bol bezcenný. "
                "(2026-09-04: reflexie písané 2 minúty po zatvorení skoro vždy vyšli ako "
                "\"dobre timeované\" a šli do retrospektívy ako fakt - preto sa verdikt teraz "
                "pýta znova s odstupom niekoľkých hodín.) DÔLEŽITÉ: tvoje direction/confidence "
                "rozhodnutie nižšie sa v TOMTO behu NEVYKONÁ - žiadna nová pozícia sa z neho priamo "
                "neotvorí, nech je confidence akákoľvek. Je to zámerné (aby okamžitý re-entry po "
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
        elif ct.get("evaluation_only"):
            action_note = (
                "Toto je mimoriadny cyklus spustený HNEĎ po zatvorení tejto pozície na SL/likvidáciou "
                "(nie bežný interval). Cez closed_trade_reflection zhodnoť, či bol vstup/SL nastavený "
                "primerane, alebo či niečo (vstup pri prehriatom RSI, chase breakoutu a pod.) vopred "
                "naznačovalo zvýšené riziko rýchleho zvratu. DÔLEŽITÉ: tvoje direction/confidence "
                "rozhodnutie nižšie sa v TOMTO behu NEVYKONÁ - žiadna nová pozícia sa z neho priamo "
                "neotvorí, nech je confidence akákoľvek. Je to zámerné (aby okamžitý re-entry po "
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

        # 2026-08-31 (na ziadost pouzivatela, po zisteni ze self-heal retry
        # stareho zaseknuteho post-close review - viz position_monitor.
        # _backfill_stale_reviews - hlasil "PRAVE zatvorena" aj pri obchode
        # zatvorenom pred dnami, hoci zvysok cyklu pouziva DNESNE trhove data.
        # Explicitny cas + hodiny odvtedy namiesto neподmieneneho "prave" -
        # Claude si sam posudi, nakolko su aktualne trhove data relevantne
        # pre TOTO zatvorenie (cerstve = priamo relevantne, stare = len
        # vseobecny kontext "co sa odvtedy stalo", nie bezprostredny dosledok).
        hours_since_close = ct.get("hours_since_close")
        if hours_since_close is not None and hours_since_close > 1:
            timing_label = f"pred {hours_since_close:.1f}h ({ct.get('closed_at_str', '?')})"
        else:
            timing_label = "práve teraz"
        # 2026-08-31 - stav cenoveho pasma V CASE VSTUPU. Bez toho by review
        # nevedelo, ci slo o fade vstup na okraji pasma alebo o bezny momentum
        # vstup, a nemalo by sa z coho poucit prave o tej novej vetve.
        epr = ct.get("entry_price_range") or {}
        if epr.get("in_range"):
            pos = epr.get("position_in_range")
            edge = epr.get("at_edge")
            entry_kind = ("vstup NA OKRAJI pásma" if edge else "vstup v STREDE pásma")
            range_line = (
                f"\nPásmo pri vstupe: {epr.get('range_low')} - {epr.get('range_high')} "
                f"(šírka {epr.get('range_width_pct')}%), pozícia v pásme "
                f"{pos if pos is not None else '?'}"
                f"{f' ({edge})' if edge else ''} - {entry_kind}."
            )
        elif epr:
            range_line = "\nPásmo pri vstupe: inštrument NEBOL v ustanovenom cenovom pásme."
        else:
            range_line = ""
        closed_trade_block = f"""## Zatvorená pozícia (dôvod: {reason_label}) - zatvorená {timing_label}
Smer: {(ct['direction'] or '').upper()} | Vstup: {ct['entry_price']} | Výstup: {ct['exit_price']}
Držaná: {ct['hours_held']:.1f}h | PnL: {sign}${ct['pnl_usd']:.2f}{range_line}
{"POZOR: toto zatvorenie NIE JE čerstvé - nižšie uvedené trhové dáta sú AKTUÁLNE (teraz), nie z momentu zatvorenia. Ber ich ako kontext 'čo sa odvtedy stalo', nie ako bezprostredný dôsledok tohto zatvorenia." if hours_since_close is not None and hours_since_close > 1 else ""}

{action_note}
{sltp_eval_block}
"""

    # 2026-09-04 produkcny nalez (ADA #177 -> #186) - KONFRONTACIA s vlastnym
    # cerstvym zatvorenim. Bot zatvoril long a o 39 minut otvoril short za
    # vyssiu cenu, do trendu, ktory sam oznacil za silny uptrend, bez novej
    # informacie. Nie je to blok (otocenie moze byt spravne), ale Claude to musi
    # mat pred ocami a vyjadrit sa k tomu.
    recent_close_block = ""
    if recent_close and recent_close.get("direction") in ("long", "short"):
        rc = recent_close
        opp = "short" if rc["direction"] == "long" else "long"
        pnl = rc.get("pnl_usd")
        pnl_txt = (f"{'zisk' if pnl and pnl >= 0 else 'strata'} ${abs(pnl):.2f}"
                   if pnl is not None else "bez zaznamenaneho PnL")
        recent_close_block = f"""
## Na tomto tickeri si PRED {rc['hours_ago']:.1f} h zatvoril pozíciu
Smer {rc['direction'].upper()}, vstup {rc['entry_price']}, výstup {rc['exit_price']},
dôvod: {rc['close_reason']}, {pnl_txt}.

Ak teraz navrhuješ **{opp}** (teda OPAČNÝ smer), musíš v reasoningu výslovne odpovedať:
čo konkrétne NOVÉ sa od vtedy stalo? Otočenie smeru je legitímne, ale len na základe novej
informácie alebo novej cenovej akcie - nie na základe rovnakých dát, ktoré si videl pred
chvíľou. Zvlášť to platí, ak by nový vstup bol pre teba HORŠÍ ako cena, za ktorú si práve
vystúpil (short vyššie / long nižšie než výstup) - to je typický vzor "zmenil som názor,
lebo sa cena pohla", ktorý je stratový.

Ak rovnaký smer ako predtým: povedz, prečo je vstup teraz lepší než keď si pozíciu zatváral.
Ak nemáš na ani jedno dobrú odpoveď, direction="none" a watch úroveň sú správna voľba.
"""

    # 2026-09-04 (navrh pouzivatela, po merani z 3.-4.9.) - PRAH SA UZ NEUVADZA.
    #
    # Doteraz tu stalo "Minimalna confidence na otvorenie je X". Namerane na 859
    # cykloch: 82 % rozhodnuti nad prahom lezalo do 2 bodov nad nim - cislo teda
    # nenieslo rozlisovaciu informaciu, len sa prisposobovalo hranici. Meranie s
    # ukrytym prahom (30 cyklov, $6.12) dalo rozpatie 18 bodov namiesto 3 a
    # median 52; realizovany win rate 42 % na 178 obchodoch sedi na 52 omnoho
    # lepsie nez na vtedajsich 66.
    #
    # Marza sa teraz skaluje confidence (risk_manager.validate_and_size), takze
    # cely rozsah sa realne pouziva. Vzorec sa tu ZAMERNE NEUVADZA - inak by sa
    # cislo znova stalo pakou na velkost pozicie namiesto odhadu a stratili by
    # sme moznost overit kalibraciu.
    threshold_block = """
## Čo tvoja odpoveď spôsobí
Toto NIE JE cvičenie ani názor do logu. Ak vrátiš direction=long/short, bot môže na základe
tejto odpovede otvoriť REÁLNU pozíciu s pákou za živé peniaze - okamžite a bez potvrdenia
človekom.

`confidence` píš ako svoj úprimný kalibrovaný odhad pravdepodobnosti, že tento setup vyjde -
NIE ako "koľko treba, aby sa obchod otvoril". Prah ti zámerne neuvádzame a nesnaž sa ho
uhádnuť; číslo má byť tvoje presvedčenie, nie páka na výsledok. Používaj celý rozsah 0-100.

Ak si na vážkach, správna odpoveď je nízka confidence (prípadne direction="none") plus watch
úroveň - NIE číslo posunuté tak, aby vyšiel želaný výsledok. Vždy, keď navrhuješ smer, ale
tvoje presvedčenie je len stredné, pridaj aj watch_price/watch_direction s cenou, ktorá by ťa
presvedčila výraznejšie.
"""

    return f"""{header}
{pre_macro_block}{close_verdict_block}{macro_event_block}{recent_close_block}{closed_trade_block}## Cielove SL/TP vzdialenosti
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
            performance_facts: str | None = None,
            new_stats_text: str | None = None,
            fred_macro: dict | None = None,
            eia_data: dict | None = None,
            marketaux_news: list[dict] | None = None,
            confidence_streak: dict | None = None,
            closed_trade: dict | None = None,
            macro_event: str | None = None,
            pre_macro_events: list[dict] | None = None,
            schedule: dict | None = None,
            recent_close: dict | None = None,
            close_verdict: dict | None = None,
            coinmarketcal_events: list[dict] | None = None,
            watch_retrigger_streak: dict | None = None,
            watch_set_context: dict | None = None,
            recent_trades_context: list[dict] | None = None,
            portfolio_exposure: list[dict] | None = None) -> tuple[dict, list[dict], dict]:
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
    performance_facts: spocitany blok o doterajsej vykonnosti (viz performance_facts.py) -
    od 2026-09-04 namiesto rolling retrospektivy.
    new_stats_text: ak toto je prvy cyklus po polnoci a vcerajsok este nebol
    zapracovany do summary, sem sa vlozi cerstvo spocitany text (viz retrospective.py)
    - Claude ma za ulohu na jeho zaklade vygenerovat daily_reflection (izolovany
    zaznam do DailyRetrospective pre dashboard)."""
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY nie je nastavený")

    system_blocks = _system_prompt_blocks(asset)
    user_prompt = _build_user_prompt(asset, ta, cross_market, session, social,
                                      btc_proxy, prev_assumptions, prev_cycle_time,
                                      performance_facts, new_stats_text,
                                      fred_macro, eia_data, marketaux_news,
                                      confidence_streak, watch_retrigger_streak, watch_set_context,
                                      open_position=None,
                                      closed_trade=closed_trade, macro_event=macro_event,
                                      pre_macro_events=pre_macro_events,
                                      schedule=schedule,
                                      recent_close=recent_close,
                                      close_verdict=close_verdict,
                                      coinmarketcal_events=coinmarketcal_events,
                                      recent_trades_context=recent_trades_context,
                                      portfolio_exposure=portfolio_exposure)
    decision, web_search_log, usage = _call_claude(asset, system_blocks, user_prompt,
                                                     DECISION_TOOL, "submit_trade_decision")
    _validate_decision(decision)
    # ta["last_price"] je cena, s ktorou Claude v tomto cykle pracoval
    _drop_already_met_watch(decision, (ta or {}).get("last_price"), f" [{asset['name']}]")
    return decision, web_search_log, usage


def analyze_position_health(asset: dict, open_position: dict, ta: dict, cross_market: dict,
                             session: dict, social: list[dict],
                             btc_proxy: dict | None = None,
                             prev_assumptions: str | None = None,
                             prev_cycle_time: datetime | None = None,
                             performance_facts: str | None = None,
                             fred_macro: dict | None = None,
                             eia_data: dict | None = None,
                             marketaux_news: list[dict] | None = None,
                             macro_event: str | None = None,
                             pre_macro_events: list[dict] | None = None,
                             schedule: dict | None = None,
                             recent_close: dict | None = None,
                             close_verdict: dict | None = None,
                             new_stats_text: str | None = None,
                             coinmarketcal_events: list[dict] | None = None,
                             recent_trades_context: list[dict] | None = None,
                             portfolio_exposure: list[dict] | None = None) -> tuple[dict, list[dict], dict]:
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
                                      performance_facts, new_stats_text,
                                      fred_macro, eia_data, marketaux_news,
                                      confidence_streak=None, open_position=open_position,
                                      macro_event=macro_event,
                                      pre_macro_events=pre_macro_events,
                                      schedule=schedule,
                                      recent_close=recent_close,
                                      close_verdict=close_verdict,
                                      coinmarketcal_events=coinmarketcal_events,
                                      recent_trades_context=recent_trades_context,
                                      portfolio_exposure=portfolio_exposure)
    decision, web_search_log, usage = _call_claude(asset, system_blocks, user_prompt,
                                                     POSITION_HEALTH_TOOL, "submit_position_health_check")
    _validate_health_decision(decision)
    _drop_already_met_watch(decision, (open_position or {}).get("live_price"),
                             f" [{asset['name']} health]")
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

# 2026-08-22 dalsi variant (NIGHT, watch-triggered cyklus) - namiesto
# <parameter name="X">..</parameter> Claude tentokrat cely dalsi obsah
# (key_assumptions/watch_price/watch_direction/watch_rationale) vratil ako
# JEDNODUCHE <X>..</X> tagy s nazvom pola priamo, este aj s falosnym
# zaverecnym </invoke> - vyzera to ako echo staršieho XML-tool-call stylu
# (nikde v NASOM systemovom prompte taky priklad nie je, over. cez grep).
# Bez tejto opravy zostali watch_price/watch_direction v DB NULL, hoci ich
# Claude realne vygeneroval - watch_monitor.py cita VZDY najnovsi CycleLog
# riadok pre symbol, takze cely watch mechanizmus pre ten ticker do dalsieho
# beznho cyklu tichy prestal fungovat. Obmedzene na ZNAME nazvy poli (nie
# hocijaky tag), aby sa nikdy neomylom "zachranilo" nieco iné.
_PLAIN_TAG_FIELDS = (
    "key_assumptions", "watch_price", "watch_direction", "watch_price_2",
    "watch_direction_2", "watch_rationale", "confidence_threshold_note",
    "data_issue", "daily_reflection",
    "closed_trade_reflection", "sl_tp_calibration_verdict",
    "close_confidence", "recommendation", "expected_direction",
)
_PLAIN_TAG_FIELD_RE = re.compile(
    r"<(" + "|".join(_PLAIN_TAG_FIELDS) + r")>(.*?)</\1>", re.DOTALL,
)
_NUMERIC_FIELDS = {"watch_price", "watch_price_2"}
_DIRECTION_FIELDS = {"watch_direction", "watch_direction_2"}
# Vseobecny "zostala tam nejaka znacka" sniff test - POUZE na logovanie
# (viz koniec _recover_malformed_fields), nie na zachranu. Ak toto niekedy
# chytí zvysok po znamych formatoch vyssie, je to TRETI, este neosetreny
# format uniku - chceme sa o tom dozvediet z logu hned, nie az znova zo
# screenshotu od pouzivatela.
_RESIDUAL_TAG_RE = re.compile(r"</?\w+>")
# Strukturalny "sum" znamych formatov (</parameter> zvysky, prip. cely
# <invoke>/</invoke> obal) - toto sa NEMA pocitat ako "novy neznamy tag",
# odstran skor, nez sa kontroluje zvysok. Hranicna znacka hostitelskeho pola
# (napr. </reasoning>, </key_assumptions>) sa odstranuje OSOBITNE nizsie
# (viz self_close_marker v _recover_malformed_fields), kedze zavisi od toho,
# ktore pole je prave hostitelom.
_KNOWN_NOISE_TAG_RE = re.compile(
    r'</?parameter(?:\s+name="\w+")?>|</?invoke(?:\s+name="[^"]*")?>'
)
# Realny zachyteny pripad (#901/XAU) ukazal aj HYBRIDNY format: pole otvorene
# ako <parameter name="X">, ale zatvorene ako </X> namiesto </parameter> -
# _MALFORMED_FIELD_RE hodnotu aj tak spravne vytiahne (jej lookahead prijme
# hociaku </\w+> znacku), len samotna </X> znacka potom zostane v chvoste ako
# "neznamy" zvysok. Kedze X je uz ZNAME meno pola (_PLAIN_TAG_FIELDS), takato
# uzatvaracia znacka nie je novy problem - odstran ju rovnako ako ostatny sum.
_KNOWN_FIELD_CLOSE_TAG_RE = re.compile(
    r"</(" + "|".join(_PLAIN_TAG_FIELDS) + r")>"
)
# 2026-08-22 (na ziadost pouzivatela, po dalsom naleze - #707/ADA, #901/XAU,
# #2832/NAS100) - poskodena odpoved sa NEMUSI vzdy prejavit prave v reasoning
# (povodny predpoklad tejto funkcie) - v tychto pripadoch skoncil zvysok v
# key_assumptions alebo data_issue namiesto reasoning. Kazde volne textove
# pole z oboch tool schem (DECISION_TOOL/POSITION_HEALTH_TOOL) je teda
# potencialnym hostitelom a musi sa skenovat rovnako.
_HOST_FIELDS = (
    "reasoning", "key_assumptions", "data_issue", "watch_rationale",
    "confidence_threshold_note", "daily_reflection",
    "closed_trade_reflection", "sl_tp_calibration_verdict",
)


def _coerce_field_value(name: str, value: str):
    """Spolocna konverzia pre oba zachranne formaty nizsie - vrati None, ak je
    hodnota prazdna alebo (pre cisla/watch_direction enum) neplatna, aby
    volajuci taku hodnotu jednoducho preskocil namiesto zapisu odpadu."""
    value = value.strip()
    if not value:
        return None
    if name == "close_confidence":
        try:
            return int(value)
        except ValueError:
            return None
    if name in _NUMERIC_FIELDS:
        try:
            return float(value)
        except ValueError:
            return None
    if name in _DIRECTION_FIELDS:
        return value if value in ("above", "below") else None
    return value


def _recover_malformed_fields(decision: dict, asset_name: str) -> dict:
    """Ak niektore z volnych textovych poli (viz _HOST_FIELDS) obsahuje stopy
    poskodenej tool-call odpovede (viz komentare vyssie), skusi z neho
    dodatocne vytiahnut chybajuce polia (LEN ak uz nie su v decision inak
    vyplnene - nikdy neprepisuje spravne prisle pole). Nema vplyv na normalne
    (nepoškodene) odpovede - tie ziadnu z dvoch znackovych stop neobsahuju,
    regexy nenajdu zhodu, decision sa vrati bezo zmeny."""
    for host_field in _HOST_FIELDS:
        host_value = decision.get(host_field)
        if not host_value:
            continue
        self_close_marker = f"</{host_field}>"
        has_parameter_style = "<parameter name=" in host_value
        has_plain_tag_style = self_close_marker in host_value
        if not has_parameter_style and not has_plain_tag_style:
            continue

        print(f"[claude_analyst] [{asset_name}] POZOR: {host_field} obsahuje stopy poskodenej "
              "tool-call odpovede (viz _recover_malformed_fields) - skusam zachranit polia.")
        prefix = host_value.split("<parameter name=")[0].split(self_close_marker)[0]
        clean_value = prefix.strip()
        # Odrezany "chvost" (vsetko za koncom cisteho pola, PRED strip()) -
        # potrebny nizsie na kontrolu, ci po pokuse o zachranu nezostalo nieco
        # NEROZPOZNANE (viz _RESIDUAL_TAG_RE nizsie), lebo samotne (uz vycistene)
        # decision[host_field] by taky zvysok nikdy neobsahovalo - bol by z neho
        # prave odrezany. Pouzitie NEstriphnuteho prefixu tu je zamerne presne -
        # strip() by posunul hranicu chvosta o pripadny orezany biely znak.
        tail = host_value[len(prefix):]
        if clean_value:
            decision[host_field] = clean_value

        if has_parameter_style:
            for name, value in _MALFORMED_FIELD_RE.findall(host_value):
                value = _TRAILING_TAG_RE.sub("", value)
                if decision.get(name):
                    continue  # spravne prislo pole sa nikdy neprepisuje
                coerced = _coerce_field_value(name, value)
                if coerced is not None:
                    decision[name] = coerced

        if has_plain_tag_style:
            for name, value in _PLAIN_TAG_FIELD_RE.findall(host_value):
                if decision.get(name):
                    continue
                coerced = _coerce_field_value(name, value)
                if coerced is not None:
                    decision[name] = coerced

        # Preventivne (2026-08-22): odstran z chvosta vsetko, co uz obe zachranne
        # cesty vyssie ROZPOZNALI (aj vlastnu hranicnu znacku hostitela), a
        # preskusaj, ci nezostalo este nieco tagu-podobne - to by znamenalo
        # este dalsi, neosetreny format uniku. Cielom je zachytit to v logu
        # HNED nabuduce, nie az znova zo screenshotu od pouzivatela.
        remainder = _MALFORMED_FIELD_RE.sub("", tail)
        remainder = _PLAIN_TAG_FIELD_RE.sub("", remainder)
        remainder = remainder.replace(self_close_marker, "")
        remainder = _KNOWN_NOISE_TAG_RE.sub("", remainder)
        remainder = _KNOWN_FIELD_CLOSE_TAG_RE.sub("", remainder)
        if _RESIDUAL_TAG_RE.search(remainder):
            print(f"[claude_analyst] [{asset_name}] POZOR: aj po pokuse o zachranu zostavaju v "
                  f"odrezanom chvoste {host_field} podozrive znacky - mozny NOVY, este neosetreny "
                  f"format uniku: {remainder.strip()[:400]!r}")

    return decision


# 2026-08-22 (na ziadost pouzivatela, po dalsom "co su tie XML tagy?" screenshote,
# tentokrat ADA) - odlisny jav nez _recover_malformed_fields vyssie: Claude
# obcas cituje web_search zdroj priamo v reasoning ako
# <cite index="N-M">citovany text</cite> - vlastny format na oznacenie povodu
# tvrdenia (nikde v nasej scheme/prompte takto nedefinovany - grep. potvrdil).
# NEJDE o poskodenu tool-call odpoved: over. cez DB - 22 vyskytov naprieč 8
# tickermi od 2026-07-24 (skoro cely mesiac, od zaciatku zaznamov), VZDY len v
# reasoning (nikdy v key_assumptions/watch_rationale), vsetky ostatne polia
# vzdy spravne vyplnene. Ciste kozmeticky artefakt v zobrazenom texte, preto
# samostatna jednoducha funkcia (nie sucast recovery vyssie, ktora rieši
# skutocnu stratu dat).
_CITE_TAG_RE = re.compile(r"<cite[^>]*>(.*?)</cite>", re.DOTALL)


def _strip_citation_tags(decision: dict) -> dict:
    for field in ("reasoning", "key_assumptions", "watch_rationale"):
        value = decision.get(field)
        if value and "<cite" in value:
            decision[field] = _CITE_TAG_RE.sub(r"\1", value)
    return decision


def _post_messages(payload: dict, label: str):
    """POST /v1/messages s retry - zdielane medzi _call_claude (plny cyklus) a
    _call_triage (lacny sken).

    2026-08-20 produkcny nalez (ADA post-close review na TP zatvoreni #57 - Read
    timed out, ZIADNY retry, reflexia navzdy stratena): requests.post() mimo
    try/except znamenalo, ze retry na retryable STATUS KOD sa nikdy nedostal ku
    slovu, ak spojenie zlyhalo/vyprsalo skor, nez prislo VOBEC nejake HTTP telo.
    Preto siet ova vynimka prechadza rovnakym retry mechanizmom ako status kody."""
    for attempt in range(_MAX_API_RETRIES + 1):
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
                print(f"[claude_analyst] [{label}] POST /v1/messages zlyhalo "
                      f"({e.__class__.__name__}: {e}) - skusam znova o "
                      f"{_API_RETRY_DELAY_SECONDS}s ({attempt + 1}/{_MAX_API_RETRIES})...")
                time.sleep(_API_RETRY_DELAY_SECONDS)
                continue
            raise
        if resp.status_code in _RETRYABLE_STATUS and attempt < _MAX_API_RETRIES:
            print(f"[claude_analyst] [{label}] POST /v1/messages -> {resp.status_code} "
                  f"(prechodna chyba), skusam znova o {_API_RETRY_DELAY_SECONDS}s "
                  f"({attempt + 1}/{_MAX_API_RETRIES})...")
            time.sleep(_API_RETRY_DELAY_SECONDS)
            continue
        break
    resp.raise_for_status()
    return resp


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
    #
    # 2026-08-22 produkcny nalez (AAOI + 7 predoslych vyskytov naprieč tickermi za
    # ~2.5 tyzdna, vzdy "" alebo "high" effort): aj pri obycajnom "high" obcas viacero
    # volitelnych reflection poli naraz (closed_trade_reflection/sl_tp_calibration_verdict/
    # summary_reflection/watch_rationale) + web_search obsah zapln celych 8192 tokenov
    # PRED povinnym polom (typicky "reasoning" na konci) - cyklus sa bezpecne, ale
    # zbytocne zahodi (_validate_decision). "low"/"medium" (momentalne nikde nepouzite)
    # ostavaju pri povodnom strope.
    effort = asset.get("effort")
    if effort in ("xhigh", "max"):
        max_tokens = 24000
    elif effort in ("low", "medium"):
        max_tokens = 8192
    else:
        max_tokens = 16000

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

        resp = _post_messages(payload, asset["name"])
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
        cleaned = _strip_citation_tags(_recover_malformed_fields(decision_block["input"], asset["name"]))
        return cleaned, web_search_log, usage_record

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


def _drop_already_met_watch(decision: dict, live_price: float | None, label: str = "") -> None:
    """Zahodi watch uroven, ktora je UZ V MOMENTE NASTAVENIA splnena.

    2026-09-01 produkcny nalez (CRCL): Claude nastavil "below 96.3" pri cene
    88.88, teda uroven, ktora uz davno platila. Poller ju vyhodnotil ako splnenu
    na najblizsom tiku a o DVE MINUTY spustil plateny mimoriadny cyklus - v ktorom
    Claude sam nahlasil data_issue ("nepreukazuje ziadny nedavny dotyk 96.3 ani
    iny logicky spustac"). Zmiatol sam seba vlastnym watchom.

    Za 7 dni islo o 21 z 1054 nastavenych watchov (2 %). Skoda je trojaka:
    zbytocny plateny cyklus, spotrebovany WATCH_TRIGGER_MAX_PER_HOUR budget
    (moze vytlacit legitimny watch) a matuci vstup pre dalsie rozhodovanie.

    Zahadzuje sa LEN ta jedna uroven, nie cely cyklus - rozhodnutie o smere je
    nezavisle a platne. Druhy par (watch_price_2) sa posudzuje samostatne, takze
    obojstranny watch prezije aj vtedy, ked je nezmyselna len jedna jeho polovica."""
    if live_price is None:
        return
    try:
        price = float(live_price)
    except (TypeError, ValueError):
        return
    if price <= 0:
        return

    for price_key, dir_key in (("watch_price", "watch_direction"),
                                ("watch_price_2", "watch_direction_2")):
        wp, wd = decision.get(price_key), decision.get(dir_key)
        if wp is None or wd not in ("above", "below"):
            continue
        already = (wd == "above" and price >= float(wp)) or (wd == "below" and price <= float(wp))
        if already:
            print(f"[claude_analyst]{label} watch {wd} {wp} je uz splneny pri live cene "
                  f"{price} - zahadzujem (inak by hned spustil zbytocny cyklus).")
            decision.pop(price_key, None)
            decision.pop(dir_key, None)


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

    # 2026-08-31 - watch polia su volitelne, ale MUSIA prist ako platny par.
    # Nekompletny par sa TICHO zahodi (nie ValueError) - health check je hodnotenie
    # uz otvorenej pozicie a zahodit ho cely kvoli doplnkovemu polu by bolo horsie
    # nez prist o watch (rovnaka uvaha ako pri key_assumptions vyssie).
    wp, wd = decision.get("watch_price"), decision.get("watch_direction")
    if wp is None or wd not in ("above", "below"):
        decision.pop("watch_price", None)
        decision.pop("watch_direction", None)
        decision.pop("watch_rationale", None)


def _build_triage_prompt(asset: dict, ta: dict, cross_market: dict, session: dict,
                          btc_proxy: dict | None, prev_assumptions: str | None,
                          prev_cycle_time: datetime | None,
                          marketaux_news: list[dict] | None,
                          hours_since_full: float | None,
                          active_watch: dict | None,
                          schedule: dict | None) -> str:
    """User prompt pre lacny sken - podmnozina plneho promptu (bez makro pravidiel,
    bez historie obchodov, bez retrospektivy, bez snippetov clankov). Viz triage()."""
    instrument = asset["name"]
    now = datetime.now(timezone.utc)

    since = (f"Posledny DOKLADNY pohlad (s citanim sprav) bol pred {hours_since_full:.1f} h."
             if hours_since_full is not None else
             "Posledny dokladny pohlad: neznamy (prvy cyklus tohto nastroja).")

    prev_block = "(ziadne)"
    if prev_assumptions:
        when = f" (z cyklu o {prev_cycle_time.strftime('%H:%M UTC')})" if prev_cycle_time else ""
        prev_block = f'"{prev_assumptions}"{when}'

    news_block = "(ziadne cerstve titulky)"
    if marketaux_news:
        news_block = "\n".join(
            f"- [pred {a.get('age_hours'):.0f}h] {a.get('title')} (sentiment {a.get('sentiment_score')})"
            if a.get("age_hours") is not None else f"- {a.get('title')}"
            for a in marketaux_news
        )

    watch_block = "(ziadna aktivna uroven)"
    if active_watch and active_watch.get("watch_price") is not None:
        parts = [f"{active_watch.get('watch_direction')} {active_watch.get('watch_price')}"]
        if active_watch.get("watch_price_2") is not None:
            parts.append(f"{active_watch.get('watch_direction_2')} {active_watch.get('watch_price_2')}")
        watch_block = ", ".join(parts) + " (uz nastavene, poller ich sleduje)"

    schedule_line = ""
    if schedule and schedule.get("next_run"):
        mins = (schedule["next_run"] - now).total_seconds() / 60
        if mins > 0:
            schedule_line = (f"\nDalsi planovany cyklus tohto nastroja: o "
                             f"{mins:.0f} min." if mins < 120 else
                             f"\nDalsi planovany cyklus tohto nastroja: o {mins/60:.1f} h.")

    btc_block = ""
    if btc_proxy is not None:
        btc_block = f"\n## BTC (krypto risk-on/off referencia)\n{json.dumps(btc_proxy, ensure_ascii=False)}\n"

    return f"""## Aktualny datum a cas
{now.strftime('%A, %d. %B %Y, %H:%M')} UTC
{since}{schedule_line}

## Technicka analyza {instrument}
{json.dumps(_ta_for_prompt(ta), indent=2, ensure_ascii=False)}

## Cross-market kontext
{json.dumps(cross_market, indent=2, ensure_ascii=False)}

## Session alignment (Azia -> Europa -> US futures)
{json.dumps(session, indent=2, ensure_ascii=False)}
{btc_block}
## Cerstve titulky (Marketaux - len nadpisy, plne spravy vidi az plna analyza)
{news_block}

## Kluc. predpoklady z posledneho dokladneho pohladu
{prev_block}

## Aktualna sledovana cenova uroven
{watch_block}

Rozhodni: stoji tento nastroj PRAVE TERAZ za plnu analyzu so spravami?
Zavolaj submit_triage.
"""


def _call_triage(asset: dict, user_prompt: str) -> tuple[dict, dict]:
    """Jedno lacne volanie bez web_search (a teda bez pause_turn slucky).
    tool_choice vynucuje volanie nastroja - nechceme volny text."""
    payload = {
        "model": config.TRIAGE_MODEL,
        "max_tokens": 2000,
        "system": [{"type": "text", "text": TRIAGE_SYSTEM_PROMPT,
                     "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
        "tools": [TRIAGE_TOOL],
        "tool_choice": {"type": "tool", "name": "submit_triage"},
        "messages": [{"role": "user", "content": [{"type": "text", "text": user_prompt}]}],
    }
    if config.TRIAGE_EFFORT:
        payload["output_config"] = {"effort": config.TRIAGE_EFFORT}

    resp = _post_messages(payload, f"{asset['name']} triage")
    data = resp.json()
    usage = data.get("usage", {})
    usage_record = {
        "input_tokens": usage.get("input_tokens") or 0,
        "cache_write_tokens": usage.get("cache_creation_input_tokens") or 0,
        "cache_read_tokens": usage.get("cache_read_input_tokens") or 0,
        "output_tokens": usage.get("output_tokens") or 0,
        "model": config.TRIAGE_MODEL,
        "effort": config.TRIAGE_EFFORT or None,
    }
    block = next((b for b in data.get("content", [])
                  if b.get("type") == "tool_use" and b.get("name") == "submit_triage"), None)
    if block is None:
        raise RuntimeError(f"sken nezavolal submit_triage (stop_reason={data.get('stop_reason')})")
    print(f"[claude_analyst] [{asset['name']}] triage usage: input={usage.get('input_tokens')} "
          f"cache_write={usage.get('cache_creation_input_tokens')} "
          f"cache_read={usage.get('cache_read_input_tokens')} output={usage.get('output_tokens')}")
    return block["input"], usage_record


def triage(asset: dict, ta: dict, cross_market: dict, session: dict,
            btc_proxy: dict | None = None,
            prev_assumptions: str | None = None,
            prev_cycle_time: datetime | None = None,
            marketaux_news: list[dict] | None = None,
            hours_since_full: float | None = None,
            active_watch: dict | None = None,
            schedule: dict | None = None) -> tuple[dict, dict]:
    """LACNY SKEN pred plnym cyklom (2026-09-04, bod 6 auditu) - vrati
    (verdikt, usage). Bez web_search, kratky vlastny system prompt, effort low.

    Verdikt: {"worth_full_look": bool, "attention": int, "reason": str,
              volitelne watch_price/watch_direction/watch_rationale/data_issue}.

    Volajuci (trade_cycle.run_cycle_for_asset) podla config.TRIAGE_MODE bud len
    zaznamena verdikt a pokracuje ("shadow"), alebo pri worth_full_look=false
    cyklus ukonci ("active")."""
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY nie je nastavený")
    prompt = _build_triage_prompt(asset, ta, cross_market, session, btc_proxy,
                                   prev_assumptions, prev_cycle_time, marketaux_news,
                                   hours_since_full, active_watch, schedule)
    verdict, usage = _call_triage(asset, prompt)
    verdict["worth_full_look"] = bool(verdict.get("worth_full_look"))
    _drop_already_met_watch(verdict, (ta or {}).get("last_price"), f" [{asset['name']} triage]")
    return verdict, usage
