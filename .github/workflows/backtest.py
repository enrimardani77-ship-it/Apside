#!/usr/bin/env python3
"""Backtest: quanto e' affidabile il modello su partite gia' giocate?

Simula di aver usato il bot in passato: ogni settimana stima le probabilita' con
i soli dati precedenti e le confronta con quello che e' successo davvero.
Per i club confronta anche il modello con le quote storiche dei bookmaker.

Uso:
  python backtest.py            # club + nazionali
  python backtest.py --grid     # prova anche altre combinazioni di parametri (lento)
  python backtest.py --no-intl  # solo club
"""
import argparse
import math
from collections import defaultdict
from datetime import date, timedelta

import value_bet as vb
from storico import outcomes

TEST_DAYS = 730          # periodo su cui misuro (circa 2 stagioni)
TRAIN_DAYS = 1100        # storico massimo usato per stimare i club
REFIT_DAYS = 7           # ogni quanti giorni ristimo il modello (club)
SEASONS_BACK = 4
INTL_TRAIN_DAYS = vb.INTL_YEARS * 365
INTL_REFIT_DAYS = 30

ODDS_SUFFIX = {"1": "H", "X": "D", "2": "A", "O2.5": ">2.5", "U2.5": "<2.5"}


# ----------------------------- DATI -------------------------------------
def season_list(today):
    start = today.year if today.month >= 7 else today.year - 1
    return ["%02d%02d" % ((start - i) % 100, (start - i + 1) % 100)
            for i in range(SEASONS_BACK)]


def odds_of(r, prefixes):
    out = {}
    for key, suf in ODDS_SUFFIX.items():
        for p in prefixes:
            try:
                v = float(r.get(p + suf))
            except (TypeError, ValueError):
                continue
            if v > 1.0:
                out[key] = v
                break
    return out


def load_club(today):
    data = defaultdict(list)
    for div in vb.LEAGUES:
        for code in season_list(today):
            try:
                rows = vb.read_csv(vb.download(vb.HISTORY_URL.format(season=code, div=div)))
            except Exception as e:
                print("  ! %s %s non scaricabile: %s" % (div, code, e))
                continue
            for r in rows:
                d = vb.parse_date(r.get("Date"))
                try:
                    hg, ag = int(r["FTHG"]), int(r["FTAG"])
                except (KeyError, ValueError, TypeError):
                    continue
                if not d or d >= today:
                    continue
                data[div].append({"d": d, "h": r["HomeTeam"], "a": r["AwayTeam"],
                                  "hg": hg, "ag": ag, "neutral": False,
                                  "avg": odds_of(r, ("Avg", "B365")),
                                  "max": odds_of(r, ("Max", "B365"))})
        data[div].sort(key=lambda m: m["d"])
    return data


def load_intl(today):
    out = []
    try:
        rows = vb.read_csv(vb.download(vb.INTL_URL))
    except Exception as e:
        print("  ! nazionali non scaricabili: %s" % e)
        return out
    oldest = today - timedelta(days=TEST_DAYS + INTL_TRAIN_DAYS + 30)
    for r in rows:
        d = vb.parse_date(r.get("date"))
        hs = (r.get("home_score") or "").strip()
        as_ = (r.get("away_score") or "").strip()
        if not d or d >= today or d < oldest or hs in ("", "NA") or as_ in ("", "NA"):
            continue
        out.append({"d": d, "h": r["home_team"], "a": r["away_team"],
                    "hg": int(float(hs)), "ag": int(float(as_)),
                    "neutral": (r.get("neutral") or "").strip().upper() == "TRUE",
                    "friendly": (r.get("tournament") or "") == "Friendly",
                    "avg": {}, "max": {}})
    out.sort(key=lambda m: m["d"])
    return out


# ----------------------------- WALK-FORWARD ----------------------------
def walk_forward(matches, half_life, shrink, train_days, refit_days, min_w, test_from, today):
    """Per ogni finestra stima il modello coi soli dati precedenti e prevede
    le partite della finestra. Ritorna una lista di previsioni."""
    out = []
    test = [m for m in matches if m["d"] >= test_from]
    if not test:
        return out
    refit = test[0]["d"]
    while refit <= today:
        nxt = refit + timedelta(days=refit_days)
        batch = [m for m in test if refit <= m["d"] < nxt]
        if batch:
            start = refit - timedelta(days=train_days)
            train = []
            for m in matches:
                if start <= m["d"] < refit:
                    w = 0.5 ** ((refit - m["d"]).days / half_life)
                    if m.get("friendly"):
                        w *= 0.6
                    train.append((m["h"], m["a"], m["hg"], m["ag"], w, m["neutral"]))
            model = vb.fit_poisson(train, shrink) if train else None
            if model:
                for m in batch:
                    xg = vb.expected_goals(model, m["h"], m["a"], m["neutral"], min_w)
                    if xg:
                        out.append({"m": m, "xg": xg, "probs": vb.market_probs(*xg)})
        refit = nxt
    return out


# ----------------------------- METRICHE ---------------------------------
def _mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def result_idx(m):
    return 0 if m["hg"] > m["ag"] else (1 if m["hg"] == m["ag"] else 2)


def brier3(p, idx):
    return sum((p[i] - (1.0 if i == idx else 0.0)) ** 2 for i in range(3))


def devig(odds):
    inv = [1.0 / o for o in odds]
    s = sum(inv)
    return [i / s for i in inv]


def model_1x2(p):
    return [p["probs"]["1"], p["probs"]["X"], p["probs"]["2"]]


def brier_1x2(preds):
    return _mean([brier3(model_1x2(p), result_idx(p["m"])) for p in preds])


def brier_over(preds):
    return _mean([(p["probs"]["O2.5"] - (1.0 if p["m"]["hg"] + p["m"]["ag"] >= 3 else 0.0)) ** 2
                  for p in preds])


def calib_line(name, pairs):
    edges = [0.0, 0.3, 0.5, 0.7, 1.01]
    parts = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = [(p, o) for p, o in pairs if lo <= p < hi]
        if len(sel) >= 15:
            parts.append("%d-%d%%: previsto %.0f%%, reale %.0f%% (n=%d)" % (
                lo * 100, min(hi, 1.0) * 100, 100 * _mean([p for p, _ in sel]),
                100 * _mean([1.0 if o else 0.0 for _, o in sel]), len(sel)))
    if not parts:
        return []
    return ["  " + name + ":"] + ["    " + x for x in parts]


def pick_stats(label, lst):
    n = len(lst)
    if not n:
        return None
    h = sum(1 for x in lst if x["hit"])
    e = _mean([x["prob"] for x in lst])
    t = "  %s: %d/%d azzeccate (%.0f%%), attese %.0f%%" % (label, h, n, 100.0 * h / n, e * 100)
    wo = [x for x in lst if x["odds"]]
    if wo:
        profit = sum((x["odds"] - 1) if x["hit"] else -1 for x in wo)
        t += " | resa con quote medie (%d giocate): %+.1f%%" % (len(wo), 100.0 * profit / len(wo))
    return t


def summarize(preds, title, market):
    L = ["", "== %s: %d partite ==" % (title, len(preds))]
    if not preds:
        return L + ["Nessun dato disponibile."]

    # 1) precisione: modello contro frequenze medie e contro il mercato
    freq = [_mean([1.0 if result_idx(p["m"]) == i else 0.0 for p in preds]) for i in range(3)]
    b_naive = _mean([brier3(freq, result_idx(p["m"])) for p in preds])
    L.append("Precisione 1X2 (Brier: piu' basso = meglio):")
    L.append("  modello %.3f | frequenze medie, senza modello %.3f" % (brier_1x2(preds), b_naive))

    if market:
        pm = [p for p in preds if all(k in p["m"]["avg"] for k in ("1", "X", "2"))]
        if len(pm) >= 30:
            mk = [(p, devig([p["m"]["avg"]["1"], p["m"]["avg"]["X"], p["m"]["avg"]["2"]])) for p in pm]
            b_mod = _mean([brier3(model_1x2(p), result_idx(p["m"])) for p, _ in mk])
            b_mkt = _mean([brier3(q, result_idx(p["m"])) for p, q in mk])
            L.append("  stesse %d partite: modello %.3f | mercato (quote medie) %.3f" % (len(pm), b_mod, b_mkt))
            best = None
            parts = []
            for w in (0.25, 0.5, 0.75, 1.0):
                b = _mean([brier3([(1 - w) * a + w * c for a, c in zip(model_1x2(p), q)],
                                  result_idx(p["m"])) for p, q in mk])
                parts.append("%d%% mercato: %.3f" % (w * 100, b))
                if best is None or b < best[1]:
                    best = (w, b)
            L.append("  mix modello+mercato: " + " | ".join(parts))
            L.append("  miglior mix: %d%% mercato" % (best[0] * 100))
        po = [p for p in preds if "O2.5" in p["m"]["avg"] and "U2.5" in p["m"]["avg"]]
        if len(po) >= 30:
            def y(p):
                return 1.0 if p["m"]["hg"] + p["m"]["ag"] >= 3 else 0.0
            qs = [devig([p["m"]["avg"]["O2.5"], p["m"]["avg"]["U2.5"]])[0] for p in po]
            b_mod = _mean([(p["probs"]["O2.5"] - y(p)) ** 2 for p in po])
            b_mkt = _mean([(q - y(p)) ** 2 for p, q in zip(po, qs)])
            L.append("Over 2.5 (Brier): modello %.3f | mercato %.3f" % (b_mod, b_mkt))

    # 2) calibrazione
    L.append("Calibrazione (previsto -> reale):")
    for key, name in (("1", "Vittoria casa"), ("X", "Pareggio"), ("2", "Vittoria ospite"),
                      ("O2.5", "Over 2.5"), ("U3.5", "Under 3.5"), ("GG", "Gol")):
        pairs = [(p["probs"][key], outcomes(p["m"]["hg"], p["m"]["ag"])[key]) for p in preds]
        L += calib_line(name, pairs)

    # 3) giocate del bot (stessa regola del report)
    safe_l, bold_l = [], []
    for p in preds:
        mis = vb.is_mismatch(p["xg"])
        probs = p["probs"]
        if mis:
            probs = {k: v for k, v in probs.items() if k not in vb.UNDER_KEYS}
        s, b = vb.choose_picks(probs)
        out = outcomes(p["m"]["hg"], p["m"]["ag"])
        for pk, lst in ((s, safe_l), (b, bold_l)):
            if pk:
                lst.append({"key": pk[0], "prob": pk[1], "hit": out[pk[0]], "mis": mis,
                            "odds": p["m"]["avg"].get(pk[0]) if market else None})
    L.append("Giocate del bot (stessa regola del report):")
    for lab, lst in (("SICURA", safe_l), ("PIU' QUOTA", bold_l),
                     ("Under (sicura+quota)", [x for x in safe_l + bold_l if x["key"] in vb.UNDER_KEYS])):
        t = pick_stats(lab, lst)
        if t:
            L.append(t)
    L.append("  partite con una SICURA: %d su %d" % (len(safe_l), len(preds)))

    # 4) partite squilibrate
    mm = [p for p in preds if vb.is_mismatch(p["xg"])]
    if len(mm) >= 15:
        goals = _mean([p["m"]["hg"] + p["m"]["ag"] for p in mm])
        xgs = _mean([p["xg"][0] + p["xg"][1] for p in mm])
        u35p = _mean([p["probs"]["U3.5"] for p in mm])
        u35r = _mean([1.0 if p["m"]["hg"] + p["m"]["ag"] <= 3 else 0.0 for p in mm])
        L.append("Partite squilibrate (%d): gol attesi %.2f, gol veri %.2f | Under 3.5 previsto %.0f%%, reale %.0f%%" % (
            len(mm), xgs, goals, u35p * 100, u35r * 100))

    # 5) value bet storiche e riferimento
    if market:
        bets = []
        for p in preds:
            out = outcomes(p["m"]["hg"], p["m"]["ag"])
            for key, o in p["m"]["max"].items():
                pr = p["probs"][key]
                edge = pr * o - 1
                if 0.30 <= pr and 0.05 <= edge <= 0.40:
                    bets.append((o, out[key], key))
        if bets:
            profit = sum((o - 1) if hit else -1 for o, hit, _ in bets)
            hits = sum(1 for _, hit, _ in bets if hit)
            L.append("Value bet (valore >= 5%%, quota massima): %d giocate, %d azzeccate (%.0f%%), resa %+.1f%%" % (
                len(bets), hits, 100.0 * hits / len(bets), 100.0 * profit / len(bets)))
        blind = []
        for p in preds:
            o = p["m"]["avg"]
            if all(k in o for k in ("1", "X", "2")):
                idx = result_idx(p["m"])
                for i, k in enumerate(("1", "X", "2")):
                    blind.append((o[k] - 1) if i == idx else -1)
        if blind:
            L.append("Riferimento: puntare a caso 1X2 alle quote medie rende %+.1f%% (e' la commissione del bookmaker)" % (
                100.0 * _mean(blind)))
    return L


# ----------------------------- GRIGLIA PARAMETRI ------------------------
def run_club(data, today, half_life, shrink, refit_days=REFIT_DAYS):
    test_from = today - timedelta(days=TEST_DAYS)
    preds = []
    for div, ms in data.items():
        preds += walk_forward(ms, half_life, shrink, TRAIN_DAYS, refit_days,
                              vb.MIN_W_CLUB, test_from, today)
    return preds


def run_grid(data, today):
    L = ["", "== PROVA DI PARAMETRI (club, ogni riga e' un esperimento) =="]
    L.append("Brier 1X2 e Over 2.5: piu' basso = meglio")
    res = []
    for hl in (120, 240, 480):
        for sh in (2.0, 4.0, 8.0):
            pr = run_club(data, today, hl, sh, refit_days=14)
            res.append((brier_1x2(pr), brier_over(pr), hl, sh, len(pr)))
            print("  provato emivita %d, smorzamento %.0f" % (hl, sh))
    res.sort()
    for b1, bo, hl, sh, n in res:
        mark = "  <- attuale" if (hl, sh) == (vb.HALF_LIFE_CLUB, vb.SHRINK_CLUB) else ""
        L.append("  emivita %3d gg, smorzamento %.0f: 1X2 %.4f | Over %.4f (n=%d)%s" % (hl, sh, b1, bo, n, mark))
    best_hl, best_sh = res[0][2], res[0][3]
    L.append("Miglior combinazione: emivita %d giorni, smorzamento %.0f" % (best_hl, best_sh))
    old_rho = vb.DC_RHO
    rr = []
    for rho in (-0.20, -0.10, -0.05, 0.0):
        vb.DC_RHO = rho
        pr = run_club(data, today, best_hl, best_sh, refit_days=14)
        rr.append((brier_over(pr), brier_1x2(pr), rho))
        print("  provato rho %.2f" % rho)
    vb.DC_RHO = old_rho
    rr.sort()
    L.append("Correzione punteggi bassi (rho), con la combinazione migliore:")
    for bo, b1, rho in rr:
        mark = "  <- attuale" if rho == old_rho else ""
        L.append("  rho %+.2f: Over %.4f | 1X2 %.4f%s" % (rho, bo, b1, mark))
    return L


# ----------------------------- MAIN -------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid", action="store_true")
    ap.add_argument("--no-intl", action="store_true")
    ap.add_argument("--no-club", action="store_true")
    args, _ = ap.parse_known_args()
    today = date.today()
    L = ["BACKTEST del %s (misura su circa %d giorni di partite gia' giocate)" % (
        today.strftime("%d/%m/%Y"), TEST_DAYS)]

    if not args.no_club:
        print("Scarico lo storico dei club...")
        data = load_club(today)
        print("Calcolo il backtest dei club...")
        preds = run_club(data, today, vb.HALF_LIFE_CLUB, vb.SHRINK_CLUB)
        L += summarize(preds, "CLUB (5 campionati)", True)
        if args.grid:
            L += run_grid(data, today)

    if not args.no_intl:
        print("Scarico lo storico delle nazionali...")
        ms = load_intl(today)
        print("Calcolo il backtest delle nazionali...")
        preds = walk_forward(ms, vb.HALF_LIFE_INTL, vb.SHRINK_INTL, INTL_TRAIN_DAYS,
                             INTL_REFIT_DAYS, vb.MIN_W_INTL,
                             today - timedelta(days=TEST_DAYS), today)
        L += summarize(preds, "NAZIONALI", False)

    L += ["", "Come leggerlo: se 'previsto' e 'reale' sono vicini il modello e' affidabile; "
          "se il modello e' peggiore del mercato conviene correggerlo con le quote."]
    print("\n" + "\n".join(L))


if __name__ == "__main__":
    main()
