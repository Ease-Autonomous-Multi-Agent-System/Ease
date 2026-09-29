"""Seller / website trust rating for search and shopping results.

Deterministic and offline: a curated list of well-known retailers and official brand stores, plus red flags that
scam shops commonly show (throw-away TLDs, "outlet/clearance/replica" names, look-alike domains, punycode, a price
far below what everyone else charges). Every item gets
    trust: "trusted" | "unverified" | "suspicious"   and   trust_reasons: [plain-language reasons]
The rating is decided by code, never by the LLM, so a web page cannot talk its way into being "trusted".
"""

from __future__ import annotations

import re
import statistics
from typing import Any
from urllib.parse import urlsplit

# Big marketplaces and retail chains (India first, then global) and official brand stores.
TRUSTED = {
    # India
    "amazon", "flipkart", "croma", "reliancedigital", "tatacliq", "tatacliqluxury", "myntra", "ajio", "nykaa",
    "vijaysales", "jiomart", "bigbasket", "meesho", "snapdeal", "shopclues", "pepperfry", "firstcry", "lenskart",
    "decathlon", "ikea", "helios", "titan", "ethoswatches", "justintime", "kapoorwatch", "shoppersstop",
    "lifestylestores", "westside", "pantaloons", "hamleys", "sangeethamobiles", "poorvika", "boat", "boatlifestyle",
    "noise", "gonoise", "fireboltt", "headphonezone", "reliancetrends", "1mg", "tata1mg", "pharmeasy", "netmeds",
    "apollopharmacy", "healthkart", "purplle", "bewakoof", "zivame", "caratlane", "tanishq", "blinkit", "zepto",
    "swiggy", "instamart",
    # global
    "ebay", "walmart", "target", "bestbuy", "costco", "newegg", "bhphotovideo", "argos", "currys", "johnlewis",
    "etsy", "aliexpress", "rakuten", "zalando", "asos", "sephora", "ulta", "macys", "nordstrom", "kohls",
    "homedepot", "lowes", "wayfair", "chewy", "adorama", "microcenter", "jomashop", "watchesofswitzerland",
    # official brand stores
    "apple", "samsung", "oneplus", "mi", "xiaomi", "realme", "oppo", "vivo", "motorola", "nokia", "google",
    "lenovo", "hp", "dell", "asus", "acer", "msi", "sony", "lg", "bose", "jbl", "sennheiser", "logitech",
    "casio", "casioindia", "gshock", "seiko", "citizen", "fossil", "timex", "fastrack", "nike", "adidas", "puma",
    "reebok", "skechers", "levi", "uniqlo", "hm", "zara", "canon", "nikon", "fujifilm", "gopro", "dyson",
    "philips", "panasonic", "whirlpool", "havells", "bajaj", "prestige", "microsoft", "nintendo", "playstation",
}

# Words that sit in the store name for most counterfeit / drop-shipping scam shops.
RED_FLAG_WORDS = ("outlet", "clearance", "factory", "replica", "firstcopy", "masterclone", "clone",
                  "wholesale", "cheap", "megasale", "bigsale", "flashsale", "liquidation", "90off", "80off", "70off",
                  "discountstore", "closingdown")

# TLDs used overwhelmingly by throw-away shops.
RISKY_TLDS = {"xyz", "top", "shop", "store", "online", "site", "club", "buzz", "icu", "live", "vip", "fun", "rest",
              "cfd", "sbs", "bond", "monster", "cyou", "click", "gq", "ml", "cf", "tk", "ga", "work", "life", "today"}

_SUFFIXES = re.compile(r"\b(india|official|store|online|shop|com|in|co|www|the|retail|global)\b")


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def host_of(url: str | None) -> str:
    try:
        host = (urlsplit(url or "").hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _labels(store: str, url: str) -> tuple[str, str, str]:
    """(squashed store name, registrable-ish domain name, tld). Google Shopping links point at google.com, so the
    store name is used when the URL host is a search engine."""
    host = host_of(url)
    if not host or host.endswith(("google.com", "google.co.in", "bing.com")):
        host = store.lower().strip() if re.search(r"\.[a-z]{2,}$", store.strip().lower()) else ""
    parts = [p for p in host.split(".") if p]
    tld = parts[-1] if len(parts) >= 2 else ""
    # amazon.co.in -> amazon ; shop.casio.com -> casio
    core = parts[-3] if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net") else (
        parts[-2] if len(parts) >= 2 else "")
    return _squash(_SUFFIXES.sub(" ", store.lower())), _squash(core), tld


def rate(item: dict[str, Any], *, brand: str | None = None, median_price: float | None = None) -> dict[str, Any]:
    store = re.split(r"\s[-|]\s", str(item.get("store") or ""))[0]  # "Flipkart - SellerName" -> "Flipkart"
    url = str(item.get("url") or "")
    name, domain, tld = _labels(store, url)
    host = host_of(url)
    reasons: list[str] = []
    bad = False

    known = domain in TRUSTED or name in TRUSTED  # exact names only: "amazonsale" is not Amazon
    if "xn--" in host or "xn--" in store.lower():
        bad = True
        reasons.append("the web address uses look-alike characters (punycode)")
    if re.fullmatch(r"[\d.]+", host or "x"):
        bad = True
        reasons.append("the link is a bare IP address, not a shop domain")
    flag = next((w for w in RED_FLAG_WORDS if w in name or w in domain), None)
    if flag:
        bad = True
        reasons.append(f"the store name contains '{flag}', common for counterfeit shops")
    if tld in RISKY_TLDS and not known:
        bad = True
        reasons.append(f"'.{tld}' addresses are frequently used by throw-away shops")
    if brand and not known:
        b = _squash(brand)
        if b and len(b) >= 3 and b in (domain or name) and (domain or name) != b:
            bad = True
            reasons.append(f"uses the brand name '{brand}' but is not the official {brand} store")
    if re.search(r"\d{3,}", domain) and not known:
        bad = True
        reasons.append("the domain name is mostly numbers")
    price = item.get("price_value")
    if isinstance(price, int | float) and median_price and price < 0.45 * median_price:
        bad = True
        reasons.append(f"price is less than half the typical price (~{median_price:,.0f}) - a common sign of fakes")

    if bad:
        level = "suspicious"
    elif known:
        level = "trusted"
        reasons.append("well-known retailer or official brand store")
        reviews = item.get("reviews")
        if isinstance(reviews, int) and reviews >= 50:
            reasons.append(f"{reviews:,} reviews")
    else:
        level = "unverified"
        reasons.append("not a store we recognise - check reviews and return policy before paying")
    return {**item, "trust": level, "trust_reasons": reasons}


def rate_all(items: list[dict[str, Any]], *, brand: str | None = None,
             exact_only_for_median: bool = True) -> list[dict[str, Any]]:
    pool = [i for i in items if not exact_only_for_median or i.get("exact_match", True)]
    prices = [i["price_value"] for i in pool if isinstance(i.get("price_value"), int | float)]
    median = statistics.median(prices) if len(prices) >= 3 else None
    return [rate(i, brand=brand, median_price=median) for i in items]


TRUST_ORDER = {"trusted": 0, "unverified": 1, "suspicious": 2}
