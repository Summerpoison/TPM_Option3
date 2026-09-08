"""Rendering. Turns analysis results into something a colleague can act on.

Two rules from the brief drive everything here:

  * insights are sentences with numbers, not bare counts
  * every insight implies an action

So a finding reads "Humans reverse the AI on 40% of reviewed decisions at this
job's pre-screening (4 of 10) -- review the step's conclusion criteria against
the listing", not "override_rate: 0.4".

Percentages always appear next to the n they came from, and any cell below the
low-confidence threshold says so. A number without its denominator is how you
end up with a dashboard nobody trusts.
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
from analyzer.model import Agreement

RULE = "=" * 78
THIN = "-" * 78


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _confidence(cell: Cell) -> str:
    return f"  (low confidence, n={cell.total})" if cell.low_confidence else ""


def render_text(analysis: Analysis, dataset: Dataset) -> str:
    out: list[str] = []
    add = out.append

    add(RULE)
    add("SCREENING ACCURACY ANALYZER")
    add(f"generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        f"  |  {len(analysis.jobs)} job(s)  |  {len(dataset.records)} AI decisions"
        f"  |  {dataset.requests} API requests")
    add(RULE)

    # -- headline ----------------------------------------------------------
    total = sum(c.total for c in analysis.cells)
    judged = sum(c.judged for c in analysis.cells)
    negative = sum(c.negative for c in analysis.cells)
    opt_out = sum(c.opt_out for c in analysis.cells)
    reviewed = sum(c.reviewed for c in analysis.cells)
    overrides = sum(c.overrides for c in analysis.cells)

    add("")
    add("SUMMARY")
    add(THIN)
    if judged:
        add(f"The AI judged {judged} candidates and rejected {negative} of them "
            f"({negative / judged:.0%}).")
    if opt_out:
        add(f"A further {opt_out} candidate(s) withdrew before being judged. They are "
            f"counted separately, not as rejections.")
    if reviewed:
        add(f"A human reviewed {reviewed} of {total} decisions ({reviewed / total:.0%}) "
            f"and reversed the AI on {overrides} of them.")
    else:
        add(f"No decision has been reviewed by a human, so no agreement can be measured.")
    add("")
    add("Override rate measures agreement on the decisions a human actually looked at.")
    add("It is not accuracy: candidates rejected early are rarely reviewed, so genuine")
    add("false rejections are systematically under-counted. See LIMITATIONS.")

    # -- findings ----------------------------------------------------------
    add("")
    add("FINDINGS")
    add(THIN)
    if not analysis.anomalies:
        add("No anomalies detected. Every step is within the configured thresholds.")
    for anomaly in analysis.anomalies:
        add(f"[{anomaly.severity.upper():<6}] {anomaly.scope}")
        add(f"         {anomaly.headline}{'  (low confidence, n=%d)' % anomaly.n if anomaly.low_confidence else ''}")
        add(f"         Why: {anomaly.detail}")
        add(f"         Do:  {anomaly.action}")
        add("")

    # -- per job and step --------------------------------------------------
    add("BY JOB AND PIPELINE STEP")
    add(THIN)
    add(f"{'step':<26}{'n':>4}{'positive':>10}{'rejected':>10}{'opt-out':>9}"
        f"{'reviewed':>10}{'override':>10}")
    for job_id, job in sorted(analysis.jobs.items(), key=lambda kv: kv[1].title):
        cells = analysis.by_job(job_id)
        if not cells:
            continue
        add("")
        add(f"{job.title}  [{job.external_id or job_id}]")
        for cell in cells:
            add(f"  {cell.step_name[:23]:<24}{cell.total:>4}{cell.positive:>10}"
                f"{cell.negative:>10}{cell.opt_out:>9}"
                f"{_pct(cell.review_coverage):>10}{_pct(cell.override_rate):>10}"
                f"{_confidence(cell)}")
        totals = job_totals(analysis, job_id)
        add(f"  {'TOTAL':<24}{totals.total:>4}{totals.positive:>10}{totals.negative:>10}"
            f"{totals.opt_out:>9}{_pct(totals.review_coverage):>10}{_pct(totals.override_rate):>10}")

    # -- per step across jobs ---------------------------------------------
    add("")
    add("BY PIPELINE STEP, ACROSS JOBS")
    add(THIN)
    add("Channel-level view: opt-out and review coverage are properties of the step,")
    add("not of any one listing. Rejection rates are shown per job above, because")
    add("averaging them across different listings describes the mix, not the stage.")
    add("")
    for category in analysis.categories:
        totals = category_totals(analysis, category)
        add(f"  {category:<22}{totals.total:>4} decisions   "
            f"opt-out {_pct(totals.opt_out_rate):>4}   "
            f"reviewed {_pct(totals.review_coverage):>4}   "
            f"override {_pct(totals.override_rate):>4}{_confidence(totals)}")

    # -- rejection reasons -------------------------------------------------
    add("")
    add("MOST COMMON REJECTION REASONS")
    add(THIN)
    for job_id, job in sorted(analysis.jobs.items(), key=lambda kv: kv[1].title):
        merged = job_totals(analysis, job_id)
        cells = [c for c in analysis.by_job(job_id) if c.reasons]
        if not cells:
            continue
        from analyzer.reasons import BucketConfig, summarize  # local: rendering only
        summary = summarize(merged.explanations, BucketConfig.load())
        if not summary.total:
            continue
        add("")
        add(f"{job.title}  --  {summary.total} rejection(s)")
        if summary.vocabulary_is_fixed:
            add(f"  The agent uses a fixed vocabulary here ({summary.distinct_exact} distinct "
                f"phrases), so these are exact counts.")
            for text, count in summary.exact.most_common(6):
                add(f"    {count:>3}  {text[:64]}")
        else:
            add(f"  Explanations are free text ({summary.distinct_exact} distinct phrasings "
                f"across {summary.total} rejections), so they are grouped by keyword.")
            for bucket, count in summary.top(6):
                share = count / summary.total
                example = summary.examples.get(bucket, "")
                add(f"    {count:>3}  {bucket:<15} {share:>4.0%}   e.g. \"{example[:44]}\"")
        if summary.other_share:
            add(f"  {summary.buckets.get('other', 0)} reason(s) matched no category "
                f"({summary.other_share:.0%}). Add their wording to reason_buckets.json.")
        if summary.missing:
            add(f"  {summary.missing} rejection(s) carried no explanation at all.")

    # -- data quality ------------------------------------------------------
    add("")
    add("DATA QUALITY")
    add(THIN)
    quality = dataset.quality
    if not quality.total:
        add("Nothing was skipped. Every record fetched was usable.")
    for label, items in (
        ("jobs that failed to load", quality.job_failures),
        ("candidates that failed to load", quality.candidate_failures),
        ("decision values that could not be interpreted", quality.malformed_decisions),
        ("candidates with more than one record for a step", quality.superseded_records),
        ("decision steps with no agent configured", quality.steps_without_agent),
        ("records skipped as unusable", quality.skipped_records),
    ):
        if not items:
            continue
        add(f"{len(items)} {label}:")
        for item in items[:5]:
            add(f"    - {item}")
        if len(items) > 5:
            add(f"    ... and {len(items) - 5} more")

    # -- limitations -------------------------------------------------------
    add("")
    add("LIMITATIONS")
    add(THIN)
    add("* Selection bias. Humans review candidates who reach the review step. Anyone")
    add("  rejected earlier is rarely looked at, so the override rate understates false")
    add("  rejections. Read it as agreement on the reviewed slice, not as accuracy.")
    add(f"* Small cells. Anything below n={LOW_CONFIDENCE_N} is marked low confidence. Deep")
    add("  pipeline steps are small by nature -- that is what a funnel does.")
    add("* Reason matching is at concept level. A rejection citing a different specific")
    add("  requirement inside a category the job does use will not be flagged.")
    add("* Overrides inferred from an independent human decision are less certain than")
    add("  explicit rejections of the AI's suggestion; the counts are shown separately.")
    add("")
    add(RULE)
    return "\n".join(out)


def render_json(analysis: Analysis, dataset: Dataset) -> str:
    """Machine-readable form of the same numbers."""
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "requests": dataset.requests,
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
        "cells": [
            {
                "job_id": c.job_id, "job_title": c.job_title,
                "step_id": c.step_id, "step_name": c.step_name, "step_category": c.step_category,
                "n": c.total, "positive": c.positive, "negative": c.negative,
                "opt_out": c.opt_out, "unevaluated": c.unevaluated, "unmappable": c.unmappable,
                "rejection_rate": c.rejection_rate, "opt_out_rate": c.opt_out_rate,
                "reviewed": c.reviewed, "unreviewed": c.unreviewed,
                "review_coverage": c.review_coverage,
                "overrides": c.overrides, "explicit_overrides": c.explicit_overrides,
                "inferred_overrides": c.inferred_overrides, "override_rate": c.override_rate,
                "low_confidence": c.low_confidence,
                "requires_review": c.requires_review,
                "reason_buckets": dict(c.reasons.buckets) if c.reasons else {},
                "reason_vocabulary_is_fixed": c.reasons.vocabulary_is_fixed if c.reasons else None,
                "off_criteria_reasons": c.off_criteria,
            }
            for c in analysis.cells
        ],
        "anomalies": [
            {
                "severity": a.severity, "scope": a.scope, "headline": a.headline,
                "detail": a.detail, "action": a.action, "n": a.n,
                "low_confidence": a.low_confidence,
            }
            for a in analysis.anomalies
        ],
        "data_quality": {
            "job_failures": dataset.quality.job_failures,
            "candidate_failures": dataset.quality.candidate_failures,
            "malformed_decisions": dataset.quality.malformed_decisions,
            "superseded_records": dataset.quality.superseded_records,
            "steps_without_agent": dataset.quality.steps_without_agent,
            "skipped_records": dataset.quality.skipped_records,
        },
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def render_candidates(analysis: Analysis, dataset: Dataset, job_id: str | None = None) -> str:
    """Per-candidate view: plain text, no summarisation.

    The brief is explicit that this stays verbatim. When someone is checking
    whether a specific person was rejected fairly, a paraphrase is worse than
    useless.
    """
    out: list[str] = [RULE, "PER-CANDIDATE DECISIONS", RULE]
    records = [r for r in dataset.records if not job_id or r.job_id == job_id]
    for record in sorted(records, key=lambda r: (r.job_id, r.person_name, r.assigned_at)):
        job = analysis.jobs.get(record.job_id)
        out.append("")
        out.append(f"{record.person_name}  --  {job.title if job else record.job_id}")
        out.append(f"  step        {record.step_name} ({record.step_category})")
        out.append(f"  decision    {record.raw_decision or '(none recorded)'}  -> {record.outcome.value}")
        out.append(f"  explanation {record.explanation or '(none)'}")
        out.append(f"  human       {record.assigner_decision or '(not reviewed)'}"
                   f"  -> {record.agreement.value} [{record.agreement_basis}]")
        out.append(f"  assigned    {record.assigned_at}")
    return "\n".join(out)
