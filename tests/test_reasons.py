from analyzer.reasons import BucketConfig, cites_no_criterion, normalize, summarize

CONFIG = BucketConfig(buckets={
    "certification": [normalize(k) for k in ("certificate", "sachkunde", "34a", "zertifikat")],
    "language": [normalize(k) for k in ("german", "deutsch", "sprachniveau", "b2")],
    "experience": [normalize(k) for k in ("experience", "erfahrung", "jahre")],
    "salary": [normalize(k) for k in ("salary", "gehalt")],
})


class TestNormalize:
    def test_folds_accents_and_case(self):
        assert normalize("Sachkundeprüfung") == normalize("SACHKUNDEPRUFUNG")

    def test_strips_punctuation_and_collapses_space(self):
        assert normalize("insufficient  german,  estimated A2!") == "insufficient german estimated a2"

    def test_keeps_section_sign(self):
        """§34a is the name of the qualification; dropping it loses the meaning."""
        assert "§34a" in normalize("no §34a certificate provided")

    def test_handles_none_and_empty(self):
        assert normalize(None) == "" and normalize("") == ""


class TestBuckets:
    def test_same_concept_in_two_languages_lands_together(self):
        assert CONFIG.classify("Deutschkenntnisse nicht ausreichend") == "language"
        assert CONFIG.classify("insufficient german, estimated A2") == "language"

    def test_unmatched_goes_to_other(self):
        assert CONFIG.classify("general fit concerns") == "other"

    def test_empty_goes_to_other(self):
        assert CONFIG.classify("") == "other"

    def test_missing_config_file_yields_no_buckets(self):
        empty = BucketConfig.load("does-not-exist.json")
        assert empty.buckets == {} and empty.classify("anything") == "other"


class TestVocabularyDetection:
    def test_fixed_vocabulary_is_recognised(self):
        summary = summarize(["Missing required certification"] * 10 + ["German level below B2"] * 6, CONFIG)
        assert summary.vocabulary_is_fixed
        assert summary.distinct_exact == 2

    def test_ragged_vocabulary_is_recognised(self):
        summary = summarize([f"a distinct reason number {i}" for i in range(12)], CONFIG)
        assert not summary.vocabulary_is_fixed

    def test_too_few_to_judge(self):
        assert summarize(["one", "two"], CONFIG).vocabulary_is_fixed

    def test_buckets_recover_what_exact_counting_fragments(self):
        ragged = [
            "Bewerber hat keine gültige Sachkundeprüfung nach §34a",
            "no §34a certificate provided",
            "Sachkundenachweis fehlt",
            "Zertifikat nicht vorhanden",
        ]
        summary = summarize(ragged, CONFIG)
        # Exact counting sees four unrelated strings; buckets see one problem.
        assert summary.distinct_exact == 4
        assert summary.buckets["certification"] == 4

    def test_missing_explanations_are_counted_not_bucketed(self):
        summary = summarize(["", "   ", "Missing required certification"], CONFIG)
        assert summary.missing == 2
        assert sum(summary.buckets.values()) == 1

    def test_other_share(self):
        summary = summarize(["general fit concerns", "vibes", "Missing certificate"], CONFIG)
        assert summary.other_share == 2 / 3


class TestCitesNoCriterion:
    CRITERIA = ("Mindestens eine zwingende Anforderung ist nicht erfüllt: fehlendes "
                "Zertifikat, unzureichendes Sprachniveau, zu geringe Erfahrung.")

    def test_flags_a_concept_the_criteria_never_mention(self):
        assert cites_no_criterion("Gehaltsvorstellung zu hoch", self.CRITERIA, CONFIG)

    def test_does_not_flag_a_concept_the_criteria_do_mention(self):
        assert not cites_no_criterion("Zertifikat fehlt", self.CRITERIA, CONFIG)
        assert not cites_no_criterion("Sprachniveau zu niedrig", self.CRITERIA, CONFIG)

    def test_does_not_flag_unbucketed_reasons(self):
        """An `other` reason is a gap in our buckets as often as in the config."""
        assert not cites_no_criterion("general fit concerns", self.CRITERIA, CONFIG)

    def test_says_nothing_when_criteria_are_missing(self):
        assert not cites_no_criterion("Gehaltsvorstellung zu hoch", "", CONFIG)

    def test_known_limitation_same_bucket_different_requirement(self):
        """Documented blind spot, pinned so it cannot regress silently.

        A driving licence on a role asking for a nursing qualification is real
        drift, but both are `certification`, so bucket-level matching misses it.
        """
        criteria = "Abgeschlossene Ausbildung, Zertifikat erforderlich"
        assert not cites_no_criterion("No driving licence provided", criteria, CONFIG)


class TestBlankExplanationsDoNotSkewTheDenominators:
    """Rejections with no text are reported separately, not counted as evidence
    that the buckets are fine."""

    def test_other_share_ignores_rejections_with_no_text(self):
        # 2 of the 4 rejections that HAVE text matched nothing -> 50%, not 25%.
        summary = summarize(["general fit concerns", "vibes", "Missing certificate",
                             "German level low", "", "   ", "", ""], CONFIG)
        assert summary.missing == 4
        assert summary.classifiable == 4
        assert summary.other_share == 0.5

    def test_a_pile_of_blanks_cannot_hide_an_incomplete_bucket_config(self):
        summary = summarize(["unmatched reason"] * 4 + [""] * 16, CONFIG)
        assert summary.other_share == 1.0        # every reason with text is unmatched
        assert summary.buckets["other"] == 4

    def test_vocabulary_check_ignores_blanks_too(self):
        summary = summarize(["Missing required certification"] * 4 + [""] * 20, CONFIG)
        assert summary.vocabulary_is_fixed       # 1 distinct across 4 real rejections

    def test_all_blank_is_not_a_division_by_zero(self):
        summary = summarize(["", "  "], CONFIG)
        assert summary.classifiable == 0 and summary.other_share == 0.0
