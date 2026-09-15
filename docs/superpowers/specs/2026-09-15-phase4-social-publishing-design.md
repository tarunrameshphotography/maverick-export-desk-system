# Phase 4 — Social publishing integrations: design

Status: approved by founder 2026-09-15, conditions incorporated below.
Feeds into: `INTELLIGENCE SYSTEM/roadmap.md` Phase 4, `radar/publishing/dispatch.py`'s
`Publisher` protocol (Phase 3 seam).

## Goal

Supply the two concrete classes that satisfy `dispatch.Publisher` — one for
LinkedIn, one for Instagram — so that once `auto_publish` is ever
deliberately flipped, `queue-dispatch --live` has real platform adapters to
call. This phase does **not** flip that gate and does **not** perform any
real publication; it is code, tests, and documentation only.

## Founder conditions (binding on this design and the implementation plan)

1. `auto_publish` stays hard-refused exactly as `settings.feature_flags()`
   enforces it today. Nothing in this phase touches that function or its
   test (`tests/test_drafts.py::test_auto_publish_flag_cannot_be_enabled`).
2. Credentials via environment variables only.
3. **Before implementing each API contract, fetch and read the current
   official LinkedIn and Meta/Instagram developer documentation** (via
   WebFetch/WebSearch in the implementation session) — request/response
   shapes, endpoint paths, and upload flows must be verified against live
   docs, not written from model memory. Cite the doc URL and the date
   checked in each module's header comment, the same convention
   `publishing.yaml`'s header already uses.
4. Token acquisition/refresh is out of scope for this implementation, but
   the credential layer must be shaped so a refresh mechanism can be added
   later without changing the `Publisher` call sites (see "Credential
   layer" below).
5. Do not assume a manually supplied long-lived token is permanent — a 401
   from an expired/revoked token must classify as `permanent_error` (never
   retried automatically, since retrying an auth failure is pointless and a
   human needs to re-supply a token), not treated as a crash or ignored.
6. LinkedIn API version must be pinned via config, not hard-coded from
   memory of "the current version" — read from `radar/config/publishing.yaml`
   (new `linkedin.api_version` key) with the version string itself decided
   at implementation time from the live docs (condition 3), not guessed now.
7. Platform API clients (`linkedin.py`, `instagram.py`) stay separate from
   the generic HTTP/error-classification layer (`platform_http.py`) — no
   platform-specific status-code judgment leaks into the shared module, and
   no shared module imports either platform module.
8. No real publication during this phase without explicit founder
   instruction — verification is unit tests with injected fake HTTP
   transports plus a `dispatch_due` wiring test, never a real network call.
9. Implementation, tests, documentation, commit, and push are all in scope
   for this phase (subject to condition 8 — "push" means the code, not a
   post).

## Architecture

```
radar/publishing/
  platform_http.py   shared: HTTP outcome -> retryable/permanent/unknown, incl. 401-is-permanent
  credentials.py      shared: env-var credential loading, one seam for future refresh
  linkedin.py         LinkedInPublisher (Posts API: text/image/multi-image/document/video)
  instagram.py         InstagramPublisher (Graph API: image/carousel/reel)
```

No change to `dispatch.py`, `queue.py`, `validation.py`, or the DB schema —
Phase 3's seam is used exactly as designed.

### Credential layer (new — condition 4)

`radar/publishing/credentials.py` defines a small `Credential` value (token
string + optional expiry-known flag) and one function per platform,
`linkedin_credential()` / `instagram_credential()`, that:
- reads the relevant env vars (`LINKEDIN_ACCESS_TOKEN`, `LINKEDIN_PERSON_URN`,
  `LINKEDIN_ORG_URN`, `INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_BUSINESS_ACCOUNT_ID`),
- returns `None` if the token itself is absent (so `cli.py` can skip
  building that publisher entirely — same "no publisher for platform"
  behavior `dispatch_due` already has),
- is the **only** place either publisher class reads `os.environ` — so a
  later refresh implementation (e.g. reading a refresh token, calling the
  platform's token endpoint, writing a new token back to `.env` or a
  keystore) replaces the body of these two functions only. `LinkedInPublisher`
  and `InstagramPublisher` take a `Credential` (or a zero-arg callable
  returning one, if refresh-on-every-call turns out to be needed later) as a
  constructor argument, never read env vars themselves.

This phase implements only the "read from env, treat as static" case; the
seam is the function boundary and the constructor argument, not a stub
refresh method — no speculative refresh code is written now.

### `platform_http.py` (shared, condition 7)

One function: `classify_response(status: int | None, headers: dict,
exc: Exception | None) -> tuple[str, int | None]` returning
`(outcome, retry_after_seconds)`:
- `exc` is a timeout or connection error → `("unknown", None)` — never
  assumed safe to retry, since the request may have landed.
- `status == 429` → `("retryable_error", Retry-After header if present)`.
- `status in (401, 403)` → `("permanent_error", None)` — expired/revoked/
  wrong-scope token; a human must re-supply credentials (condition 5).
- `status >= 500` → `("retryable_error", None)`.
- `400 <= status < 500` (other than 401/403/429) → `("permanent_error", None)`
  — the platform rejected the request as malformed; retrying identically
  will not help.
- `200 <= status < 300` → the caller (platform module) is responsible for
  parsing the body; `platform_http` does not decide "published" for a 2xx,
  since only the platform module knows what a valid success body looks
  like for that specific call.

Pure function, no I/O, fully unit-testable without a fake session.

### `linkedin.py`

`LinkedInPublisher(credential: Credential, session=None)`; `session`
defaults to a `requests.Session()`, injectable for tests. `name =
"linkedin_api"`, `platform = "linkedin"`. API version read from
`settings.publishing_rules()["linkedin"]["api_version"]` (new config key,
value filled in during implementation after checking live docs — condition
3/6) and sent as the required version header on every call.

`publish(payload)` dispatches on `payload.post_format`:
- `linkedin_text` — one Posts API call, no media.
- `linkedin_image` / `linkedin_multi_image` / `linkedin_document` /
  `linkedin_video` — upload flow per the (live-doc-verified) asset upload
  contract for each media kind, then a Posts API call referencing the
  resulting asset URN(s). `linkedin_document` also sends the media row's
  `title` (required — `validation.py` already enforces
  `requires_document_title` before this is ever called). `linkedin_video`'s
  upload step must handle the documented single- vs multi-part upload
  response shape (implementation confirms which applies at the sizes this
  system allows — max 500MB per `publishing.yaml`'s `media_rules`).
- Author URN: `payload.target_account == "founder_profile"` →
  `credential.person_urn`; `"company_page"` → `credential.org_urn`. Missing
  the URN needed for the item's target account is a `permanent_error`
  (config problem, not a platform problem, not a crash).

Every HTTP call's outcome goes through `platform_http.classify_response`
first; only a genuine 2xx with a parseable post ID becomes
`PublishResult(outcome="published", external_post_id=...)`.

### `instagram.py`

`InstagramPublisher(credential: Credential, session=None)`. `name =
"instagram_api"`, `platform = "instagram"`. Graph API version likewise read
from config (`publishing_rules()["instagram"]["api_version"]`), verified
against live docs at implementation time.

Two-step container flow, per `payload.post_format`:
- `instagram_image` — create container (`image_url` from
  `media[0].public_url`, caption) → publish container.
- `instagram_carousel` — create N child containers
  (`is_carousel_item=true`) → create parent container (`children=[ids]`,
  `media_type=CAROUSEL`) → publish.
- `instagram_reel` — create container (`media_type=REELS`, `video_url`,
  optional `cover_url`) → poll container status a small bounded number of
  times (config: `instagram.reel_status_poll_attempts` /
  `..._poll_interval_seconds`, not indefinite) → publish once `FINISHED`.
  Still processing when the bound is hit → `outcome="unknown"` (safe
  default; a human or a later reconciliation adapter checks it — never
  guessed, never double-posted, matching the "unknown is a valid answer"
  guardrail in `CLAUDE.md`).

## Data flow / error handling

`PublishPayload` in, `PublishResult` out — the existing Phase 3 contract,
unchanged. `dispatch.apply_result` already downgrades a `published` result
with no post ID to `unknown`, so the publishers only need to parse honestly,
not defend against that case themselves. A malformed/unexpected response
body on an otherwise-2xx call is `unknown`, not a guess at either outcome.

## Testing

`tests/test_platform_http.py` — pure function tests for every status-code
branch above, including the 401→permanent and timeout/connection-error→
unknown cases the founder specifically called out.

`tests/test_credentials.py` — env-var presence/absence → `Credential` or
`None`, one test per required var missing.

`tests/test_linkedin_publisher.py`, `tests/test_instagram_publisher.py` — a
fake session (`.post`/`.put`/`.get`, matching only the subset of
`requests.Session` actually used) is injected; zero real network calls,
matching `test_generation.py`'s fake-`GenerateFn` pattern. Covers: correct
request sequencing for every `post_format` (including multi-step upload,
carousel child/parent, and reel polling to both `FINISHED` and
still-processing-at-the-bound), the version header being sent, missing-URN
→ `permanent_error`, and every `platform_http` outcome surfacing correctly
end to end through `publish()`.

2-3 tests exercise both publishers through `dispatch.dispatch_due` directly
(with `assert_live_publishing_allowed` monkeypatched exactly as Phase 3's
existing tests already do — never the `auto_publish` flag itself) to prove
the wiring at the dispatch layer, not just the class in isolation.

A CLI smoke test on a scratch DB confirms: with no env vars set, both
publishers are absent from the dict `cmd_queue_dispatch` builds and
`queue-dispatch --live` still refuses (exit 2, unchanged) before ever
reaching that dict; with fake env vars set, the publishers are constructed
without error (construction only — no call is made, since `--live` is still
refused by the gate, per condition 8).

## Documentation updates

- `roadmap.md` Phase 4 row/section: mark built-and-tested, gate still
  closed, cite what was verified against live docs and when.
- `milestones.md`: new dated entry with the same evidence-based structure
  as Phase 3's entry (what was built, what tests cover, what was
  deliberately not done — real publication, token refresh, flipping
  `auto_publish`).
- `.env.example`: document the five new env vars, with a comment on how a
  founder obtains each (LinkedIn Community Management API app + OAuth
  consent → long-lived token; Meta app + Instagram professional account
  linked to a Facebook Page → long-lived token), and an explicit note that
  these are not auto-refreshed by this system yet.
- `radar/config/publishing.yaml`: new `linkedin.api_version`,
  `instagram.api_version`, `instagram.reel_status_poll_attempts`,
  `instagram.reel_status_poll_interval_seconds` keys, each with a header
  comment citing the doc URL and date checked (condition 3).

## Explicitly out of scope

- OAuth token acquisition/refresh implementation (seam only — condition 4).
- Flipping `auto_publish`.
- Any real HTTP call to LinkedIn or Instagram's actual APIs, in this
  session or via the CLI (condition 8) — all verification is fake-transport
  unit tests plus dry-run/wiring checks.
