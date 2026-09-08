"""Rendering. Turns analysis results into something a colleague can act on.

Two rules from the brief drive everything here:

  * insights are sentences with numbers, not bare counts
  * every insight implies an action

So a finding reads "Recruiters reversed Paul on 40% of the decisions they
reviewed at this step (4 of 10) -- check the step's criteria against the
listing", not "override_rate: 0.4".

Percentages always appear next to the n they came from, and any cell below the
low-confidence threshold says so. A number without its denominator is how you
end up with a dashboard nobody trusts.

The agent is called Paul throughout, because that is what the people reading
this call it.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from analyzer.analysis import (
    LOW_CONFIDENCE_N,
    Analysis,
    Cell,
    category_totals,
    job_totals,
)
from analyzer.fetch import Dataset

RULE = "=" * 78
THIN = "-" * 78
COLUMNS = (
    f"  {'step':<24}{'total':>6}{'passed':>8}{'rejected':>10}"
    f"{'opt-out':>9}{'reviewed':>10}{'reversed':>10}"
)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _plural(count: int, word: str = "decision") -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _confidence(cell: Cell) -> str:
    return f"   (only {_plural(cell.total)})" if cell.low_confidence else ""


def _row(label: str, cell: Cell) -> str:
    return (
        f"  {label[:22]:<24}{cell.total:>6}{cell.positive:>8}{cell.negative:>10}"
        f"{cell.opt_out:>9}{_pct(cell.review_coverage):>10}"
        f"{_pct(cell.override_rate):>10}{_confidence(cell)}"
    )


def _funnel_notes(analysis: Analysis) -> list[str]:
    """Short comparisons, so a number has something to be measured against.

    Deliberately relative. There is no industry benchmark here for what a
    healthy override rate looks like, so the only honest comparison is between
    the steps and jobs actually analysed -- and each line says what it compared.
    """
    notes: list[str] = []
    totals = [(cat, category_totals(analysis, cat)) for cat in analysis.categories]
    totals = [(cat, t) for cat, t in totals if t.total]
    if not totals:
        return notes

    judged = [(c, t) for c, t in totals if t.rejection_rate is not None]
    if judged:
        cat, cell = max(judged, key=lambda kv: kv[1].rejection_rate)
        notes.append(
            f"Most candidates are lost at {cat}: Paul rejected {cell.rejection_rate:.0%} "
            f"of the {cell.judged} he judged there."
        )

    covered = [(c, t) for c, t in totals if t.review_coverage is not None]
    if len(covered) > 1:
        low = min(covered, key=lambda kv: kv[1].review_coverage)
        high = max(covered, key=lambda kv: kv[1].review_coverage)
        if low[0] != high[0]:
            notes.append(
                f"Review coverage ranges from {low[1].review_coverage:.0%} at {low[0]} "
                f"to {high[1].review_coverage:.0%} at {high[0]}."
            )

    reversible = [(c, t) for c, t in totals if t.override_rate is not None]
    if reversible:
        cat, cell = max(reversible, key=lambda kv: kv[1].override_rate)
        if cell.override_rate:
            notes.append(
                f"Disagreement concentrates at {cat}: {cell.override_rate:.0%} of the "
                f"decisions reviewed there were reversed, out of "
                f"{sum(t.overrides for _, t in totals)} reversals in total."
            )
        else:
            notes.append("No reviewed decision was reversed at any step.")

    rates = {round(c.override_rate, 2) for c in analysis.cells if c.override_rate is not None}
    if len(rates) > 1:
        notes.append(
            "These rates differ by job, so the table above describes the mix of jobs "
            "analysed rather than the steps themselves. Act on the per-job table."
        )
    return notes


def render_text(analysis: Analysis, dataset: Dataset) -> str:
    out: list[str] = []
    add = out.append

    candidates = len({(r.job_id, r.person_slug) for r in dataset.records})
    total = sum(c.total for c in analysis.cells)
    judged = sum(c.judged for c in analysis.cells)
    positive = sum(c.positive for c in analysis.cells)
    negative = sum(c.negative for c in analysis.cells)
    opt_out = sum(c.opt_out for c in analysis.cells)
    reviewed = sum(c.reviewed for c in analysis.cells)
    overrides = sum(c.overrides for c in analysis.cells)

    add(RULE)
    add("SCREENING ACCURACY REPORT")
    add(f"Paul's screening decisions  ·  {datetime.now(timezone.utc).strftime('%d %B %Y, %H:%M UTC')}")
    add(RULE)
    add("")
    add(f"Analysed {len(analysis.jobs)} job(s), {candidates} candidate(s), {total} decision(s).")
    for job_id, job in sorted(analysis.jobs.items(), key=lambda kv: kv[1].title):
        cells = analysis.by_job(job_id)
        people = len({r.person_slug for r in dataset.records if r.job_id == job_id})
        add(f"  · {job.title}   [{job.external_id or job_id}]")
        add(f"      {people} candidate(s), {sum(c.total for c in cells)} decision(s), "
            f"{len(cells)} step(s) with a screening agent")

    # -- headline ----------------------------------------------------------
    add("")
    add("WHAT PAUL DECIDED")
    add(THIN)
    if judged:
        add(f"Paul judged {judged} candidate(s): {positive} passed, {negative} were rejected "
            f"({negative / judged:.0%} rejected).")
    if opt_out:
        add(f"Another {opt_out} candidate(s) are counted as opt-outs: they stopped responding "
            f"or declined to")
        add("continue, so Paul never judged them. They are excluded from the rejection rate above.")
    add("")
    if reviewed:
        add(f"A recruiter reviewed {reviewed} of {total} decision(s) ({reviewed / total:.0%}) "
            f"and reversed Paul on {overrides}.")
        add("")
        add("Reading the reversal rate: it covers only decisions a recruiter opened. Candidates")
        add("Paul rejects early are rarely opened, so mistakes there never show up in it. Read it")
        add("as how often recruiters disagree when they look, not as how often Paul is right.")
    else:
        add("No decision has been reviewed by a recruiter, so agreement cannot be measured.")

    # -- findings ----------------------------------------------------------
    add("")
    add("FINDINGS")
    add(THIN)
    add("Priority means how urgently this needs attention. It is not a score for Paul.")
    add("")
    if not analysis.anomalies:
        add("Nothing flagged. Every step is within the thresholds documented in TECH_NOTE.md.")
    for index, anomaly in enumerate(analysis.anomalies, start=1):
        thin = f"   (only {_plural(anomaly.n)} -- treat as a hint)" if anomaly.low_confidence else ""
        add(f"{index}. [{anomaly.severity.upper()} PRIORITY]  {anomaly.scope}")
        add(f"   What    {anomaly.headline}{thin}")
        add(f"   Why     {anomaly.detail}")
        add(f"   Action  {anomaly.action}")
        add("")

    # -- per job and step --------------------------------------------------
    add("EACH JOB, STEP BY STEP")
    add(THIN)
    add("The view to act on: every rate here belongs to one listing at one step.")
    add("'reviewed' is the share a recruiter opened; 'reversed' is the share of those they")
    add("disagreed with.")
    add("")
    add(COLUMNS)
    for job_id, job in sorted(analysis.jobs.items(), key=lambda kv: kv[1].title):
        cells = analysis.by_job(job_id)
        if not cells:
            continue
        add("")
        add(f"  {job.title}")
        for cell in cells:
            add(_row(cell.step_name, cell))
        add(_row("all steps", job_totals(analysis, job_id)))

    # -- per step across jobs ---------------------------------------------
    add("")
    add("THE FUNNEL: ALL JOBS COMBINED")
    add(THIN)
    add("Where candidates are lost, and how thin review coverage gets further down.")
    add("")
    add(COLUMNS)
    for category in analysis.categories:
        add(_row(category, category_totals(analysis, category)))
    notes = _funnel_notes(analysis)
    if notes:
        add("")
        for note in notes:
            add(f"  · {note}")

    # -- rejection reasons -------------------------------------------------
    add("")
    add("WHY PAUL REJECTED PEOPLE")
    add(THIN)
    from analyzer.reasons import BucketConfig, summarize  # local import: rendering only

    config = BucketConfig.load()
    for job_id, job in sorted(analysis.jobs.items(), key=lambda kv: kv[1].title):
        merged = job_totals(analysis, job_id)
        summary = summarize(merged.explanations, config)
        if not summary.total:
            continue
        add("")
        add(f"{job.title}  --  {summary.total} rejection(s)")
        if summary.vocabulary_is_fixed:
            add(f"  Paul reuses the same {summary.distinct_exact} phrases here, so these are exact")
            add("  counts of what he wrote.")
            for text, count in summary.exact.most_common(6):
                add(f"    {count:>3}  {text[:62]}")
        else:
            add(f"  Paul phrases these {summary.distinct_exact} different ways across "
                f"{summary.total} rejections, so they")
            add("  are grouped by topic, with one real example of each.")
            for bucket, count in summary.top(6):
                example = summary.examples.get(bucket, "")
                add(f"    {count:>3}  {bucket:<14}{count / summary.total:>5.0%}   "
                    f"e.g. {example[:40]!r}")
        if summary.other_share:
            add(f"  {summary.buckets.get('other', 0)} rejection(s) matched no topic "
                f"({summary.other_share:.0%}). Add their")
            add("  wording to reason_buckets.json so they stop landing in 'other'.")
        if summary.missing:
            add(f"  {summary.missing} rejection(s) carried no explanation at all.")

    # -- data quality ------------------------------------------------------
    add("")
    add("WHAT WAS SKIPPED OR LOOKED WRONG")
    add(THIN)
    quality = dataset.quality
    if not quality.total:
        add("Nothing. Every record fetched was usable.")
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
        add("")
        add(f"{len(items)} {label}:")
        for item in items[:5]:
            add(f"    · {item}")
        if len(items) > 5:
            add(f"    ... and {len(items) - 5} more")

    # -- limitations -------------------------------------------------------
    add("")
    add("WHAT THIS REPORT CANNOT TELL YOU")
    add(THIN)
    add("· Whether Paul is right. Only reviewed decisions can be checked, and recruiters")
    add("  mostly review candidates who got through. Rejections are the blind spot.")
    add(f"· Much from small numbers. Anything under {LOW_CONFIDENCE_N} decisions is marked. "
        f"Late steps are")
    add("  small by nature, so read those rows as direction rather than measurement.")
    add("· Whether a rejection reason is fair. The report groups reasons by topic and checks")
    add("  the topic against the step's criteria; it cannot judge the reasoning itself.")
    add("· How certain a reversal is. Ones inferred from a recruiter's own decision are")
    add("  weaker evidence than an explicit rejection of Paul's suggestion; the JSON")
    add("  output separates the two counts.")
    add("")
    add(RULE)
    return "\n".join(out)


def render_json(analysis: Analysis, dataset: Dataset) -> str:
    """Machine-readable form of the same numbers."""
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "api_requests": dataset.requests,
        "jobs": [
            {
                "job_id": job.job_id,
                "external_id": job.external_id,
                "title": job.title,
                "steps": [
                    {
                        "step_id": s.step_id, "name": s.name, "category": s.category,
                        "order": s.order, "produces_decisions": s.produces_decisions,
                        "has_agent": s.has_agent, "human_in_loop": s.human_in_loop,
                    }
                    for s in job.steps
                ],
            }
            for job in dataset.jobs
        ],
        "by_job_and_step": [
            {
                "job_id": c.job_id, "job_title": c.job_title,
                "step_id": c.step_id, "step_name": c.step_name, "step_category": c.step_category,
                "decisions": c.total, "passed": c.positive, "rejected": c.negative,
                "opt_out": c.opt_out, "unevaluated": c.unevaluated, "unmappable": c.unmappable,
                "rejection_rate": c.rejection_rate, "opt_out_rate": c.opt_out_rate,
                "reviewed": c.reviewed, "unreviewed": c.unreviewed,
                "review_coverage": c.review_coverage,
                "reversals": c.overrides, "reversals_explicit": c.explicit_overrides,
                "reversals_inferred": c.inferred_overrides, "reversal_rate": c.override_rate,
                "low_confidence": c.low_confidence,
                "step_requires_review": c.requires_review,
                "reason_topics": dict(c.reasons.buckets) if c.reasons else {},
                "reason_vocabulary_is_fixed": c.reasons.vocabulary_is_fixed if c.reasons else None,
                "reasons_citing_unstated_requirements": c.off_criteria,
            }
            for c in analysis.cells
        ],
        "findings": [
            {
                "priority": a.severity, "scope": a.scope, "what": a.headline,
                "why": a.detail, "action": a.action, "based_on_decisions": a.n,
                "low_confidence": a.low_confidence,
            }
            for a in analysis.anomalies
        ],
        "skipped_or_suspect": {
            "job_failures": dataset.quality.job_failures,
            "candidate_failures": dataset.quality.candidate_failures,
            "undocumented_decision_values": dataset.quality.malformed_decisions,
            "duplicate_step_records": dataset.quality.superseded_records,
            "steps_without_agent": dataset.quality.steps_without_agent,
            "skipped_records": dataset.quality.skipped_records,
        },
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def render_candidates(analysis: Analysis, dataset: Dataset, job_id: str | None = None) -> str:
    """Per-candidate view: plain text, no summarisation.

    The brief is explicit that this stays verbatim. When someone is checking
    whether one specific person was rejected fairly, a paraphrase is worse than
    useless.
    """
    out: list[str] = [RULE, "EVERY DECISION, CANDIDATE BY CANDIDATE", RULE]
    records = [r for r in dataset.records if not job_id or r.job_id == job_id]
    for record in sorted(records, key=lambda r: (r.job_id, r.person_name, r.assigned_at)):
        job = analysis.jobs.get(record.job_id)
        out.append("")
        out.append(f"{record.person_name}  --  {job.title if job else record.job_id}")
        out.append(f"  step         {record.step_name} ({record.step_category})")
        out.append(f"  Paul said    {record.raw_decision or '(nothing recorded)'}"
                   f"  ->  {record.outcome.value}")
        out.append(f"  because      {record.explanation or '(no explanation given)'}")
        out.append(f"  recruiter    {record.assigner_decision or '(did not review)'}"
                   f"  ->  {record.agreement.value} [{record.agreement_basis}]")
        out.append(f"  recorded     {record.assigned_at}")
    return "\n".join(out)
