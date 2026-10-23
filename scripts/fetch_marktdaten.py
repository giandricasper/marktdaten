#!/usr/bin/env python3
"""
Finanzcockpit – Marktdaten-Abruf
================================
Lädt für alle Ticker aus tickers.json die Kursverläufe (5 Jahre, Wochenschlusskurse)
sowie Wechselkurse in CHF über yfinance (Yahoo Finance) und schreibt sie nach
data/marktdaten.json. Läuft wöchentlich via GitHub Actions, kann aber auch lokal
gestartet werden:   pip install yfinance pandas   →   python scripts/fetch_marktdaten.py

Robustheit:
- Fehler bei einzelnen Tickern brechen den Lauf nicht ab; sie landen unter "fehler".
- Für fehlgeschlagene Ticker werden die Daten des letzten erfolgreichen Laufs behalten.
- Pence-Notierungen (GBp/GBX) werden in GBP umgerechnet.
"""
import json
import datetime as dt
import pathlib
import sys

import yfinance as yf

ROOT = pathlib.Path(__file__).resolve().parent.parent
CFG = ROOT / "tickers.json"
OUT = ROOT / "data" / "marktdaten.json"
PERIOD = "5y"
SUBUNITS = {"GBp": ("GBP", 100), "GBX": ("GBP", 100), "ZAc": ("ZAR", 100), "ILA": ("ILS", 100)}


def weekly_series(sym: str):
    """Tagesdaten laden, auf Wochenschluss (Freitag) verdichten, letzten Tag exakt datieren."""
    t = yf.Ticker(sym)
    h = t.history(period=PERIOD, interval="1d", auto_adjust=False)
    if h is None or h.empty or "Close" not in h:
        raise ValueError("keine Kursdaten")
    close = h["Close"].dropna()
    if close.empty:
        raise ValueError("nur leere Kurse")
    idx = close.index
    if getattr(idx, "tz", None) is not None:
        close.index = idx.tz_localize(None)
    wk = close.resample("W-FRI").last().dropna()
    pts = [[d.strftime("%Y-%m-%d"), round(float(v), 6)] for d, v in wk.items()]
    pts[-1][0] = close.index[-1].strftime("%Y-%m-%d")  # letzte (evtl. angebrochene) Woche = echtes Datum
    return t, pts


def currency_and_name(t):
    cur, name = None, ""
    try:
        cur = t.fast_info.get("currency")
    except Exception:
        pass
    try:
        info = t.info or {}
        cur = cur or info.get("currency")
        name = info.get("shortName") or info.get("longName") or ""
    except Exception:
        pass
    return cur or "CHF", name


def main():
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    tickers = sorted(set(cfg.get("tickers", [])))
    fx_list = sorted(set(cfg.get("fx", [])) | {"USD", "EUR"})

    old = {}
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            old = {}

    out = {"stand": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "quelle": "Yahoo Finance via yfinance", "kurse": {}, "fx": {}, "fehler": {}}

    for sym in tickers:
        try:
            t, pts = weekly_series(sym)
            cur, name = currency_and_name(t)
            if cur in SUBUNITS:
                cur, div = SUBUNITS[cur]
                pts = [[d, round(v / div, 6)] for d, v in pts]
            out["kurse"][sym] = {"waehrung": cur, "name": name, "verlauf": pts}
            if cur != "CHF":
                fx_list.append(cur)
            print(f"OK   {sym:<14} {cur}  {len(pts)} Wochen, letzter Kurs {pts[-1][1]}")
        except Exception as e:  # noqa: BLE001
            out["fehler"][sym] = str(e)[:200]
            if sym in old.get("kurse", {}):
                out["kurse"][sym] = old["kurse"][sym]
                out["fehler"][sym] += " (alte Daten behalten)"
            print(f"FEHL {sym:<14} {e}", file=sys.stderr)

    for cur in sorted(set(fx_list)):
        if cur == "CHF":
            continue
        sym = f"{cur}CHF=X"
        try:
            _, pts = weekly_series(sym)
            out["fx"][cur] = pts
            print(f"OK   {sym:<14} {len(pts)} Wochen, letzter Kurs {pts[-1][1]}")
        except Exception as e:  # noqa: BLE001
            out["fehler"][sym] = str(e)[:200]
            if cur in old.get("fx", {}):
                out["fx"][cur] = old["fx"][cur]
            print(f"FEHL {sym:<14} {e}", file=sys.stderr)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"\nGeschrieben: {OUT}  ({len(out['kurse'])} Ticker, {len(out['fx'])} Währungen, {len(out['fehler'])} Fehler)")


if __name__ == "__main__":
    main()
