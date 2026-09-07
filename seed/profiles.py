"""Candidate profile generation for real-agent seeding.

In real mode the seeder does NOT author decisions -- Paul makes them. What it
authors instead is the *input*: candidates whose stated qualifications vary in
controlled ways against the job's mandatory requirements.

This gives a stronger form of ground truth than authored decisions did. We know
what each candidate actually claims, so we can check whether Paul's stated
rejection reason matches the requirement the candidate genuinely fails -- which
is the thing the "reason cites no configured criterion" flag is really about.

Profiles are written in German, matching the listings and the agent prompts.
"""
from __future__ import annotations

import random
from dataclasses import asdict, dataclass

#: German language levels, ordered. The listings require at least B2.
LEVELS = ("A2", "B1", "B2", "C1", "Muttersprache")
REQUIRED_LEVEL_INDEX = LEVELS.index("B2")


@dataclass(frozen=True)
class Profile:
    """What a candidate claims. Recorded in the manifest as ground truth."""

    has_certificate: bool
    german_level: str
    years_experience: int
    shift_available: bool

    def meets(self, *, required_years: int) -> bool:
        return (
            self.has_certificate
            and LEVELS.index(self.german_level) >= REQUIRED_LEVEL_INDEX
            and self.years_experience >= required_years
            and self.shift_available
        )

    def gaps(self, *, required_years: int) -> list[str]:
        """Which mandatory requirements this candidate fails, by bucket name."""
        missing = []
        if not self.has_certificate:
            missing.append("certification")
        if LEVELS.index(self.german_level) < REQUIRED_LEVEL_INDEX:
            missing.append("language")
        if self.years_experience < required_years:
            missing.append("experience")
        if not self.shift_available:
            missing.append("availability")
        return missing


def build_population(rng: random.Random, count: int, *, required_years: int) -> list[Profile]:
    """A mix that gives the agent something to discriminate between.

    Roughly: 40% meet every requirement, 40% fail exactly one, 20% fail two.
    Failing exactly one is the interesting case -- it is where a rejection
    reason can be checked against a known, single cause.
    """
    profiles: list[Profile] = []
    for index in range(count):
        bucket = index % 5
        qualified = Profile(
            has_certificate=True,
            german_level=rng.choice(("B2", "C1", "Muttersprache")),
            years_experience=rng.randint(required_years, required_years + 4),
            shift_available=True,
        )
        if bucket in (0, 1):
            profiles.append(qualified)
            continue
        # Fail one requirement, or two for the last bucket.
        failures = rng.sample(
            ["certification", "language", "experience", "availability"],
            1 if bucket in (2, 3) else 2,
        )
        profiles.append(
            Profile(
                has_certificate="certification" not in failures,
                german_level=rng.choice(("A2", "B1")) if "language" in failures else qualified.german_level,
                years_experience=rng.randint(0, max(0, required_years - 1))
                if "experience" in failures
                else qualified.years_experience,
                shift_available="availability" not in failures,
            )
        )
    rng.shuffle(profiles)
    return profiles


def cover_letter(
    profile: Profile, *, first_name: str, last_name: str, job_title: str, certificate: str, field_label: str
) -> str:
    """A short German application letter stating the profile's facts plainly.

    Deliberately plain and factual rather than persuasive: the point is to give
    the agent checkable claims about the mandatory requirements, not to test
    its prose comprehension.
    """
    cert_line = (
        f"Ich verfüge über {certificate}."
        if profile.has_certificate
        else f"Einen Nachweis über {certificate} habe ich bisher nicht erworben."
    )
    language_line = (
        "Deutsch ist meine Muttersprache."
        if profile.german_level == "Muttersprache"
        else f"Meine Deutschkenntnisse liegen auf dem Niveau {profile.german_level}."
    )
    if profile.years_experience == 0:
        experience_line = f"Berufserfahrung im Bereich {field_label} konnte ich bisher nicht sammeln."
    elif profile.years_experience == 1:
        experience_line = f"Ich habe ein Jahr Berufserfahrung im Bereich {field_label}."
    else:
        experience_line = (
            f"Ich habe {profile.years_experience} Jahre Berufserfahrung im Bereich {field_label}."
        )
    shift_line = (
        "Zum Schichtdienst inklusive Nacht- und Wochenendarbeit bin ich bereit."
        if profile.shift_available
        else "Schichtdienst in der Nacht und am Wochenende kann ich aus familiären Gründen nicht übernehmen."
    )

    return (
        f"Sehr geehrte Damen und Herren,\n\n"
        f"hiermit bewerbe ich mich auf die Stelle als {job_title}.\n\n"
        f"{cert_line} {experience_line} {language_line} {shift_line}\n\n"
        f"Über eine Einladung zu einem Gespräch freue ich mich sehr.\n\n"
        f"Mit freundlichen Grüßen\n{first_name} {last_name}"
    )


def profile_dict(profile: Profile, *, required_years: int) -> dict:
    data = asdict(profile)
    data["meets_all_requirements"] = profile.meets(required_years=required_years)
    data["gaps"] = profile.gaps(required_years=required_years)
    return data
