import pytest

from analyzer.model import (
    Agreement,
    Outcome,
    derive_agreement,
    normalize_outcome,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        # The documented taxonomy.
        ("PositiveDecision", Outcome.POSITIVE),
        ("NegativeDecision", Outcome.NEGATIVE),
        ("OptOutNoAnswer", Outcome.OPT_OUT),
        ("OptOutDeclineToTalkWithAI", Outcome.OPT_OUT),
        ("OptOutDeclineToContinueApplication", Outcome.OPT_OUT),
        # The spec's own inconsistent example, and the NextStepRule spelling.
        ("PositiveConclusion", Outcome.POSITIVE),
        ("POSITIVE_CONCLUSION", Outcome.POSITIVE),
        ("negative conclusion", Outcome.NEGATIVE),
        # Absent vs. present-but-unmappable are different findings.
        (None, Outcome.NONE),
        ("", Outcome.NONE),
        ("   ", Outcome.NONE),
        ("Maybe", Outcome.UNKNOWN),
        ("Escalated", Outcome.UNKNOWN),
    ],
)
def test_normalize_outcome(raw, expected):
    assert normalize_outcome(raw) is expected


def test_opt_out_is_not_negative():
    """The distinction the whole rejection-rate metric rests on."""
    assert normalize_outcome("OptOutNoAnswer") is not Outcome.NEGATIVE


class TestDeriveAgreement:
    def test_no_assigner_means_unreviewed(self):
        result = derive_agreement(None, paul_decision="NegativeDecision")
        assert result.agreement is Agreement.UNREVIEWED

    def test_blank_assigner_means_unreviewed(self):
        assert derive_agreement("").agreement is Agreement.UNREVIEWED

    def test_explicit_approve(self):
        result = derive_agreement("ApprovePaulDecision")
        assert result.agreement is Agreement.AGREE
        assert result.basis == "explicit_approve"

    def test_explicit_reject(self):
        result = derive_agreement("RejectPaulDecision")
        assert result.agreement is Agreement.OVERRIDE
        assert result.basis == "explicit_reject"

    def test_independent_matching_suggestion_is_agreement(self):
        result = derive_agreement("PositiveDecision", paul_decision_suggestion="PositiveConclusion")
        assert result.agreement is Agreement.AGREE
        assert result.basis == "compared_to_suggestion"

    def test_independent_contradicting_suggestion_is_override(self):
        result = derive_agreement("PositiveDecision", paul_decision_suggestion="NegativeDecision")
        assert result.agreement is Agreement.OVERRIDE
        assert result.human_outcome is Outcome.POSITIVE
        assert result.ai_outcome is Outcome.NEGATIVE

    def test_falls_back_to_paul_decision_and_says_so(self):
        result = derive_agreement("NegativeDecision", paul_decision="PositiveDecision")
        assert result.agreement is Agreement.OVERRIDE
        assert result.basis == "compared_to_decision"

    def test_suggestion_wins_over_decision(self):
        result = derive_agreement(
            "PositiveDecision",
            paul_decision_suggestion="PositiveDecision",
            paul_decision="NegativeDecision",
        )
        assert result.agreement is Agreement.AGREE
        assert result.basis == "compared_to_suggestion"

    def test_no_ai_opinion_is_independent_not_agreement(self):
        result = derive_agreement("PositiveDecision")
        assert result.agreement is Agreement.INDEPENDENT
        assert result.basis == "no_ai_opinion"

    def test_ai_opt_out_is_not_an_agreement_signal(self):
        result = derive_agreement("NegativeDecision", paul_decision_suggestion="OptOutNoAnswer")
        assert result.agreement is Agreement.NOT_APPLICABLE

    def test_unmappable_assigner_decision_is_not_guessed(self):
        result = derive_agreement("SomethingNew")
        assert result.agreement is Agreement.INDEPENDENT
        assert result.basis == "unmappable_assigner_decision"

    def test_unknown_ai_value_does_not_count_as_agreement(self):
        result = derive_agreement("PositiveDecision", paul_decision_suggestion="Escalated")
        assert result.agreement is Agreement.INDEPENDENT
