# Brief: Paul's Job API Challenge — Option 3 "Screening Accuracy Analyzer"

## Context

Take-home assignment for a Technical Project Manager role at Paul's Job (PJRI), an AI-assisted recruiting platform. Time budget: 3–5 hours. Reviewers will discuss decisions, not line count. Code is expected to be agent-written; the candidate must be able to explain and defend every design choice.

Submission: GitHub repo with README (goal, setup, endpoints used, assumptions), example output, and a tech note (error handling, limitations, next steps). API key only via environment variables; provide `.env.example`; never commit secrets.

```
# .env.example
PAULSJOB_API_KEY=your_api_key_here
PAULSJOB_BASE_URL=https://api.paulsjob.ai/dev
```

API docs: https://api.paulsjob.ai/dev/docs (OpenAPI; JS-rendered, needs a browser or the raw spec URL).

## What the tool does

Batch CLI script (not a live dashboard). Fetches candidate assessments across jobs and pipeline steps, then reports:

1. Positive vs. negative AI decisions per job and per pipeline step
2. Most common rejection reasons per job and per step
3. Human-vs-AI agreement, derived from `AssignerDecision` (see below)
4. Anomaly flags (e.g. rejection reasons that match no criterion in the job config; steps with high override rate; large unreviewed share)
5. A data-quality section listing everything skipped and why

Output: CLI text and/or JSON; simple HTML is optional.

Primary beneficiary: the implementation/customer-success person who configured the pipeline. Secondary: product (does the agent behave as expected), clients (wrongly rejected candidates, listing too narrow).

## Pipeline structure (assumed, verify against data)

Steps roughly: AI pre-screening (+ optional follow-up questions) → AI first interview → human-in-the-loop (HITL). Evaluate every step that produces an AI assessment; group by step. Pre-screening rejections speak to hard criteria/the listing; interview rejections speak to agent judgment.

## Deriving agreement (ground truth)

From the assign-person-to-job-step schema, `AssignerDecision` enum:

| Value | Meaning | Derived `agreement` |
|---|---|---|
| `ApprovePaulDecision` | human approved Paul's suggestion | agree |
| `RejectPaulDecision` | human overrode Paul | override |
| `PositiveDecision` / `NegativeDecision` | human acted independently | compare to `PaulDecisionSuggestion` if present → agree/override; else `independent` |
| omitted | Paul decided end-to-end | unreviewed |

Headline metrics: override rate per step/job, and unreviewed share.

`PaulDecision` and `PaulDecisionSuggestion` are free-form strings → normalize to positive/negative/other; report unmappable values.

Known limitation to state explicitly: selection bias. Humans only review candidates who reach HITL; candidates rejected at pre-screen may never be reviewed, so override rate underestimates false rejections. Present as "agreement on the reviewed slice," not "accuracy."

## Architecture

Three layers, kept separate:

- **API client** — auth, pagination, retries/backoff. Knows nothing about recruiting.
- **Analysis** — pure functions on plain data structures. Testable without the API.
- **Output** — renders insights from analysis results.

Assumptions must be written down as they are encountered (README section).

## Rejection-reason aggregation — tiered, cheapest first

1. **Exact counting**: normalize (lowercase, strip punctuation/whitespace), `collections.Counter`. Works if the agent uses a fixed vocabulary.
2. **Keyword buckets**: if ragged, define buckets in a config file (e.g. `language`: deutsch/german/sprach/language; `experience`: erfahrung/jahre/experience). Unmatched → `other`; large `other` means buckets are incomplete.
3. **LLM as classifier** (only if forced): fixed bucket list, one label per explanation, structured output, cheap model, spot-check a sample. Then back to counting.

Optional LLM summary: only on the aggregated top-N list (a dozen lines), never on raw explanations. Cache by hash of the aggregate. Numbers are code; prose is a thin layer on top.

Co-occurrence of reasons: count label-set combinations once explanations are reduced to labels.

## API handling rules

- **Pagination**: loop until no next page/cursor. Never report on page one only.
- **Rate limits**: on HTTP 429, honor `Retry-After` if present, else exponential backoff (1s, 2s, 4s…), max N retries.
- **Transient errors** (timeouts, 5xx): retry a few times, then give up on that item and continue.
- **Malformed data**: skip the record, log it, never crash.
- **Per-job isolation**: one broken job must not kill the run.
- **Minimum-data threshold**: don't hide small-n insights; mark them low-confidence with n visible.
- Every skip/failure ends up in the data-quality section of the report.

## Product/UX requirements

- Insights as sentences with numbers, not raw counts.
- Every insight implies an action (check config, widen criteria, review these candidates).
- Error messages say what to check ("Job 123: no screening agent configured on step 2"), never stack traces.
- Per-candidate view: plain text, no summarization.

## Next steps (for the tech note)

- Human-override comparison as a proper accuracy measure once HITL data exists
- Scheduled runs with trend over time
- Caching to avoid re-fetching
- Feeding rejection-reason patterns back into listing/criteria edits
- Semi-automated re-evaluation of flagged candidates

## OPEN — must be resolved before/while building

1. **Test data.** The dev account is empty. Check whether the dev environment ships sample data. If not, seed it via API (create job → pipeline → applications) or by hand. Budget time for this; it may be the biggest cost.
2. **Read endpoints.** Confirm which endpoint returns assessments/applications and whether it includes `AssignerDecision`, `PaulDecision`, `PaulDecisionSuggestion`, `PaulDecisionExplanation`, and step IDs. The schema above is from the *write* side. If the read side lacks these, the agreement metric is out of scope — say so.
3. **Pagination style** (page number vs. cursor) and **rate limits** — read from docs.
4. **Which steps produce assessments** — discover from data, don't assume.
5. **Rejection-reason format** — free text vs. fixed vocabulary. Decides which aggregation tier is needed. Look at ~50 real explanations before choosing.
6. **Job criteria access** — is the job's criteria/config retrievable so "reason matches no criterion" can be flagged?
7. **Output format** — CLI + JSON is the default; decide whether HTML is worth the time.
8. **LLM usage** — whether to use any LLM at all. Default: no, unless tier 3 is forced or a summary line adds clear value.
9. **Language** — Python is the working assumption; not yet decided.
