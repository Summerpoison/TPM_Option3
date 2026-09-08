"""Analysis tests. Records are built by hand, so no network and no fixtures."""
from analyzer.analysis import (
    ACT_MIN_PEOPLE,
    HIGH_OPT_OUT_RATE,
    LOW_CONFIDENCE_N,
    analyse,
    category_totals,
    job_totals,
)
from analyzer.fetch import Dataset, DecisionRecord, JobInfo, StepConfig
from analyzer.model import Agreement, Outcome
from analyzer.reasons import BucketConfig, normalize

CONFIG = BucketConfig(buckets={
    "certification": [normalize(k) for k in ("certificate", "zertifikat", "34a")],
    "language": [normalize(k) for k in ("german", "deutsch", "sprachniveau")],
    "salary": [normalize(k) for k in ("salary", "gehalt")],
})


def step(step_id="s1", category="PreScreening", order=1, hil=None, negative_criteria=""):
    return StepConfig(
        step_id=step_id, name=f"Step {step_id}", category=category, order=order,
        has_agent=True, agent_active=True, human_in_loop=hil,
        negative_criteria=negative_criteria,
    )


def job(job_id="j1", steps=None):
    return JobInfo(job_id=job_id, title=f"Job {job_id}", external_id=f"ext-{job_id}",
                   requirements="", steps=tuple(steps or [step()]))


def record(job_id="j1", step_id="s1", outcome=Outcome.NEGATIVE, agreement=Agreement.UNREVIEWED,
           basis="no_assigner_decision", explanation="Missing certificate", person="Ann Example"):
    return DecisionRecord(
        job_id=job_id, step_id=step_id, step_name=f"Step {step_id}", step_category="PreScreening",
        person_slug=person.lower().replace(" ", "-"), person_name=person,
        outcome=outcome, raw_decision=outcome.value, explanation=explanation,
        agreement=agreement, agreement_basis=basis, assigner_decision=None,
        assigned_at="2026-01-01T00:00:00Z",
    )


def dataset(records, jobs=None):
    return Dataset(jobs=jobs or [job()], records=records)


class TestCellCounts:
    def test_opt_outs_are_excluded_from_the_rejection_rate(self):
        """The distinction the headline number depends on."""
        records = (
            [record(outcome=Outcome.NEGATIVE)] * 3
            + [record(outcome=Outcome.POSITIVE)] * 1
            + [record(outcome=Outcome.OPT_OUT)] * 6
        )
        cell = analyse(dataset(records), CONFIG).cells[0]
        assert cell.total == 10
        assert cell.judged == 4
        assert cell.rejection_rate == 0.75          # 3 of 4 judged
        assert cell.rejection_rate != 3 / 10        # not 3 of 10 overall

    def test_unmappable_and_unevaluated_are_separate(self):
        records = [record(outcome=Outcome.UNKNOWN), record(outcome=Outcome.NONE)]
        cell = analyse(dataset(records), CONFIG).cells[0]
        assert cell.unmappable == 1 and cell.unevaluated == 1
        assert cell.judged == 0 and cell.rejection_rate is None

    def test_rates_are_none_rather_than_zero_when_undefined(self):
        cell = analyse(dataset([record(outcome=Outcome.OPT_OUT)]), CONFIG).cells[0]
        assert cell.rejection_rate is None
        assert cell.override_rate is None


class TestOverrideRate:
    def test_denominator_is_comparable_reviews_not_everything(self):
        records = (
            [record(agreement=Agreement.OVERRIDE, basis="explicit_reject")] * 2
            + [record(agreement=Agreement.AGREE, basis="explicit_approve")] * 2
            + [record(agreement=Agreement.UNREVIEWED)] * 16
        )
        cell = analyse(dataset(records), CONFIG).cells[0]
        assert cell.override_rate == 0.5     # 2 of 4 reviewed, not 2 of 20
        assert cell.review_coverage == 0.2

    def test_explicit_and_inferred_overrides_are_counted_separately(self):
        records = [
            record(agreement=Agreement.OVERRIDE, basis="explicit_reject"),
            record(agreement=Agreement.OVERRIDE, basis="compared_to_decision"),
        ]
        cell = analyse(dataset(records), CONFIG).cells[0]
        assert cell.explicit_overrides == 1 and cell.inferred_overrides == 1

    def test_independent_reviews_count_as_reviewed_but_not_comparable(self):
        records = [record(agreement=Agreement.INDEPENDENT, basis="no_ai_opinion")] * 4
        cell = analyse(dataset(records), CONFIG).cells[0]
        assert cell.reviewed == 4
        assert cell.override_rate is None


class TestGrouping:
    def test_cells_key_on_step_id_not_category(self):
        """Two steps can share a category -- this account's template has two."""
        # Same category, two distinct steps: grouping by category would merge
        # them into one row and quietly halve the number of pipeline stages.
        steps = [step("s1", "PreScreening", 1), step("s2", "PreScreening", 2)]
        records = [record(step_id="s1"), record(step_id="s2"), record(step_id="s2")]
        result = analyse(dataset(records, jobs=[job(steps=steps)]), CONFIG)
        assert len(result.cells) == 2
        assert sorted(c.total for c in result.cells) == [1, 2]

    def test_job_rollup_sums_its_cells(self):
        steps = [step("s1", "PreScreening", 1), step("s2", "AIVoiceInterview", 2)]
        records = [record(step_id="s1")] * 3 + [record(step_id="s2")] * 2
        result = analyse(dataset(records, jobs=[job(steps=steps)]), CONFIG)
        assert job_totals(result, "j1").total == 5

    def test_category_rollup_spans_jobs(self):
        jobs = [job("j1"), job("j2")]
        records = [record(job_id="j1")] * 2 + [record(job_id="j2")] * 3
        result = analyse(dataset(records, jobs=jobs), CONFIG)
        assert category_totals(result, "PreScreening").total == 5


class TestLowConfidence:
    def test_small_cells_are_marked_but_still_reported(self):
        result = analyse(dataset([record()] * (LOW_CONFIDENCE_N - 1)), CONFIG)
        assert result.cells[0].low_confidence
        assert result.cells[0].total == LOW_CONFIDENCE_N - 1  # not dropped

    def test_large_cells_are_not_marked(self):
        result = analyse(dataset([record()] * LOW_CONFIDENCE_N), CONFIG)
        assert not result.cells[0].low_confidence


class TestFindings:
    """One card per (job, step); priority from people affected and confidence."""

    @staticmethod
    def people(n, **kw):
        return [record(person=f"Person {i:02d}", **kw) for i in range(n)]

    @staticmethod
    def signals(result):
        return [s for f in result.findings for s in f.signals]

    # -- reversals ----------------------------------------------------------
    def test_many_reversals_are_act_now_and_name_the_people(self):
        records = (
            self.people(5, agreement=Agreement.OVERRIDE, basis="explicit_reject")
            + [record(agreement=Agreement.AGREE, basis="explicit_approve", person=f"Ok {i}")
               for i in range(5)]
        )
        result = analyse(dataset(records), CONFIG)
        assert len(result.findings) == 1
        card = result.findings[0]
        assert card.priority == "act"
        signal = card.signals[0]
        assert "reversed Paul on 5 of the 10" in signal.headline
        assert signal.people == [f"Person {i:02d}" for i in range(5)]

    def test_few_reversals_on_enough_reviews_is_check(self):
        records = (
            self.people(3, agreement=Agreement.OVERRIDE, basis="explicit_reject")
            + [record(agreement=Agreement.AGREE, basis="explicit_approve")] * 7
        )
        result = analyse(dataset(records), CONFIG)
        assert result.findings[0].priority == "check"

    def test_reversals_on_too_few_reviews_only_watched(self):
        """40% of 5 is two people: listed, never a priority."""
        records = (
            self.people(2, agreement=Agreement.OVERRIDE, basis="explicit_reject")
            + [record(agreement=Agreement.AGREE, basis="explicit_approve")] * 3
        )
        result = analyse(dataset(records), CONFIG)
        assert result.findings == []
        assert any("reversed" in s.headline for _scope, s in result.watch)

    def test_agreement_produces_no_finding(self):
        """The control case: a healthy step must produce nothing."""
        records = [record(agreement=Agreement.AGREE, basis="explicit_approve")] * 20
        result = analyse(dataset(records), CONFIG)
        assert result.findings == [] and result.watch == []

    # -- approval gaps on always_on steps ------------------------------------
    def test_gap_only_counts_when_the_step_requires_review(self):
        records = [record(agreement=Agreement.UNREVIEWED)] * 20
        off = analyse(dataset(records, jobs=[job(steps=[step(hil="always_off")])]), CONFIG)
        on = analyse(dataset(records, jobs=[job(steps=[step(hil="always_on")])]), CONFIG)
        assert off.findings == []
        assert on.findings and "without the recruiter approval" in on.findings[0].signals[0].headline

    def test_nobody_reviewing_is_a_notification_problem(self):
        records = self.people(20, agreement=Agreement.UNREVIEWED)
        result = analyse(dataset(records, jobs=[job(steps=[step(hil="always_on")])]), CONFIG)
        signal = result.findings[0].signals[0]
        assert result.findings[0].priority == "act"
        assert signal.kind == "config"
        assert "notified" in signal.action
        assert len(signal.people) == 20

    def test_a_few_missed_is_a_review_list_not_a_notification_problem(self):
        """16 of 20 reviewed proves notification works; the action is the four names."""
        records = (
            self.people(4, agreement=Agreement.UNREVIEWED)
            + [record(agreement=Agreement.AGREE, basis="explicit_approve", person=f"Seen {i}")
               for i in range(16)]
        )
        result = analyse(dataset(records, jobs=[job(steps=[step(hil="always_on")])]), CONFIG)
        card = result.findings[0]
        signal = card.signals[0]
        assert card.priority == "check"
        assert signal.kind == "review"
        assert "notified" not in signal.action
        assert signal.people == [f"Person {i:02d}" for i in range(4)]
        assert "4 of 20" in signal.headline

    def test_opt_outs_unevaluated_and_unrecognised_are_not_missed_reviews(self):
        records = (
            [record(outcome=Outcome.OPT_OUT)] * 3
            + [record(outcome=Outcome.NONE)] * 2
            + [record(outcome=Outcome.UNKNOWN)] * 2
            + [record(agreement=Agreement.AGREE, basis="explicit_approve")] * 15
        )
        result = analyse(dataset(records, jobs=[job(steps=[step(hil="always_on")])]), CONFIG)
        cell = result.cells[0]
        assert cell.reviewable == 15 and cell.approval_gap == 0
        assert cell.review_coverage == 1.0
        assert result.findings == []

    # -- opt-outs -------------------------------------------------------------
    def test_opt_out_cluster_is_check_never_act(self):
        records = [record(outcome=Outcome.OPT_OUT)] * 5 + [record(outcome=Outcome.POSITIVE)] * 5
        result = analyse(dataset(records), CONFIG)
        signal = result.findings[0].signals[0]
        assert signal.kind == "contact" and signal.priority == "check"
        assert 5 / 10 >= HIGH_OPT_OUT_RATE

    def test_two_opt_outs_of_seven_is_only_watched(self):
        records = [record(outcome=Outcome.OPT_OUT)] * 2 + [record(outcome=Outcome.POSITIVE)] * 5
        result = analyse(dataset(records), CONFIG)
        assert result.findings == []
        assert any("dropped out" in s.headline for _scope, s in result.watch)

    # -- reasons outside the listing ------------------------------------------
    def test_reasons_citing_an_unstated_requirement_name_the_topic_and_people(self):
        steps = [step(negative_criteria="Zertifikat fehlt oder Sprachniveau zu niedrig")]
        records = self.people(3, explanation="Gehaltsvorstellung zu hoch")
        result = analyse(dataset(records, jobs=[job(steps=steps)]), CONFIG)
        signal = result.findings[0].signals[0]
        assert "cite salary" in signal.headline and "never asks for" in signal.headline
        assert signal.priority == "check"
        assert len(signal.people) == 3

    def test_a_single_off_criteria_reason_is_only_watched(self):
        steps = [step(negative_criteria="Zertifikat fehlt")]
        records = [record(explanation="Gehaltsvorstellung zu hoch")]
        result = analyse(dataset(records, jobs=[job(steps=steps)]), CONFIG)
        assert result.findings == [] and len(result.watch) == 1

    def test_many_off_criteria_reasons_are_act_now(self):
        steps = [step(negative_criteria="Zertifikat fehlt")]
        records = self.people(ACT_MIN_PEOPLE, explanation="Gehaltsvorstellung zu hoch")
        result = analyse(dataset(records, jobs=[job(steps=steps)]), CONFIG)
        assert result.findings[0].priority == "act"

    # -- shape ----------------------------------------------------------------
    def test_unmappable_values_are_data_quality_not_a_finding(self):
        records = [record(outcome=Outcome.UNKNOWN)] * 2
        result = analyse(dataset(records), CONFIG)
        assert result.findings == []
        assert result.cells[0].unmappable == 2

    def test_one_card_per_step_carries_all_its_signals(self):
        records = (
            self.people(5, agreement=Agreement.OVERRIDE, basis="explicit_reject")
            + [record(agreement=Agreement.AGREE, basis="explicit_approve")] * 5
            + [record(outcome=Outcome.OPT_OUT)] * 5
        )
        result = analyse(dataset(records), CONFIG)
        assert len(result.findings) == 1
        kinds = {s.kind for s in result.findings[0].signals}
        assert kinds == {"listing", "contact"}

    def test_every_signal_carries_an_action_and_a_number(self):
        records = (
            self.people(5, agreement=Agreement.OVERRIDE, basis="explicit_reject")
            + [record(agreement=Agreement.AGREE, basis="explicit_approve")] * 5
            + [record(outcome=Outcome.OPT_OUT)] * 5
        )
        result = analyse(dataset(records), CONFIG)
        for signal in self.signals(result) + [s for _scope, s in result.watch]:
            assert signal.action.strip() and signal.n > 0
            assert signal.priority in ("act", "check", "watch")

    def test_cards_are_ordered_act_first_then_by_people_affected(self):
        steps = [step("s1", "PreScreening", 1, hil="always_on"),
                 step("s2", "AIVoiceInterview", 2, hil="always_on")]
        records = (
            # s1: four missed reviews -> check
            [record(step_id="s1", agreement=Agreement.UNREVIEWED, person=f"A{i}") for i in range(4)]
            + [record(step_id="s1", agreement=Agreement.AGREE, basis="explicit_approve")] * 16
            # s2: nobody reviewing -> act
            + [record(step_id="s2", agreement=Agreement.UNREVIEWED, person=f"B{i}") for i in range(12)]
        )
        result = analyse(dataset(records, jobs=[job(steps=steps)]), CONFIG)
        assert [f.priority for f in result.findings] == ["act", "check"]
        assert result.findings[0].step_id == "s2"


class TestEmptyInput:
    def test_no_records_produces_no_cells_and_no_crash(self):
        result = analyse(Dataset(jobs=[job()], records=[]), CONFIG)
        assert result.cells == [] and result.findings == [] and result.watch == []
