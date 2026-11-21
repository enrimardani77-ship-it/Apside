#!/usr/bin/env python3
"""L'Oracolo: genera la pagina web con il tasto e salva l'ultimo risultato.

- site/index.html  : la pagina (pubblicata su GitHub Pages)
- data/latest.json : ultimo risultato (la pagina lo legge quando premi il tasto)
Lanciato da GitHub Actions, ma funziona anche a mano: python build_site.py --days 1
"""
import argparse
import json
import os
import traceback
import urllib.request
from datetime import date, datetime, timedelta

import storico
import value_bet as vb

PAGE = r"""<!doctype html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-title" content="L’Oracolo">
<title>L’Oracolo</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E🔮%3C/text%3E%3C/svg%3E">
<style>
:root{color-scheme:light dark;--bg:#f6f7f9;--fg:#15171a;--card:#fff;--line:#e4e7ec;--mut:#667085;
--acc:#6d28d9;--acc2:#8b5cf6;--hi:#16a34a;--mid:#d97706;--lo:#64748b;--bad:#dc2626;--soft:#f1edfd}
@media (prefers-color-scheme:dark){:root{--bg:#0e0f13;--fg:#eceef2;--card:#171a20;--line:#272b33;--mut:#9aa3b2;
--acc:#a78bfa;--acc2:#7c3aed;--hi:#4ade80;--mid:#fbbf24;--lo:#94a3b8;--bad:#f87171;--soft:#211a3a}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.45 -apple-system,system-ui,sans-serif;
padding:max(14px,env(safe-area-inset-top)) 14px max(28px,env(safe-area-inset-bottom));max-width:720px;margin-inline:auto}
.hidden{display:none!important}
header{display:flex;align-items:center;gap:12px;margin:6px 0 14px}
.orb{font-size:2rem;width:52px;height:52px;border-radius:16px;display:grid;place-items:center;
background:linear-gradient(135deg,var(--acc2),var(--acc));box-shadow:0 6px 18px rgba(109,40,217,.35)}
h1{font-size:1.45rem;margin:0;letter-spacing:-.01em}
.sub{color:var(--mut);font-size:.88rem}
h2{font-size:1.02rem;margin:22px 0 8px}
.actions{display:flex;gap:10px}
.controls{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px}
.controls #league{grid-column:1/-1}
.controls input{margin:0;min-width:0}
.scope{display:inline-block;font-size:.8rem;font-weight:650;color:var(--acc);background:var(--soft);border-radius:99px;padding:4px 11px;margin-top:12px}
.btn{flex:1;border:0;border-radius:14px;padding:15px 16px;font-size:1.05rem;font-weight:700;color:#fff;
background:linear-gradient(135deg,var(--acc2),var(--acc))}
.btn:disabled{opacity:.6}
.btn.small{flex:none;padding:9px 14px;font-size:.95rem}
.btn.sec{background:var(--card);color:var(--fg);border:1px solid var(--line)}
select,input{font-size:1rem;border-radius:12px;border:1px solid var(--line);padding:10px;background:var(--card);color:var(--fg)}
input{width:100%;margin:6px 0}
.upd{color:var(--mut);font-size:.85rem;margin:8px 2px 0}
.flash{animation:flash 1.6s ease-out}
@keyframes flash{0%{color:var(--hi);font-weight:700}100%{color:var(--mut)}}
.progress{margin-top:12px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px}
.pbar{height:8px;border-radius:99px;background:var(--line);overflow:hidden}
.pbar i{display:block;height:100%;width:4%;border-radius:99px;background:linear-gradient(90deg,var(--acc2),var(--acc));transition:width .8s ease}
.pstep{font-size:.88rem;color:var(--mut);margin-top:8px}
.tiles{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:14px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:10px 8px;text-align:center}
.tile b{display:block;font-size:1.35rem}
.tile span{font-size:.75rem;color:var(--mut)}
.toprow{display:flex;align-items:center;gap:10px;background:var(--card);border:1px solid var(--line);
border-left:5px solid var(--lo);border-radius:14px;padding:11px 12px;margin-bottom:8px}
.toprow.hi{border-left-color:var(--hi)}.toprow.mid{border-left-color:var(--mid)}
.rank{width:26px;height:26px;border-radius:50%;background:var(--soft);color:var(--acc);display:grid;place-items:center;font-weight:700;font-size:.85rem;flex:none}
.tmid{flex:1;min-width:0}
.teams{font-weight:650;overflow-wrap:anywhere}
.meta{font-size:.8rem;color:var(--mut)}
.lab{font-size:.9rem;margin-top:2px}
.right{text-align:right;flex:none}
.bigpct{font-size:1.3rem;font-weight:800}
.min{font-size:.75rem;color:var(--mut)}
.hint{font-size:.86rem;color:var(--mut);margin:-2px 0 8px}
.combogrid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.combo{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:10px 6px;text-align:center}
.combo.warn{border-color:var(--bad)}
.cn{font-size:.78rem;color:var(--mut)}.cp{font-size:1.25rem;font-weight:800}.cm{font-size:.74rem;color:var(--mut)}
.chips{display:flex;gap:8px;overflow-x:auto;padding:4px 0 8px;margin-top:6px;-webkit-overflow-scrolling:touch}
.chip{flex:none;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:99px;padding:7px 13px;font-size:.88rem}
.chip.on{background:var(--acc);border-color:var(--acc);color:#fff}
.match{background:var(--card);border:1px solid var(--line);border-radius:14px;margin-bottom:8px;overflow:hidden}
.msum{list-style:none;display:flex;align-items:center;gap:10px;padding:12px;cursor:pointer}
.msum::-webkit-details-marker{display:none}
.ml{flex:1;min-width:0}
.pill{flex:none;font-weight:800;font-size:.95rem;padding:5px 10px;border-radius:99px;background:var(--soft);color:var(--lo)}
.pill.hi{color:var(--hi)}.pill.mid{color:var(--mid)}.pill.none{font-weight:600;font-size:.82rem}
.mbody{padding:0 12px 12px;border-top:1px solid var(--line)}
.xg{font-size:.85rem;color:var(--mut);margin:10px 0 4px}
.tagw{display:inline-block;font-size:.78rem;background:var(--soft);color:var(--acc);border-radius:8px;padding:3px 8px;margin:2px 0 6px}
.note{font-size:.86rem;margin:3px 0}
.pick{margin-top:10px;padding:10px;border-radius:12px;background:var(--bg)}
.pick-top{display:flex;align-items:baseline;gap:8px}
.tag{font-size:.7rem;text-transform:uppercase;letter-spacing:.05em;color:var(--acc);font-weight:700;flex:none}
.pick-label{font-weight:650;flex:1}
.pick-pct{font-weight:800}
.meter{height:7px;border-radius:99px;background:var(--line);overflow:hidden;margin:8px 0 6px}
.meter i{display:block;height:100%;border-radius:99px;background:var(--lo)}
.pick.hi .meter i{background:var(--hi)}.pick.mid .meter i{background:var(--mid)}
.pick-sub{font-size:.84rem;color:var(--mut)}
.badge{display:inline-block;margin-top:6px;font-size:.78rem;font-weight:650;border-radius:8px;padding:3px 8px}
.badge.ok{background:rgba(22,163,74,.15);color:var(--hi)}.badge.bad{background:rgba(220,38,38,.14);color:var(--bad)}
.props{margin-top:10px;font-size:.86rem}.ptitle{font-size:.7rem;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);font-weight:700}
.pline{margin:3px 0}
.mk{margin-top:12px}.mkg{margin-top:8px}
.mkrow{display:flex;align-items:baseline;gap:8px;padding:5px 0;border-top:1px solid var(--line);font-size:.88rem}
.mkrow .l{flex:1}.mkrow .p{font-weight:700;min-width:2.8em;text-align:right}.mkrow .q{color:var(--mut);min-width:5.5em;text-align:right}
.mkrow .d{min-width:3.2em;text-align:right;font-size:.8rem}.d.up{color:var(--hi)}.d.dn{color:var(--bad)}
.mult{margin-top:14px;border:1px solid var(--line);background:var(--card);border-radius:14px;padding:12px 14px}
.mleg{display:flex;gap:8px;align-items:baseline;padding:6px 0;border-top:1px solid var(--line);font-size:.9rem}
.mleg:first-of-type{border-top:0}.mleg .l{flex:1}.mleg .p{font-weight:700}.mleg .q{color:var(--mut);min-width:5.2em;text-align:right}
.tagm{display:inline-block;margin-top:6px;font-size:.75rem;color:var(--mut);border:1px solid var(--line);border-radius:8px;padding:2px 8px}
.mkh{font-size:.72rem;color:var(--mut);margin:4px 0}
.empty{color:var(--mut);font-size:.92rem;padding:8px 2px}
pre{white-space:pre-wrap;word-wrap:break-word;background:var(--card);border:1px solid var(--line);padding:14px;border-radius:12px;font:13px/1.5 ui-monospace,Menlo,monospace}
details.box{margin-top:16px;color:var(--mut);font-size:.92rem}
details.box summary{cursor:pointer;font-weight:600}
.foot{margin-top:22px;font-size:.78rem;color:var(--mut)}
</style></head><body>
<header><div class="orb">🔮</div><div><h1>L’Oracolo</h1><div class="sub">Consigli sul calcio, calcolati dai dati</div></div></header>
<div class="actions"><button id="go" class="btn">▶ Chiedi all’Oracolo</button></div>
<div class="controls">
  <select id="league" aria-label="Campionato">
    <option value="tutti">Tutti i campionati</option>
    <option value="E0">Premier League</option>
    <option value="SP1">La Liga</option>
    <option value="D1">Bundesliga</option>
    <option value="I1">Serie A</option>
    <option value="F1">Ligue 1</option>
    <option value="intl">Nazionali</option>
  </select>
  <input id="date" type="date" aria-label="Data da analizzare">
  <select id="days" aria-label="Giorni"><option value="1">1 giorno</option><option value="2">2 giorni</option><option value="3">3 giorni</option><option value="7">1 settimana</option></select>
</div>
<div id="progress" class="progress hidden"><div class="pbar"><i id="pfill"></i></div><div id="pstep" class="pstep"></div></div>
<div id="status" class="upd"></div>
<div id="app" class="hidden">
  <div id="scope" class="scope hidden"></div>
  <div id="tiles" class="tiles"></div>
  <section id="top"></section>
  <section id="combos"></section>
  <section id="multipla" class="mult" style="display:none"></section>
  <h2>Le partite</h2>
  <div id="chips" class="chips"></div>
  <section id="matches"></section>
  <div id="gnotes" class="hint"></div>
</div>
<pre id="out" class="hidden"></pre>
<details class="box"><summary>Storico e precisione</summary><pre id="stats"></pre></details>
<details class="box" id="settings"><summary>Impostazioni</summary>
  <p>Per avviare l’analisi da questa pagina serve un token GitHub. Resta salvato solo su questo telefono, non nel repository.</p>
  <input id="tok" type="password" placeholder="Incolla qui il token" autocomplete="off">
  <div class="actions"><button id="save" class="btn small">Salva</button><button id="clear" class="btn small sec">Cancella</button></div>
  <div id="tokstate"></div>
</details>
<div class="foot">Le percentuali sono stime di un modello statistico, non certezze. Le quote dei bookmaker incorporano commissioni: gioca solo se la quota reale è almeno quella minima indicata, e con moderazione.</div>
<script type="application/json" id="boot">__BOOT__</script>
<script>
const owner = location.hostname.split(".")[0];
const repo = location.pathname.split("/")[1] || "";
const API = "https://api.github.com/repos/" + owner + "/" + repo;
const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
};
const pct = (p) => Math.round(p * 100) + "%";
const conf = (p) => (p >= 0.75 ? "hi" : p >= 0.66 ? "mid" : "lo");
let activeLeague = "Tutte";
function todayStr() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
}

function getToken() { try { return localStorage.getItem("gh_token") || ""; } catch (e) { return ""; } }
function setToken(t) { try { if (t) localStorage.setItem("gh_token", t); else localStorage.removeItem("gh_token"); } catch (e) {} }
function setStatus(msg, flash) {
  const s = $("status");
  s.textContent = msg;
  s.classList.remove("flash");
  if (flash) { void s.offsetWidth; s.classList.add("flash"); }
}
function tokState() { $("tokstate").textContent = getToken() ? "Token salvato." : "Nessun token salvato."; }

function pickBox(tag, pick) {
  const box = el("div", "pick " + conf(pick.prob));
  const top = el("div", "pick-top");
  top.append(el("span", "tag", tag), el("span", "pick-label", pick.label), el("span", "pick-pct", pct(pick.prob)));
  const meter = el("div", "meter");
  const fill = el("i");
  fill.style.width = Math.round(pick.prob * 100) + "%";
  meter.append(fill);
  box.append(top, meter, el("div", "pick-sub", "Gioca solo se la quota è almeno " + pick.minodds.toFixed(2)));
  if (pick.odds) {
    box.append(el("div", "badge " + (pick.ok ? "ok" : "bad"),
      "Quota trovata " + pick.odds.toFixed(2) + " (" + pick.book + "): " + (pick.ok ? "conviene" : "troppo bassa") +
      (pick.avg ? " · media " + pick.avg.toFixed(2) : "")));
  }
  return box;
}

function matchCard(m, open) {
  const det = el("details", "match");
  det.open = open;
  const sum = el("summary", "msum");
  const left = el("div", "ml");
  left.append(el("div", "teams", m.home + " – " + m.away));
  left.append(el("div", "meta", m.league + (m.time ? " · " + m.time : "")));
  sum.append(left);
  sum.append(m.safe ? el("div", "pill " + conf(m.safe.prob), pct(m.safe.prob)) : el("div", "pill none", "salta"));
  det.append(sum);
  const body = el("div", "mbody");
  body.append(el("div", "xg", "Gol attesi " + m.xg[0].toFixed(1) + " – " + m.xg[1].toFixed(1)));
  if (m.mismatch) body.append(el("div", "tagw", "Partita squilibrata: niente Under"));
  (m.notes || []).forEach((n) => body.append(el("div", "note", n)));
  body.append(m.safe ? pickBox("Sicura", m.safe) : el("div", "empty", "Partita incerta: nessuna giocata abbastanza solida."));
  if (m.bold) body.append(pickBox("Più quota", m.bold));
  if (m.anchored) body.append(el("div", "tagm", "Ancorata alle quote di mercato"));
  if (m.markets && m.markets.length) {
    const mk = el("div", "mk");
    mk.append(el("div", "ptitle", "Altre proposte"));
    mk.append(el("div", "mkh", "probabilità · quota minima · scarto dalla media del campionato (punti)"));
    m.markets.forEach((g) => {
      const gd = el("div", "mkg");
      gd.append(el("div", "ptitle", g.name));
      g.items.forEach((it) => {
        const row = el("div", "mkrow");
        row.append(el("span", "l", it.label));
        row.append(el("span", "p", pct(it.prob)));
        row.append(el("span", "q", "min " + it.minodds.toFixed(2)));
        if (typeof it.delta === "number") {
          row.append(el("span", "d " + (it.delta > 0 ? "up" : it.delta < 0 ? "dn" : ""),
            (it.delta > 0 ? "+" : "") + it.delta));
        }
        gd.append(row);
      });
      mk.append(gd);
    });
    body.append(mk);
  }
  if (m.props && m.props.length) {
    const p = el("div", "props");
    p.append(el("div", "ptitle", "Cartellini e tiri in porta"));
    m.props.forEach((x) => p.append(el("div", "pline", x)));
    body.append(p);
  }
  det.append(body);
  return det;
}

function renderTiles(v) {
  const t = $("tiles");
  t.textContent = "";
  const solid = v.matches.filter((m) => m.safe).length;
  [[String(v.matches.length), "partite"], [String(solid), "giocate solide"], [v.date || "", "data"]].forEach((x) => {
    const c = el("div", "tile");
    c.append(el("b", null, x[0]), el("span", null, x[1]));
    t.append(c);
  });
}

function renderTop(v) {
  const box = $("top");
  box.textContent = "";
  box.append(el("h2", null, "Le migliori giocate"));
  if (!v.top.length) { box.append(el("div", "empty", "Nessuna giocata abbastanza solida oggi: meglio saltare.")); return; }
  v.top.forEach((t, i) => {
    const row = el("div", "toprow " + conf(t.prob));
    row.append(el("div", "rank", String(i + 1)));
    const mid = el("div", "tmid");
    mid.append(el("div", "teams", t.home + " – " + t.away));
    mid.append(el("div", "meta", t.league + (t.time ? " · " + t.time : "")));
    mid.append(el("div", "lab", t.label));
    row.append(mid);
    const right = el("div", "right");
    right.append(el("div", "bigpct", pct(t.prob)), el("div", "min", "quota min " + t.minodds.toFixed(2)));
    row.append(right);
    box.append(row);
  });
}

function renderMultipla(v) {
  const box = $("multipla");
  if (!box) return;
  box.textContent = "";
  const mu = v.multipla;
  if (!mu) { box.style.display = "none"; return; }
  box.style.display = "";
  box.append(el("div", "ptitle", "Multipla in stile (favorite con pagamento anticipato + Gol)"));
  mu.legs.forEach((g) => {
    const r = el("div", "mleg");
    const l = el("div", "l");
    l.append(el("div", "teams", g.home + " – " + g.away), el("div", "meta", g.label));
    r.append(l, el("span", "p", pct(g.prob)), el("span", "q", "min " + g.minodds.toFixed(2)));
    box.append(r);
  });
  box.append(el("div", "pick-sub", "Insieme: " + pct(mu.prob) + " · quota totale minima " + mu.minodds.toFixed(2)));
  box.append(el("div", "note", mu.note));
}

function renderCombos(v) {
  const box = $("combos");
  box.textContent = "";
  if (!v.combos || !v.combos.length) return;
  box.append(el("h2", null, "Se vuoi combinarle"));
  box.append(el("div", "hint", "Meglio singole o al massimo doppie: più gambe metti, meno probabilità hai di passare."));
  const grid = el("div", "combogrid");
  v.combos.forEach((c) => {
    const card = el("div", "combo" + (c.prob < 0.4 ? " warn" : ""));
    card.append(el("div", "cn", c.n + " insieme"), el("div", "cp", pct(c.prob)), el("div", "cm", "quota min " + c.minodds.toFixed(2)));
    grid.append(card);
  });
  box.append(grid);
}

function renderMatches(v) {
  const box = $("matches");
  box.textContent = "";
  const list = v.matches.filter((m) => activeLeague === "Tutte" || m.league === activeLeague);
  if (!list.length) { box.append(el("div", "empty", "Nessuna partita in questo periodo.")); return; }
  list.forEach((m) => box.append(matchCard(m, list.length <= 3)));
}

function renderChips(v) {
  const box = $("chips");
  box.textContent = "";
  const leagues = ["Tutte"].concat(Array.from(new Set(v.matches.map((m) => m.league))));
  if (leagues.length <= 2) return;
  leagues.forEach((l) => {
    const c = el("button", "chip" + (l === activeLeague ? " on" : ""), l);
    c.type = "button";
    c.addEventListener("click", () => { activeLeague = l; renderChips(v); renderMatches(v); });
    box.append(c);
  });
}

function render(d, flash) {
  const v = d.view;
  const structured = !!(v && v.matches && v.top);
  setStatus(d.updated ? "Aggiornato: " + d.updated : "", flash);
  $("app").classList.toggle("hidden", !structured);
  $("out").classList.toggle("hidden", structured);
  if (structured) {
    if (activeLeague !== "Tutte" && !v.matches.some((m) => m.league === activeLeague)) activeLeague = "Tutte";
    const sc = $("scope");
    if (v.scope) {
      sc.textContent = v.scope.label + " · " + v.scope.date + (v.scope.days > 1 ? " (" + v.scope.days + " giorni)" : "");
      sc.classList.remove("hidden");
    } else { sc.classList.add("hidden"); }
    renderTiles(v); renderTop(v); renderCombos(v); renderMultipla(v); renderChips(v); renderMatches(v);
    $("gnotes").textContent = (v.notes || []).join(" ");
  } else {
    $("out").textContent = d.report || "";
  }
  if (d.stats) $("stats").textContent = d.stats;
}

async function getLatest() {
  const url = API + "/contents/data/latest.json?ref=main&t=" + Date.now();
  const base = { "Accept": "application/vnd.github.raw+json" };
  const tok = getToken();
  let r = await fetch(url, { headers: tok ? Object.assign({ "Authorization": "Bearer " + tok }, base) : base, cache: "no-store" });
  if (r.status === 401 && tok) r = await fetch(url, { headers: base, cache: "no-store" });
  if (!r.ok) throw new Error("lettura " + r.status);
  return await r.json();
}

function progress(sec) {
  const steps = [[8, "Sveglio i server…"], [25, "Scarico dati e quote…"], [55, "Calcolo le probabilità…"], [999, "Preparo i consigli…"]];
  $("pfill").style.width = Math.min(94, Math.round(100 * (1 - Math.exp(-sec / 38)))) + "%";
  $("pstep").textContent = steps.find((s) => sec < s[0])[1] + " (" + sec + " s)";
}

async function run() {
  const tok = getToken();
  if (!tok) { $("settings").open = true; setStatus("Prima salva il token nelle Impostazioni."); return; }
  const date = $("date").value || todayStr();
  if (date < todayStr()) { setStatus("Scegli la data di oggi o di un giorno futuro."); return; }
  try { localStorage.setItem("oracolo_league", $("league").value); } catch (e) {}
  $("go").disabled = true;
  setStatus("Avvio dell’analisi…");
  let before = null;
  try { before = (await getLatest()).run_id; } catch (e) {}
  try {
    const r = await fetch(API + "/actions/workflows/report.yml/dispatches", {
      method: "POST",
      headers: { "Accept": "application/vnd.github+json", "Authorization": "Bearer " + tok,
                 "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json" },
      body: JSON.stringify({ ref: "main", inputs: { days: $("days").value, league: $("league").value, date: date } })
    });
    if (r.status !== 204) {
      setStatus(r.status === 401 || r.status === 403 || r.status === 404
        ? "Token non valido o senza permesso Actions (errore " + r.status + ")."
        : "Avvio non riuscito (errore " + r.status + ").");
      $("go").disabled = false;
      return;
    }
  } catch (e) { setStatus("Errore di rete: " + e.message); $("go").disabled = false; return; }
  setStatus("");
  $("progress").classList.remove("hidden");
  const start = Date.now();
  progress(0);
  while (Date.now() - start < 240000) {
    await sleep(3000);
    progress(Math.round((Date.now() - start) / 1000));
    try {
      const d = await getLatest();
      if (d.run_id !== before) {
        $("pfill").style.width = "100%";
        await sleep(400);
        $("progress").classList.add("hidden");
        render(d, true);
        $("go").disabled = false;
        window.scrollTo({ top: 0, behavior: "smooth" });
        return;
      }
    } catch (e) {}
  }
  $("progress").classList.add("hidden");
  setStatus("Ci sta mettendo troppo: controlla la scheda Actions su GitHub.");
  $("go").disabled = false;
}

$("go").addEventListener("click", run);
$("save").addEventListener("click", () => { setToken($("tok").value.trim()); $("tok").value = ""; tokState(); });
$("clear").addEventListener("click", () => { setToken(""); tokState(); });
tokState();
$("date").min = todayStr();
$("date").value = todayStr();
try { const l = localStorage.getItem("oracolo_league"); if (l) $("league").value = l; } catch (e) {}
try { render(JSON.parse($("boot").textContent), false); } catch (e) {}
getLatest().then((d) => render(d, false)).catch(() => {});
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
    headers = {"Title": "L'Oracolo - consigli del giorno"}   # solo ASCII negli header
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
    ap.add_argument("--league", default="tutti")    # tutti, E0, SP1, D1, I1, F1, intl
    ap.add_argument("--date", default="")           # AAAA-MM-GG, vuoto = oggi
    args, _ = ap.parse_known_args()

    today = date.today()
    start, extra_notes = today, []
    if (args.date or "").strip():
        try:
            start = date.fromisoformat(args.date.strip())
        except ValueError:
            extra_notes.append("Data non valida: analizzo oggi.")
        if start < today:
            start = today
            extra_notes.append("La data scelta e' passata: analizzo oggi.")
    last = start + timedelta(days=max(args.days, 1) - 1)

    lg = (args.league or "tutti").strip()
    with_club, with_intl, divs = not args.no_club, not args.no_intl, None
    if lg.lower() in ("intl", "nazionali"):
        with_club = False
    elif lg.upper() in vb.LEAGUES:
        divs, with_intl = {lg.upper()}, False

    sink, view = [], {}
    try:
        report = vb.build_report(start, last, with_club, with_intl,
                                 sink=sink, view=view, divs=divs)
        if view is not None and extra_notes:
            view["notes"] = list(view.get("notes", [])) + extra_notes
    except Exception:
        report = "ERRORE durante l'esecuzione:\n\n" + traceback.format_exc()
        view = {}

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
    latest = {"run_id": run_id, "updated": updated, "report": report, "stats": stats, "view": view}

    os.makedirs("site", exist_ok=True)
    os.makedirs("data", exist_ok=True)
    boot = json.dumps({"updated": updated, "report": report, "stats": stats, "view": view},
                      ensure_ascii=False).replace("<", "\\u003c")
    with open("site/index.html", "w", encoding="utf-8") as f:
        f.write(PAGE.replace("__BOOT__", boot))
    with open("data/latest.json", "w", encoding="utf-8") as f:
        json.dump(latest, f, ensure_ascii=False)
    print(report)

    if not report.startswith("ERRORE") and os.environ.get("EVENT") != "push":
        notify(summary(report), os.environ.get("PAGE_URL"))


if __name__ == "__main__":
    main()
