#!/usr/bin/env python3
"""Verifica delle fonti dell'Oracolo, campionato per campionato.

Controlla che ogni fonte risponda, che abbia i dati che servono e che i nomi
delle squadre del calendario ESPN vengano riconosciuti dal modello.
Si lancia da GitHub (workflow "Verifica fonti") oppure: python verifica_fonti.py
"""
import json
from datetime import date, timedelta

import value_bet as vb

NEEDED = {"base": ["FTHG", "FTAG"], "cartellini": ["HY", "AY"], "tiri in porta": ["HST", "AST"],
          "quote medie": ["AvgH", "AvgD", "AvgA"], "quote Bet365": ["B365H", "B365D", "B365A"],
          "Over/Under": ["Avg>2.5", "Avg<2.5"]}


def safe(fn, default=None):
    try:
        return fn()
    except Exception as e:
        return default if default is not None else "errore: %s" % e


def main():
    today = date.today()
    L = ["VERIFICA FONTI DELL'ORACOLO del %s" % today.strftime("%d/%m/%Y"), ""]
    problems = []

    # 1) calendario club football-data
    L.append("== 1. Calendario club (fixtures.csv di football-data.co.uk) ==")
    try:
        fx = vb.read_csv(vb.download(vb.FIXTURES_URL))
        by = {}
        for r in fx:
            d = vb.parse_date(r.get("Date"))
            if r.get("Div") in vb.LEAGUES and d:
                by.setdefault(r["Div"], []).append(d)
        for div, name in vb.LEAGUES.items():
            ds = by.get(div, [])
            if ds:
                L.append("  %s: %d partite, dal %s al %s" % (name, len(ds), min(ds), max(ds)))
            else:
                L.append("  %s: nessuna partita (viene usato ESPN)" % name)
    except Exception as e:
        L.append("  NON RAGGIUNGIBILE: %s" % e)
        problems.append("fixtures.csv non raggiungibile")

    # 2) storico e 3) ESPN per campionato
    L += ["", "== 2. Storico e calendario ESPN, per campionato =="]
    for div, name in vb.LEAGUES.items():
        L.append("")
        L.append("%s" % name)
        teams, last = set(), None
        cols = set()
        for code in vb.season_codes(today):
            try:
                rows = vb.read_csv(vb.download(vb.HISTORY_URL.format(season=code, div=div)))
            except Exception as e:
                L.append("  storico %s: NON SCARICABILE (%s)" % (code, e))
                problems.append("%s storico %s" % (name, code))
                continue
            played = [r for r in rows if (r.get("FTHG") or "").strip() != ""]
            cols |= set(rows[0].keys()) if rows else set()
            for r in rows:
                teams.add(r.get("HomeTeam"))
                teams.add(r.get("AwayTeam"))
                d = vb.parse_date(r.get("Date"))
                if d and (r.get("FTHG") or "").strip() != "" and (last is None or d > last):
                    last = d
            L.append("  storico %s: %d partite giocate" % (code, len(played)))
        teams.discard(None)
        if last:
            age = (today - last).days
            L.append("  ultima partita registrata: %s (%d giorni fa)%s" % (
                last, age, "  <- dati vecchi?" if age > 14 else ""))
        miss = [k for k, v in NEEDED.items() if not all(c in cols for c in v)]
        L.append("  colonne: %s" % ("tutte presenti" if not miss else "MANCANO " + ", ".join(miss)))
        if miss:
            problems.append("%s: colonne mancanti (%s)" % (name, ", ".join(miss)))

        # ESPN prossimi 7 giorni
        found, bad = [], set()
        for k in range(7):
            day = today + timedelta(days=k)
            for r in vb.espn_club_rows(div, day):
                h = vb.resolve_name(r["HomeTeam"], teams) if teams else None
                a = vb.resolve_name(r["AwayTeam"], teams) if teams else None
                found.append((r["Date"], r["HomeTeam"], r["AwayTeam"]))
                if not h:
                    bad.add(r["HomeTeam"])
                if not a:
                    bad.add(r["AwayTeam"])
        if found:
            L.append("  ESPN prossimi 7 giorni: %d partite (es. %s - %s il %s)" % (
                len(found), found[0][1], found[0][2], found[0][0]))
        else:
            L.append("  ESPN prossimi 7 giorni: nessuna partita trovata (pausa del campionato o fonte non raggiungibile)")
        if bad:
            L.append("  NOMI NON RICONOSCIUTI: %s" % ", ".join(sorted(bad)))
            problems.append("%s: nomi ESPN non riconosciuti (%s)" % (name, ", ".join(sorted(bad))))
        elif found:
            L.append("  nomi squadre: tutti riconosciuti")

    # 4) FPL
    L += ["", "== 3. Infortuni Premier League (Fantasy Premier League) =="]
    try:
        data = json.loads(vb.download(vb.FPL_URL))
        el, tm = data.get("elements", []), data.get("teams", [])
        flagged = [p for p in el if p.get("status") in ("i", "d", "s", "u")]
        L.append("  OK: %d squadre, %d giocatori, %d con stato di assenza o dubbio" % (len(tm), len(el), len(flagged)))
        need = ["goals_scored", "assists", "minutes", "yellow_cards", "element_type", "status", "web_name"]
        lack = [k for k in need if el and k not in el[0]]
        if lack:
            L.append("  PROBLEMA: mancano i campi %s" % ", ".join(lack))
            problems.append("FPL: campi mancanti")
    except Exception as e:
        L.append("  NON RAGGIUNGIBILE: %s" % e)
        problems.append("FPL non raggiungibile")

    # 5) nazionali
    L += ["", "== 4. Nazionali =="]
    try:
        rows = vb.read_csv(vb.download(vb.INTL_URL))
        ds = [vb.parse_date(r.get("date")) for r in rows
              if (r.get("home_score") or "").strip() not in ("", "NA")]
        ds = [d for d in ds if d]
        L.append("  Dataset risultati: %d partite, ultima il %s" % (len(ds), max(ds) if ds else "n/d"))
    except Exception as e:
        L.append("  Dataset risultati: NON RAGGIUNGIBILE (%s)" % e)
        problems.append("dataset nazionali")
    fx = vb.espn_fixtures(today, today + timedelta(days=6))
    L.append("  ESPN calendario nazionali (7 giorni): %s" % (
        "%d partite" % len(fx) if fx else "nessuna partita trovata"))

    L += ["", "== RISULTATO =="]
    if problems:
        L.append("Da sistemare:")
        L += ["  - " + p for p in problems]
    else:
        L.append("Nessun problema rilevato.")
    print("\n".join(L))


if __name__ == "__main__":
    main()
