"""Rejection-reason aggregation, cheapest tier first.

Tier 1 -- exact counting over a normalised string. Correct and free when the
agent uses a fixed vocabulary. It is tried first and reported honestly: if the
distinct-value count is close to the rejection count, the vocabulary is ragged
and tier 1 is not telling you anything.

Tier 2 -- keyword buckets from a config file. Recovers the real distribution
when the same concept is phrased a dozen ways, including across languages.
Anything unmatched lands in `other`, and a large `other` share is reported as
"the buckets are incomplete" rather than hidden.

Tier 3 -- an LLM classifier. NOT implemented, deliberately. Tier 2 handles the
observed data, and a model in this path would make the numbers unreproducible
and unexplainable for no gain. See README, "Why there is no LLM".

The bucket config is JSON rather than YAML so the tool keeps zero runtime
dependencies; the structure is the same either way.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

DEFAULT_CONFIG = "reason_buckets.json"
OTHER = "other"


def normalize(text: str) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace.

    Accent folding matters here: the same reason appears as "Sachkundeprüfung"
    and "Sachkundepruefung", and those must not count as two reasons.
    """
    folded = unicodedata.normalize("NFKD", str(text or ""))
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    folded = folded.replace("ß", "ss").lower()
    folded = re.sub(r"[^a-z0-9§]+", " ", folded)
    return re.sub(r"\s+", " ", folded).strip()


@dataclass
class BucketConfig:
    """bucket name -> keywords. A reason matches if any keyword is a substring."""

    buckets: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str = DEFAULT_CONFIG) -> "BucketConfig":
        if not os.path.exists(path):
            return cls(buckets={})
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
        buckets = raw.get("buckets") if isinstance(raw, dict) else raw
        if not isinstance(buckets, dict):
            return cls(buckets={})
        return cls(
            buckets={
                str(name): [normalize(k) for k in keywords if str(k).strip()]
                for name, keywords in buckets.items()
                if isinstance(keywords, list)
            }
        )

    def classify(self, text: str) -> str:
        normalized = normalize(text)
        if not normalized:
            return OTHER
        for name, keywords in self.buckets.items():
            if any(keyword and keyword in normalized for keyword in keywords):
                return name
        return OTHER


@dataclass
class ReasonSummary:
    """What the rejection explanations look like, by both tiers."""

    total: int = 0
    #: Tier 1: distinct normalised strings and their counts.
    exact: Counter = field(default_factory=Counter)
    #: Tier 2: bucket -> count.
    buckets: Counter = field(default_factory=Counter)
    #: One example of the raw text per bucket, so a reader can sanity-check it.
    examples: dict[str, str] = field(default_factory=dict)
    #: Reasons with no explanation text at all.
    missing: int = 0

    @property
    def distinct_exact(self) -> int:
        return len(self.exact)

    @property
    def vocabulary_is_fixed(self) -> bool:
        """True when exact counting is genuinely informative.

        A fixed vocabulary repeats: few distinct values across many rejections.
        The threshold is a judgement call, stated rather than hidden -- at most
        one distinct string per two rejections, and no more than 12 in total.
        Below three rejections there is nothing to judge, so we say nothing.
        """
        if self.total < 3:
            return True
        return (self.distinct_exact / self.total) <= 0.5 and self.distinct_exact <= 12

    @property
    def other_share(self) -> float:
        if not self.total:
            return 0.0
        return self.buckets.get(OTHER, 0) / self.total

    def top(self, limit: int = 5) -> list[tuple[str, int]]:
        return self.buckets.most_common(limit)


def summarize(explanations: list[str], config: BucketConfig) -> ReasonSummary:
    summary = ReasonSummary(total=len(explanations))
    for text in explanations:
        if not str(text or "").strip():
            summary.missing += 1
            continue
        summary.exact[normalize(text)] += 1
        bucket = config.classify(text)
        summary.buckets[bucket] += 1
        summary.examples.setdefault(bucket, str(text).strip())
    return summary


def cites_no_criterion(explanation: str, criteria_text: str, config: BucketConfig) -> bool:
    """Does this reason reference something the step's criteria never mention?

    Compared at bucket level, not word level. The agent's configured criteria
    are prose, so exact matching would flag almost everything; asking "does the
    concept this reason belongs to appear anywhere in the criteria" is the
    weaker claim we can actually support.

    Conservative by design: it returns False whenever criteria are missing or
    the reason lands in `other`, because an unmatched reason is a gap in our
    buckets at least as often as a gap in the config.

    KNOWN LIMITATION, stated in the report: matching is at concept level. A
    rejection citing a *different specific requirement inside a bucket the job
    does use* -- a driving licence on a role that asks for a nursing
    qualification, both `certification` -- will not be flagged. Catching that
    reliably needs structured criteria or a classifier; see README next steps.
    """
    if not str(criteria_text or "").strip() or not str(explanation or "").strip():
        return False
    bucket = config.classify(explanation)
    if bucket == OTHER:
        return False
    criteria_bucket_keywords = config.buckets.get(bucket) or []
    normalized_criteria = normalize(criteria_text)
    return not any(keyword and keyword in normalized_criteria for keyword in criteria_bucket_keywords)
