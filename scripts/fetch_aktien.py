#!/usr/bin/env python3
"""
Finanzcockpit – Aktiendaten für die Einzelaktien-Bewertung (Seite 03)
=====================================================================
Schreibt data/aktien.json mit:
  - Universum: grösste Titel je Index (S&P 500 Top 50, DAX 40, SMI 20, Nikkei 225 Top 50, Hang Seng Top 50)
  - Wochenkurse ab 2010 (dividendenbereinigt = Total Return) auf gemeinsamem Freitagsraster
  - Rohkurse (nicht bereinigt) zum Quartalsende – für historische Multiples
  - Kennzahlen (Yahoo «info») und Jahresabschlüsse (4 Jahre Yahoo; US-Titel zusätzlich SEC EDGAR ab ~2009, point-in-time)
  - Wechselkurse (CHF je Einheit) und Index-Kurse
  - Wikipedia-Seitenaufrufe (monatlich, 24 Monate) als Aufmerksamkeitsmass

Lokal starten:  pip install yfinance pandas lxml requests  →  python scripts/fetch_aktien.py
Robust: Fehler einzelner Titel brechen den Lauf nicht ab (→ "fehler"); dann bleiben die Daten des letzten Laufs.
"""
import datetime as dt
import io
import json
import math
import os
import pathlib
import re
import sys
import time
import urllib.parse

import pandas as pd
import requests
import yfinance as yf

ROOT = pathlib.Path(__file__).resolve().parent.parent
CFG = ROOT / "aktien_universum.json"
OUT = ROOT / "data" / "aktien.json"
UNI = ROOT / "data" / "universum.json"     # Cache: gewähltes Universum + Wikipedia-Titel
START = "2010-01-01"
PAUSE = float(os.environ.get("PAUSE", "0.25"))   # Sekunden zwischen Abrufen (Rate-Limit)
UA = os.environ.get("SEC_UA") or "Finanzcockpit privat finanzcockpit@users.noreply.github.com"
HDR = {"User-Agent": UA}
SUBUNITS = {"GBp": ("GBP", 100), "GBX": ("GBP", 100), "ZAc": ("ZAR", 100), "ILA": ("ILS", 100)}
EXPECTED = {"SP500": (450, 520), "DAX": (38, 42), "SMI": (19, 21), "N225": (200, 230), "HSI": (55, 100)}


def log(*a):
    print(*a, flush=True)


def sig(x, n=6):
    """Auf n signifikante Stellen runden (kleinere JSON-Datei)."""
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    if x == 0:
        return 0
    return round(x, -int(math.floor(math.log10(abs(x)))) + (n - 1))


def retry(fn, tries=3, wait=2.0):
    last = None
    for k in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(wait * (k + 1))
    raise last


# ---------------------------------------------------------------- Universum
def norm_symbol(idx, raw, suffix):
    s = str(raw).strip().upper()
    if not s or s in ("NAN", "NONE"):
        return None
    if idx == "SP500":
        return s.replace(".", "-")
    if idx == "HSI":
        d = re.sub(r"\D", "", s)
        return d.zfill(4) + ".HK" if d else None
    if idx == "N225":
        d = re.sub(r"\D", "", s)
        return d + ".T" if len(d) == 4 else None
    s = s.split(".")[0].split(":")[-1].strip()
    return s + suffix if re.fullmatch(r"[A-Z0-9]{1,6}", s) else None


def wiki_constituents(idx, cfg):
    """Versucht die aktuelle Indexzusammensetzung aus Wikipedia zu lesen. None bei Misserfolg."""
    try:
        html = requests.get(cfg["wikipedia"], headers=HDR, timeout=30).text
        tables = pd.read_html(io.StringIO(html))
    except Exception as e:  # noqa: BLE001
        log(f"  Wikipedia {idx}: {e}")
        return None
    lo, hi = EXPECTED[idx]
    best = None
    for t in tables:
        cols = [str(c) for c in (t.columns.get_level_values(-1) if hasattr(t.columns, "levels") else t.columns)]
        t.columns = cols
        col = next((c for c in cols if re.search(r"ticker|symbol|code|stock", c, re.I)), None)
        if not col:
            continue
        syms = [norm_symbol(idx, v, cfg["suffix"]) for v in t[col].tolist()]
        syms = list(dict.fromkeys(s for s in syms if s))
        if lo <= len(syms) <= hi and (best is None or len(syms) > len(best)):
            best = syms
    return best


def market_cap(sym):
    t = yf.Ticker(sym)
    fi = t.fast_info
    mc = fi.get("marketCap") or fi.get("market_cap")
    cur = fi.get("currency")
    if not mc:
        raise ValueError("keine Marktkapitalisierung")
    return float(mc), cur


def choose_universe(cfg, old_uni, force=False):
    """Top-N je Index nach Marktkapitalisierung. Monatlich neu bestimmt, sonst aus dem Cache."""
    today = dt.date.today()
    if old_uni and not force and old_uni.get("monat") == today.strftime("%Y-%m"):
        log("Universum: Cache vom", old_uni.get("monat"))
        return old_uni
    uni = {"monat": today.strftime("%Y-%m"), "indizes": {}, "wiki": (old_uni or {}).get("wiki", {})}
    for idx, c in cfg.items():
        if idx.startswith("_"):
            continue
        members = wiki_constituents(idx, c)
        src = "Wikipedia"
        if not members:
            members, src = list(c["kandidaten"]), "Startliste"
        n = c["anzahl"]
        if len(members) > n:
            caps = []
            for s in members:
                try:
                    mc, _ = retry(lambda: market_cap(s), tries=2, wait=1)
                    caps.append((s, mc))
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(PAUSE / 2)
            caps.sort(key=lambda x: -x[1])
            chosen = [s for s, _ in caps[:n]] or members[:n]
        else:
            chosen = members
        uni["indizes"][idx] = chosen
        log(f"Universum {idx}: {len(chosen)} Titel (Quelle {src}, {len(members)} Mitglieder)")
    return uni


# ---------------------------------------------------------------- Kurse
def week_grid():
    """Freitage bis heute. Der Workflow läuft sonntags – die letzte Woche ist damit abgeschlossen."""
    return pd.date_range(START, dt.date.today(), freq="W-FRI")


def weekly(series, grid):
    s = series.dropna()
    if getattr(s.index, "tz", None) is not None:
        s.index = s.index.tz_localize(None)
    w = s.resample("W-FRI").last()
    w = w.reindex(grid).ffill(limit=2)
    return w


def to_list(w, div=1.0):
    return [sig(v / div) if pd.notna(v) else None for v in w.tolist()]


def history(sym, adjusted):
    h = yf.Ticker(sym).history(start=START, interval="1d", auto_adjust=adjusted)
    if h is None or h.empty or "Close" not in h:
        raise ValueError("keine Kursdaten")
    return h["Close"]


def quarter_raw(raw, grid):
    """Rohkurs (nicht dividendenbereinigt) am letzten Wochenschluss jedes Quartals – für historische Multiples."""
    w = weekly(raw, grid).dropna()
    last = w.groupby(w.index.to_period("Q")).tail(1)
    return [[d.strftime("%Y-%m-%d"), sig(v)] for d, v in last.items()]


# ---------------------------------------------------------------- Abschlüsse (Yahoo)
ROWS = {
    "rev": ["Total Revenue", "Operating Revenue"],
    "ebit": ["EBIT", "Operating Income"],
    "ebitda": ["EBITDA", "Normalized EBITDA"],
    "ni": ["Net Income Common Stockholders", "Net Income"],
    "da": ["Reconciled Depreciation", "Depreciation And Amortization"],
    "int": ["Interest Expense", "Interest Expense Non Operating"],
    "taxrate": ["Tax Rate For Calcs"],
    "eps": ["Diluted EPS", "Basic EPS"],
    "ocf": ["Operating Cash Flow"],
    "capex": ["Capital Expenditure"],
    "divpaid": ["Cash Dividends Paid", "Common Stock Dividend Paid"],
    "debt": ["Total Debt"],
    "cash": ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"],
    "equity": ["Stockholders Equity", "Common Stock Equity"],
    "shares": ["Ordinary Shares Number", "Share Issued"],
}


def pick(df, names, col):
    if df is None or df.empty:
        return None
    for n in names:
        if n in df.index:
            v = df.at[n, col] if col in df.columns else None
            if v is not None and pd.notna(v):
                return float(v)
    return None


def yahoo_years(t):
    inc, cf, bs = t.income_stmt, t.cashflow, t.balance_sheet
    cols = set()
    for df in (inc, cf, bs):
        if df is not None and not df.empty:
            cols |= set(df.columns)
    years = []
    for col in sorted(cols):
        y = {"fy": pd.Timestamp(col).strftime("%Y-%m-%d"), "quelle": "Yahoo"}
        for k, names in ROWS.items():
            df = inc if k in ("rev", "ebit", "ebitda", "ni", "da", "int", "taxrate", "eps") else cf if k in ("ocf", "capex", "divpaid") else bs
            y[k] = sig(pick(df, names, col))
        if y.get("rev") is None and y.get("ni") is None:
            continue
        years.append(y)
    return years


# ---------------------------------------------------------------- SEC EDGAR (nur USA)
SEC_TAGS = {
    "rev": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
            "RevenueFromContractWithCustomerIncludingAssessedTax", "RevenuesNetOfInterestExpense"],
    "ebit": ["OperatingIncomeLoss"],
    "ni": ["NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "da": ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization", "DepreciationAmortizationAndAccretionNet", "Depreciation"],
    "int": ["InterestExpense", "InterestExpenseNonoperating"],
    "tax": ["IncomeTaxExpenseBenefit"],
    "pretax": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
               "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    "divps": ["CommonStockDividendsPerShareDeclared", "CommonStockDividendsPerShareCashPaid"],
    "ltdebt": ["LongTermDebt", "LongTermDebtNoncurrent"],
    "stdebt": ["DebtCurrent", "LongTermDebtCurrent", "ShortTermBorrowings"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "shares": ["WeightedAverageNumberOfDilutedSharesOutstanding", "WeightedAverageNumberOfSharesOutstandingBasic"],
}
INSTANT = {"ltdebt", "stdebt", "cash", "equity"}


def sec_cik_map():
    r = requests.get("https://www.sec.gov/files/company_tickers.json", headers=HDR, timeout=30)
    r.raise_for_status()
    return {v["ticker"].upper().replace(".", "-"): int(v["cik_str"]) for v in r.json().values()}


def sec_annual(facts, tags, instant):
    """Jahreswerte aus 10-K: je Geschäftsjahresende der ZUERST eingereichte Wert (point-in-time, keine späteren Restatements)."""
    gaap = facts.get("facts", {}).get("us-gaap", {})
    for tag in tags:
        node = gaap.get(tag)
        if not node:
            continue
        units = node.get("units", {})
        arr = units.get("USD") or units.get("USD/shares") or units.get("shares") or next(iter(units.values()), [])
        out = {}
        for f in arr:
            if not str(f.get("form", "")).startswith("10-K") or f.get("fp") != "FY":
                continue
            end = f.get("end")
            if not end:
                continue
            if not instant:
                st = f.get("start")
                if not st:
                    continue
                days = (dt.date.fromisoformat(end) - dt.date.fromisoformat(st)).days
                if days < 340 or days > 390:
                    continue
            prev = out.get(end)
            if prev is None or f["filed"] < prev[1]:
                out[end] = (f["val"], f["filed"])
        if out:
            return out
    return {}


def sec_years(cik):
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
    r = requests.get(url, headers=HDR, timeout=60)
    r.raise_for_status()
    facts = r.json()
    ser = {k: sec_annual(facts, tags, k in INSTANT) for k, tags in SEC_TAGS.items()}
    ends = sorted(set(ser["rev"]) | set(ser["ni"]))
    years = []
    for end in ends:
        if end < "2008-06-30":
            continue
        filed = max((ser[k][end][1] for k in ("rev", "ni") if end in ser[k]), default=None)
        g = lambda k: ser[k][end][0] if end in ser[k] else None  # noqa: E731
        debt = None
        if g("ltdebt") is not None or g("stdebt") is not None:
            debt = (g("ltdebt") or 0) + (g("stdebt") or 0)
        ebit, da = g("ebit"), g("da")
        taxrate = None
        if g("tax") is not None and g("pretax"):
            taxrate = g("tax") / g("pretax") if g("pretax") > 0 else None
        capex = g("capex")
        years.append({"fy": end, "filed": filed, "quelle": "SEC", "rev": sig(g("rev")), "ebit": sig(ebit),
                      "ebitda": sig(ebit + da) if ebit is not None and da is not None else None,
                      "ni": sig(g("ni")), "da": sig(da), "int": sig(g("int")), "taxrate": sig(taxrate, 4),
                      "eps": sig(g("eps")), "ocf": sig(g("ocf")), "capex": sig(-capex) if capex is not None else None,
                      "divps": sig(g("divps")), "debt": sig(debt), "cash": sig(g("cash")), "equity": sig(g("equity")),
                      "shares": sig(g("shares"))})
    return years


def merge_years(sec, yah):
    """SEC als Basis (point-in-time); fehlende Felder und neuere Jahre aus Yahoo ergänzen."""
    by = {y["fy"]: dict(y) for y in sec}
    for y in yah:
        # Yahoo datiert auf das Geschäftsjahresende; SEC-Ende kann wenige Tage abweichen
        match = next((k for k in by if abs((dt.date.fromisoformat(k) - dt.date.fromisoformat(y["fy"])).days) <= 10), None)
        if match:
            for k, v in y.items():
                if by[match].get(k) is None and v is not None and k not in ("fy", "quelle"):
                    by[match][k] = v
        else:
            by[y["fy"]] = dict(y)
    return [by[k] for k in sorted(by)]


# ---------------------------------------------------------------- Wikipedia-Aufmerksamkeit
def wiki_title(name, sym):
    q = re.sub(r"\b(Inc|Corp|Corporation|Co|Ltd|Limited|AG|SE|plc|Holdings?|Group|N\.?V|S\.?A|K\.?K)\b\.?,?", "", name or "", flags=re.I).strip(" ,.")
    q = q or sym
    r = requests.get("https://en.wikipedia.org/w/api.php", headers=HDR, timeout=20,
                     params={"action": "query", "list": "search", "srsearch": f"{q} company", "srlimit": 1, "format": "json"})
    hits = r.json().get("query", {}).get("search", [])
    return hits[0]["title"].replace(" ", "_") if hits else None


def wiki_views(title):
    end = dt.date.today().replace(day=1) - dt.timedelta(days=1)
    start = (end.replace(day=1) - pd.DateOffset(months=23)).date()
    url = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia.org/all-access/user/"
           f"{urllib.parse.quote(title, safe='')}/monthly/{start:%Y%m}01/{end:%Y%m%d}")
    r = requests.get(url, headers=HDR, timeout=20)
    if r.status_code == 404:
        return []
    r.raise_for_status()
    return [[it["timestamp"][:4] + "-" + it["timestamp"][4:6], it["views"]] for it in r.json().get("items", [])]


# ---------------------------------------------------------------- Hauptprogramm
INFO_KEYS = {"trailingPE": "pe", "forwardPE": "fpe", "trailingEps": "eps", "forwardEps": "feps", "priceToBook": "pb",
             "bookValue": "bvps", "enterpriseValue": "ev", "ebitda": "ebitda", "totalDebt": "debt", "totalCash": "cash",
             "totalRevenue": "rev", "revenueGrowth": "revg", "earningsGrowth": "epsg", "returnOnEquity": "roe",
             "operatingMargins": "opm", "profitMargins": "npm", "dividendYield": "dy", "payoutRatio": "payout",
             "beta": "beta", "sharesOutstanding": "shares", "marketCap": "mcap", "currentPrice": "price",
             "freeCashflow": "fcf", "operatingCashflow": "ocf"}


def fetch_stock(sym, idx, grid, cik_map, wiki_cache):
    t = yf.Ticker(sym)
    info = retry(lambda: t.info or {}, tries=2)
    cur = info.get("currency") or "USD"
    fin = info.get("financialCurrency") or cur
    div = 1.0
    if cur in SUBUNITS:
        cur, div = SUBUNITS[cur]
    adj = retry(lambda: history(sym, True))
    raw = retry(lambda: history(sym, False))
    w = weekly(adj, grid)
    rec = {"name": info.get("shortName") or info.get("longName") or sym, "langname": info.get("longName") or "",
           "index": idx, "sektor": info.get("sector") or "Unbekannt", "branche": info.get("industry") or "",
           "land": info.get("country") or "", "waehrung": cur, "finWaehrung": fin,
           "kurse": to_list(w, div), "roh": [[d, sig(v / div)] for d, v in quarter_raw(raw, grid)],
           "info": {v: sig(info.get(k)) for k, v in INFO_KEYS.items() if info.get(k) is not None}}
    if "price" not in rec["info"] and len(raw):
        rec["info"]["price"] = sig(float(raw.dropna().iloc[-1]) / div)
    yah = []
    try:
        yah = yahoo_years(t)
    except Exception as e:  # noqa: BLE001
        rec["hinweis"] = f"Yahoo-Abschlüsse: {e}"[:150]
    years = yah
    if idx == "SP500" and cik_map:
        cik = cik_map.get(sym.upper())
        if cik:
            try:
                years = merge_years(sec_years(cik), yah)
                time.sleep(0.15)  # SEC: max. 10 Anfragen/Sekunde
            except Exception as e:  # noqa: BLE001
                rec["hinweis"] = f"SEC: {e}"[:150]
    rec["jahre"] = years
    title = wiki_cache.get(sym)
    try:
        if not title:
            title = wiki_title(rec["langname"] or rec["name"], sym)
            if title:
                wiki_cache[sym] = title
        rec["wiki"] = title
        rec["views"] = wiki_views(title) if title else []
    except Exception as e:  # noqa: BLE001
        rec["views"] = []
        rec.setdefault("hinweis", f"Wikipedia: {e}"[:150])
    return rec


def main():
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    old = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    old_uni = json.loads(UNI.read_text(encoding="utf-8")) if UNI.exists() else {}
    for k, c in cfg.items():
        if not k.startswith("_"):
            for s, w in (c.get("wiki") or {}).items():
                old_uni.setdefault("wiki", {})[s] = w
    uni = choose_universe(cfg, old_uni, force="--neu" in sys.argv)
    UNI.parent.mkdir(parents=True, exist_ok=True)
    UNI.write_text(json.dumps(uni, ensure_ascii=False, indent=1), encoding="utf-8")

    grid = week_grid()
    out = {"stand": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "quelle": "Yahoo Finance (yfinance), SEC EDGAR, Wikimedia", "wochen": [d.strftime("%Y-%m-%d") for d in grid],
           "indizes": {}, "aktien": {}, "fx": {}, "fehler": {}}
    try:
        cik_map = sec_cik_map()
    except Exception as e:  # noqa: BLE001
        cik_map = {}
        out["fehler"]["SEC"] = str(e)[:200]

    currencies = {"USD", "EUR", "JPY", "HKD"}
    for idx, members in uni["indizes"].items():
        c = cfg[idx]
        try:
            w = weekly(retry(lambda: history(c["symbol"], False)), grid)
            out["indizes"][idx] = {"name": c["name"], "symbol": c["symbol"], "waehrung": c["waehrung"], "land": c["land"],
                                   "kurse": to_list(w), "titel": members}
        except Exception as e:  # noqa: BLE001
            out["fehler"][c["symbol"]] = str(e)[:200]
            if idx in old.get("indizes", {}):
                out["indizes"][idx] = {**old["indizes"][idx], "titel": members}
        for sym in members:
            try:
                rec = fetch_stock(sym, idx, grid, cik_map, uni.setdefault("wiki", {}))
                out["aktien"][sym] = rec
                currencies |= {rec["waehrung"], rec["finWaehrung"]}
                log(f"OK   {idx:<6} {sym:<10} {rec['waehrung']}/{rec['finWaehrung']}  {len(rec['jahre'])} Jahre, "
                    f"{sum(v is not None for v in rec['kurse'])} Wochen, {len(rec.get('views', []))} Mt. Wikipedia")
            except Exception as e:  # noqa: BLE001
                out["fehler"][sym] = str(e)[:200]
                if sym in old.get("aktien", {}):
                    out["aktien"][sym] = old["aktien"][sym]
                    out["fehler"][sym] += " (alte Daten behalten)"
                log(f"FEHL {idx:<6} {sym:<10} {e}")
            time.sleep(PAUSE)

    for cur in sorted(c for c in currencies if c and c != "CHF"):
        try:
            out["fx"][cur] = to_list(weekly(retry(lambda: history(f"{cur}CHF=X", False)), grid))
        except Exception as e:  # noqa: BLE001
            out["fehler"][f"{cur}CHF=X"] = str(e)[:200]
            if cur in old.get("fx", {}) and len(old["fx"][cur]) == len(grid):
                out["fx"][cur] = old["fx"][cur]

    UNI.write_text(json.dumps(uni, ensure_ascii=False, indent=1), encoding="utf-8")
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"\nGeschrieben: {OUT} ({OUT.stat().st_size / 1e6:.1f} MB) – {len(out['aktien'])} Aktien, "
        f"{len(out['indizes'])} Indizes, {len(out['fx'])} Währungen, {len(out['fehler'])} Fehler")


if __name__ == "__main__":
    main()
