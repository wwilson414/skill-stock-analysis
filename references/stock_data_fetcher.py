#!/usr/bin/env python3
"""
Stock Data Fetcher + Technical Indicator Calculator
Outputs structured JSON for Claude Code analysis.
No AI/LLM calls -- pure data + math.

Data source priority (graceful degradation):
  A-share: Tushare Pro (if TUSHARE_TOKEN set) > 同花顺官方API(if HITHINK_FINANCE_API_KEY set) > efinance > 同花顺(THS) > akshare > yfinance
  HK:      efinance > akshare > yfinance
  US:      yfinance (primary)

News search priority (via --news flag):
  A股:   akshare 东方财富个股新闻 (free, no key) > Tavily > SerpAPI > skip (use WebSearch in Claude)
  HK/US: Tavily (if TAVILY_API_KEY set) > SerpAPI (if SERPAPI_KEY set) > skip (use WebSearch in Claude)

Usage:
    python3 stock_data_fetcher.py --stocks "600519,TSLA,HK00700" [--days 120] [--news]

Environment variables (optional, for enhanced data):
    TUSHARE_TOKEN    - Tushare Pro token (free signup at tushare.pro)
    HITHINK_FINANCE_API_KEY - 同花顺官方金融数据API key (fuyao.aicubes.cn 用同花顺账号签发; 提供A股前复权行情/估值/财务/标的检索; 官方推荐变量名, REST/MCP/CLI/Python 共用; 兼容别名 FUYAO_API_KEY / THS_API_KEY)
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
            # 同花顺 uses only stdlib (urllib/json/re) — no pip package needed
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

    # 港股: HK00700 -> ('cn_hk', '00700', 'HK00700')
    if upper.startswith("HK") and upper[2:].isdigit():
        return ("cn_hk", upper[2:], upper)

    # A股: 600519 -> ('cn_a', '600519', '600519')
    if upper.isdigit() and len(upper) == 6:
        return ("cn_a", upper, upper)

    # 美股: TSLA -> ('us', 'TSLA', 'TSLA')
    # 仅接受 ASCII 字母；中文名(如"贵州茅台") isalpha 也为 True，须排除，
    # 交由同花顺官方标的检索(需 HITHINK_FINANCE_API_KEY)解析，无 Key 时明确拒绝。
    if upper.isalpha() and upper.isascii() and 1 <= len(upper) <= 5:
        return ("us", upper, upper)

    # 带后缀的A股: 600519.SH -> strip
    if "." in upper:
        base, suffix = upper.rsplit(".", 1)
        if suffix in ("SH", "SZ", "SS") and base.isdigit():
            return ("cn_a", base, base)

    # 带前缀的A股: SH600519 -> strip
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
    # A股
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
    """Fetch A-share via Tushare Pro. Returns (ohlcv, source) or raises."""
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise EnvironmentError("TUSHARE_TOKEN not set")
    import tushare as ts
    pro = ts.pro_api(token)
    ts_code = f"{code}.SH" if code.startswith(("600", "601", "603", "688")) else f"{code}.SZ"
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
    df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
    if df is None or df.empty:
        raise ValueError(f"Tushare returned no data for {code}")
    col_map = {
        "trade_date": "date", "open": "open", "close": "close",
        "high": "high", "low": "low", "vol": "volume",
        "amount": "amount", "pct_chg": "pct_chg",
    }
    df = df.rename(columns=col_map)
    df["date"] = df["date"].apply(lambda x: f"{x[:4]}-{x[4:6]}-{x[6:]}" if len(str(x)) == 8 else x)
    _log(f"[{code}] Using Tushare Pro (premium)")
    return _df_to_ohlcv(df, days), "tushare"


# --- efinance (Priority 1, free) ---

def _fetch_efinance_a(code: str, days: int):
    """Fetch A-share via efinance (EastMoney). Returns (ohlcv, source) or raises."""
    import efinance as ef
    df = ef.stock.get_quote_history(code)
    if df is None or df.empty:
        raise ValueError(f"efinance returned no data for {code}")
    col_map = {
        "日期": "date", "开盘": "open", "收盘": "close",
        "最高": "high", "最低": "low", "成交量": "volume",
        "成交额": "amount", "涨跌幅": "pct_chg",
    }
    df = df.rename(columns=col_map)
    _log(f"[{code}] Using efinance (free)")
    return _df_to_ohlcv(df, days), "efinance"


def _fetch_efinance_hk(code: str, days: int):
    """Fetch HK stock via efinance."""
    import efinance as ef
    df = ef.stock.get_quote_history(code, stock_type="hk")
    if df is None or df.empty:
        raise ValueError(f"efinance returned no data for HK{code}")
    col_map = {
        "日期": "date", "开盘": "open", "收盘": "close",
        "最高": "high", "最低": "low", "成交量": "volume",
        "成交额": "amount", "涨跌幅": "pct_chg",
    }
    df = df.rename(columns=col_map)
    _log(f"[HK{code}] Using efinance (free)")
    return _df_to_ohlcv(df, days), "efinance"


# --- 同花顺/10jqka (Priority 2, free, zero-dependency via stdlib) ---
#
# Public JSONP endpoints of d.10jqka.com.cn (同花顺行情). The hs_ prefix only
# covers A-shares; HK/US return 502 from this endpoint family, so 同花顺 is
# wired into the A-share chain only.

def _ths_http_get(url: str, timeout: int = 12, retries: int = 1) -> str:
    """GET a 同花顺 JSONP endpoint with browser-like headers.

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
        raise ValueError("同花顺 returned unexpected (non-JSONP) response")
    return json.loads(m.group(1))


def _ths_parse_kline_rows(data_str: str) -> list:
    """Parse a 同花顺 kline payload into normalized OHLCV bars.

    Row format (CSV, semicolon separated):
      YYYYMMDD,open,high,low,close,volume,amount,pct_chg,...
    volume unit = 手 (1手 = 100 shares), amount unit = 元.
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
    """Fetch A-share daily K-line from 同花顺 (10jqka).

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
        _log(f"[{code}] 同花顺 last.js failed: {e}")

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
                _log(f"[{code}] 同花顺 {year}.js unavailable: {e}")
            if body is not None:
                for b in ybars:
                    if b["date"] not in have_dates:
                        bars.append(b)
                        have_dates.add(b["date"])
            fetched += 1
            year -= 1

    if not bars:
        raise ValueError(f"同花顺 returned no data for {code}")

    bars.sort(key=lambda b: b["date"])
    for i in range(1, len(bars)):
        prev, curr = bars[i - 1]["close"], bars[i]["close"]
        if prev and curr and prev > 0:
            bars[i]["pct_chg"] = round((curr - prev) / prev * 100, 2)

    _log(f"[{code}] Using 同花顺 (free, stdlib-only, {len(bars)} bars)")
    return bars[-days:], "ths"


def _fetch_realtime_ths(code: str) -> dict:
    """Fetch A-share realtime snapshot from 同花顺 today.js + last.js.

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
        _log(f"[{code}] 同花顺 today.js failed: {e}")

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
        _log(f"[{code}] 同花顺 last.js (realtime) failed: {e}")
    return rt


# --- 同花顺官方 API (fuyao.aicubes.cn, 需 HITHINK_FINANCE_API_KEY, 兼容 FUYAO_API_KEY / THS_API_KEY) ---
#
# 官方结构化金融数据 REST API：snake_case 字段、自带涨跌幅、支持前/后复权，
# 并有估值(PE/PB/PS/PCF)、财务指标、标的检索等增强数据。仅覆盖 A 股。
# 未配置 API Key 时所有函数直接跳过，不影响零配置降级链。

def _ths_api_key() -> str:
    """同花顺官方 API Key。

    官方推荐变量名 HITHINK_FINANCE_API_KEY（REST/MCP/CLI/Python 四端共用）；
    FUYAO_API_KEY / THS_API_KEY 作为兼容别名保留。
    """
    return (
        os.environ.get("HITHINK_FINANCE_API_KEY")
        or os.environ.get("FUYAO_API_KEY")
        or os.environ.get("THS_API_KEY")
        or ""
    )


def _fuyao_get(path: str, params: dict = None) -> dict:
    """GET 同花顺官方 API，携带 X-api-key 鉴权，返回 ApiResponse 信封。"""
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
    """A股纯代码 -> 完整 thscode（600519 -> 600519.SH，920xxx -> 920xxx.BJ）。"""
    if code.startswith(("600", "601", "603", "605", "688", "689", "51", "52", "56", "58")):
        return f"{code}.SH"
    if code.startswith(("43", "83", "87", "88", "92")):
        return f"{code}.BJ"
    return f"{code}.SZ"


def _fetch_fuyao_a(code: str, days: int) -> tuple:
    """A股历史 K 线 via 同花顺官方 API（前复权，字段自带官方语义）。

    返回 (ohlcv, source)。窗口跨度由脚本按 days 推算，官方上限 10 年。
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
        raise ValueError(f"同花顺官方API: {resp.get('message')}")
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
        raise ValueError(f"同花顺官方API returned no data for {code}")
    bars.sort(key=lambda b: b["date"])
    for i in range(1, len(bars)):
        prev, curr = bars[i - 1]["close"], bars[i]["close"]
        if prev and curr and prev > 0:
            bars[i]["pct_chg"] = round((curr - prev) / prev * 100, 2)
    _log(f"[{code}] Using 同花顺官方API (前复权, {len(bars)} bars)")
    return bars[-days:], "ths_api"


def _fetch_realtime_fuyao(code: str) -> dict:
    """A股实时行情 + 估值 via 同花顺官方 API（快照自带官方涨跌幅）。

    行情快照不含中文名与估值，故再调一次估值快照补充 name / PE / PB / PS / PCF。
    """
    resp = _fuyao_get("/api/a-share/prices/snapshot", {"thscodes": _fuyao_thscode(code)})
    if resp.get("code") != 0:
        raise ValueError(f"同花顺官方API snapshot: {resp.get('message')}")
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
    """同花顺官方标的检索：按 thscode / ticker / 中文名解析标准标的。

    返回 [{"thscode", "ticker", "name", "market", ...}]；解析失败返回空列表。
    """
    resp = _fuyao_get("/api/meta/tickers/search", {"q": query})
    if resp.get("code") != 0:
        return []
    return (resp.get("data") or {}).get("item") or []


# --- akshare (Priority 3, free) ---

def _fetch_akshare_a(code: str, days: int):
    """Fetch A-share via akshare."""
    import akshare as ak
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
    try:
        df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                start_date=start_date, end_date=end_date, adjust="qfq")
    except Exception:
        df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                start_date=start_date, end_date=end_date, adjust="")
    if df is None or df.empty:
        raise ValueError(f"akshare returned no data for {code}")
    col_map = {
        "日期": "date", "开盘": "open", "收盘": "close",
        "最高": "high", "最低": "low", "成交量": "volume",
        "成交额": "amount", "涨跌幅": "pct_chg",
    }
    df = df.rename(columns=col_map)
    _log(f"[{code}] Using akshare (free)")
    return _df_to_ohlcv(df, days), "akshare"




def _fetch_akshare_hk(code: str, days: int):
    """Fetch HK stock via akshare."""
    import akshare as ak
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
    try:
        df = ak.stock_hk_hist(symbol=code, period="daily",
                              start_date=start_date, end_date=end_date, adjust="qfq")
    except Exception:
        df = ak.stock_hk_hist(symbol=code, period="daily",
                              start_date=start_date, end_date=end_date, adjust="")
    if df is None or df.empty:
        raise ValueError(f"akshare returned no data for HK{code}")
    col_map = {
        "日期": "date", "开盘": "open", "收盘": "close",
        "最高": "high", "最低": "low", "成交量": "volume",
        "成交额": "amount", "涨跌幅": "pct_chg",
    }
    df = df.rename(columns=col_map)
    _log(f"[HK{code}] Using akshare (free)")
    return _df_to_ohlcv(df, days), "akshare"


# --- yfinance (Priority 4, free, fallback for all markets) ---

def _fetch_yfinance(code: str, market: str, days: int):
    """Fetch any stock via yfinance (universal fallback)."""
    import yfinance as yf
    yf_code = to_yfinance_code(code, market)
    ticker = yf.Ticker(yf_code)
    hist = ticker.history(period=f"{days}d")
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


# --- Realtime quote fetchers ---

def _fetch_realtime_a(code: str) -> dict:
    """Fetch A-share realtime quote with fallback."""
    # Try 同花顺官方 API first (fastest, official fields + valuation; needs key)
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
            row = spot_df[spot_df["代码"] == code]
            if not row.empty:
                r = row.iloc[0]
                return {
                    "name": str(r.get("名称", code)),
                    "price": _safe_float(r.get("最新价")),
                    "change_pct": _safe_float(r.get("涨跌幅")),
                    "change_amount": _safe_float(r.get("涨跌额")),
                    "volume": _safe_float(r.get("成交量")),
                    "amount": _safe_float(r.get("成交额")),
                    "amplitude": _safe_float(r.get("振幅")),
                    "turnover_rate": _safe_float(r.get("换手率")),
                    "pe_ratio": _safe_float(r.get("市盈率-动态")),
                    "pb_ratio": _safe_float(r.get("市净率")),
                    "total_mv": _safe_float(r.get("总市值")),
                    "circ_mv": _safe_float(r.get("流通市值")),
                    "high": _safe_float(r.get("最高")),
                    "low": _safe_float(r.get("最低")),
                    "open": _safe_float(r.get("今开")),
                    "pre_close": _safe_float(r.get("昨收")),
                    "volume_ratio": _safe_float(r.get("量比")),
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
                    "name": str(r.get("股票名称", code)),
                    "price": _safe_float(r.get("最新价")),
                    "change_pct": _safe_float(r.get("涨跌幅")),
                }
        except Exception:
            pass
    # Try 同花顺 (free, stdlib-only; works when EastMoney endpoints are blocked)
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
    """Fetch HK realtime quote."""
    if _check_source("akshare"):
        try:
            import akshare as ak
            spot_df = ak.stock_hk_spot_em()
            matched = spot_df[spot_df["代码"] == code]
            if not matched.empty:
                r = matched.iloc[0]
                return {
                    "name": str(r.get("名称", f"HK{code}")),
                    "price": _safe_float(r.get("最新价")),
                    "change_pct": _safe_float(r.get("涨跌幅")),
                    "volume": _safe_float(r.get("成交量")),
                    "pe_ratio": _safe_float(r.get("市盈率")),
                    "pb_ratio": _safe_float(r.get("市净率")),
                    "total_mv": _safe_float(r.get("总市值")),
                }
        except Exception:
            pass
    return {}


def _fetch_realtime_us(code: str) -> dict:
    """Fetch US realtime quote via yfinance."""
    try:
        import yfinance as yf
        info = yf.Ticker(code).info
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
        }
    except Exception:
        return {}


# --- Priority router ---

def fetch_cn_a(code: str, days: int) -> dict:
    """Fetch A-share with priority: Tushare > 同花顺官方API(有Key) > efinance > 同花顺 > akshare > yfinance."""
    ohlcv = None
    source = "unknown"
    errors = []

    # Priority 0: Tushare Pro (if token configured)
    if os.environ.get("TUSHARE_TOKEN") and _check_source("tushare"):
        try:
            ohlcv, source = _fetch_tushare_a(code, days)
        except Exception as e:
            errors.append(f"tushare: {e}")

    # Priority 1: 同花顺官方 API (需 HITHINK_FINANCE_API_KEY, 前复权+结构化字段)
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

    # Priority 3: 同花顺 (free, no pip dependency)
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
    return {"ohlcv": ohlcv, "realtime": realtime, "name": name, "source": source, "errors": errors}


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

    if ohlcv is None and _check_source("yfinance"):
        try:
            ohlcv, source = _fetch_yfinance(code, "cn_hk", days)
        except Exception as e:
            errors.append(f"yfinance: {e}")

    if ohlcv is None:
        raise ValueError(f"All data sources failed for HK{code}: {'; '.join(errors)}")

    realtime = _fetch_realtime_hk(code)
    name = realtime.get("name", f"HK{code}")
    return {"ohlcv": ohlcv, "realtime": realtime, "name": name, "source": source, "errors": errors}


def fetch_us(code: str, days: int) -> dict:
    """Fetch US stock via yfinance (primary source for US)."""
    ohlcv, source = _fetch_yfinance(code, "us", days)
    realtime = _fetch_realtime_us(code)
    if not realtime and ohlcv:
        last = ohlcv[-1]
        realtime = {"name": code, "price": last["close"], "change_pct": last.get("pct_chg")}
    name = realtime.get("name", code)
    return {"ohlcv": ohlcv, "realtime": realtime, "name": name, "source": source}


# ============================================================
# SECTION 2.5: News Search (optional, with graceful degradation)
# ============================================================

def _fetch_news_akshare(code: str, max_results: int = 5) -> list:
    """A股个股新闻 via akshare 东方财富(stock_news_em)。免费、无需 API Key。

    返回 list of {"title", "content", "url", "date", "source", "publisher"}，
    按发布时间倒序。仅支持 A 股六位代码。
    """
    import akshare as ak
    df = ak.stock_news_em(symbol=code)
    if df is None or df.empty:
        return []
    rows = []
    for _, r in df.iterrows():
        try:
            rows.append({
                "title": str(r.get("新闻标题", "")).strip(),
                "content": str(r.get("新闻内容", "")).strip().replace("\n", " ")[:200],
                "url": str(r.get("新闻链接", "")).strip(),
                "date": str(r.get("发布时间", "")).strip(),
                "source": "akshare-em",
                "publisher": str(r.get("文章来源", "")).strip() or "东方财富",
            })
        except Exception:
            continue
    rows.sort(key=lambda x: x["date"], reverse=True)
    return rows[:max_results]


def search_news(stock_name: str, code: str, max_results: int = 5, market: str = "cn_a") -> list:
    """
    Search news with priority:
      A股: akshare 东方财富个股新闻 (free, no key) > Tavily > SerpAPI > empty
      HK/US: Tavily > SerpAPI > empty
    Returns list of {"title": ..., "content": ..., "url": ..., "date": ..., "source": ...}
    """
    # Priority 0: akshare 东方财富个股新闻 (A股专属, 免费无 Key)
    if market == "cn_a" and _check_source("akshare"):
        try:
            results = _fetch_news_akshare(code, max_results)
            if results:
                _log(f"[{code}] News via akshare/东方财富 ({len(results)} results)")
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
                    "source": "serpapi",
                })
            if results:
                _log(f"[{code}] News via Google News via SerpAPI ({len(results)} results)")
                return results
        except Exception as e:
            _log(f"[{code}] SerpAPI failed: {e}")

    # No news source available — return empty, let Claude use WebSearch
    _log(f"[{code}] No news source available, skipping (Claude will use WebSearch)")
    return []


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


def calc_volume_analysis(volumes: list, closes: list) -> dict:
    """Analyze volume patterns."""
    if len(volumes) < 6 or len(closes) < 2:
        return {"vol_ratio": None, "trend": "insufficient_data"}

    # 5-day average volume (excluding today)
    avg_vol_5 = sum(volumes[-6:-1]) / 5 if len(volumes) >= 6 else volumes[-1]
    curr_vol = volumes[-1]

    vol_ratio = round(curr_vol / avg_vol_5, 2) if avg_vol_5 > 0 else None

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
    """Calculate bias ratio (乖离率)."""
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
                     vol_data: dict, bias_data: dict, support_data: dict) -> dict:
    """
    Composite scoring system (100 points total):
    - Trend/MA alignment: 30 pts
    - Bias (乖离率): 20 pts
    - Volume: 15 pts
    - MACD: 15 pts
    - RSI: 10 pts
    - Support: 10 pts
    """
    breakdown = {}

    # 1. Trend score (30 pts)
    alignment = ma_data.get("alignment_detail", "consolidation")
    trend_scores = {
        "strong_bullish": 30, "bullish": 26, "weak_bullish": 18,
        "consolidation": 12, "weak_bearish": 8, "bearish": 4,
        "strong_bearish": 0, "insufficient_data": 12,
    }
    breakdown["trend"] = trend_scores.get(alignment, 12)

    # 2. Bias score (20 pts) - prefer slightly below MA5
    bias_ma5 = bias_data.get("bias_ma5", 0)
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

    # 3. Volume score (15 pts)
    vol_trend = vol_data.get("trend", "normal")
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

    # 5. RSI score (10 pts)
    rsi_zone = rsi_data.get("zone", "neutral")
    rsi_scores = {
        "oversold": 10, "strong": 8, "neutral": 5,
        "weak": 3, "overbought": 0, "unknown": 5,
    }
    breakdown["rsi"] = rsi_scores.get(rsi_zone, 5)

    # 6. Support score (10 pts)
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

    if total >= 75 and alignment_val in ["bullish", "strong_bullish"]:
        signal = "strong_buy"
    elif total >= 60 and alignment_val in bullish_alignments:
        signal = "buy"
    elif total >= 45:
        signal = "hold"
    elif total >= 30:
        signal = "wait"
    elif alignment_val in ["bearish", "strong_bearish"]:
        signal = "strong_sell"
    else:
        signal = "sell"

    signal_cn = {
        "strong_buy": "强烈买入", "buy": "买入", "hold": "持有",
        "wait": "观望", "sell": "卖出", "strong_sell": "强烈卖出",
    }

    return {
        "total": total,
        "breakdown": breakdown,
        "signal": signal,
        "signal_cn": signal_cn.get(signal, signal),
    }


# ============================================================
# SECTION 5: Main Orchestrator
# ============================================================

def analyze_stock(code: str, days: int = 120, fetch_news: bool = False) -> dict:
    """Full analysis pipeline for a single stock."""
    market, normalized, display = classify_stock(code)

    # 中文公司名解析：优先用同花顺官方标的检索消歧（需 HITHINK_FINANCE_API_KEY）
    if market == "unknown" and _ths_api_key():
        try:
            hits = _search_fuyao(code)
            a_share = [h for h in hits if h.get("market") == "A股" or str(h.get("thscode", "")).endswith((".SH", ".SZ", ".BJ"))]
            if a_share:
                thscode = a_share[0]["thscode"]
                normalized = thscode.rsplit(".", 1)[0]
                market = "cn_a"
                display = normalized
                _log(f"[{code}] 同花顺官方API解析为 {thscode} ({a_share[0].get('name', '')})")
        except Exception as e:
            _log(f"[{code}] 同花顺官方API标的检索失败: {e}")

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

    closes = [bar["close"] for bar in ohlcv if bar["close"] is not None]
    volumes = [bar["volume"] for bar in ohlcv if bar["volume"] is not None]

    if len(closes) < 10:
        raise ValueError(f"Insufficient valid close prices for {code}")

    # Calculate all indicators
    ma = calc_ma(closes, [5, 10, 20, 60])
    macd = calc_macd(closes)
    rsi = calc_rsi(closes, [6, 12, 24])
    vol = calc_volume_analysis(volumes, closes)
    bias = calc_bias(closes, ma)
    support = calc_support(closes, ma)
    score = calc_trend_score(ma, macd, rsi, vol, bias, support)

    # News search (optional)
    news = []
    if fetch_news:
        stock_name = raw.get("name", display)
        news = search_news(stock_name, display, market=market)

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
        },
        "trend_score": score,
        "recent_bars": ohlcv[-10:],
        "total_bars": len(ohlcv),
        "fetch_time": datetime.now().isoformat(),
    }
    if news:
        result["news"] = news
    return result


def main():
    parser = argparse.ArgumentParser(description="Stock Data Fetcher")
    parser.add_argument("--stocks", required=True, help="Comma-separated stock codes")
    parser.add_argument("--days", type=int, default=120, help="History trading days")
    parser.add_argument("--news", action="store_true", help="Also search news (A股 via akshare/东方财富 free; HK/US needs TAVILY_API_KEY or SERPAPI_KEY)")
    args = parser.parse_args()

    codes = [c.strip() for c in args.stocks.split(",") if c.strip()]
    results = []
    errors = []

    # Report available data sources
    sources_status = {}
    for lib in ["tushare", "efinance", "ths", "akshare", "yfinance"]:
        sources_status[lib] = "available" if _check_source(lib) else "not installed"
    sources_status["tushare_token"] = "configured" if os.environ.get("TUSHARE_TOKEN") else "not set"
    sources_status["ths_api"] = "configured" if _ths_api_key() else "not set"
    sources_status["tavily_api"] = "configured" if os.environ.get("TAVILY_API_KEY") else "not set"
    sources_status["serpapi"] = "configured" if os.environ.get("SERPAPI_KEY") else "not set"
    sources_status["news"] = (
        "akshare-em (A股个股新闻, free)"
        if _check_source("akshare")
        else ("tavily" if os.environ.get("TAVILY_API_KEY")
              else ("serpapi" if os.environ.get("SERPAPI_KEY") else "none"))
    )
    _log(f"Data sources: {json.dumps(sources_status)}")

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
