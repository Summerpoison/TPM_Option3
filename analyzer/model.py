"""Normalisation of the platform's decision vocabulary.

Everything in this module is a pure function over plain values, so it is
testable without touching the API.

Two vocabularies matter:

`PaulDecision` / `PaulDecisionSuggestion` -- what the AI concluded. Typed in
the OpenAPI spec as a bare string, but the platform's own `NextStepRule.Name`
enum shows the taxonomy behind it: POSITIVE_CONCLUSION, NEGATIVE_CONCLUSION and
three OPT_OUT_* variants. The spec's *examples* are already inconsistent with
its *description* ("PositiveConclusion" vs "PositiveDecision"), so we normalise
tolerantly and report anything we cannot map rather than guessing.

`AssignerDecision` -- what a human did, a proper enum of four values. Null when
Paul drove the decision end to end.
"""
from __future__ import annotations

import enum
import re
from dataclasses import dataclass


class Outcome(enum.Enum):
    """What an evaluation concluded, once normalised."""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    #: The candidate withdrew (silence, declined to continue, refused the AI).
    #: Deliberately NOT folded into NEGATIVE: the AI never judged these people,
    #: and counting them as rejections inflates the rejection rate.
    OPT_OUT = "opt_out"
    #: No decision recorded: step not reached, or no agent configured on it.
    NONE = "none"
    #: A non-empty value we could not map. Always surfaced in data quality.
    UNKNOWN = "unknown"


class Agreement(enum.Enum):
    """How a human's decision related to the AI's, on one step."""

    AGREE = "agree"
    OVERRIDE = "override"
    #: Human acted with no AI recommendation to compare against.
    INDEPENDENT = "independent"
    #: No human involved. Paul decided end to end.
    UNREVIEWED = "unreviewed"
    #: Human acted, but the AI side was an opt-out, so "agreement" is undefined.
    NOT_APPLICABLE = "not_applicable"


#: Prefixes are matched after squashing to lowercase alphanumerics, so
#: "PositiveDecision", "POSITIVE_CONCLUSION" and "Positive Conclusion" all land
#: on the same outcome.
_OUTCOME_PREFIXES: tuple[tuple[str, Outcome], ...] = (
    ("optout", Outcome.OPT_OUT),
    ("positive", Outcome.POSITIVE),
    ("negative", Outcome.NEGATIVE),
)


def _squash(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def normalize_outcome(raw: str | None) -> Outcome:
    """Map a free-form Paul decision string onto an Outcome.

    Absent/blank means "not evaluated", which is different from "evaluated and
    unmappable" -- the first is normal, the second is a data-quality finding.
    """
    if raw is None:
        return Outcome.NONE
    squashed = _squash(str(raw))
    if not squashed:
        return Outcome.NONE
    for prefix, outcome in _OUTCOME_PREFIXES:
        if squashed.startswith(prefix):
            return outcome
    return Outcome.UNKNOWN


#: The four documented values of the AssignerDecision enum.
ASSIGNER_APPROVE = "ApprovePaulDecision"
ASSIGNER_REJECT = "RejectPaulDecision"
ASSIGNER_POSITIVE = "PositiveDecision"
ASSIGNER_NEGATIVE = "NegativeDecision"


@dataclass(frozen=True)
class AgreementResult:
    """The derived agreement plus how we arrived at it.

    `basis` matters for honest reporting. An override derived from an explicit
    `RejectPaulDecision` is a fact. An override inferred by comparing a human's
    independent decision against `PaulDecision` is an inference, and the report
    should not present the two as equally certain.
    """

    agreement: Agreement
    basis: str
    human_outcome: Outcome = Outcome.NONE
    ai_outcome: Outcome = Outcome.NONE


def derive_agreement(
    assigner_decision: str | None,
    paul_decision_suggestion: str | None = None,
    paul_decision: str | None = None,
) -> AgreementResult:
    """Derive human-vs-AI agreement for a single step assignment.

    The two explicit cases are unambiguous: the human was acting *on* a Paul
    suggestion and either took it or did not.

    The independent cases are the interesting ones. The human moved the
    candidate themselves; whether that constitutes agreement depends on there
    being an AI opinion to compare against. We prefer `PaulDecisionSuggestion`
    (documented as exactly this: Paul's recommendation when Paul did not
    finalise) and fall back to `PaulDecision`, recording which was used.
    """
    if assigner_decision is None or not str(assigner_decision).strip():
        return AgreementResult(Agreement.UNREVIEWED, basis="no_assigner_decision")

    decision = str(assigner_decision).strip()

    if decision == ASSIGNER_APPROVE:
        return AgreementResult(Agreement.AGREE, basis="explicit_approve")
    if decision == ASSIGNER_REJECT:
        return AgreementResult(Agreement.OVERRIDE, basis="explicit_reject")

    human = normalize_outcome(decision)
    if human not in (Outcome.POSITIVE, Outcome.NEGATIVE):
        # An AssignerDecision outside the documented enum. Recorded, not guessed.
        return AgreementResult(Agreement.INDEPENDENT, basis="unmappable_assigner_decision", human_outcome=human)

    ai_raw, basis = paul_decision_suggestion, "compared_to_suggestion"
    if ai_raw is None or not str(ai_raw).strip():
        ai_raw, basis = paul_decision, "compared_to_decision"

    ai = normalize_outcome(ai_raw)
    if ai in (Outcome.NONE, Outcome.UNKNOWN):
        return AgreementResult(Agreement.INDEPENDENT, basis="no_ai_opinion", human_outcome=human, ai_outcome=ai)
    if ai is Outcome.OPT_OUT:
        # The candidate withdrew; a human decision on top of that is not an
        # agreement signal in either direction.
        return AgreementResult(Agreement.NOT_APPLICABLE, basis="ai_opt_out", human_outcome=human, ai_outcome=ai)

    agreement = Agreement.AGREE if ai is human else Agreement.OVERRIDE
    return AgreementResult(agreement, basis=basis, human_outcome=human, ai_outcome=ai)
