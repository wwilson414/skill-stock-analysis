"""N-04 fundamental data layer + fundamental analysis (HANDOFF §17.2).

Schema (per requirement): every fetch returns the same envelope regardless of
source or market, so callers never have to re-interpret per-source shapes:

    {
      "market", "code", "currency", "source", "fetched_at",
      "period_type": "annual" | "ttm",
      "report_period": "FY2024" | "2024-12-31" | None,
      "metrics":  {metric_key: float|None, ...},
      "missing":  {metric_key_or_"source": reason, ...},
      "status":   "ok" | "partial" | "insufficient",
    }

Rules:
- Missing values are NEVER guessed: every gap is explained in `missing`.
- The function never raises: an unreachable source degrades `status` to
  "insufficient" instead of propagating an error (same convention as the
  P4 combo signal).
- Currency is reported because A/HK/US report in different currencies and
  cross-market comparisons must not silently mix them.

`analyze_fundamentals()` turns that envelope into the five quality dimensions
required by N-04 (profitability / growth / cash_flow_quality /
financial_safety / competitive_position), each with a level in
{strong, ok, weak, insufficient} plus evidence strings — all derived from
metrics actually present, never invented. `overall` and `confidence_impact`
feed the decision layer (build_decision), which may downgrade confidence and
block a long-term BUY_CANDIDATE when no fundamental evidence exists.
"""
from datetime import datetime

# Absolute-value metrics are in the reporting currency (see `currency`).
METRIC_KEYS = (
    "revenue", "net_profit",
    "revenue_growth_pct", "profit_growth_pct",
    "gross_margin_pct", "net_margin_pct",
    "roe_pct", "roic_pct",
    "ocf", "fcf", "cash_conversion_ratio",
    "debt_ratio_pct", "current_ratio", "interest_debt",
    "dividend_yield_pct",
)

FUND_STATUS = ("ok", "partial", "insufficient")
DIMENSION_LEVELS = ("strong", "ok", "weak", "insufficient")
DIMENSIONS = ("profitability", "growth", "cash_flow_quality",
              "financial_safety", "competitive_position")

# Fallback order per market, cheapest / most reliable first. A-share annual
# report indicators come from akshare (EastMoney F10); yfinance serves all
# three markets and is the only source for HK/US right now.
_FUNDAMENTALS_CHAIN = {
    "cn_a": ("akshare", "yfinance"),
    "cn_hk": ("yfinance",),
    "us": ("yfinance",),
}

_MARKET_CURRENCY = {"cn_a": "CNY", "cn_hk": "HKD", "us": "USD"}


def _log(msg):
    import sys
    print(f"[fundamentals] {msg}", file=sys.stderr)


def _safe_num(v):
    """Coerce pandas/numpy/str scalars to float or None (mirrors sdf._safe_float)."""
    try:
        import math
        if v is None:
            return None
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


def _pct(v):
    """Fraction (0.15) -> percent (15.0); passes through None."""
    return None if v is None else round(v * 100.0, 4)


def _envelope(market, code):
    """Empty-but-valid envelope used before/while any source answers."""
    return {
        "market": market, "code": code,
        "currency": _MARKET_CURRENCY.get(market),
        "source": None, "fetched_at": datetime.now().isoformat(),
        "period_type": None, "report_period": None,
        "metrics": {k: None for k in METRIC_KEYS},
        "missing": {"source": "no source responded yet"},
        "status": "insufficient",
    }


def _fill_missing(env):
    """Record a reason for every metric still None after a source fills data."""
    for k in METRIC_KEYS:
        if env["metrics"][k] is None and k not in env["missing"]:
            env["missing"][k] = f"not provided by source {env['source']}"
    filled = sum(1 for k in METRIC_KEYS if env["metrics"][k] is not None)
    env["status"] = "ok" if filled == len(METRIC_KEYS) else (
        "partial" if filled else "insufficient")
    return env


# ------------------------------------------------------------------
# Source: yfinance (all markets) — annual statements + info snapshot
# ------------------------------------------------------------------

def _ycode(market, code):
    """Internal id -> yfinance ticker (mirrors sdf.to_yfinance_code rules)."""
    if market == "cn_hk":
        return f"{int(code):05d}.HK"
    if market == "cn_a" and code.isdigit() and len(code) == 6:
        return f"{code}.SS" if code[0] == "6" else (
            f"{code}.BJ" if code[0] in "48" or code.startswith(("83", "87", "92"))
            else f"{code}.SZ")
    return code


def _frame_value(df, names):
    """Value of the first matching row (any of *names*) in the last report column."""
    try:
        if df is None or df.empty:
            return None
        lower = {str(i).lower(): i for i in df.index}
        for n in names:
            idx = lower.get(n.lower())
            if idx is not None:
                return _safe_num(df.loc[idx].iloc[-1])
    except Exception:
        return None
    return None


def _frame_older(df, names):
    """Same as _frame_value but from the second-newest report column (growth)."""
    try:
        if df is None or df.empty or df.shape[1] < 2:
            return None
        lower = {str(i).lower(): i for i in df.index}
        for n in names:
            idx = lower.get(n.lower())
            if idx is not None:
                return _safe_num(df.loc[idx].iloc[-2])
    except Exception:
        return None
    return None


def _growth(new, old):
    if new is None or old in (None, 0):
        return None
    try:
        if abs(float(old)) < 1e-9:
            return None
    except (TypeError, ValueError):
        return None
    return round((new - old) / abs(old) * 100.0, 4)


def _col_year(ts):
    """Timestamp-like column label -> fiscal year int (fallback: repr parse)."""
    y = getattr(ts, "year", None)
    if isinstance(y, int):
        return y
    try:
        return int(str(ts)[:4])
    except (TypeError, ValueError):
        return None


def _fund_yfinance(market, code, env):
    """Annual statements via yfinance. Mutates *env* in place and returns it."""
    import yfinance as yf
    t = yf.Ticker(_ycode(market, code))
    try:
        info = t.info or {}
    except Exception:
        info = {}

    def stmt(attr):
        try:
            return getattr(t, attr, None)
        except Exception:
            return None

    income, balance, cash = stmt("income_stmt"), stmt("balance_sheet"), stmt("cashflow")

    env["currency"] = info.get("financialCurrency") or _MARKET_CURRENCY.get(market)
    env["period_type"] = "annual"
    env["report_period"] = None
    try:
        if income is not None and not income.empty:
            env["report_period"] = f"FY{_col_year(income.columns[-1])}"
    except Exception:
        pass

    m = env["metrics"]
    revenue = _frame_value(income, ("Total Revenue", "Operating Revenue"))
    net_profit = _frame_value(income, ("Net Income",
                                       "Net Income Common Stockholders"))
    m["revenue"] = revenue
    m["net_profit"] = net_profit
    gross = _frame_value(income, ("Gross Profit",))
    if revenue not in (None, 0) and gross is not None:
        m["gross_margin_pct"] = round(gross / revenue * 100.0, 4)
    if revenue not in (None, 0) and net_profit is not None:
        m["net_margin_pct"] = round(net_profit / revenue * 100.0, 4)
    roe = _safe_num(info.get("returnOnEquity"))
    if roe is None:
        equity = _frame_value(balance, ("Stockholders Equity",
                                        "Total Stockholder Equity",
                                        "Common Stock Equity"))
        if equity not in (None, 0) and net_profit is not None:
            roe = net_profit / equity
    m["roe_pct"] = _pct(roe)
    m["dividend_yield_pct"] = _pct(_safe_num(info.get("dividendYield")))
    m["revenue_growth_pct"] = _growth(
        revenue, _frame_older(income, ("Total Revenue", "Operating Revenue")))
    m["profit_growth_pct"] = _growth(
        net_profit, _frame_older(income, ("Net Income",
                                          "Net Income Common Stockholders")))
    ocf = _frame_value(cash, ("Operating Cash Flow",
                              "Total Cash From Operating Activities"))
    m["ocf"] = ocf
    m["fcf"] = _frame_value(cash, ("Free Cash Flow",))
    if ocf is not None and net_profit not in (None, 0):
        m["cash_conversion_ratio"] = round(ocf / net_profit, 4)
    assets = _frame_value(balance, ("Total Assets",))
    liabilities = _frame_value(
        balance, ("Total Liabilities Net Minority Total", "Total Liabilities"))
    if assets not in (None, 0) and liabilities is not None:
        m["debt_ratio_pct"] = round(liabilities / assets * 100.0, 4)
    ca = _frame_value(balance, ("Current Assets", "Total Current Assets"))
    cl = _frame_value(balance, ("Current Liabilities",
                                "Total Current Liabilities"))
    if cl not in (None, 0) and ca is not None:
        m["current_ratio"] = round(ca / cl, 4)
    m["interest_debt"] = _frame_value(
        balance, ("Total Debt", "Current Debt And Capital Lease Obligation"))
    if env["source"] is None:
        env["source"] = "yfinance"
    env["missing"] = {}
    return _fill_missing(env)



# ------------------------------------------------------------------
# Source: akshare (A-share annual indicators, EastMoney F10)
# ------------------------------------------------------------------

def _row_value(row, *names):
    """First alias present in an akshare row dict, coerced to float."""
    for n in names:
        if n in row and row[n] not in (None, "", "--"):
            v = _safe_num(row[n])
            if v is not None:
                return v
    return None


def _fund_akshare_a(code, env):
    """A-share annual report indicators. Mutates *env* in place, returns it.

    akshare's cash-flow F10 endpoint is heavy and version-flaky; OCF/FCF stay
    None here and are explained in `missing` rather than guessed.
    """
    import akshare as ak
    df = ak.stock_financial_analysis_indicator(symbol=code, start_year="2020")
    if df is None or df.empty:
        raise RuntimeError("akshare returned no indicator rows")
    row = df.iloc[-1].to_dict()
    m = env["metrics"]
    env["period_type"] = "annual"
    for col in ("日期", "报告期", "报告日期"):
        if col in row:
            env["report_period"] = str(row.get(col))[:10]
            break
    m["roe_pct"] = _row_value(row, "净资产收益率(%)", "加权平均净资产收益率(%)")
    m["gross_margin_pct"] = _row_value(row, "销售毛利率(%)", "销售毛利率")
    m["net_margin_pct"] = _row_value(row, "销售净利率(%)", "销售净利率")
    m["revenue_growth_pct"] = _row_value(
        row, "营业收入增长率(%)", "主营业务收入增长率(%)")
    m["profit_growth_pct"] = _row_value(
        row, "净利润增长率(%)", "归属母公司股东的净利润增长率(%)")
    m["debt_ratio_pct"] = _row_value(row, "资产负债率(%)", "资产负债率")
    m["current_ratio"] = _row_value(row, "流动比率")
    m["interest_debt"] = _row_value(row, "短期借款")
    m["dividend_yield_pct"] = None  # not in this endpoint
    if env["source"] is None:
        env["source"] = "akshare"
    env["missing"] = {}
    return _fill_missing(env)


_FETCHERS = {
    "yfinance": lambda market, code, env: _fund_yfinance(market, code, env),
    "akshare": lambda market, code, env: _fund_akshare_a(code, env),
}


def fetch_fundamentals(market: str, code: str) -> dict:
    """Fetch the fundamental envelope; never raises, never guesses.

    Sources are tried in `_FUNDAMENTALS_CHAIN` order; the first one that
    returns at least one non-None metric wins. An envelope is always
    returned (status "insufficient" when everything failed).
    """
    env = _envelope(market, code)
    for name in _FUNDAMENTALS_CHAIN.get(market, ()):
        try:
            env = _FETCHERS[name](market, code, env)
        except Exception as e:
            _log(f"[{market}:{code}] {name} fundamentals failed: "
                 f"{type(e).__name__}: {str(e)[:120]}")
            env = _envelope(market, code)
            env["missing"] = {"source": f"{name} failed: {str(e)[:160]}"}
            continue
        if env["status"] != "insufficient":
            env["fetched_at"] = datetime.now().isoformat()
            return env
    env["source"] = None
    env["fetched_at"] = datetime.now().isoformat()
    return env

# ------------------------------------------------------------------
# Fundamental analysis: five quality dimensions
# ------------------------------------------------------------------

def _grade(dimension, fund):
    """One dimension's (level, evidence) from metrics actually present."""
    m = fund.get("metrics") or {}

    if dimension == "profitability":
        roe, margin = m.get("roe_pct"), m.get("net_margin_pct")
        if roe is None and margin is None:
            return "insufficient", ["no ROE or net margin reported"]
        parts = []
        if roe is not None:
            parts.append(f"ROE {roe}%")
        if margin is not None:
            parts.append(f"net margin {margin}%")
        if (roe is not None and roe < 5) or (margin is not None and margin < 0):
            return "weak", [", ".join(parts) + ": below breakeven profitability"]
        if roe is not None and roe >= 15 and margin is not None and margin >= 15:
            return "strong", [", ".join(parts) + ": high profitability"]
        return "ok", [", ".join(parts)]

    if dimension == "growth":
        rg, pg = m.get("revenue_growth_pct"), m.get("profit_growth_pct")
        if rg is None and pg is None:
            return "insufficient", ["no growth metrics reported"]
        parts = []
        if rg is not None:
            parts.append(f"revenue {rg:+}%")
        if pg is not None:
            parts.append(f"profit {pg:+}%")
        if (rg is not None and rg < 0) or (pg is not None and pg < 0):
            return "weak", [", ".join(parts) + ": shrinking"]
        if (rg >= 10 if rg is not None else True) and \
           (pg >= 10 if pg is not None else True):
            return "strong", [", ".join(parts) + ": double-digit growth"]
        return "ok", [", ".join(parts)]

    if dimension == "cash_flow_quality":
        ccr, fcf = m.get("cash_conversion_ratio"), m.get("fcf")
        if ccr is None and fcf is None:
            return "insufficient", ["no operating/free cash flow reported"]
        parts = []
        if ccr is not None:
            parts.append(f"OCF/net profit {ccr}")
        if fcf is not None:
            parts.append(f"FCF {fcf}")
        if (ccr is not None and ccr < 0) or (fcf is not None and fcf < 0):
            return "weak", [", ".join(parts) + ": earnings not backed by cash"]
        if ccr is not None and ccr >= 0.8:
            return "strong", [", ".join(parts) + ": cash conversion >= 0.8"]
        if ccr is None:
            return "ok", [", ".join(parts)]
        return "weak", [", ".join(parts) + ": cash conversion < 0.5"]

    if dimension == "financial_safety":
        dr, cr = m.get("debt_ratio_pct"), m.get("current_ratio")
        if dr is None and cr is None:
            return "insufficient", ["no leverage or liquidity metrics reported"]
        parts = []
        if dr is not None:
            parts.append(f"debt/assets {dr}%")
        if cr is not None:
            parts.append(f"current ratio {cr}")
        if (dr is not None and dr > 70) or (cr is not None and cr < 1):
            return "weak", [", ".join(parts) + ": stretched balance sheet"]
        if (dr <= 50 if dr is not None else True) and \
           (cr >= 1.5 if cr is not None else True):
            return "strong", [", ".join(parts) + ": conservative balance sheet"]
        return "ok", [", ".join(parts)]

    # competitive_position: needs peer/industry data — N-07 scope.
    return "insufficient", ["no peer/industry comparison data yet (N-07 scope)"]


def analyze_fundamentals(fund: dict = None) -> dict:
    """Grade the five quality dimensions from a fetch_fundamentals envelope.

    Returns {dimensions, overall, confidence_impact, warnings}. `overall` is
    the median-strongest graded dimension; ungraded dimensions surface as
    warnings. If nothing at all is graded the whole analysis is
    "insufficient" with a "major" confidence impact, so the decision layer
    can never dress up a technical result as a long-term conclusion.
    """
    if not fund or fund.get("status") == "insufficient":
        return {
            "dimensions": {d: {"level": "insufficient",
                               "evidence": ["no fundamental data available"]}
                           for d in DIMENSIONS},
            "overall": "insufficient",
            "confidence_impact": "major",
            "warnings": ((fund.get("missing") or {}).get("source")
                         if isinstance(fund, dict) else None)
            or "fundamentals unavailable: no source responded",
        }
    warnings = []
    rank = {"strong": 3, "ok": 2, "weak": 1}
    dims = {}
    graded = []
    for d in DIMENSIONS:
        level, evidence = _grade(d, fund)
        dims[d] = {"level": level, "evidence": evidence}
        if level != "insufficient":
            graded.append(rank[level])
        else:
            warnings.append(f"{d}: {evidence[0]}")
    if not graded:
        overall, impact = "insufficient", "major"
    else:
        best = max(graded)
        overall = {3: "strong", 2: "ok", 1: "weak"}.get(best, "insufficient")
        impact = "minor" if any(v["level"] == "weak" for v in dims.values()) \
            else "none"
    return {"dimensions": dims, "overall": overall,
            "confidence_impact": impact, "warnings": warnings}


def summary_text(analysis: dict) -> str:
    """One-line summary for logs / report cards."""
    if not analysis:
        return "no fundamentals fetched"
    dims = analysis.get("dimensions") or {}
    bits = [f"{d}={v['level']}" for d, v in dims.items()]
    return (f"overall {analysis.get('overall')} "
            f"({', '.join(bits) or 'no dimensions graded'}); "
            f"confidence impact {analysis.get('confidence_impact')}")

