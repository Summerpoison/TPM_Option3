"""Read-only check of the pipeline template the seeder builds its jobs from.

The seeder initialises every seeded job from one pipeline template -- the one
in PAULSJOB_PIPELINE_TEMPLATE_ID, or else the one named "SAA Seed Pipeline" --
and writes decisions to its steps by category. This script finds that
template, lists its steps with their agents' HumanInLoop setting, and says
whether every category the seeder writes to is present.

A template's steps and agents are not part of the template response. Each
comes from its own endpoint, and agents are listed per step:

    GET /recruiting/job-step-templates/pipelines/{id}/steps
    GET /recruiting/job-step-templates/pipelines/{id}/steps/{step}/agents

Run this after building the template in the UI:

    py discover.py

Read-only. Writes nothing.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Iterable

from analyzer.client import ApiError, PaulsjobClient, path_segment
from analyzer.config import Settings
from seed.scenarios import DECISION_STEPS
from seed.seeder import Seeder

PIPELINES = "/recruiting/job-step-templates/pipelines"


@dataclass(frozen=True)
class TemplateStep:
    """One step of a pipeline template, and what its agents are set to."""

    order: int
    category: str
    name: str
    #: Number of agents on the step; None when the agents could not be read.
    agents: int | None
    #: HumanInLoop.Setting of the step's agents, e.g. "always_off"; "" if none.
    human_in_loop: str = ""


def _as_list(payload: object, key: str) -> list[dict]:
    """Pull a list of objects out of a response whose shape we do not fully trust."""
    value = payload.get(key) if isinstance(payload, dict) else payload
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def list_templates(client: PaulsjobClient) -> list[dict]:
    return _as_list(client.get(PIPELINES, PerPage=50), "PipelineTemplates")


def pick_template(
    templates: Iterable[dict], *, template_id: str = "", name: str = Seeder.TEMPLATE_NAME
) -> dict | None:
    """The template the seeder will use: the configured id wins, else the one with its name."""
    for template in templates:
        if template_id and str(template.get("ID")) == template_id:
            return template
        if not template_id and template.get("Name") == name:
            return template
    return None


def template_steps(client: PaulsjobClient, template_id: str) -> list[TemplateStep]:
    """A template's steps in pipeline order, each with its agents' HumanInLoop setting.

    One request for the steps, then one per step for its agents. A step whose
    agents cannot be read is still listed, so a broken agent call cannot make
    a step look missing.
    """
    base = f"{PIPELINES}/{path_segment(template_id)}/steps"
    steps: list[TemplateStep] = []
    for raw in _as_list(client.get(base), "JobStepTemplates"):
        if not raw.get("ID"):
            continue
        category = raw.get("Category") or ""
        if isinstance(category, dict):
            category = category.get("ID") or ""
        order = raw.get("OrderIndex")
        try:
            agents = _as_list(client.get(f"{base}/{path_segment(raw['ID'])}/agents"), "JobStepAgentTemplates")
        except ApiError as exc:
            print(f"  could not read agents on step '{raw.get('Name')}': {str(exc).splitlines()[0]}")
            agent_count, human_in_loop = None, ""
        else:
            # HumanInLoop is an object; the setting lives in its `Setting` field.
            settings = {
                str(loop.get("Setting") or "")
                for loop in (a.get("HumanInLoop") for a in agents)
                if isinstance(loop, dict)
            }
            agent_count, human_in_loop = len(agents), ",".join(sorted(settings - {""}))
        steps.append(
            TemplateStep(
                order=order if isinstance(order, int) else 10**6,
                category=str(category),
                name=str(raw.get("Name") or ""),
                agents=agent_count,
                human_in_loop=human_in_loop,
            )
        )
    return sorted(steps, key=lambda s: s.order)


def missing_categories(steps: Iterable[TemplateStep], wanted: Iterable[str] = DECISION_STEPS) -> list[str]:
    """The categories the seeder writes to that no step of the template has."""
    present = {step.category for step in steps}
    return [category for category in wanted if category not in present]


def main() -> int:
    settings = Settings.from_env()
    client = PaulsjobClient(settings.base_url, settings.api_key)
    template_id = os.environ.get("PAULSJOB_PIPELINE_TEMPLATE_ID", "").strip()

    try:
        templates = list_templates(client)
    except ApiError as exc:
        print(f"Could not list pipeline templates.\n  {exc}", file=sys.stderr)
        return 2

    print("=== Pipeline templates on this account ===")
    if not templates:
        print("  none found")
    for template in templates:
        print(f"  {template.get('Name', '?')}  (ID={template.get('ID', '?')})")

    template = pick_template(templates, template_id=template_id)
    if template is None:
        looked_for = (
            f"the template with ID {template_id} (PAULSJOB_PIPELINE_TEMPLATE_ID)"
            if template_id else f"a template named {Seeder.TEMPLATE_NAME!r}"
        )
        print(
            f"\nThe seeder uses {looked_for}, and there is none on this account.\n"
            "  Create it in the UI (README: 'Reproducing the test data'), or set\n"
            "  PAULSJOB_PIPELINE_TEMPLATE_ID to an existing template."
        )
        print(f"\nRequests: {client.stats.requests}")
        return 1

    print(f"\n=== Template the seeder uses: {template.get('Name')} ===")
    print(f"  chosen by {'PAULSJOB_PIPELINE_TEMPLATE_ID' if template_id else 'its name'}")
    try:
        steps = template_steps(client, str(template.get("ID")))
    except ApiError as exc:
        print(f"  could not read its steps: {exc}", file=sys.stderr)
        return 2
    for step in steps:
        agents = "?" if step.agents is None else step.agents
        loop = f"  HumanInLoop={step.human_in_loop}" if step.human_in_loop else ""
        print(f"  {step.order:>3}. {step.category:<22} {step.name[:32]:<32} agents={agents}{loop}")

    print("\n=== What the seeder needs ===")
    missing = missing_categories(steps)
    for category in DECISION_STEPS:
        print(f"  {'-- ' if category in missing else 'OK '}{category}")
    if missing:
        print(
            f"\n  {template.get('Name')!r} has no step for: {', '.join(missing)}.\n"
            "  The seeder writes decisions to these categories (DECISION_STEPS in\n"
            "  seed/scenarios.py). Add the steps in the UI, or set\n"
            "  PAULSJOB_PIPELINE_TEMPLATE_ID to a template that has them."
        )
    else:
        print("\n  Every step the seeder writes to is present.")

    print(f"\nRequests: {client.stats.requests}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
