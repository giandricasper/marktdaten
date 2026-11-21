# Marktdaten für das Finanzcockpit

Dieses Repository lädt jeden Samstag automatisch Kurse und Wechselkurse (Yahoo Finance via `yfinance`) und speichert sie in `data/marktdaten.json`. Das Finanzcockpit (lokale HTML-Datei) liest diese Datei.

**Hier liegen nur öffentliche Marktdaten. Niemals die Finanzcockpit-Exportdatei hochladen.**

## Einrichtung (einmalig)

1. Auf github.com ein **Public** Repository erstellen (z.B. `marktdaten`).
2. Alle Dateien dieses Ordners hochladen und die Struktur beibehalten:
   - `.github/workflows/marktdaten.yml`
   - `scripts/fetch_marktdaten.py`
   - `tickers.json`
3. **Settings → Actions → General → Workflow permissions:** «Read and write permissions» aktivieren.
4. **Actions → Marktdaten aktualisieren → Run workflow** (erster Lauf).
5. `data/marktdaten.json` öffnen → **Raw** → URL kopieren → im Cockpit unter «Daten & Einstellungen» eintragen.

## Neue Position mit Ticker

Im Cockpit unter «Daten» `tickers.json` herunterladen und hier ersetzen (Datei anklicken → Stift → Inhalt einfügen → Commit). Danach optional «Run workflow».

## Wenn etwas nicht funktioniert

- Fehlerhafte Ticker erscheinen im Cockpit unter «Daten» und werden mit den letzten gültigen Daten weitergeführt.
- Den Ticker auf finance.yahoo.com prüfen (Börsenendung: `.SW` SIX, `.L` London, `.DE` XETRA, `.T` Tokio, `.HK` Hongkong).
- Lokal testen: `pip install yfinance pandas` und danach `python scripts/fetch_marktdaten.py`.
- Notfall ohne GitHub: das Skript lokal ausführen und `data/marktdaten.json` im Cockpit über «Datei importieren» laden.

## Aktiendaten für Seite 03 (Einzelaktien-Bewertung)

Ein zweiter Workflow, **«Aktiendaten aktualisieren»** (`.github/workflows/aktien.yml`), läuft jeden Sonntag um 05:23 UTC und schreibt `data/aktien.json` (ca. 2–4 MB) und `data/universum.json`.

| Datei | Zweck |
|---|---|
| `aktien_universum.json` | Startliste je Index (S&P 500, DAX, SMI, Nikkei 225, Hang Seng). Der Workflow liest einmal pro Monat die aktuelle Zusammensetzung von Wikipedia und wählt die grössten Titel nach Marktkapitalisierung; klappt das nicht, gilt die Startliste. |
| `scripts/fetch_aktien.py` | Lädt Kurse ab 2010 (inkl. Dividenden), Kennzahlen, Jahresabschlüsse (Yahoo; US-Titel zusätzlich SEC EDGAR mit Einreichungsdatum), Wechselkurse und Wikipedia-Seitenaufrufe. |

**Einrichten:** die drei Dateien ins Repository hochladen, dann unter *Actions → Aktiendaten aktualisieren → Run workflow* einmal manuell starten. Der erste Lauf dauert 20–60 Minuten. Das Cockpit findet `aktien.json` automatisch neben `marktdaten.json`.

**SEC-Kontakt:** Die SEC verlangt bei automatischen Abrufen eine Kontaktangabe im User-Agent. Standard ist eine anonyme GitHub-Adresse; du kannst unter *Settings → Secrets and variables → Actions → Variables* eine Variable `SEC_UA` anlegen (z.B. «Finanzcockpit Vorname Nachname mail@beispiel.ch»); der Workflow übernimmt sie automatisch.

**Fehler:** Einzelne Titel, die nicht antworten, landen unter `"fehler"`; ihre Daten vom letzten Lauf bleiben erhalten.
