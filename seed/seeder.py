"""Create the seed data described in scenarios.py.

Writes to a shared dev environment, so:
  * everything it creates is tagged with a `saa-seed-` external id
  * it is idempotent -- an existing job is reused, not duplicated
  * --dry-run prints the plan without issuing a single write
  * it never deletes anything

Deterministic: a fixed RNG seed means re-running produces the same population,
so the manifest stays meaningful across runs.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
from dataclasses import dataclass, field, replace
from typing import Any

from analyzer.client import ApiError, HttpError, PaulsjobClient
from analyzer.config import Settings
from seed.scenarios import SCENARIOS, JobScenario, ReasonSpec

log = logging.getLogger("seeder")

RNG_SEED = 20260907
MANIFEST_PATH = "fixtures/seed_manifest.json"

#: Written verbatim into PaulDecision. Matches the platform's taxonomy.
POSITIVE = "PositiveDecision"
NEGATIVE = "NegativeDecision"
OPT_OUTS = ("OptOutNoAnswer", "OptOutDeclineToTalkWithAI", "OptOutDeclineToContinueApplication")
#: Values that should NOT normalise, to give the data-quality section content.
MALFORMED = ("Escalated", "PendingReview", "", "Unklar")


@dataclass
class PlannedAssignment:
    step_category: str
    paul_decision: str
    paul_decision_explanation: str
    reason_bucket: str | None
    reason_in_criteria: bool
    assigner_decision: str | None
    paul_decision_suggestion: str | None


@dataclass
class PlannedCandidate:
    first_name: str
    last_name: str
    email: str
    assignments: list[PlannedAssignment] = field(default_factory=list)
    person_slug: str | None = None


@dataclass
class PlannedJob:
    external_id: str
    title: str
    demonstrates: str
    criteria: list[str]
    candidates: list[PlannedCandidate] = field(default_factory=list)
    paulsjob_job_id: str | None = None
    steps: dict[str, str] = field(default_factory=dict)  # category -> step id


FIRST_NAMES = [
    "Anna", "Mehmet", "Julia", "Kwame", "Sofia", "Lukas", "Fatima", "Jonas",
    "Elena", "Ahmed", "Marie", "Tobias", "Aisha", "David", "Nina", "Piotr",
    "Leyla", "Stefan", "Chiara", "Omar", "Hannah", "Viktor", "Amara", "Felix",
]
LAST_NAMES = [
    "Schmidt", "Yilmaz", "Weber", "Mensah", "Rossi", "Becker", "Haddad", "Fischer",
    "Petrova", "Nowak", "Keller", "Okafor", "Braun", "Kaya", "Lange", "Silva",
]


class Seeder:
    def __init__(self, client: PaulsjobClient, *, dry_run: bool = False) -> None:
        self.client = client
        self.dry_run = dry_run
        self.rng = random.Random(RNG_SEED)
        self.errors: list[str] = []
        self._cached_owner: str | None = None
        self._cached_template: str | None = None
        self.skipped = 0
        self.muted = 0

    # -- planning (no network) --------------------------------------------
    def plan(self, scenarios: list[JobScenario]) -> list[PlannedJob]:
        """Decide the whole population up front, deterministically.

        Planning before writing means --dry-run shows exactly what a real run
        would do, and the manifest can be produced even if writes fail later.
        """
        return [self._plan_job(s) for s in scenarios]

    def _plan_job(self, scenario: JobScenario) -> PlannedJob:
        job = PlannedJob(
            external_id=scenario.external_id,
            title=scenario.title,
            demonstrates=scenario.demonstrates,
            criteria=list(scenario.criteria),
        )
        malformed_left = scenario.malformed_decisions

        for index in range(scenario.candidates):
            first = self.rng.choice(FIRST_NAMES)
            last = self.rng.choice(LAST_NAMES)
            candidate = PlannedCandidate(
                first_name=first,
                last_name=last,
                # Unique, obviously synthetic, and routed to a domain that
                # cannot receive mail. No real person's address is ever used.
                email=f"{scenario.external_id}-{index:03d}@seed.invalid",
            )

            still_in_funnel = True
            for category in ("PreScreening", "AIVoiceInterview", "HumanInterview"):
                if not still_in_funnel or category not in scenario.pass_rates:
                    break
                assignment, still_in_funnel = self._plan_assignment(scenario, category, malformed_left)
                if assignment.paul_decision in MALFORMED:
                    malformed_left -= 1
                candidate.assignments.append(assignment)

            job.candidates.append(candidate)
        return job

    def _plan_assignment(
        self, scenario: JobScenario, category: str, malformed_left: int
    ) -> tuple[PlannedAssignment, bool]:
        opt_out_rate = scenario.opt_out_rates.get(category, 0.0)
        pass_rate = scenario.pass_rates.get(category, 0.5)

        reason: ReasonSpec | None = None
        if malformed_left > 0 and self.rng.random() < 0.05:
            decision = self.rng.choice(MALFORMED)
            explanation = "Decision recorded with a non-standard status value."
            advanced = False
        elif self.rng.random() < opt_out_rate:
            decision = self.rng.choice(OPT_OUTS)
            explanation = "Candidate did not continue the process."
            advanced = False
        elif self.rng.random() < pass_rate:
            decision = POSITIVE
            explanation = "Meets the mandatory requirements for this role; proceed to the next step."
            advanced = True
        else:
            decision = NEGATIVE
            reason = self.rng.choice(scenario.reasons)
            explanation = reason.text
            advanced = False

        assigner_decision, suggestion = self._plan_review(scenario, category, decision)

        return (
            PlannedAssignment(
                step_category=category,
                paul_decision=decision,
                paul_decision_explanation=explanation,
                reason_bucket=reason.bucket if reason else None,
                reason_in_criteria=reason.in_job_criteria if reason else True,
                assigner_decision=assigner_decision,
                paul_decision_suggestion=suggestion,
            ),
            advanced,
        )

    def _plan_review(
        self, scenario: JobScenario, category: str, decision: str
    ) -> tuple[str | None, str | None]:
        """Decide whether a human reviewed this, and whether they agreed."""
        if self.rng.random() >= scenario.review_rates.get(category, 0.0):
            return None, None  # unreviewed: Paul decided end to end
        if decision in OPT_OUTS or decision in MALFORMED:
            return None, None  # nothing for a human to agree or disagree with

        overrode = self.rng.random() < scenario.override_rates.get(category, 0.0)
        # Mix explicit and independent forms so the analyzer's `basis` handling
        # is exercised: some reviews approve/reject Paul's suggestion directly,
        # others record an independent decision alongside a suggestion.
        if self.rng.random() < 0.6:
            return ("RejectPaulDecision" if overrode else "ApprovePaulDecision"), decision
        opposite = NEGATIVE if decision == POSITIVE else POSITIVE
        return (opposite if overrode else decision), decision

    # -- writing -----------------------------------------------------------
    def run(self, jobs: list[PlannedJob]) -> list[PlannedJob]:
        for job in jobs:
            try:
                self._seed_job(job)
            except ApiError as exc:
                # Per-job isolation: one broken job must not kill the run.
                self.errors.append(f"{job.external_id}: {exc}")
                log.error("job %s failed: %s", job.external_id, exc)
        return jobs

    def _seed_job(self, job: PlannedJob) -> None:
        job.paulsjob_job_id = self._ensure_job(job)
        if self.dry_run:
            log.info("[dry-run] would seed %d candidates into %s", len(job.candidates), job.external_id)
            return
        # A pipeline cannot be initialised until the job has an owner, and a
        # company API key has no person behind it, so nothing is assigned
        # automatically. Jobs created before this fix also need one attached.
        self._ensure_job_owner(job)
        job.steps = self._ensure_steps(job)
        self._apply_human_in_loop(job)
        seeded = 0
        for candidate in job.candidates:
            try:
                self._seed_candidate(job, candidate)
                seeded += 1
            except ApiError as exc:
                self.errors.append(f"{job.external_id}/{candidate.email}: {exc}")
        log.info("job %s (%s): %d/%d candidates seeded", job.external_id, job.paulsjob_job_id, seeded, len(job.candidates))

    def _ensure_job(self, job: PlannedJob) -> str | None:
        """Reuse an existing seeded job if one is already there."""
        existing = self._find_job(job.external_id)
        if existing:
            log.info("job %s already exists (%s), reusing", job.external_id, existing)
            return existing
        if self.dry_run:
            log.info("[dry-run] would create job %s (%s)", job.external_id, job.title)
            return None
        scenario = next(s for s in SCENARIOS if s.external_id == job.external_id)
        created = self.client.post(
            "/recruiting/jobs",
            json_body={
                "JobPositionTitle": scenario.title,
                # Not a string: an object whose required field is JobRequirements.
                # Convenient, because it is also where the hard criteria live --
                # which is what the "reason cites no configured criterion" check
                # compares rejection explanations against.
                "JobPositionDescription": {
                    "JobRequirements": scenario.description,
                    "IdealCandidateProfile": "; ".join(scenario.criteria),
                },
                "JobExternalID": scenario.external_id,
                "Published": True,
                "Expired": False,
            },
        )
        job_id = self._extract_job_id(created)
        log.info("created job %s -> %s", job.external_id, job_id)
        return job_id

    def _find_job(self, external_id: str) -> str | None:
        try:
            found = self.client.get(f"/recruiting/jobs/by-external-id/{external_id}")
        except HttpError as exc:
            if exc.status == 404:
                return None
            raise
        return self._extract_job_id(found)

    @staticmethod
    def _extract_job_id(payload: Any) -> str | None:
        """Response shapes vary; look for the id rather than assuming a path."""
        if not isinstance(payload, dict):
            return None
        for key in ("PaulsjobJobID", "PaulsJobID", "ID", "JobID"):
            value = payload.get(key)
            if value:
                return str(value)
        for nested in ("Job", "data"):
            inner = payload.get(nested)
            if isinstance(inner, dict):
                found = Seeder._extract_job_id(inner)
                if found:
                    return found
        return None

    def _owner_slug(self) -> str | None:
        """Resolve a person to own the seeded jobs, once per run.

        Preference order, so we disturb the environment as little as possible:
          1. PAULSJOB_JOB_OWNER_SLUG, if the operator set one
          2. an employee that already exists on the account
          3. a clearly-tagged seed employee we create
        """
        if self._cached_owner is not None:
            return self._cached_owner or None

        configured = os.environ.get("PAULSJOB_JOB_OWNER_SLUG", "").strip()
        if configured:
            log.info("using configured job owner %s", configured)
            self._cached_owner = configured
            return configured

        try:
            found = self.client.post("/company/person/search-employees", json_body={"PerPage": 5, "Page": 1})
            slug = self._first_slug(found)
            if slug:
                log.info("using existing employee as job owner: %s", slug)
                self._cached_owner = slug
                return slug
        except ApiError as exc:
            log.debug("employee lookup failed (%s); will create a seed owner", exc)

        created = self.client.post(
            "/company/person",
            json_body={
                "EmployeeOrCandidate": "employee",
                "FirstName": "Seed",
                "LastName": "Owner",
                "Email": "saa-seed-owner@seed.invalid",
                "Idempotent": True,
            },
        )
        slug = self._first_slug(created)
        log.info("created seed job owner: %s", slug)
        self._cached_owner = slug or ""
        return slug

    #: Create-person returns `user_slug` in snake_case, while the rest of the
    #: API is PascalCase. Match case-insensitively rather than enumerate every
    #: spelling this API might use.
    _SLUG_KEYS = ("userslug", "personslug", "slug")

    @staticmethod
    def _first_slug(payload: Any) -> str | None:
        """Find a person slug in a response whose shape we have not verified."""
        if isinstance(payload, dict):
            lowered = {str(k).replace("_", "").lower(): v for k, v in payload.items()}
            for key in Seeder._SLUG_KEYS:
                value = lowered.get(key)
                if value:
                    return str(value)
            for key in ("Person", "People", "Persons", "Employees", "Data", "Items"):
                found = Seeder._first_slug(payload.get(key))
                if found:
                    return found
        if isinstance(payload, list):
            for item in payload:
                found = Seeder._first_slug(item)
                if found:
                    return found
        return None

    def _ensure_job_owner(self, job: PlannedJob) -> None:
        slug = self._owner_slug()
        if not slug:
            self.errors.append(f"{job.external_id}: could not resolve a job owner; set PAULSJOB_JOB_OWNER_SLUG")
            return
        try:
            self.client.post(
                f"/recruiting/jobs/{job.paulsjob_job_id}/hiring-managers",
                json_body={"PersonSlug": slug, "IsJobOwner": True},
            )
            log.debug("job %s owner set to %s", job.external_id, slug)
        except HttpError as exc:
            # Already attached on a re-run: not a failure.
            if exc.status in (409, 422) and "owner" not in exc.message.lower():
                log.debug("owner already set on %s (%s)", job.external_id, exc.status)
                return
            if exc.status == 409:
                return
            raise

    def _ensure_steps(self, job: PlannedJob) -> dict[str, str]:
        """Initialise the pipeline if needed and map category -> step id."""
        steps = self._list_steps(job.paulsjob_job_id)
        if not steps:
            template_id = os.environ.get("PAULSJOB_PIPELINE_TEMPLATE_ID", "").strip()
            if template_id:
                self.client.post(
                    f"/recruiting/jobs/{job.paulsjob_job_id}/steps/init",
                    json_body={"PipelineTemplateID": template_id},
                )
            else:
                # This account has no pipeline template library and no company
                # default, so `steps/init` with AutoInit has nothing to select,
                # and POST /jobs/{id}/steps is documented but not routed. So we
                # build a template once per run and initialise every job from
                # it -- which keeps the environment reproducible from code, with
                # no manual UI setup for a reviewer.
                self.client.post(
                    f"/recruiting/jobs/{job.paulsjob_job_id}/steps/init",
                    json_body={"PipelineTemplateID": self._ensure_template()},
                )
            steps = self._list_steps(job.paulsjob_job_id)
        # A category is NOT unique within a pipeline: this account's own
        # template has two TeamDiscussion steps ("Teamdiskussion" and
        # "Erledigt"). Keep the lowest OrderIndex per category so the seeder
        # targets the first occurrence deterministically, and log the collision
        # rather than letting it disappear silently. The analyzer must key on
        # StepID for the same reason.
        by_category: dict[str, tuple[int, str]] = {}
        for step in steps:
            category = step.get("Category") or {}
            category_id = category.get("ID") if isinstance(category, dict) else category
            step_id = step.get("ID")
            if not category_id or not step_id:
                continue
            order = step.get("OrderIndex")
            order = order if isinstance(order, int) else 10**6
            key = str(category_id)
            if key in by_category:
                log.debug(
                    "job %s: category %s appears on more than one step; using the earliest",
                    job.external_id, key,
                )
                if by_category[key][0] <= order:
                    continue
            by_category[key] = (order, str(step_id))
        mapping = {category: step_id for category, (_order, step_id) in by_category.items()}
        log.debug("job %s steps: %s", job.external_id, sorted(mapping))
        return mapping

    TEMPLATE_NAME = "SAA Seed Pipeline"

    def _ensure_template(self) -> str | None:
        """Create (once) the pipeline template every seeded job is built from.

        Deliberately NOT marked IsDefault: this is a shared environment, and
        changing the company-wide default would affect jobs we do not own.
        """
        if self._cached_template is not None:
            return self._cached_template or None

        listing = self.client.get("/recruiting/job-step-templates/pipelines", PerPage=50)
        existing = (listing or {}).get("PipelineTemplates") or [] if isinstance(listing, dict) else []
        for template in existing:
            if isinstance(template, dict) and template.get("Name") == self.TEMPLATE_NAME:
                self._cached_template = str(template.get("ID"))
                log.info("reusing pipeline template %s (%s)", self.TEMPLATE_NAME, self._cached_template)
                return self._cached_template

        # Deliberately not auto-created. A pipeline template is only usable
        # once every agent-requiring step has an agent with next-step rules and
        # routing targets; a bare template created here would be silently
        # broken. Recreating it faithfully is a separate, explicit step.
        raise SystemExit(
            f"No pipeline template named {self.TEMPLATE_NAME!r} exists on this account.\n"
            "  Create it once (see README: 'Pipeline setup'), or set\n"
            "  PAULSJOB_PIPELINE_TEMPLATE_ID to an existing template.\n"
            "  Its verified structure is recorded in fixtures/pipeline_template.json,\n"
            "  fixtures/pipeline_steps.json and fixtures/pipeline_agents.json."
        )

    @staticmethod
    def _extract_id(payload: Any) -> str | None:
        if isinstance(payload, dict):
            for key in ("ID", "Id", "id", "PipelineTemplateID"):
                if payload.get(key):
                    return str(payload[key])
            for nested in ("PipelineTemplate", "Data"):
                found = Seeder._extract_id(payload.get(nested))
                if found:
                    return found
        return None

    @staticmethod
    def _agent_list(payload: Any) -> list[dict]:
        """Agents come back under different keys depending on the endpoint.

        Template steps return `JobStepAgentTemplates`; the spec documents
        `Agents` for job steps. Accept either rather than assume.
        """
        if isinstance(payload, list):
            return [a for a in payload if isinstance(a, dict)]
        if isinstance(payload, dict):
            for key in ("Agents", "JobStepAgentTemplates", "JobStepAgents", "StepAgents"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [a for a in value if isinstance(a, dict)]
        return []

    def _apply_human_in_loop(self, job: PlannedJob) -> None:
        """Override HumanInLoop on this job's step agents where the scenario says so.

        The shared template sets always_off on the AI screening steps, matching
        the platform default. Job 3's pathology is a step configured to always
        require review that is nevertheless almost never reviewed, so we flip
        that job's agents to always_on after the pipeline is initialised.

        This is what makes the anomaly config-aware rather than a threshold I
        picked: the report compares configured intent against observed reviews.
        """
        scenario = next(s for s in SCENARIOS if s.external_id == job.external_id)
        if not scenario.human_in_loop:
            return
        for step in self._list_steps(job.paulsjob_job_id):
            category = step.get("Category") or {}
            category_id = category.get("ID") if isinstance(category, dict) else category
            setting = scenario.human_in_loop.get(str(category_id))
            if not setting or not step.get("ID"):
                continue
            try:
                listing = self.client.get(
                    f"/recruiting/jobs/{job.paulsjob_job_id}/steps/{step['ID']}/agents"
                )
            except HttpError as exc:
                self.errors.append(f"{job.external_id}: could not read agents on {category_id}: {exc}")
                continue

            for agent in self._agent_list(listing):
                agent_id = agent.get("ID")
                if not agent_id:
                    continue
                # Re-send the stored agent with only HumanInLoop.Setting
                # changed: the platform rejects a save that drops next-step
                # rules, so a partial payload would break the routing.
                body = dict(agent)
                body.pop("ID", None)
                human_in_loop = dict(body.get("HumanInLoop") or {})
                human_in_loop["Setting"] = setting
                human_in_loop.pop("Condition", None)  # only valid when conditional
                body["HumanInLoop"] = human_in_loop
                try:
                    self.client.request(
                        "PUT",
                        f"/recruiting/jobs/{job.paulsjob_job_id}/steps/{step['ID']}/agents/{agent_id}",
                        json_body=body,
                    )
                    log.info("%s/%s HumanInLoop -> %s", job.external_id, category_id, setting)
                except HttpError as exc:
                    # Not fatal: the anomaly then degrades to a threshold,
                    # which the report states rather than hides.
                    self.errors.append(
                        f"{job.external_id}: could not set HumanInLoop on {category_id}: {exc}"
                    )

    def _list_steps(self, job_id: str | None) -> list[dict]:
        data = self.client.get(f"/recruiting/jobs/{job_id}/steps")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("Steps", "JobSteps", "data"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
        return []

    def _already_assigned(self, person_slug: str, job_id: str | None) -> bool:
        """Has this candidate already been walked through this job's pipeline?

        Person creation is idempotent and applications are keyed by job, but
        step assignments APPEND to the history. Without this check a second run
        would double every decision record and silently corrupt the counts the
        analyzer reports.
        """
        try:
            data = self.client.get(
                f"/recruiting/{person_slug}/jobs/{job_id}/steps-assignment-history", PerPage=1
            )
        except ApiError:
            return False  # can't tell: prefer seeding over silently skipping
        return bool((data or {}).get("History"))

    def _mute_paul(self, person_slug: str) -> None:
        """Stop the live AI acting on a seeded candidate.

        Assigning a candidate to a step that has an active agent TRIGGERS that
        agent: Paul runs a real assessment, consumes credits, and may write his
        own conclusion over the ground truth we are planting. Since we supply
        the decisions ourselves, Paul must not run on these people at all.

        Muted before the first assignment, never after -- the ordering is the
        whole point. Reversible via the unmute endpoint.
        """
        try:
            self.client.post(
                f"/company/person/{person_slug}/paul-mute-events",
                json_body={
                    "DisabledByType": "manual",
                    "DisabledByPersonSlug": self._owner_slug(),
                    "Notes": (
                        "Seed data for the Screening Accuracy Analyzer. Decisions are "
                        "supplied via the API; the live agent must not run on this person."
                    ),
                },
            )
            self.muted += 1
        except ApiError as exc:
            # Refuse to assign an unmuted candidate: better to skip one than to
            # spend credits and corrupt the seeded decisions.
            raise HttpError(
                409,
                f"could not mute Paul for {person_slug}; skipping to avoid triggering the live agent ({exc})",
                "/company/person/paul-mute-events",
            ) from exc

    def _seed_candidate(self, job: PlannedJob, candidate: PlannedCandidate) -> None:
        candidate.person_slug = self._ensure_person(candidate)
        self._mute_paul(candidate.person_slug)
        if self._already_assigned(candidate.person_slug, job.paulsjob_job_id):
            log.debug("%s already has history on %s, skipping", candidate.email, job.external_id)
            self.skipped += 1
            return
        self.client.post(
            f"/recruiting/{candidate.person_slug}/applications/",
            json_body={
                "JobPositionID": str(job.paulsjob_job_id),
                "JobPositionTitle": job.title,
            },
        )
        for assignment in candidate.assignments:
            step_id = job.steps.get(assignment.step_category)
            if not step_id:
                self.errors.append(
                    f"{job.external_id}: no '{assignment.step_category}' step in this job's pipeline"
                )
                continue
            body: dict[str, Any] = {
                "PaulDecision": assignment.paul_decision,
                "PaulDecisionExplanation": assignment.paul_decision_explanation,
                "AgentReview": True,
                "HumanReview": assignment.assigner_decision is not None,
            }
            if assignment.assigner_decision:
                body["AssignerDecision"] = assignment.assigner_decision
            if assignment.paul_decision_suggestion:
                body["PaulDecisionSuggestion"] = assignment.paul_decision_suggestion
            self.client.post(
                f"/recruiting/{candidate.person_slug}/jobs/{job.paulsjob_job_id}/steps/{step_id}",
                json_body=body,
            )

    def _ensure_person(self, candidate: PlannedCandidate) -> str | None:
        created = self.client.post(
            "/company/person",
            json_body={
                "EmployeeOrCandidate": "candidate",
                "FirstName": candidate.first_name,
                "LastName": candidate.last_name,
                "Email": candidate.email,
                "Idempotent": True,
            },
        )
        slug = self._first_slug(created)
        if not slug:
            raise HttpError(200, f"no person slug in response: {created!r}", "/company/person")
        return slug


def write_manifest(jobs: list[PlannedJob], path: str = MANIFEST_PATH) -> dict:
    """Record what was planted, so a test can assert the analyzer finds it."""
    manifest = {
        "rng_seed": RNG_SEED,
        "jobs": [
            {
                "external_id": job.external_id,
                "paulsjob_job_id": job.paulsjob_job_id,
                "title": job.title,
                "demonstrates": job.demonstrates,
                "criteria": job.criteria,
                "candidates": len(job.candidates),
                "assignments": sum(len(c.assignments) for c in job.candidates),
                "expected": _expected(job),
            }
            for job in jobs
        ],
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)
    return manifest


def _expected(job: PlannedJob) -> dict:
    """Per-step ground truth: what the analyzer should report for this job."""
    by_step: dict[str, dict] = {}
    for candidate in job.candidates:
        for assignment in candidate.assignments:
            cell = by_step.setdefault(
                assignment.step_category,
                {"total": 0, "positive": 0, "negative": 0, "opt_out": 0, "malformed": 0,
                 "reviewed": 0, "overrides": 0, "off_criteria_reasons": 0},
            )
            cell["total"] += 1
            decision = assignment.paul_decision
            if decision == "PositiveDecision":
                cell["positive"] += 1
            elif decision == "NegativeDecision":
                cell["negative"] += 1
                if not assignment.reason_in_criteria:
                    cell["off_criteria_reasons"] += 1
            elif decision in OPT_OUTS:
                cell["opt_out"] += 1
            else:
                cell["malformed"] += 1
            if assignment.assigner_decision:
                cell["reviewed"] += 1
                explicit_override = assignment.assigner_decision == "RejectPaulDecision"
                independent_override = (
                    assignment.assigner_decision in ("PositiveDecision", "NegativeDecision")
                    and assignment.paul_decision_suggestion
                    and assignment.assigner_decision != assignment.paul_decision_suggestion
                )
                if explicit_override or independent_override:
                    cell["overrides"] += 1
    return by_step


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed the Paulsjob dev environment with test data.")
    parser.add_argument("--dry-run", action="store_true", help="print the plan without writing anything")
    parser.add_argument("--manifest", default=MANIFEST_PATH)
    parser.add_argument(
        "--jobs", type=int, default=0,
        help="seed only the first N scenarios (0 = all). Use with --candidates for a smoke test.",
    )
    parser.add_argument(
        "--candidates", type=int, default=0,
        help="override candidates per job (0 = as defined). Use a small value to validate the write path first.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(message)s",
    )

    settings = Settings.from_env()
    client = PaulsjobClient(settings.base_url, settings.api_key)
    seeder = Seeder(client, dry_run=args.dry_run)

    scenarios = SCENARIOS[: args.jobs] if args.jobs else SCENARIOS
    if args.candidates:
        scenarios = [replace(s, candidates=args.candidates) for s in scenarios]
        print(f"Smoke test: {len(scenarios)} job(s), {args.candidates} candidate(s) each\n")

    jobs = seeder.plan(scenarios)
    planned_candidates = sum(len(j.candidates) for j in jobs)
    planned_assignments = sum(len(c.assignments) for j in jobs for c in j.candidates)
    print(f"Planned: {len(jobs)} jobs, {planned_candidates} candidates, {planned_assignments} step assignments")
    for job in jobs:
        print(f"  {job.external_id}  {job.title[:44]:<44} {len(job.candidates):>3} candidates")
        print(f"      demonstrates: {job.demonstrates}")

    if args.dry_run:
        print("\nDry run: nothing was written.")
        return 0

    seeder.run(jobs)
    manifest = write_manifest(jobs, args.manifest)
    print(f"\nRequests: {client.stats.requests} ({client.stats.retries} retried)")
    if seeder.muted:
        print(f"Muted Paul for {seeder.muted} candidate(s) before assigning (no agent runs, no credits).")
    if seeder.skipped:
        print(f"Skipped {seeder.skipped} candidate(s) that already had assignment history.")
    if seeder.errors:
        print(f"Errors ({len(seeder.errors)}):")
        for error in seeder.errors[:20]:
            print(f"  - {error}")
    print(f"Manifest written to {args.manifest} ({len(manifest['jobs'])} jobs)")
    return 1 if seeder.errors else 0


if __name__ == "__main__":
    sys.exit(main())
