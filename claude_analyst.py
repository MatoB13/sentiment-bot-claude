"""
Zavola Claude (Anthropic API) s TA kontextom pre dany asset (NAS100/NVDA/ADA/GOLD/WTI/NIGHT).
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
import time
from datetime import datetime, timezone

import requests

import config
import market_data

# Prechodne infra chyby (Cloudflare/Anthropic docasne nedostupne) - bezpecne
# opakovat, kedze Messages API call nema ziadne vedlajsie ucinky (nehybe
# peniazmi, neotvara poziciu). 529 je Anthropic-ove vlastne "overloaded_error".
# Odstup 60s (nie povodnych 3s) - realny 529 vydrzal cez cele povodne ~6s okno
# (2026-07-31, XAU cyklus), minuta by mala prekryt bezny kratkodoby vypadok.
_RETRYABLE_STATUS = {502, 503, 504, 520, 521, 522, 523, 524, 529}
_MAX_API_RETRIES = 2
_API_RETRY_DELAY_SECONDS = 60

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
                    "Volitelne - len ked direction=none A skutocny blokujuci dovod je "
                    "konkretna CENOVA uroven (retest/breakout), ktoru by cenovy pohyb sam "
                    "vedel potvrdit. NEVYPLNAJ, ak je blokujuci dovod CASOVA UDALOST "
                    "(FOMC/CPI/NFP/PMI/earnings) - ziadny cenovy pohyb pred udalostou "
                    "neistotu nevyriesi, takze by to sposobilo zbytocne opakovane "
                    "mimoriadne cykly pri beznom trhovom sume. Vynechaj cely field, ak "
                    "nie je relevantny."
                ),
            },
            "watch_direction": {
                "type": "string", "enum": ["above", "below"],
                "description": "Volitelne, vzdy spolu s watch_price (rovnake pravidlo - len pre cenovo podmienene 'none').",
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
  konzervatívnejší (nízka confidence alebo "none")."""

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
}

# System prompt je rozdeleny na 2 cache_control bloky (viz _system_prompt_blocks nizsie):
#   1. SYSTEM_PROMPT_SHARED - vseobecna metodika, BYTE-IDENTICKA pre vsetkych 6 tickerov aj
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
nie všeobecné prehľady, ktoré ťa zavalia starším materiálom.

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
- watch_price/watch_direction (VOLITEĽNÉ, len ak direction="none"): nastav LEN ak je skutočný
  blokujúci dôvod tvojho "none" rozhodnutia konkrétna CENOVÁ úroveň (napr. čakáš na retest
  supportu/resistance, potvrdenie breakoutu) - teda niečo, čo by CENOVÝ POHYB samotný vedel
  vyriešiť. Toto spustí lacný poller sledujúci live cenu, ktorý ťa mimoriadne zavolá znova AK sa
  podmienka splní, namiesto čakania na ďalší pravidelný cyklus.
  NENASTAVUJ tieto polia, ak je skutočný blokujúci dôvod ČASOVÁ UDALOSŤ (napr. čakáš na FOMC/CPI/
  PPI/NFP/PMI report, earnings, alebo iný naplánovaný event) - v tom prípade žiadny cenový pohyb
  pred touto udalosťou tvoju neistotu nevyrieši, takže watch na cenu by bol zavádzajúci (spustil by
  sa pri bežnom trhovom šume/drifte, nie pri skutočnom potvrdení, a viedol by k zbytočným opakovaným
  mimoriadnym cyklom bez toho, aby sa čokoľvek reálne zmenilo). V takom prípade oba polia vynechaj
  úplne - počkaj na ďalší pravidelný cyklus alebo priamo na výsledok danej udalosti.
  Rovnako vynechaj oba polia, ak je direction="long"/"short" (pozícia sa už otvára).
  VŽDY, keď tieto polia nastavíš, MUSÍ `reasoning` explicitne a konkrétne uviesť, čo presne
  sledovaná podmienka znamená a čo by jej potvrdenie spustilo - napr. "sledujem breakdown pod
  0.1614, čo by potvrdilo pokračovanie downtrendu a otvorilo priestor pre short" alebo "čakám na
  retest 0.166 zospodu ako potvrdenie support-held pred long vstupom". Nestačí len skonštatovať,
  že rozsah/hladina "zostáva v platnosti" - vysvetli VZŤAH medzi watch_price/watch_direction a tým,
  čo by si pri jeho splnení urobil, zakaždým, nie len príležitostne.
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
- Po dokončení (prípadného) vyhľadávania zavolaj nástroj `submit_trade_decision` s finálnym
  rozhodnutím - to je jediný spôsob, ako rozhodnutie odovzdať.
"""


_VOLUME_NOTE = """
Sviečky obsahujú aj piaty údaj - `volume` (objem za danú hodinu, z {instrument}
futures/akciových dát, kompletné a spoľahlivé). Sleduj DIVERGENCIU medzi objemom
a cenovým pohybom: ak neobvykle vysoký objem (výrazne nad bežným objemom
posledných sviečok) nespôsobí zodpovedajúci pohyb ceny, alebo cena sa dokonca
otočí opačným smerom, môže to znamenať, že veľký hráč absorboval danú stranu
(predaj/nákup) - potenciálny signál vyčerpania/otočky (klasická "climax volume"
téza z Wyckoff/Volume Spread Analysis). Toto je len JEDEN vstup do tvojho
úsudku popri ostatných signáloch, nie mechanické pravidlo - vyžaduje kontext
(je objem naozaj neobvyklý, alebo len bežná variabilita)."""


_PER_ASSET_SYSTEM_APPENDIX_TEMPLATE = """Si skúsený intradenný analytik pre {label}.
Dostaneš technickú analýzu (TA) {instrument} - vrátane `recent_candles`, surových posledných
{candle_bars} hodinových sviečok {candle_format} - cross-market kontext, session
alignment{btc_proxy_note} a prípadne social-media sentiment. Máš k dispozícii nástroj web_search -
použi ho na vyhľadanie čerstvých {news_focus}, ktoré by mohli hýbať cenou v najbližších 24
hodinách. Vyhľadávaj len ak to dáva zmysel (max. niekoľko vyhľadávaní).
{volume_note}

Ako syntetizovať viacero signálov pre {instrument} (nepočítaj váhy mechanicky, posúď to ako
skúsený analytik):
{macro_rules}
"""


def _system_prompt_blocks(asset: dict) -> list[dict]:
    """System prompt ako 2 cache_control bloky (viz komentar nad SYSTEM_PROMPT_SHARED vyssie):
    zdielana metodika (rovnaka pre vsetkych 6 tickerov, ttl=1h) + per-asset dodatok (nazov/makro
    pravidla/candle format, tiez ttl=1h - pomaha aj bez zdielania medzi tickermi)."""
    text = ASSET_TEXT[asset["name"]]
    btc_proxy_note = ", krypto-makro proxy (BTC)" if asset.get("needs_btc_proxy") else ""
    include_volume = asset.get("include_volume", False)
    candle_format = "[open,high,low,close,volume]" if include_volume else "[open,high,low,close]"
    volume_note = _VOLUME_NOTE.format(instrument=asset["name"]) if include_volume else ""
    per_asset_text = _PER_ASSET_SYSTEM_APPENDIX_TEMPLATE.format(
        label=text["label"],
        instrument=asset["name"],
        news_focus=text["news_focus"],
        macro_rules=text["macro_rules"].format(instrument=asset["name"]),
        btc_proxy_note=btc_proxy_note,
        candle_bars=market_data.RECENT_CANDLES_BARS,
        candle_format=candle_format,
        volume_note=volume_note,
    )
    return [
        {"type": "text", "text": SYSTEM_PROMPT_SHARED,
         "cache_control": {"type": "ephemeral", "ttl": "1h"}},
        {"type": "text", "text": per_asset_text,
         "cache_control": {"type": "ephemeral", "ttl": "1h"}},
    ]


def _build_user_prompt(asset: dict, ta: dict, cross_market: dict, session: dict,
                        social: list[dict], btc_proxy: dict | None,
                        prev_assumptions: str | None,
                        prev_cycle_time: datetime | None = None,
                        retrospective_reflection: str | None = None,
                        new_stats_text: str | None = None,
                        fred_macro: dict | None = None,
                        eia_data: dict | None = None,
                        marketaux_news: list[dict] | None = None,
                        confidence_streak: dict | None = None) -> str:
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

    marketaux_block = ""
    if marketaux_news:
        articles = "\n".join(
            f"- [{a.get('published_at', '?')}] {a.get('title')} "
            f"(zdroj: {a.get('source')}, sentiment: {a.get('sentiment_score')})"
            for a in marketaux_news
        )
        marketaux_block = (
            f"\n## Najnovšie financne spravy so sentiment skore (Marketaux, NIE web_search)\n"
            f"{articles}\n"
            f"(sentiment skore je -1 az +1 na urovni konkretneho clanku, priamo od Marketaux, "
            f"nie tvoj vlastny odhad)\n"
        )

    return f"""## Aktuálny dátum a čas
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
{marketaux_block}

## Kľúčové predpoklady z predchádzajúceho cyklu (~{interval_h}h dozadu)
{prev_block}
{streak_block}
{retro_block}
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
            prev_cycle_time: datetime | None = None,
            retrospective_reflection: str | None = None,
            new_stats_text: str | None = None,
            fred_macro: dict | None = None,
            eia_data: dict | None = None,
            marketaux_news: list[dict] | None = None,
            confidence_streak: dict | None = None) -> tuple[dict, list[dict]]:
    """Vrati (decision, web_search_log). web_search_log je zoznam
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

    # cache_control na systemovom prompte aj user sprave: ak Claude narazi na
    # pause_turn (casto sa stava pri viacerych web_search volaniach), musime
    # poslat celu doterajsiu konverzaciu znova - bez cachovania by sa system
    # prompt + user sprava platili nanovo na plnu cenu pri kazdom pokracovani.
    # system_blocks samotne maju VLASTNY ttl=1h cache_control (viz
    # _system_prompt_blocks) - ten zdielany blok tak zostava teply naprieč
    # vsetkymi 6 tickermi (ADA/NIGHT bezia vzdy kazdu hodinu).
    messages = [{"role": "user",
                 "content": [{"type": "text",
                               "text": _build_user_prompt(asset, ta, cross_market, session, social,
                                                           btc_proxy, prev_assumptions, prev_cycle_time,
                                                           retrospective_reflection, new_stats_text,
                                                           fred_macro, eia_data, marketaux_news,
                                                           confidence_streak),
                               "cache_control": {"type": "ephemeral"}}]}]
    web_search_log: list[dict] = []

    # server-side web_search moze pri velmi dlhom hladani vratit stop_reason=pause_turn -
    # v takom pripade treba poslat konverzaciu znova a nechat ju dokoncit (max 1 pokracovanie).
    for _ in range(2):
        payload = {
            "model": config.CLAUDE_MODEL,
            "max_tokens": 8192,
            "system": system_blocks,
            "tools": [
                {"type": "web_search_20260209", "name": "web_search", "max_uses": 7},
                DECISION_TOOL,
            ],
            "messages": messages,
        }

        for attempt in range(_MAX_API_RETRIES + 1):
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": config.ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
                timeout=300,
            )
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
    Strike/Anthropic ho posiela sifrovany (encrypted_content), citame len metadata.

    Ak vyhladavanie ZLYHA (napr. rate limit na strane Anthropic), "content" nie
    je zoznam vysledkov ale dict/chyba - predtym sme to tichy zapisali ako
    prazdny zaznam bez naznaku PRECO (presne to sposobilo neistotu pri NAS100
    cykle 2026-07-24 11:49, kde vsetkych 5 pokusov skoncilo bez zdrojov). Teraz
    zapiseme aj "error" pole a vypiseme to do konzoly, aby sa to dalo diagnostikovat."""
    log = []
    pending_query = None
    for block in content_blocks:
        if block.get("type") == "server_tool_use" and block.get("name") == "web_search":
            pending_query = block.get("input", {}).get("query")
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
                print(f"[claude_analyst] web_search zlyhalo: {entry['error']} (query={pending_query!r})")
            log.append(entry)
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
