# Product MCP — Trusted ingress connectivity and recovery

## Authority and topology

The primary user-facing Product MCP remains `https://mcp.ordax.com.br`.
The already deployed `https://ordax-mcp.ordaxsystems.workers.dev` is an
**alternate, path-restricted ingress to that exact same Cloudflare Worker**.
It does not hold an alternate identity, queue, policy, database or model relay.

Only the **device Runtime client** may select the alternative ingress for:
- `/v3/device/transport/{heartbeat,claim,start,lease,progress,report,recover-report}`
- `/v3/product/device/central-session`

The direct ChatGPT MCP endpoint, OAuth authorization and external clients
continue using the public branded hostname; this patch does not change them.

## Security and replay contract

1. TLS verification and redirects-disabled remain mandatory on both routes.
2. Switching is allowed only after `httpx.ConnectError` or
   `httpx.ConnectTimeout`, before HTTP payload delivery.
3. Never replay an operation following a read timeout, HTTP status (including
   503), unexpected response, authorization denial, or ambiguous execution.
4. The exact same device identity, lease/attempt/effect IDs, epoch, report
   digest and body are retained; no new action can be synthesized by ingress.
5. Only the canonical configured origin enables alternative ingress; custom
   URLs never grant the alternate hostname a credential.
6. Prefer the reachable restricted route briefly, re-probing the primary
   every 120 seconds, returning to the primary as soon as it works.
7. Terminal reports continue to be saved in the existing on-disk outbox
   before network delivery, and can be reconciled by the same fenced attempt
   when the Runtime reconnects. No automatic command re-execution.

## Observed evidence — 2026-10-20

On DESKTOP-COHT67R, normal DNS resolution of mcp.ordax.com.br returned
Cloudflare anycast addresses but HTTPS connectivity timed out. The restricted
workers.dev hostname was reachable. A diagnostic `curl --resolve` to
mcp.ordax.com.br using a separately reachable Cloudflare edge IP returned
HTTP 200 with the canonical MCP health response. The domain mapping in the
same Cloudflare account was enabled, and PostgreSQL was directly queryable.
These observations support an edge-path connectivity failure, but do not
identify its network owner or demonstrate universal availability.

Tests: `tests/test_product_ingress_failover.py` and the related existing
Product transport/session/outbox suites.

## Gates not yet passed

- Build and install a Runtime release containing this patch on Windows,
  then verify normal startup and a live authenticated device heartbeat.
- A controlled Runtime termination *after* a test command saves a terminal
  report, followed by restart and the same original report reconciliation.
- Check the Windows startup/watchdog mechanism: a legacy watchdog log referred
  to a scheduled task `OrdaX Dev Agent`, while that task was not found in the
  observed Task Scheduler listing. This needs a release-specific audit.
- Verify end-to-end ChatGPT MCP discovery/attestation through the public
  hostname independently; the alternate device ingress does not solve public
  MCP network availability.
