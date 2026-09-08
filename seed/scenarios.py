"""Seed scenario definitions.

The dev account ships no data and its screening agent never concludes -- across
13 candidates and every pipeline configuration we could reach, `PaulDecision`
stayed null (see README, "Data provenance"). So the decision records are
authored here, written through the same endpoints the platform itself uses, and
the human review decisions on top of them are genuine.

That makes this file the ground truth for the whole exercise: every insight and
anomaly the analyzer claims to detect is planted here first, and the seeder
emits a manifest of what it planted so a test can assert the analyzer recovers
it.

Two jobs, deliberately different, so that per-job and per-step views disagree
and the (job, step) breakdown earns its place:

  saa-seed-21  security   -- the problem job. High override at pre-screening,
                             ragged bilingual rejection text, an opt-out
                             cluster at the voice interview, malformed values.
  saa-seed-22  healthcare -- the control. Fixed vocabulary, near-total review
                             coverage, low override. Its only fault is a
                             handful of rejections citing criteria that are not
                             in its listing.

Domain follows the platform's actual usage: security and healthcare roles with
hard, checkable criteria and high application volume.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: Step categories that produce an AI screening decision on this pipeline.
#: Discovered at runtime from each job's steps; this is the seeding target.
DECISION_STEPS = ("PreScreening", "AIVoiceInterview", "HumanInterview")


@dataclass(frozen=True)
class ReasonSpec:
    """One rejection reason, with the criterion it is supposed to map to."""

    text: str
    #: Bucket the analyzer should sort this into (tier-2 keyword buckets).
    bucket: str
    #: False when the reason cites something the job config does not contain --
    #: this is what the "reason matches no criterion" anomaly must catch.
    in_job_criteria: bool = True


@dataclass
class JobScenario:
    external_id: str
    title: str
    description: str
    #: Hard criteria, mirrored in the description.
    criteria: list[str]
    #: The qualification the listing demands, in German (real mode only).
    certificate: str
    #: Field name used in cover letters (real mode only).
    field_label: str
    #: Years of experience the listing requires.
    required_years: int
    candidates: int
    #: Fraction of candidates concluded positive at each step.
    pass_rates: dict[str, float] = field(default_factory=dict)
    #: Fraction who opt out instead -- candidate withdrawal, not rejection.
    opt_out_rates: dict[str, float] = field(default_factory=dict)
    #: Fraction of decisions a human reviewed at all.
    review_rates: dict[str, float] = field(default_factory=dict)
    #: Of reviewed decisions, the fraction where the human overrode Paul.
    override_rates: dict[str, float] = field(default_factory=dict)
    #: Rejection reasons drawn on for negative decisions.
    reasons: list[ReasonSpec] = field(default_factory=list)
    #: Records given a deliberately unmappable PaulDecision value.
    malformed_decisions: int = 0
    #: HumanInLoop per step category. NOTE: per-job agent config is immutable
    #: (`422 job agent update not allowed`), so this only takes effect if the
    #: template was created with it. Left empty; see README.
    human_in_loop: dict[str, str] = field(default_factory=dict)
    #: We author the decisions; the agent does not run.
    authored: bool = True
    #: What this job demonstrates; copied into the manifest.
    demonstrates: str = ""


# Reason vocabularies -------------------------------------------------------
# Fixed vocabulary: the tier-1 case, where exact counting is enough.
CLEAN_REASONS = [
    ReasonSpec("Missing required certification", "certification"),
    ReasonSpec("Insufficient years of experience", "experience"),
    ReasonSpec("German language level below B2", "language"),
    ReasonSpec("Not available for shift work", "availability"),
]

# Ragged: same four concepts, free text, two languages, inconsistent casing.
# Exact counting fragments this into ~12 buckets of 1-2; keyword buckets
# recover the real distribution. This is what forces tier 2.
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
    # Outside every bucket on purpose: proves `other` is real, and that a large
    # `other` share is reported as "the buckets are incomplete".
    ReasonSpec("Profil wirkt insgesamt nicht überzeugend", "other"),
    ReasonSpec("general fit concerns", "other"),
]

# Cites a criterion the listing does not contain -- the anomaly case.
OFF_CRITERIA_REASONS = [
    ReasonSpec("No driving licence class C provided", "other", in_job_criteria=False),
    ReasonSpec("Candidate has no prior management experience", "other", in_job_criteria=False),
]


SCENARIOS: list[JobScenario] = [
    JobScenario(
        external_id="saa-seed-21",
        title="Sicherheitsmitarbeiter (m/w/d) - Objektschutz",
        description=(
            "Bewachung von Firmengelände und Zugangskontrolle im Schichtdienst.\n\n"
            "Zwingende Anforderungen:\n"
            "- Sachkundeprüfung nach §34a GewO\n"
            "- Deutschkenntnisse mindestens B2\n"
            "- Mindestens 3 Jahre Berufserfahrung im Sicherheitsdienst\n"
            "- Bereitschaft zum Schichtdienst inkl. Nacht und Wochenende"
        ),
        criteria=["§34a certification", "German B2", "3 years experience", "shift availability"],
        certificate="die Sachkundeprüfung nach §34a GewO",
        field_label="Sicherheitsdienst",
        required_years=3,
        candidates=20,
        pass_rates={"PreScreening": 0.45, "AIVoiceInterview": 0.50, "HumanInterview": 0.60},
        # Heavy opt-out at the voice interview: a channel problem, not an
        # accuracy problem. Folding these into "rejected" would be wrong.
        opt_out_rates={"PreScreening": 0.08, "AIVoiceInterview": 0.30},
        review_rates={"PreScreening": 0.70, "AIVoiceInterview": 0.55, "HumanInterview": 1.0},
        # The headline finding: humans reverse the AI's hard-criteria calls.
        override_rates={"PreScreening": 0.35, "AIVoiceInterview": 0.10, "HumanInterview": 0.0},
        reasons=RAGGED_REASONS,
        malformed_decisions=3,
        demonstrates=(
            "The problem job. High override rate at pre-screening, ragged bilingual "
            "rejection text that forces keyword bucketing, an opt-out cluster at the "
            "voice interview, and malformed decision values for the data-quality section."
        ),
    ),
    JobScenario(
        external_id="saa-seed-22",
        title="Pflegefachkraft (m/w/d) - Intensivstation",
        description=(
            "Pflege und Betreuung auf der Intensivstation eines Akutkrankenhauses.\n\n"
            "Zwingende Anforderungen:\n"
            "- Abgeschlossene Ausbildung als Pflegefachkraft\n"
            "- Deutschkenntnisse mindestens B2\n"
            "- Mindestens 2 Jahre Berufserfahrung in der Pflege\n"
            "- Bereitschaft zum Schichtdienst"
        ),
        criteria=["nursing qualification", "German B2", "2 years experience", "shift availability"],
        certificate="eine abgeschlossene Ausbildung als Pflegefachkraft",
        field_label="Pflege",
        required_years=2,
        candidates=20,
        pass_rates={"PreScreening": 0.60, "AIVoiceInterview": 0.60, "HumanInterview": 0.70},
        opt_out_rates={"PreScreening": 0.05, "AIVoiceInterview": 0.08},
        review_rates={"PreScreening": 0.95, "AIVoiceInterview": 0.90, "HumanInterview": 1.0},
        override_rates={"PreScreening": 0.05, "AIVoiceInterview": 0.05, "HumanInterview": 0.0},
        # Fixed vocabulary, but a quarter of rejections cite criteria that are
        # not in this listing at all.
        reasons=CLEAN_REASONS + OFF_CRITERIA_REASONS,
        demonstrates=(
            "The control job. Fixed vocabulary, near-total review coverage, low override "
            "-- the analyzer must NOT flag it for those. Its one real fault is rejections "
            "citing criteria absent from the listing, which the anomaly check should catch."
        ),
    ),
]
