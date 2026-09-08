"""Self-contained HTML report.

One file, no CDN, no framework, no dependencies — it has to open from a file://
URL on a laptop with no network. Inline CSS; the collapsible sections are native
<details>, so the page carries no JavaScript at all.

Structure mirrors the text report: what to do first, the numbers behind it
collapsed underneath. A reader who only opens the first section should leave
with a list of candidates to review and steps to check, not a table.

Visual language follows the platform's own UI: dark header, lime accent, a warm
pastel wash behind white rounded cards, and status pills shaped like the ones in
the applications list.

The categorical palette for outcome mix is NOT the brand's UI colours. Those are
tuned for chrome, not for adjacent data marks: a green/red pair reads as a
3.3 delta-E under deuteranopia, i.e. the same colour. These four were re-stepped
and validated (lightness band, chroma floor, CVD separation, normal-vision floor,
contrast) for both light and dark surfaces. Status colours for finding priority
are kept separate from them and always ship with a label, never colour alone.
"""
from __future__ import annotations

import html
from datetime import datetime, timezone

from analyzer.analysis import (
    LOW_CONFIDENCE_N,
    PRIORITY_LABEL,
    Analysis,
    Cell,
    Signal,
    category_totals,
    job_totals,
)
from analyzer.fetch import Dataset
from analyzer.reasons import BucketConfig, summarize
from analyzer.report import LIMITATION

#: Validated categorical palette: passed / rejected / opt-out / not evaluated.
OUTCOME_LIGHT = ("#3178a8", "#c2365c", "#b07d18", "#6f5fa8")
OUTCOME_DARK = ("#4a92c4", "#d14f70", "#b8862a", "#8878c8")

CSS = """
:root{
  --ink:#1b1a22; --ink-2:#4a4854; --ink-3:#76737f;
  --surface:#ffffff; --page:#fbfaf7; --line:#e9e6e0;
  --dark:#17161c; --accent:#d4f04f; --primary:#6b4de6;
  --pass:#3178a8; --reject:#c2365c; --optout:#b07d18; --none:#6f5fa8;
  --act:#c2365c; --check:#b07d18; --watch:#5a6675;
  --act-bg:#fdeef1; --check-bg:#fdf4e3; --watch-bg:#eef1f4;
  --radius:14px;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --ink:#f2f0f5; --ink-2:#bdb9c6; --ink-3:#8a8794;
    --surface:#201f26; --page:#161519; --line:#332f3a;
    --pass:#4a92c4; --reject:#d14f70; --optout:#b8862a; --none:#8878c8;
    --act:#d14f70; --check:#b8862a; --watch:#8a95a5;
    --act-bg:#341f27; --check-bg:#33291a; --watch-bg:#23262b;
  }
}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);
  font:15px/1.55 "Segoe UI",-apple-system,BlinkMacSystemFont,Roboto,Helvetica,Arial,sans-serif;}
.wash{background:
   radial-gradient(900px 380px at 8% -6%, #fdf3c9 0%, transparent 62%),
   radial-gradient(760px 340px at 92% -12%, #efe0f7 0%, transparent 64%),
   radial-gradient(680px 300px at 60% 0%, #e6f5cf 0%, transparent 58%);
  padding:0 0 8px;}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]) .wash{background:
   radial-gradient(900px 380px at 8% -6%, #2a2718 0%, transparent 62%),
   radial-gradient(760px 340px at 92% -12%, #241c30 0%, transparent 64%);}}
header.bar{background:var(--dark);color:#fff;padding:18px 28px;display:flex;
  align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}
header.bar h1{margin:0;font-size:19px;font-weight:650;letter-spacing:.2px}
header.bar .dot{color:var(--accent)}
header.bar .meta{color:#a9a6b4;font-size:13px}
main{max-width:1120px;margin:0 auto;padding:26px 22px 60px}
section{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);
  padding:22px 24px;margin:0 0 20px;box-shadow:0 1px 2px rgba(20,18,30,.04)}
h2{margin:0 0 4px;font-size:16px;font-weight:650;letter-spacing:.2px}
h2 + .sub{margin:0 0 18px;color:var(--ink-3);font-size:13.5px;max-width:74ch}
h3{margin:22px 0 8px;font-size:14px;font-weight:620;color:var(--ink-2)}
p{margin:0 0 10px;max-width:80ch}
.muted{color:var(--ink-3)}
.jobs{list-style:none;margin:10px 0 0;padding:0}
.jobs li{padding:9px 0;border-top:1px solid var(--line)}
.jobs .t{font-weight:600}
.jobs .d{font-size:13px;color:var(--ink-3)}
.tag{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;
  color:var(--ink-3);background:var(--page);border:1px solid var(--line);
  border-radius:6px;padding:1px 6px;margin-left:7px}
.card{border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:0 0 12px;
  background:var(--surface)}
.card.act{border-left:4px solid var(--act)}
.card.check{border-left:4px solid var(--check)}
.card .scope{font-weight:600;margin-left:9px}
.card ul{margin:10px 0 0;padding:0;list-style:none}
.card li{padding:8px 0 4px;border-top:1px solid var(--line)}
.card li:first-child{border-top:none;padding-top:6px}
.card .what{font-weight:600;margin:0 0 4px}
.card .why{color:var(--ink-2);font-size:13.5px;margin:0 0 6px}
.card .do{font-size:13.5px;margin:0}
.card .do b{font-weight:640}
.card .people{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 2px}
.card .person{font-size:12.5px;background:var(--page);border:1px solid var(--line);
  border-radius:999px;padding:2px 9px}
.pill{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;font-weight:640;
  letter-spacing:.4px;text-transform:uppercase;border-radius:999px;padding:3px 10px;
  border:1px solid transparent}
.pill.act{color:var(--act);background:var(--act-bg);border-color:var(--act)}
.pill.check{color:var(--check);background:var(--check-bg);border-color:var(--check)}
.pill.watch{color:var(--watch);background:var(--watch-bg);border-color:var(--watch)}
.pill.thin{color:var(--ink-3);background:var(--page);border-color:var(--line);
  text-transform:none;letter-spacing:0;font-weight:560}
.watch{margin:8px 0 0;padding-left:18px;color:var(--ink-2);font-size:13.5px}
.watch li{margin:5px 0}
.scroll{overflow-x:auto;max-width:100%}
table{width:100%;border-collapse:collapse;font-size:13.5px;margin-top:6px}
th,td{padding:8px 9px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
th[title]{cursor:help;text-decoration:underline dotted var(--ink-3);text-underline-offset:3px}
th:first-child,td:first-child{text-align:left;white-space:normal}
thead th{color:var(--ink-3);font-weight:600;font-size:12px;text-transform:uppercase;
  letter-spacing:.5px;border-bottom:1px solid var(--line)}
tbody tr:hover{background:var(--page)}
tr.total td{font-weight:640;border-bottom:2px solid var(--line)}
tr.jobhead td{padding-top:16px;font-weight:640;border-bottom:none;color:var(--ink)}
td .n{color:var(--ink-3);font-size:12px;margin-left:4px}
.mix{display:flex;height:9px;border-radius:4px;overflow:hidden;min-width:104px;gap:2px;background:transparent}
.mix i{display:block;height:100%}
.mix i:first-child{border-radius:4px 0 0 4px}
.mix i:last-child{border-radius:0 4px 4px 0}
.legend{display:flex;gap:16px;flex-wrap:wrap;margin:12px 0 2px;font-size:12.5px;color:var(--ink-2)}
.legend span{display:inline-flex;align-items:center;gap:6px}
.swatch{width:10px;height:10px;border-radius:3px;display:inline-block}
.notes{margin:14px 0 0;padding:0;list-style:none}
.notes li{padding:6px 0 6px 16px;position:relative;color:var(--ink-2);font-size:13.5px;max-width:86ch}
.notes li::before{content:"";position:absolute;left:0;top:13px;width:5px;height:5px;
  border-radius:50%;background:var(--accent)}
.reason{display:grid;grid-template-columns:150px 46px 1fr;gap:10px;align-items:center;
  padding:5px 0;font-size:13.5px}
.reason .bar{background:var(--page);border-radius:4px;height:9px;position:relative;overflow:hidden}
.reason .bar i{display:block;height:100%;border-radius:4px;background:var(--reject)}
.reason .ex{grid-column:1/-1;color:var(--ink-3);font-size:12.5px;padding:0 0 6px}
details{border-top:1px solid var(--line);padding:12px 0 2px}
details summary{cursor:pointer;font-weight:600;font-size:14px;list-style:none}
details summary::-webkit-details-marker{display:none}
details summary::before{content:"▸ ";color:var(--ink-3)}
details[open] summary::before{content:"▾ "}
details ul{margin:10px 0 4px;padding-left:20px;color:var(--ink-2);font-size:13.5px}
details li{margin:4px 0}
details .body{padding:6px 0 4px}
footer{color:var(--ink-3);font-size:12.5px;text-align:center;padding:8px 0 28px}
@media print{.wash{background:none}section{break-inside:avoid;box-shadow:none}details{display:block}}
"""


def _e(text: object) -> str:
    return html.escape(str(text if text is not None else ""))


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _mix_bar(cell: Cell) -> str:
    """Proportion of outcomes. Counts are in the adjacent columns, so the bar is
    a shape to scan, never the only place a number appears."""
    if not cell.total:
        return ""
    parts = (
        (cell.positive, "var(--pass)", "passed"),
        (cell.negative, "var(--reject)", "rejected"),
        (cell.opt_out, "var(--optout)", "opted out"),
        (cell.unevaluated + cell.unmappable, "var(--none)", "not evaluated"),
    )
    title = ", ".join(f"{n} {label}" for n, _c, label in parts if n)
    segments = "".join(
        f'<i style="width:{n / cell.total:.2%};background:{colour}"></i>'
        for n, colour, _l in parts if n
    )
    return f'<span class="mix" title="{_e(title)}">{segments}</span>'


def _cell_row(label: str, cell: Cell, css: str = "") -> str:
    return (
        f'<tr class="{css}">'
        f"<td>{_e(label)}</td>"
        f"<td>{_mix_bar(cell)}</td>"
        f"<td>{cell.total}</td>"
        f"<td>{cell.positive}</td>"
        f"<td>{cell.negative}</td>"
        f"<td>{cell.opt_out}</td>"
        f"<td>{cell.unevaluated + cell.unmappable}</td>"
        f"<td>{cell.reviewed}</td>"
        f"<td>{cell.overrides}</td>"
        f"</tr>"
    )


#: Counts only, so every row adds up: total = passed + rejected + opt-out + none.
#: Definitions live in the column tooltips rather than a paragraph above.
HEAD_ROW = (
    "<thead><tr>"
    "<th>step</th>"
    "<th>mix</th>"
    "<th title='Every decision Paul recorded at this step: passed + rejected + opt-out + none'>total</th>"
    "<th title='Paul concluded the candidate meets the criteria'>passed</th>"
    "<th title='Paul concluded the candidate does not meet the criteria'>rejected</th>"
    "<th title='The candidate stopped responding or declined to continue; Paul never judged them'>opt-out</th>"
    "<th title='No usable decision: not evaluated yet, or a value the platform does not document (see skipped)'>none</th>"
    "<th title='Decisions a recruiter opened, out of passed + rejected'>reviewed</th>"
    "<th title='Reviewed decisions the recruiter disagreed with. Only opened decisions count, and early "
    "rejections are rarely opened, so this says how often recruiters disagree when they look, not how "
    "often Paul is right'>reversed</th>"
    "</tr></thead>"
)

LEGEND = (
    '<div class="legend">'
    '<span><i class="swatch" style="background:var(--pass)"></i>passed</span>'
    '<span><i class="swatch" style="background:var(--reject)"></i>rejected</span>'
    '<span><i class="swatch" style="background:var(--optout)"></i>opted out</span>'
    '<span><i class="swatch" style="background:var(--none)"></i>not evaluated</span>'
    "</div>"
)


def _signal_html(signal: Signal) -> str:
    people = ""
    if signal.people:
        people = "<div class='people'>" + "".join(
            f"<span class='person'>{_e(p)}</span>" for p in signal.people
        ) + "</div>"
    return (
        f"<li><div class='what'>{_e(signal.headline)}</div>"
        f"<div class='why'>{_e(signal.evidence)}</div>"
        f"<p class='do'><b>{_e(signal.action)}</b>{':' if signal.people else ''}</p>"
        f"{people}</li>"
    )


def render_html(analysis: Analysis, dataset: Dataset, funnel_notes: list[str]) -> str:
    parts: list[str] = []
    add = parts.append

    total = sum(c.total for c in analysis.cells)
    candidates = len({(r.job_id, r.person_slug) for r in dataset.records})
    generated = datetime.now(timezone.utc).strftime("%d %B %Y, %H:%M UTC")

    add("<!doctype html><html lang='en'><head><meta charset='utf-8'>")
    add("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    add("<title>Screening Accuracy Report</title>")
    add(f"<style>{CSS}</style></head><body><div class='wash'>")
    add("<header class='bar'><h1>Screening Accuracy Report "
        "<span class='dot'>·</span> Paul's decisions</h1>"
        f"<div class='meta'>{_e(generated)}</div></header><main>")

    # -- what was analysed -------------------------------------------------
    add("<section><h2>What was analysed</h2>")
    add(f"<p class='sub'>{len(analysis.jobs)} job(s), {candidates} candidate(s), "
        f"{total} decision(s) that Paul recorded at a screening step.</p>")
    add("<ul class='jobs'>")
    for job_id, job in sorted(analysis.jobs.items(), key=lambda kv: kv[1].title):
        cells = analysis.by_job(job_id)
        people = len({r.person_slug for r in dataset.records if r.job_id == job_id})
        add(f"<li><div class='t'>{_e(job.title)}"
            f"<span class='tag'>{_e(job.external_id or job_id)}</span></div>"
            f"<div class='d'>{people} candidate(s) · {sum(c.total for c in cells)} decision(s) · "
            f"{len(cells)} step(s) with a screening agent</div></li>")
    add("</ul></section>")

    # -- what to do --------------------------------------------------------
    add("<section><h2>What to do</h2>")
    add("<p class='sub'>One card per job and step. <em>Act now</em> means at least five "
        "people are affected; <em>check</em> means the pattern is real but small. "
        "Names are the candidates concerned.</p>")
    if not analysis.findings:
        add("<p>Nothing. Every step is within the thresholds documented in TECH_NOTE.md.</p>")
    for finding in analysis.findings:
        add(f"<div class='card {finding.priority}'>"
            f"<span class='pill {finding.priority}'>{_e(PRIORITY_LABEL[finding.priority])}</span>"
            f"<span class='scope'>{_e(finding.scope)}</span><ul>")
        for signal in finding.signals:
            add(_signal_html(signal))
        add("</ul></div>")
    if analysis.watch:
        add("<h3>Worth watching</h3>")
        add(f"<p class='muted'>Too few decisions to act on yet (under {LOW_CONFIDENCE_N}, or "
            "fewer than five people). Listed so they are not lost.</p><ul class='watch'>")
        for scope, signal in analysis.watch:
            add(f"<li><strong>{_e(scope)}</strong>: {_e(signal.headline)}.</li>")
        add("</ul>")
    add("</section>")

    # -- the numbers -------------------------------------------------------
    add("<section><h2>The numbers</h2>")
    add("<p class='sub'>Counts, per listing and step; every row adds up to its total. "
        "Hover a column heading for what it counts.</p>")

    add("<details open><summary>Each job, step by step</summary><div class='body'>")
    add(LEGEND)
    add("<div class='scroll'><table>" + HEAD_ROW + "<tbody>")
    for job_id, job in sorted(analysis.jobs.items(), key=lambda kv: kv[1].title):
        cells = analysis.by_job(job_id)
        if not cells:
            continue
        add(f"<tr class='jobhead'><td colspan='9'>{_e(job.title)}</td></tr>")
        for cell in cells:
            add(_cell_row(cell.step_name, cell))
        add(_cell_row("all steps", job_totals(analysis, job_id), css="total"))
    add("</tbody></table></div></div></details>")

    add("<details><summary>The funnel, all jobs combined</summary><div class='body'>")
    add("<div class='scroll'><table>" + HEAD_ROW + "<tbody>")
    for category in analysis.categories:
        add(_cell_row(category, category_totals(analysis, category)))
    add("</tbody></table></div>")
    if funnel_notes:
        add("<ul class='notes'>")
        for note in funnel_notes:
            add(f"<li>{_e(note)}</li>")
        add("</ul>")
    add("</div></details>")

    config = BucketConfig.load()
    add("<details><summary>Why Paul rejected people, per job and step</summary><div class='body'>")
    rendered_any = False
    for job_id, job in sorted(analysis.jobs.items(), key=lambda kv: kv[1].title):
        for cell in analysis.by_job(job_id):
            summary = summarize(cell.explanations, config)
            if not summary.total:
                continue
            rendered_any = True
            add(f"<h3>{_e(job.title)} / {_e(cell.step_name)} — {summary.total} rejection(s)</h3>")
            if summary.vocabulary_is_fixed:
                phrases = f"{summary.distinct_exact} phrase{'s' if summary.distinct_exact != 1 else ''}"
                add(f"<p class='muted'>Paul reuses the same {phrases} here, "
                    f"so these are exact counts of what he wrote.</p>")
                for text, count in summary.exact.most_common(6):
                    add(f"<div class='reason'><span>{_e(text[:52])}</span><span>{count}</span>"
                        f"<span class='bar'><i style='width:{count / summary.total:.1%}'></i></span>"
                        f"</div>")
            else:
                add(f"<p class='muted'>Paul phrases these {summary.distinct_exact} different ways "
                    f"across {summary.total} rejections, so they are grouped by topic.</p>")
                for bucket, count in summary.top(6):
                    example = summary.examples.get(bucket, "")
                    add(f"<div class='reason'><span>{_e(bucket)}</span>"
                        f"<span>{count} · {count / summary.total:.0%}</span>"
                        f"<span class='bar'><i style='width:{count / summary.total:.1%}'></i></span>"
                        f"<span class='ex'>e.g. “{_e(example[:96])}”</span></div>")
            if summary.other_share:
                add(f"<p class='muted'>{summary.buckets.get('other', 0)} rejection(s) matched no "
                    f"topic ({summary.other_share:.0%}). Add their wording to "
                    f"<code>reason_buckets.json</code>.</p>")
            if summary.missing:
                add(f"<p class='muted'>{summary.missing} rejection(s) carried no explanation.</p>")
    if not rendered_any:
        add("<p>No rejections were recorded.</p>")
    add("</div></details></section>")

    # -- data quality + limitations ---------------------------------------
    quality = dataset.quality
    add("<section><h2>What was skipped or looked wrong</h2>")
    if not quality.total:
        add("<p>Nothing. Every record fetched was usable.</p>")
    for label, items in (
        ("job(s) could not be loaded", quality.job_failures),
        ("candidate(s) could not be loaded", quality.candidate_failures),
        ("decision(s) used a value the platform does not document", quality.malformed_decisions),
        ("candidate(s) had more than one record for the same step", quality.superseded_records),
        ("step(s) expect a screening agent but have none", quality.steps_without_agent),
        ("record(s) were unusable and skipped", quality.skipped_records),
    ):
        if not items:
            continue
        add(f"<details><summary>{len(items)} {_e(label)}</summary><ul>")
        for item in items:
            add(f"<li>{_e(item)}</li>")
        add("</ul></details>")
    add("</section>")

    add(f"<section><h2>One thing to keep in mind</h2><p>{_e(LIMITATION)}</p></section>")

    add("</main></div>")
    add(f"<footer>Generated {_e(generated)} · thresholds and method documented in "
        f"TECH_NOTE.md</footer>")
    add("</body></html>")
    return "\n".join(parts)
