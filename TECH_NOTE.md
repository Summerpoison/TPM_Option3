# Tech note

Error handling, limitations, and next steps.

---

## 1. Error handling

### Degrade loudly

Every case below was found by calling the API, not by reading the spec. The
rule the client is built on: when something cannot be parsed, show the raw
thing rather than a tidy summary of nothing.

An early failure printed `HTTP 400: HTTP 400`. The fix was a floor, not more
special cases: if the extracted message degrades to the bare status code, the
response body is appended. The next run printed
`Invalid type for field JobPositionDescription. Expected type: JobPositionDescription`,
which no amount of guessing would have produced. A test pins this
(`test_unparseable_error_still_shows_the_body`).

### Three failure classes

| Class | Means | The user is told |
|---|---|---|
| `BlockedError` | rejected before reaching the API (Cloudflare/WAF) | check the User-Agent and network egress |
| `HttpError` | the API answered, with its own message | e.g. `missing permission job:view` |
| `TransportError` | no response at all | check connection and base URL |

A Cloudflare 403 and an authorization 403 share a status code and need
opposite fixes. Collapsing them produces exactly the unactionable message the
brief warns against. The first request of this project was a Cloudflare 1010
block on `urllib`'s default User-Agent, so the client always sends its own.

### Pagination

Two styles behind one interface: a `LastEvaluatedKey` cursor and
`Page`/`TotalPage`. Both are capped at `max_pages` so an endless cursor cannot
hang a run. `test_never_reports_page_one_only` pins the failure mode the brief
names.

### Retries and rate limits

429 and 5xx are retried, 4xx never. `Retry-After` is honoured when present
and capped at 60 seconds; otherwise exponential backoff with jitter, since the
fetch phase issues one request per candidate and correlated retries would
arrive in lockstep.

The API documents no rate limit at all: no 429, no `Retry-After`, no
rate-limit headers. The backoff is defensive guesswork against a policy we
cannot see, which is a reason to keep concurrency low rather than a reason to
assume there is no ceiling.

### Redirects

`urllib` will not follow a redirect for a POST. Several write endpoints here
redirect for trailing-slash normalisation, so the client follows them with the
correct semantics: 307/308 preserve method and body, 301/302/303 downgrade to
GET. Chains are bounded and do not consume the retry budget. A redirect to any
other origin is refused, because the API key travels in a header on every
request.

### Isolation

One unreadable job, one failed candidate, one malformed record: each is
recorded and the run continues. Everything skipped appears in the report. A
run that silently analysed 60% of the data would be worse than one that failed.

### Secrets

Key from the environment only; never logged, never in a URL, never in output.
Logging records method, path and attempt, never headers. `.gitignore` was the
first file written, so no commit has ever contained a `.env`.

---

## 2. Where the spec and the service disagree

All found by calling. They are why the fetch layer is as tolerant as it is.

1. `data.Categories`, not `data`, as the array
2. Error `message` is sometimes a string, sometimes a list
3. `JobPositionDescription` is an object, not a string
4. Write endpoints redirect 307
5. Template agents come back under `JobStepAgentTemplates`, not `Agents`
6. Create-person returns `user_slug`, snake_case in a PascalCase API
7. `EmployeeOrCandidate` must be lowercase; every other enum is PascalCase
8. Application filtering needs `paulsjob_job_id` with the `in` operator and
   an integer; `eq` is rejected and a string id fails validation

Not a disagreement, but worth knowing: the spec marks `GET /recruiting/jobs`
and `POST /recruiting/jobs/{id}/steps` as deprecated, and both already return
a plain-text 404 rather than still working. The client uses the replacements
(`search-jobs`, `steps/init`) from the start.

Behaviour that changed the design:

* **Endpoints assume a logged-in person.** A job needs an explicit owner
  because a company API key has no creator to default to.
* **Assignments append.** Re-assigning leaves a second record for the same
  step. Without an explicit rule this double-counts.
* **A category is not unique within a pipeline.** Grouping by category merges
  stages; everything keys on step id.
* **Conclusion criteria live in `NextStepRules[].TargetAudience.FilterText`.**
* **Per-job agent config is immutable** (`422 job agent update not allowed`),
  and `pipeline-templates/switch` returns success without applying. Config
  has to be right in the template before jobs are created from it, which is
  an operational constraint for onboarding.
* **The API key cannot read `assessment` or `assistant_action`.** An API
  client sees the agent's decision but never its reasoning.

---

## 3. Limitations

### Selection bias

The reversal count covers decisions a recruiter opened. Candidates rejected
at pre-screening are rarely opened, so the reviewed population is the one
that survived the first filter, and false rejections are systematically
under-counted. A job could reject every qualified applicant at pre-screening
and still show no reversals. The report says this in its closing paragraph.
The fix is procedural, not statistical; see next steps.

### The data is authored, both halves of it

Covered in the README. The AI decisions are authored because the dev
environment's screening agent never concludes; the human review decisions are
authored because nobody reviewed these candidates in the UI. The analyzer is
therefore validated against known ground truth and against neither real agent
nor real reviewer behaviour. Reviewing the seeded candidates in the UI would
fix the second half without touching code.

### Small cells are inherent

A funnel is multiplicative, so the human-interview cell is small for every
client. Nothing is dropped for being small: a pattern on fewer than ten
decisions, or fewer than five people, is listed as worth watching rather than
turned into a priority.

### Reason matching is at topic level

`cites_no_criterion` compares a rejection's topic against the step's criteria.
It catches a topic the listing never mentions, such as salary on a role with
no salary requirement. It does not catch a different specific requirement
inside a topic the job does use: a driving licence on a role asking for a
nursing qualification are both `certification`. A test pins this blind spot.

### `conditional` review is treated as no review

`HumanInLoop` has three settings. `always_on` is checked. `conditional` means
review is required when a condition the agent evaluates holds, and that
condition cannot be re-evaluated from outside, so it is treated like
`always_off`: no finding rather than a finding built on a guess.

### Thresholds are judgement calls

`HIGH_OVERRIDE_RATE = 0.25`, `HIGH_OPT_OUT_RATE = 0.25`,
`LARGE_OTHER_SHARE = 0.30`, `LOW_CONFIDENCE_N = 10`, `ACT_MIN_PEOPLE = 5`,
`NOTIFICATION_GAP = 0.50`. Named constants, printed next to the numbers they
fired on, so a reader can disagree with the threshold rather than the finding.

A rate alone never sets priority. It needs `ACT_MIN_PEOPLE` affected to be
*act now* and `LOW_CONFIDENCE_N` decisions behind it to be *check*; below both
it is only watched. Opt-outs never reach *act now*, because the fix is about
contact timing, not configuration. On an `always_on` step, coverage below
`NOTIFICATION_GAP` means recruiters are probably not seeing the step and the
action is to check notifications; above it the action is the list of missed
candidates, since the reviewed ones prove notification works.

### Not implemented

* Sorting in the HTML tables. It shipped and was removed: the tables
  interleave job-heading rows with data rows, and a client-side reorder filed
  steps under the wrong job. The tables are small and in pipeline order; the
  JSON covers slicing. The page carries no JavaScript.
* Tier-3 LLM classification, deliberately; see README.
* Creating the pipeline template from code. The verified payloads are
  committed as fixtures; the template is a documented manual step.

---

## 4. Next steps

In the order I would do them.

1. **Incremental fetching.** One history request per candidate is the cost
   driver: 8,000 applications a month is 8,000 requests per run.
   `search-applications` filters on `CreatedAtGte`/`CreatedAtLte`, so scope a
   run to a window, cache by candidate, re-fetch only what changed.
   Prerequisite for everything below.

2. **Trend over time.** Every number today is a snapshot. The question the
   implementation person actually has is whether last week's criteria change
   helped, which needs scheduled runs and stored results.

3. **Sample rejections for review.** The honest fix for the selection bias is
   procedural: pick rejected candidates for human review deliberately, so the
   reviewed slice stops being the survivors. The tool can choose the sample,
   starting with reason topics that are reversed often elsewhere. Only then
   does "accuracy" become a defensible word.

4. **Feed reason patterns back into the listing.** When one criterion rejects
   most applicants, the useful question is whether it should be a hard
   requirement. The tool can assemble that argument; a human has to make it.

5. **Re-evaluate flagged candidates.** After a step's criteria are corrected,
   the candidates rejected under the old ones are still rejected. A re-run
   over that cohort, with a human confirming each, closes the loop.

6. **Config-drift checks across jobs.** The agent config is already fetched.
   Comparing each job's steps against its template would catch what I hit by
   hand: deleting a step silently orphaned another step's routing target, and
   the UI showed only a disabled button.

### What I would refactor first

`analyzer/fetch.py` carries both orchestration and response-shape tolerance.
If the API stabilised, the shape-guessing helpers should collapse into typed
parsers with explicit validation. Today the tolerance earns its place, since
the shapes genuinely varied across the cases above, but it is the code
most likely to become superstition once the API is predictable.
