# Work in progress — state note

Scratch file for resuming. Not part of the submission; delete before handing over.

## Where things stand

Built and tested (50 tests, no network needed):
- `analyzer/client.py` — transport: auth, both pagination styles, retries,
  307/308 redirects, multipart, error taxonomy
- `analyzer/model.py` — decision normalisation + human/AI agreement
- `seed/` — scenario-driven seeding, idempotent, dry-runnable

Not built yet — this is the actual deliverable:
- fetch orchestration (jobs → applications → per-candidate step history)
- analysis layer (aggregations over normalised records)
- rejection-reason aggregation (tier 1 exact, tier 2 keyword buckets)
- output (CLI text + JSON)
- README, tech note, example output

## The open blocker

The live agent records the assignment but never concludes. `PaulDecision`
stays null indefinitely.

Ruled out: candidate muted; pipeline misconfigured; no CV; wrong ordering;
empty profile (a fully populated profile still produced nothing).

Still untested — the only remaining lead:
- `DeliverySettings.Channels: []` — the agent may need a delivery channel to
  run its flow at all
- `IsLastPreScreeningStep: false` — the step may never be treated as
  concluding pre-screening

Both are agent config. Per-job agent config is immutable
(`422 job agent update not allowed`), and `pipeline-templates/switch` returns
200 without propagating. So testing means: new template with those set → new
job → one candidate. Roughly 15 credits.

## Decision pending

- **A** — run that one experiment, then proceed either way
- **B** — switch scenarios to `authored=True`, seed 2 jobs x 20, do the HITL
  review in the UI for genuine `AssignerDecision` values, and build

Either way the human-review half stays real, and the limitation statement is
the same shape: decisions authored to a declared spec, review decisions
genuine, analyzer validated for correctness against known ground truth rather
than against live agent behaviour.

## Credits

~12 per candidate: 5 on person creation (structured profile extraction, fires
on creation regardless of source) + 7 on `update-person-unstructured-json`.
Mode-independent — authored mode creates people too. Budget ~1000, so ~12 real
candidates is sensible, not 40.

## Live state on the dev account

- Jobs: `saa-seed-01` (182680, 3 authored candidates, muted),
  `saa-seed-11` (182721, 5 real-mode candidates)
- Template: `SAA Seed Pipeline` `01a07cf4-5bea-7a88-a560-77ac73733139`
  (Neu → Vorauswahl → KI-Voice → Menschliches Interview → Teamdiskussion →
  Ablehnen → Erledigt)
- Unmuted and idle: Nadia Farhat `01a07d41-6000-792e-a6cf-73a33a4aa8d3`
  (fully populated profile), Tobias Yilmaz `01a07d14-c5de-7a3a-8465-8faa9ebea829`
  — left live in case the agent concludes late; everyone else is muted
- Nothing is scheduled or running

## API findings (for the tech note)

Spec-vs-reality, all found by calling rather than reading:
1. `data.Categories`, not `data` as the array
2. `GET /recruiting/jobs` — documented, deleted (plain-text 404)
3. `POST /recruiting/jobs/{id}/steps` — documented, deleted
4. Error `message` is sometimes a string, sometimes a list
5. `JobPositionDescription` is an object, not a string
6. Write endpoints redirect 307; urllib won't follow for POST
7. Template agents return under `JobStepAgentTemplates`, not `Agents`
8. Create-person returns `user_slug` — snake_case in a PascalCase API
9. `EmployeeOrCandidate` wants lowercase; every other enum is PascalCase
10. Cloudflare 1010 blocks urllib's default User-Agent before the API sees it

Behavioural findings:
- Endpoints designed around a logged-in person behave differently under
  machine auth: a job needs an explicit owner because an API key has no
  "creator" to default to
- Step assignments APPEND to history — re-assigning creates a second record
  for the same step, so the analyzer needs an explicit rule (latest non-null
  decision per person+step) and must report duplicates
- A category is not unique within a pipeline (two `TeamDiscussion` steps):
  group by StepID, carry category as an attribute
- Conclusion criteria live in `NextStepRules[].TargetAudience.FilterText`,
  not in a `PositiveDecisionCondition` field
- `Actions.StatusChange` gives the routing graph, so "did every negative
  decision land in Ablehnen" is checkable
- API key lacks `assessment:read` and `assistant_action:read`, so an API
  client can never see the agent's reasoning — only its concluded decision
- Deleting a step silently orphaned another step's routing target; the UI
  surfaced it only as a disabled button with no message

## "Profilaktivität" is character count (verified)

The candidate list shows a Profilaktivität percentage. Its tooltip claims it
measures overall participation and activity: how far a user has completed their
profile, plus active interactions such as answering messages, reading content
and contributing posts.

It is `len(UnstructuredData) / 2000`. Exact to four decimal places across six
candidates:

| candidate  | score   | chars | populated fields |
|------------|---------|-------|------------------|
| Nadia      | 35.25%  | 705   | 15               |
| Tomasz     | 28.70%  | 574   | 18               |
| Agnieszka  | 23.35%  | 467   | 18               |
| Katarzyna  | 22.15%  | 443   | 19               |
| Piotr      | 20.45%  | 409   | 18               |
| Magdalena  | 20.05%  | 401   | 18               |

Each documented input falsified:
- completeness -- Katarzyna has the most populated fields and the second
  lowest score; Nadia has the fewest and the highest
- answering messages -- every candidate has TotalSent 0 / TotalReceived 0
- reading content, contributing posts -- @seed.invalid addresses, never
  logged in, cannot have done either

Consequence: a candidate scores higher for LACKING a requirement, because
stating an absence takes more words than stating a qualification. Tomasz
outscores every qualified candidate purely because his summary explains a
missing certificate.

Relevance to this tool: it is the same failure mode as the "Paul is talking to
this candidate right now" warning shown while zero messages exist. Surface
numbers assert things the underlying data does not support. That is the
argument for computing metrics from decision records and stating the
derivation -- which is what the analyzer does.
