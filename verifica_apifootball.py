#!/usr/bin/env python3
"""Prova la chiave API-Football: dice cosa il piano gratuito ti lascia davvero vedere
(partite di oggi, infortuni, formazioni, arbitro) e quante richieste restano oggi."""
import json
import os
import urllib.request
from datetime import date, timedelta

KEY = os.environ.get("APIFOOTBALL_KEY", "").strip()
BASE = "https://v3.football.api-sports.io"
LEAGUES = {"Premier League": 39, "La Liga": 140, "Bundesliga": 78, "Serie A": 135, "Ligue 1": 61}
SEASON = date.today().year if date.today().month >= 7 else date.today().year - 1


def call(path):
    req = urllib.request.Request(BASE + path, headers={"x-apisports-key": KEY})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8")), r.headers


def main():
    if not KEY:
        print("Manca il segreto APIFOOTBALL_KEY (Settings > Secrets and variables > Actions).")
        return
    print("VERIFICA API-FOOTBALL (stagione %d)" % SEASON)
    try:
        d, h = call("/status")
        resp = d.get("response") or {}
        if isinstance(resp, list):
            resp = {}
        sub = resp.get("subscription", {})
        req = resp.get("requests", {})
        print("Piano: %s | richieste oggi: %s su %s" % (sub.get("plan"), req.get("current"), req.get("limit_day")))
        if d.get("errors"):
            print("Errori:", d["errors"])
    except Exception as e:
        print("Chiave non accettata o rete bloccata: %s" % e)
        return

    # cerco la prima giornata con partite nei prossimi 7 giorni
    for name, lid in LEAGUES.items():
        found = None
        for k in range(0, 8):
            day = (date.today() + timedelta(days=k)).isoformat()
            try:
                d, _ = call("/fixtures?league=%d&season=%d&date=%s" % (lid, SEASON, day))
            except Exception as e:
                print("%s: errore %s" % (name, e))
                break
            if d.get("errors"):
                print("%s: %s" % (name, d["errors"]))
                break
            if d.get("response"):
                found = (day, d["response"])
                break
        if not found:
            print("%s: nessuna partita nei prossimi 7 giorni (o stagione non accessibile)" % name)
            continue
        day, fx = found
        f0 = fx[0]
        print("%s: %d partite il %s, es. %s - %s | arbitro: %s" % (
            name, len(fx), day, f0["teams"]["home"]["name"], f0["teams"]["away"]["name"],
            f0["fixture"].get("referee")))
        try:
            d, _ = call("/injuries?league=%d&season=%d&date=%s" % (lid, SEASON, day))
            inj = d.get("response") or []
            print("   infortuni/squalificati segnalati: %d%s" % (len(inj), " | " + str(d["errors"]) if d.get("errors") else ""))
            for x in inj[:3]:
                print("   - %s (%s): %s" % (x["player"]["name"], x["team"]["name"], x["player"].get("reason")))
        except Exception as e:
            print("   infortuni: errore %s" % e)
        try:
            d, _ = call("/fixtures/lineups?fixture=%d" % f0["fixture"]["id"])
            print("   formazioni gia' pubblicate: %s" % ("si" if d.get("response") else "non ancora (escono ~1h prima)"))
        except Exception as e:
            print("   formazioni: errore %s" % e)
    d, h = call("/status")
    r = (d.get("response") or {}).get("requests", {}) if isinstance(d.get("response"), dict) else {}
    print("Richieste usate oggi dopo la prova: %s su %s" % (r.get("current"), r.get("limit_day")))


if __name__ == "__main__":
    main()
