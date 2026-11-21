#!/usr/bin/env python3
"""
L'Oracolo - consigli sul calcio: 5 top campionati europei + nazionali
----------------------------------------------------------
Per ogni partita ti dice COSA giocare (es. "Genoa-Parma: Under 3.5") e la
QUOTA MINIMA sotto cui non conviene. Nessuna chiave API, nessun login.
Solo libreria standard (Pyto, Pythonista, a-Shell, GitHub Actions).

Fonti:
  Club:      football-data.co.uk (storico + calendario con quote)
  Nazionali: calendario da ESPN (endpoint pubblico non ufficiale), poi dataset
             github.com/martj42/international_results, poi lista manuale.

Uso:
  python value_bet.py              # partite di oggi
  python value_bet.py --days 3     # prossimi 3 giorni
  python value_bet.py --no-intl    # salta le nazionali
  python value_bet.py --no-club    # salta i club

Notifica opzionale: variabile d'ambiente NTFY_TOPIC (ntfy.sh).
"""
import argparse
import csv
import difflib
import io
import json
import math
import os
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

# ----------------------------- CONFIG ---------------------------------
LEAGUES = {
    "E0": "Premier League",
    "SP1": "La Liga",
    "D1": "Bundesliga",
    "I1": "Serie A",
    "F1": "Ligue 1",
}
FIXTURES_URL = "https://www.football-data.co.uk/fixtures.csv"
HISTORY_URL = "https://www.football-data.co.uk/mmz4281/{season}/{div}.csv"
INTL_URL = ("https://raw.githubusercontent.com/martj42/international_results/"
            "master/results.csv")
ESPN_URL = ("https://site.api.espn.com/apis/site/v2/sports/soccer/"
            "{slug}/scoreboard?dates={d}")
# (slug ESPN, etichetta). Gli slug sbagliati o non disponibili vengono ignorati.
ESPN_INTL = [
    ("uefa.nations", "Nations League"),
    ("fifa.friendly", "Amichevole"),
    ("uefa.euroq", "Qualificazioni Euro"),
    ("fifa.worldq.uefa", "Qualificazioni Mondiali UEFA"),
    ("fifa.worldq.conmebol", "Qualificazioni Mondiali CONMEBOL"),
    ("fifa.worldq.concacaf", "Qualificazioni Mondiali CONCACAF"),
    ("fifa.worldq.caf", "Qualificazioni Mondiali CAF"),
    ("fifa.worldq.afc", "Qualificazioni Mondiali AFC"),
    ("concacaf.nations.league", "Nations League CONCACAF"),
]

# Infortuni Premier League: API ufficiale del Fantasy Premier League (gratis, senza chiave)
FPL_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"
FPL_ATT_WEIGHT = 0.5    # quanto pesa l'assenza di chi segna/fa assist (0-1)
FPL_ATT_CAP = 0.25      # calo massimo dei gol attesi di una squadra
FPL_GK_BOOST = 0.07     # +7% ai gol dell'avversario se manca il portiere titolare
FPL_DEF_BOOST = 0.025   # +2.5% per ogni difensore titolare assente
FPL_DEF_CAP = 0.15      # aumento massimo dei gol subiti
# Queste cifre sono stime: andranno tarate quando avremo lo storico dei risultati.

# Props (cartellini, tiri in porta): margine piu' alto perche' i bookmaker
# applicano commissioni maggiori su questi mercati.
PROPS_MARGIN = 1.10
YELLOW_PRIOR_RATE = 0.12   # ammonizioni ogni 90' di un giocatore "medio"
YELLOW_PRIOR_W = 5.0       # peso di questo valore iniziale (in partite da 90')

# Calendario di riserva per i club (ESPN, non ufficiale): serve quando fixtures.csv
# di football-data.co.uk non contiene ancora la giornata richiesta.
ESPN_CLUB = {"E0": "eng.1", "SP1": "esp.1", "D1": "ger.1", "I1": "ita.1", "F1": "fra.1"}

HALF_LIFE_CLUB = 240     # giorni: il peso di una partita si dimezza ogni 240 gg
HALF_LIFE_INTL = 365
INTL_YEARS = 4
SHRINK_CLUB = 4.0        # "partite fittizie" che avvicinano alla media
SHRINK_INTL = 3.0
MIN_W_CLUB = 4.0         # dati minimi (pesati) per fidarsi di una squadra
MIN_W_INTL = 2.0
DC_RHO = -0.10           # correzione Dixon-Coles per i punteggi bassi

# Partite squilibrate: le goleade sono piu' frequenti di quanto dica il modello,
# quindi per queste non consiglio gli Under.
MISMATCH_XG_CLUB = 1.8   # club: la favorita segna 1.8+ gol attesi
MISMATCH_XG_INTL = 1.8   # nazionali
MISMATCH_RATIO = 2.2     # oppure ha almeno 2.2 volte i gol attesi dell'altra
SPREAD_CLUB = 0.0        # incertezza sui gol totali (0 = nessuna), club
SPREAD_INTL = 0.0        # idem, nazionali
UNDER_KEYS = ("U1.5", "U2.5", "U3.5")

MIN_PROB = 0.60          # sotto questa probabilita' non consiglio nulla
MAX_SAFE_PROB = 0.80     # sopra questa la quota sarebbe < 1.25: gamba che non rende
BOLD_MIN_PROB = 0.55     # per l'alternativa "piu' quota"
MARGIN = 1.05            # quota minima = MARGIN / probabilita'

# Lista di riserva per le nazionali (usata solo se le altre fonti falliscono).
# (casa, ospite, campo_neutro, competizione). Nomi come nel dataset martj42.
MANUAL_DATE = date(2026, 11, 17)
MANUAL_FIXTURES = [
    ("Denmark", "Norway", False, "Nations League"),
    ("Wales", "Portugal", False, "Nations League"),
    ("Bosnia and Herzegovina", "Romania", False, "Nations League"),
    ("Republic of Ireland", "Kosovo", False, "Nations League"),
    ("Israel", "Austria", True, "Nations League"),
    ("Poland", "Sweden", False, "Nations League"),
    ("Ukraine", "Georgia", True, "Nations League"),
    ("Hungary", "Northern Ireland", False, "Nations League"),
]

NAME_ALIASES = {
    # club: nomi ESPN -> nomi football-data
    "Inter Milan": "Inter", "Internazionale": "Inter", "AC Milan": "Milan",
    "AS Roma": "Roma", "Atalanta BC": "Atalanta", "Bologna FC": "Bologna",
    "Hellas Verona": "Verona", "Manchester United": "Man United",
    "Manchester City": "Man City", "Tottenham Hotspur": "Tottenham",
    "Newcastle United": "Newcastle", "Wolverhampton Wanderers": "Wolves",
    "Nottingham Forest": "Nott'm Forest", "Brighton & Hove Albion": "Brighton",
    "West Ham United": "West Ham", "Leeds United": "Leeds", "AFC Bournemouth": "Bournemouth",
    "Leicester City": "Leicester", "Ipswich Town": "Ipswich",
    "Atletico Madrid": "Ath Madrid", "Athletic Club": "Ath Bilbao", "Real Betis": "Betis",
    "Real Sociedad": "Sociedad", "Celta Vigo": "Celta", "Rayo Vallecano": "Vallecano",
    "Espanyol": "Espanol", "Deportivo Alaves": "Alaves", "Real Valladolid": "Valladolid",
    "Borussia Dortmund": "Dortmund", "Bayer Leverkusen": "Leverkusen",
    "Borussia Monchengladbach": "M'gladbach", "Eintracht Frankfurt": "Ein Frankfurt",
    "VfB Stuttgart": "Stuttgart", "VfL Wolfsburg": "Wolfsburg", "SC Freiburg": "Freiburg",
    "FSV Mainz 05": "Mainz", "1. FC Union Berlin": "Union Berlin", "FC Augsburg": "Augsburg",
    "TSG Hoffenheim": "Hoffenheim", "1. FC Heidenheim 1846": "Heidenheim",
    "FC St. Pauli": "St Pauli", "VfL Bochum": "Bochum", "Bayern Munich": "Bayern Munich",
    "Paris Saint-Germain": "Paris SG", "Olympique de Marseille": "Marseille",
    "Olympique Lyonnais": "Lyon", "AS Monaco": "Monaco", "Stade Rennais": "Rennes",
    "OGC Nice": "Nice", "LOSC Lille": "Lille", "RC Lens": "Lens", "Stade de Reims": "Reims",
    "Stade Brestois 29": "Brest", "RC Strasbourg": "Strasbourg", "FC Nantes": "Nantes",
    "Montpellier HSC": "Montpellier", "Toulouse FC": "Toulouse", "Angers SCO": "Angers",
    "AJ Auxerre": "Auxerre", "Le Havre AC": "Le Havre", "Saint-Etienne": "St Etienne",
    # neopromosse: il nome football-data puo' variare, provo le varianti in ordine
    "Coventry City": ["Coventry"], "Hull City": ["Hull"],
    "Deportivo": ["La Coruna", "Deportivo", "Dep. La Coruna"],
    "Racing Santander": ["Santander", "Racing Santander", "Racing"],
    "FC Cologne": ["FC Koln", "Koln", "Cologne"],
    "SC Paderborn 07": ["Paderborn", "Paderborn 07"],
    "Bosnia-Herzegovina": "Bosnia and Herzegovina",
    "Bosnia & Herzegovina": "Bosnia and Herzegovina",
    "Man Utd": "Man United",
    "Spurs": "Tottenham",
    "Ireland": "Republic of Ireland",
    "Czechia": "Czech Republic",
    "Turkiye": "Turkey",
    "Türkiye": "Turkey",
    "USA": "United States",
    "Korea Republic": "South Korea",
    "Cape Verde Islands": "Cape Verde",
    "Cote d'Ivoire": "Ivory Coast",
    "Côte d'Ivoire": "Ivory Coast",
}

BOOK_PREFIXES = ["B365", "BW", "IW", "PS", "P", "WH", "VC", "Max"]
BOOK_NAMES = {"B365": "Bet365", "BW": "Betway", "IW": "Interwetten",
              "PS": "Pinnacle", "P": "Pinnacle", "WH": "William Hill",
              "VC": "VC Bet", "Max": "miglior quota"}

# chiave -> descrizione
MARKET_LABELS = {
    "1": "1 (vince la casa)", "X": "X (pareggio)", "2": "2 (vince l'ospite)",
    "1X": "1X (casa o pareggio)", "X2": "X2 (ospite o pareggio)",
    "12": "12 (no pareggio)",
    "U1.5": "Under 1.5 gol", "O1.5": "Over 1.5 gol",
    "U2.5": "Under 2.5 gol", "O2.5": "Over 2.5 gol",
    "U3.5": "Under 3.5 gol", "O3.5": "Over 3.5 gol",
    "GG": "Gol (segnano entrambe)", "NG": "No Gol (almeno una a secco)",
}
MARKET_LABELS.update({
    "MG13": "Multigol 1-3", "MG14": "Multigol 1-4", "MG24": "Multigol 2-4", "MG25": "Multigol 2-5",
    "EP1": "Casa vince o va avanti di 2 (pagamento anticipato)",
    "EP2": "Ospite vince o va avanti di 2 (pagamento anticipato)",
    "1X&U3.5": "1X + Under 3.5", "X2&U3.5": "X2 + Under 3.5",
    "1X&O1.5": "1X + Over 1.5", "X2&O1.5": "X2 + Over 1.5", "GG&O2.5": "Gol + Over 2.5",
})
# gruppi di proposte mostrati nella pagina: (titolo, mercati, margine del bookmaker)
EXTRA_MARGIN = 1.10      # multigol, combo e risultati esatti: commissioni piu' alte
MARKET_GROUPS = [
    ("Esito e doppie chance", ["1", "X", "2", "1X", "X2", "12"], None),
    ("Gol totali", ["U1.5", "O1.5", "U2.5", "O2.5", "U3.5", "O3.5"], None),
    ("Gol / No Gol", ["GG", "NG"], None),
    ("Multigol", ["MG13", "MG14", "MG24", "MG25"], EXTRA_MARGIN),
    ("Pagamento anticipato", ["EP1", "EP2"], EXTRA_MARGIN),
    ("Combo", ["1X&U3.5", "X2&U3.5", "1X&O1.5", "X2&O1.5", "GG&O2.5"], EXTRA_MARGIN),
]
# colonne quote football-data per i mercati che ci sono nel CSV
ODDS_COLUMNS = {"1": "H", "X": "D", "2": "A", "O2.5": ">2.5", "U2.5": "<2.5"}


# Ancoraggio alle quote di mercato (solo club con quote nel calendario): 0 = solo modello,
# 1 = le quote 1X2 e Over/Under 2.5 decidono tutto. Il backtest dice se conviene.
MARKET_W = 0.8

# Parametri ottimizzati dal backtest: se esiste data/params.json sostituiscono
# i valori di default qui sopra (per tornare indietro basta cancellare il file).
PARAMS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "params.json")
try:
    with open(PARAMS_FILE, encoding="utf-8") as _f:
        _P = json.load(_f)
except Exception:
    _P = {}
HALF_LIFE_CLUB = _P.get("half_life_club", HALF_LIFE_CLUB)
HALF_LIFE_INTL = _P.get("half_life_intl", HALF_LIFE_INTL)
SHRINK_CLUB = _P.get("shrink_club", SHRINK_CLUB)
SHRINK_INTL = _P.get("shrink_intl", SHRINK_INTL)
SPREAD_CLUB = _P.get("spread_club", SPREAD_CLUB)
SPREAD_INTL = _P.get("spread_intl", SPREAD_INTL)
DC_RHO = _P.get("dc_rho", DC_RHO)
MISMATCH_XG_CLUB = _P.get("mismatch_xg_club", MISMATCH_XG_CLUB)
MISMATCH_XG_INTL = _P.get("mismatch_xg_intl", MISMATCH_XG_INTL)
MISMATCH_RATIO = _P.get("mismatch_ratio", MISMATCH_RATIO)
MARKET_W = _P.get("market_w", MARKET_W)


# ----------------------------- UTILITA' --------------------------------
def download(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode("utf-8-sig", errors="replace")


def read_csv(text):
    return list(csv.DictReader(io.StringIO(text)))


def parse_date(s):
    s = (s or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def season_codes(today):
    start = today.year if today.month >= 7 else today.year - 1
    return ["%02d%02d" % ((start - i) % 100, (start - i + 1) % 100)
            for i in range(2)]


def best_odds(row, suffix):
    best, src = None, None
    for p in BOOK_PREFIXES:
        try:
            v = float(row.get(p + suffix))
        except (TypeError, ValueError):
            continue
        if v > 1.0 and (best is None or v > best):
            best, src = v, p
    return best, BOOK_NAMES.get(src, src)


def avg_odds(row, suffix):
    """Quota media dei bookmaker (colonna Avg se c'e', altrimenti media dei singoli)."""
    try:
        v = float(row.get("Avg" + suffix))
        if v > 1.0:
            return v
    except (TypeError, ValueError):
        pass
    vals = []
    for p in BOOK_PREFIXES:
        if p == "Max":
            continue
        try:
            v = float(row.get(p + suffix))
        except (TypeError, ValueError):
            continue
        if v > 1.0:
            vals.append(v)
    return sum(vals) / len(vals) if vals else None


def odds_sane(row):
    """False se le quote 1X2 sono rotte: margine fuori norma o una quota Max
    troppo lontana dalla media (di solito un errore di inserimento)."""
    inv = 0.0
    for sfx in ("H", "D", "A"):
        a = avg_odds(row, sfx)
        if not a:
            return False
        inv += 1.0 / a
        mx, _ = best_odds(row, sfx)
        if mx and mx > 1.30 * a:
            return False
    return 1.00 <= inv <= 1.15


def _plain(name):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", name or "")
                   if not unicodedata.combining(c))


def resolve_name(name, known):
    if name in known:
        return name
    plain = _plain(name)
    for cand in (name, plain):
        alt = NAME_ALIASES.get(cand)
        for a in ([alt] if isinstance(alt, str) else (alt or [])):
            if a in known:
                return a
    if plain in known:
        return plain
    close = difflib.get_close_matches(plain, list(known), n=1, cutoff=0.78)
    if close:
        return close[0]
    # nome piu' corto contenuto in quello piu' lungo (es. "Coventry City" / "Coventry"),
    # solo se il candidato e' uno solo
    pl = plain.lower()
    hits = [k for k in known if len(k) >= 5 and (k.lower() in pl or pl in k.lower())]
    return hits[0] if len(hits) == 1 else None


# ----------------------------- MODELLO ---------------------------------
def fit_poisson(matches, k, iters=25):
    """matches: (casa, ospite, gol_casa, gol_ospite, peso, neutro).
    Stima attacco/difesa corretti per la forza degli avversari."""
    tw = sum(m[4] for m in matches)
    if tw <= 0:
        return None
    mu = max(sum(m[4] * (m[2] + m[3]) for m in matches) / (2 * tw), 0.3)

    hg_tot = sum(m[4] * m[2] for m in matches if not m[5])
    ag_tot = sum(m[4] * m[3] for m in matches if not m[5])
    ha = math.sqrt(hg_tot / ag_tot) if hg_tot > 0 and ag_tot > 0 else 1.0

    att = defaultdict(lambda: 1.0)
    dfn = defaultdict(lambda: 1.0)
    team_w = defaultdict(float)
    for h, a, _, _, w, _ in matches:
        team_w[h] += w
        team_w[a] += w

    for _ in range(iters):
        na, da = defaultdict(float), defaultdict(float)
        nd, dd = defaultdict(float), defaultdict(float)
        for h, a, hg, ag, w, neu in matches:
            fh = 1.0 if neu else ha
            fa = 1.0 if neu else 1.0 / ha
            na[h] += w * hg
            da[h] += w * mu * dfn[a] * fh
            na[a] += w * ag
            da[a] += w * mu * dfn[h] * fa
            nd[h] += w * ag
            dd[h] += w * mu * att[a] * fa
            nd[a] += w * hg
            dd[a] += w * mu * att[h] * fh
        for t in team_w:
            att[t] = (na[t] + k * mu) / (da[t] + k * mu)
            dfn[t] = (nd[t] + k * mu) / (dd[t] + k * mu)

    return {"mu": mu, "ha": ha, "att": dict(att), "dfn": dict(dfn),
            "team_w": dict(team_w)}


def expected_goals(model, home, away, neutral, min_w):
    tw = model["team_w"]
    if tw.get(home, 0) < min_w or tw.get(away, 0) < min_w:
        return None
    mu, ha = model["mu"], model["ha"]
    fh = 1.0 if neutral else ha
    fa = 1.0 if neutral else 1.0 / ha
    lh = mu * model["att"][home] * model["dfn"][away] * fh
    la = mu * model["att"][away] * model["dfn"][home] * fa
    return lh, la


def poisson_pmf(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def tau(i, j, lh, la, rho):
    """Correzione Dixon-Coles: i punteggi bassi (0-0, 1-0, 0-1, 1-1) non
    sono indipendenti. Importante per Under 1.5 / 2.5 e pareggi."""
    if i == 0 and j == 0:
        return 1 - lh * la * rho
    if i == 0 and j == 1:
        return 1 + lh * rho
    if i == 1 and j == 0:
        return 1 + la * rho
    if i == 1 and j == 1:
        return 1 - rho
    return 1.0


def _market_probs_single(lh, la, maxg=10):
    grid, total = {}, 0.0
    for i in range(maxg + 1):
        for j in range(maxg + 1):
            p = poisson_pmf(i, lh) * poisson_pmf(j, la) * tau(i, j, lh, la, DC_RHO)
            grid[(i, j)] = p
            total += p

    def P(cond):
        return sum(p for (i, j), p in grid.items() if cond(i, j)) / total

    return {
        "1": P(lambda i, j: i > j),
        "X": P(lambda i, j: i == j),
        "2": P(lambda i, j: i < j),
        "1X": P(lambda i, j: i >= j),
        "X2": P(lambda i, j: i <= j),
        "12": P(lambda i, j: i != j),
        "U1.5": P(lambda i, j: i + j <= 1),
        "O1.5": P(lambda i, j: i + j >= 2),
        "U2.5": P(lambda i, j: i + j <= 2),
        "O2.5": P(lambda i, j: i + j >= 3),
        "U3.5": P(lambda i, j: i + j <= 3),
        "O3.5": P(lambda i, j: i + j >= 4),
        "GG": P(lambda i, j: i > 0 and j > 0),
        "NG": P(lambda i, j: i == 0 or j == 0),
    }


def market_probs(lh, la, maxg=10, spread=0.0):
    """Probabilita' dei mercati. Con spread > 0 media tre scenari di gol totali
    (-spread, 0, +spread): rende piu' realistiche le code (goleade e 0-0)."""
    if spread <= 0:
        return _market_probs_single(lh, la, maxg)
    acc = None
    for sgn, w in ((-1, 0.3), (0, 0.4), (1, 0.3)):
        f = 1 + sgn * spread
        p = _market_probs_single(lh * f, la * f, maxg)
        if acc is None:
            acc = {k: w * v for k, v in p.items()}
        else:
            for k, v in p.items():
                acc[k] += w * v
    return acc


def is_mismatch(xg, kind="club"):
    lh, la = xg
    hi, lo = max(lh, la), max(min(lh, la), 0.05)
    thr = MISMATCH_XG_INTL if kind == "intl" else MISMATCH_XG_CLUB
    return hi >= thr or hi / lo >= MISMATCH_RATIO


def choose_picks(probs):
    """Ritorna (giocata_sicura, giocata_piu_quota) come (chiave, prob)."""
    safe_c = [(k, p) for k, p in probs.items() if MIN_PROB <= p <= MAX_SAFE_PROB]
    if not safe_c:
        return None, None
    safe = max(safe_c, key=lambda x: x[1])
    bold_c = [(k, p) for k, p in probs.items()
              if BOLD_MIN_PROB <= p <= safe[1] - 0.06 and k != safe[0]]
    bold = min(bold_c, key=lambda x: x[1]) if bold_c else None
    return safe, bold


# ----------------------------- CALENDARI -------------------------------
# ----------------------------- ANCORAGGIO AL MERCATO --------------------
def devig(odds):
    """Da quote a probabilita' senza la commissione del bookmaker."""
    inv = [1.0 / o for o in odds]
    t = sum(inv)
    return [i / t for i in inv]


def market_target(r):
    """Probabilita' di mercato dalla riga di fixtures.csv: {"p": [1, X, 2], "ou": Over2.5}
    oppure None se mancano le quote."""
    def read(prefixes, suffixes):
        vals = []
        for p in prefixes:
            try:
                v = [float(r.get(p + sfx)) for sfx in suffixes]
            except (TypeError, ValueError):
                continue
            if all(x > 1.0 for x in v):
                vals.append(devig(v))
        return [sum(c) / len(vals) for c in zip(*vals)] if vals else None
    others = [p for p in BOOK_PREFIXES if p != "Max"]
    p3 = read(["Avg"], ("H", "D", "A")) or read(others, ("H", "D", "A"))
    if not p3:
        return None
    ou = read(["Avg"], (">2.5", "<2.5")) or read(others, (">2.5", "<2.5"))
    return {"p": p3, "ou": ou[0] if ou else None}


def _quad(lh, la, spread=0.0, maxg=8):
    """(P(1), P(X), P(2), P(Over 2.5)) con lo stesso modello di market_probs."""
    scen = [(1.0, 1.0)] if spread <= 0 else [(0.3, 1 - spread), (0.4, 1.0), (0.3, 1 + spread)]
    r1 = rx = r2 = ro = 0.0
    for w, f in scen:
        a, b = lh * f, la * f
        pa = [poisson_pmf(i, a) for i in range(maxg + 1)]
        pb = [poisson_pmf(i, b) for i in range(maxg + 1)]
        t = s1 = sx = s2 = so = 0.0
        for i in range(maxg + 1):
            for j in range(maxg + 1):
                p = pa[i] * pb[j] * tau(i, j, a, b, DC_RHO)
                t += p
                if i > j:
                    s1 += p
                elif i == j:
                    sx += p
                else:
                    s2 += p
                if i + j >= 3:
                    so += p
        r1 += w * s1 / t
        rx += w * sx / t
        r2 += w * s2 / t
        ro += w * so / t
    return r1, rx, r2, ro


def anchor_xg(xg, target, w, spread=0.0):
    """Sposta i gol attesi verso le probabilita' di mercato (peso w).
    Con w=0 non cambia nulla; con w=1 il modello riproduce 1X2 e Over 2.5 del mercato."""
    if w <= 0 or not target:
        return xg
    lh, la = xg
    cur = _quad(lh, la, spread)
    t3 = [(1 - w) * cur[i] + w * target["p"][i] for i in range(3)]
    tou = None if target.get("ou") is None else (1 - w) * cur[3] + w * target["ou"]

    def loss(a, b):
        q = _quad(a, b, spread)
        e = sum((q[i] - t3[i]) ** 2 for i in range(3))
        if tou is not None:
            e += (q[3] - tou) ** 2
        return e

    best = loss(lh, la)
    for step in (0.2, 0.08, 0.03, 0.01):
        for _ in range(8):
            moved = False
            for dh, da in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)):
                nh, na = lh + dh * step, la + da * step
                if not (0.1 <= nh <= 6.0 and 0.1 <= na <= 6.0):
                    continue
                e = loss(nh, na)
                if e < best - 1e-12:
                    best, lh, la, moved = e, nh, na, True
            if not moved:
                break
    return lh, la


# ----------------------------- ALTRE PROPOSTE PER PARTITA ---------------
def score_markets(hg, ag):
    """Quali mercati sono 'passati' con questo punteggio (anche quelli extra)."""
    t = hg + ag
    d = {"1": hg > ag, "X": hg == ag, "2": hg < ag, "1X": hg >= ag, "X2": hg <= ag,
         "12": hg != ag, "U1.5": t <= 1, "O1.5": t >= 2, "U2.5": t <= 2, "O2.5": t >= 3,
         "U3.5": t <= 3, "O3.5": t >= 4, "GG": hg > 0 and ag > 0, "NG": hg == 0 or ag == 0,
         "MG13": 1 <= t <= 3, "MG14": 1 <= t <= 4, "MG24": 2 <= t <= 4, "MG25": 2 <= t <= 5,
         "1X&U3.5": hg >= ag and t <= 3, "X2&U3.5": hg <= ag and t <= 3,
         "1X&O1.5": hg >= ag and t >= 2, "X2&O1.5": hg <= ag and t >= 2,
         "GG&O2.5": hg > 0 and ag > 0 and t >= 3}
    d["ES%d-%d" % (hg, ag)] = True
    return d


def base_rates(results):
    """results: lista di (gol casa, gol ospite, peso). Frequenza media di ogni mercato."""
    sums, tw = defaultdict(float), 0.0
    for hg, ag, w in results:
        tw += w
        for k, v in score_markets(hg, ag).items():
            if v:
                sums[k] += w
    return {k: v / tw for k, v in sums.items()} if tw > 0 else {}


def early_payout(lh, la, spread=0.0, steps=90):
    """(P casa, P ospite) di 'vince o va avanti di 2 gol in qualunque momento'.
    I gol arrivano a caso nei 90 minuti; calcolo passo per passo la differenza reti."""
    scen = [(1.0, 1.0)] if spread <= 0 else [(0.3, 1 - spread), (0.4, 1.0), (0.3, 1 + spread)]
    res = []
    for sign in (1, -1):
        tot = 0.0
        for w, f in scen:
            a, b = lh * f / steps, la * f / steps
            ph, pa = 1 - math.exp(-a), 1 - math.exp(-b)
            if sign < 0:
                ph, pa = pa, ph
            lo = -8
            st = {0: 1.0}          # differenza (nostra squadra - avversaria), non ancora a +2
            hit = 0.0
            for _ in range(steps):
                new = defaultdict(float)
                for d, p in st.items():
                    for dh, qh in ((1, ph), (0, 1 - ph)):
                        for da, qa in ((1, pa), (0, 1 - pa)):
                            q = p * qh * qa
                            nd = d + dh - da
                            if nd >= 2:
                                hit += q
                            else:
                                new[max(nd, lo)] += q
                st = new
            win = sum(p for d, p in st.items() if d >= 1)
            tot += w * (hit + win)
        res.append(min(tot, 1.0))
    return res[0], res[1]


def extra_probs(lh, la, spread=0.0, maxg=8):
    """Probabilita' di multigol, combo e dei 3 risultati esatti piu' probabili."""
    scen = [(1.0, 1.0)] if spread <= 0 else [(0.3, 1 - spread), (0.4, 1.0), (0.3, 1 + spread)]
    acc, score = defaultdict(float), defaultdict(float)
    for w, f in scen:
        a, b = lh * f, la * f
        pa = [poisson_pmf(i, a) for i in range(maxg + 1)]
        pb = [poisson_pmf(i, b) for i in range(maxg + 1)]
        grid, t = {}, 0.0
        for i in range(maxg + 1):
            for j in range(maxg + 1):
                p = pa[i] * pb[j] * tau(i, j, a, b, DC_RHO)
                grid[(i, j)] = p
                t += p
        for (i, j), p in grid.items():
            pn = w * p / t
            score[(i, j)] += pn
            for k, v in score_markets(i, j).items():
                if v and k[:2] in ("MG", "1X", "X2", "GG") and k in MARKET_LABELS \
                        and k not in ("1X", "X2", "GG"):
                    acc[k] += pn
    out = dict(acc)
    out["EP1"], out["EP2"] = early_payout(lh, la, spread)
    for (i, j), p in sorted(score.items(), key=lambda kv: -kv[1])[:3]:
        out["ES%d-%d" % (i, j)] = p
    return out


def market_groups(m):
    """Elenco delle proposte per la pagina: per gruppo, con probabilita',
    quota minima e scarto (in punti) dalla media storica del campionato."""
    probs = dict(m["probs"])
    probs.update(m.get("extra", {}))
    base = m.get("base") or {}

    def item(k, label, p, marg):
        d = {"label": label, "prob": round(p, 4), "minodds": round(marg / p, 2)}
        if base:
            d["delta"] = int(round((p - base.get(k, 0.0)) * 100))
        return d

    groups = []
    for name, keys, marg in MARKET_GROUPS:
        items = []
        for k in keys:
            p = probs.get(k)
            if p is None or p < 0.10 or p > 0.92:
                continue
            if m.get("mismatch") and k in UNDER_KEYS:
                continue
            items.append(item(k, MARKET_LABELS[k], p, marg or MARGIN))
        items.sort(key=lambda x: -x["prob"])
        if items:
            groups.append({"name": name, "items": items})
    ex = sorted(((k, p) for k, p in probs.items() if k.startswith("ES")), key=lambda kv: -kv[1])[:3]
    if ex:
        groups.append({"name": "Risultati esatti piu' probabili",
                       "items": [item(k, "Risultato " + k[2:], p, EXTRA_MARGIN) for k, p in ex]})
    return groups


def best_picks(groups, n=6):
    """Le giocate migliori della partita tra tutti i mercati: probabilita' almeno 58%,
    quota minima almeno 1.15 (sotto non vale la pena), al massimo 2 per famiglia di mercati."""
    pool = []
    for g in groups:
        if g["name"].startswith("Risultati"):
            continue
        for it in g["items"]:
            if it["prob"] >= 0.58 and it["minodds"] >= 1.15:
                pool.append((g["name"], it))
    pool.sort(key=lambda x: -x[1]["prob"])
    per, out = defaultdict(int), []
    for name, it in pool:
        if per[name] >= 2:
            continue
        per[name] += 1
        out.append(dict(it, family=name))
        if len(out) >= n:
            break
    return out


def load_club_rows(div, today):
    """Storico partite con gol, gialli e tiri in porta (se presenti nel CSV)."""
    out = []
    for code in season_codes(today):
        url = HISTORY_URL.format(season=code, div=div)
        try:
            rows = read_csv(download(url))
        except Exception as e:
            print("  ! storico %s %s non scaricabile: %s" % (div, code, e))
            continue
        for r in rows:
            d = parse_date(r.get("Date"))
            try:
                hg, ag = int(r["FTHG"]), int(r["FTAG"])
            except (KeyError, ValueError, TypeError):
                continue
            if not d or d >= today:
                continue
            w = 0.5 ** ((today - d).days / HALF_LIFE_CLUB)
            stats = {"hg": hg, "ag": ag}
            for key, col in (("hy", "HY"), ("ay", "AY"), ("hst", "HST"), ("ast", "AST")):
                try:
                    stats[key] = int(r[col])
                except (KeyError, ValueError, TypeError):
                    stats[key] = None
            out.append((r["HomeTeam"], r["AwayTeam"], w, stats))
    return out


def build_models(rows):
    """Un modello per i gol, uno per i cartellini gialli, uno per i tiri in porta."""
    def mk(kh, ka):
        ms = [(h, a, s[kh], s[ka], w, False) for h, a, w, s in rows
              if s[kh] is not None and s[ka] is not None]
        return fit_poisson(ms, SHRINK_CLUB) if ms else None
    return {"goals": mk("hg", "ag"), "cards": mk("hy", "ay"), "sot": mk("hst", "ast"),
            "base": base_rates([(s["hg"], s["ag"], 1.0) for h, a, w, s in rows])}


# ----------------------------- INFORTUNI (FPL) ---------------------------
STATUS_LABELS = {"i": "infortunato", "d": "in dubbio", "s": "squalificato",
                 "u": "non disponibile", "n": "non disponibile"}


def load_fpl_map(known):
    """Ritorna {nome squadra football-data: [giocatori FPL]} oppure None."""
    try:
        data = json.loads(download(FPL_URL))
    except Exception as e:
        print("  ! FPL non disponibile: %s" % e)
        return None
    names = {t["id"]: t["name"] for t in data.get("teams", [])}
    out = defaultdict(list)
    for p in data.get("elements", []):
        fpl_name = names.get(p.get("team"))
        if not fpl_name:
            continue
        mapped = resolve_name(fpl_name, known)
        if mapped:
            out[mapped].append(p)
    return dict(out)


def miss_prob(p):
    """Probabilita' (0-1) che il giocatore non giochi."""
    chance = p.get("chance_of_playing_next_round")
    status = p.get("status")
    if status in ("i", "s", "u", "n"):
        return 1.0 if chance is None else 1.0 - chance / 100.0
    if status == "d":
        return 0.5 if chance is None else 1.0 - chance / 100.0
    return 0.0


def team_absence_effect(players):
    """Ritorna (calo_attacco, aumento_gol_subiti, assenze_chiave)."""
    tot = sum(p.get("goals_scored", 0) + 0.5 * p.get("assists", 0) for p in players)
    max_min = max((p.get("minutes", 0) for p in players), default=0)
    gks = sorted((p for p in players if p.get("element_type") == 1),
                 key=lambda p: p.get("minutes", 0), reverse=True)
    starter_gk = gks[0] if gks else None
    att_loss = def_up = 0.0
    key = []
    for p in players:
        mp = miss_prob(p)
        if mp <= 0:
            continue
        share = ((p.get("goals_scored", 0) + 0.5 * p.get("assists", 0)) / tot) if tot > 0 else 0.0
        starter = max_min > 0 and p.get("minutes", 0) >= 0.5 * max_min
        relevant = False
        if share >= 0.04:
            att_loss += FPL_ATT_WEIGHT * share * mp
            relevant = True
        if p is starter_gk and starter:
            def_up += FPL_GK_BOOST * mp
            relevant = True
        elif p.get("element_type") == 2 and starter:
            def_up += FPL_DEF_BOOST * mp
            relevant = True
        if relevant:
            label = STATUS_LABELS.get(p.get("status"), "assente")
            chance = p.get("chance_of_playing_next_round")
            if chance is not None and 0 < chance < 100:
                label += " %d%%" % chance
            key.append((share * mp, "%s (%s)" % (p.get("web_name", "?"), label)))
    key.sort(reverse=True)
    return att_loss, def_up, [k[1] for k in key[:3]]


def apply_absences(xg, home_players, away_players):
    lh, la = xg
    h_loss, h_def, h_names = team_absence_effect(home_players)
    a_loss, a_def, a_names = team_absence_effect(away_players)
    new_lh = lh * (1 - min(h_loss, FPL_ATT_CAP)) * (1 + min(a_def, FPL_DEF_CAP))
    new_la = la * (1 - min(a_loss, FPL_ATT_CAP)) * (1 + min(h_def, FPL_DEF_CAP))
    notes = []
    if h_names:
        notes.append("Assenze casa: " + ", ".join(h_names))
    if a_names:
        notes.append("Assenze ospite: " + ", ".join(a_names))
    dh, da = new_lh / lh - 1, new_la / la - 1
    if notes and (abs(dh) >= 0.01 or abs(da) >= 0.01):
        notes.append("Effetto sui gol attesi: casa %+.0f%%, ospite %+.0f%%" % (dh * 100, da * 100))
    return (new_lh, new_la), notes


# ----------------------------- PROPS: CARTELLINI E TIRI IN PORTA ---------
def poisson_over(lam, line):
    """P(X > line) per una Poisson, con line del tipo 3.5."""
    k = int(line)
    cdf = sum(poisson_pmf(i, lam) for i in range(k + 1))
    return max(0.0, min(1.0, 1 - cdf))


def count_markets(label, home, away, lam_h, lam_a, total_lines, team_lines):
    out = {}
    for line in total_lines:
        p = poisson_over(lam_h + lam_a, line)
        out["%s totali Over %.1f" % (label, line)] = p
        out["%s totali Under %.1f" % (label, line)] = 1 - p
    for name, lam in ((home, lam_h), (away, lam_a)):
        for line in team_lines:
            p = poisson_over(lam, line)
            out["%s %s Over %.1f" % (label, name, line)] = p
            out["%s %s Under %.1f" % (label, name, line)] = 1 - p
    return out


def player_card_probs(players, lam_cards):
    """Probabilita' di ammonizione dei giocatori di una squadra (dati FPL).
    lam_cards = gialli attesi dalla squadra in questa partita."""
    max_min = max((p.get("minutes", 0) for p in players), default=0)
    if max_min <= 0:
        return []
    games = max_min / 90.0
    team_avg = sum(p.get("yellow_cards", 0) for p in players) / games
    f = lam_cards / team_avg if team_avg > 0 else 1.0
    f = max(0.7, min(1.4, f))
    out = []
    for p in players:
        exp_min = 90.0 * min(1.0, p.get("minutes", 0) / max_min) * (1 - miss_prob(p))
        if exp_min < 45:
            continue
        m90 = p.get("minutes", 0) / 90.0
        rate = (p.get("yellow_cards", 0) + YELLOW_PRIOR_RATE * YELLOW_PRIOR_W) / (m90 + YELLOW_PRIOR_W)
        prob = 1 - math.exp(-rate * (exp_min / 90.0) * f)
        out.append((prob, p.get("web_name", "?")))
    return out


def build_props(model, r, div, fpl_map):
    home, away = r["HomeTeam"], r["AwayTeam"]
    cards = (expected_goals(model["cards"], home, away, False, MIN_W_CLUB)
             if model.get("cards") else None)
    sot = (expected_goals(model["sot"], home, away, False, MIN_W_CLUB)
           if model.get("sot") else None)
    lines = []
    if cards:
        pool = count_markets("Cartellini", home, away, cards[0], cards[1], (3.5, 4.5, 5.5), (1.5, 2.5))
        safe, _ = choose_picks(pool)
        if safe:
            lines.append("%s - prob %.0f%% - gioca se quota >= %.2f" % (
                safe[0], safe[1] * 100, PROPS_MARGIN / safe[1]))
    if sot:
        pool = count_markets("Tiri in porta", home, away, sot[0], sot[1], (7.5, 8.5, 9.5), (3.5, 4.5, 5.5))
        safe, _ = choose_picks(pool)
        if safe:
            lines.append("%s - prob %.0f%% - gioca se quota >= %.2f" % (
                safe[0], safe[1] * 100, PROPS_MARGIN / safe[1]))
    if cards and div == "E0" and fpl_map:
        hp, ap = fpl_map.get(home), fpl_map.get(away)
        if hp and ap:
            cand = player_card_probs(hp, cards[0]) + player_card_probs(ap, cards[1])
            cand.sort(reverse=True)
            top = [c for c in cand[:3] if c[0] > 0.0]
            if top:
                lines.append("Ammonizione (probabilita' basse, rischio alto): " + ", ".join(
                    "%s %.0f%% (quota min %.2f)" % (n, p * 100, PROPS_MARGIN / p) for p, n in top))
    return lines


def espn_club_rows(div, day):
    """Partite non ancora giocate di un campionato in un giorno (calendario ESPN),
    nel formato delle righe di fixtures.csv. I nomi vanno poi mappati sul modello."""
    url = ESPN_URL.format(slug=ESPN_CLUB[div], d=day.strftime("%Y%m%d"))
    try:
        data = json.loads(download(url))
    except Exception:
        return []
    out = []
    for ev in data.get("events", []) or []:
        state = (((ev.get("status") or {}).get("type") or {}).get("state"))
        if state and state != "pre":
            continue
        comp = (ev.get("competitions") or [{}])[0]
        home = away = None
        for c in comp.get("competitors", []) or []:
            nm = (c.get("team") or {}).get("displayName")
            if c.get("homeAway") == "home":
                home = nm
            elif c.get("homeAway") == "away":
                away = nm
        dt = parse_iso_local(ev.get("date"))
        if not home or not away or not dt:
            continue
        out.append({"Div": div, "Date": dt.date().strftime("%d/%m/%Y"),
                    "Time": dt.strftime("%H:%M"), "HomeTeam": home, "AwayTeam": away,
                    "_espn": True})
    return out


def club_matches(today, last, with_props=True, divs=None):
    """Ritorna (lista_partite, note). Ogni partita e' un dict."""
    try:
        fixtures = read_csv(download(FIXTURES_URL))
    except Exception as e:
        return [], ["Calendario club non scaricabile: %s" % e]

    todays = []
    have = set()
    for r in fixtures:
        d = parse_date(r.get("Date"))
        if r.get("Div") in LEAGUES and (divs is None or r.get("Div") in divs) \
                and d and today <= d <= last:
            todays.append(r)
            have.add((r["Div"], d))

    # giorni senza partite in fixtures.csv: provo il calendario ESPN
    wanted = [x for x in LEAGUES if divs is None or x in divs]
    espn_seen, extra_notes = set(), []
    day = today
    while day <= last:
        for div in wanted:
            if (div, day) in have:
                continue
            for r in espn_club_rows(div, day):
                d = parse_date(r["Date"])
                key = (div, r["HomeTeam"], r["AwayTeam"])
                if d and today <= d <= last and key not in espn_seen \
                        and (div, d) not in have:
                    espn_seen.add(key)
                    todays.append(r)
        day += timedelta(days=1)
    if espn_seen:
        extra_notes.append("Calendario di %d partite preso da ESPN: per queste non ho le quote "
                           "dei bookmaker, solo la quota minima." % len(espn_seen))
    if not todays:
        label = ", ".join(LEAGUES[x] for x in sorted(divs) if x in LEAGUES) if divs else "i 5 campionati"
        return [], ["Nessuna partita di %s nel periodo richiesto (il calendario dei club "
                    "copre solo i giorni piu' vicini)." % label]

    models, out, skipped, bad_odds = {}, [], 0, 0

    def get_model(div):
        if div not in models:
            print("Calcolo modello %s..." % LEAGUES[div])
            rows_h = load_club_rows(div, today)
            models[div] = build_models(rows_h) if rows_h else None
        return models[div]

    # nomi ESPN -> nomi usati dal modello (football-data)
    mapped = []
    for r in todays:
        if r.get("_espn"):
            m0 = get_model(r["Div"])
            known = set(m0["goals"]["team_w"]) if m0 and m0.get("goals") else set()
            h2 = resolve_name(r["HomeTeam"], known) if known else None
            a2 = resolve_name(r["AwayTeam"], known) if known else None
            if not h2 or not a2:
                skipped += 1
                continue
            r["HomeTeam"], r["AwayTeam"] = h2, a2
        mapped.append(r)
    todays = mapped

    fpl_map, fpl_failed = None, False
    e0 = [r for r in todays if r["Div"] == "E0"]
    if e0:
        known = set(r["HomeTeam"] for r in e0) | set(r["AwayTeam"] for r in e0)
        fpl_map = load_fpl_map(known)
        fpl_failed = fpl_map is None

    for r in todays:
        div = r["Div"]
        model = get_model(div)
        gm = model["goals"] if model else None
        xg = (expected_goals(gm, r["HomeTeam"], r["AwayTeam"], False, MIN_W_CLUB)
              if gm else None)
        if not xg:
            skipped += 1
            continue
        anchored = False
        has_odds = bool(avg_odds(r, "H") or best_odds(r, "H")[0])
        sane = odds_sane(r) if has_odds else True
        if has_odds and not sane:
            bad_odds += 1
            r = {k: v for k, v in r.items()
                 if not any(k.startswith(p) for p in ["Avg", "Max"] + BOOK_PREFIXES)}
        tgt = market_target(r) if MARKET_W > 0 else None
        if tgt:
            xg = anchor_xg(xg, tgt, MARKET_W, SPREAD_CLUB)
            anchored = True
        match_notes = []
        if div == "E0" and fpl_map:
            hp, ap = fpl_map.get(r["HomeTeam"]), fpl_map.get(r["AwayTeam"])
            if hp and ap:
                xg, match_notes = apply_absences(xg, hp, ap)
        odds, avg = {}, {}
        for key, suffix in ODDS_COLUMNS.items():
            o, book = best_odds(r, suffix)
            if o:
                odds[key] = (o, book)
                a = avg_odds(r, suffix)
                if a:
                    avg[key] = round(a, 2)
        out.append({
            "league": LEAGUES[div], "div": div, "kind": "club",
            "date": parse_date(r.get("Date")).isoformat(),
            "time": (r.get("Time") or "").strip(),
            "home": r["HomeTeam"], "away": r["AwayTeam"],
            "xg": xg, "probs": market_probs(*xg, spread=SPREAD_CLUB), "odds": odds, "avg": avg,
            "notes": match_notes, "anchored": anchored,
            "extra": extra_probs(xg[0], xg[1], SPREAD_CLUB),
            "base": model.get("base", {}) if model else {},
            "props": build_props(model, r, div, fpl_map) if (with_props and model) else [],
        })
    notes = ["%d partite saltate (dati insufficienti o squadre non riconosciute)." % skipped] if skipped else []
    notes += extra_notes
    if bad_odds:
        notes.append("%d partite hanno quote anomale (margine o quote fuori norma): le ho ignorate." % bad_odds)
    n_anch = sum(1 for m in out if m.get("anchored"))
    if n_anch:
        notes.append("Per %d partite le probabilita' sono ancorate alle quote di mercato (peso %d%%)." % (
            n_anch, round(MARKET_W * 100)))
    if fpl_failed:
        notes.append("Infortuni Premier non disponibili (fonte FPL non raggiunta).")
    return out, notes


def parse_iso_local(s):
    s = (s or "").strip()
    for cand in (s.replace("Z", "+00:00"),):
        try:
            dt = datetime.fromisoformat(cand)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone()
        except ValueError:
            pass
    for fmt in ("%Y-%m-%dT%H:%MZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).astimezone()
        except ValueError:
            continue
    return None


def espn_fixtures(today, last):
    out, seen = [], set()
    d = today
    while d <= last:
        for slug, label in ESPN_INTL:
            url = ESPN_URL.format(slug=slug, d=d.strftime("%Y%m%d"))
            try:
                data = json.loads(download(url))
            except Exception:
                continue
            for ev in data.get("events", []) or []:
                comp = (ev.get("competitions") or [{}])[0]
                state = (((ev.get("status") or {}).get("type") or {}).get("state"))
                if state and state != "pre":
                    continue
                home = away = None
                for c in comp.get("competitors", []) or []:
                    nm = (c.get("team") or {}).get("displayName")
                    if c.get("homeAway") == "home":
                        home = nm
                    elif c.get("homeAway") == "away":
                        away = nm
                if not home or not away or (home, away) in seen:
                    continue
                seen.add((home, away))
                dt = parse_iso_local(ev.get("date"))
                out.append((home, away, bool(comp.get("neutralSite")), label,
                            dt.strftime("%H:%M") if dt else "", d))
        d += timedelta(days=1)
    return out


def intl_matches(today, last):
    try:
        rows = read_csv(download(INTL_URL))
    except Exception as e:
        return [], ["Dati nazionali non scaricabili: %s" % e]

    hist, ds_fixtures = [], []
    for r in rows:
        d = parse_date(r.get("date"))
        if not d:
            continue
        neutral = (r.get("neutral") or "").strip().upper() == "TRUE"
        hs = (r.get("home_score") or "").strip()
        as_ = (r.get("away_score") or "").strip()
        scored = hs not in ("", "NA") and as_ not in ("", "NA")
        age = (today - d).days
        if scored and 0 < age <= INTL_YEARS * 365:
            w = 0.5 ** (age / HALF_LIFE_INTL)
            if (r.get("tournament") or "") == "Friendly":
                w *= 0.6
            hist.append((r["home_team"], r["away_team"],
                         int(float(hs)), int(float(as_)), w, neutral))
        elif not scored and today <= d <= last:
            ds_fixtures.append((r["home_team"], r["away_team"], neutral,
                                r.get("tournament") or "", "", d))

    fixtures = espn_fixtures(today, last)
    if not fixtures:
        fixtures = ds_fixtures
    if not fixtures and today <= MANUAL_DATE <= last:
        fixtures = [(h, a, n, t, "20:45", MANUAL_DATE) for h, a, n, t in MANUAL_FIXTURES]
    if not fixtures:
        return [], ["Nessuna partita tra nazionali trovata per queste date."]

    print("Calcolo modello nazionali...")
    model = fit_poisson(hist, SHRINK_INTL)
    if not model:
        return [], ["Storico nazionali vuoto."]
    known = set(model["team_w"].keys())
    base_i = base_rates([(x[2], x[3], x[4]) for x in hist])
    out, notes = [], []
    for h, a, neu, tour, time_s, fdate in fixtures:
        h2, a2 = resolve_name(h, known), resolve_name(a, known)
        xg = expected_goals(model, h2, a2, neu, MIN_W_INTL) if h2 and a2 else None
        if not xg:
            notes.append("Dati insufficienti o nome non riconosciuto: %s - %s" % (h, a))
            continue
        out.append({"league": tour, "time": time_s, "home": h, "away": a,
                    "xg": xg, "probs": market_probs(*xg, spread=SPREAD_INTL), "odds": {},
                    "kind": "intl", "date": fdate.isoformat(),
                    "key_home": h2, "key_away": a2,
                    "extra": extra_probs(xg[0], xg[1], SPREAD_INTL), "base": base_i})
    return out, notes


# ----------------------------- REPORT -----------------------------------
def pick_line(prefix, key, p, odds, avg=None):
    line = "  %s %s - prob %.0f%% - gioca se quota >= %.2f" % (
        prefix, MARKET_LABELS[key], p * 100, MARGIN / p)
    if key in odds:
        o, book = odds[key]
        verdict = "OK" if o >= MARGIN / p else "troppo bassa"
        line += "\n     quota trovata %.2f (%s): %s" % (o, book, verdict)
        if avg and key in avg and avg[key] < o:
            line += " [media dei bookmaker %.2f]" % avg[key]
    return line


def build_report(today, last, with_club, with_intl, with_props=True, sink=None, view=None,
                 divs=None):
    if with_intl and not with_club:
        scope_label = "Nazionali"
    elif with_club and not with_intl and divs and len(divs) == 1:
        scope_label = LEAGUES.get(next(iter(divs)), "Club")
    else:
        scope_label = "Tutti i campionati"
    lines = ["L'ORACOLO - consigli del %s" % today.strftime("%d/%m/%Y")]
    if scope_label != "Tutti i campionati":
        lines[0] += " (%s)" % scope_label
    if last > today:
        lines[0] += " -> %s" % last.strftime("%d/%m")
    lines.append("Probabilita' stimate dal modello. Gioca solo se la quota "
                 "reale e' pari o sopra il minimo indicato.")

    matches, notes = [], []
    if with_club:
        m, n = club_matches(today, last, with_props, divs)
        matches += m
        notes += n
    if with_intl:
        m, n = intl_matches(today, last)
        matches += m
        notes += n

    rows = []
    for m in matches:
        m["mismatch"] = is_mismatch(m["xg"], m.get("kind") or "club")
        probs = m["probs"]
        if m["mismatch"]:
            probs = {k: p for k, p in probs.items() if k not in UNDER_KEYS}
        safe, bold = choose_picks(probs)
        rows.append((m, safe, bold))

    if sink is not None:
        for m, safe, bold in rows:
            def _pk(pk):
                if not pk:
                    return None
                o = m["odds"].get(pk[0])
                return [pk[0], round(pk[1], 4), o[0] if o else None]
            sink.append({
                "id": "%s|%s|%s" % (m.get("date"), m["home"], m["away"]),
                "date": m.get("date"), "kind": m.get("kind"), "div": m.get("div"),
                "league": m["league"], "home": m["home"], "away": m["away"],
                "key_home": m.get("key_home"), "key_away": m.get("key_away"),
                "xg": [round(m["xg"][0], 2), round(m["xg"][1], 2)],
                "probs": {k: round(v, 4) for k, v in m["probs"].items()},
                "mismatch": bool(m.get("mismatch")),
                "safe": _pk(safe), "bold": _pk(bold), "result": None,
            })

    top = sorted([r for r in rows if r[1]], key=lambda r: r[1][1], reverse=True)[:5]
    lines += ["", "== TOP DEL GIORNO =="]
    if top:
        for i, (m, safe, _) in enumerate(top, 1):
            lines.append("%d. %s - %s: %s (%.0f%%), quota min %.2f" % (
                i, m["home"], m["away"], MARKET_LABELS[safe[0]], safe[1] * 100,
                MARGIN / safe[1]))
    else:
        lines.append("Nessuna giocata abbastanza solida oggi: meglio saltare.")

    if len(top) >= 2:
        lines += ["", "== COME GIOCARLE =="]
        lines.append("Meglio singole o al massimo doppie. Le multiple lunghe perdono quasi sempre:")
        for n in (2, 3, 5):
            if len(top) >= n:
                p = 1.0
                for r in top[:n]:
                    p *= r[1][1]
                lines.append("  prime %d insieme: passano %.0f%% delle volte, quota minima %.2f" % (
                    n, p * 100, MARGIN / p))

    lines += ["", "== DETTAGLIO PARTITE =="]
    rows.sort(key=lambda r: (r[0]["league"], r[0]["time"]))
    for m, safe, bold in rows:
        lines.append("")
        lines.append("%s %s | %s - %s (gol attesi %.1f-%.1f)" % (
            m["league"], m["time"], m["home"], m["away"], m["xg"][0], m["xg"][1]))
        for n in m.get("notes", []):
            lines.append("  " + n)
        if m.get("mismatch"):
            lines.append("  Partita squilibrata: niente Under (le goleade sono piu' frequenti).")
        if not safe:
            lines.append("  Partita incerta: salta.")
        else:
            lines.append(pick_line("SICURA:", safe[0], safe[1], m["odds"], m.get("avg")))
            if bold:
                lines.append(pick_line("PIU' QUOTA (piu' rischio):", bold[0], bold[1], m["odds"], m.get("avg")))
        for pl in m.get("props", []):
            lines.append("  " + pl)

    if notes:
        lines += [""] + notes
    if view is not None:
        def _pick(pk, odds, avg=None):
            if not pk:
                return None
            key, p = pk[0], pk[1]
            d = {"label": MARKET_LABELS[key], "prob": round(p, 4),
                 "minodds": round(MARGIN / p, 2)}
            if key in odds:
                o, book = odds[key]
                d["odds"], d["book"], d["ok"] = o, book, bool(o >= MARGIN / p)
                if avg and key in avg and avg[key] < o:
                    d["avg"] = avg[key]
            return d
        combos = []
        for n in (2, 3, 5):
            if len(top) >= n:
                pp = 1.0
                for r in top[:n]:
                    pp *= r[1][1]
                combos.append({"n": n, "prob": round(pp, 4), "minodds": round(MARGIN / pp, 2)})
        ordered = sorted(rows, key=lambda r: (r[0]["time"] or "99:99", r[0]["league"]))
        view["multipla"] = build_multipla(rows)
        view.update({
            "date": today.strftime("%d/%m/%Y"),
            "scope": {"label": scope_label, "date": today.strftime("%d/%m/%Y"),
                      "days": (last - today).days + 1},
            "top": [{"home": m["home"], "away": m["away"], "league": m["league"],
                     "time": m["time"], "label": MARKET_LABELS[s[0]],
                     "prob": round(s[1], 4), "minodds": round(MARGIN / s[1], 2)}
                    for m, s, _ in top],
            "combos": combos,
            "matches": [{"league": m["league"], "time": m["time"], "home": m["home"],
                         "away": m["away"],
                         "xg": [round(m["xg"][0], 1), round(m["xg"][1], 1)],
                         "mismatch": bool(m.get("mismatch")),
                         "anchored": bool(m.get("anchored")),
                         "markets": market_groups(m),
                         "best": best_picks(market_groups(m)),
                         "safe": _pick(s, m["odds"], m.get("avg")), "bold": _pick(b, m["odds"], m.get("avg")),
                         "notes": list(m.get("notes", [])), "props": list(m.get("props", []))}
                        for m, s, b in ordered],
            "notes": list(notes),
        })

    if with_props and any(m.get("props") for m in matches):
        lines += ["", "Cartellini e tiri in porta: i cartellini contano solo i gialli (controlla "
                  "come li conta il tuo bookmaker). Non vedo le quote di questi mercati: "
                  "confronta con la quota minima. Le ammonizioni valgono solo se il giocatore "
                  "parte titolare (le formazioni escono circa 1 ora prima)."]
    lines += ["", "Nota: modello semplice. Infortuni considerati solo per la Premier, "
              "formazioni ufficiali no. Nessuna giocata e' sicura al 100%."]
    return "\n".join(lines)


def build_multipla(rows, n_legs=4):
    """Multipla in stile 'favorita con pagamento anticipato + Gol': una gamba per partita,
    scelta tra pagamento anticipato e Gol, con probabilita' tra 55% e 88%."""
    cands = []
    for m, _s, _b in rows:
        pr = dict(m["probs"])
        pr.update(m.get("extra", {}))
        best = None
        for k in ("EP1", "EP2", "GG"):
            p = pr.get(k)
            if p is None or not (0.55 <= p <= 0.88):
                continue
            if best is None or p > best[1]:
                best = (k, p)
        if best:
            cands.append((m, best[0], best[1]))
    cands.sort(key=lambda c: -c[2])
    legs = cands[:n_legs]
    if len(legs) < 2:
        return None
    pp, mo = 1.0, 1.0
    out = []
    for m, k, p in legs:
        marg = EXTRA_MARGIN if k.startswith("EP") else MARGIN
        pp *= p
        mo *= marg / p
        out.append({"home": m["home"], "away": m["away"], "league": m["league"],
                    "time": m["time"], "label": MARKET_LABELS[k], "prob": round(p, 4),
                    "minodds": round(marg / p, 2)})
    return {"legs": out, "prob": round(pp, 4), "minodds": round(mo, 2),
            "note": "Passa circa %d volte su 100. Gioca solo se la quota totale e' almeno %.2f "
                    "(il bonus multipla del bookmaker conta, ma ogni gamba in piu' aggiunge margine)." % (
                        round(pp * 100), mo)}


def send_ntfy(text):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return
    try:
        req = urllib.request.Request(
            "https://ntfy.sh/" + topic,
            data=text[:3800].encode("utf-8"),
            headers={"Title": "L'Oracolo - consigli del giorno"})
        urllib.request.urlopen(req, timeout=20)
        print("Notifica inviata su ntfy.")
    except Exception as e:
        print("Notifica non inviata: %s" % e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--no-intl", action="store_true")
    ap.add_argument("--no-club", action="store_true")
    ap.add_argument("--no-props", action="store_true")
    args, _ = ap.parse_known_args()

    today = date.today()
    last = today + timedelta(days=max(args.days, 1) - 1)
    report = build_report(today, last, not args.no_club, not args.no_intl, not args.no_props)
    print("\n" + report)
    send_ntfy(report)


if __name__ == "__main__":
    main()
