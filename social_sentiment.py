"""
Volitelny zdroj: nedavne prispevky z X (Twitter) k obchodovanym assetom
(NAS100/NVDA/ADA). X API v2 'recent search' vyzaduje platený tier pre
zmysluplny rate limit (Basic/Pro). Ak ENABLE_TWITTER=false, tento modul
jednoducho vrati [].
"""
import requests

import config

QUERIES = {
    "NAS100": '(NAS100 OR "Nasdaq 100" OR $QQQ OR $NDX OR $AAPL OR $MSFT OR $NVDA) lang:en -is:retweet',
    "NVDA": '($NVDA OR "Nvidia" OR "Jensen Huang") lang:en -is:retweet',
    "ADA": '($ADA OR "Cardano" OR "Strike Finance") lang:en -is:retweet',
}


def fetch_recent_posts(asset_name: str = "NAS100", max_results: int = 30) -> list[dict]:
    if not config.ENABLE_TWITTER or not config.TWITTER_BEARER_TOKEN:
        return []

    query = QUERIES.get(asset_name, QUERIES["NAS100"])
    headers = {"Authorization": f"Bearer {config.TWITTER_BEARER_TOKEN}"}
    params = {
        "query": query,
        "max_results": min(max(max_results, 10), 100),
        "tweet.fields": "created_at,public_metrics",
    }
    try:
        resp = requests.get(
            "https://api.twitter.com/2/tweets/search/recent",
            headers=headers, params=params, timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
    except requests.RequestException as e:
        print(f"[social_sentiment] chyba pri fetchi: {e}")
        return []

    return [
        {
            "text": t.get("text"),
            "created_at": t.get("created_at"),
            "likes": t.get("public_metrics", {}).get("like_count", 0),
            "retweets": t.get("public_metrics", {}).get("retweet_count", 0),
        }
        for t in data
    ]


if __name__ == "__main__":
    import json
    print(json.dumps(fetch_recent_posts(), indent=2))
