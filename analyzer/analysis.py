"""Aggregation and findings. Pure functions over plain data.

Nothing here touches the network, so every number in the report can be
reproduced from a fixture.

The unit of analysis is the (job, step) CELL, with per-job and per-step
roll-ups computed from those cells rather than separately.

That ordering is deliberate. "Pre-screening rejects 40%" is an average over
listings with different criteria, and if one strict job supplies most of the
volume the step-level figure describes that job rather than the stage. The
actionable statement is "*this* job's pre-screening rejects 80% while the
others sit near 35%", which only exists if cells are computed first.

Findings are built per cell too, one card per (job, step), and each card
carries the people it is about. A finding is a piece of work for the person
who configured the pipeline, not a statistic: "review these four candidates",
"compare this step's criteria with the listing", "check that recruiters see
this step". Priority comes from how many people are affected and how sure we
can be, not from which rule happened to fire.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from analyzer.fetch import Dataset, DecisionRecord, JobInfo, StepConfig
from analyzer.model import Agreement, Outcome
from analyzer.reasons import BucketConfig, ReasonSummary, cites_no_criterion, summarize

#: Below this many decisions a percentage is noise. Cells under it are still
#: reported -- the brief is explicit that small-n insights are not hidden --
#: but marked low-confidence with n visible, and no finding built on fewer
#: than this can be more than "worth watching".
LOW_CONFIDENCE_N = 10

#: A finding is "act now" only when at least this many people are affected.
#: Two people leaving a step is not a pattern, whatever the percentage says.
ACT_MIN_PEOPLE = 5

#: Rate thresholds. Named and stated so a reader can disagree with them;
#: every finding prints the numbers it fired on.
HIGH_OVERRIDE_RATE = 0.25
HIGH_OPT_OUT_RATE = 0.25
LARGE_OTHER_SHARE = 0.30

#: On an always_on step every decision should be approved. Below this share of
#: reviewed decisions the gap is no longer a few missed candidates; recruiters
#: are probably not seeing the step at all, which is a different problem with
#: a different fix.
NOTIFICATION_GAP = 0.50

PRIORITY_ORDER = {"act": 0, "check": 1, "watch": 2}
PRIORITY_LABEL = {"act": "act now", "check": "check", "watch": "watch"}


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
    records: list[DecisionRecord] = field(default_factory=list)

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
    def reviewable(self) -> int:
        """Decisions a recruiter could have acted on: positive or negative.

        An opt-out has nothing to approve, an unevaluated step has no decision
        yet, and an unrecognised value is not a decision we can vouch for (it
        is reported in data quality instead). None of them is a missed review.
        """
        return self.judged

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
    def approval_gap(self) -> int:
        """Reviewable decisions nobody reviewed."""
        return max(0, self.reviewable - self.reviewed)

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
    def comparable(self) -> int:
        """Reviews where the human had an AI opinion to agree or disagree with."""
        return self.agreements.get(Agreement.AGREE, 0) + self.overrides

    @property
    def override_rate(self) -> float | None:
        """Share of COMPARABLE reviews a human reversed.

        Denominator is reviews with an AI opinion to compare against, not all
        decisions: this measures agreement on the slice a human actually
        looked at. It is not accuracy -- see the selection-bias note.
        """
        return self.overrides / self.comparable if self.comparable else None

    @property
    def review_coverage(self) -> float | None:
        """Share of reviewable decisions a recruiter opened."""
        return min(1.0, self.reviewed / self.reviewable) if self.reviewable else None

    @property
    def low_confidence(self) -> bool:
        return self.total < LOW_CONFIDENCE_N

    @property
    def override_low_confidence(self) -> bool:
        """The reversal rate rests on `comparable`, which is smaller than `total`."""
        return self.comparable < LOW_CONFIDENCE_N

    @property
    def requires_review(self) -> bool:
        """Only `always_on` is treated as requiring review.

        `conditional` means review is required when a condition the agent
        evaluates holds, and that condition is not exposed in a way this tool
        can re-evaluate, so it is treated like `always_off`: no finding, rather
        than a finding built on a guess. Stated in TECH_NOTE.md.
        """
        return bool(self.step and self.step.human_in_loop == "always_on")

    # -- people ------------------------------------------------------------
    def people(self, predicate) -> list[str]:
        names = {r.person_name for r in self.records if predicate(r)}
        return sorted(names)


@dataclass
class Signal:
    """One observation on one cell, and the work it implies."""

    priority: str  # "act" | "check" | "watch"
    kind: str  # "review" | "listing" | "config" | "contact"
    headline: str
    evidence: str
    action: str
    people: list[str] = field(default_factory=list)
    n: int = 0  # the denominator the headline rests on


@dataclass
class Finding:
    """One card per (job, step): everything worth doing there."""

    job_id: str
    job_title: str
    step_id: str
    step_name: str
    signals: list[Signal] = field(default_factory=list)

    @property
    def scope(self) -> str:
        return f"{self.job_title} / {self.step_name}"

    @property
    def priority(self) -> str:
        return min((s.priority for s in self.signals), key=PRIORITY_ORDER.get, default="watch")

    @property
    def affected(self) -> int:
        return len({p for s in self.signals for p in s.people})


@dataclass
class Analysis:
    cells: list[Cell] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    watch: list[tuple[str, Signal]] = field(default_factory=list)
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
        merged.records.extend(cell.records)
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
            records=list(records),
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
    analysis.findings, analysis.watch = detect_findings(analysis, config)
    return analysis


# -- findings ----------------------------------------------------------------

def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _signals_for(cell: Cell, config: BucketConfig) -> list[Signal]:
    signals: list[Signal] = []

    # 1. An always_on step whose decisions were not all approved. Two
    #    different situations hide behind one number: a few slipped through
    #    (review them), or recruiters are not seeing the step at all (fix
    #    notification). Coverage decides which one this is.
    gap = cell.approval_gap
    if cell.requires_review and gap:
        coverage = cell.review_coverage or 0.0
        unreviewed = cell.people(
            lambda r: r.agreement is Agreement.UNREVIEWED
            and r.outcome in (Outcome.POSITIVE, Outcome.NEGATIVE)
        )
        headline = (
            f"{gap} of {cell.reviewable} decisions went through without the "
            f"recruiter approval this step requires"
        )
        if cell.reviewable < LOW_CONFIDENCE_N:
            priority = "watch"
        elif gap >= ACT_MIN_PEOPLE:
            priority = "act"
        else:
            priority = "check"
        if coverage < NOTIFICATION_GAP and gap >= ACT_MIN_PEOPLE:
            signals.append(Signal(
                priority, "config", headline,
                evidence=(
                    f"the step is configured HumanInLoop=always_on, but only {coverage:.0%} "
                    f"of its decisions were reviewed; at that level recruiters are probably not "
                    f"seeing this step at all"
                ),
                action=(
                    "Check that recruiters are notified for this step (or set it to always_off "
                    "if approval is not wanted), then review the candidates who went through"
                ),
                people=unreviewed, n=cell.reviewable,
            ))
        else:
            signals.append(Signal(
                priority, "review", headline,
                evidence=(
                    f"the step is configured HumanInLoop=always_on; recruiters did review the "
                    f"other {cell.reviewed}, so notification works and these are the ones missed"
                ),
                action="Review these candidates",
                people=unreviewed, n=cell.reviewable,
            ))

    # 2. Recruiters disagree with the agent often, where they look.
    rate = cell.override_rate
    if rate is not None and rate >= HIGH_OVERRIDE_RATE:
        if cell.overrides >= ACT_MIN_PEOPLE:
            priority = "act"
        elif cell.comparable >= LOW_CONFIDENCE_N:
            priority = "check"
        else:
            priority = "watch"
        evidence = f"{rate:.0%} of the decisions a recruiter compared against Paul's were reversed"
        if cell.inferred_overrides:
            evidence += (
                f" ({cell.explicit_overrides} explicit rejections of Paul's suggestion, "
                f"{cell.inferred_overrides} inferred from the recruiter's own decision)"
            )
        signals.append(Signal(
            priority, "listing",
            headline=(
                f"Recruiters reversed Paul on {cell.overrides} of the {cell.comparable} "
                f"decisions they reviewed"
            ),
            evidence=evidence,
            action=(
                "Compare this step's conclusion criteria with the listing's mandatory "
                "requirements; start with the reversed candidates"
            ),
            people=cell.people(lambda r: r.agreement is Agreement.OVERRIDE), n=cell.comparable,
        ))

    # 3. Rejections that cite something the listing never asks for. A fact
    #    about configuration rather than a rate, so it needs no minimum n --
    #    but a single one is as likely a bucket gap as a config gap.
    off = len(cell.off_criteria)
    if off:
        off_set = set(cell.off_criteria)
        topics = sorted({config.classify(e) for e in off_set})
        priority = "act" if off >= ACT_MIN_PEOPLE else "check" if off >= 2 else "watch"
        examples = "; ".join(sorted(off_set)[:3])
        signals.append(Signal(
            priority, "listing",
            headline=(
                f"{_plural(off, 'rejection')} cite {' / '.join(topics)}, which this listing "
                f"never asks for"
            ),
            evidence=f"for example: {examples}",
            action=(
                "Either add the requirement to the listing, or correct the step's conclusion "
                "criteria; then decide whether these candidates should be re-evaluated"
            ),
            people=cell.people(
                lambda r: r.outcome is Outcome.NEGATIVE and r.explanation in off_set
            ),
            n=cell.negative,
        ))

    # 4. Candidates leave before a decision. A channel problem, not a criteria
    #    problem, and never "act now": the fix is a conversation about contact
    #    timing, not a config change.
    opt_rate = cell.opt_out_rate
    if opt_rate is not None and opt_rate >= HIGH_OPT_OUT_RATE and cell.opt_out:
        enough = cell.opt_out >= ACT_MIN_PEOPLE and cell.total >= LOW_CONFIDENCE_N
        signals.append(Signal(
            "check" if enough else "watch", "contact",
            headline=(
                f"{cell.opt_out} of {cell.total} candidates dropped out here before Paul "
                f"could judge them"
            ),
            evidence=(
                "they stopped responding, declined to continue, or declined to speak to Paul; "
                "counted as opt-outs, separately from the rejection rate"
            ),
            action=(
                "Look at how and when candidates are contacted at this step, rather than "
                "at the criteria"
            ),
            people=[], n=cell.total,
        ))

    return signals


def detect_findings(
    analysis: Analysis, config: BucketConfig
) -> tuple[list[Finding], list[tuple[str, Signal]]]:
    """One card per cell for anything worth doing; one line per thing to watch."""
    findings: list[Finding] = []
    watch: list[tuple[str, Signal]] = []
    for cell in analysis.cells:
        card = Finding(cell.job_id, cell.job_title, cell.step_id, cell.step_name)
        for signal in _signals_for(cell, config):
            if signal.priority == "watch":
                watch.append((card.scope, signal))
            else:
                card.signals.append(signal)
        if card.signals:
            card.signals.sort(key=lambda s: (PRIORITY_ORDER[s.priority], -len(s.people)))
            findings.append(card)
    findings.sort(key=lambda f: (PRIORITY_ORDER[f.priority], -f.affected, f.scope))
    return findings, watch
