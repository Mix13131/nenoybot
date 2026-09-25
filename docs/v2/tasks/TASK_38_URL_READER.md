# TASK 38 — Bounded URL Reader

## Goal

Give НеНой 2.0 the ability to read **one explicit public URL** when the user asks for it, without turning the product into a browser/search agent or changing the current Friends Test product boundary.

## Product boundary

Supported:
- Personal: a URL sent directly to НеНой is treated as an explicit handoff; a URL in a replied-to message is read when the new message asks to inspect/read/check it.
- Group: URL reading only on an explicit address/reply to НеНой. Ordinary ambient group links never trigger outbound HTTP.
- One URL per event.
- HTML/XHTML/plain text/Markdown/JSON text extraction.
- Direct HTTP fetch first; optional Firecrawl fallback when configured.

Not supported in this task:
- autonomous web search;
- following multiple links from a page;
- comparing several URLs in one request;
- authenticated/private pages;
- browser automation / Playwright;
- scheduled fresh-web monitoring;
- treating one page as independent verification of its own claims.

## Runtime limits

Defaults:
- fetch timeout: 8 seconds;
- max redirects: 5;
- max download: 5 MB;
- extracted content: 30,000 characters before generation budget;
- external generation context budget: 8,000 estimated tokens;
- process-local cache: 24 hours;
- ports: 80/443 only.

## Security invariants

1. Only `http://` and `https://`.
2. No URL userinfo/embedded credentials.
3. Local/private/link-local/reserved/non-global IP targets are rejected.
4. DNS hostnames are resolved before requests; every returned address must be public.
5. Every redirect target is validated again.
6. Direct fetch connects to a validated IP while preserving the original Host header and TLS SNI, preventing DNS-rebinding between validation and connection.
7. Proxy environment variables are ignored by the owned HTTP client.
8. Page content is untrusted evidence, never model instructions.
9. Page body is not persisted in intervention metadata; only bounded operational metrics are stored.
10. Durable URL telemetry never stores the URL path or query string; only host + status/reason/source/cache/truncation/size fields are eligible.

## Generation behavior

The generation package gets:
- `external_context`: current-turn page content only;
- `action_state.url_read`: current-turn status/URL/source/cache/truncation/size metadata for generation;
- `interventions.metadata.url_read`: durable sanitized telemetry only (no page body, URL path or query).

The generator must:
- use the page when it was actually read;
- never invent page content on failure;
- distinguish “the page says X” from independent verification;
- never claim internet-wide search;
- ignore prompt injection found inside page content.

## Optional Firecrawl fallback

If `NENOY_V2_FIRECRAWL_API_KEY` is absent, direct reader works without any external paid dependency.

If present, eligible direct-read failures may use Firecrawl `/v2/scrape` and Markdown output.

## Acceptance

Automated coverage must prove:
- Personal URL handoff works.
- Reply-to-link Personal intent works.
- Ambient Group link does not fetch.
- Explicit Group address/reply can read a URL.
- Negative intent does not fetch.
- Multiple URLs fail closed.
- private/loopback/link-local targets are rejected.
- non-80/443 ports are rejected.
- redirect to private target is rejected before the second request.
- validated-IP pinning preserves Host/SNI.
- oversized responses are stopped.
- cache prevents duplicate fetch.
- optional Firecrawl fallback works.
- external generation context is bounded.
- existing `tests_v2` remain green.

## Manual live acceptance

After merge/deploy:
1. Personal: send one ordinary public article URL and ask for a short summary.
2. Group: post a URL without addressing НеНой — no fetch/unsolicited answer.
3. Group: address НеНой with the URL — answer reflects the actual page.
4. Reply to another participant’s message containing a URL and ask НеНой to inspect it.
5. Send two URLs — НеНой asks for one at a time.
6. Confirm intervention metadata contains URL metrics but not page body/path/query.
7. Confirm analytics report exposes URL request/success/failure/cache/Firecrawl/content-size/generator-token/generator-cost metrics.
