"""discover.py tests. The client answers from the committed fixtures, so no network.

The fixtures are the real responses for the SAA Seed Pipeline. discover.py once
reported that template as missing every step the seeder needs, because it
looked for the steps inside the template response; these tests pin it to the
endpoints the steps and agents actually come from.
"""
import json
from pathlib import Path

from analyzer.client import HttpError
from discover import TemplateStep, missing_categories, pick_template, template_steps
from seed.scenarios import DECISION_STEPS
from seed.seeder import Seeder

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


TEMPLATE = load("pipeline_template.json")
STEPS = load("pipeline_steps.json")
#: Keyed by step category; each value is that step's list of agents.
AGENTS = load("pipeline_agents.json")
BASE = f"/recruiting/job-step-templates/pipelines/{TEMPLATE['ID']}/steps"


class FakeClient:
    """Serves the steps and per-step agents endpoints, and 404s everything else."""

    def __init__(self, fail_agents_for=()):
        self.fail_agents_for = set(fail_agents_for)

    def get(self, path, **params):
        if path == BASE:
            return STEPS
        for step in STEPS["JobStepTemplates"]:
            if path == f"{BASE}/{step['ID']}/agents":
                if step["Category"] in self.fail_agents_for:
                    raise HttpError(500, "internal error", path)
                return {"JobStepAgentTemplates": AGENTS.get(step["Category"], [])}
        raise HttpError(404, "404 page not found", path)


class TestTemplateSteps:
    def test_steps_come_from_the_steps_endpoint_in_pipeline_order(self):
        steps = template_steps(FakeClient(), TEMPLATE["ID"])
        expected = sorted(STEPS["JobStepTemplates"], key=lambda s: s["OrderIndex"])
        assert [s.category for s in steps] == [s["Category"] for s in expected]

    def test_the_committed_template_has_every_step_the_seeder_needs(self):
        """The original bug: this template was reported as missing all three."""
        assert missing_categories(template_steps(FakeClient(), TEMPLATE["ID"])) == []

    def test_human_in_loop_is_read_from_its_setting_field(self):
        steps = {s.category: s for s in template_steps(FakeClient(), TEMPLATE["ID"])}
        assert steps["HumanInterview"].agents == 1
        assert steps["HumanInterview"].human_in_loop == AGENTS["HumanInterview"][0]["HumanInLoop"]["Setting"]
        assert steps["New"].agents == 0
        assert steps["New"].human_in_loop == ""

    def test_unreadable_agents_do_not_make_a_step_look_missing(self):
        client = FakeClient(fail_agents_for={"PreScreening"})
        steps = template_steps(client, TEMPLATE["ID"])
        prescreening = next(s for s in steps if s.category == "PreScreening")
        assert prescreening.agents is None
        assert missing_categories(steps) == []


class TestMissingCategories:
    def test_lists_absent_categories_in_the_seeders_order(self):
        steps = [TemplateStep(order=1, category="PreScreening", name="Vorauswahl", agents=1)]
        assert missing_categories(steps) == [c for c in DECISION_STEPS if c != "PreScreening"]


class TestPickTemplate:
    TEMPLATES = [
        {"ID": "other-id", "Name": "Other Pipeline"},
        {"ID": TEMPLATE["ID"], "Name": Seeder.TEMPLATE_NAME},
    ]

    def test_picks_the_seeders_template_by_name(self):
        assert pick_template(self.TEMPLATES)["ID"] == TEMPLATE["ID"]

    def test_a_configured_id_wins_over_the_name(self):
        assert pick_template(self.TEMPLATES, template_id="other-id")["Name"] == "Other Pipeline"

    def test_none_when_the_template_does_not_exist(self):
        assert pick_template(self.TEMPLATES[:1]) is None
        assert pick_template(self.TEMPLATES, template_id="no-such-id") is None
