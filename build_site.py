#!/usr/bin/env python3
"""Genera la pagina web (site/index.html) con i consigli del giorno.
Lanciato da GitHub Actions, ma funziona anche a mano: python build_site.py --days 1
"""
import argparse
import html
import os
import traceback
import urllib.request
from datetime import date, datetime, timedelta

import value_bet as vb


def page(body, updated, repo):
    run_link = ""
    if repo:
        run_link = ('<a class="btn" href="https://github.com/%s/actions/workflows/report.yml">'
                    'Rilancia lo script</a>' % html.escape(repo))
    return """<!doctype html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Consigli del giorno</title>
<style>
:root{color-scheme:light dark;--bg:#fff;--fg:#1a1a1a;--card:#f3f4f6;--acc:#0a7d3b}
@media (prefers-color-scheme:dark){:root{--bg:#111;--fg:#eee;--card:#1d1f23;--acc:#4ade80}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.45 -apple-system,system-ui,sans-serif;
padding:max(16px,env(safe-area-inset-top)) 16px max(16px,env(safe-area-inset-bottom))}
h1{font-size:1.3rem;margin:.2rem 0}
.upd{opacity:.65;font-size:.85rem;margin-bottom:12px}
pre{white-space:pre-wrap;word-wrap:break-word;background:var(--card);padding:14px;border-radius:12px;
font:14px/1.5 ui-monospace,Menlo,monospace}
.btn{display:inline-block;margin:6px 0 14px;padding:10px 16px;border-radius:10px;background:var(--acc);
color:#fff;text-decoration:none;font-weight:600}
</style></head><body>
<h1>Consigli del giorno</h1>
<div class="upd">Aggiornato: %s</div>
%s
<pre>%s</pre>
</body></html>""" % (html.escape(updated), run_link, html.escape(body))


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
        headers["Click"] = click   # toccando la notifica si apre la pagina
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
    try:
        report = vb.build_report(today, last, not args.no_club, not args.no_intl)
    except Exception:
        report = "ERRORE durante l'esecuzione:\n\n" + traceback.format_exc()

    os.makedirs("site", exist_ok=True)
    updated = datetime.now().strftime("%d/%m/%Y %H:%M")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    with open("site/index.html", "w", encoding="utf-8") as f:
        f.write(page(report, updated, repo))
    with open("site/report.txt", "w", encoding="utf-8") as f:
        f.write(report)
    print(report)
    # notifica solo per le esecuzioni automatiche o manuali, non a ogni modifica
    if not report.startswith("ERRORE") and os.environ.get("EVENT") != "push":
        notify(summary(report), os.environ.get("PAGE_URL"))


if __name__ == "__main__":
    main()
