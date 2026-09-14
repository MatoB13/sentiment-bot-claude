"""Titulky z Google News (verejne RSS vyhladavanie) pre SLABO POKRYTE tickery -
vstup pre sken aj plny cyklus (2026-09-14, na ziadost pouzivatela).

PRECO: meranie 14.9. (7 dni, podiel skenov s aspon 1 titulkom PRIAMO o tickeri
z Marketaux + Benzinga + krypto RSS podla nazvu): MINIMAX, NEAR, NIGHT, PUMP 0 %,
ADA 6 %, ZHIPU 15 %, HYPE 30 %, CRCL 40 %, UNITREE 42 %, AAOI 50 %. Nase krypto
RSS (The Block, Decrypt, Cointelegraph, CryptoSlate) su plytke (~45 titulkov za
25 h, altcoiny v nich takmer nie su). V ten isty den Google News za 24 h:
ZHIPU 47 en + 43 zh (CNBC/WSJ/Bloomberg: akcie -10 % po 5 mld. USD emisii),
UNITREE 9 en + 25 zh, ADA 12, PUMP 8, HYPE 7, MINIMAX (zh) 9 - bot o tom nevedel.

ZAMIETNUTE ALTERNATIVY (overene 14.9.): CryptoPanic je plateny, CoinGecko news
len plan Analyst ($129/mes), CoinMarketCap content len Growth ($299/mes); RSS
blogov projektov 404 alebo prazdne.

AKO: vyhladavanie https://news.google.com/rss/search?q=... (bez kluca; z Railway
overene 14.9. - HTTP 200, ~0.3 s, bez presmerovania na suhlas). Dotazy su v
assets.py (GOOGLE_NEWS), pri cinskych firmach aj po cinsky. Google pripaja k
titulku " - Zdroj" - odstrani sa. Filtre:
  - titulok musi ticker SPOMINAT (regexy z assets) - vyhladavanie vracia aj clanky,
    kde je ticker len v texte,
  - rutinne titulky (cenove predpovede, "price analysis", "live share price",
    "fond X kupil akcie") sa vyhodia - su to SEO clanky bez udalosti,
  - duplicitne titulky (ta ista agenturna sprava vo viacerych mediach) raz.
NOVE = vyslo po poslednom plnom pohlade na ticker (ako Benzinga).

Neoficialne rozhranie bez zaruky - preto NIKDY nevyhodi vynimku, pri chybe vrati
[] a zapise stav (dashboard, matica zdrojov), a kesuje sa aj neuspech.
"""
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import requests

import config

_URL = "https://news.google.com/rss/search"
_TIMEOUT_SECONDS = 10
_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
_LOCALES = {"en": ("en-US", "US", "US:en"), "zh": ("zh-CN", "CN", "CN:zh-Hans")}

# SEO/rutinne titulky bez udalosti - namerane 14.9. na vzorke Google News.
_ROUTINE_RE = re.compile(
    r"price (prediction|forecast|analysis|today|chart)|predictions?\b.*\b20\d\d|"
    r"live (share )?price|share price[,:]? (news|quote)|stock price (today|quote)|"
    r"\((\w+ )?INR\)|technical analysis|key levels|"
    # MarketBeat & spol.: "X Purchases Shares of 671,514 ...", "$CRCL Shares Bought by ...",
    # "... Acquires New Stake in ...", "... Has $1.2 Million Position in ..." - NIE
    # "Alibaba raises stake in Zhipu" (to je udalost).
    r"shares? (bought|sold|acquired|purchased) by|(buys|sells|acquires|purchases) "
    r"(new )?(shares|stake|position) (of|in)\b|(purchases|buys|sells) [\d,.]+ shares|"
    r"has \$[\d.,]+ ?(million|thousand|billion)? (stake|position|holdings)|"
    r"shares in .{0,80}\b(bought|sold|acquired) by|"
    # platene tlacove spravy s reklamou na presale ("... While X Presale Pulls $10M")
    r"\bpre-?sale\b|"
    r"should you buy|how to buy|top \d+ |price movement explained|volatility explained|"
    r"股价行情|股票股价", re.I)

# Zdroje, ktore su len platene tlacove spravy / SEO (14.9.: openPR = presale reklamy
# s vymyslenymi cenami, napr. "Cardano Price Holds $0.20" pri realnych $0.58).
_BLOCKED_SOURCES = {"openpr.com", "openpr"}

# {asset_name: (fetched_at_epoch, [items])}
_cache: dict = {}
# {asset_name: stav posledneho vyberu} - do triage.google_news / cycle_logs.google_news
_last_status: dict = {}


def enabled() -> bool:
    return bool(config.GOOGLE_NEWS_ENABLED)


def covers(asset: dict) -> bool:
    return bool((asset.get("google_news") or {}).get("queries"))


def last_status(asset_name: str) -> dict | None:
    return _last_status.get(asset_name)


def is_routine(title: str) -> bool:
    return bool(_ROUTINE_RE.search(title or ""))


def _clean_title(title: str, source: str) -> str:
    title = (title or "").strip()
    if source and title.endswith(" - " + source):
        title = title[: -len(source) - 3].rstrip()
    return title


def _parse(xml_bytes: bytes) -> list[dict]:
    out = []
    root = ET.fromstring(xml_bytes)
    for item in root.iter("item"):
        src_el = item.find("source")
        source = (src_el.text or "").strip() if src_el is not None else ""
        title = _clean_title(item.findtext("title") or "", source)
        try:
            ts = parsedate_to_datetime(item.findtext("pubDate") or "")
        except (TypeError, ValueError):
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if title:
            out.append({"title": title, "source": source, "created": ts})
    return out


def _fetch(lang: str, query: str) -> list[dict]:
    hl, gl, ceid = _LOCALES.get(lang, _LOCALES["en"])
    q = f"{query} when:{max(1, int(config.GOOGLE_NEWS_QUERY_DAYS))}d"
    resp = requests.get(f"{_URL}?q={quote(q)}&hl={hl}&gl={gl}&ceid={ceid}",
                        headers={"User-Agent": _USER_AGENT}, timeout=_TIMEOUT_SECONDS)
    resp.raise_for_status()
    return _parse(resp.content)


def _raw_for_asset(asset: dict) -> tuple[list[dict], list[str], int]:
    """Stiahnute polozky vsetkych dotazov tickera (kesovane). Vrati (polozky, chyby, ok dotazov)."""
    name = asset["name"]
    now_ts = time.time()
    hit = _cache.get(name)
    if hit and now_ts - hit[0] < config.GOOGLE_NEWS_CACHE_MINUTES * 60:
        return hit[1], hit[2], hit[3]
    items, errors, ok = [], [], 0
    for lang, query in asset["google_news"]["queries"]:
        try:
            items.extend(_fetch(lang, query))
            ok += 1
        except Exception as e:
            errors.append(f"{lang}: {type(e).__name__}: {str(e)[:80]}")
            print(f"[google_news] [{name}] dotaz {lang} '{query}' zlyhal (pokracujem): {e}")
    _cache[name] = (now_ts, items, errors, ok)
    return items, errors, ok


def _match_re(asset: dict):
    pats = list(asset["google_news"].get("match") or []) or list(asset.get("news_keywords") or [])
    return re.compile("|".join(f"(?:{p})" for p in pats), re.I) if pats else None


def _norm(title: str) -> str:
    return re.sub(r"\W+", " ", title.lower()).strip()


def get_headlines_for_asset(asset: dict, since: datetime | None = None) -> list[dict]:
    """Titulky o tickeri od najnovsieho: {title, age_hours, source, new}.
    since = posledny plny pohlad (NOVE su po nom, ukazu sa do GOOGLE_NEWS_MAX_NEW_ITEMS
    aj ked su starsie nez GOOGLE_NEWS_MAX_AGE_HOURS); stare len doplnia do
    GOOGLE_NEWS_MAX_ITEMS. since=None = nove je vsetko do MAX_AGE_HOURS.
    [] ked je zdroj vypnuty, ticker nema dotazy alebo zdroj zlyhal."""
    if not enabled() or not covers(asset):
        return []
    raw, errors, ok = _raw_for_asset(asset)
    now = datetime.now(timezone.utc)
    if since is None:
        since = now - timedelta(hours=config.GOOGLE_NEWS_MAX_AGE_HOURS)
    elif since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    rx = _match_re(asset)
    seen, unique = set(), []
    for i in sorted(raw, key=lambda x: x["created"], reverse=True):
        if rx is not None and not rx.search(i["title"]):
            continue
        if is_routine(i["title"]) or (i["source"] or "").strip().lower() in _BLOCKED_SOURCES:
            continue
        key = _norm(i["title"])
        if key in seen:
            continue
        seen.add(key)
        age = max(0.0, (now - i["created"]).total_seconds() / 3600)
        new = i["created"] > since
        if not new and age > config.GOOGLE_NEWS_MAX_AGE_HOURS:
            continue
        unique.append({"title": i["title"], "age_hours": round(age, 2),
                       "source": i["source"], "new": new})
    new_items = [i for i in unique if i["new"]][:max(0, config.GOOGLE_NEWS_MAX_NEW_ITEMS)]
    old_items = [i for i in unique if not i["new"]]
    result = new_items + old_items[:max(0, config.GOOGLE_NEWS_MAX_ITEMS - len(new_items))]
    _last_status[asset["name"]] = {
        "ok": ok > 0, "queries_ok": ok, "queries_total": len(asset["google_news"]["queries"]),
        "errors": errors, "raw": len(raw), "count": len(result), "new_count": len(new_items),
        "since": since.isoformat(),
        "items": result,
    }
    return result
