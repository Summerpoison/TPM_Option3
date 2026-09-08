"""Self-contained HTML report.

One file, no CDN, no framework, no dependencies — it has to open from a file://
URL on a laptop with no network. Inline CSS; the collapsible sections are native
<details>, so the page carries no JavaScript at all.

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
import json
from datetime import datetime, timezone

from analyzer.analysis import LOW_CONFIDENCE_N, Analysis, Cell, category_totals, job_totals
from analyzer.fetch import Dataset
from analyzer.reasons import BucketConfig, summarize

#: Validated categorical palette: passed / rejected / opt-out / not evaluated.
OUTCOME_LIGHT = ("#3178a8", "#c2365c", "#b07d18", "#6f5fa8")
OUTCOME_DARK = ("#4a92c4", "#d14f70", "#b8862a", "#8878c8")

CSS = """
:root{
  --ink:#1b1a22; --ink-2:#4a4854; --ink-3:#76737f;
  --surface:#ffffff; --page:#fbfaf7; --line:#e9e6e0;
  --dark:#17161c; --accent:#d4f04f; --primary:#6b4de6;
  --pass:#3178a8; --reject:#c2365c; --optout:#b07d18; --none:#6f5fa8;
  --high:#c2365c; --medium:#b07d18; --low:#5a6675;
  --high-bg:#fdeef1; --medium-bg:#fdf4e3; --low-bg:#eef1f4;
  --radius:14px;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --ink:#f2f0f5; --ink-2:#bdb9c6; --ink-3:#8a8794;
    --surface:#201f26; --page:#161519; --line:#332f3a;
    --pass:#4a92c4; --reject:#d14f70; --optout:#b8862a; --none:#8878c8;
    --high:#d14f70; --medium:#b8862a; --low:#8a95a5;
    --high-bg:#341f27; --medium-bg:#33291a; --low-bg:#23262b;
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
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(158px,1fr));gap:12px;margin:4px 0 6px}
.tile{background:var(--page);border:1px solid var(--line);border-radius:11px;padding:13px 15px}
.tile .n{font-size:26px;font-weight:660;letter-spacing:-.4px;line-height:1.15}
.tile .k{font-size:12px;color:var(--ink-3);margin-top:3px}
.tile .of{font-size:12px;color:var(--ink-3)}
.jobs{list-style:none;margin:10px 0 0;padding:0}
.jobs li{padding:9px 0;border-top:1px solid var(--line)}
.jobs .t{font-weight:600}
.jobs .d{font-size:13px;color:var(--ink-3)}
.tag{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;
  color:var(--ink-3);background:var(--page);border:1px solid var(--line);
  border-radius:6px;padding:1px 6px;margin-left:7px}
.finding{border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:0 0 11px;
  background:var(--surface)}
.finding.high{border-left:4px solid var(--high)}
.finding.medium{border-left:4px solid var(--medium)}
.finding.low{border-left:4px solid var(--low)}
.pill{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;font-weight:640;
  letter-spacing:.4px;text-transform:uppercase;border-radius:999px;padding:3px 10px;
  border:1px solid transparent}
.pill.high{color:var(--high);background:var(--high-bg);border-color:var(--high)}
.pill.medium{color:var(--medium);background:var(--medium-bg);border-color:var(--medium)}
.pill.low{color:var(--low);background:var(--low-bg);border-color:var(--low)}
.pill.thin{color:var(--ink-3);background:var(--page);border-color:var(--line);
  text-transform:none;letter-spacing:0;font-weight:560}
.finding .scope{color:var(--ink-3);font-size:13px;margin-left:9px}
.finding .what{font-weight:600;margin:9px 0 6px}
.finding dl{display:grid;grid-template-columns:64px 1fr;gap:3px 12px;margin:0;font-size:13.5px}
.finding dt{color:var(--ink-3)}
.finding dd{margin:0}
table{width:100%;border-collapse:collapse;font-size:13.5px;margin-top:6px}
th,td{padding:8px 9px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
th:first-child,td:first-child{text-align:left;white-space:normal}
thead th{color:var(--ink-3);font-weight:600;font-size:12px;text-transform:uppercase;
  letter-spacing:.5px;border-bottom:1px solid var(--line)}
tbody tr:hover{background:var(--page)}
tr.total td{font-weight:640;border-bottom:2px solid var(--line)}
tr.jobhead td{padding-top:16px;font-weight:640;border-bottom:none;color:var(--ink)}
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
footer{color:var(--ink-3);font-size:12.5px;text-align:center;padding:8px 0 28px}
@media print{.wash{background:none}section{break-inside:avoid;box-shadow:none}}
"""

#: The tables interleave job-heading rows with data rows, so any client-side
#: reorder files a step under the wrong job. Sorting was removed rather than
#: patched: these tables are small and already ordered by pipeline position,
#: which is the order a reader wants. The JSON output is there for slicing.
JS = ""


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
    thin = (
        f' <span class="pill thin">only {cell.total} decision'
        f'{"" if cell.total == 1 else "s"}</span>' if cell.low_confidence else ""
    )
    return (
        f'<tr class="{css}">'
        f"<td>{_e(label)}{thin}</td>"
        f"<td>{_mix_bar(cell)}</td>"
        f"<td>{cell.total}</td>"
        f"<td>{cell.positive}</td>"
        f"<td>{cell.negative}</td>"
        f"<td>{cell.opt_out}</td>"
        f"<td>{_pct(cell.review_coverage)}</td>"
        f"<td>{_pct(cell.override_rate)}</td>"
        f"</tr>"
    )


HEAD_ROW = (
    "<thead><tr><th>step</th><th>mix</th><th>total</th><th>passed</th>"
    "<th>rejected</th><th>opt-out</th><th>reviewed</th><th>reversed</th></tr></thead>"
)

LEGEND = (
    '<div class="legend">'
    '<span><i class="swatch" style="background:var(--pass)"></i>passed</span>'
    '<span><i class="swatch" style="background:var(--reject)"></i>rejected</span>'
    '<span><i class="swatch" style="background:var(--optout)"></i>opted out</span>'
    '<span><i class="swatch" style="background:var(--none)"></i>not evaluated</span>'
    "</div>"
)


def render_html(analysis: Analysis, dataset: Dataset, funnel_notes: list[str]) -> str:
    parts: list[str] = []
    add = parts.append

    total = sum(c.total for c in analysis.cells)
    judged = sum(c.judged for c in analysis.cells)
    positive = sum(c.positive for c in analysis.cells)
    negative = sum(c.negative for c in analysis.cells)
    opt_out = sum(c.opt_out for c in analysis.cells)
    reviewed = sum(c.reviewed for c in analysis.cells)
    overrides = sum(c.overrides for c in analysis.cells)
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

    # -- what Paul decided -------------------------------------------------
    add("<section><h2>What Paul decided</h2>")
    add("<div class='tiles'>")
    # `judged` counts decisions, not people -- a candidate is judged again at
    # each step they reach, so this is routinely larger than the candidate pool.
    add(f"<div class='tile'><div class='n'>{judged}</div>"
        f"<div class='k'>decisions Paul concluded "
        f"<span class='of'>(across {candidates} candidates)</span></div></div>")
    if judged:
        add(f"<div class='tile'><div class='n' style='color:var(--reject)'>"
            f"{negative / judged:.0%}</div><div class='k'>rejected "
            f"<span class='of'>({negative} of {judged})</span></div></div>")
    add(f"<div class='tile'><div class='n' style='color:var(--optout)'>{opt_out}</div>"
        f"<div class='k'>opted out before judgement</div></div>")
    if total:
        add(f"<div class='tile'><div class='n'>{reviewed / total:.0%}</div>"
            f"<div class='k'>reviewed by a recruiter <span class='of'>({reviewed} of {total})</span>"
            f"</div></div>")
    add(f"<div class='tile'><div class='n'>{overrides}</div>"
        f"<div class='k'>reversed by a recruiter</div></div>")
    add("</div>")
    if opt_out:
        add(f"<p class='muted'>The {opt_out} opt-out(s) stopped responding or declined to "
            f"continue, so Paul never judged them. They are counted separately and excluded "
            f"from the rejection rate.</p>")
    if reviewed:
        add("<p class='muted'><strong>Reading the reversal rate:</strong> it covers only "
            "decisions a recruiter opened. Candidates Paul rejects early are rarely opened, "
            "so mistakes there never show up in it. Read it as how often recruiters disagree "
            "when they look — not as how often Paul is right.</p>")
    else:
        add("<p class='muted'>No decision has been reviewed by a recruiter, so agreement "
            "cannot be measured.</p>")
    add("</section>")

    # -- findings ----------------------------------------------------------
    add("<section><h2>Findings</h2>")
    add("<p class='sub'>Priority is how urgently this needs attention. It is not a score "
        "for Paul.</p>")
    if not analysis.anomalies:
        add("<p>Nothing flagged. Every step is within the thresholds documented in "
            "TECH_NOTE.md.</p>")
    for anomaly in analysis.anomalies:
        thin = (f" <span class='pill thin'>only {anomaly.n} decision"
                f"{'' if anomaly.n == 1 else 's'} — treat as a hint</span>"
                if anomaly.low_confidence else "")
        add(f"<div class='finding {anomaly.severity}'>"
            f"<span class='pill {anomaly.severity}'>{_e(anomaly.severity)} priority</span>"
            f"<span class='scope'>{_e(anomaly.scope)}</span>"
            f"<div class='what'>{_e(anomaly.headline)}{thin}</div>"
            f"<dl><dt>Why</dt><dd>{_e(anomaly.detail)}</dd>"
            f"<dt>Action</dt><dd>{_e(anomaly.action)}</dd></dl></div>")
    add("</section>")

    # -- per job -----------------------------------------------------------
    add("<section><h2>Each job, step by step</h2>")
    add("<p class='sub'>The view to act on: every rate here belongs to one listing at one "
        "step. <em>Reviewed</em> is the share a recruiter opened; <em>reversed</em> is the "
        "share of those they disagreed with.</p>")
    add(LEGEND)
    add("<table>" + HEAD_ROW + "<tbody>")
    for job_id, job in sorted(analysis.jobs.items(), key=lambda kv: kv[1].title):
        cells = analysis.by_job(job_id)
        if not cells:
            continue
        add(f"<tr class='jobhead'><td colspan='8'>{_e(job.title)}</td></tr>")
        for cell in cells:
            add(_cell_row(cell.step_name, cell))
        add(_cell_row("all steps", job_totals(analysis, job_id), css="total"))
    add("</tbody></table></section>")

    # -- funnel ------------------------------------------------------------
    add("<section><h2>The funnel, all jobs combined</h2>")
    add("<p class='sub'>Where candidates are lost, and how thin review coverage gets "
        "further down.</p>")
    add("<table>" + HEAD_ROW + "<tbody>")
    for category in analysis.categories:
        add(_cell_row(category, category_totals(analysis, category)))
    add("</tbody></table>")
    if funnel_notes:
        add("<ul class='notes'>")
        for note in funnel_notes:
            add(f"<li>{_e(note)}</li>")
        add("</ul>")
    add("</section>")

    # -- reasons -----------------------------------------------------------
    config = BucketConfig.load()
    add("<section><h2>Why Paul rejected people</h2>")
    rendered_any = False
    for job_id, job in sorted(analysis.jobs.items(), key=lambda kv: kv[1].title):
        summary = summarize(job_totals(analysis, job_id).explanations, config)
        if not summary.total:
            continue
        rendered_any = True
        add(f"<h3>{_e(job.title)} — {summary.total} rejection(s)</h3>")
        if summary.vocabulary_is_fixed:
            add(f"<p class='muted'>Paul reuses the same {summary.distinct_exact} phrases here, "
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
    add("</section>")

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

    add("<section><h2>What this report cannot tell you</h2><ul class='notes'>")
    for line in (
        "Whether Paul is right. Only reviewed decisions can be checked, and recruiters mostly "
        "review candidates who got through. Rejections are the blind spot.",
        f"Much from small numbers. Anything under {LOW_CONFIDENCE_N} decisions is marked. Late "
        "steps are small by nature, so read those rows as direction rather than measurement.",
        "Whether a rejection reason is fair. Reasons are grouped by topic and the topic is "
        "checked against the step's criteria; the reasoning itself is not judged.",
        "How certain a reversal is. Ones inferred from a recruiter's own decision are weaker "
        "evidence than an explicit rejection of Paul's suggestion; the JSON separates them.",
    ):
        add(f"<li>{_e(line)}</li>")
    add("</ul></section>")

    add("</main></div>")
    add(f"<footer>Generated {_e(generated)} · thresholds and method documented in "
        f"TECH_NOTE.md</footer>")
    add("</body></html>")
    return "\n".join(parts)
