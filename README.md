# Screening Accuracy Analyzer

PJRI API Challenge — Option 3.

Reads the AI's candidate screening decisions out of the Paul's Job API and turns
them into findings someone can act on: what the agent decided, where humans
disagreed with it, why candidates were rejected, and which steps look
misconfigured.

```bash
python analyze.py --job saa-seed-2
```

Example output: [`examples/report.html`](examples/report.html) ·
[`examples/report.txt`](examples/report.txt) ·
[`examples/report.json`](examples/report.json) ·
[`examples/per_candidate.txt`](examples/per_candidate.txt)

> Every finding in the example output was planted by the seeder and recovered by
> the analyzer. They demonstrate the tool, not the platform — see *Data provenance*.

---

## The problem it solves, and who it's for

**Primary user: the implementation / customer-success person who configured the
pipeline.** They set up the steps, wrote the conclusion criteria and decided
where a human has to approve. They currently have no way to check whether any of
that is doing what they intended. The platform's own surfaces do not answer it —
during this exercise the dashboard reported "0 in pre-screening" directly above a
list of candidates currently in pre-screening.

So the tool answers four questions from the decision records themselves:

| Question | Where it lands |
|---|---|
| Is the agent rejecting more than we expect, and where? | per (job, step) breakdown |
| When a human looks, do they agree? | override rate, with explicit and inferred counts split |
| What is it rejecting people *for*? | rejection reasons, exact or bucketed |
| Is any step behaving differently from how it is configured? | findings section |

Secondary readers: **product**, for whether the agent behaves as designed; and
indirectly **clients**, since a high override rate at pre-screening usually means
either the criteria are too narrow or the listing is.

---

## Setup

Python 3.11+. **No runtime dependencies** — standard library only, so a reviewer
can clone and run it. `pytest` is needed only for the tests.

```bash
cp .env.example .env        # then put your key in .env
python analyze.py --job saa-seed-2
```

`.env`:

```
PAULSJOB_API_KEY=your_api_key_here
PAULSJOB_BASE_URL=https://api.paulsjob.ai/dev/v1
```

> The base URL needs the `/v1`. The brief's example omits it, and every endpoint
> 404s without it.

The key is read from the environment only. It is never logged, never printed and
never placed in a URL — it travels in the `x-company-api-key` header, and request
logging deliberately records method, path and attempt number but never headers.
The client refuses to follow a redirect to any other origin, so the key can only
ever reach the host in `PAULSJOB_BASE_URL`, and that URL must be `https://`
(plain http is accepted only for localhost). Ids and slugs taken from API
responses are URL-encoded before they are placed in a request path.

### Commands

```bash
python analyze.py                      # every job
python analyze.py --job saa-seed-2     # jobs whose external id starts with this
python analyze.py --json out.json      # machine-readable alongside the text
python analyze.py --html report.html   # self-contained HTML report
python analyze.py --candidates         # per-candidate detail, verbatim
python -m pytest tests/ -q             # no network needed
```

Supporting scripts: `probe.py` (connectivity and inventory), `discover.py`
(pipeline templates and their agent config). Both read-only.

---

## Reproducing the test data

The dev account starts empty, so the repo can recreate its own data.

**One manual step first.** A pipeline template must exist, and it cannot be
created reliably from the API — a template is only valid once every
agent-requiring step has an agent with conclusion rules and routing targets, and
`POST /recruiting/jobs/{id}/steps` is documented but returns 404. Create one in
the UI named **`SAA Seed Pipeline`** with these steps:

```
Neu → Vorauswahl → KI-Voice-Interview → Menschliches Interview
    → Teamdiskussion → Ablehnen → Erledigt
```

The exact structure this was built and verified against is committed in
[`fixtures/pipeline_template.json`](fixtures/pipeline_template.json),
[`pipeline_steps.json`](fixtures/pipeline_steps.json) and
[`pipeline_agents.json`](fixtures/pipeline_agents.json), including the
next-step rules. Then:

```bash
python -m seed.seeder --dry-run     # show the plan, write nothing
python -m seed.seeder               # create 2 jobs, 40 candidates, 60 decisions
```

The seeder is deterministic (fixed RNG seed), idempotent per person *and* per
assignment, tags everything `saa-seed-*`, uses `@seed.invalid` addresses that
cannot receive mail, and never deletes anything. It writes
`fixtures/seed_manifest.json` recording what it planted.

---

## Data provenance — read this before trusting any number

**The screening decisions in this dataset were written by the seeder, not by the
platform's AI.** That is not a shortcut; it is what the environment permitted.

Across 13 candidates and every configuration reachable through the API, the
pre-screening agent never concluded. `PaulDecision` stayed null. Verified by
auditing the whole account: of 17 assignment-history records, the 4 carrying a
decision were all ours — traceable to verbatim string literals in
`seed/scenarios.py` — and all 13 left to the agent were null.

Ruled out along the way: the candidate being muted; pipeline misconfiguration;
no CV; creation ordering; and an empty profile (a fully populated one, with the
certificate, language level and years of experience all present, still produced
nothing). Some AI *is* connected — profile extraction on person creation and
job-description processing both run and both consume credits. The component that
makes screening decisions specifically does not.

What this means for the numbers:

* The analyzer is validated for **correctness against known ground truth** — the
  seed manifest says what was planted and the report recovers it.
* It is **not** validated against real agent behaviour, and no finding here
  describes how the agent actually behaves.
* The **rejection-reason vocabulary is ours**, so which aggregation tier a real
  client needs is an open question. The tiering exists precisely so the answer
  can change without touching code.

**The human review decisions are authored too.** `AssignerDecision` values in this
dataset were written by the seeder alongside the AI decisions they refer to. They
are not records of anyone actually clicking approve or reject.

That is worth being explicit about, because it is the one thing that could still
be made real here: reviewing these candidates in the UI would produce genuine
`AssignerDecision` records on top of authored suggestions, and the analyzer reads
them identically. It has not been done, so nothing in the output should be read
as evidence about how real reviewers behave.

---

## Endpoints used

Read:

| Endpoint | Why |
|---|---|
| `POST /recruiting/jobs/search-jobs` | list jobs (the `GET` variant is documented but deleted) |
| `GET /recruiting/jobs/{id}/steps` | pipeline structure |
| `GET /recruiting/jobs/{id}/steps/{step}/agents` | agent config: `HumanInLoop`, conclusion criteria, routing |
| `POST /recruiting/applications/search-applications` | candidates per job |
| `GET /recruiting/{person}/jobs/{id}/steps-assignment-history` | **the core one** — every decision, per step |
| `GET /recruiting/job-step-categories` | which categories can hold a decision-making agent |

The history endpoint is the only place carrying `PaulDecision`,
`PaulDecisionExplanation`, `PaulDecisionSuggestion` and `AssignerDecision`
together, per step. The application search exposes only the *current* step's
decision — the spec says outright that "historical-step conclusions are not
reflected here" — so it is used for enumeration, not analysis.

Write (seeder only): `POST /recruiting/jobs`, `.../hiring-managers`,
`.../steps/init`, `/company/person`, `/recruiting/{person}/applications/`,
`/recruiting/{person}/jobs/{id}/steps/{step}`, `/company/person/{p}/paul-mute-events`.

---

## Assumptions

1. **A step produces a screening decision if its category can hold a
   configurable agent.** Taken from the platform's own metadata rather than a
   hardcoded list: `GET /recruiting/job-step-categories` marks `New`,
   `TeamDiscussion`, `ContractOffer` and `Onboarding` with
   `AllowChangeConfig: false`, so they structurally cannot. `Rejected` and
   `Outreach` are configurable but carry only messaging, so they are treated as
   destinations and used to sanity-check routing, not counted as decisions.
2. **Opt-outs are not rejections.** `NextStepRule.Name` has five values, three of
   them `OPT_OUT_*`. Those candidates withdrew; the AI never judged them.
   Counting them as rejections inflates the rate, so they are a separate bucket
   and the rejection rate's denominator is positive + negative only.
3. **Grouping keys on `StepID`, never on category.** A category is not unique
   within a pipeline — the template used here has two `TeamDiscussion` steps.
   Grouping by category would silently merge two stages.
4. **A candidate can hold several records for one step**, because assignments
   append. The latest record carrying a decision wins; the rest are reported in
   data quality rather than counted twice.
5. **Override rate is measured over reviewed decisions**, not all of them. It
   describes agreement on the slice a human looked at.
6. **`PaulDecision` is tolerantly normalised.** It is typed as a bare string and
   the spec's own examples disagree with its descriptions (`PositiveConclusion`
   vs `PositiveDecision`), so matching is prefix-based after normalisation, and
   anything unmappable is reported rather than guessed at.
7. **Absent and unmappable are different facts.** No decision means "not
   evaluated"; a present-but-unrecognised value is a data-quality finding.

---

## How to read the output

The report is a to-do list with the evidence attached, in that order:

1. **What to do.** One card per job and step, naming the candidates concerned.
   Three kinds of work cover everything the tool finds: review these candidates,
   fix this step's configuration, look at this listing's criteria.
2. **Worth watching.** Patterns on too few decisions to act on, one line each.
3. **The numbers.** Per job and step, the funnel, and rejection reasons per step.
4. **What was skipped or looked wrong**, and what the report cannot tell you.

Priority comes from how many people are affected and how sure we can be, not
from which rule fired. *Act now* needs at least five people affected; *check*
needs at least ten decisions behind the rate; anything smaller is only watched.
So two candidates opting out of seven is a watch item, never a priority — and a
step where 16 of 20 decisions were reviewed does not get told to check its
notifications, because the other 16 prove they work. It gets the four names.

* **Percentages always appear next to their n**, and the reversal rate carries
  its own n because it rests on the reviewed slice, not the row total. Any cell
  below n=10 is marked low confidence rather than dropped — deep pipeline steps
  are small by nature.
* **Review coverage is measured over reviewable decisions.** An opt-out has
  nothing to approve and an unevaluated step has no decision yet, so neither
  counts as a missed review.
* **The by-job view is the primary one.** The per-step view is limited to things
  that belong to the channel rather than the listing — opt-out rate, review
  coverage — because averaging rejection rates across different listings
  describes the mix of jobs, not the stage.
* **The HTML view is the same content, easier to scan.** One self-contained file
  with no CDN, no framework and no JavaScript, so it opens from `file://` with no
  network. The numbers sit in collapsible sections under the action cards; the
  outcome-mix bars carry their counts in adjacent columns, so no value exists only
  as a colour. Its categorical palette is *not* the product's
  UI colours: a green/red pair separates by only 3.3 delta-E under deuteranopia,
  so the four outcome colours were re-stepped and validated for lightness, chroma,
  colour-vision separation and contrast on both light and dark surfaces.
* **Overrides are split into explicit and inferred.** A `RejectPaulDecision` is a
  fact; an independent human decision that happens to contradict the AI's
  suggestion is an inference, and the two are not equally certain.

---

## Why there is no LLM

Tier 1 (exact counting) and tier 2 (keyword buckets in
[`reason_buckets.json`](reason_buckets.json)) handle the observed data, and the
report says which tier it used and why. A classifier in this path would make the
numbers unreproducible and unexplainable in exchange for nothing measurable.

The point where it becomes worth reconsidering is stated in the tech note: if
`other` stays large after a genuine attempt at the buckets, that is evidence the
vocabulary is too varied for keywords — and the fix would still be a
fixed-label classifier feeding the same counting code, not prose generation.

---

## Layout

```
analyzer/
  client.py      transport: auth, pagination, retries, redirects, error taxonomy
  model.py       decision vocabulary and human/AI agreement — pure functions
  fetch.py       API → flat records; knows the endpoints, does no analysis
  reasons.py     rejection-reason aggregation, tiered
  analysis.py    cells, roll-ups, anomalies — pure functions
  report.py      text, JSON and per-candidate rendering
analyze.py       CLI
seed/            test-data creation
tests/           no network
```

The split matters for one reason: `analysis.py` and `model.py` never import the
client, so every number in the report can be reproduced from a fixture.

See [`TECH_NOTE.md`](TECH_NOTE.md) for error handling, limitations and next steps.
