"""Seed scenario definitions.

The dev account ships no data, so we create it. Two modes:

REAL (default) -- we author only the *candidates*. Their cover letters state
checkable claims about the job's mandatory requirements, and the live agent
makes the screening decisions. A human then reviews a sample in the UI, which
produces genuine AssignerDecision values. This is the primary data set: real
agent vocabulary, real field population, real quirks.

AUTHORED -- we write the decision records ourselves. Used only for cases the
agent will not produce on demand (malformed decision values, opt-outs that
require real timeouts). Clearly marked in the manifest so the report can say
which rows came from where.

Domain follows the platform's actual usage: security and healthcare roles with
hard, checkable criteria and high application volume.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: Step categories that produce an AI screening decision on this pipeline.
#: Discovered at runtime from each job's steps; this is the seeding target.
DECISION_STEPS = ("PreScreening", "AIVoiceInterview", "HumanInterview")


@dataclass
class JobScenario:
    external_id: str
    title: str
    description: str
    #: Hard criteria, mirrored in the description and used to shape profiles.
    criteria: list[str]
    #: The certificate/qualification the listing demands, in German.
    certificate: str
    #: Field name used in cover letters ("im Bereich ...").
    field_label: str
    #: Years of experience the listing requires.
    required_years: int
    candidates: int
    #: What this job is meant to demonstrate; copied into the manifest.
    demonstrates: str = ""
    #: Real mode: the agent decides. Authored mode: we write the records.
    authored: bool = False
    #: Authored mode only -- decision values that must not normalise, so the
    #: data-quality section has real content.
    malformed_decisions: int = 0
    #: HumanInLoop override per step category, applied after pipeline init.
    human_in_loop: dict[str, str] = field(default_factory=dict)


SCENARIOS: list[JobScenario] = [
    JobScenario(
        external_id="saa-seed-11",
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
        demonstrates=(
            "Security role with four hard criteria. Candidate profiles vary so that "
            "roughly 40% meet every requirement, 40% fail exactly one, 20% fail two."
        ),
    ),
    JobScenario(
        external_id="saa-seed-12",
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
        # Second job exists so the report has more than one row to compare, and
        # so per-step numbers can be shown as a roll-up over differing jobs
        # rather than a single job's numbers relabelled.
        demonstrates=(
            "Healthcare role with a different requirement mix, so job-level and "
            "step-level views differ and the (job, step) breakdown earns its place."
        ),
    ),
]
