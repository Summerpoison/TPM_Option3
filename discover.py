"""Read-only discovery of the pipeline templates configured on this account.

The seeder assumes a pipeline containing PreScreening, AIVoiceInterview and
HumanInterview steps. That assumption has to be checked against the account's
actual template rather than the spec's examples, because the scenarios target
step categories by name.

Run this after building a pipeline template in the UI:

    py discover.py

Read-only. Writes nothing.
"""
from __future__ import annotations

import json
import sys

from analyzer.client import ApiError, PaulsjobClient
from analyzer.config import Settings


def show_pipeline(name: str, payload: object) -> list[str]:
    """Print a template's steps and return the step categories it contains."""
    if not isinstance(payload, dict):
        print(f"  (unexpected shape: {type(payload).__name__})")
        return []

    steps = None
    for key in ("Steps", "JobStepTemplates", "StepTemplates", "PipelineSteps"):
        value = payload.get(key)
        if isinstance(value, list):
            steps = value
            break
    if steps is None:
        print(f"  no step list found; keys present: {sorted(payload)}")
        return []

    categories: list[str] = []
    for step in sorted(steps, key=lambda s: s.get("OrderIndex", 0) if isinstance(s, dict) else 0):
        if not isinstance(step, dict):
            continue
        category = step.get("Category") or step.get("CategoryID")
        if isinstance(category, dict):
            category = category.get("ID")
        category = str(category) if category else "?"
        categories.append(category)
        agents = step.get("Agents") or step.get("StepAgents") or []
        human_in_loop = ""
        if isinstance(agents, list) and agents:
            loops = {a.get("HumanInLoop") for a in agents if isinstance(a, dict)}
            human_in_loop = f"  HumanInLoop={','.join(sorted(str(x) for x in loops if x))}"
        print(
            f"  {step.get('OrderIndex', '?'):>3}. {category:<22}"
            f" {str(step.get('Name', ''))[:32]:<32}"
            f" agents={len(agents) if isinstance(agents, list) else 0}{human_in_loop}"
        )
    return categories


def main() -> int:
    settings = Settings.from_env()
    client = PaulsjobClient(settings.base_url, settings.api_key)

    print("=== Company default pipeline template ===")
    default_categories: list[str] = []
    try:
        default = client.get("/recruiting/job-step-templates/pipelines/default")
        print(f"  name: {default.get('Name') if isinstance(default, dict) else '?'}")
        print(f"  id:   {default.get('ID') if isinstance(default, dict) else '?'}")
        default_categories = show_pipeline("default", default)
    except ApiError as exc:
        # Expected when no default is configured -- which is exactly why
        # steps/init with AutoInit:true cannot be relied on.
        print(f"  none available: {exc}")

    print("\n=== All pipeline templates ===")
    try:
        listing = client.get("/recruiting/job-step-templates/pipelines", PerPage=50)
        templates = (listing or {}).get("PipelineTemplates") or [] if isinstance(listing, dict) else []
        if not templates:
            print("  none found")
        for template in templates:
            if not isinstance(template, dict):
                continue
            print(f"\n  {template.get('Name', '?')}  (ID={template.get('ID', '?')})")
            try:
                full = client.get(f"/recruiting/job-step-templates/pipelines/{template.get('ID')}")
                show_pipeline(str(template.get("Name")), full)
            except ApiError as exc:
                print(f"    could not expand: {exc}")
    except ApiError as exc:
        print(f"  listing failed: {exc}")

    print("\n=== What the seeder needs ===")
    wanted = ["PreScreening", "AIVoiceInterview", "HumanInterview"]
    for category in wanted:
        mark = "OK " if category in default_categories else "-- "
        print(f"  {mark}{category}")
    missing = [c for c in wanted if c not in default_categories]
    if missing:
        print(
            "\n  The default template does not contain: " + ", ".join(missing) + "\n"
            "  Either add these steps to the template, or set PAULSJOB_PIPELINE_TEMPLATE_ID\n"
            "  to the template you built and adjust seed/scenarios.py to its categories."
        )
    print(f"\nRequests: {client.stats.requests}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
