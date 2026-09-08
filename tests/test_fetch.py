"""Fetch-layer tests. The client is faked, so nothing touches the network."""
import pytest

from analyzer.fetch import Fetcher, JobInfo, StepConfig
from analyzer.model import Agreement, Outcome


def step(step_id="s1", category="PreScreening", order=1):
    return StepConfig(step_id=step_id, name="Vorauswahl", category=category, order=order)


def job(steps=None):
    return JobInfo(job_id="182760", title="Security", external_id="saa-seed-21",
                   steps=tuple(steps or [step()]))


def entry(step_id="s1", decision=None, assigned_at="2026-01-01T00:00:00Z",
          assigner=None, suggestion=None, explanation="", category="PreScreening"):
    return {
        "StepID": step_id, "StepName": "Vorauswahl", "StepCategory": category,
        "PaulDecision": decision, "PaulDecisionExplanation": explanation,
        "PaulDecisionSuggestion": suggestion, "AssignerDecision": assigner,
        "AssignedAt": assigned_at,
    }


@pytest.fixture
def fetcher():
    return Fetcher(client=None)


class TestHistoryCollapsing:
    """Assignments APPEND, so one step can hold several records."""

    def test_single_record_passes_through(self, fetcher):
        records = fetcher._records_from_history(
            job(), "slug", "Ann", [entry(decision="NegativeDecision", explanation="no cert")]
        )
        assert len(records) == 1
        assert records[0].outcome is Outcome.NEGATIVE
        assert records[0].explanation == "no cert"

    def test_latest_decision_wins_and_is_counted_once(self, fetcher):
        history = [
            entry(decision="PositiveDecision", assigned_at="2026-01-01T00:00:00Z"),
            entry(decision="NegativeDecision", assigned_at="2026-01-02T00:00:00Z"),
        ]
        records = fetcher._records_from_history(job(), "slug", "Ann", history)
        assert len(records) == 1                       # not double counted
        assert records[0].outcome is Outcome.NEGATIVE  # the later one
        assert len(fetcher.quality.superseded_records) == 1

    def test_a_decision_beats_a_later_empty_record(self, fetcher):
        """A re-assignment with no decision must not erase the decision."""
        history = [
            entry(decision="NegativeDecision", assigned_at="2026-01-01T00:00:00Z"),
            entry(decision=None, assigned_at="2026-01-05T00:00:00Z"),
        ]
        records = fetcher._records_from_history(job(), "slug", "Ann", history)
        assert records[0].outcome is Outcome.NEGATIVE

    def test_all_empty_still_yields_an_unevaluated_record(self, fetcher):
        history = [entry(decision=None), entry(decision=None, assigned_at="2026-01-02T00:00:00Z")]
        records = fetcher._records_from_history(job(), "slug", "Ann", history)
        assert len(records) == 1 and records[0].outcome is Outcome.NONE

    def test_separate_steps_stay_separate(self, fetcher):
        steps = [step("s1", "PreScreening", 1), step("s2", "AIVoiceInterview", 2)]
        history = [entry("s1", "PositiveDecision"), entry("s2", "NegativeDecision",
                                                          category="AIVoiceInterview")]
        records = fetcher._records_from_history(job(steps), "slug", "Ann", history)
        assert len(records) == 2
        assert not fetcher.quality.superseded_records

    def test_entries_without_a_step_id_are_ignored(self, fetcher):
        records = fetcher._records_from_history(job(), "slug", "Ann", [{"PaulDecision": "x"}])
        assert records == []


class TestStepFiltering:
    def test_terminal_steps_are_not_treated_as_decisions(self, fetcher):
        steps = [step("s9", "Rejected", 6)]
        history = [entry("s9", "PositiveDecision", category="Rejected")]
        assert fetcher._records_from_history(job(steps), "slug", "Ann", history) == []

    def test_state_steps_are_not_treated_as_decisions(self, fetcher):
        steps = [step("s0", "New", 0)]
        history = [entry("s0", None, category="New")]
        assert fetcher._records_from_history(job(steps), "slug", "Ann", history) == []

    def test_unknown_step_falls_back_to_the_records_category(self, fetcher):
        """A step missing from the job config is still analysable."""
        history = [entry("ghost", "NegativeDecision", category="AIVoiceInterview")]
        records = fetcher._records_from_history(job(), "slug", "Ann", history)
        assert len(records) == 1 and records[0].step_category == "AIVoiceInterview"


class TestQualityReporting:
    def test_unmappable_decision_is_recorded_with_the_candidate(self, fetcher):
        fetcher._records_from_history(job(), "slug", "Ann", [entry(decision="Escalated")])
        assert len(fetcher.quality.malformed_decisions) == 1
        assert "Ann" in fetcher.quality.malformed_decisions[0]
        assert "Escalated" in fetcher.quality.malformed_decisions[0]

    def test_empty_decision_is_not_reported_as_malformed(self, fetcher):
        """Absent means 'not evaluated' -- a different fact, not a defect."""
        fetcher._records_from_history(job(), "slug", "Ann", [entry(decision="")])
        assert fetcher.quality.malformed_decisions == []


class TestAgreementWiring:
    def test_assigner_decision_is_carried_through(self, fetcher):
        history = [entry(decision="PositiveDecision", assigner="RejectPaulDecision")]
        record = fetcher._records_from_history(job(), "slug", "Ann", history)[0]
        assert record.agreement is Agreement.OVERRIDE
        assert record.agreement_basis == "explicit_reject"

    def test_no_assigner_means_unreviewed(self, fetcher):
        record = fetcher._records_from_history(
            job(), "slug", "Ann", [entry(decision="PositiveDecision")]
        )[0]
        assert record.agreement is Agreement.UNREVIEWED


class TestDuplicateNoteScope:
    """Duplicate records are only worth reporting for steps we analyse."""

    def test_duplicates_on_an_analysed_step_are_reported(self, fetcher):
        history = [entry(decision="PositiveDecision"),
                   entry(decision="NegativeDecision", assigned_at="2026-01-02T00:00:00Z")]
        fetcher._records_from_history(job(), "slug", "Ann", history)
        assert len(fetcher.quality.superseded_records) == 1

    def test_duplicates_on_a_terminal_step_are_not_reported(self, fetcher):
        """Its records are discarded, so a warning would send the reader
        chasing something that affects no number in the report."""
        steps = [step("s9", "Rejected", 6)]
        history = [entry("s9", "NegativeDecision", category="Rejected"),
                   entry("s9", "NegativeDecision", category="Rejected",
                         assigned_at="2026-01-02T00:00:00Z")]
        records = fetcher._records_from_history(job(steps), "slug", "Ann", history)
        assert records == []
        assert fetcher.quality.superseded_records == []


class TestPathEncoding:
    """Ids and slugs from API responses are data, not path structure."""

    class _Client:
        def __init__(self):
            self.paths = []

        def get(self, path, **params):
            self.paths.append(path)
            return {}

        def paginate_cursor(self, path, items_key, **params):
            self.paths.append(path)
            return iter(())

    def test_history_path_encodes_slug(self):
        client = self._Client()
        fetcher = Fetcher(client=client)
        fetcher.applications = lambda job_id: iter([{"Person": {"Slug": "x/../../admin"}}])
        fetcher.records_for_job(job())
        assert client.paths == ["/recruiting/x%2F..%2F..%2Fadmin/jobs/182760/steps-assignment-history"]

    def test_steps_path_encodes_job_id(self):
        client = self._Client()
        Fetcher(client=client)._steps("1/2")
        assert client.paths == ["/recruiting/jobs/1%2F2/steps"]
