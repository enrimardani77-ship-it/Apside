#!/usr/bin/env python3
"""Storico delle previsioni.

- salva ogni previsione in data/predictions.json
- il giorno dopo la confronta con il risultato vero
- calcola quanto il bot ci azzecca (testo mostrato nella pagina)
"""
import json
import os
from collections import defaultdict
from datetime import date, datetime, timedelta

import value_bet as vb

PRED_FILE = "data/predictions.json"
KEEP = 800            # quante previsioni tenere al massimo
GIVE_UP_DAYS = 10     # dopo quanti giorni senza risultato smetto di cercarlo


def load(path=PRED_FILE):
    try:
        with open(path, encoding="utf-8") as f:
            return {p["id"]: p for p in json.load(f)}
    except Exception:
        return {}


def save(preds, path=PRED_FILE):
    items = sorted(preds.values(), key=lambda p: (p.get("date") or "", p["id"]))[-KEEP:]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, separators=(",", ":"))


def outcomes(hg, ag):
    t = hg + ag
    return {"1": hg > ag, "X": hg == ag, "2": hg < ag,
            "1X": hg >= ag, "X2": hg <= ag, "12": hg != ag,
            "U1.5": t <= 1, "O1.5": t >= 2, "U2.5": t <= 2, "O2.5": t >= 3,
            "U3.5": t <= 3, "O3.5": t >= 4,
            "GG": hg > 0 and ag > 0, "NG": hg == 0 or ag == 0}


def merge(preds, new_records):
    """Aggiunge le nuove previsioni; quelle gia' verificate non si toccano."""
    for r in new_records:
        if not r.get("date"):
            continue
        old = preds.get(r["id"])
        if old and isinstance(old.get("result"), list):
            continue
        r["made"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        preds[r["id"]] = r


# ----------------------------- RISULTATI --------------------------------
def club_results(div, today):
    out = {}
    for code in vb.season_codes(today):
        try:
            rows = vb.read_csv(vb.download(vb.HISTORY_URL.format(season=code, div=div)))
        except Exception:
            continue
        for r in rows:
            d = vb.parse_date(r.get("Date"))
            try:
                hg, ag = int(r["FTHG"]), int(r["FTAG"])
            except (KeyError, ValueError, TypeError):
                continue
            if d:
                out[(d.isoformat(), r["HomeTeam"], r["AwayTeam"])] = (hg, ag)
    return out


def espn_results(day):
    import json as _json
    out = {}
    for slug, _ in vb.ESPN_INTL:
        url = vb.ESPN_URL.format(slug=slug, d=day.strftime("%Y%m%d"))
        try:
            data = _json.loads(vb.download(url))
        except Exception:
            continue
        for ev in data.get("events", []) or []:
            comp = (ev.get("competitions") or [{}])[0]
            state = (((ev.get("status") or {}).get("type") or {}).get("state"))
            if state != "post":
                continue
            home = away = hs = as_ = None
            for c in comp.get("competitors", []) or []:
                nm = (c.get("team") or {}).get("displayName")
                try:
                    sc = int(c.get("score"))
                except (TypeError, ValueError):
                    sc = None
                if c.get("homeAway") == "home":
                    home, hs = nm, sc
                elif c.get("homeAway") == "away":
                    away, as_ = nm, sc
            if home and away and hs is not None and as_ is not None:
                out[(day.isoformat(), home, away)] = (hs, as_)
    return out


def dataset_results():
    out = {}
    try:
        rows = vb.read_csv(vb.download(vb.INTL_URL))
    except Exception:
        return out
    for r in rows:
        d = vb.parse_date(r.get("date"))
        hs = (r.get("home_score") or "").strip()
        as_ = (r.get("away_score") or "").strip()
        if d and hs not in ("", "NA") and as_ not in ("", "NA"):
            out[(d.isoformat(), r["home_team"], r["away_team"])] = (int(float(hs)), int(float(as_)))
    return out


def resolve_pending(preds, today):
    """Cerca i risultati delle previsioni ancora senza esito."""
    todays = today.isoformat()
    pend = [p for p in preds.values()
            if p.get("result") is None and p.get("date") and p["date"] <= todays]
    if not pend:
        return 0
    found = 0

    # club: dai CSV di football-data
    for div in sorted(set(p.get("div") for p in pend if p.get("kind") == "club" and p.get("div"))):
        res = club_results(div, today)
        for p in pend:
            if p.get("kind") == "club" and p.get("div") == div:
                r = res.get((p["date"], p["home"], p["away"]))
                if r:
                    p["result"] = list(r)
                    found += 1

    # nazionali: prima ESPN, poi il dataset
    intl = [p for p in pend if p.get("kind") == "intl" and p.get("result") is None]
    for dstr in sorted(set(p["date"] for p in intl)):
        res = espn_results(date.fromisoformat(dstr))
        for p in intl:
            if p["date"] == dstr and p.get("result") is None:
                r = res.get((dstr, p["home"], p["away"]))
                if r:
                    p["result"] = list(r)
                    found += 1
    left = [p for p in intl if p.get("result") is None]
    if left:
        ds = dataset_results()
        for p in left:
            d0 = date.fromisoformat(p["date"])
            for dd in (d0, d0 + timedelta(days=1), d0 - timedelta(days=1)):
                r = ds.get((dd.isoformat(), p.get("key_home"), p.get("key_away")))
                if r:
                    p["result"] = list(r)
                    found += 1
                    break

    # rinuncio alle partite troppo vecchie senza risultato
    limit = (today - timedelta(days=GIVE_UP_DAYS)).isoformat()
    for p in pend:
        if p.get("result") is None and p["date"] < limit:
            p["result"] = "nd"
    return found


# ----------------------------- STATISTICHE ------------------------------
def _pct(a, b):
    return "%.0f%%" % (100.0 * a / b) if b else "n/d"


def stats_text(preds):
    done = [p for p in preds.values() if isinstance(p.get("result"), list) and p.get("probs")]
    if not done:
        return ("STORICO\nNessuna partita verificata ancora. I risultati compaiono dal giorno "
                "dopo le prime previsioni salvate.")
    done.sort(key=lambda p: p["date"])
    L = ["STORICO - %d partite verificate (dal %s)" % (len(done), done[0]["date"])]

    picks = []
    for p in done:
        out = outcomes(*p["result"])
        for typ in ("safe", "bold"):
            pk = p.get(typ)
            if pk and pk[0] in out:
                picks.append({"typ": typ, "kind": p.get("kind"), "key": pk[0],
                              "prob": pk[1], "odds": pk[2], "hit": out[pk[0]],
                              "date": p["date"]})

    def line(label, sel):
        n = len(sel)
        if not n:
            return None
        h = sum(1 for x in sel if x["hit"])
        e = sum(x["prob"] for x in sel) / n
        return "%s: %d/%d azzeccate (%s), attese %.0f%%" % (label, h, n, _pct(h, n), e * 100)

    safe = [x for x in picks if x["typ"] == "safe"]
    bold = [x for x in picks if x["typ"] == "bold"]
    L.append("")
    for lab, sel in (("SICURA", safe), ("PIU' QUOTA", bold),
                     ("  solo club", [x for x in safe if x["kind"] == "club"]),
                     ("  solo nazionali", [x for x in safe if x["kind"] == "intl"])):
        t = line(lab, sel)
        if t:
            L.append(t)

    bands = [(0.0, 0.65), (0.65, 0.72), (0.72, 1.01)]
    bl = []
    for lo, hi in bands:
        sel = [x for x in picks if lo <= x["prob"] < hi]
        if sel:
            h = sum(1 for x in sel if x["hit"])
            e = sum(x["prob"] for x in sel) / len(sel)
            bl.append("%.0f-%.0f%%: %d/%d (%s, attese %.0f%%)" % (
                max(lo, 0.55) * 100, min(hi, 1.0) * 100, h, len(sel), _pct(h, len(sel)), e * 100))
    if bl:
        L += ["", "Per fascia di probabilita':"] + ["  " + b for b in bl]

    unders = [x for x in picks if x["key"] in vb.UNDER_KEYS]
    t = line("Giocate Under", unders)
    if t:
        L += ["", t]

    withodds = [x for x in picks if x["odds"]]
    if withodds:
        profit = sum((x["odds"] - 1) if x["hit"] else -1 for x in withodds)
        L.append("Resa con quote note (%d giocate, 1 unita' ciascuna): %+.0f%%" % (
            len(withodds), profit / len(withodds) * 100))

    by_day = defaultdict(list)
    for x in safe:
        by_day[x["date"]].append(x)
    combo = []
    for n, name in ((2, "Doppia (prime 2)"), (3, "Tripla (prime 3)"), (5, "Multipla (prime 5)")):
        days = [v for v in by_day.values() if len(v) >= n]
        if days:
            ok = sum(1 for v in days
                     if all(x["hit"] for x in sorted(v, key=lambda x: -x["prob"])[:n]))
            combo.append("  %s: passata %d giorni su %d" % (name, ok, len(days)))
    if combo:
        L += ["", "Se avessi giocato le giocate migliori del giorno insieme:"] + combo

    cal = []
    for key, name in (("1", "Vittoria casa"), ("X", "Pareggio"), ("2", "Vittoria ospite"),
                      ("O2.5", "Over 2.5"), ("GG", "Gol (entrambe)")):
        pairs = [(p["probs"][key], outcomes(*p["result"])[key]) for p in done if key in p["probs"]]
        if len(pairs) >= 10:
            pred = sum(a for a, _ in pairs) / len(pairs)
            real = sum(1 for _, b in pairs if b) / len(pairs)
            cal.append("  %s: previsto %.0f%%, reale %.0f%%" % (name, pred * 100, real * 100))
    if cal:
        L += ["", "Calibrazione (tutte le partite):"] + cal

    if len(done) < 100:
        L += ["", "Pochi dati: servono almeno 100 partite per fidarsi di questi numeri."]
    return "\n".join(L)
