# Tech note

Error handling, limitations, and what I would do next.

---

## 1. Error handling

### The principle that did the most work

Every specific error case below was found by calling the API and reading what
came back — not by reading the spec. So the rule the client is built on is
**degrade loudly**: when something cannot be parsed, show the raw thing rather
than a tidy summary of nothing.

That rule caught more than my predictions did. An early failure produced
`HTTP 400: HTTP 400` — technically an error message, practically useless. The
fix was not to enumerate more shapes but to guarantee a floor: if the extracted
message ever degrades to the bare status code, the response body is appended.
The very next run printed
`Invalid type for field JobPositionDescription. Expected type: JobPositionDescription`,
which I had not anticipated and would not have. There is a test named
`test_unparseable_error_still_shows_the_body` pinning it.

### Error taxonomy

Three distinct failure classes, because they need three different responses:

| Class | Means | What the user is told |
|---|---|---|
| `BlockedError` | rejected before reaching the API (Cloudflare/WAF) | check the User-Agent and network egress |
| `HttpError` | the API answered; carries **its own message** | e.g. `missing permission job:view` |
| `TransportError` | no response at all | check connection and base URL |

The distinction is not academic. A Cloudflare 403 and an authorization 403 are
the same status code and completely different problems — one says "fix your
client", the other "your key lacks a scope". Collapsing them produces exactly the
unactionable message the brief warns about. A test asserts they stay separate.

The first real request of this project failed with a Cloudflare **1010** block,
because `urllib` identifies itself as `Python-urllib/3.14`. The client now always
sends a descriptive User-Agent — a requirement of talking to this API at all, not
a workaround.

### Pagination

Two styles, hidden behind one interface:

* `LastEvaluatedKey` cursor — `paginate_cursor`, follows until the key is empty
* `Page` / `TotalPage` — `paginate_pages`, stops at `TotalPage` or a short page

Both are capped at `max_pages` so a server that never terminates a cursor cannot
hang the run. A test named `test_never_reports_page_one_only` pins the failure
mode the brief calls out by name.

### Retries and rate limits

Retries 429 and 5xx, never 4xx. Honours `Retry-After` when present; otherwise
exponential backoff **with jitter** — the fetch phase issues one request per
candidate, so correlated retries would arrive in lockstep.

Worth stating plainly: **the API documents no rate limit.** No 429 response, no
`Retry-After`, no rate-limit headers anywhere in the spec. The backoff is
defensive guesswork against a policy we cannot see, which is a reason to treat
concurrency cautiously rather than a reason to assume there is no ceiling.

### Redirects

`urllib` refuses to follow redirects for POST — sensibly, since replaying a write
to a new URL is dangerous — so a 307 surfaces as an error. Several write
endpoints here redirect for trailing-slash normalisation. The client follows them
with the semantics that matter: **307/308 preserve method and body; 301/302/303
downgrade to GET.** Getting that backwards would silently re-POST a body
somewhere unintended. Chains are bounded and do not consume the retry budget.

### Isolation and partial failure

* **Per job.** One unreadable job records a data-quality entry and the run
  continues.
* **Per candidate.** One failed history call loses that candidate, not the job.
* **Per record.** A malformed record is skipped and reported, never fatal.
* **Everything skipped appears in the report.** A run that silently analysed 60%
  of the data would be worse than one that failed.

### Secrets

Key from the environment only; never logged, never in a URL, never in output.
Logging records method, path and attempt — never headers, which is where the key
lives. `.gitignore` was written before any other file, so no commit in this
repository has ever contained a `.env`.

---

## 2. Ten places the spec and the service disagree

All found by calling. Listed because they shaped the client's defensiveness:

1. `data.Categories`, not `data`, as the array
2. `GET /recruiting/jobs` — documented, **deleted** (plain-text 404)
3. `POST /recruiting/jobs/{id}/steps` — documented, **deleted**
4. Error `message` is sometimes a string, sometimes a list
5. `JobPositionDescription` is an object, not a string
6. Write endpoints redirect 307
7. Template agents return under `JobStepAgentTemplates`, not `Agents`
8. Create-person returns `user_slug` — snake_case in a PascalCase API
9. `EmployeeOrCandidate` requires lowercase; every other enum is PascalCase
10. Application filtering needs `paulsjob_job_id` with the `in` operator and an
    integer — `eq` is rejected and a string id fails validation

Behavioural findings that changed the design:

* **Endpoints assume a logged-in person.** A job needs an explicit owner because
  a company API key has no "creator" to default to — the convenience default is
  built for a UI session, and machine clients fall through it.
* **Assignments append.** Re-assigning leaves a second record for the same step.
  Without an explicit rule this silently double-counts.
* **A category is not unique within a pipeline.** Grouping by it merges stages.
* **Conclusion criteria live in `NextStepRules[].TargetAudience.FilterText`**,
  not in a field named for them.
* **Per-job agent config is immutable** (`422 job agent update not allowed`), and
  `pipeline-templates/switch` returns `200 success` without propagating the
  change. Configuration must therefore be correct in the template *before* jobs
  are created from it — a real operational constraint for client onboarding.
* **The API key cannot read `assessment` or `assistant_action`**, so an API
  client can see the agent's concluded decision but never its reasoning.

---

## 3. Limitations

### Selection bias — the important one

Override rate measures agreement on the decisions **a human actually looked at**.
Candidates rejected at pre-screening are rarely reviewed, so the population
generating this number is the population that survived the AI's first filter.

That biases it in the direction that matters most: **false rejections are
systematically under-counted.** A job could reject every qualified applicant at
pre-screening and still show a flawless override rate, because nobody reviewed
the rejections. The report says this in its own limitations section rather than
only here. Fixing it needs deliberate sampling — see next steps.

### The data is authored -- both halves of it

Covered in the README. The AI decisions are authored because the dev
environment's screening agent never concludes. The human review decisions are
authored too: no candidate in this dataset was actually reviewed by a person in
the UI.

So the analyzer is validated for correctness against known ground truth, and is
validated against neither real agent behaviour nor real reviewer behaviour. The
override rate demonstrates that the metric is computed correctly; it says
nothing about whether humans in fact disagree with this agent.

Reviewing the seeded candidates in the UI would fix the second half without
touching any code -- the analyzer reads a genuine `AssignerDecision` identically
to an authored one.

### Small cells are inherent, not a sampling artefact

A funnel is multiplicative, so the human-interview cell is small for every
client, always. Cells below n=10 are marked low confidence and reported anyway —
hiding them would hide exactly the steps where a single bad decision matters
most. This is why n appears beside every percentage.

### Reason matching is at concept level

`cites_no_criterion` compares a rejection's *bucket* against the step's criteria.
It catches a category the job never mentions — a salary rejection on a listing
with no salary requirement. It does **not** catch a different specific
requirement inside a category the job does use: a driving licence on a role
asking for a nursing qualification are both `certification`. A test pins this
blind spot so it cannot regress unnoticed.

### Thresholds are judgement calls

`HIGH_OVERRIDE_RATE = 0.25`, `HIGH_OPT_OUT_RATE = 0.25`,
`LARGE_OTHER_SHARE = 0.30`, `LOW_CONFIDENCE_N = 10`. Named constants, printed
alongside the number each fired on, so a reader can disagree with the threshold
rather than the finding. `LOW_REVIEW_WHEN_REQUIRED = 0.90` is the exception and
is not tuned: `always_on` means every action needs approval, so the expected
value is 100% and the allowance is for timing.

### Not implemented

* Sorting in the HTML tables. It shipped and was removed: the tables interleave
  job-heading rows with data rows, and any client-side reorder filed a step
  under the wrong job — the exact misattribution the (job, step) design exists
  to prevent. Making it work needs a job column on every row so each row stands
  alone; the tables are small and already in pipeline order, so the JSON output
  covers slicing instead. The page now carries no JavaScript at all.
* Tier-3 LLM classification — deliberate, see README
* Recreating the pipeline template from code — the verified payloads are
  committed as fixtures, but the template is a documented manual step

---

## 4. Next steps

**In the order I would actually do them.**

1. **Make the fetch incremental.** One history request per candidate is the cost
   driver: a client at 8,000 applications a month means 8,000 requests for a
   month of data. `search-applications` supports `CreatedAtGte`/`CreatedAtLte`,
   so scope a run to a window, cache by candidate, and re-fetch only what
   changed. Prerequisite for everything below.

2. **Trend over time.** Every current number is a snapshot. The question the
   implementation person actually has is "did last week's criteria change help?",
   which needs scheduled runs and stored results. Cheap once (1) exists.

3. **Attack the selection bias.** The honest fix is not statistical, it is
   procedural: sample rejected candidates for human review deliberately, so the
   reviewed slice stops being the survivors. The tool can pick the sample —
   prioritising rejections in reason buckets with high override rates elsewhere —
   and only then does "accuracy" become a defensible word.

4. **Feed the reason patterns back into the listing.** The interesting version of
   "38% of rejections are certification" is noticing that a criterion rejects most
   applicants and asking whether it should be a hard requirement. That is an
   argument the tool can assemble but a human must make.

5. **Re-evaluate flagged candidates.** Where a step's criteria have been
   corrected, the candidates rejected under the old ones are still rejected. A
   semi-automated re-run over that cohort, with a human confirming each, closes
   the loop between finding a config error and repairing its damage.

6. **Config-drift checks across jobs.** The agent config is already fetched.
   Comparing every job's steps against its template would catch the failure I hit
   by hand: deleting a step silently orphaned another step's routing target, and
   the UI reported it only as a disabled button with no message.

### What I would refactor first

`analyzer/fetch.py` carries both orchestration and response-shape tolerance. If
the API stabilised, the shape-guessing helpers (`_as_list`, the multi-key slug
lookups) should collapse into typed parsers with explicit validation. Today the
tolerance earns its place — the shapes genuinely varied across ten documented
cases — but it is the code most likely to become superstition once the API is
predictable, and superstition is expensive to remove later.

---

## 5. Two product observations

Not part of the deliverable, but they came out of using the platform seriously
for two days, and they are the reason this tool has a job.

**A metric labelled as engagement.** The candidate list shows *Profilaktivität*
with a tooltip describing profile completeness plus message, reading and posting
activity. In this account it is exactly `len(profile_text) / 2000` — matching to
four decimal places across six candidates. Every input the tooltip names is
either uncorrelated (the candidate with the most populated fields scores second
lowest) or structurally zero (nobody has been messaged or has ever logged in). A
side effect is that a candidate scores *higher* for lacking a requirement,
because stating an absence takes more words than stating a qualification.

I would raise this as a question rather than a finding: **is it stubbed in dev?**
`EngagementScore` is listed among the externally syncable attributes, so a dev
account with no real interaction data may well substitute a proxy. That cannot be
determined from inside dev.

**Numbers that assert more than the data supports.** Cancelling an agent warned
that "Paul is talking to this candidate right now" while
`message-channel-breakdown` reported `TotalSent: 0, TotalReceived: 0` for every
candidate in the account. The dashboard reported zero candidates in pre-screening
directly above a list of candidates in pre-screening.

Both may be dev-environment artefacts. But they are the same shape as the metric
above, and together they are the clearest statement of what this tool is for: the
platform's surface numbers cannot currently be checked against its own records,
so the analyzer computes from the decision records and states its derivation —
including its thresholds, its denominators and everything it had to skip.
