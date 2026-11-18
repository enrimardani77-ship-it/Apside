#!/usr/bin/env python3
"""Genera la pagina web con il tasto "Analizza ora" e salva l'ultimo risultato.

- site/index.html  : pagina con il tasto (pubblicata su GitHub Pages)
- data/latest.json : ultimo report (la pagina lo legge per mostrarti il risultato)
Lanciato da GitHub Actions, ma funziona anche a mano: python build_site.py --days 1
"""
import argparse
import html
import json
import os
import traceback
import urllib.request
from datetime import date, datetime, timedelta

import storico
import value_bet as vb

PAGE = """<!doctype html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Consigli del giorno</title>
<style>
:root{color-scheme:light dark;--bg:#fff;--fg:#1a1a1a;--card:#f3f4f6;--acc:#0a7d3b;--mut:#6b7280}
@media (prefers-color-scheme:dark){:root{--bg:#111;--fg:#eee;--card:#1d1f23;--acc:#4ade80;--mut:#9ca3af}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.45 -apple-system,system-ui,sans-serif;
padding:max(16px,env(safe-area-inset-top)) 16px max(24px,env(safe-area-inset-bottom))}
h1{font-size:1.3rem;margin:.2rem 0 .8rem}
.row{display:flex;gap:10px;align-items:stretch;margin-bottom:10px}
.btn{flex:1;border:0;border-radius:12px;padding:14px 16px;font-size:1.05rem;font-weight:700;
background:var(--acc);color:#fff}
.btn:disabled{opacity:.55}
.btn.small{flex:none;padding:8px 14px;font-size:.95rem}
.btn.sec{background:var(--card);color:var(--fg)}
select,input{font-size:1rem;border-radius:10px;border:1px solid var(--mut);padding:10px;
background:var(--card);color:var(--fg)}
input{width:100%;margin:6px 0}
.upd{color:var(--mut);font-size:.88rem;margin:4px 0 10px}
pre{white-space:pre-wrap;word-wrap:break-word;background:var(--card);padding:14px;border-radius:12px;
font:14px/1.5 ui-monospace,Menlo,monospace}
details{margin-top:18px;color:var(--mut);font-size:.92rem}
</style></head><body>
<h1>Consigli del giorno</h1>
<div class="row">
  <button id="go" class="btn">▶ Analizza ora</button>
  <select id="days" aria-label="Giorni"><option value="1">Oggi</option>
  <option value="2">2 giorni</option><option value="3">3 giorni</option></select>
</div>
<div id="status" class="upd">Ultimo aggiornamento: __UPDATED__</div>
<pre id="out">__REPORT__</pre>
<details id="hist"><summary>Storico e precisione</summary>
  <pre id="stats">__STATS__</pre>
</details>
<details id="settings"><summary>Impostazioni</summary>
  <p>Per avviare l’analisi da questa pagina serve un token GitHub. Resta salvato solo
  su questo telefono, non nel repository.</p>
  <input id="tok" type="password" placeholder="Incolla qui il token" autocomplete="off">
  <div class="row"><button id="save" class="btn small">Salva</button>
  <button id="clear" class="btn small sec">Cancella</button></div>
  <div id="tokstate"></div>
</details>
<script>
const owner = location.hostname.split(".")[0];
const repo = location.pathname.split("/")[1] || "";
const API = "https://api.github.com/repos/" + owner + "/" + repo;
const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function getToken() { try { return localStorage.getItem("gh_token") || ""; } catch (e) { return ""; } }
function setToken(t) { try { if (t) localStorage.setItem("gh_token", t); else localStorage.removeItem("gh_token"); } catch (e) {} }
function status(msg) { $("status").textContent = msg; }
function tokState() { $("tokstate").textContent = getToken() ? "Token salvato." : "Nessun token salvato."; }

async function getLatest() {
  const url = API + "/contents/data/latest.json?ref=main&t=" + Date.now();
  const base = { "Accept": "application/vnd.github.raw+json" };
  const tok = getToken();
  let r = await fetch(url, { headers: tok ? Object.assign({ "Authorization": "Bearer " + tok }, base) : base, cache: "no-store" });
  if (r.status === 401 && tok) r = await fetch(url, { headers: base, cache: "no-store" });
  if (!r.ok) throw new Error("lettura " + r.status);
  return await r.json();
}

function show(d) {
  $("out").textContent = d.report;
  if (d.stats) $("stats").textContent = d.stats;
  status("Aggiornato: " + d.updated);
}

async function run() {
  const tok = getToken();
  if (!tok) {
    $("settings").open = true;
    status("Prima salva il token nelle Impostazioni.");
    return;
  }
  $("go").disabled = true;
  status("Avvio dell’analisi…");
  let before = null;
  try { before = (await getLatest()).run_id; } catch (e) {}
  try {
    const r = await fetch(API + "/actions/workflows/report.yml/dispatches", {
      method: "POST",
      headers: {
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + tok,
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ ref: "main", inputs: { days: $("days").value } })
    });
    if (r.status !== 204) {
      status(r.status === 401 || r.status === 403 || r.status === 404
        ? "Token non valido o senza permesso Actions (errore " + r.status + ")."
        : "Avvio non riuscito (errore " + r.status + ").");
      $("go").disabled = false;
      return;
    }
  } catch (e) {
    status("Errore di rete: " + e.message);
    $("go").disabled = false;
    return;
  }
  status("Analisi in corso, circa 1 minuto…");
  const start = Date.now();
  while (Date.now() - start < 240000) {
    await sleep(6000);
    try {
      const d = await getLatest();
      if (d.run_id !== before) { show(d); $("go").disabled = false; return; }
    } catch (e) {}
    status("Analisi in corso… " + Math.round((Date.now() - start) / 1000) + " s");
  }
  status("Ci sta mettendo troppo: controlla la scheda Actions su GitHub.");
  $("go").disabled = false;
}

$("go").addEventListener("click", run);
$("save").addEventListener("click", () => { setToken($("tok").value.trim()); $("tok").value = ""; tokState(); });
$("clear").addEventListener("click", () => { setToken(""); tokState(); });
tokState();
getLatest().then(show).catch(() => {});
</script>
</body></html>"""


def summary(report):
    """Estrae solo la sezione TOP DEL GIORNO per la notifica."""
    out, on = [], False
    for ln in report.splitlines():
        if ln.startswith("== TOP DEL GIORNO"):
            on = True
            continue
        if on and ln.startswith("=="):
            break
        if on and ln.strip():
            out.append(ln)
    return "\n".join(out) or "Nessuna giocata solida oggi."


def notify(text, click):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return
    headers = {"Title": "Consigli del giorno"}
    if click:
        headers["Click"] = click
    try:
        req = urllib.request.Request("https://ntfy.sh/" + topic,
                                     data=text[:3500].encode("utf-8"),
                                     headers=headers)
        urllib.request.urlopen(req, timeout=20)
        print("Notifica inviata.")
    except Exception as e:
        print("Notifica non inviata: %s" % e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--no-intl", action="store_true")
    ap.add_argument("--no-club", action="store_true")
    args, _ = ap.parse_known_args()

    today = date.today()
    last = today + timedelta(days=max(args.days, 1) - 1)
    sink = []
    try:
        report = vb.build_report(today, last, not args.no_club, not args.no_intl, sink=sink)
    except Exception:
        report = "ERRORE durante l'esecuzione:\n\n" + traceback.format_exc()

    # storico: verifica le previsioni passate, salva le nuove, calcola la precisione
    try:
        preds = storico.load()
        found = storico.resolve_pending(preds, today)
        print("Risultati trovati per %d previsioni passate." % found)
        if not report.startswith("ERRORE"):
            storico.merge(preds, sink)
        storico.save(preds)
        stats = storico.stats_text(preds)
    except Exception:
        stats = "Storico non disponibile:\n" + traceback.format_exc()

    updated = datetime.now().strftime("%d/%m/%Y %H:%M")
    run_id = "%s-%s" % (os.environ.get("GITHUB_RUN_ID", "locale"),
                        os.environ.get("GITHUB_RUN_ATTEMPT", datetime.now().strftime("%H%M%S")))

    os.makedirs("site", exist_ok=True)
    os.makedirs("data", exist_ok=True)
    page = PAGE.replace("__UPDATED__", html.escape(updated)).replace("__REPORT__", html.escape(report)).replace("__STATS__", html.escape(stats))
    with open("site/index.html", "w", encoding="utf-8") as f:
        f.write(page)
    with open("data/latest.json", "w", encoding="utf-8") as f:
        json.dump({"run_id": run_id, "updated": updated, "report": report, "stats": stats},
                  f, ensure_ascii=False)
    print(report)

    if not report.startswith("ERRORE") and os.environ.get("EVENT") != "push":
        notify(summary(report), os.environ.get("PAGE_URL"))


if __name__ == "__main__":
    main()
