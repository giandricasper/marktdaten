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
