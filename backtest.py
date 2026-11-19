#!/usr/bin/env python3
"""Backtest e taratura dell'Oracolo.

Simula di aver usato il bot in passato: ogni settimana stima le probabilita' con
i soli dati precedenti e le confronta con quello che e' successo davvero.
Per i club confronta anche il modello con le quote storiche dei bookmaker.

Uso:
  python backtest.py            # misura i parametri attuali (club + nazionali)
  python backtest.py --tune     # cerca i parametri migliori e li salva in data/params.json
  python backtest.py --no-intl  # solo club
"""
import argparse
import json
import os
from collections import defaultdict
from datetime import date, timedelta

import value_bet as vb
from storico import outcomes

TEST_DAYS = 730          # periodo su cui misuro (circa 2 stagioni)
TRAIN_DAYS = 1100        # storico massimo usato per stimare i club
REFIT_DAYS = 7           # ogni quanti giorni ristimo il modello (club)
TUNE_REFIT_CLUB = 21     # durante la taratura, per fare piu' in fretta
TUNE_REFIT_INTL = 45
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
def walk_forward(matches, half_life, shrink, train_days, refit_days, min_w,
                 test_from, today, spread=0.0):
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
                        out.append({"m": m, "xg": xg,
                                    "probs": vb.market_probs(*xg, spread=spread)})
        refit = nxt
    return out


def run_club(data, today, half_life, shrink, refit_days=REFIT_DAYS, spread=0.0):
    test_from = today - timedelta(days=TEST_DAYS)
    preds = []
    for div, ms in data.items():
        preds += walk_forward(ms, half_life, shrink, TRAIN_DAYS, refit_days,
                              vb.MIN_W_CLUB, test_from, today, spread)
    return preds


def run_intl(ms, today, half_life, shrink, refit_days=INTL_REFIT_DAYS, spread=0.0):
    return walk_forward(ms, half_life, shrink, INTL_TRAIN_DAYS, refit_days,
                        vb.MIN_W_INTL, today - timedelta(days=TEST_DAYS), today, spread)


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


def total(p):
    return p["m"]["hg"] + p["m"]["ag"]


def brier_1x2(preds):
    return _mean([brier3(model_1x2(p), result_idx(p["m"])) for p in preds])


def brier_over(preds):
    return _mean([(p["probs"]["O2.5"] - (1.0 if total(p) >= 3 else 0.0)) ** 2 for p in preds])


def brier_u35(preds):
    return _mean([(p["probs"]["U3.5"] - (1.0 if total(p) <= 3 else 0.0)) ** 2 for p in preds])


def objective(preds):
    """Punteggio da minimizzare: 1X2 + Over 2.5 + Under 3.5 (piu' basso = meglio)."""
    if not preds:
        return 9.9
    return brier_1x2(preds) + 2 * brier_over(preds) + 2 * brier_u35(preds)


def metrics_line(preds):
    return "1X2 %.4f | Over 2.5 %.4f | Under 3.5 %.4f" % (
        brier_1x2(preds), brier_over(preds), brier_u35(preds))


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


def xg_buckets(preds):
    edges = [0.0, 1.5, 2.0, 2.5, 3.0, 4.0, 99.0]
    L = ["Under 3.5 per gol attesi della favorita (previsto -> reale):"]
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = [p for p in preds if lo <= max(p["xg"]) < hi]
        if len(sel) >= 20:
            L.append("  %.1f%s: previsto %.0f%%, reale %.0f%% (n=%d)" % (
                lo, "+" if hi >= 99 else "-%.1f" % hi,
                100 * _mean([p["probs"]["U3.5"] for p in sel]),
                100 * _mean([1.0 if total(p) <= 3 else 0.0 for p in sel]), len(sel)))
    return L if len(L) > 1 else []


def summarize(preds, title, market, kind):
    L = ["", "== %s: %d partite ==" % (title, len(preds))]
    if not preds:
        return L + ["Nessun dato disponibile."]

    freq = [_mean([1.0 if result_idx(p["m"]) == i else 0.0 for p in preds]) for i in range(3)]
    b_naive = _mean([brier3(freq, result_idx(p["m"])) for p in preds])
    L.append("Precisione 1X2 (Brier: piu' basso = meglio):")
    L.append("  modello %.3f | frequenze medie, senza modello %.3f" % (brier_1x2(preds), b_naive))
    L.append("  Over 2.5 %.3f | Under 3.5 %.3f" % (brier_over(preds), brier_u35(preds)))

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
            qs = [devig([p["m"]["avg"]["O2.5"], p["m"]["avg"]["U2.5"]])[0] for p in po]
            b_mod = _mean([(p["probs"]["O2.5"] - (1.0 if total(p) >= 3 else 0.0)) ** 2 for p in po])
            b_mkt = _mean([(q - (1.0 if total(p) >= 3 else 0.0)) ** 2 for p, q in zip(po, qs)])
            L.append("Over 2.5 (Brier): modello %.3f | mercato %.3f" % (b_mod, b_mkt))

    L.append("Calibrazione (previsto -> reale):")
    for key, name in (("1", "Vittoria casa"), ("X", "Pareggio"), ("2", "Vittoria ospite"),
                      ("O2.5", "Over 2.5"), ("U3.5", "Under 3.5"), ("GG", "Gol")):
        pairs = [(p["probs"][key], outcomes(p["m"]["hg"], p["m"]["ag"])[key]) for p in preds]
        L += calib_line(name, pairs)

    safe_l, bold_l = [], []
    for p in preds:
        mis = vb.is_mismatch(p["xg"], kind)
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

    mm = [p for p in preds if vb.is_mismatch(p["xg"], kind)]
    if len(mm) >= 15:
        L.append("Partite segnalate come squilibrate (%d): gol attesi %.2f, gol veri %.2f | Under 3.5 previsto %.0f%%, reale %.0f%%" % (
            len(mm), _mean([p["xg"][0] + p["xg"][1] for p in mm]), _mean([total(p) for p in mm]),
            100 * _mean([p["probs"]["U3.5"] for p in mm]),
            100 * _mean([1.0 if total(p) <= 3 else 0.0 for p in mm])))
    L += xg_buckets(preds)

    if market:
        bets = []
        for p in preds:
            out = outcomes(p["m"]["hg"], p["m"]["ag"])
            for key, o in p["m"]["max"].items():
                pr = p["probs"][key]
                edge = pr * o - 1
                if 0.30 <= pr and 0.05 <= edge <= 0.40:
                    bets.append((o, out[key]))
        if bets:
            profit = sum((o - 1) if hit else -1 for o, hit in bets)
            hits = sum(1 for _, hit in bets if hit)
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


# ----------------------------- TARATURA ---------------------------------
def pick_guard(preds):
    """Soglia di gol attesi della favorita oltre la quale l'Under 3.5 e' sopravvalutato
    di almeno 4 punti. Se non c'e' nessuna soglia, la guardia e' inutile (99)."""
    t = 1.5
    while t <= 4.0:
        sel = [p for p in preds if max(p["xg"]) >= t]
        if len(sel) >= 40:
            gap = _mean([p["probs"]["U3.5"] for p in sel]) - \
                _mean([1.0 if total(p) <= 3 else 0.0 for p in sel])
            if gap >= 0.04:
                return t
        t += 0.25
    return 99.0


def tune(label, runner, base, spreads, extra=None):
    """runner(hl, sh, spread) -> preds. Ritorna (miglior combinazione, righe di testo)."""
    L = []
    res = []
    for hl, sh in base:
        pr = runner(hl, sh, 0.0)
        res.append((objective(pr), hl, sh))
        print("  %s: emivita %d, smorzamento %s" % (label, hl, sh))
    res.sort()
    _, hl, sh = res[0]
    best_spread, best_obj = 0.0, res[0][0]
    for sp in spreads:
        pr = runner(hl, sh, sp)
        o = objective(pr)
        print("  %s: incertezza %.2f" % (label, sp))
        if o < best_obj:
            best_spread, best_obj = sp, o
    L.append("  emivita %d giorni, smorzamento %s, incertezza gol %.2f" % (hl, sh, best_spread))
    return (hl, sh, best_spread), L


def tune_club(data, today):
    cur = run_club(data, today, vb.HALF_LIFE_CLUB, vb.SHRINK_CLUB, TUNE_REFIT_CLUB, vb.SPREAD_CLUB)
    L = ["", "== TARATURA CLUB =="]
    L.append("Prima: " + metrics_line(cur))
    base = [(hl, sh) for hl in (120, 240, 480) for sh in (2.0, 4.0, 8.0)]
    (hl, sh, sp), t = tune("club", lambda h, s, x: run_club(data, today, h, s, TUNE_REFIT_CLUB, x),
                           base, (0.10, 0.20, 0.30))
    best_rho, best_o = vb.DC_RHO, None
    for rho in (-0.20, -0.10, -0.05, 0.0):
        vb.DC_RHO = rho
        o = objective(run_club(data, today, hl, sh, TUNE_REFIT_CLUB, sp))
        print("  club: rho %.2f" % rho)
        if best_o is None or o < best_o:
            best_rho, best_o = rho, o
    vb.DC_RHO = best_rho
    after = run_club(data, today, hl, sh, TUNE_REFIT_CLUB, sp)
    L.append("Dopo:  " + metrics_line(after))
    L += t
    L.append("  correzione punteggi bassi (rho) %.2f" % best_rho)
    params = {"half_life_club": hl, "shrink_club": sh, "spread_club": sp, "dc_rho": best_rho}
    vb.HALF_LIFE_CLUB, vb.SHRINK_CLUB, vb.SPREAD_CLUB = hl, sh, sp
    vb.DC_RHO = best_rho
    return params, L


def tune_intl(ms, today):
    cur = run_intl(ms, today, vb.HALF_LIFE_INTL, vb.SHRINK_INTL, TUNE_REFIT_INTL, vb.SPREAD_INTL)
    L = ["", "== TARATURA NAZIONALI =="]
    L.append("Prima: " + metrics_line(cur))
    base = [(hl, sh) for hl in (180, 365, 730) for sh in (0.5, 1.0, 2.0, 3.0)]
    (hl, sh, sp), t = tune("nazionali", lambda h, s, x: run_intl(ms, today, h, s, TUNE_REFIT_INTL, x),
                           base, (0.15, 0.30, 0.45))
    after = run_intl(ms, today, hl, sh, TUNE_REFIT_INTL, sp)
    L.append("Dopo:  " + metrics_line(after))
    L += t
    vb.HALF_LIFE_INTL, vb.SHRINK_INTL, vb.SPREAD_INTL = hl, sh, sp
    return {"half_life_intl": hl, "shrink_intl": sh, "spread_intl": sp}, L


def save_params(params):
    os.makedirs(os.path.dirname(vb.PARAMS_FILE), exist_ok=True)
    params["tuned_on"] = date.today().isoformat()
    with open(vb.PARAMS_FILE, "w", encoding="utf-8") as f:
        json.dump(params, f, indent=1)


# ----------------------------- ANCORAGGIO AL MERCATO --------------------
ANCHOR_SAMPLE = 700      # partite usate per il confronto (il calcolo e' lento)
ANCHOR_WS = (0.5, 0.8, 1.0)


def anchor_eval(preds, spread):
    """Confronta modello puro, modello ancorato alle quote (vari pesi) e mercato.
    Ritorna (righe di testo, miglior peso)."""
    pm = [p for p in preds if all(k in p["m"].get("avg", {}) for k in ("1", "X", "2", "O2.5", "U2.5"))]
    if len(pm) < 60:
        return ["Ancoraggio al mercato: troppe poche partite con quote per misurarlo."], vb.MARKET_W
    step = max(1, len(pm) // ANCHOR_SAMPLE)
    pm = pm[::step]
    rows = {"modello puro": [], "mercato": []}
    for w in ANCHOR_WS:
        rows["ancorato %d%%" % round(w * 100)] = []
    res = {k: [] for k in rows}
    for p in pm:
        a = p["m"]["avg"]
        tgt = {"p": vb.devig([a["1"], a["X"], a["2"]]), "ou": vb.devig([a["O2.5"], a["U2.5"]])[0]}
        idx, tot = result_idx(p["m"]), total(p)
        def score(p1x2, pover, pu35):
            return (brier3(p1x2, idx), (pover - (1.0 if tot >= 3 else 0.0)) ** 2,
                    (pu35 - (1.0 if tot <= 3 else 0.0)) ** 2)
        res["modello puro"].append(score(model_1x2(p), p["probs"]["O2.5"], p["probs"]["U3.5"]))
        res["mercato"].append((brier3(tgt["p"], idx), (tgt["ou"] - (1.0 if tot >= 3 else 0.0)) ** 2,
                               float("nan")))
        for w in ANCHOR_WS:
            xg = vb.anchor_xg(p["xg"], tgt, w, spread)
            pr = vb.market_probs(*xg, spread=spread)
            res["ancorato %d%%" % round(w * 100)].append(
                score([pr["1"], pr["X"], pr["2"]], pr["O2.5"], pr["U3.5"]))
    L = ["", "Ancoraggio alle quote (su %d partite con quote; Brier, piu' basso = meglio):" % len(pm)]
    best_w, best_o = 0.0, None
    for name, lst in res.items():
        b1 = _mean([x[0] for x in lst])
        bo = _mean([x[1] for x in lst])
        bu = _mean([x[2] for x in lst if x[2] == x[2]])
        L.append("  %-14s 1X2 %.4f | Over 2.5 %.4f%s" % (
            name, b1, bo, (" | Under 3.5 %.4f" % bu) if bu == bu else ""))
        if name.startswith("ancorato"):
            o = b1 + 2 * bo + 2 * bu
            if best_o is None or o < best_o:
                best_o, best_w = o, float(name.split()[1].rstrip("%")) / 100
        if name == "modello puro":
            pure_o = b1 + 2 * bo + 2 * bu
    if best_o is not None and pure_o <= best_o:
        best_w = 0.0
    L.append("  peso migliore: %d%% %s" % (round(best_w * 100),
             "(il modello puro non viene migliorato)" if best_w == 0 else "(meglio del modello puro)"))
    return L, best_w


# ----------------------------- MAIN -------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tune", action="store_true")
    ap.add_argument("--no-intl", action="store_true")
    ap.add_argument("--no-club", action="store_true")
    args, _ = ap.parse_known_args()
    today = date.today()
    head = "TARATURA E BACKTEST DELL'ORACOLO" if args.tune else "BACKTEST DELL'ORACOLO"
    L = ["%s del %s (misura su circa %d giorni di partite gia' giocate)" % (
        head, today.strftime("%d/%m/%Y"), TEST_DAYS)]
    params = {"mismatch_ratio": 99.0}
    tuned_any = False

    club_data = intl_ms = None
    if not args.no_club:
        print("Scarico lo storico dei club...")
        club_data = load_club(today)
    if not args.no_intl:
        print("Scarico lo storico delle nazionali...")
        intl_ms = load_intl(today)

    if args.tune:
        if club_data is not None:
            p, t = tune_club(club_data, today)
            params.update(p)
            L += t
            tuned_any = True
        if intl_ms is not None:
            p, t = tune_intl(intl_ms, today)
            params.update(p)
            L += t
            tuned_any = True

    if club_data is not None:
        print("Calcolo il backtest dei club...")
        preds = run_club(club_data, today, vb.HALF_LIFE_CLUB, vb.SHRINK_CLUB, REFIT_DAYS, vb.SPREAD_CLUB)
        if args.tune:
            g = pick_guard(preds)
            params["mismatch_xg_club"] = g
            vb.MISMATCH_XG_CLUB, vb.MISMATCH_RATIO = g, 99.0
            L += ["", "Guardia partite squilibrate (club): " +
                  ("serve dalla soglia %.2f gol attesi" % g if g < 99 else "non serve, tolta")]
        L += summarize(preds, "CLUB (5 campionati)", True, "club")
        print("Confronto con l'ancoraggio al mercato...")
        al, bw = anchor_eval(preds, vb.SPREAD_CLUB)
        L += al
        if args.tune:
            params["market_w"] = bw

    if intl_ms is not None:
        print("Calcolo il backtest delle nazionali...")
        preds = run_intl(intl_ms, today, vb.HALF_LIFE_INTL, vb.SHRINK_INTL, INTL_REFIT_DAYS, vb.SPREAD_INTL)
        if args.tune:
            g = pick_guard(preds)
            params["mismatch_xg_intl"] = g
            vb.MISMATCH_XG_INTL, vb.MISMATCH_RATIO = g, 99.0
            L += ["", "Guardia partite squilibrate (nazionali): " +
                  ("serve dalla soglia %.2f gol attesi" % g if g < 99 else "non serve, tolta")]
        L += summarize(preds, "NAZIONALI", False, "intl")

    if args.tune and tuned_any:
        save_params(params)
        L += ["", "Parametri salvati in data/params.json: l'Oracolo li usera' dalla prossima analisi "
              "(per tornare indietro basta cancellare quel file)."]
    L += ["", "Come leggerlo: se 'previsto' e 'reale' sono vicini il modello e' affidabile; "
          "se il modello e' peggiore del mercato conviene correggerlo con le quote."]
    print("\n" + "\n".join(L))


if __name__ == "__main__":
    main()
