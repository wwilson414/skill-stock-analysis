#!/usr/bin/env python3
"""
Stock Data Fetcher + Technical Indicator Calculator
Outputs structured JSON for Claude Code analysis.
No AI/LLM calls -- pure data + math.

Adjustment standard: All data sources in the degradation chain use forward-adjusted (qfq) prices, see _SOURCE_ADJUSTMENT comments for details.
If any source fails, immediately throw error and switch to next source; never silently degrade to non-adjusted data.

Data source priority (graceful degradation):
  A-share: Tushare Pro (if TUSHARE_TOKEN set) > THS Official API(if HITHINK_FINANCE_API_KEY set) > efinance > THS > akshare > yfinance
  HK:      efinance > akshare > Tencent Finance > yfinance
  US:      Tencent Finance > yfinance

News search priority (via --news flag):
  A-share:   akshare East Moneyindividual stock news (free, no key) > Tavily > SerpAPI > skip (use WebSearch in Claude)
  HK/US: Tavily (if TAVILY_API_KEY set) > SerpAPI (if SERPAPI_KEY set) > skip (use WebSearch in Claude)

Usage:
    python3 stock_data_fetcher.py --stocks "600519,TSLA,HK00700" [--days 120] [--news]

Environment variables (optional, for enhanced data):
    TUSHARE_TOKEN    - Tushare Pro token (free signup at tushare.pro)
    HITHINK_FINANCE_API_KEY - THS official finance data API key (issued via THS account at fuyao.aicubes.cn; provides A-share forward-adjusted quotes/valuation/financials/ticker search; official recommended variable name, shared by REST/MCP/CLI/Python; compatible aliases FUYAO_API_KEY / THS_API_KEY)
    TAVILY_API_KEY   - Tavily API key (1000 free calls/month)
    SERPAPI_KEY       - SerpAPI key (100 free calls/month)
"""

import os
import sys
import json
import argparse
import warnings
from datetime import datetime, timedelta

warnings.filterwarnings("ignore")

# Data source availability detection
_AVAILABLE_SOURCES = {}

def _check_source(name):
    """Lazy-check if a data source library is importable AND usable.

    Some installs (e.g. broken efinance) import at top level but lack the
    subpackage we actually call (efinance.stock), so verify the subpackage
    too instead of failing later at fetch time.
    """
    if name not in _AVAILABLE_SOURCES:
        if name == "ths":
            # THS uses only stdlib (urllib/json/re) — no pip package needed
            _AVAILABLE_SOURCES[name] = True
            return True
        try:
            mod = __import__(name)
            if name == "efinance":
                import importlib
                importlib.import_module("efinance.stock")
                if getattr(mod, "stock", None) is None:
                    raise ImportError("efinance.stock subpackage unavailable")
            _AVAILABLE_SOURCES[name] = True
        except Exception as e:
            _AVAILABLE_SOURCES[name] = False
            _log(f"Data source '{name}' unusable: {type(e).__name__}: {e}")
    return _AVAILABLE_SOURCES[name]

def _log(msg):
    """Log to stderr so it doesn't pollute JSON stdout."""
    print(f"[INFO] {msg}", file=sys.stderr)


# ============================================================
# SECTION 1: Stock Code Parser
# ============================================================

def classify_stock(code: str) -> tuple:
    """
    Returns (market, normalized_code, display_code)
    market: 'cn_a', 'cn_hk', 'us'
    """
    code = code.strip()
    upper = code.upper()

    # HK: HK00700 -> ('cn_hk', '00700', 'HK00700')
    if upper.startswith("HK") and upper[2:].isdigit():
        return ("cn_hk", upper[2:], upper)

    # A-share: 600519 -> ('cn_a', '600519', '600519')
    if upper.isdigit() and len(upper) == 6:
        return ("cn_a", upper, upper)

    # US: TSLA -> ('us', 'TSLA', 'TSLA')
    # Only accepts ASCII letters; Chinese names (e.g., "Kweichow Moutai") isalpha is also True，must be excluded,
    # Hand over to THS official ticker search (requires HITHINK_FINANCE_API_KEY) to parse; explicitly reject when no Key.
    if upper.isalpha() and upper.isascii() and 1 <= len(upper) <= 5:
        return ("us", upper, upper)

    # A-share with suffix: 600519.SH -> strip
    if "." in upper:
        base, suffix = upper.rsplit(".", 1)
        if suffix in ("SH", "SZ", "SS") and base.isdigit():
            return ("cn_a", base, base)

    # A-share with prefix: SH600519 -> strip
    if upper[:2] in ("SH", "SZ") and upper[2:].isdigit():
        return ("cn_a", upper[2:], upper[2:])

    return ("unknown", code, code)


def to_yfinance_code(code: str, market: str) -> str:
    """Convert to Yahoo Finance ticker format."""
    if market == "cn_hk":
        num = code.lstrip("0") or "0"
        return f"{num.zfill(4)}.HK"
    if market == "us":
        return code
    # A-share
    if code.startswith(("600", "601", "603", "688")):
        return f"{code}.SS"
    if code.startswith(("51", "52", "56", "58")):
        return f"{code}.SS"
    return f"{code}.SZ"


# ============================================================
# SECTION 2: Data Fetchers (with graceful degradation)
# ============================================================

def _df_to_ohlcv(df, days):
    """Convert a normalized DataFrame to OHLCV list."""
    import pandas as pd
    for c in ["open", "close", "high", "low", "volume", "amount", "pct_chg"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values("date").tail(days).reset_index(drop=True)
    ohlcv = []
    for _, row in df.iterrows():
        ohlcv.append({
            "date": str(row.get("date", "")),
            "open": _safe_float(row.get("open")),
            "high": _safe_float(row.get("high")),
            "low": _safe_float(row.get("low")),
            "close": _safe_float(row.get("close")),
            "volume": _safe_float(row.get("volume")),
            "amount": _safe_float(row.get("amount")),
            "pct_chg": _safe_float(row.get("pct_chg")),
        })
    return ohlcv


# --- Tushare Pro (Priority 0, needs TUSHARE_TOKEN) ---

def _fetch_tushare_a(code: str, days: int):
    """Fetch A-share via Tushare Pro (qfq forward-adjusted). Returns (ohlcv, source) or raises.

    Adjustment unified: must use pro_bar(adj='qfq'). pro.daily() produces non-adjusted data, on ex-dividend dates
    will produce false price jumps, contaminating MA/MACD/RSI/drawdown. When pro_bar fails, immediately throw error, let degradation chain
    switch to next forward-adjusted source, never silently degrade to non-adjusted.
    """
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise EnvironmentError("TUSHARE_TOKEN not set")
    import tushare as ts
    pro = ts.pro_api(token)
    ts_code = f"{code}.SH" if code.startswith(("600", "601", "603", "688")) else f"{code}.SZ"
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
    df = ts.pro_bar(ts_code=ts_code, api=pro, adj="qfq",
                    start_date=start_date, end_date=end_date)
    if df is None or df.empty:
        raise ValueError(f"Tushare returned no data for {code}")
    col_map = {
        "trade_date": "date", "open": "open", "close": "close",
        "high": "high", "low": "low", "vol": "volume",
        "amount": "amount", "pct_chg": "pct_chg",
    }
    df = df.rename(columns=col_map)
    df["date"] = df["date"].apply(lambda x: f"{x[:4]}-{x[4:6]}-{x[6:]}" if len(str(x)) == 8 else x)
    _log(f"[{code}] Using Tushare Pro (qfq forward-adjusted)")
    return _df_to_ohlcv(df, days), "tushare"


# --- efinance (Priority 1, free) ---

def _fetch_efinance_a(code: str, days: int):
    """Fetch A-share via efinance (EastMoney). Returns (ohlcv, source) or raises."""
    import efinance as ef
    df = ef.stock.get_quote_history(code, fqt=1)  # fqt=1: forward-adjusted (explicitly specified to prevent default value changes)
    if df is None or df.empty:
        raise ValueError(f"efinance returned no data for {code}")
    col_map = {
        "date": "date", "open": "open", "close": "close",
        "high": "high", "low": "low", "volume": "volume",
        "turnover": "amount", "change percent": "pct_chg",
    }
    df = df.rename(columns=col_map)
    _log(f"[{code}] Using efinance (qfq forward-adjusted)")
    return _df_to_ohlcv(df, days), "efinance"


def _fetch_efinance_hk(code: str, days: int):
    """Fetch HK stock via efinance."""
    import efinance as ef
    df = ef.stock.get_quote_history(code, stock_type="hk", fqt=1)  # fqt=1: forward-adjusted
    if df is None or df.empty:
        raise ValueError(f"efinance returned no data for HK{code}")
    col_map = {
        "date": "date", "open": "open", "close": "close",
        "high": "high", "low": "low", "volume": "volume",
        "turnover": "amount", "change percent": "pct_chg",
    }
    df = df.rename(columns=col_map)
    _log(f"[HK{code}] Using efinance (qfq forward-adjusted)")
    return _df_to_ohlcv(df, days), "efinance"


# --- THS/10jqka (Priority 2, free, zero-dependency via stdlib) ---
#
# Public JSONP endpoints of d.10jqka.com.cn (THSquotes). The hs_ prefix only
# covers A-shares; HK/US return 502 from this endpoint family, so THS is
# wired into the A-share chain only.

def _ths_http_get(url: str, timeout: int = 12, retries: int = 1) -> str:
    """GET a THS JSONP endpoint with browser-like headers.

    retries: number of extra attempts on failure. K-line paths pass
    retries=0 (fail fast — the graceful degradation chain catches errors);
    the low-volume realtime path may retry once.
    """
    import urllib.request
    last_err = None
    for _attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 "
                              "Safari/537.36",
                "Referer": "http://q.10jqka.com.cn/",
                "Accept": "*/*",
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            last_err = e
    raise last_err


def _ths_parse_jsonp(body: str) -> dict:
    """Strip the JSONP wrapper `func({...})` and return the inner dict."""
    import json
    import re
    m = re.match(r"^[^(]+\((.*)\)\s*;?\s*$", body, re.S)
    if not m:
        raise ValueError("THS returned unexpected (non-JSONP) response")
    return json.loads(m.group(1))


def _ths_parse_kline_rows(data_str: str) -> list:
    """Parse a THS kline payload into normalized OHLCV bars.

    Row format (CSV, semicolon separated):
      YYYYMMDD,open,high,low,close,volume,amount,pct_chg,...
    volume unit = lots (1 lot = 100 shares), amount unit = CNY.
    """
    bars = []
    for row in (data_str or "").split(";"):
        f = row.split(",")
        if len(f) < 7:
            continue
        try:
            date = f[0].strip()
            if len(date) != 8 or not date.isdigit():
                continue
            open_ = float(f[1]); high = float(f[2])
            low = float(f[3]); close = float(f[4])
            if min(open_, high, low, close) <= 0:
                continue
            bars.append({
                "date": f"{date[:4]}-{date[4:6]}-{date[6:]}",
                "open": _safe_float(open_),
                "high": _safe_float(high),
                "low": _safe_float(low),
                "close": _safe_float(close),
                "volume": _safe_float(f[5]),
                "amount": _safe_float(f[6]),
                "pct_chg": None,
            })
        except (ValueError, IndexError):
            continue
    return bars


def _fetch_ths_a(code: str, days: int) -> tuple:
    """Fetch A-share daily K-line from THS (10jqka).

    - last.js returns the ~140 most recent trading days in one request.
    - For longer ranges we merge year files (YYYY.js) backwards from the
      current year, each returning that whole year's daily bars. Year files
      occasionally 502; we skip-and-warn instead of failing hard.
    Returns (ohlcv, source). No API key and no third-party dependency.
    """
    base = f"http://d.10jqka.com.cn/v6/line/hs_{code}/01/"
    bars = []
    try:
        body = _ths_http_get(f"{base}last.js", retries=0)
        bars = _ths_parse_kline_rows(_ths_parse_jsonp(body).get("data", ""))
    except Exception as e:
        _log(f"[{code}] THS last.js failed: {e}")

    if len(bars) < days:
        year = datetime.now().year
        have_dates = {b["date"] for b in bars}
        fetched = 0
        while len(bars) < days and fetched < 6:  # cap ~6 years (~1500 bars)
            body = None
            try:
                body = _ths_http_get(f"{base}{year}.js", retries=0)
                ybars = _ths_parse_kline_rows(_ths_parse_jsonp(body).get("data", ""))
            except Exception as e:
                _log(f"[{code}] THS {year}.js unavailable: {e}")
            if body is not None:
                for b in ybars:
                    if b["date"] not in have_dates:
                        bars.append(b)
                        have_dates.add(b["date"])
            fetched += 1
            year -= 1

    if not bars:
        raise ValueError(f"THS returned no data for {code}")

    bars.sort(key=lambda b: b["date"])
    for i in range(1, len(bars)):
        prev, curr = bars[i - 1]["close"], bars[i]["close"]
        if prev and curr and prev > 0:
            bars[i]["pct_chg"] = round((curr - prev) / prev * 100, 2)

    _log(f"[{code}] Using THS (free, stdlib-only, {len(bars)} bars)")
    return bars[-days:], "ths"


def _fetch_realtime_ths(code: str) -> dict:
    """Fetch A-share realtime snapshot from THS today.js + last.js.

    today.js field IDs are undocumented, so we read price/volume/amount with
    best-effort guards and recompute change_pct against the last completed
    daily close from last.js — the math stays self-consistent even if some
    vendor field lands on a surprising value. When today.js is unavailable
    (after hours, endpoint flakiness, ...) we fall back to the latest daily
    bar from last.js so the caller still gets a usable snapshot.
    """
    base = f"http://d.10jqka.com.cn/v6/line/hs_{code}/01/"
    rt = {}
    price = None
    date_today = ""
    try:
        node = _ths_parse_jsonp(_ths_http_get(f"{base}today.js", timeout=10))
        node = node.get(f"hs_{code}") or node
        price = _safe_float(node.get("11"))
        if price:
            date_today = str(node.get("1") or "").replace("-", "")
            rt = {
                "name": node.get("name", code),
                "price": price,
                "high": _safe_float(node.get("8")),
                "low": _safe_float(node.get("9")),
                "volume": _safe_float(node.get("13")),
                "amount": _safe_float(node.get("19")),
            }
    except Exception as e:
        _log(f"[{code}] THS today.js failed: {e}")

    try:
        last = _ths_parse_jsonp(_ths_http_get(f"{base}last.js", timeout=10, retries=0))
        bars = _ths_parse_kline_rows(last.get("data", ""))
        if not bars:
            raise ValueError("empty kline rows")
        prev_close = None
        for b in reversed(bars):
            if not date_today or b["date"].replace("-", "") < date_today:
                prev_close = b["close"]
                break
        if not rt:
            # today.js unavailable / no live price — use latest daily bar
            rt = {
                "name": last.get("name") or code,
                "price": bars[-1]["close"],
                "high": bars[-1]["high"],
                "low": bars[-1]["low"],
                "volume": bars[-1]["volume"],
                "amount": bars[-1]["amount"],
            }
            price = bars[-1]["close"]
            prev_close = bars[-2]["close"] if len(bars) > 1 else None
        if prev_close is None and len(bars) > 1:
            prev_close = bars[-2]["close"]
        if prev_close and prev_close > 0 and price:
            rt["change_pct"] = round((price - prev_close) / prev_close * 100, 2)
    except Exception as e:
        _log(f"[{code}] THS last.js (realtime) failed: {e}")
    return rt


# --- THS Official API (fuyao.aicubes.cn, requires HITHINK_FINANCE_API_KEY, compatible with FUYAO_API_KEY / THS_API_KEY) ---
#
# Official structured finance data REST API: snake_case fields, includes change percent, supports forward/backward adjustment,
# also has valuation (PE/PB/PS/PCF), financial indicators, ticker search and other enhanced data. Only covers A-shares.
# When API Key is not configured, all functions are skipped directly, not affecting the zero-config degradation chain.

def _ths_api_key() -> str:
    """THS Official API Key.

    Official recommended variable name HITHINK_FINANCE_API_KEY (shared by REST/MCP/CLI/Python four endpoints);
    FUYAO_API_KEY / THS_API_KEY kept as compatible aliases.
    """
    return (
        os.environ.get("HITHINK_FINANCE_API_KEY")
        or os.environ.get("FUYAO_API_KEY")
        or os.environ.get("THS_API_KEY")
        or ""
    )


def _fuyao_get(path: str, params: dict = None) -> dict:
    """GET THS Official API, carries X-api-key authentication, returns ApiResponse envelope."""
    import json as _json
    import urllib.parse
    import urllib.request
    key = _ths_api_key()
    if not key:
        raise EnvironmentError("HITHINK_FINANCE_API_KEY not set (aliases FUYAO_API_KEY / THS_API_KEY also checked)")
    url = "https://fuyao.aicubes.cn" + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(url, headers={
        "X-api-key": key,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=12) as resp:
        return _json.loads(resp.read().decode("utf-8", errors="replace"))


def _fuyao_thscode(code: str) -> str:
    """A-share pure code -> complete thscode (600519 -> 600519.SH, 920xxx -> 920xxx.BJ)."""
    if code.startswith(("600", "601", "603", "605", "688", "689", "51", "52", "56", "58")):
        return f"{code}.SH"
    if code.startswith(("43", "83", "87", "88", "92")):
        return f"{code}.BJ"
    return f"{code}.SZ"


def _fetch_fuyao_a(code: str, days: int) -> tuple:
    """A-share historical K-line via THS Official API (forward-adjusted, fields have official semantics).

    Returns (ohlcv, source). Window span calculated by script based on days, official limit 10 years.
    """
    end_ms = int(datetime.now().timestamp() * 1000)
    start_ms = int((datetime.now() - timedelta(days=days * 2)).timestamp() * 1000)
    resp = _fuyao_get("/api/a-share/prices/historical", {
        "thscode": _fuyao_thscode(code),
        "interval": "1d",
        "start": start_ms,
        "end": end_ms,
        "adjust": "forward",
    })
    if resp.get("code") != 0:
        raise ValueError(f"THS Official API: {resp.get('message')}")
    items = (resp.get("data") or {}).get("item") or []
    bars = []
    for it in items:
        try:
            d = datetime.fromtimestamp(int(it["date_ms"]) / 1000)
        except (KeyError, TypeError, ValueError):
            continue
        bars.append({
            "date": d.strftime("%Y-%m-%d"),
            "open": _safe_float(it.get("open_price")),
            "high": _safe_float(it.get("high_price")),
            "low": _safe_float(it.get("low_price")),
            "close": _safe_float(it.get("close_price")),
            "volume": _safe_float(it.get("volume")),
            "amount": _safe_float(it.get("turnover")),
            "pct_chg": None,
        })
    if not bars:
        raise ValueError(f"THS Official API returned no data for {code}")
    bars.sort(key=lambda b: b["date"])
    for i in range(1, len(bars)):
        prev, curr = bars[i - 1]["close"], bars[i]["close"]
        if prev and curr and prev > 0:
            bars[i]["pct_chg"] = round((curr - prev) / prev * 100, 2)
    _log(f"[{code}] Using THS Official API (forward-adjusted, {len(bars)} bars)")
    return bars[-days:], "ths_api"


def _fetch_realtime_fuyao(code: str) -> dict:
    """A-share real-time quotes + valuation via THS Official API (snapshot includes official change percent).

    Quote snapshot does not include Chinese name and valuation, so call valuation snapshot again to supplement name / PE / PB / PS / PCF.
    """
    resp = _fuyao_get("/api/a-share/prices/snapshot", {"thscodes": _fuyao_thscode(code)})
    if resp.get("code") != 0:
        raise ValueError(f"THS Official API snapshot: {resp.get('message')}")
    items = (resp.get("data") or {}).get("item") or []
    if not items:
        return {}
    it = items[0]
    rt = {
        "name": code,
        "price": _safe_float(it.get("last_price")),
        "change_pct": _safe_float(it.get("price_change_ratio_pct")),
        "open": _safe_float(it.get("open_price")),
        "high": _safe_float(it.get("high_price")),
        "low": _safe_float(it.get("low_price")),
        "pre_close": _safe_float(it.get("prev_price")),
        "volume": _safe_float(it.get("volume")),
        "amount": _safe_float(it.get("turnover")),
    }
    try:
        vresp = _fuyao_get("/api/a-share/valuations/snapshot", {"thscodes": _fuyao_thscode(code)})
        vit = ((vresp.get("data") or {}).get("item") or [{}])[0]
        if vit:
            rt["name"] = vit.get("name") or rt["name"]
            rt["pe_ttm"] = _safe_float(vit.get("pe_ttm"))
            rt["pb_ratio"] = _safe_float(vit.get("pb_mrq"))
            rt["ps_ttm"] = _safe_float(vit.get("ps_ttm"))
            rt["pcf_ttm"] = _safe_float(vit.get("pcf_ttm"))
    except Exception:
        pass
    return rt


def _search_fuyao(query: str) -> list:
    """THS official ticker search: parse standard tickers by thscode / ticker / Chinese name.

    Returns [{"thscode", "ticker", "name", "market", ...}]; parse failure returns null list.
    """
    resp = _fuyao_get("/api/meta/tickers/search", {"q": query})
    if resp.get("code") != 0:
        return []
    return (resp.get("data") or {}).get("item") or []


# --- akshare (Priority 3, free) ---

def _fetch_akshare_a(code: str, days: int):
    """Fetch A-share via akshare (qfq forward-adjusted). Failure immediately throws error to degradation chain, never degrades to non-adjusted."""
    import akshare as ak
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
    df = ak.stock_zh_a_hist(symbol=code, period="daily",
                            start_date=start_date, end_date=end_date, adjust="qfq")
    if df is None or df.empty:
        raise ValueError(f"akshare returned no data for {code}")
    col_map = {
        "date": "date", "open": "open", "close": "close",
        "high": "high", "low": "low", "volume": "volume",
        "turnover": "amount", "change percent": "pct_chg",
    }
    df = df.rename(columns=col_map)
    _log(f"[{code}] Using akshare (qfq forward-adjusted)")
    return _df_to_ohlcv(df, days), "akshare"




def _fetch_akshare_hk(code: str, days: int):
    """Fetch HK stock via akshare (qfq forward-adjusted). Failure immediately throws error to degradation chain, never degrades to non-adjusted."""
    import akshare as ak
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
    df = ak.stock_hk_hist(symbol=code, period="daily",
                          start_date=start_date, end_date=end_date, adjust="qfq")
    if df is None or df.empty:
        raise ValueError(f"akshare returned no data for HK{code}")
    col_map = {
        "date": "date", "open": "open", "close": "close",
        "high": "high", "low": "low", "volume": "volume",
        "turnover": "amount", "change percent": "pct_chg",
    }
    df = df.rename(columns=col_map)
    _log(f"[HK{code}] Using akshare (qfq forward-adjusted)")
    return _df_to_ohlcv(df, days), "akshare"


# --- yfinance (Priority 4, free, fallback for all markets) ---

def _fetch_yfinance(code: str, market: str, days: int):
    """Fetch any stock via yfinance (universal fallback)."""
    import yfinance as yf
    yf_code = to_yfinance_code(code, market)
    ticker = yf.Ticker(yf_code)
    # auto_adjust=True: forward-adjusted caliber, unified with A-share qfq chain (old version yfinance defaults to False, must be explicit)
    hist = ticker.history(period=f"{days}d", auto_adjust=True)
    if hist is None or hist.empty:
        raise ValueError(f"yfinance returned no data for {yf_code}")
    ohlcv = []
    for idx, row in hist.iterrows():
        date_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
        ohlcv.append({
            "date": date_str,
            "open": _safe_float(row.get("Open")),
            "high": _safe_float(row.get("High")),
            "low": _safe_float(row.get("Low")),
            "close": _safe_float(row.get("Close")),
            "volume": _safe_float(row.get("Volume")),
            "amount": None, "pct_chg": None,
        })
    for i in range(1, len(ohlcv)):
        prev = ohlcv[i - 1]["close"]
        curr = ohlcv[i]["close"]
        # Both closes must be valid (not None) to compute pct_chg;
        # yfinance may return NaN rows (e.g. halted/no-volume days)
        # which _safe_float() converts to None.
        if prev is not None and curr is not None and prev > 0:
            ohlcv[i]["pct_chg"] = round((curr - prev) / prev * 100, 2)
    _log(f"[{code}] Using yfinance (free, fallback)")
    return ohlcv, "yfinance"


# --- Tencent Finance (free, zero-dependency via stdlib, stable domestic connection) ---
#
# Covers HK/US K-line (fqkline) and real-time quotes (qt.gtimg.cn). Under domestic network, East Money
# (efinance/akshare) connections are often reset, Yahoo (yfinance) correctly blocks mainland IPs with persistent 429,
# Tencent interface is a stable fallback that can be directly connected domestically. K-line row format is
# [date, open, close, high, low, volume, ...] (note OCLH order, not OHLC);
# Real-time quotes are GBK encoded, `~`-delimited field strings. No authentication, no official quota statement, please control frequency.

_QQ_US_SYMBOL_CACHE = {}


def _qq_http_get(url: str, timeout: int = 12) -> bytes:
    """GET a Tencent quote endpoint with browser-like headers (stdlib only)."""
    import urllib.request
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 "
                      "Safari/537.36",
        "Referer": "https://gu.qq.com/",
        "Accept": "*/*",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _qq_kline(symbol: str, days: int) -> list:
    """Fetch daily qfq OHLCV rows for a Tencent symbol (hk00700 / usTSLA.OQ)."""
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?param={symbol},day,,,{days},qfq")
    payload = json.loads(_qq_http_get(url).decode("utf-8", errors="replace"))
    if not isinstance(payload, dict) or payload.get("code") != 0:
        raise ValueError(f"tencent kline bad response for {symbol}")
    node = (payload.get("data") or {}).get(symbol) or {}
    rows = node.get("qfqday") or node.get("day") or []
    if not rows:
        raise ValueError(f"tencent kline returned no rows for {symbol}")
    ohlcv = []
    for row in rows:
        # row[6:] may be a dividend-info dict on HK qfq rows — ignore it.
        try:
            ohlcv.append({
                "date": str(row[0]),
                "open": _safe_float(row[1]),
                "close": _safe_float(row[2]),
                "high": _safe_float(row[3]),
                "low": _safe_float(row[4]),
                "volume": _safe_float(row[5]),
                "amount": None, "pct_chg": None,
            })
        except (IndexError, TypeError, ValueError):
            continue
    return [r for r in ohlcv if r["close"] is not None]


def _qq_add_pct_chg(ohlcv: list) -> list:
    """Compute daily pct_chg from consecutive closes (Tencent rows lack it)."""
    for i in range(1, len(ohlcv)):
        prev, curr = ohlcv[i - 1]["close"], ohlcv[i]["close"]
        if prev is not None and curr is not None and prev > 0:
            ohlcv[i]["pct_chg"] = round((curr - prev) / prev * 100, 2)
    return ohlcv


def _qq_us_symbol(code: str) -> str:
    """Resolve the US exchange suffix for Tencent symbols: .OQ(NASDAQ)/.N(NYSE)."""
    if code in _QQ_US_SYMBOL_CACHE:
        return _QQ_US_SYMBOL_CACHE[code]
    last_err = None
    for suffix in ("OQ", "N"):
        symbol = f"us{code}.{suffix}"
        try:
            if _qq_kline(symbol, 5):
                _QQ_US_SYMBOL_CACHE[code] = symbol
                return symbol
        except Exception as e:
            last_err = e
    raise last_err or ValueError(f"tencent: cannot resolve US symbol for {code}")


def _fetch_qq_hk(code: str, days: int):
    """Fetch HK stock via Tencent fqkline."""
    ohlcv = _qq_kline(f"hk{code}", days + 10)[-days:]
    if not ohlcv:
        raise ValueError(f"tencent returned no data for HK{code}")
    _log(f"[HK{code}] Using tencent/qq (free)")
    return _qq_add_pct_chg(ohlcv), "tencent"


def _fetch_qq_us(code: str, days: int):
    """Fetch US stock via Tencent fqkline."""
    symbol = _qq_us_symbol(code)
    ohlcv = _qq_kline(symbol, days + 10)[-days:]
    if not ohlcv:
        raise ValueError(f"tencent returned no data for {symbol}")
    _log(f"[{code}] Using tencent/qq (free, symbol={symbol})")
    return _qq_add_pct_chg(ohlcv), "tencent"


def _parse_qt_quote(body: str) -> dict:
    """Parse a qt.gtimg.cn realtime quote line (GBK, `~`-separated fields).

    Field layout (classic gtimg): 1=name 3=current price 4=previous close 5=today open 6=volume (lots)
    31=change amount 32=change% 33=high 34=low 37=turnover (10k) 38=turnover rate 39=P/E ratio
    44=circulating market cap (100M) 45=total market cap (100M) 46=P/B ratio — HK/US some fields may be null.
    Market cap fields ×1e8 converted to original currency absolute value, consistent with efinance/yfinance caliber.
    """
    import re
    m = re.search(r'="([^"]*)"', body)
    if not m:
        return {}
    f = m.group(1).split("~")

    def g(i):
        return f[i] if i < len(f) else ""

    price = _safe_float(g(3))
    if not price:
        return {}

    def raw(i):
        v = _safe_float(g(i))
        return v * 1e8 if v is not None else None

    return {
        "name": g(1),
        "price": price,
        "pre_close": _safe_float(g(4)),
        "open": _safe_float(g(5)),
        "volume": _safe_float(g(6)),
        "change_amount": _safe_float(g(31)),
        "change_pct": _safe_float(g(32)),
        "high": _safe_float(g(33)),
        "low": _safe_float(g(34)),
        "amount": _safe_float(g(37)),
        "turnover_rate": _safe_float(g(38)),
        "pe_ratio": _safe_float(g(39)),
        "circ_mv": raw(44),
        "total_mv": raw(45),
        "pb_ratio": _safe_float(g(46)),
        "realtime_source": "tencent",
    }


def _fetch_realtime_qt(symbol: str) -> dict:
    """Fetch one realtime quote from qt.gtimg.cn (GBK-encoded)."""
    body = _qq_http_get(f"https://qt.gtimg.cn/q={symbol}").decode("gbk", errors="replace")
    return _parse_qt_quote(body)


# --- Realtime quote fetchers ---

def _fetch_realtime_a(code: str) -> dict:
    """Fetch A-share realtime quote with fallback."""
    # Try THS Official API first (fastest, official fields + valuation; needs key)
    if _ths_api_key():
        try:
            rt = _fetch_realtime_fuyao(code)
            if rt.get("price"):
                return rt
        except Exception:
            pass
    # Try akshare spot (most reliable for realtime)
    if _check_source("akshare"):
        try:
            import akshare as ak
            spot_df = ak.stock_zh_a_spot_em()
            row = spot_df[spot_df["code"] == code]
            if not row.empty:
                r = row.iloc[0]
                return {
                    "name": str(r.get("name", code)),
                    "price": _safe_float(r.get("latest price")),
                    "change_pct": _safe_float(r.get("change percent")),
                    "change_amount": _safe_float(r.get("change amount")),
                    "volume": _safe_float(r.get("volume")),
                    "amount": _safe_float(r.get("turnover")),
                    "amplitude": _safe_float(r.get("amplitude")),
                    "turnover_rate": _safe_float(r.get("turnover rate")),
                    "pe_ratio": _safe_float(r.get("P/E ratio - dynamic")),
                    "pb_ratio": _safe_float(r.get("P/B ratio")),
                    "total_mv": _safe_float(r.get("total market cap")),
                    "circ_mv": _safe_float(r.get("circulating market cap")),
                    "high": _safe_float(r.get("high")),
                    "low": _safe_float(r.get("low")),
                    "open": _safe_float(r.get("today open")),
                    "pre_close": _safe_float(r.get("previous close")),
                    "volume_ratio": _safe_float(r.get("volume ratio")),
                }
        except Exception:
            pass
    # Try efinance
    if _check_source("efinance"):
        try:
            import efinance as ef
            qt = ef.stock.get_realtime_quotes([code])
            if qt is not None and not qt.empty:
                r = qt.iloc[0]
                return {
                    "name": str(r.get("stock name", code)),
                    "price": _safe_float(r.get("latest price")),
                    "change_pct": _safe_float(r.get("change percent")),
                }
        except Exception:
            pass
    # Try THS (free, stdlib-only; works when EastMoney endpoints are blocked)
    try:
        rt = _fetch_realtime_ths(code)
        if rt.get("price"):
            return rt
    except Exception:
        pass
    # yfinance fallback (works even when akshare/efinance endpoints are blocked)
    if _check_source("yfinance"):
        try:
            import yfinance as yf
            info = yf.Ticker(to_yfinance_code(code, "cn_a")).info
            if info:
                return {
                    "name": info.get("shortName") or info.get("longName") or code,
                    "price": _safe_float(info.get("currentPrice") or info.get("regularMarketPrice")),
                    "change_pct": _safe_float(info.get("regularMarketChangePercent")),
                    "pe_ratio": _safe_float(info.get("trailingPE")),
                    "pb_ratio": _safe_float(info.get("priceToBook")),
                    "total_mv": _safe_float(info.get("marketCap")),
                    "high": _safe_float(info.get("dayHigh")),
                    "low": _safe_float(info.get("dayLow")),
                    "open": _safe_float(info.get("regularMarketOpen")),
                    "pre_close": _safe_float(info.get("regularMarketPreviousClose")),
                    "week_52_high": _safe_float(info.get("fiftyTwoWeekHigh")),
                    "week_52_low": _safe_float(info.get("fiftyTwoWeekLow")),
                }
        except Exception:
            pass
    return {}


def _fetch_realtime_hk(code: str) -> dict:
    """Fetch HK realtime quote.

    Degradation chain consistent with fetch_hk historical quotes: efinance → akshare → yfinance.
    efinance.get_latest_quote fetches snapshot by single ticker (includes name/change percent/high low/PE/market cap),
    more stable than akshare HK full market snapshot (stock_hk_spot_em, fetches all tickers at once and easily rate-limited),
    so ranked first. Each source failure writes log (no longer silently pass and return null dict).
    """
    # Priority 1: efinance (single-code snapshot, rich fields)
    if _check_source("efinance"):
        try:
            import efinance as ef
            qt = ef.stock.get_latest_quote(code)
            if qt is not None and not qt.empty:
                r = qt.iloc[0]
                rt = {
                    "name": str(r.get("name", f"HK{code}")),
                    "price": _safe_float(r.get("latest price")),
                    "change_pct": _safe_float(r.get("change percent")),
                    "change_amount": _safe_float(r.get("change amount")),
                    "volume": _safe_float(r.get("volume")),
                    "amount": _safe_float(r.get("turnover")),
                    "turnover_rate": _safe_float(r.get("turnover rate")),
                    "volume_ratio": _safe_float(r.get("volume ratio")),
                    "pe_ratio": _safe_float(r.get("dynamic P/E ratio")),
                    "total_mv": _safe_float(r.get("total market cap")),
                    "circ_mv": _safe_float(r.get("circulating market cap")),
                    "high": _safe_float(r.get("high")),
                    "low": _safe_float(r.get("low")),
                    "open": _safe_float(r.get("today open")),
                    "pre_close": _safe_float(r.get("yesterdayclose")),
                    "realtime_source": "efinance",
                }
                if rt["price"]:
                    return rt
        except Exception as e:
            _log(f"[HK{code}] efinance real-time snapshot failed: {e}")

    # Priority 2: akshare East Money HK full market snapshot
    if _check_source("akshare"):
        try:
            import akshare as ak
            spot_df = ak.stock_hk_spot_em()
            matched = spot_df[spot_df["code"] == code]
            if not matched.empty:
                r = matched.iloc[0]
                return {
                    "name": str(r.get("name", f"HK{code}")),
                    "price": _safe_float(r.get("latest price")),
                    "change_pct": _safe_float(r.get("change percent")),
                    "volume": _safe_float(r.get("volume")),
                    "pe_ratio": _safe_float(r.get("P/E ratio")),
                    "pb_ratio": _safe_float(r.get("P/B ratio")),
                    "total_mv": _safe_float(r.get("total market cap")),
                    "realtime_source": "akshare",
                }
        except Exception as e:
            _log(f"[HK{code}] akshare real-time snapshot failed: {e}")

    # Priority 2.5: Tencent real-time quotes (free, stdlib-only, stable domestic connection)
    try:
        rt = _fetch_realtime_qt(f"hk{code}")
        if rt.get("price"):
            return rt
    except Exception as e:
        _log(f"[HK{code}] tencent real-time snapshot failed: {e}")

    # Priority 3: yfinance (works when EastMoney endpoints are blocked)
    if _check_source("yfinance"):
        try:
            import yfinance as yf
            info = yf.Ticker(to_yfinance_code(code, "cn_hk")).info
            if info:
                return {
                    "name": info.get("shortName") or info.get("longName") or f"HK{code}",
                    "price": _safe_float(info.get("currentPrice") or info.get("regularMarketPrice")),
                    "change_pct": _safe_float(info.get("regularMarketChangePercent")),
                    "volume": _safe_float(info.get("regularMarketVolume")),
                    "pe_ratio": _safe_float(info.get("trailingPE")),
                    "pb_ratio": _safe_float(info.get("priceToBook")),
                    "total_mv": _safe_float(info.get("marketCap")),
                    "high": _safe_float(info.get("dayHigh")),
                    "low": _safe_float(info.get("dayLow")),
                    "open": _safe_float(info.get("regularMarketOpen")),
                    "pre_close": _safe_float(info.get("regularMarketPreviousClose")),
                    "week_52_high": _safe_float(info.get("fiftyTwoWeekHigh")),
                    "week_52_low": _safe_float(info.get("fiftyTwoWeekLow")),
                    "realtime_source": "yfinance",
                }
        except Exception as e:
            _log(f"[HK{code}] yfinance real-time snapshotfailure: {e}")
    return {}


def _fetch_realtime_us(code: str) -> dict:
    """Fetch US realtime quote: yfinance (fields richer) → tencent (stable domestic connection)."""
    try:
        import yfinance as yf
        info = yf.Ticker(code).info
        if info and (info.get("currentPrice") or info.get("regularMarketPrice")):
            return {
                "name": info.get("shortName") or info.get("longName") or code,
                "price": _safe_float(info.get("currentPrice") or info.get("regularMarketPrice")),
                "change_pct": _safe_float(info.get("regularMarketChangePercent")),
                "volume": _safe_float(info.get("regularMarketVolume")),
                "pe_ratio": _safe_float(info.get("trailingPE")),
                "pb_ratio": _safe_float(info.get("priceToBook")),
                "total_mv": _safe_float(info.get("marketCap")),
                "high": _safe_float(info.get("dayHigh")),
                "low": _safe_float(info.get("dayLow")),
                "open": _safe_float(info.get("regularMarketOpen")),
                "pre_close": _safe_float(info.get("regularMarketPreviousClose")),
                "week_52_high": _safe_float(info.get("fiftyTwoWeekHigh")),
                "week_52_low": _safe_float(info.get("fiftyTwoWeekLow")),
                "avg_volume": _safe_float(info.get("averageVolume")),
                "dividend_yield": _safe_float(info.get("dividendYield")),
                "sector": info.get("sector", ""),
                "industry": info.get("industry", ""),
                "realtime_source": "yfinance",
            }
    except Exception as e:
        _log(f"[{code}] yfinance real-time snapshotfailure: {e}")
    # Tencent fallback (free, stdlib-only, stable domestic connection)
    # Note: Real-time quote symbols do not have exchange suffix (usTSLA), adding .OQ/.N will cause ticker not found;
    # K-line interface is the opposite, must have suffix (see _qq_us_symbol).
    try:
        rt = _fetch_realtime_qt(f"us{code}")
        if rt.get("price"):
            return rt
    except Exception as e:
        _log(f"[{code}] tencent real-time snapshot failed: {e}")
    return {}


# --- Priority router ---

# Adjustment caliber audit (2026-09 empirical verification): All sources in degradation chain use forward-adjusted (qfq),
# if any source fails, immediately throw error and switch to next source, never silently degrade to non-adjusted.
# - tushare:   pro_bar(adj='qfq')          (pro.daily() is non-adjusted, deprecated)
# - ths_api:   Official API adjust=forward forward-adjusted
# - efinance:  get_quote_history(fqt=1) forward-adjusted
# - ths:       d.10jqka.com.cn /01/ 段即forward-adjusted（verified point-by-point against Tencent qfq）
# - akshare:   stock_zh_a_hist / stock_hk_hist adjust='qfq'
# - tencent:   fqkline param=...,qfq
# - yfinance:  history(auto_adjust=True)
_SOURCE_ADJUSTMENT = "qfq (forward-adjusted)"


def fetch_cn_a(code: str, days: int) -> dict:
    """Fetch A-share with priority: Tushare > THS Official API (with key) > efinance > THS > akshare > yfinance."""
    ohlcv = None
    source = "unknown"
    errors = []

    # Priority 0: Tushare Pro (if token configured)
    if os.environ.get("TUSHARE_TOKEN") and _check_source("tushare"):
        try:
            ohlcv, source = _fetch_tushare_a(code, days)
        except Exception as e:
            errors.append(f"tushare: {e}")

    # Priority 1: THS Official API (requires HITHINK_FINANCE_API_KEY, forward-adjusted + structured fields)
    if ohlcv is None and _ths_api_key():
        try:
            ohlcv, source = _fetch_fuyao_a(code, days)
        except Exception as e:
            errors.append(f"ths_api: {e}")

    # Priority 2: efinance
    if ohlcv is None and _check_source("efinance"):
        try:
            ohlcv, source = _fetch_efinance_a(code, days)
        except Exception as e:
            errors.append(f"efinance: {e}")

    # Priority 3: THS (free, no pip dependency)
    if ohlcv is None:
        try:
            ohlcv, source = _fetch_ths_a(code, days)
        except Exception as e:
            errors.append(f"ths: {e}")

    # Priority 4: akshare
    if ohlcv is None and _check_source("akshare"):
        try:
            ohlcv, source = _fetch_akshare_a(code, days)
        except Exception as e:
            errors.append(f"akshare: {e}")

    # Priority 5: yfinance (universal fallback)
    if ohlcv is None and _check_source("yfinance"):
        try:
            ohlcv, source = _fetch_yfinance(code, "cn_a", days)
        except Exception as e:
            errors.append(f"yfinance: {e}")

    if ohlcv is None:
        raise ValueError(f"All data sources failed for A-share {code}: {'; '.join(errors)}")

    realtime = _fetch_realtime_a(code)
    name = realtime.get("name", code)
    return {"ohlcv": ohlcv, "realtime": realtime, "name": name, "source": source,
            "adjustment": _SOURCE_ADJUSTMENT, "errors": errors}


def fetch_hk(code: str, days: int) -> dict:
    """Fetch HK stock with priority: efinance > akshare > yfinance."""
    ohlcv = None
    source = "unknown"
    errors = []

    if _check_source("efinance"):
        try:
            ohlcv, source = _fetch_efinance_hk(code, days)
        except Exception as e:
            errors.append(f"efinance: {e}")

    if ohlcv is None and _check_source("akshare"):
        try:
            ohlcv, source = _fetch_akshare_hk(code, days)
        except Exception as e:
            errors.append(f"akshare: {e}")

    # Priority 2.5: Tencent (free, stdlib-only, stable domestic connection)
    if ohlcv is None:
        try:
            ohlcv, source = _fetch_qq_hk(code, days)
        except Exception as e:
            errors.append(f"tencent: {e}")

    if ohlcv is None and _check_source("yfinance"):
        try:
            ohlcv, source = _fetch_yfinance(code, "cn_hk", days)
        except Exception as e:
            errors.append(f"yfinance: {e}")

    if ohlcv is None:
        raise ValueError(f"All data sources failed for HK{code}: {'; '.join(errors)}")

    realtime = _fetch_realtime_hk(code)
    if not realtime.get("price") and ohlcv:
        # All real-time sources failed -> use last daily bar as fallback, ensuring dashboard still has current price/change percent (same as fetch_us)
        last = ohlcv[-1]
        realtime = {
            **realtime,
            "name": realtime.get("name") or f"HK{code}",
            "price": last["close"],
            "change_pct": last.get("pct_chg"),
            "realtime_source": "last_daily_bar",
        }
        errors.append("realtime: all quote sources failed, using last daily bar")
    name = realtime.get("name") or f"HK{code}"
    return {"ohlcv": ohlcv, "realtime": realtime, "name": name, "source": source,
            "adjustment": _SOURCE_ADJUSTMENT, "errors": errors}


def fetch_us(code: str, days: int) -> dict:
    """Fetch US stock: tencent (stable domestic connection) -> yfinance (fallback)."""
    ohlcv = None
    source = "unknown"
    errors = []

    # Priority 1: Tencent (free, stdlib-only, stable domestic connection)
    try:
        ohlcv, source = _fetch_qq_us(code, days)
    except Exception as e:
        errors.append(f"tencent: {e}")

    # Priority 2: yfinance (universal fallback)
    if ohlcv is None and _check_source("yfinance"):
        try:
            ohlcv, source = _fetch_yfinance(code, "us", days)
        except Exception as e:
            errors.append(f"yfinance: {e}")

    if ohlcv is None:
        raise ValueError(f"All data sources failed for US {code}: {'; '.join(errors)}")

    realtime = _fetch_realtime_us(code)
    if not realtime.get("price") and ohlcv:
        last = ohlcv[-1]
        realtime = {
            **realtime,
            "name": realtime.get("name") or code,
            "price": last["close"],
            "change_pct": last.get("pct_chg"),
            "realtime_source": "last_daily_bar",
        }
        errors.append("realtime: all quote sources failed, using last daily bar")
    name = realtime.get("name", code)
    return {"ohlcv": ohlcv, "realtime": realtime, "name": name, "source": source, "errors": errors}


# ============================================================
# SECTION 2.5: News Search (optional, with graceful degradation)
# ============================================================

def _fetch_news_akshare(code: str, max_results: int = 5) -> list:
    """A-share individual stock news via akshare East Money (stock_news_em). Free, no API Key required.

    Returns list of {"title", "content", "url", "date", "source", "publisher"},
    Sorted by release time descending. Only supports A-share 6-digit codes.
    """
    import akshare as ak
    df = ak.stock_news_em(symbol=code)
    if df is None or df.empty:
        return []
    rows = []
    for _, r in df.iterrows():
        try:
            rows.append({
                "title": str(r.get("news title", "")).strip(),
                "content": str(r.get("newscontent", "")).strip().replace("\n", " ")[:200],
                "url": str(r.get("newslink", "")).strip(),
                "date": str(r.get("releasetime", "")).strip(),
                "source": "akshare-em",
                "publisher": str(r.get("article source", "")).strip() or "East Money",
            })
        except Exception:
            continue
    rows.sort(key=lambda x: x["date"], reverse=True)
    return rows[:max_results]


def search_news(stock_name: str, code: str, max_results: int = 5, market: str = "cn_a") -> list:
    """
    Search news with priority:
      A-share: akshare East Moneyindividual stock news (free, no key) > Tavily > SerpAPI > empty
      HK/US: Tavily > SerpAPI > empty
    Returns list of {"title": ..., "content": ..., "url": ..., "date": ..., "source": ...}
    """
    # Priority 0: akshare East Money individual stock news (A-share exclusive, free, no key)
    if market == "cn_a" and _check_source("akshare"):
        try:
            results = _fetch_news_akshare(code, max_results)
            if results:
                _log(f"[{code}] News via akshare/East Money ({len(results)} results)")
                return results
        except Exception as e:
            _log(f"[{code}] akshare news failed: {e}")

    # Priority 1: Tavily
    tavily_key = os.environ.get("TAVILY_API_KEY")
    if tavily_key:
        try:
            from tavily import TavilyClient
            client = TavilyClient(api_key=tavily_key)
            query = f"{stock_name} {code} stock news"
            resp = client.search(query=query, max_results=max_results, search_depth="basic")
            results = []
            for r in resp.get("results", [])[:max_results]:
                results.append({
                    "title": r.get("title", ""),
                    "content": r.get("content", "")[:200],
                    "url": r.get("url", ""),
                    "date": r.get("published_date") or r.get("publishedDate"),
                    "source": "tavily",
                })
            if results:
                _log(f"[{code}] News via Tavily ({len(results)} results)")
                return results
        except Exception as e:
            _log(f"[{code}] Tavily failed: {e}")

    # Priority 1: SerpAPI
    serpapi_key = os.environ.get("SERPAPI_KEY")
    if serpapi_key:
        try:
            from serpapi import GoogleSearch
            params = {
                "engine": "google_news",
                "q": f"{stock_name} {code} stock news OR earnings OR announcement",
                "api_key": serpapi_key,
                "num": max_results,
                "hl": "zh-CN",
                "gl": "CN",
            }
            search = GoogleSearch(params)
            data = search.get_dict()
            results = []
            for r in data.get("organic_results", [])[:max_results]:
                results.append({
                    "title": r.get("title", ""),
                    "content": r.get("snippet", "") or r.get("body", "")[:200],
                    "url": r.get("link", ""),
                    "date": r.get("date"),  # Google News: relative time string like "3 days ago"
                    "source": "serpapi",
                })
            if results:
                _log(f"[{code}] News via Google News via SerpAPI ({len(results)} results)")
                return results
        except Exception as e:
            _log(f"[{code}] SerpAPI failed: {e}")

    # No news source available - return empty, let Claude use WebSearch
    _log(f"[{code}] No news source available, skipping (Claude will use WebSearch)")
    return []


# --- News structuring: dates + time decay + deterministic sentiment ---

def _parse_news_date(raw):
    """Parse a news date string into ISO 'YYYY-MM-DD' (or None).

    Handles absolute formats, ISO with timezone, and relative strings from
    Google News/Tavily ("3 days ago", "x hours ago", "yesterday"...).
    """
    if not raw:
        return None
    raw = str(raw).strip()
    fmts = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
            "%Y/%m/%d %H:%M:%S", "%Y/%m/%d", "%Y-%m-%d %H:%M", "%Y-%m-%d")
    for f in fmts:
        try:
            return datetime.strptime(raw, f).strftime("%Y-%m-%d")
        except ValueError:
            continue
    import re
    m = re.match(r"(\d{4}-\d{1,2}-\d{1,2})", raw)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            pass
    now = datetime.now()
    # relative English: "3 days ago", "2 hours ago", "5 months ago" ...
    m = re.search(r"(\d+)\s*(minutes?|hours?|days?|weeks?|months?)\s*ago", raw, re.I)
    if m:
        num, unit = int(m.group(1)), m.group(2).lower()
        delta = {"minute": timedelta(minutes=num), "hour": timedelta(hours=num),
                 "day": timedelta(days=num), "week": timedelta(weeks=num),
                 "month": timedelta(days=30 * num)}
        for k, v in delta.items():
            if unit.startswith(k):
                return (now - v).strftime("%Y-%m-%d")
        return None
    # relative Chinese: "3 days ago", "12 hours ago", "2 months ago" ...
    m = re.search(r"(\d+)\s*(minute|hour|day|week|month|month) ago", raw)
    if m:
        num, unit = int(m.group(1)), m.group(2)
        delta = {"minute": timedelta(minutes=num), "hour": timedelta(hours=num),
                 "day": timedelta(days=num), "week": timedelta(weeks=num),
                 "month": timedelta(days=30 * num), "month": timedelta(days=30 * num)}
        return (now - delta[unit]).strftime("%Y-%m-%d")
    if "yesterday" in raw.lower() or "yesterday" in raw:
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    if "today" in raw.lower() or "today" in raw:
        return now.strftime("%Y-%m-%d")
    return None


_NEWS_POSITIVE = [
    "positive", "pre-profit increase", "net profit growth", "net profit growth", "year-over-year growth", "beat expectations", "new high",
    "win bid", "selected", "signed", "signed", "major contract", "buyback", "increase holding", "dividend", "dividend",
    "cash dividend", "upgrade", "approved", "cooperation", "capacity expansion", "price increase", "price increase", "turn loss to profit", "profit",
]
_NEWS_NEGATIVE = [
    "negative", "pre-loss", "first loss", "loss", "net profit decline", "net profit decline", "year-over-year decline",
    "below expectations", "reduce holding", "plan to reduce", "liquidate", "unlock", "pledge", "freeze", "penalty",
    "fine", "case filed", "investigation", "warning letter", "regulatory letter", "violation", "illegal", "lawsuit", "arbitration",
    "goodwill impairment", "impairment provision", "delisting", "market maker", "downgrade", "terminate", "failure", "resignation",
    "departure", "under investigation", "inquiry",
]
_MAJOR_RISK_KEYWORDS = [
    "case filed", "investigation", "delisting", "penalty", "violation", "illegal", "goodwill impairment", "pre-loss",
    "pledge", "freeze", "market maker", "inquiry",
]
_NEWS_EVENT_RULES = [
    ("regulatory", ["penalty", "fine", "case filed", "investigation", "warning letter", "regulatory letter", "violation",
                    "illegal", "inquiry", "lawsuit", "arbitration", "delisting"]),
    ("shareholder_selling", ["reduce holding", "liquidate", "unlock", "pledge"]),
    ("buyback_holding", ["buyback", "increase holding"]),
    ("dividend", ["dividend", "dividend", "ex-rights", "ex-dividend", "cash dividend"]),
    ("earnings", ["pre-profit increase", "pre-loss", "quarterly report", "annual report", "interim report", "performance", "net profit",
                  "net profit", "turn loss to profit", "loss"]),
    ("ma_restructuring", ["acquisition", "merger", "restructuring", "backdoor listing", "private placement", "fundraising"]),
    ("contracts_growth", ["win bid", "selected", "signed", "signed", "orders", "cooperation",
                          "capacity expansion", "price increase", "price increase", "approved"]),
]


def analyze_news_sentiment(news_items: list) -> dict:
    """Deterministic news structuring (mutates items in place, returns summary).

    Per item adds: date (ISO), age_days, sentiment (positive/negative/neutral/
    mixed), event_type (list), major_risk (bool).
    Summary aggregates a time-decayed sentiment score in [-1, 1] with a
    14-day half-life (undated items capped at weight 0.5) — makes the 30%
    news weight reproducible instead of LLM-mood-dependent.
    """
    now = datetime.now()
    total_w = pos_w = neg_w = 0.0
    counts = {"positive": 0, "negative": 0, "neutral": 0, "mixed": 0}
    event_types = set()
    has_major_risk = False

    for it in news_items:
        text = f"{it.get('title', '')} {it.get('content', '')}"
        iso = _parse_news_date(it.get("date"))
        if iso:
            it["date"] = iso
            age = (now - datetime.strptime(iso, "%Y-%m-%d")).total_seconds() / 86400
            it["age_days"] = round(max(age, 0.0), 1)
        else:
            it["age_days"] = None

        pos = sum(1 for k in _NEWS_POSITIVE if k in text)
        neg = sum(1 for k in _NEWS_NEGATIVE if k in text)
        if neg and not pos:
            s = "negative"
        elif pos and not neg:
            s = "positive"
        elif pos and neg:
            s = "mixed"
        else:
            s = "neutral"
        it["sentiment"] = s
        it["major_risk"] = any(k in text for k in _MAJOR_RISK_KEYWORDS)
        evts = [name for name, kws in _NEWS_EVENT_RULES if any(k in text for k in kws)]
        it["event_type"] = evts or ["other"]
        event_types.update(evts)
        has_major_risk = has_major_risk or it["major_risk"]

        counts[s] = counts.get(s, 0) + 1
        w = 0.5 ** (it["age_days"] / 14.0) if it["age_days"] is not None else 0.5
        total_w += w
        if s == "positive":
            pos_w += w
        elif s == "negative":
            neg_w += w

    news_items.sort(key=lambda x: x.get("age_days") if x.get("age_days") is not None else 9999)

    sentiment_score = round((pos_w - neg_w) / total_w, 3) if total_w > 0 else 0.0
    if sentiment_score >= 0.15:
        label = "positive"
    elif sentiment_score <= -0.15:
        label = "negative"
    else:
        label = "neutral"
    return {
        "sentiment_score": sentiment_score,
        "sentiment_label": label,
        "counts": counts,
        "event_types": sorted(event_types),
        "has_major_risk": has_major_risk,
        "dated_items": sum(1 for it in news_items if it.get("age_days") is not None),
        "total_items": len(news_items),
        "stale": all(it.get("age_days") is None for it in news_items) and bool(news_items),
    }


# ============================================================
# SECTION 3: Technical Indicator Calculations
# ============================================================

def _safe_float(val) -> float:
    """Safely convert to float."""
    if val is None:
        return None
    try:
        import math
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, 4)
    except (ValueError, TypeError):
        return None


def calc_ema(data: list, period: int) -> list:
    """Calculate Exponential Moving Average."""
    if not data or len(data) < period:
        return [None] * len(data)
    result = [None] * (period - 1)
    multiplier = 2.0 / (period + 1)
    # First EMA = SMA of first 'period' values
    sma = sum(data[:period]) / period
    result.append(sma)
    for i in range(period, len(data)):
        ema = (data[i] - result[-1]) * multiplier + result[-1]
        result.append(ema)
    return result


def calc_ma(closes: list, periods: list) -> dict:
    """Calculate Simple Moving Averages."""
    result = {}
    for p in periods:
        key = f"MA{p}"
        if len(closes) >= p:
            ma_val = sum(closes[-p:]) / p
            result[key] = round(ma_val, 4)
        else:
            result[key] = None

    # MA alignment status
    ma5 = result.get("MA5")
    ma10 = result.get("MA10")
    ma20 = result.get("MA20")

    if all(v is not None for v in [ma5, ma10, ma20]):
        if ma5 > ma10 > ma20:
            result["alignment"] = "bullish"
            spread = (ma5 - ma20) / ma20 * 100 if ma20 > 0 else 0
            result["alignment_detail"] = "strong_bullish" if spread > 5 else "bullish"
        elif ma5 < ma10 < ma20:
            result["alignment"] = "bearish"
            spread = (ma20 - ma5) / ma20 * 100 if ma20 > 0 else 0
            result["alignment_detail"] = "strong_bearish" if spread > 5 else "bearish"
        elif ma5 > ma10 and ma10 <= ma20:
            result["alignment"] = "weak_bullish"
            result["alignment_detail"] = "weak_bullish"
        elif ma5 < ma10 and ma10 >= ma20:
            result["alignment"] = "weak_bearish"
            result["alignment_detail"] = "weak_bearish"
        else:
            result["alignment"] = "consolidation"
            result["alignment_detail"] = "consolidation"
    else:
        result["alignment"] = "insufficient_data"
        result["alignment_detail"] = "insufficient_data"

    return result


def calc_macd(closes: list, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """Calculate MACD: DIF, DEA, Histogram, and cross signals."""
    if len(closes) < slow + signal:
        return {"DIF": None, "DEA": None, "hist": None, "signal": "insufficient_data"}

    ema_fast = calc_ema(closes, fast)
    ema_slow = calc_ema(closes, slow)

    dif_list = []
    for i in range(len(closes)):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            dif_list.append(ema_fast[i] - ema_slow[i])
        else:
            dif_list.append(None)

    # DEA = EMA of DIF
    valid_dif = [d for d in dif_list if d is not None]
    if len(valid_dif) < signal:
        return {"DIF": None, "DEA": None, "hist": None, "signal": "insufficient_data"}

    dea_list = calc_ema(valid_dif, signal)

    # Current values
    curr_dif = valid_dif[-1] if valid_dif else None
    curr_dea = dea_list[-1] if dea_list else None
    prev_dif = valid_dif[-2] if len(valid_dif) >= 2 else None
    prev_dea = dea_list[-2] if len(dea_list) >= 2 else None

    hist = round((curr_dif - curr_dea) * 2, 4) if curr_dif is not None and curr_dea is not None else None

    # Cross signal detection
    macd_signal = "neutral"
    if all(v is not None for v in [curr_dif, curr_dea, prev_dif, prev_dea]):
        curr_diff = curr_dif - curr_dea
        prev_diff = prev_dif - prev_dea

        if prev_diff <= 0 and curr_diff > 0:
            macd_signal = "golden_cross_above_zero" if curr_dif > 0 else "golden_cross"
        elif prev_diff >= 0 and curr_diff < 0:
            macd_signal = "death_cross"
        elif curr_dif > 0 and curr_dea > 0:
            macd_signal = "bullish"
        elif curr_dif < 0 and curr_dea < 0:
            macd_signal = "bearish"

        # Zero axis cross
        if prev_dif is not None and curr_dif is not None:
            if prev_dif < 0 and curr_dif >= 0:
                macd_signal = "crossing_above_zero"
            elif prev_dif > 0 and curr_dif <= 0:
                macd_signal = "crossing_below_zero"

    return {
        "DIF": round(curr_dif, 4) if curr_dif is not None else None,
        "DEA": round(curr_dea, 4) if curr_dea is not None else None,
        "hist": hist,
        "signal": macd_signal,
    }


def calc_rsi(closes: list, periods: list) -> dict:
    """Calculate RSI using Wilder's method."""
    result = {}
    for period in periods:
        key = f"RSI{period}"
        if len(closes) < period + 1:
            result[key] = None
            continue

        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [max(0, d) for d in deltas]
        losses = [max(0, -d) for d in deltas]

        # First average
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        # Smoothed averages (Wilder's method)
        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))

        result[key] = round(rsi, 2)

    # RSI zone
    rsi12 = result.get("RSI12")
    if rsi12 is not None:
        if rsi12 >= 80:
            result["zone"] = "overbought"
        elif rsi12 >= 60:
            result["zone"] = "strong"
        elif rsi12 >= 40:
            result["zone"] = "neutral"
        elif rsi12 >= 20:
            result["zone"] = "weak"
        else:
            result["zone"] = "oversold"
    else:
        result["zone"] = "unknown"

    return result


def calc_pullback_context(closes: list, ma_data: dict) -> dict:
    """
    Classify where the stock sits in its medium-term structure, so that
    "low RSI / shrink pullback" is NOT blindly treated as oversold opportunity.

    Distinguishes:
    - uptrend_pullback:  pullback in uptrend (price above MA60, bullish alignment,
                         shallow decline from recent high) -> volume-contraction pullback/low RSI is credible
    - downtrend_decline: decline in downtrend (bearish alignment or price below MA60 and
                         continuously falling) -> low RSI indicates weak trend, not oversold opportunity
    - range_swing:       Range swing
    """
    if len(closes) < 25:
        return {"phase": "insufficient_data"}

    curr = closes[-1]
    window = closes[-120:] if len(closes) >= 120 else closes
    hi, lo = max(window), min(window)

    # Position within recent range: 0 = at low, 100 = at high
    range_pos_pct = round((curr - lo) / (hi - lo) * 100, 1) if hi > lo else 50.0

    # 20-day change (medium-term direction)
    base_20 = closes[-21] if len(closes) >= 21 else closes[0]
    chg_20d_pct = round((curr - base_20) / base_20 * 100, 2) if base_20 > 0 else None

    # Drawdown from the recent high (within the lookback window)
    off_high_pct = round((curr - hi) / hi * 100, 2) if hi > 0 else None

    # Distance from MA60 (medium-term trend anchor)
    ma60 = ma_data.get("MA60")
    dist_ma60_pct = round((curr - ma60) / ma60 * 100, 2) if (ma60 and ma60 > 0) else None

    alignment = ma_data.get("alignment", "consolidation")
    strong_bearish_set = {"bearish", "strong_bearish"}

    above_ma60 = dist_ma60_pct is not None and dist_ma60_pct > 0
    below_ma60 = dist_ma60_pct is not None and dist_ma60_pct < 0
    falling_20d = chg_20d_pct is not None and chg_20d_pct < -3
    shallow_pullback = off_high_pct is not None and -12 <= off_high_pct < 0

    # Downtrend dominates: clearly falling 20d, broken below MA60 while still
    # sinking, or strongly bearish MA alignment. Short MA5/MA10 crosses on a
    # 1-2 day dip are noise and must NOT alone flip the phase.
    if falling_20d or alignment in strong_bearish_set or (below_ma60 and chg_20d_pct is not None and chg_20d_pct < 0):
        phase = "downtrend_decline"
    elif above_ma60 and shallow_pullback and not falling_20d:
        phase = "uptrend_pullback"
    else:
        phase = "range_swing"

    return {
        "phase": phase,
        "range_pos_pct": range_pos_pct,
        "chg_20d_pct": chg_20d_pct,
        "off_high_pct": off_high_pct,
        "dist_ma60_pct": dist_ma60_pct,
    }


def calc_atr(bars: list, period: int = 14):
    """Wilder ATR from aligned OHLC bars. Returns None if insufficient data."""
    if len(bars) < period + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i].get("high"), bars[i].get("low"), bars[i - 1]["close"]
        if h is None or l is None or pc is None:
            continue
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return None
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def calc_risk_levels(valid_bars: list, closes: list, ma_data: dict) -> dict:
    """Volatility-aware stop/target levels with R:R, so the AI cannot hallucinate prices.

    - stop: structural level (MA20/20-day low) prioritized, but use ATR to add buffer and limit within
      within [current price-3ATR, current price-1ATR] range; use current price-2ATR when no structural level
    - target: recent 60-day high (resistance level), and at least current price+3ATR
    - rr_ratio = (target-close)/(close-stop), <1.5 buy points are hard-blocked at signal layer
    """
    out = {"atr": None, "atr_pct": None, "ann_vol_pct": None,
           "stop_structural": None, "stop_suggested": None,
           "target_suggested": None, "rr_ratio": None}
    curr = closes[-1]
    if curr is None or curr <= 0:
        return out

    atr = calc_atr(valid_bars, 14)

    # Annualized volatility from last 20 daily returns
    ann_vol = None
    if len(closes) >= 22:
        rets = []
        for i in range(len(closes) - 20, len(closes)):
            p0, p1 = closes[i - 1], closes[i]
            if p0 and p1 and p0 > 0:
                rets.append(p1 / p0 - 1)
        if len(rets) >= 15:
            mean = sum(rets) / len(rets)
            var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
            ann_vol = round(var ** 0.5 * 250 ** 0.5 * 100, 2)

    out["atr"] = round(atr, 4) if atr else None
    out["atr_pct"] = round(atr / curr * 100, 2) if atr else None
    out["ann_vol_pct"] = ann_vol

    if not atr or atr <= 0:
        return out

    # Structural stop: nearest support below price among MA20 / 20-day low
    ma20 = ma_data.get("MA20")
    low20 = min((b["low"] for b in valid_bars[-20:] if b.get("low") is not None),
                default=None)
    below = [x for x in (ma20, low20) if x is not None and x < curr]
    if below:
        structural = max(below)  # nearest support below price
        out["stop_structural"] = round(structural, 4)
        stop = structural - 0.25 * atr            # quarter-ATR buffer below structure
        stop = max(min(stop, curr - atr), curr - 3 * atr)  # clamp to [1ATR, 3ATR]
    else:
        stop = curr - 2 * atr
    out["stop_suggested"] = round(stop, 4)

    # Target: 60-day high (resistance), at least curr + 3 ATR
    high60 = max((b["high"] for b in valid_bars[-60:] if b.get("high") is not None),
                 default=None)
    target = max(x for x in (high60, curr + 3 * atr) if x is not None)
    out["target_suggested"] = round(target, 4)

    if stop < curr:
        out["rr_ratio"] = round((target - curr) / (curr - stop), 2)
    return out


_BENCHMARKS = {
    "cn_a": ("sh000300", "CSI 300"),
    "cn_hk": ("hkHSI", "Hang Seng Index"),
    "us": ("SPY", "S&P 500(SPY)"),
}
_BENCH_CACHE = {}


def _benchmark_closes(market: str, days: int = 140):
    """Benchmark index closes for RS. Tencent fqkline first, yfinance fallback.

    Non-fatal by design: returns None when all sources fail, and RS scoring
    then falls back to neutral. Results cached per market within one run.
    """
    if market not in _BENCHMARKS:
        return None
    if market in _BENCH_CACHE:
        return _BENCH_CACHE[market]

    symbol, _ = _BENCHMARKS[market]
    closes = None

    # Priority 1: tencent fqkline (stdlib only, qfq param harmless for indices)
    try:
        sym = _qq_us_symbol("SPY") if market == "us" else symbol
        bars = _qq_kline(sym, days)
        cl = [b["close"] for b in bars if b.get("close") is not None]
        if len(cl) >= 61:
            closes = cl
            _log(f"[benchmark] {symbol} via tencent ({len(cl)} bars)")
    except Exception as e:
        _log(f"[benchmark] tencent {symbol} failed: {type(e).__name__}: {e}")

    # Priority 2: yfinance
    if closes is None and _check_source("yfinance"):
        try:
            import yfinance as yf
            yf_sym = {"cn_a": "000300.SS", "cn_hk": "^HSI", "us": "SPY"}[market]
            hist = yf.Ticker(yf_sym).history(period=f"{days}d", auto_adjust=True)
            cl = [float(v) for v in hist["Close"].dropna().tolist()]
            if len(cl) >= 61:
                closes = cl
                _log(f"[benchmark] {yf_sym} via yfinance ({len(cl)} bars)")
        except Exception as e:
            _log(f"[benchmark] yfinance {market} failed: {type(e).__name__}: {e}")

    _BENCH_CACHE[market] = closes
    return closes


def calc_relative_strength(market: str, closes: list, bench_closes) -> dict:
    """RS vs benchmark: stock N-day return minus benchmark N-day return."""
    label = _BENCHMARKS.get(market, (None, "unknown"))[1]
    out = {"benchmark": label, "stock_ret_20d": None, "stock_ret_60d": None,
           "bench_ret_20d": None, "bench_ret_60d": None,
           "rs_20d": None, "rs_60d": None}

    def ret(vals, n):
        if vals and len(vals) > n and vals[-n - 1] and vals[-n - 1] > 0:
            return round((vals[-1] / vals[-n - 1] - 1) * 100, 2)
        return None

    out["stock_ret_20d"] = ret(closes, 20)
    out["stock_ret_60d"] = ret(closes, 60)

    if bench_closes:
        out["bench_ret_20d"] = ret(bench_closes, 20)
        out["bench_ret_60d"] = ret(bench_closes, 60)
        if out["stock_ret_20d"] is not None and out["bench_ret_20d"] is not None:
            out["rs_20d"] = round(out["stock_ret_20d"] - out["bench_ret_20d"], 2)
        if out["stock_ret_60d"] is not None and out["bench_ret_60d"] is not None:
            out["rs_60d"] = round(out["stock_ret_60d"] - out["bench_ret_60d"], 2)
    return out


def calc_tradability(realtime: dict, code: str, name: str = "") -> dict:
    """A-share limit status and buy executability (cannot buy at limit up, cannot sell stop-loss at limit down; T+1).

    Sector thresholds: STAR Market (688/689) and ChiNext (300/301/302) 20%, BSE (43/83/87/88/92) 30%,
    ST 5%, Main Board 10%. HK/US no limit restriction -> not_applicable.
    """
    chg = realtime.get("change_pct")
    if not (isinstance(code, str) and len(code) == 6 and code.isdigit()):
        return {"limit_status": "not_applicable", "limit_threshold_pct": None,
                "change_pct": chg}
    if code.startswith(("688", "689", "300", "301", "302")):
        th = 20.0
    elif code.startswith(("43", "83", "87", "88", "92")):
        th = 30.0
    else:
        th = 5.0 if "ST" in (name or "").upper() else 10.0

    if chg is None:
        status = "unknown"
    elif chg >= th - 0.2:
        status = "limit_up"
    elif chg <= -(th - 0.2):
        status = "limit_down"
    elif chg >= 0.8 * th:
        status = "near_limit_up"
    elif chg <= -0.8 * th:
        status = "near_limit_down"
    else:
        status = "normal"
    return {"limit_status": status, "limit_threshold_pct": th, "change_pct": chg}


def fetch_upcoming_unlocks(code: str, within_days: int = 60) -> list:
    """A-share lock-up unlock upcoming events via akshare (best-effort, non-fatal).

    Returns list of {"date": "YYYY-MM-DD", "pct_of_float": float|None, "detail": str}
    sorted by date, only events within [today, today+within_days]. [] on any failure.
    """
    if not _check_source("akshare"):
        return []
    import akshare as ak
    today = datetime.now().date()
    horizon = today + timedelta(days=within_days)

    for fn_name in ("stock_restricted_release_stockholder_em",
                    "stock_restricted_release_queue_sina"):
        fn = getattr(ak, fn_name, None)
        if fn is None:
            continue
        try:
            df = fn(symbol=code)
        except Exception:
            continue
        if df is None or getattr(df, "empty", True):
            continue
        out = []
        try:
            date_col = next((c for c in df.columns
                             if "date" in str(c) or "time" in str(c)), None)
            ratio_col = next((c for c in df.columns
                              if "ratio" in str(c) or "%" in str(c)), None)
            qty_col = next((c for c in df.columns if "quantity" in str(c)), None)
            if date_col is None:
                continue
            for _, r in df.iterrows():
                d = _parse_news_date(r.get(date_col))
                if not d:
                    continue
                dd = datetime.strptime(d, "%Y-%m-%d").date()
                if not (today <= dd <= horizon):
                    continue
                pct = None
                if ratio_col is not None:
                    v = _safe_float(r.get(ratio_col))
                    if v is not None and 0 < v <= 100:
                        pct = v
                out.append({"date": d, "pct_of_float": pct,
                            "detail": str(r.get(qty_col)) if qty_col is not None else ""})
        except Exception:
            continue
        if out:
            out.sort(key=lambda x: x["date"])
            _log(f"[{code}] unlocks via akshare/{fn_name}: {len(out)} within {within_days}d")
            return out
    _log(f"[{code}] no upcoming unlock data (akshare endpoints unavailable)")
    return []


def calc_volume_analysis(volumes: list, closes: list) -> dict:
    """Analyze volume patterns. Tolerates None volumes (aligned with valid_bars)."""
    if len(volumes) < 6 or len(closes) < 2:
        return {"vol_ratio": None, "trend": "insufficient_data"}

    # 5-day average volume (excluding today); skip None/zero entries
    prev5 = [v for v in volumes[-6:-1] if v]
    curr_vol = volumes[-1]

    avg_vol_5 = (sum(prev5) / len(prev5)) if prev5 else None
    vol_ratio = (round(curr_vol / avg_vol_5, 2)
                 if (curr_vol and avg_vol_5) else None)

    # Price change direction
    price_up = closes[-1] >= closes[-2]

    # Volume trend classification
    if vol_ratio is None:
        trend = "unknown"
    elif vol_ratio >= 1.5 and price_up:
        trend = "heavy_volume_up"
    elif vol_ratio >= 1.5 and not price_up:
        trend = "heavy_volume_down"
    elif vol_ratio <= 0.7 and not price_up:
        trend = "shrink_pullback"
    elif vol_ratio <= 0.7 and price_up:
        trend = "shrink_up"
    else:
        trend = "normal"

    return {"vol_ratio": vol_ratio, "trend": trend}


def calc_bias(closes: list, ma_data: dict) -> dict:
    """Calculate bias ratio (bias ratio)."""
    if not closes:
        return {}
    curr = closes[-1]
    result = {}
    for key in ["MA5", "MA10", "MA20"]:
        ma_val = ma_data.get(key)
        if ma_val and ma_val > 0:
            bias = round((curr - ma_val) / ma_val * 100, 2)
            result[f"bias_{key.lower()}"] = bias
    return result


def calc_support(closes: list, ma_data: dict) -> dict:
    """Check if price is supported by MA lines."""
    if not closes:
        return {"support_ma5": False, "support_ma10": False}
    curr = closes[-1]
    ma5 = ma_data.get("MA5")
    ma10 = ma_data.get("MA10")

    support_ma5 = False
    support_ma10 = False

    if ma5 and curr > 0:
        # Price within 1% of MA5
        support_ma5 = abs(curr - ma5) / curr * 100 <= 1.0
    if ma10 and curr > 0:
        support_ma10 = abs(curr - ma10) / curr * 100 <= 1.5

    return {"support_ma5": support_ma5, "support_ma10": support_ma10}


# ============================================================
# SECTION 4: Composite Trend Scoring (100 points)
# ============================================================

def calc_trend_score(ma_data: dict, macd_data: dict, rsi_data: dict,
                     vol_data: dict, bias_data: dict, support_data: dict,
                     context: dict = None) -> dict:
    """
    Composite scoring system (100 points total):
    - Trend/MA alignment: 26 pts
    - Bias (bias ratio): 20 points
    - Volume: 15 pts
    - MACD: 15 pts
    - RSI: 10 pts
    - Relative Strength (vs benchmark): 4 pts
    - Support: 10 pts

    Phase-aware: pullback context (calc_pullback_context) downgrades
    "shrink pullback / low RSI / below-MA5 bias" rewards when the stock is in
    a downtrend - those patterns are only buy signals inside an uptrend.

    Buy gates (hard): downtrend_decline phase, or risk/reward ratio < 1.5
    (context["rr_ratio"], from calc_risk_levels) — blocked from buy signals.
    """
    breakdown = {}
    context = context or {}
    phase = context.get("phase", "range_swing")
    in_downtrend = phase == "downtrend_decline"

    # 1. Trend score (26 pts)
    alignment = ma_data.get("alignment_detail", "consolidation")
    trend_scores = {
        "strong_bullish": 26, "bullish": 22, "weak_bullish": 16,
        "consolidation": 10, "weak_bearish": 7, "bearish": 3,
        "strong_bearish": 0, "insufficient_data": 10,
    }
    breakdown["trend"] = trend_scores.get(alignment, 10)

    # 2. Bias score (20 points) - prefer slightly below MA5, but only in uptrend
    bias_ma5 = bias_data.get("bias_ma5", 0)
    if in_downtrend:
        # In a downtrend, sitting below MA5 = continuing weakness, not a dip
        if bias_ma5 is None:
            breakdown["bias"] = 5
        elif -3 <= bias_ma5 < 0:
            breakdown["bias"] = 8
        elif 0 <= bias_ma5 < 2:
            breakdown["bias"] = 10
        elif 2 <= bias_ma5 < 5:
            breakdown["bias"] = 7   # dead-cat bounce overextension
        elif bias_ma5 >= 5:
            breakdown["bias"] = 2   # bounce far above MA5, likely to fail
        elif -5 <= bias_ma5 < -3:
            breakdown["bias"] = 5
        else:
            breakdown["bias"] = 2   # far below MA5 = falling knife
    else:
        if bias_ma5 is None:
            breakdown["bias"] = 10
        elif -3 <= bias_ma5 < 0:
            breakdown["bias"] = 20  # Slightly below MA5 = ideal dip
        elif 0 <= bias_ma5 < 2:
            breakdown["bias"] = 18  # Close to MA5
        elif 2 <= bias_ma5 < 5:
            breakdown["bias"] = 14  # Slightly above
        elif bias_ma5 >= 5:
            breakdown["bias"] = 4   # Too far above, don't chase
        elif -5 <= bias_ma5 < -3:
            breakdown["bias"] = 14  # Pulling back more
        else:
            breakdown["bias"] = 6   # Far below

    # 3. Volume score (15 pts) - shrink pullback only counts in an uptrend;
    #    in a downtrend, shrink price-drop is decline (bleeding), not pullback
    vol_trend = vol_data.get("trend", "normal")
    if in_downtrend:
        vol_scores = {
            "shrink_pullback": 4, "heavy_volume_up": 8, "normal": 6,
            "shrink_up": 8, "heavy_volume_down": 0, "insufficient_data": 5,
            "unknown": 5,
        }
    else:
        vol_scores = {
            "shrink_pullback": 15, "heavy_volume_up": 12, "normal": 10,
            "shrink_up": 6, "heavy_volume_down": 0, "insufficient_data": 8,
            "unknown": 8,
        }
    breakdown["volume"] = vol_scores.get(vol_trend, 8)

    # 4. MACD score (15 pts)
    macd_signal = macd_data.get("signal", "neutral")
    macd_scores = {
        "golden_cross_above_zero": 15, "crossing_above_zero": 13,
        "golden_cross": 12, "bullish": 10, "neutral": 7,
        "bearish": 3, "death_cross": 0, "crossing_below_zero": 1,
        "insufficient_data": 7,
    }
    breakdown["macd"] = macd_scores.get(macd_signal, 7)

    # 5. RSI score (10 pts) - low RSI is only "oversold opportunity" in an
    #    uptrend; in a downtrend it just means the trend is weak (falling knife)
    rsi_zone = rsi_data.get("zone", "neutral")
    if in_downtrend:
        rsi_scores = {
            "oversold": 4, "strong": 6, "neutral": 5,
            "weak": 2, "overbought": 3, "unknown": 5,
        }
    else:
        rsi_scores = {
            "oversold": 10, "strong": 8, "neutral": 5,
            "weak": 3, "overbought": 0, "unknown": 5,
        }
    breakdown["rsi"] = rsi_scores.get(rsi_zone, 5)

    # 6. Relative Strength score (4 pts) vs benchmark (CSI 300/Hang Seng Index/SPY)
    #    context["rs_60d"] = stock 60d return minus benchmark 60d return (pct)
    rs60 = context.get("rs_60d")
    if rs60 is None:
        breakdown["relative_strength"] = 2  # benchmark unavailable -> neutral
    elif rs60 >= 10:
        breakdown["relative_strength"] = 4  # strongly leading the market
    elif rs60 >= 0:
        breakdown["relative_strength"] = 3  # leading
    elif rs60 >= -5:
        breakdown["relative_strength"] = 2  # mildly lagging
    else:
        breakdown["relative_strength"] = 0  # badly lagging the market

    # 7. Support score (10 pts)
    sup_score = 0
    if support_data.get("support_ma5"):
        sup_score += 5
    if support_data.get("support_ma10"):
        sup_score += 5
    breakdown["support"] = sup_score

    total = sum(breakdown.values())

    # Signal generation
    alignment_val = ma_data.get("alignment", "consolidation")
    bullish_alignments = ["bullish", "strong_bullish", "weak_bullish"]

    # Hard gates: downtrend phase (pullback-like patterns are continuation),
    # poor risk/reward (rr_ratio < 1.5), limit-up (can't buy on A-shares, T+1),
    # and large upcoming unlock (>= 5% of float within 30 days).
    rr = context.get("rr_ratio")
    limit_status = context.get("limit_status") or "unknown"
    unlock_pct = context.get("unlock_pct_30d")

    buy_gates = []
    warnings = []
    if in_downtrend:
        buy_gates.append(f"phase={phase}: downtrend, pullback-like patterns are continuation")
    if rr is not None and rr < 1.5:
        buy_gates.append(f"rr_ratio={rr} < 1.5: risk/reward not justified")
    if limit_status == "limit_up":
        buy_gates.append("limit_up: limit up sealed, buy not executable today (T+1)")
    if unlock_pct is not None and unlock_pct >= 5:
        buy_gates.append(f"upcoming unlock {unlock_pct}% of float within 30d: large unlock")
    if limit_status == "limit_down":
        warnings.append("limit_down: limit down, stop-loss order may not fill today")
    elif limit_status == "near_limit_down":
        warnings.append("near_limit_down: Near limit down, watch for stop-loss slippage")
    if unlock_pct is not None and 3 <= unlock_pct < 5:
        warnings.append(f"upcoming unlock {unlock_pct}% of float within 30d")
    buy_blocked = bool(buy_gates)

    if total >= 75 and alignment_val in ["bullish", "strong_bullish"] and not buy_blocked:
        signal = "strong_buy"
    elif total >= 60 and alignment_val in bullish_alignments and not buy_blocked:
        signal = "buy"
    elif total >= 60 and alignment_val in bullish_alignments:
        signal = "hold"  # buy-grade setup but a hard gate fired: wait for repair
    elif total >= 45:
        signal = "hold"
    elif total >= 30:
        signal = "wait"
    elif alignment_val in ["bearish", "strong_bearish"]:
        signal = "strong_sell"
    else:
        signal = "sell"

    signal_cn = {
        "strong_buy": "Strong Buy", "buy": "Buy", "hold": "Hold",
        "wait": "Wait", "sell": "Sell", "strong_sell": "Strong Sell",
    }

    return {
        "total": total,
        "breakdown": breakdown,
        "signal": signal,
        "signal_cn": signal_cn.get(signal, signal),
        "buy_gates": buy_gates,
        "warnings": warnings,
    }


# ============================================================
# SECTION 5: Main Orchestrator
# ============================================================

def analyze_stock(code: str, days: int = 120, fetch_news: bool = False) -> dict:
    """Full analysis pipeline for a single stock."""
    market, normalized, display = classify_stock(code)

    # Chinese company name parsing: prioritize THS official ticker search for disambiguation (requires HITHINK_FINANCE_API_KEY)
    if market == "unknown" and _ths_api_key():
        try:
            hits = _search_fuyao(code)
            a_share = [h for h in hits if h.get("market") == "A-share" or str(h.get("thscode", "")).endswith((".SH", ".SZ", ".BJ"))]
            if a_share:
                thscode = a_share[0]["thscode"]
                normalized = thscode.rsplit(".", 1)[0]
                market = "cn_a"
                display = normalized
                _log(f"[{code}] THS Official API parsed as {thscode} ({a_share[0].get('name', '')})")
        except Exception as e:
            _log(f"[{code}] THS Official API ticker search failure: {e}")

    if market == "unknown":
        raise ValueError(f"Cannot classify stock code: {code}")

    # Fetch data with graceful degradation
    if market == "cn_a":
        raw = fetch_cn_a(normalized, days)
    elif market == "cn_hk":
        raw = fetch_hk(normalized, days)
    else:
        raw = fetch_us(normalized, days)

    ohlcv = raw["ohlcv"]
    if not ohlcv or len(ohlcv) < 10:
        raise ValueError(f"Insufficient data for {code}: only {len(ohlcv)} bars")

    # Date alignment: closes/volumes/recent_bars must be derived from the same valid bar sequence.
    # Previously compressing close and volume separately caused indicator windows to span non-consecutive trading days,
    # and misaligned dates between indicators and recent_bars.
    valid_bars = [b for b in ohlcv if b.get("close") is not None]
    closes = [b["close"] for b in valid_bars]
    volumes = [b.get("volume") for b in valid_bars]

    if len(closes) < 10:
        raise ValueError(f"Insufficient valid close prices for {code}")

    # Calculate all indicators
    ma = calc_ma(closes, [5, 10, 20, 60])
    macd = calc_macd(closes)
    rsi = calc_rsi(closes, [6, 12, 24])
    vol = calc_volume_analysis(volumes, closes)
    bias = calc_bias(closes, ma)
    support = calc_support(closes, ma)
    # Phase/position context: separates uptrend pullback from downtrend decline,
    # so "rallied recently then pulled back" is NOT auto-labeled oversold
    context = calc_pullback_context(closes, ma)
    # Volatility-aware stop/target with R:R (ATR-based, feeds buy gate)
    risk = calc_risk_levels(valid_bars, closes, ma)
    # A-share micro structure: limit status (affects buy executability) + upcoming unlock events (best-effort)
    tradability = calc_tradability(raw.get("realtime", {}), normalized, raw.get("name", ""))
    unlocks = fetch_upcoming_unlocks(normalized, 60) if market == "cn_a" else []
    unlock_pct_30d = None
    today_str = datetime.now().strftime("%Y-%m-%d")
    horizon30 = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    for u in unlocks:
        if today_str <= u["date"] <= horizon30 and u.get("pct_of_float") is not None:
            unlock_pct_30d = max(unlock_pct_30d or 0.0, u["pct_of_float"])
    # Relative strength vs market benchmark (non-fatal if benchmark unavailable)
    try:
        bench = _benchmark_closes(market)
    except Exception as e:
        _log(f"[{code}] benchmark fetch failed: {e}")
        bench = None
    rs = calc_relative_strength(market, closes, bench)
    # Feed RS, R:R, tradability, unlocks into scoring context (buy gates)
    context["rs_60d"] = rs.get("rs_60d")
    context["rr_ratio"] = risk.get("rr_ratio")
    context["limit_status"] = tradability.get("limit_status")
    context["unlock_pct_30d"] = unlock_pct_30d
    score = calc_trend_score(ma, macd, rsi, vol, bias, support, context)

    # News search (optional) - structured: dates + time-decayed sentiment
    news = []
    news_summary = None
    if fetch_news:
        stock_name = raw.get("name", display)
        news = search_news(stock_name, display, market=market)
        news_summary = analyze_news_sentiment(news)

    result = {
        "code": display,
        "market": market,
        "name": raw.get("name", display),
        "data_source": raw.get("source", "unknown"),
        "fetch_errors": raw.get("errors", []),
        "realtime": raw.get("realtime", {}),
        "indicators": {
            "ma": ma,
            "macd": macd,
            "rsi": rsi,
            "volume": vol,
            "bias": bias,
            "support": support,
            "context": context,
            "risk": risk,
            "relative_strength": rs,
            "tradability": tradability,
        },
        "events": {"upcoming_unlocks": unlocks, "unlock_pct_30d": unlock_pct_30d},
        "trend_score": score,
        "recent_bars": valid_bars[-10:],  # same sequence as indicators input, dates strictly aligned
        "as_of": valid_bars[-1].get("date"),  # Indicator calculation cutoff date
        "adjustment": raw.get("adjustment", "unknown"),
        "total_bars": len(ohlcv),
        "fetch_time": datetime.now().isoformat(),
    }
    if news:
        result["news"] = news
    if news_summary is not None:
        result["news_summary"] = news_summary
    return result


# ============================================================
# SECTION 6: Backtesting Framework (P3 - Score Validation & Calibration)
# ============================================================

_MIN_BARS_FOR_INDICATORS = 61
_SCORE_BUCKETS = [(0, 30, "0-30"), (30, 45, "30-45"), (45, 60, "45-60"),
                  (60, 75, "60-75"), (75, 200, "75+")]


def _forward_return(closes: list, idx: int, n: int):
    """Forward n-bar return from index idx (pct). None if insufficient future data."""
    if idx + n >= len(closes) or not closes[idx] or closes[idx] <= 0:
        return None
    future = closes[idx + n]
    return round((future / closes[idx] - 1) * 100, 4) if future else None


def _calc_rs_simple(stock_closes: list, bench_closes: list = None) -> dict:
    """Simplified RS for backtesting — returns shape calc_trend_score expects."""
    out = {"benchmark": "none", "rs_60d": None, "rs_20d": None}
    if not bench_closes or len(bench_closes) < 61:
        return out
    def ret(vals, n):
        if len(vals) > n and vals[-n - 1] and vals[-n - 1] > 0:
            return (vals[-1] / vals[-n - 1] - 1) * 100
        return None
    sr20, sr60 = ret(stock_closes, 20), ret(stock_closes, 60)
    br20, br60 = ret(bench_closes, 20), ret(bench_closes, 60)
    if sr20 is not None and br20 is not None:
        out["rs_20d"] = round(sr20 - br20, 2)
    if sr60 is not None and br60 is not None:
        out["rs_60d"] = round(sr60 - br60, 2)
    return out


def compute_signal_from_ohlcv(bars: list, benchmark_closes: list = None):
    """
    Compute signal from OHLCV bars only (no external data fetching).
    Used for backtesting where we walk through historical data.
    Returns dict with signal info, or None if insufficient data.
    """
    valid_bars = [b for b in bars if b.get("close") is not None]
    if len(valid_bars) < _MIN_BARS_FOR_INDICATORS:
        return None

    closes = [b["close"] for b in valid_bars]
    volumes = [b.get("volume") for b in valid_bars]

    ma = calc_ma(closes, [5, 10, 20, 60])
    macd_data = calc_macd(closes)
    rsi_data = calc_rsi(closes, [6, 12, 24])
    vol_data = calc_volume_analysis(volumes, closes)
    bias_data = calc_bias(closes, ma)
    support_data = calc_support(closes, ma)
    context = calc_pullback_context(closes, ma)
    risk = calc_risk_levels(valid_bars, closes, ma)
    rs_data = _calc_rs_simple(closes, benchmark_closes)

    # Feed RS and R:R into context (same pattern as analyze_stock)
    context["rs_60d"] = rs_data.get("rs_60d")
    context["rr_ratio"] = risk.get("rr_ratio")

    return calc_trend_score(ma, macd_data, rsi_data, vol_data, bias_data,
                            support_data, context)


def backtest_stock(code: str, days: int = 252, forward_days: list = None,
                   ohlcv_data: list = None, bench_closes: list = None) -> dict:
    """
    Walk through historical data day-by-day, generate signals at each point,
    and track forward returns for calibration.
    """
    if forward_days is None:
        forward_days = [5, 10, 20]

    if ohlcv_data is None:
        market, normalized, display = classify_stock(code)
        total_days = days + max(forward_days) + 10
        if market == "cn_a":
            raw = fetch_cn_a(normalized, total_days)
        elif market == "cn_hk":
            raw = fetch_hk(normalized, total_days)
        elif market == "us":
            raw = fetch_us(normalized, total_days)
        else:
            return {"code": code, "error": f"unknown_market: {market}"}
        ohlcv_data = raw.get("ohlcv", [])

    if len(ohlcv_data) < _MIN_BARS_FOR_INDICATORS + max(forward_days):
        return {"code": code, "error": "insufficient_data",
                "total_bars": len(ohlcv_data)}

    start_idx = _MIN_BARS_FOR_INDICATORS
    end_idx = len(ohlcv_data) - max(forward_days)
    all_closes = [b["close"] for b in ohlcv_data]

    signals = []
    for i in range(start_idx, end_idx):
        window = ohlcv_data[:i + 1]
        score = compute_signal_from_ohlcv(window, bench_closes)
        if score is None:
            continue

        closes = [b["close"] for b in window if b.get("close") is not None]
        if not closes:
            continue

        fwd_returns = {}
        for fd in forward_days:
            fr = _forward_return(all_closes, i, fd)
            if fr is not None:
                fwd_returns[f"{fd}d"] = fr

        signals.append({
            "date": ohlcv_data[i].get("date", f"idx_{i}"),
            "score_total": score["total"],
            "signal": score["signal"],
            "breakdown": score["breakdown"],
            "entry_price": closes[-1],
            "forward_returns": fwd_returns,
        })

    calib = _aggregate_calibration(signals, forward_days)
    calib["code"] = code
    calib["total_signals"] = len(signals)
    calib["lookback_bars"] = _MIN_BARS_FOR_INDICATORS
    calib["forward_days"] = forward_days
    return calib


def _pearson(xs, ys):
    """Pearson correlation coefficient."""
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    return round(num / (dx * dy), 4) if dx > 0 and dy > 0 else None


def _aggregate_calibration(signals: list, forward_days: list) -> dict:
    """Aggregate signals into calibration statistics."""
    if not signals:
        return {"error": "no_signals_generated"}

    # By signal type
    by_signal = {}
    for sig in signals:
        by_signal.setdefault(sig["signal"], []).append(sig)

    signal_stats = {}
    for signal_type, sigs in by_signal.items():
        stats = {"count": len(sigs)}
        for fd in forward_days:
            rets = [s["forward_returns"].get(f"{fd}d") for s in sigs
                    if s["forward_returns"].get(f"{fd}d") is not None]
            if rets:
                stats[f"fwd_{fd}d"] = {
                    "mean": round(sum(rets) / len(rets), 2),
                    "median": round(sorted(rets)[len(rets) // 2], 2),
                    "win_rate": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1),
                    "count": len(rets),
                }
        signal_stats[signal_type] = stats

    # By score bucket
    by_bucket = {}
    for sig in signals:
        total = sig["score_total"]
        bucket = next((b[2] for b in _SCORE_BUCKETS if b[0] <= total < b[1]), "unknown")
        by_bucket.setdefault(bucket, []).append(sig)

    bucket_stats = {}
    for bucket_name, sigs in by_bucket.items():
        stats = {"count": len(sigs)}
        for fd in forward_days:
            rets = [s["forward_returns"].get(f"{fd}d") for s in sigs
                    if s["forward_returns"].get(f"{fd}d") is not None]
            if rets:
                stats[f"fwd_{fd}d"] = {
                    "mean": round(sum(rets) / len(rets), 2),
                    "median": round(sorted(rets)[len(rets) // 2], 2),
                    "win_rate": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1),
                    "count": len(rets),
                }
        bucket_stats[bucket_name] = stats

    # Score-return correlation
    correlations = {}
    for fd in forward_days:
        pairs = [(s["score_total"], s["forward_returns"][f"{fd}d"])
                 for s in signals if s["forward_returns"].get(f"{fd}d") is not None]
        corr = _pearson([p[0] for p in pairs], [p[1] for p in pairs])
        if corr is not None:
            correlations[f"score_vs_{fd}d"] = corr

    # Component analysis
    component_corr = _component_correlation(signals, forward_days)

    return {
        "by_signal": signal_stats,
        "by_bucket": bucket_stats,
        "correlations": correlations,
        "component_correlations": component_corr,
    }


def _component_correlation(signals: list, forward_days: list) -> dict:
    """Which score components predict forward returns? Drives weight calibration."""
    components = ["trend", "bias", "volume", "macd", "rsi", "relative_strength", "support"]
    result = {}

    for comp in components:
        for fd in forward_days:
            pairs = [(s["breakdown"].get(comp, 0), s["forward_returns"][f"{fd}d"])
                     for s in signals
                     if s["breakdown"].get(comp) is not None
                     and s["forward_returns"].get(f"{fd}d") is not None]
            if len(pairs) < 3:
                continue
            corr = _pearson([p[0] for p in pairs], [p[1] for p in pairs])
            if corr is not None:
                result[f"{comp}_vs_{fd}d"] = corr

    sorted_comps = sorted(
        [(k, v) for k, v in result.items() if k.endswith("_vs_20d")],
        key=lambda x: abs(x[1]), reverse=True
    )
    result["ranked_for_20d"] = [{k: v} for k, v in sorted_comps]
    return result


def backtest_report(calib: dict) -> str:
    """Human-readable calibration report."""
    if "error" in calib:
        return f"Backtest error: {calib['error']}"

    lines = []
    lines.append(f"=== Backtest Calibration Report: {calib['code']} ===")
    lines.append(f"Total signals: {calib['total_signals']} | "
                 f"Lookback: {calib['lookback_bars']} bars | "
                 f"Forward horizons: {calib['forward_days']}d")
    lines.append("")

    lines.append("--- Score-Return Correlation ---")
    for k, v in calib["correlations"].items():
        lines.append(f"  {k}: {v}")
    lines.append("")

    lines.append("--- Forward Returns by Signal Type ---")
    for sig_type, stats in calib["by_signal"].items():
        lines.append(f"  {sig_type} (n={stats['count']}):")
        for fd in calib["forward_days"]:
            key = f"fwd_{fd}d"
            if key in stats:
                s = stats[key]
                lines.append(f"    {fd}d: mean={s['mean']}% median={s['median']}% "
                             f"win={s['win_rate']}% (n={s['count']})")
    lines.append("")

    lines.append("--- Forward Returns by Score Bucket ---")
    for bucket, stats in calib["by_bucket"].items():
        lines.append(f"  {bucket} (n={stats['count']}):")
        for fd in calib["forward_days"]:
            key = f"fwd_{fd}d"
            if key in stats:
                s = stats[key]
                lines.append(f"    {fd}d: mean={s['mean']}% median={s['median']}% "
                             f"win={s['win_rate']}% (n={s['count']})")
    lines.append("")

    lines.append("--- Component Correlation with 20d Forward Return ---")
    for item in calib["component_correlations"].get("ranked_for_20d", []):
        for comp, corr in item.items():
            lines.append(f"  {comp}: {corr}")
    lines.append("")

    lines.append("--- Calibration Suggestions ---")
    for s in _generate_calibration_suggestions(calib):
        lines.append(f"  - {s}")

    return "\n".join(lines)


def _generate_calibration_suggestions(calib: dict) -> list:
    """Actionable suggestions based on calibration results."""
    suggestions = []
    corrs = calib.get("correlations", {})
    score_corr = corrs.get("score_vs_20d")

    if score_corr is not None:
        if score_corr < 0.1:
            suggestions.append(
                f"Score-20d correlation is weak ({score_corr}). "
                "Current scoring system has low predictive power. "
                "Consider reweighting based on component correlations.")
        elif score_corr < 0.3:
            suggestions.append(
                f"Score-20d correlation is moderate ({score_corr}). "
                "System has some predictive power but room for improvement.")
        else:
            suggestions.append(
                f"Score-20d correlation is strong ({score_corr}). "
                "Scoring system is well-calibrated.")

    # Monotonicity check
    buckets = calib.get("by_bucket", {})
    prev_mean, monotonic = None, True
    for bn in ["0-30", "30-45", "45-60", "60-75", "75+"]:
        if bn in buckets:
            m = buckets[bn].get("fwd_20d", {}).get("mean")
            if m is not None:
                if prev_mean is not None and m < prev_mean:
                    monotonic = False
                prev_mean = m
    if not monotonic:
        suggestions.append(
            "Score buckets are NOT monotonically increasing in forward returns. "
            "Thresholds may need adjustment or weights recalibrated.")

    # Component-based suggestions
    ranked = calib.get("component_correlations", {}).get("ranked_for_20d", [])
    if ranked:
        top = list(ranked[0].items())[0]
        bottom = list(ranked[-1].items())[0]
        suggestions.append(
            f"Strongest predictor: {top[0]} (corr={top[1]}) — consider increasing its weight.")
        suggestions.append(
            f"Weakest predictor: {bottom[0]} (corr={bottom[1]}) — consider decreasing its weight.")

    # Buy/sell asymmetry
    by_sig = calib.get("by_signal", {})
    buy_rets = by_sig.get("buy", {}).get("fwd_20d", {}).get("mean")
    sell_rets = by_sig.get("sell", {}).get("fwd_20d", {}).get("mean")
    if buy_rets is not None and sell_rets is not None and buy_rets < sell_rets:
        suggestions.append(
            f"Buy signals underperform sell signals ({buy_rets}% vs {sell_rets}% 20d). "
            "Buy thresholds may be too loose or buy logic flawed.")

    return suggestions


def main():
    parser = argparse.ArgumentParser(description="Stock Data Fetcher")
    parser.add_argument("--stocks", required=True, help="Comma-separated stock codes")
    parser.add_argument("--days", type=int, default=120, help="History trading days")
    parser.add_argument("--news", action="store_true", help="Also search news (A-share via akshare/East Money free; HK/US needs TAVILY_API_KEY or SERPAPI_KEY)")
    parser.add_argument("--backtest", action="store_true", help="Run backtest calibration (walks through historical data, generates signals, tracks forward returns)")
    parser.add_argument("--backtest-days", type=int, default=252, help="Backtest lookback window in trading days (default 252)")
    parser.add_argument("--forward-days", type=str, default="5,10,20", help="Forward return horizons in trading days (comma-separated)")
    args = parser.parse_args()

    codes = [c.strip() for c in args.stocks.split(",") if c.strip()]
    results = []
    errors = []

    # Report available data sources
    sources_status = {}
    for lib in ["tushare", "efinance", "ths", "akshare", "yfinance"]:
        sources_status[lib] = "available" if _check_source(lib) else "not installed"
    sources_status["tencent"] = "available (stdlib)"
    sources_status["tushare_token"] = "configured" if os.environ.get("TUSHARE_TOKEN") else "not set"
    sources_status["ths_api"] = "configured" if _ths_api_key() else "not set"
    sources_status["tavily_api"] = "configured" if os.environ.get("TAVILY_API_KEY") else "not set"
    sources_status["serpapi"] = "configured" if os.environ.get("SERPAPI_KEY") else "not set"
    sources_status["news"] = (
        "akshare-em (A-shareindividual stock news, free)"
        if _check_source("akshare")
        else ("tavily" if os.environ.get("TAVILY_API_KEY")
              else ("serpapi" if os.environ.get("SERPAPI_KEY") else "none"))
    )
    _log(f"Data sources: {json.dumps(sources_status)}")

    # Backtest mode
    if args.backtest:
        forward_days = [int(x.strip()) for x in args.forward_days.split(",") if x.strip()]
        backtest_results = []
        for code in codes:
            try:
                _log(f"[backtest] Running calibration for {code}...")
                calib = backtest_stock(code, days=args.backtest_days,
                                       forward_days=forward_days)
                backtest_results.append(calib)
                # Print report to stderr for visibility
                _log(f"\n{backtest_report(calib)}")
            except Exception as e:
                errors.append({"code": code, "error": str(e), "type": type(e).__name__})

        output = {
            "analysis_date": datetime.now().strftime("%Y-%m-%d"),
            "mode": "backtest",
            "forward_days": forward_days,
            "stocks": backtest_results,
            "errors": errors,
            "total_requested": len(codes),
            "total_success": len(backtest_results),
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    for code in codes:
        try:
            result = analyze_stock(code, args.days, fetch_news=args.news)
            results.append(result)
        except Exception as e:
            errors.append({"code": code, "error": str(e), "type": type(e).__name__})

    output = {
        "analysis_date": datetime.now().strftime("%Y-%m-%d"),
        "analysis_time": datetime.now().strftime("%H:%M:%S"),
        "data_sources": sources_status,
        "stocks": results,
        "errors": errors,
        "total_requested": len(codes),
        "total_success": len(results),
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
