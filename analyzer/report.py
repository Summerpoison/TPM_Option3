"""Rendering. Turns analysis results into something a colleague can act on.

The report is a to-do list with the evidence attached, in that order:

  1. What to do          -- one card per (job, step), naming the candidates
  2. Worth watching      -- too few decisions to act on; one line each
  3. The numbers         -- per job and step, the funnel, rejection reasons
  4. Skipped or suspect  -- everything the fetch could not use
  5. What it cannot tell you

Two rules from the brief drive the wording: insights are sentences with the
numbers they rest on, and every insight implies an action. Percentages always
appear next to the n they came from; anything under the low-confidence
threshold says so, keyed on the denominator of that rate, not the row total.

The agent is called Paul throughout, because that is what the people reading
this call it.
"""
from __future__ import annotations

import json
import textwrap
from datetime import datetime, timezone

from analyzer.analysis import (
    LOW_CONFIDENCE_N,
    PRIORITY_LABEL,
    Analysis,
    Cell,
    Finding,
    Signal,
    category_totals,
    job_totals,
)
from analyzer.fetch import Dataset

RULE = "=" * 78
THIN = "-" * 78
COLUMNS = (
    f"  {'step':<24}{'total':>6}{'passed':>7}{'rejected':>9}"
    f"{'opt-out':>8}{'none':>6}{'reviewed':>9}{'reversed':>9}"
)

#: Column key. Counts only, so every row adds up and no number appears that
#: is not in the row: total = passed + rejected + opt-out + none.
COLUMN_KEY = (
    "  none      no usable decision: the step was not evaluated, or the value recorded",
    "            is not one the platform documents (listed under 'skipped')",
    "  reviewed  decisions a recruiter opened, out of passed + rejected",
    "  reversed  reviewed decisions the recruiter disagreed with. Only opened decisions",
    "            count, and early rejections are rarely opened, so this says how often",
    "            recruiters disagree when they look, not how often Paul is right",
)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _plural(count: int, word: str = "decision") -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _row(label: str, cell: Cell) -> str:
    return (
        f"  {label[:22]:<24}{cell.total:>6}{cell.positive:>7}{cell.negative:>9}"
        f"{cell.opt_out:>8}{cell.unevaluated + cell.unmappable:>6}"
        f"{cell.reviewed:>9}{cell.overrides:>9}"
    )


def _wrap(text: str, indent: str, width: int = 78) -> list[str]:
    return textwrap.wrap(text, width=width, initial_indent=indent, subsequent_indent=indent)


def funnel_notes(analysis: Analysis) -> list[str]:
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

    # Where the most people leave, counted, not the highest rate: a 69% rate
    # on 13 people loses fewer candidates than a 48% rate on 40.
    cat, cell = max(totals, key=lambda kv: kv[1].negative + kv[1].opt_out)
    lost = cell.negative + cell.opt_out
    if lost:
        notes.append(
            f"{cat} is where the most candidates leave: {cell.negative} rejected and "
            f"{cell.opt_out} opted out, of the {cell.total} who reached it."
        )
    judged = [(c, t) for c, t in totals if t.rejection_rate is not None and t.judged >= LOW_CONFIDENCE_N]
    if len(judged) > 1:
        cat, cell = max(judged, key=lambda kv: kv[1].rejection_rate)
        notes.append(
            f"The strictest step is {cat}: Paul rejected {cell.rejection_rate:.0%} of the "
            f"{cell.judged} he judged there."
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

    reversals = sum(t.overrides for _, t in totals)
    if reversals:
        cat, cell = max(totals, key=lambda kv: kv[1].overrides)
        notes.append(
            f"{cell.overrides} of the {reversals} reversals in total happened at {cat}."
        )

    rates = {round(c.override_rate, 2) for c in analysis.cells if c.override_rate is not None}
    if len(rates) > 1:
        notes.append(
            "These rates differ by job, so this table describes the mix of jobs analysed "
            "rather than the steps themselves. Act on the per-job table."
        )
    return notes


#: The single caveat worth closing on. Everything else the report cannot do is
#: either visible in the tables (small numbers) or documented in TECH_NOTE.md.
LIMITATION = (
    "This report shows what Paul decided and where recruiters disagreed with him. It "
    "cannot say whether a decision was right: only decisions a recruiter opened can be "
    "checked, and those are mostly the candidates who got through. Rejection reasons are "
    "grouped and compared with what the listing asks for, not with what the candidate "
    "actually offered."
)


def _render_signal(signal: Signal, out: list[str]) -> None:
    out.extend(_wrap(f"· {signal.headline}", "   "))
    out.extend(_wrap(f"why: {signal.evidence}", "       "))
    action = signal.action
    if signal.people:
        action += ": " + ", ".join(signal.people)
    out.extend(_wrap(f"-> {action}", "       "))


def render_text(analysis: Analysis, dataset: Dataset) -> str:
    out: list[str] = []
    add = out.append

    candidates = len({(r.job_id, r.person_slug) for r in dataset.records})
    total = sum(c.total for c in analysis.cells)

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

    # -- what to do --------------------------------------------------------
    add("")
    add("WHAT TO DO")
    add(THIN)
    add("One card per job and step. 'Act now' means at least five people are affected;")
    add("'check' means the pattern is real but small. Names are the candidates concerned.")
    add("")
    if not analysis.findings:
        add("Nothing. Every step is within the thresholds documented in TECH_NOTE.md.")
    for finding in analysis.findings:
        add(f"[{PRIORITY_LABEL[finding.priority].upper()}]  {finding.scope}")
        for signal in finding.signals:
            _render_signal(signal, out)
        add("")

    # -- watch list --------------------------------------------------------
    if analysis.watch:
        add("WORTH WATCHING")
        add(THIN)
        add(f"Too few decisions to act on yet (under {LOW_CONFIDENCE_N}, or fewer than five people).")
        add("Listed so they are not lost; revisit when there is more data.")
        add("")
        for scope, signal in analysis.watch:
            out.extend(_wrap(f"· {scope}: {signal.headline}.", "  "))
        add("")

    # -- the numbers -------------------------------------------------------
    add("THE NUMBERS")
    add(THIN)
    add("Counts, per listing and step; every row adds up to its total.")
    add("")
    add("Each job, step by step")
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
    add("")
    out.extend(COLUMN_KEY)

    add("")
    add("The funnel, all jobs combined")
    add("")
    add(COLUMNS)
    for category in analysis.categories:
        add(_row(category, category_totals(analysis, category)))
    notes = funnel_notes(analysis)
    if notes:
        add("")
        for note in notes:
            out.extend(_wrap(f"· {note}", "  "))

    # -- rejection reasons -------------------------------------------------
    add("")
    add("Why Paul rejected people, per job and step")
    from analyzer.reasons import BucketConfig, summarize  # local import: rendering only

    config = BucketConfig.load()
    for job_id, job in sorted(analysis.jobs.items(), key=lambda kv: kv[1].title):
        for cell in analysis.by_job(job_id):
            summary = summarize(cell.explanations, config)
            if not summary.total:
                continue
            add("")
            add(f"{job.title} / {cell.step_name}  --  {_plural(summary.total, 'rejection')}")
            if summary.vocabulary_is_fixed:
                add(f"  Paul reuses the same {_plural(summary.distinct_exact, 'phrase')} here, "
                    f"so these are exact")
                add("  counts of what he wrote.")
                for text, count in summary.exact.most_common(6):
                    add(f"    {count:>3}  {text[:64]}")
            else:
                add(f"  Paul phrases these {summary.distinct_exact} different ways across "
                    f"{summary.total} rejections, so they")
                add("  are grouped by topic, with one real example of each.")
                for bucket, count in summary.top(6):
                    example = summary.examples.get(bucket, "")
                    add(f"    {count:>3}  {bucket:<14}{count / summary.total:>5.0%}   "
                        f"e.g. {example[:60]}")
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
    add("ONE THING TO KEEP IN MIND")
    add(THIN)
    out.extend(_wrap(LIMITATION, ""))
    add("")
    add(RULE)
    return "\n".join(out)


def _finding_json(finding: Finding) -> dict:
    return {
        "priority": finding.priority,
        "job_id": finding.job_id, "job_title": finding.job_title,
        "step_id": finding.step_id, "step_name": finding.step_name,
        "people_affected": finding.affected,
        "items": [
            {
                "priority": s.priority, "kind": s.kind, "what": s.headline,
                "why": s.evidence, "action": s.action, "candidates": s.people,
                "based_on": s.n,
            }
            for s in finding.signals
        ],
    }


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
        "what_to_do": [_finding_json(f) for f in analysis.findings],
        "worth_watching": [
            {"scope": scope, "kind": s.kind, "what": s.headline, "why": s.evidence, "based_on": s.n}
            for scope, s in analysis.watch
        ],
        "by_job_and_step": [
            {
                "job_id": c.job_id, "job_title": c.job_title,
                "step_id": c.step_id, "step_name": c.step_name, "step_category": c.step_category,
                "decisions": c.total, "passed": c.positive, "rejected": c.negative,
                "opt_out": c.opt_out, "unevaluated": c.unevaluated, "unmappable": c.unmappable,
                "rejection_rate": c.rejection_rate, "opt_out_rate": c.opt_out_rate,
                "reviewable": c.reviewable, "reviewed": c.reviewed, "unreviewed": c.unreviewed,
                "review_coverage": c.review_coverage, "approval_gap": c.approval_gap,
                "reversals": c.overrides, "reversals_explicit": c.explicit_overrides,
                "reversals_inferred": c.inferred_overrides,
                "reversal_rate": c.override_rate, "reversal_rate_based_on": c.comparable,
                "low_confidence": c.low_confidence,
                "step_requires_review": c.requires_review,
                "reason_topics": dict(c.reasons.buckets) if c.reasons else {},
                "reason_vocabulary_is_fixed": c.reasons.vocabulary_is_fixed if c.reasons else None,
                "reasons_citing_unstated_requirements": c.off_criteria,
            }
            for c in analysis.cells
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
