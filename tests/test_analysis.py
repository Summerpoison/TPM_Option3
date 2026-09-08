"""Analysis tests. Records are built by hand, so no network and no fixtures."""
from analyzer.analysis import (
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


class TestAnomalies:
    def test_high_override_rate_is_flagged(self):
        records = (
            [record(agreement=Agreement.OVERRIDE, basis="explicit_reject")] * 5
            + [record(agreement=Agreement.AGREE, basis="explicit_approve")] * 5
        )
        anomalies = analyse(dataset(records), CONFIG).anomalies
        assert any("reversed Paul" in a.headline for a in anomalies)

    def test_agreement_does_not_fire_the_override_flag(self):
        """The control case: a healthy step must produce no finding."""
        records = [record(agreement=Agreement.AGREE, basis="explicit_approve")] * 20
        anomalies = analyse(dataset(records), CONFIG).anomalies
        assert not any("reversed Paul" in a.headline for a in anomalies)

    def test_unreviewed_only_flagged_when_the_step_requires_review(self):
        records = [record(agreement=Agreement.UNREVIEWED)] * 20
        off = analyse(dataset(records, jobs=[job(steps=[step(hil="always_off")])]), CONFIG)
        on = analyse(dataset(records, jobs=[job(steps=[step(hil="always_on")])]), CONFIG)
        assert not any("requires recruiter approval" in a.headline for a in off.anomalies)
        assert any("requires recruiter approval" in a.headline for a in on.anomalies)

    def test_opt_out_cluster_is_flagged_as_a_channel_problem(self):
        records = [record(outcome=Outcome.OPT_OUT)] * 5 + [record(outcome=Outcome.POSITIVE)] * 5
        anomalies = analyse(dataset(records), CONFIG).anomalies
        flagged = [a for a in anomalies if "dropped out" in a.headline]
        assert flagged and flagged[0].severity == "medium"
        assert 5 / 10 >= HIGH_OPT_OUT_RATE

    def test_reason_citing_an_unstated_requirement_is_flagged(self):
        steps = [step(negative_criteria="Zertifikat fehlt oder Sprachniveau zu niedrig")]
        records = [record(explanation="Gehaltsvorstellung zu hoch")] * 3
        anomalies = analyse(dataset(records, jobs=[job(steps=steps)]), CONFIG).anomalies
        assert any("never asks for" in a.headline for a in anomalies)

    def test_unmappable_values_are_flagged(self):
        records = [record(outcome=Outcome.UNKNOWN)] * 2
        assert any("does not recognise" in a.headline
                   for a in analyse(dataset(records), CONFIG).anomalies)

    def test_every_anomaly_carries_an_action_and_a_number(self):
        records = (
            [record(agreement=Agreement.OVERRIDE, basis="explicit_reject")] * 5
            + [record(agreement=Agreement.AGREE)] * 5
            + [record(outcome=Outcome.OPT_OUT)] * 5
        )
        for anomaly in analyse(dataset(records), CONFIG).anomalies:
            assert anomaly.action.strip() and anomaly.n > 0
            assert anomaly.severity in ("high", "medium", "low")

    def test_findings_are_ordered_by_severity(self):
        records = (
            [record(agreement=Agreement.OVERRIDE, basis="explicit_reject")] * 5
            + [record(agreement=Agreement.AGREE)] * 5
            + [record(outcome=Outcome.UNKNOWN)] * 2
        )
        order = [a.severity for a in analyse(dataset(records), CONFIG).anomalies]
        assert order == sorted(order, key=lambda s: {"high": 0, "medium": 1, "low": 2}[s])


class TestEmptyInput:
    def test_no_records_produces_no_cells_and_no_crash(self):
        result = analyse(Dataset(jobs=[job()], records=[]), CONFIG)
        assert result.cells == [] and result.anomalies == []
