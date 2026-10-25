"""Turns a scored live QA run into the report the QA brief asks for (one HTML page):
per scenario PASS/FAIL, the original messages, what was extracted, the database rows,
the optimizer's input and pick, and the gaps found.

    cd worker && uv run python tests/qa/report_qa.py <run dir> <out.html>"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
PERSONAS = json.loads((HERE.parents[1] / "fixtures" / "qa" / "personas.json").read_text())
E = html.escape


def money(cents):
    return "—" if cents is None else f"${cents / 100:,.0f}"


def chip(ok, yes="PASS", no="FAIL"):
    return f'<span class="chip {"pass" if ok else "fail"}">{yes if ok else no}</span>'


def table(head, rows):
    out = ["<div class=\"scroll\"><table><thead><tr>", *(f"<th>{E(h)}</th>" for h in head), "</tr></thead><tbody>"]
    for r in rows:
        out += ["<tr>", *(f"<td>{c}</td>" for c in r), "</tr>"]
    return "".join(out + ["</tbody></table></div>"])


def fold(title, body, open_=False):
    return f'<details{" open" if open_ else ""}><summary>{E(title)}</summary><div class="fold">{body}</div></details>'


def pre(data):
    return f"<pre>{E(json.dumps(data, indent=1, default=str)[:6000])}</pre>"


def extraction_html(extraction):
    parts = []
    for t in PERSONAS["travellers"]:
        e = extraction[t["name"]]
        rows = [[E(r["what"]), f"“{E(r['quote'])}”", E(f"{r['expected']} · {r['expected_hardness']}"),
                 E(f"{r['got']} · {r['got_hardness']}") if r["got_hardness"] else "<em>not captured</em>",
                 chip(r["match"], "match", "miss")] for r in e["items"]]
        rows += [[E(x["what"]), f"“{E(x['got'].get('source_quote', ''))}”", "<em>not expected</em>",
                  E(json.dumps(x["got"].get("rule") or x["got"].get("value"))[:90]), '<span class="chip note">extra</span>']
                 for x in e["extra"]]
        rows += [["from profile", "—", "—", E(json.dumps(x.get("rule") or x.get("value"))[:90]), '<span class="chip note">profile</span>']
                 for x in e["from_profile"]]
        said = t["replies"][0]["says"].replace("\n\n", "<br><br>")
        parts.append(f"<h4>{E(t['name'])}</h4><blockquote>{said}</blockquote>"
                     + table(["What", "Their words (expected)", "Expected", "Claude extracted", ""], rows))
    return "".join(parts)


def solve_html(solves, room):
    mine = [s for s in solves if str(s["room"]) == str(room)]
    if not mine:
        return "<p>No optimizer run in this room.</p>"
    s = mine[0]
    request, response = s.get("request") or {}, s.get("response") or {}
    soft = [c for c in request.get("constraints", []) if c.get("kind") == "soft"]
    rows = [[E(c.get("owner_id", "")), E(c["id"]), E(c["attribute"]), E(f"{c['operator']} {c['value']}"), str(c.get("importance", ""))]
            for c in soft]
    best = (response.get("proposals") or [{}])[0]
    people = [[E(p["participant_id"]), f"{p['new_compromise_burden']:.3f}", E(", ".join(p.get("unknown_preference_ids") or []) or "—")]
              for p in best.get("per_person", [])]
    return ("<p>Stage: " + E(", ".join(s["stage"])) + f" · status {E(s['status'])} · {len(request.get('candidates', []))} options "
            f"after the shortlist · {response.get('combinations_examined', '?')} combinations examined</p>"
            + table(["Whose", "Constraint", "Scores", "Wants", "Importance"], rows)
            + "<p>Chosen: " + E(", ".join(best.get("candidate_ids", []))) + "</p>"
            + table(["Person", "Compromise (0-1)", "Unknown facts"], people)
            + fold("Full optimizer request and response", pre({"request": request, "response": response})))


def picks_html(picks):
    rows = [[E(n), E(p["flight_identity"][:-10]), money(p["total_cents"]), str(p["stops"]), E(p["departure_date"]),
             E(p.get("return_arrival_date") or "—")] for n, p in picks.items()]
    return table(["Person", "Flight", "Round trip", "Stops out", "Leaves", "Home"], rows)


def build(run: Path) -> str:
    score = json.loads((run / "score.json").read_text())
    results = json.loads((run / "results.json").read_text())
    v = score["verdicts"]
    passed = sum(1 for x in v.values() if x["pass"])  # noqa: F841 (shown in the template)
    q1, q2, q3, q4, q5 = (v[f"QA-0{i}"] for i in range(1, 6))
    c1 = q1["checks"]
    sections = []

    sections.append(("QA-01", "Initial feasible negotiation", q1["pass"], f"""
<p>All four personas onboard through their private chats (their QA message verbatim; Claude plays their follow-ups), then the flight stage is planned. Hard limits are checked on the published plan.</p>
{table(["Check", "Result"], [[E(k.replace("_", " ")), chip(bool(c1.get(k)), "yes", "no")] for k in
    ("flight_plan_published", "chris_leaves_on_or_after_21", "chris_home_by_24", "chris_under_600", "nothing_after_the_trip")]
    + [["extraction matches the brief", f"{c1['extraction_matches']} of {c1['extraction_items']}"], ["scope", E(str(c1.get("scope")))]])}
<h3>The flight plan</h3>{picks_html(c1.get("picks", {}))}
{fold("What each person said, and what Claude extracted", extraction_html(q1["extraction"]))}
{fold("Optimizer input and result (room A, first plan)", solve_html(results["solves"], results["rooms"]["A"]))}"""))

    c2 = q2["checks"]
    sections.append(("QA-02", "Dynamic budget change", q2["pass"], f"""
<p>After everyone agrees the flights and the stay is proposed, Alex says: <q>{E(PERSONAS["changes"]["QA-02"]["says"])}</q> His agent shows the change back, he confirms, and the room replans.</p>
{table(["Check", "Result"], [["Alex's whole-trip limit now", E(", ".join(money(x) for x in c2["alex_budget_now"]))],
    ["agreed flights still stand", chip(c2["flights_still_agreed"], "yes", "no")],
    ["a new stay proposal", chip(c2["a_live_stay_proposal"], "yes", "no")],
    ["Alex's flight + his stay share", money(c2.get("alex_total")) + (" ≤ $900 " + chip(c2.get("within_900"), "yes", "no") if "within_900" in c2 else "")]])}"""))

    c3 = q3["checks"]
    sections.append(("QA-03", "No feasible agreement", q3["pass"], f"""
<p>Chris says: <q>{E(PERSONAS["travellers"][2]["changes"]["QA-03"]["says"])}</q> The trip still ends Nov 24.</p>
{table(["Check", "Result"], [["proposals left waiting for approval", str(c3["live_proposals_after"])],
    ["“no plan” events", str(c3["no_plan_events"])]])}
<h3>What the room sees</h3><blockquote>{E((c3["room_text"] or [""])[-1] or "")}</blockquote>
<h3>What only Chris sees</h3><blockquote>{E((c3["chris_note"] or [""])[-1] or "")}</blockquote>
{fold("Chris's rules after the change", pre(c3["chris_rules"]))}"""))

    c4 = q4["checks"]
    pick = c4.get("bella_pick")
    questions = "".join(f"<li>{E(q['kind'].replace('_', ' ').lower())}: <q>{E(q['asked'])}</q> {'— yes' if q['yes'] else '— no'}</li>"
                        for q in c4["bella_questions"])
    sections.append(("QA-04", "Soft preference compromise", q4["pass"], f"""
<p>Room C uses the pricey-nonstop variant: Bella's nonstops cost $1,350 and $1,480, her connections $430 and $350. She ranked comfort first and asked to be checked with before any compromise.</p>
{table(["Check", "Result"], [["her search filtered stops", "no (a wish, not a filter)" if c4["bella_search_filter"] is None else E(str(c4["bella_search_filter"]))],
    ["her flight in the plan", E(f"{pick['flight_identity'][:-10]}, {money(pick['total_cents'])}, {pick['stops']} stops") if pick else "<em>no plan: she declined each option</em>"]])}
<h3>What Bella was asked (Claude answered as Bella)</h3><ul class="asks">{questions or "<li>nothing</li>"}</ul>"""))

    c5 = q5["checks"]
    sections.append(("QA-05", "Persisted state correctness", q5["pass"], f"""
{table(["Check", "Result"], [["one live version per decision", chip(c5["one_live_version_each"], "yes", "no")],
    ["approvals of replaced versions are stale", chip(c5["superseded_approvals_stale"], "yes", "no")],
    ["the compromise ledger counts only agreed decisions", chip(c5["ledger_only_for_agreed"], "yes", "no")]])}
{fold("Proposal states by decision (room A)", pre(c5["states_by_decision"]))}"""))

    notes = json.loads((run / "notes.json").read_text()) if (run / "notes.json").exists() else {}
    for title, key in (("Found and fixed by these live runs", "fixed"), ("Still open", "open")):
        if notes.get(key):
            items = "".join(f"<li><b>{E(n['title'])}.</b> {E(n['detail'])}</li>" for n in notes[key])
            sections.append((key, title, None, f'<ul class="asks">{items}</ul>'))
    body = "".join(f"""<section id="{tid}"><header class="sect"><div><p class="eyebrow">{tid if ok is not None else "Notes"}</p><h2>{E(title)}</h2></div>{chip(ok) if ok is not None else ""}</header>{content}</section>"""
                   for tid, title, ok, content in sections)
    return TEMPLATE.format(passed=passed, total=len(v), db=E(score["database"]), minutes=score["minutes"],
                           calls=score["claude_calls"].get("claude", "?"), body=body,
                           nav="".join(f'<a href="#{tid}">{tid} {chip(ok)}</a>' for tid, _, ok, _ in sections if ok is not None))


TEMPLATE = """<title>Flight Stage QA Run</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@500;700&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
/* A test report: verdicts up top, one section per scenario, evidence folded under each. */
:root {{ --bg:#f3f5f7; --surface:#ffffff; --ink:#18222d; --muted:#5b6774; --line:#d9dfe5; --accent:#1f5f8b;
  --pass:#1f7a4d; --pass-bg:#e2f3ea; --fail:#a3361f; --fail-bg:#f8e6e1; --note-bg:#eceff3; --quote:#f7f9fb;
  --display:"Archivo","Helvetica Neue",Arial,sans-serif; --body:"Source Serif 4",Georgia,serif; --mono:"IBM Plex Mono",ui-monospace,Menlo,monospace; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#11171d; --surface:#18212a; --ink:#e3e9ee; --muted:#9aa7b3; --line:#2b3742;
  --accent:#7cb8e4; --pass:#7fd3a6; --pass-bg:#173326; --fail:#f0a08e; --fail-bg:#3a2019; --note-bg:#222c36; --quote:#151d25; color-scheme:dark; }} }}
:root[data-theme="dark"] {{ --bg:#11171d; --surface:#18212a; --ink:#e3e9ee; --muted:#9aa7b3; --line:#2b3742;
  --accent:#7cb8e4; --pass:#7fd3a6; --pass-bg:#173326; --fail:#f0a08e; --fail-bg:#3a2019; --note-bg:#222c36; --quote:#151d25; color-scheme:dark; }}
body {{ background:var(--bg); color:var(--ink); font:16px/1.6 var(--body); }}
.wrap {{ max-width:1000px; margin:0 auto; padding-inline:18px; padding-block:36px 72px; display:grid; gap:28px; }}
h1,h2,h3,h4 {{ font-family:var(--display); line-height:1.2; text-wrap:balance; margin:0; }}
h1 {{ font-size:clamp(28px,5vw,40px); }} h2 {{ font-size:22px; }} h3 {{ font-size:16px; margin-top:6px; }} h4 {{ font-size:15px; margin-top:14px; }}
p {{ margin:0; max-width:72ch; }} .muted {{ color:var(--muted); }}
.eyebrow {{ font:600 12px/1 var(--display); letter-spacing:1.4px; text-transform:uppercase; color:var(--muted); }}
.chip {{ font:600 12px/1 var(--display); padding:6px 10px; border-radius:999px; white-space:nowrap; letter-spacing:.4px; }}
.chip.pass {{ background:var(--pass-bg); color:var(--pass); }} .chip.fail {{ background:var(--fail-bg); color:var(--fail); }}
.chip.note {{ background:var(--note-bg); color:var(--muted); }}
nav {{ display:flex; flex-wrap:wrap; gap:10px 16px; }} nav a {{ color:var(--ink); text-decoration:none; font:600 14px var(--display); display:inline-flex; gap:8px; align-items:center; }}
section {{ background:var(--surface); border:1px solid var(--line); border-radius:12px; padding:20px; display:grid; gap:14px; min-width:0; }}
.sect {{ display:flex; justify-content:space-between; align-items:flex-start; gap:12px; flex-wrap:wrap; }}
.scroll {{ overflow-x:auto; }} table {{ border-collapse:collapse; width:100%; font-size:14px; }}
th,td {{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ font:600 11px/1.3 var(--display); letter-spacing:.8px; text-transform:uppercase; color:var(--muted); }}
td {{ font-variant-numeric:tabular-nums; }}
blockquote {{ margin:0; padding:12px 14px; background:var(--quote); border-left:3px solid var(--accent); font-size:15px; }}
q {{ font-style:italic; }} ul.asks {{ margin:0; padding-left:20px; display:grid; gap:6px; font-size:15px; }}
details {{ border-top:1px dashed var(--line); padding-top:10px; }} summary {{ cursor:pointer; color:var(--accent); font:600 14px var(--display); }}
summary:focus-visible {{ outline:2px solid var(--accent); outline-offset:3px; border-radius:4px; }}
.fold {{ display:grid; gap:10px; margin-top:10px; min-width:0; }}
pre {{ font:12px/1.45 var(--mono); background:var(--quote); border:1px solid var(--line); border-radius:8px; padding:12px; overflow-x:auto; max-height:420px; margin:0; }}
.summary {{ display:flex; flex-wrap:wrap; gap:8px 28px; font:15px var(--body); }} .summary b {{ font-family:var(--display); }}
</style>
<div class="wrap">
  <header style="display:grid;gap:10px">
    <p class="eyebrow">Tandem · flight-stage QA · live run</p>
    <h1>Flight Stage QA Run</h1>
    <p class="muted">The QA brief's five scenarios on the real pipeline: a fresh local SpacetimeDB, the real worker (Claude reads every message and powers every agent; the default optimizer chooses), the four personas onboarding by chat, and the brief's controlled flight itineraries instead of live search.</p>
    <div class="summary"><span><b>{passed} of {total}</b> scenarios pass</span><span>{minutes} minutes</span><span>{calls} Claude calls for the worker</span><span class="muted">database {db}</span></div>
    <nav aria-label="Scenarios">{nav}</nav>
  </header>
  {body}
</div>
"""


if __name__ == "__main__":
    run, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.write_text(build(run))
    print(out)
