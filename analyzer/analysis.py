"""Aggregation and anomaly detection. Pure functions over plain data.

Nothing here touches the network, so every number in the report can be
reproduced from a fixture.

The unit of analysis is the (job, step) CELL, with per-job and per-step
roll-ups computed from those cells rather than separately.

That ordering is deliberate. "Pre-screening rejects 40%" is an average over
listings with different criteria, and if one strict job supplies most of the
volume the step-level figure describes that job rather than the stage. The
actionable statement is "*this* job's pre-screening rejects 80% while the
others sit near 35%", which only exists if cells are computed first.

The per-step view still earns its place for things that are properties of the
channel rather than the listing: opt-out rate, review coverage, config drift.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from analyzer.fetch import Dataset, JobInfo, StepConfig
from analyzer.model import Agreement, Outcome
from analyzer.reasons import BucketConfig, ReasonSummary, cites_no_criterion, summarize

#: Below this many decisions a percentage is noise. Cells under it are still
#: reported -- the brief is explicit that small-n insights are not hidden --
#: but marked low-confidence with n visible.
LOW_CONFIDENCE_N = 10

#: Thresholds for anomalies. Named and stated so a reader can disagree with
#: them; every flag prints the number it fired on.
HIGH_OVERRIDE_RATE = 0.25
#: `always_on` means every agent action needs human approval, so the expected
#: coverage is 100%. Anything materially below that is a gap between how the
#: step is configured and how it is actually being used -- not a tuned
#: threshold, just an allowance for timing.
LOW_REVIEW_WHEN_REQUIRED = 0.90
HIGH_OPT_OUT_RATE = 0.25
LARGE_OTHER_SHARE = 0.30


@dataclass
class Cell:
    """Every decision at one step of one job."""

    job_id: str
    job_title: str
    step_id: str
    step_name: str
    step_category: str
    step: StepConfig | None = None
    outcomes: Counter = field(default_factory=Counter)
    agreements: Counter = field(default_factory=Counter)
    bases: Counter = field(default_factory=Counter)
    explanations: list[str] = field(default_factory=list)
    reasons: ReasonSummary | None = None
    off_criteria: list[str] = field(default_factory=list)

    # -- counts ------------------------------------------------------------
    @property
    def total(self) -> int:
        return sum(self.outcomes.values())

    @property
    def positive(self) -> int:
        return self.outcomes.get(Outcome.POSITIVE, 0)

    @property
    def negative(self) -> int:
        return self.outcomes.get(Outcome.NEGATIVE, 0)

    @property
    def opt_out(self) -> int:
        return self.outcomes.get(Outcome.OPT_OUT, 0)

    @property
    def unevaluated(self) -> int:
        return self.outcomes.get(Outcome.NONE, 0)

    @property
    def unmappable(self) -> int:
        return self.outcomes.get(Outcome.UNKNOWN, 0)

    @property
    def judged(self) -> int:
        """Decisions the AI actually made: positive or negative.

        Opt-outs are excluded on purpose. The candidate withdrew; the AI never
        judged them, and counting them as rejections inflates the rate.
        """
        return self.positive + self.negative

    @property
    def rejection_rate(self) -> float | None:
        return self.negative / self.judged if self.judged else None

    @property
    def opt_out_rate(self) -> float | None:
        return self.opt_out / self.total if self.total else None

    # -- human review ------------------------------------------------------
    @property
    def reviewed(self) -> int:
        return sum(
            self.agreements.get(a, 0)
            for a in (Agreement.AGREE, Agreement.OVERRIDE, Agreement.INDEPENDENT)
        )

    @property
    def unreviewed(self) -> int:
        return self.agreements.get(Agreement.UNREVIEWED, 0)

    @property
    def overrides(self) -> int:
        return self.agreements.get(Agreement.OVERRIDE, 0)

    @property
    def explicit_overrides(self) -> int:
        return self.bases.get("explicit_reject", 0)

    @property
    def inferred_overrides(self) -> int:
        return self.overrides - self.explicit_overrides

    @property
    def override_rate(self) -> float | None:
        """Share of REVIEWED decisions a human reversed.

        Denominator is reviewed, not total: this measures agreement on the
        slice a human actually looked at. It is not accuracy -- see the
        selection-bias note in the report.
        """
        comparable = self.agreements.get(Agreement.AGREE, 0) + self.overrides
        return self.overrides / comparable if comparable else None

    @property
    def review_coverage(self) -> float | None:
        return self.reviewed / self.total if self.total else None

    @property
    def low_confidence(self) -> bool:
        return self.total < LOW_CONFIDENCE_N

    @property
    def requires_review(self) -> bool:
        return bool(self.step and self.step.human_in_loop == "always_on")


@dataclass
class Anomaly:
    """A finding, with the number it fired on and what to do about it."""

    severity: str  # "high" | "medium" | "low"
    scope: str
    headline: str
    detail: str
    action: str
    n: int
    low_confidence: bool = False


@dataclass
class Analysis:
    cells: list[Cell] = field(default_factory=list)
    anomalies: list[Anomaly] = field(default_factory=list)
    jobs: dict[str, JobInfo] = field(default_factory=dict)

    def by_job(self, job_id: str) -> list[Cell]:
        return [c for c in self.cells if c.job_id == job_id]

    def by_category(self, category: str) -> list[Cell]:
        return [c for c in self.cells if c.step_category == category]

    @property
    def categories(self) -> list[str]:
        seen = {c.step_category for c in self.cells}
        return sorted(seen, key=lambda cat: min(
            (c.step.order if c.step else 10**6) for c in self.cells if c.step_category == cat
        ))


def _rollup(cells: list[Cell], **identity: str) -> Cell:
    """Sum cells into one, for per-job and per-step views."""
    merged = Cell(**identity)
    for cell in cells:
        merged.outcomes.update(cell.outcomes)
        merged.agreements.update(cell.agreements)
        merged.bases.update(cell.bases)
        merged.explanations.extend(cell.explanations)
        merged.off_criteria.extend(cell.off_criteria)
    return merged


def job_totals(analysis: Analysis, job_id: str) -> Cell:
    job = analysis.jobs.get(job_id)
    return _rollup(
        analysis.by_job(job_id),
        job_id=job_id,
        job_title=job.title if job else job_id,
        step_id="",
        step_name="all steps",
        step_category="",
    )


def category_totals(analysis: Analysis, category: str) -> Cell:
    return _rollup(
        analysis.by_category(category),
        job_id="",
        job_title="all jobs",
        step_id="",
        step_name=category,
        step_category=category,
    )


def analyse(dataset: Dataset, config: BucketConfig) -> Analysis:
    analysis = Analysis(jobs={j.job_id: j for j in dataset.jobs})

    grouped: dict[tuple[str, str], list] = defaultdict(list)
    for record in dataset.records:
        grouped[(record.job_id, record.step_id)].append(record)

    for (job_id, step_id), records in grouped.items():
        job = analysis.jobs.get(job_id)
        step = job.step(step_id) if job else None
        cell = Cell(
            job_id=job_id,
            job_title=job.title if job else job_id,
            step_id=step_id,
            step_name=records[0].step_name,
            step_category=records[0].step_category,
            step=step,
        )
        for record in records:
            cell.outcomes[record.outcome] += 1
            cell.agreements[record.agreement] += 1
            cell.bases[record.agreement_basis] += 1
            if record.outcome is Outcome.NEGATIVE:
                cell.explanations.append(record.explanation)
                criteria = (step.negative_criteria if step else "") or (job.requirements if job else "")
                if cites_no_criterion(record.explanation, criteria, config):
                    cell.off_criteria.append(record.explanation)
        cell.reasons = summarize(cell.explanations, config)
        analysis.cells.append(cell)

    analysis.cells.sort(key=lambda c: (c.job_title, c.step.order if c.step else 10**6))
    analysis.anomalies = detect_anomalies(analysis)
    return analysis


def detect_anomalies(analysis: Analysis) -> list[Anomaly]:
    found: list[Anomaly] = []

    for cell in analysis.cells:
        where = f"{cell.job_title} / {cell.step_name}"

        rate = cell.override_rate
        if rate is not None and rate >= HIGH_OVERRIDE_RATE:
            detail = (
                f"{cell.overrides} of {cell.agreements.get(Agreement.AGREE, 0) + cell.overrides} "
                f"reviewed decisions were reversed by a human ({rate:.0%})"
            )
            if cell.inferred_overrides:
                detail += (
                    f"; {cell.explicit_overrides} explicit, "
                    f"{cell.inferred_overrides} inferred from an independent decision"
                )
            found.append(Anomaly(
                severity="high", scope=where,
                headline=f"Humans reverse the AI on {rate:.0%} of reviewed decisions here",
                detail=detail,
                action="Review this step's conclusion criteria against the listing's mandatory requirements.",
                n=cell.total, low_confidence=cell.low_confidence,
            ))

        coverage = cell.review_coverage
        if cell.requires_review and coverage is not None and coverage < LOW_REVIEW_WHEN_REQUIRED:
            found.append(Anomaly(
                severity="high", scope=where,
                headline="Step requires human review, but most decisions went through unreviewed",
                detail=(
                    f"the agent is configured HumanInLoop=always_on, yet only "
                    f"{cell.reviewed} of {cell.total} decisions ({coverage:.0%}) were reviewed"
                ),
                action="Check that reviewers are being notified, or relax the step's HumanInLoop setting.",
                n=cell.total, low_confidence=cell.low_confidence,
            ))

        opt_out = cell.opt_out_rate
        if opt_out is not None and opt_out >= HIGH_OPT_OUT_RATE:
            found.append(Anomaly(
                severity="medium", scope=where,
                headline=f"{opt_out:.0%} of candidates dropped out at this step rather than being judged",
                detail=(
                    f"{cell.opt_out} of {cell.total} withdrew (no answer, declined to continue, "
                    f"or declined to talk to the AI). These are not rejections and are excluded "
                    f"from the rejection rate."
                ),
                action="A channel or format problem rather than a screening one: check messaging and timing.",
                n=cell.total, low_confidence=cell.low_confidence,
            ))

        if cell.off_criteria:
            examples = "; ".join(sorted(set(cell.off_criteria))[:2])
            found.append(Anomaly(
                severity="medium", scope=where,
                headline=f"{len(cell.off_criteria)} rejection(s) cite a requirement this job never states",
                detail=f"for example: {examples}",
                action="Either add the requirement to the listing, or correct the step's conclusion criteria.",
                n=cell.negative, low_confidence=cell.low_confidence,
            ))

        summary = cell.reasons
        if summary and summary.total >= 5 and summary.other_share >= LARGE_OTHER_SHARE:
            found.append(Anomaly(
                severity="low", scope=where,
                headline=f"{summary.other_share:.0%} of rejection reasons matched no known category",
                detail=f"{summary.buckets.get('other', 0)} of {summary.total} fell into 'other'",
                action="Add the missing wording to reason_buckets.json; this is a gap in the config, not the data.",
                n=summary.total, low_confidence=cell.low_confidence,
            ))

        if cell.unmappable:
            found.append(Anomaly(
                severity="low", scope=where,
                headline=f"{cell.unmappable} decision value(s) could not be interpreted",
                detail="present but outside the platform's documented decision vocabulary",
                action="See the data-quality section for the exact values and candidates.",
                n=cell.total, low_confidence=cell.low_confidence,
            ))

    order = {"high": 0, "medium": 1, "low": 2}
    found.sort(key=lambda a: (order.get(a.severity, 3), a.low_confidence, -a.n))
    return found
