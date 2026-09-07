"""Seed scenario definitions.

The dev environment ships no data, so we author it. That makes this file the
ground truth for the whole exercise: every insight and anomaly the analyzer
claims to detect is planted here first, and the seeder emits a manifest of what
it planted so a test can assert the analyzer recovers it.

Domain flavour follows the platform's real usage -- security and healthcare
roles with hard, checkable criteria (licences, certifications, language level,
shift availability) and high application volume.

IMPORTANT: because we author the explanations, this data cannot tell us what the
real agent's vocabulary looks like. It validates that the analyzer is *correct*,
not that its findings describe real agent behaviour. Stated in the README.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: Step categories we treat as producing an AI screening decision. Discovered
#: at runtime from the job's actual pipeline; this is only the seeding target.
DECISION_STEPS = ("PreScreening", "AIVoiceInterview", "HumanInterview")

#: The pipeline the seeder builds on each job, in order. The account has no
#: pipeline template library and no company default, so `steps/init` has nothing
#: to auto-select and the steps are created directly on each job instead.
#: Categories and colours come from GET /recruiting/job-step-categories.
PIPELINE = [
    ("New", "New", "#D9D9D9"),
    ("PreScreening", "Pre-Screening", "#9BD9B0"),
    ("AIVoiceInterview", "AI Voice Interview", "#9BC5D9"),
    ("HumanInterview", "Human Interview", "#C5B0D9"),
    ("Rejected", "Rejected", "#D99B9B"),
]


@dataclass(frozen=True)
class ReasonSpec:
    """One rejection reason, with the criterion it is supposed to map to."""

    text: str
    #: Bucket the analyzer should sort this into (tier-2 keyword buckets).
    bucket: str
    #: False when the reason cites something the job config does not contain --
    #: this is what anomaly flag "reason matches no criterion" must catch.
    in_job_criteria: bool = True


@dataclass
class JobScenario:
    external_id: str
    title: str
    description: str
    #: Hard criteria written into the job description, used both to make the
    #: listing realistic and to check rejection reasons against.
    criteria: list[str]
    candidates: int
    #: Fraction of candidates concluded positive at each step, in order.
    pass_rates: dict[str, float]
    #: Fraction of *evaluated* candidates who opt out instead, per step.
    opt_out_rates: dict[str, float] = field(default_factory=dict)
    #: Fraction of decisions a human reviewed at all, per step.
    review_rates: dict[str, float] = field(default_factory=dict)
    #: Of reviewed decisions, the fraction where the human overrode Paul.
    override_rates: dict[str, float] = field(default_factory=dict)
    #: Rejection reasons drawn on for negative decisions.
    reasons: list[ReasonSpec] = field(default_factory=list)
    #: HumanInLoop setting written onto each step's agent, per category.
    #: 'always_on' | 'always_off' | 'conditional'. Lets the analyzer compare
    #: configured intent against observed review behaviour.
    human_in_loop: dict[str, str] = field(default_factory=dict)
    #: Number of records given a deliberately unmappable PaulDecision value.
    malformed_decisions: int = 0
    #: What this job is meant to demonstrate; copied into the manifest.
    demonstrates: str = ""


# Reason vocabularies -------------------------------------------------------
# Clean: a fixed vocabulary, the tier-1 case (exact counting is enough).
CLEAN_REASONS = [
    ReasonSpec("Missing required certification", "certification"),
    ReasonSpec("Insufficient years of experience", "experience"),
    ReasonSpec("German language level below B2", "language"),
    ReasonSpec("Not available for shift work", "availability"),
]

# Ragged: same concepts, free text, two languages, inconsistent casing. This is
# the tier-2 case -- exact counting fragments it, keyword buckets recover it.
RAGGED_REASONS = [
    ReasonSpec("Bewerber hat keine gültige Sachkundeprüfung nach §34a", "certification"),
    ReasonSpec("no §34a certificate provided", "certification"),
    ReasonSpec("Sachkundenachweis fehlt", "certification"),
    ReasonSpec("Deutschkenntnisse nicht ausreichend (unter B2)", "language"),
    ReasonSpec("insufficient german, estimated A2", "language"),
    ReasonSpec("Sprachniveau zu niedrig für die Position", "language"),
    ReasonSpec("nur 1 Jahr Berufserfahrung, gefordert sind 3", "experience"),
    ReasonSpec("too little relevant experience", "experience"),
    ReasonSpec("Keine Bereitschaft zur Nachtschicht", "availability"),
    ReasonSpec("cannot work weekends", "availability"),
    # Deliberately outside every bucket: proves the `other` bucket is real and
    # that a large `other` share is reported as "buckets are incomplete".
    ReasonSpec("Profil wirkt insgesamt nicht überzeugend", "other"),
    ReasonSpec("general fit concerns", "other"),
]

# Cites a criterion the job config does not contain -- the anomaly case.
OFF_CRITERIA_REASONS = [
    ReasonSpec("No driving licence class C provided", "other", in_job_criteria=False),
    ReasonSpec("Candidate has no prior management experience", "other", in_job_criteria=False),
]


SCENARIOS: list[JobScenario] = [
    JobScenario(
        external_id="saa-seed-01",
        title="Sicherheitsmitarbeiter (m/w/d) - Objektschutz",
        description=(
            "Bewachung von Firmengelände und Zugangskontrolle im Schichtdienst.\n\n"
            "Anforderungen:\n"
            "- Sachkundeprüfung nach §34a GewO\n"
            "- Deutschkenntnisse mindestens B2\n"
            "- Mindestens 3 Jahre Berufserfahrung im Sicherheitsdienst\n"
            "- Bereitschaft zum Schichtdienst inkl. Nacht und Wochenende"
        ),
        criteria=["§34a certification", "German B2", "3 years experience", "shift availability"],
        candidates=60,
        pass_rates={"PreScreening": 0.45, "AIVoiceInterview": 0.50, "HumanInterview": 0.60},
        opt_out_rates={"PreScreening": 0.08, "AIVoiceInterview": 0.10},
        review_rates={"PreScreening": 0.70, "AIVoiceInterview": 0.60, "HumanInterview": 1.0},
        # The headline finding: humans disagree with Paul at pre-screening a lot.
        override_rates={"PreScreening": 0.35, "AIVoiceInterview": 0.10, "HumanInterview": 0.0},
        reasons=CLEAN_REASONS,
        demonstrates="High override rate at PreScreening: the AI's hard-criteria calls are being reversed.",
    ),
    JobScenario(
        external_id="saa-seed-02",
        title="Pflegefachkraft (m/w/d) - Intensivstation",
        description=(
            "Pflege und Betreuung auf der Intensivstation eines Akutkrankenhauses.\n\n"
            "Anforderungen:\n"
            "- Abgeschlossene Ausbildung als Pflegefachkraft\n"
            "- Deutschkenntnisse mindestens B2\n"
            "- Mindestens 2 Jahre Erfahrung\n"
            "- Bereitschaft zum Schichtdienst"
        ),
        criteria=["nursing qualification", "German B2", "2 years experience", "shift availability"],
        candidates=60,
        pass_rates={"PreScreening": 0.55, "AIVoiceInterview": 0.55, "HumanInterview": 0.65},
        opt_out_rates={"PreScreening": 0.06, "AIVoiceInterview": 0.08},
        review_rates={"PreScreening": 0.55, "AIVoiceInterview": 0.60, "HumanInterview": 1.0},
        override_rates={"PreScreening": 0.12, "AIVoiceInterview": 0.10, "HumanInterview": 0.0},
        # A quarter of rejections cite criteria that are not in the listing.
        reasons=CLEAN_REASONS + OFF_CRITERIA_REASONS + OFF_CRITERIA_REASONS,
        demonstrates="Rejection reasons citing criteria absent from the job config.",
    ),
    JobScenario(
        external_id="saa-seed-03",
        title="Notfallsanitäter (m/w/d)",
        description=(
            "Notfallrettung im Rettungsdienst einer Großstadt.\n\n"
            "Anforderungen:\n"
            "- Ausbildung als Notfallsanitäter\n"
            "- Führerschein Klasse C1\n"
            "- Deutschkenntnisse mindestens B2\n"
            "- Bereitschaft zum Schichtdienst"
        ),
        criteria=["paramedic qualification", "driving licence C1", "German B2", "shift availability"],
        candidates=60,
        pass_rates={"PreScreening": 0.50, "AIVoiceInterview": 0.55, "HumanInterview": 0.60},
        opt_out_rates={"PreScreening": 0.07, "AIVoiceInterview": 0.09},
        # Configured for human review, but almost nobody reviews. The anomaly is
        # the gap between configuration and behaviour, not the number itself.
        review_rates={"PreScreening": 0.08, "AIVoiceInterview": 0.10, "HumanInterview": 1.0},
        human_in_loop={"PreScreening": "always_on", "AIVoiceInterview": "always_on"},
        override_rates={"PreScreening": 0.30, "AIVoiceInterview": 0.20, "HumanInterview": 0.0},
        reasons=CLEAN_REASONS,
        demonstrates="Step configured for human review but overwhelmingly unreviewed.",
    ),
    JobScenario(
        external_id="saa-seed-04",
        title="Sicherheitskraft (m/w/d) - Veranstaltungsschutz",
        description=(
            "Einlasskontrolle und Ordnerdienst bei Großveranstaltungen.\n\n"
            "Anforderungen:\n"
            "- Sachkundeprüfung nach §34a GewO\n"
            "- Deutschkenntnisse mindestens B2\n"
            "- Bereitschaft zu Wochenendarbeit"
        ),
        criteria=["§34a certification", "German B2", "weekend availability"],
        candidates=60,
        pass_rates={"PreScreening": 0.40, "AIVoiceInterview": 0.45, "HumanInterview": 0.60},
        # Heavy opt-out at the voice interview: a channel problem, not an
        # accuracy problem. Folding these into "rejected" would be wrong.
        opt_out_rates={"PreScreening": 0.10, "AIVoiceInterview": 0.30},
        review_rates={"PreScreening": 0.50, "AIVoiceInterview": 0.50, "HumanInterview": 1.0},
        override_rates={"PreScreening": 0.15, "AIVoiceInterview": 0.12, "HumanInterview": 0.0},
        reasons=RAGGED_REASONS,
        malformed_decisions=4,
        demonstrates=(
            "Ragged free-text reasons in two languages (forces keyword buckets), "
            "a high opt-out cluster at the voice interview, and malformed decision values."
        ),
    ),
    JobScenario(
        external_id="saa-seed-05",
        title="Gesundheits- und Krankenpfleger (m/w/d) - Allgemeinstation",
        description=(
            "Grund- und Behandlungspflege auf der Allgemeinstation.\n\n"
            "Anforderungen:\n"
            "- Abgeschlossene Ausbildung in der Gesundheits- und Krankenpflege\n"
            "- Deutschkenntnisse mindestens B2\n"
            "- Bereitschaft zum Schichtdienst"
        ),
        criteria=["nursing qualification", "German B2", "shift availability"],
        candidates=60,
        pass_rates={"PreScreening": 0.60, "AIVoiceInterview": 0.60, "HumanInterview": 0.70},
        opt_out_rates={"PreScreening": 0.04, "AIVoiceInterview": 0.05},
        # Fully reviewed, humans almost always agree, fixed vocabulary.
        review_rates={"PreScreening": 0.95, "AIVoiceInterview": 0.95, "HumanInterview": 1.0},
        override_rates={"PreScreening": 0.04, "AIVoiceInterview": 0.03, "HumanInterview": 0.0},
        reasons=CLEAN_REASONS,
        demonstrates=(
            "Control job. Clean vocabulary, near-total review coverage, low override. "
            "The analyzer must report NO anomalies here -- this is what makes the "
            "findings on the other four credible."
        ),
    ),
]
