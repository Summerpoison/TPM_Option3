"""Fetch and flatten the platform's data into records the analysis can use.

This layer knows the recruiting endpoints; it does no analysis. Everything it
returns is a plain dataclass, so `analysis.py` can be tested against fixtures
with no network at all.

Shape of the fan-out:

    jobs  ->  per job: steps (+ agent config)
          ->  per job: applications (paged)
          ->  per application: step assignment history

The history call is one request per candidate, which is the cost driver. At a
client doing thousands of applications a month this is the thing to make
incremental (see README, "Next steps").

Two rules the API forces on us, both learned the hard way:

* A step CATEGORY is not unique within a pipeline -- this account's own
  template has two `TeamDiscussion` steps. Everything is therefore keyed on
  StepID, with category carried as an attribute.
* Assignments APPEND to history. Re-assigning a candidate to the same step
  leaves two records. We take the latest record that carries a decision, and
  report the superseded ones as a data-quality note rather than counting them
  twice.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterator

from analyzer.client import ApiError, PaulsjobClient
from analyzer.model import Agreement, Outcome, derive_agreement, normalize_outcome

log = logging.getLogger(__name__)

#: Categories that can hold a decision-making agent. Everything else is a
#: state or a destination. Confirmed against GET /recruiting/job-step-categories
#: where these are exactly the categories with AllowChangeConfig true and
#: conclusion rules; New/TeamDiscussion/ContractOffer/Onboarding have neither.
DECISION_CATEGORIES = frozenset({
    "PreScreening", "AIVoiceInterview", "HumanInterview",
    "RecruitingDay", "CodeExecution", "DataCollection",
})
#: Terminal destinations. Not analysed as decisions, but used to check that
#: negatively-decided candidates actually land somewhere sensible.
TERMINAL_CATEGORIES = frozenset({"Rejected", "Outreach"})


@dataclass(frozen=True)
class StepConfig:
    """A step in one job's pipeline, plus what its agent is configured to do."""

    step_id: str
    name: str
    category: str
    order: int
    has_agent: bool = False
    agent_active: bool = False
    #: 'always_on' | 'always_off' | 'conditional' | None
    human_in_loop: str | None = None
    #: Conclusion criteria, from NextStepRules[].TargetAudience.FilterText.
    positive_criteria: str = ""
    negative_criteria: str = ""
    #: NextStepRule name -> target step id, from Actions.StatusChange.
    routing: dict[str, str] = field(default_factory=dict)

    @property
    def produces_decisions(self) -> bool:
        return self.category in DECISION_CATEGORIES

    @property
    def is_terminal(self) -> bool:
        return self.category in TERMINAL_CATEGORIES


@dataclass(frozen=True)
class JobInfo:
    job_id: str
    title: str
    external_id: str
    requirements: str = ""
    steps: tuple[StepConfig, ...] = ()

    def step(self, step_id: str) -> StepConfig | None:
        return next((s for s in self.steps if s.step_id == step_id), None)


@dataclass(frozen=True)
class DecisionRecord:
    """One AI decision on one candidate at one step, already normalised."""

    job_id: str
    step_id: str
    step_name: str
    step_category: str
    person_slug: str
    person_name: str
    outcome: Outcome
    raw_decision: str | None
    explanation: str
    agreement: Agreement
    agreement_basis: str
    assigner_decision: str | None
    assigned_at: str


@dataclass
class DataQuality:
    """Everything skipped, superseded or unmappable. Surfaced in the report.

    The brief asks for this and it is not decoration: a null decision that means
    "no agent configured here" and one that means "not evaluated yet" are
    different facts, and collapsing them is how a dashboard ends up claiming
    two contradictory things at once.
    """

    job_failures: list[str] = field(default_factory=list)
    candidate_failures: list[str] = field(default_factory=list)
    malformed_decisions: list[str] = field(default_factory=list)
    superseded_records: list[str] = field(default_factory=list)
    steps_without_agent: list[str] = field(default_factory=list)
    skipped_records: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(
            len(v) for v in (
                self.job_failures, self.candidate_failures, self.malformed_decisions,
                self.superseded_records, self.steps_without_agent, self.skipped_records,
            )
        )


@dataclass
class Dataset:
    jobs: list[JobInfo] = field(default_factory=list)
    records: list[DecisionRecord] = field(default_factory=list)
    quality: DataQuality = field(default_factory=DataQuality)
    requests: int = 0


def _as_list(payload: object, *keys: str) -> list[dict]:
    """Pull a list out of a response whose shape we do not fully trust."""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def _category_id(step: dict) -> str:
    category = step.get("Category") or step.get("CategoryID") or ""
    if isinstance(category, dict):
        category = category.get("ID") or ""
    return str(category)


class Fetcher:
    def __init__(self, client: PaulsjobClient) -> None:
        self.client = client
        self.quality = DataQuality()

    # -- jobs and their configuration --------------------------------------
    def jobs(self, external_id_prefix: str | None = None) -> list[JobInfo]:
        found: list[JobInfo] = []
        for raw in self.client.paginate_pages("/recruiting/jobs/search-jobs", "Jobs"):
            job_id = raw.get("PaulsjobJobID") or raw.get("ID")
            if not job_id:
                self.quality.skipped_records.append(f"job with no id: {sorted(raw)[:6]}")
                continue
            external = str(raw.get("JobExternalID") or "")
            if external_id_prefix and not external.startswith(external_id_prefix):
                continue
            try:
                steps = self._steps(str(job_id))
            except ApiError as exc:
                # Per-job isolation: one broken job must not kill the run.
                self.quality.job_failures.append(f"job {job_id}: could not read steps -- {exc}")
                steps = ()
            found.append(
                JobInfo(
                    job_id=str(job_id),
                    title=str(raw.get("JobPositionTitle") or "(untitled)"),
                    external_id=external,
                    requirements=self._requirements(raw),
                    steps=steps,
                )
            )
        return found

    @staticmethod
    def _requirements(raw: dict) -> str:
        description = raw.get("JobPositionDescription")
        if isinstance(description, dict):
            return " ".join(
                str(description.get(k) or "")
                for k in ("JobRequirements", "IdealCandidateProfile")
            ).strip()
        return str(description or "")

    def _steps(self, job_id: str) -> tuple[StepConfig, ...]:
        raw_steps = _as_list(self.client.get(f"/recruiting/jobs/{job_id}/steps"), "Steps", "JobSteps")
        configs: list[StepConfig] = []
        for step in raw_steps:
            step_id = step.get("ID")
            if not step_id:
                continue
            category = _category_id(step)
            order = step.get("OrderIndex")
            agent = self._agent(job_id, str(step_id)) if category in DECISION_CATEGORIES else None
            if category in DECISION_CATEGORIES and agent is None:
                self.quality.steps_without_agent.append(
                    f"job {job_id} step '{step.get('Name')}' ({category}) has no agent configured"
                )
            configs.append(
                StepConfig(
                    step_id=str(step_id),
                    name=str(step.get("Name") or category or "(unnamed)"),
                    category=category,
                    order=order if isinstance(order, int) else 10**6,
                    has_agent=agent is not None,
                    agent_active=bool((agent or {}).get("IsActive")),
                    human_in_loop=((agent or {}).get("HumanInLoop") or {}).get("Setting"),
                    positive_criteria=self._criteria(agent, "POSITIVE_CONCLUSION"),
                    negative_criteria=self._criteria(agent, "NEGATIVE_CONCLUSION"),
                    routing=self._routing(agent),
                )
            )
        return tuple(sorted(configs, key=lambda s: s.order))

    def _agent(self, job_id: str, step_id: str) -> dict | None:
        try:
            payload = self.client.get(f"/recruiting/jobs/{job_id}/steps/{step_id}/agents")
        except ApiError as exc:
            self.quality.job_failures.append(f"job {job_id} step {step_id}: agents unreadable -- {exc}")
            return None
        # Job steps return `Agents`; template steps return `JobStepAgentTemplates`.
        agents = _as_list(payload, "Agents", "JobStepAgentTemplates", "StepAgents")
        return agents[0] if agents else None

    @staticmethod
    def _criteria(agent: dict | None, rule_name: str) -> str:
        """Conclusion criteria live in the next-step rule's target audience."""
        for rule in (agent or {}).get("NextStepRules") or []:
            if isinstance(rule, dict) and rule.get("Name") == rule_name:
                audience = rule.get("TargetAudience") or {}
                return str(audience.get("FilterText") or "").strip()
        return ""

    @staticmethod
    def _routing(agent: dict | None) -> dict[str, str]:
        routes: dict[str, str] = {}
        for rule in (agent or {}).get("NextStepRules") or []:
            if not isinstance(rule, dict):
                continue
            target = (rule.get("Actions") or {}).get("StatusChange")
            if rule.get("Name") and target:
                routes[str(rule["Name"])] = str(target)
        return routes

    # -- candidates and their decisions ------------------------------------
    def applications(self, job_id: str) -> Iterator[dict]:
        # The filter key is `paulsjob_job_id`, it only accepts the `in`
        # operator, and the value must be an integer -- `eq` is rejected and a
        # string id fails validation.
        try:
            value: object = int(job_id)
        except (TypeError, ValueError):
            value = job_id
        payload = {"Must": [{"Key": "paulsjob_job_id", "Operator": "in", "Value": [value]}]}
        try:
            yield from self.client.paginate_pages(
                "/recruiting/applications/search-applications", "JobApplications", payload=payload
            )
        except ApiError as exc:
            self.quality.job_failures.append(f"job {job_id}: applications unreadable -- {exc}")

    def records_for_job(self, job: JobInfo) -> list[DecisionRecord]:
        """One request per candidate. Failures are per-candidate, not fatal."""
        collected: list[DecisionRecord] = []
        for app in self.applications(job.job_id):
            person = app.get("Person") or {}
            slug = person.get("Slug") or person.get("PersonSlug") or person.get("user_slug")
            if not slug:
                self.quality.skipped_records.append(f"job {job.job_id}: application with no person slug")
                continue
            name = f"{person.get('FirstName') or ''} {person.get('LastName') or ''}".strip() or str(slug)
            try:
                history = list(
                    self.client.paginate_cursor(
                        f"/recruiting/{slug}/jobs/{job.job_id}/steps-assignment-history", "History"
                    )
                )
            except ApiError as exc:
                self.quality.candidate_failures.append(f"{name} on job {job.job_id}: {exc}")
                continue
            collected.extend(self._records_from_history(job, str(slug), name, history))
        return collected

    def _records_from_history(
        self, job: JobInfo, slug: str, name: str, history: list[dict]
    ) -> list[DecisionRecord]:
        """Collapse a candidate's history into at most one record per step.

        Assignments append, so the same step can appear repeatedly. We keep the
        latest entry that actually carries a decision; if none does, we keep the
        latest entry so the step still shows up as unevaluated.
        """
        by_step: dict[str, list[dict]] = defaultdict(list)
        for entry in history:
            step_id = entry.get("StepID")
            if step_id:
                by_step[str(step_id)].append(entry)

        records: list[DecisionRecord] = []
        for step_id, entries in by_step.items():
            entries.sort(key=lambda e: str(e.get("AssignedAt") or ""))
            decided = [e for e in entries if (e.get("PaulDecision") or "").strip()]
            chosen = (decided or entries)[-1]
            if len(entries) > 1:
                self.quality.superseded_records.append(
                    f"{name} on job {job.job_id} step {step_id}: "
                    f"{len(entries)} assignment records, using the latest with a decision"
                )

            step = job.step(step_id)
            category = str(chosen.get("StepCategory") or (step.category if step else ""))
            if step and not step.produces_decisions:
                continue  # terminal or state step: not an AI decision
            if not step and category not in DECISION_CATEGORIES:
                continue

            raw = chosen.get("PaulDecision")
            outcome = normalize_outcome(raw)
            if outcome is Outcome.UNKNOWN:
                self.quality.malformed_decisions.append(
                    f"{name} on job {job.job_id} step "
                    f"'{chosen.get('StepName') or category}': unmappable PaulDecision {raw!r}"
                )
            result = derive_agreement(
                chosen.get("AssignerDecision"),
                chosen.get("PaulDecisionSuggestion"),
                raw,
            )
            records.append(
                DecisionRecord(
                    job_id=job.job_id,
                    step_id=step_id,
                    step_name=str(chosen.get("StepName") or (step.name if step else category)),
                    step_category=category,
                    person_slug=slug,
                    person_name=name,
                    outcome=outcome,
                    raw_decision=str(raw) if raw is not None else None,
                    explanation=str(chosen.get("PaulDecisionExplanation") or "").strip(),
                    agreement=result.agreement,
                    agreement_basis=result.basis,
                    assigner_decision=chosen.get("AssignerDecision"),
                    assigned_at=str(chosen.get("AssignedAt") or ""),
                )
            )
        return records


def load(client: PaulsjobClient, *, external_id_prefix: str | None = None) -> Dataset:
    """Fetch everything the analysis needs, isolating failures per job."""
    fetcher = Fetcher(client)
    dataset = Dataset(quality=fetcher.quality)
    dataset.jobs = fetcher.jobs(external_id_prefix)
    for job in dataset.jobs:
        try:
            dataset.records.extend(fetcher.records_for_job(job))
        except ApiError as exc:
            fetcher.quality.job_failures.append(f"job {job.job_id}: aborted -- {exc}")
        except Exception as exc:  # noqa: BLE001 - one bad job must not kill the run
            fetcher.quality.job_failures.append(f"job {job.job_id}: unexpected {type(exc).__name__}: {exc}")
    dataset.requests = client.stats.requests
    return dataset
