# AI Stack & Data Integration Plan — Progress Tracker

Tracking progress against `~/.cursor/plans/ai-stack-data-integration-plan_e90071ca.plan.md`
(also attached as `uploads/ai-stack-data-integration-plan_e90071ca.plan-L1-L332-0.md`).

**Plan file is read-only — never edit it. Update this tracker instead.**

---

## How to resume after a Cursor restart

1. Re-open `/Users/beenu/Desktop/AiSOC` in Cursor.
2. Read this file top-to-bottom.
3. Re-create the todo list (use `TodoWrite`) from the snapshot in the
   "Live todo snapshot" section below — keep the same `id`s.
4. Resume from the section marked **"Resume here"** at the bottom.
5. Workspace rule: continue without stopping until every todo is done.

---

## Workstream status

| WS | Title                              | Status      | Anchor |
|----|------------------------------------|-------------|--------|
| 1  | Repo + plan alignment              | DONE        | pre-session |
| 2  | Click-and-connect OAuth            | DONE        | pre-session |
| 3  | Catalog expansion (P0 batch)       | DONE        | 14 connectors landed + manifests + docs |
| 4  | Capability taxonomy                | DONE        | enum + per-instance scoping + `/agents/tools` |
| 5  | Self-healing                       | DONE        | OAuth refresh + backfill + freshness SLO + UI badges |
| 6  | Universal capture                  | DONE        | `/v1/inbox/*` in `services/ingest` + tokens panel |
| 7  | Tenant lake API                    | DONE        | endpoints + rewriter + rate limiter + 69/69 tests |
| 8  | Bidirectional ITSM                 | PENDING     | push_case + `/v1/inbox/itsm` + actions worker |
| 9  | Docs + Sonali                      | PENDING     | `api-coverage.md` + `itsm-as-source-of-truth.md` + question list |

---

## GitHub hygiene workstream (in flight)

User asked for:
1. GitHub fully updated (docs/architecture in sync with code).
2. Code-scanning alerts at https://github.com/beenuar/AiSOC/security/code-scanning fixed.
3. Contributor graph showing only `beenu` (no `cursoragent`, no `AiSOC Bot`).
4. **Author identity for all commits: `Beenu Arora <beenu@cyble.com>`** — no
   co-author trailers, no `cursor.com` addresses, no `users.noreply.github.com`.

### Status

| ID                         | Task                                                                         | Status      |
|----------------------------|------------------------------------------------------------------------------|-------------|
| gh-1                       | Audit current git state, branches, identities                                | DONE        |
| gh-4                       | Secret scan with gitleaks + triage false positives                           | DONE (`.gitleaksignore` committed) |
| gh-2                       | Pull live code-scanning alerts and triage                                    | DONE (47 alerts triaged) |
| gh-6                       | Fix code-scanning issues (critical/high first)                               | DONE for SSRF + log-injection in `cases.py` + `fusion.py`; remaining notes (unused imports, bind-all, weak hash) triaged as benign |
| gh-5                       | Sync docs/architecture so GitHub matches current state                       | PENDING (covered by WS9) |
| gh-commit-pre-rewrite      | Commit fixes + tracker with `Beenu Arora <beenu@cyble.com>`, no co-authors   | IN PROGRESS |
| gh-3a..gh-3e               | History-rewrite plan (filter-repo + force-push)                              | PENDING     |
| gh-prs                     | Close 19 dependabot PRs; comment on #37 for K4R7IK to rebase                 | PENDING     |
| gh-7                       | Verify code-scanning alerts cleared after push                               | PENDING     |

### Co-author trailer issue (resolved at the local layer)

The Cursor harness was injecting `Co-authored-by: Cursor <cursoragent@cursor.com>`
into every commit message at shell-invocation time, even though local git
config is clean (`user.name=Beenu Arora`, `user.email=beenu@cyble.com`,
no `commit.template`, no active hooks, no mailmap).

Workaround applied: after each commit, run

```bash
FILTER_BRANCH_SQUELCH_WARNING=1 git filter-branch -f \
  --msg-filter "sed '/^Co-authored-by: Cursor/d' \
                | awk 'NF{p=1} p' \
                | awk 'BEGIN{n=0} {lines[n++]=\$0} END{e=n-1; while(e>=0 && lines[e]==\"\") e--; for(i=0;i<=e;i++) print lines[i]}'" \
  HEAD~N..HEAD
```

…where `N` is the number of recent commits with the trailer. The two AwK
passes strip leading and trailing blank lines so the rewritten commit message
is byte-clean.

Verification command:

```bash
git log --format='%h %an <%ae>%n%(trailers:only=true)' -n 5
```

---

## WS7 — Tenant lake API (shipped)

### What landed

- `services/api/pyproject.toml` — `sqlglot>=23.0.0,<27.0.0` + `clickhouse-driver`.
- `services/api/app/db/clickhouse.py` — async ClickHouse client wrapper:
  `get_clickhouse_client`, `close_clickhouse`, `execute_lake_query`,
  `fetch_lake_schema`. Exceptions: `LakeQueryNotConfiguredError`,
  `LakeQueryTimeoutError`, `LakeQueryError`. Returns `LakeQueryResult` dataclass.
- `services/api/app/services/lake_sql.py` — sqlglot rewriter:
  - `rewrite_for_tenant(sql, tenant_id, *, max_limit, allowlist)` →
    `RewriteResult(sql, referenced_tables, applied_limit)`.
  - SELECT-only allowlist; rejects DML/DDL/`KILL`/table-valued functions.
  - Recursive `tenant_id` predicate injection via `optimizer.scope`.
  - Empty-projection `SELECT` rejection (treats bare `SELECT` as syntax error).
  - **Subtle bug fixed**: `_direct_tables` previously read `select.args.get("from_")`;
    sqlglot stores the FROM clause under the dict key `"from"` (the Python attr
    is `from_`, but `args[]` uses the YAML-style key). All FROMs were silently
    skipped before the fix → no predicate injection, empty `referenced_tables`.
  - **Forbidden roots**: `(exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Alter,
    exp.Create, exp.TruncateTable, exp.Command, exp.Kill)`.
- `services/api/app/services/lake_rate_limit.py` — per-tenant in-memory token
  bucket. `LakeRateLimiter.acquire(tenant_id, cost=1.0)` → `RateLimitDecision`
  with `to_headers()` for `X-RateLimit-*` + `Retry-After`. Uses
  `time.monotonic` for refill clock; `_TokenBucket.last_refill` is a
  `field(default_factory=time.monotonic)` — see "Test gotcha" below.
- `services/api/app/api/v1/endpoints/lake.py` — `POST /api/v1/lake/sql` and
  `GET /api/v1/lake/schema`. Uses `AuthUser`, `DBSession`,
  `require_permission("lake:query"|"lake:read_schema")`. Helpers:
  `_acquire_or_429`, `_scrub_sql_for_log`, `_audit_query_attempt`.
  Pydantic models: `LakeQueryRequest`, `LakeQueryResponse`, `LakeColumnInfo`,
  `LakeTableInfo`, `LakeSchemaResponse`.
- `services/api/migrations/034_lake_permissions.sql` — registers
  `lake:query` + `lake:read_schema` in `role_permissions`.
- `services/mcp/src/tools/lake.ts` — `aisoc_lake_query` + `aisoc_lake_schema`
  MCP tools (typed surface, prompt-injection guards, calls API endpoints).
- `services/mcp/tests/lake.test.ts` — comprehensive tests for both tools
  (Zod schemas, multi-statement guard, forbidden tables, response shape).

### Tests — done

- `services/api/tests/test_lake_sql.py` — **44/44 passing**.
- `services/api/tests/test_lake_rate_limit.py` — **25/25 passing**.

### Test gotcha — `LakeRateLimiter` + `time.monotonic`

`_TokenBucket.last_refill` is set via `field(default_factory=time.monotonic)`,
which captures the **real** clock at bucket-creation time. If a test patches
`app.services.lake_rate_limit.time.monotonic` *after* the bucket has been
created, `_refill` computes `elapsed = patched_now - real_creation_now`,
which is hugely negative and gets clamped to 0 → no tokens refill.

Workaround already used in `test_lake_rate_limit.py`:

```python
async def _seed_bucket(limiter, tenant, *, at_time):
    """Force-create the tenant's bucket and align last_refill with the patched clock."""
    await limiter.acquire(tenant, cost=0.0)
    bucket = limiter._buckets[tenant]
    bucket.last_refill = at_time
```

Apply the same pattern when writing endpoint tests that depend on rate-limit
timing.

---

## Security fixes (gh-6)

### `services/api/app/api/v1/endpoints/fusion.py`

- Added `_SAFE_PATH_RE = re.compile(r"^/[A-Za-z0-9_\-./%]*$")` and
  `_validate_proxy_path(path)` that rejects `..`, protocol-relative `//host/...`,
  and any character outside the allowlist.
- `_proxy_get` validates the path before any `httpx.AsyncClient.get` and logs
  the *validated* path (kills the `py/log-injection` sink).
- `entity_risk_detail` now URL-encodes `entity_type` + `entity_value` via
  `urllib.parse.quote(safe="")` before composing the path.

### `services/api/app/api/v1/endpoints/cases.py`

- Same path validator pattern (`_SAFE_PROXY_PATH_RE`, `_validate_agents_path`),
  same `//` rejection.
- `case_investigation_run` URL-encodes `run_id` before proxying.
- `case_investigation_pdf` URL-encodes `run_id` and scrubs both `case_id` and
  `run_id` for the `Content-Disposition` filename via
  `_safe_filename_segment` (kills CR/LF/quote injection in headers).

### `.gitleaksignore`

Six triaged false positives, each with a comment:

- `detections/fixtures/positive/jwt-none-alg.json:jwt:2` — positive test fixture.
- `infra/render/render.yaml:generic-api-key:70` and `:124` — env var **names**
  (`AISOC_DISABLE_NEO4J=true`).
- `scripts/detection_specs_part3_application.py:generic-api-key:61` —
  detection rule literal `count_5min_per_ip_gt: 30`.
- `scripts/detection_specs_part2.py:jwt:1111` — positive sample for jwt-none-alg.
- `services/api/tests/test_security_defaults.py:generic-api-key:34` —
  pre-generated Fernet key for tests; production injects the real one via env.

---

## WS7 — Verification

```bash
cd services/api && source .venv/bin/activate
python -m pytest tests/test_lake_sql.py tests/test_lake_rate_limit.py -q
# 69 passed
```

`ws7-verify` (full pytest + MCP build) is still pending and tracked under the
post-rewrite block.

---

## WS8 — Bidirectional ITSM (pending)

Plan calls for:
- `push_case` + `push_status_change` capabilities on `jira_connector` and
  `servicenow` (services/connectors).
- `POST /v1/inbox/itsm` inbound webhook in `services/ingest`.
- Actions worker in `services/api/app/workers/` to fan out case events.

Reference plan §WS8.

---

## WS9 — Docs + Sonali (pending)

- `apps/docs/docs/connectors/api-coverage.md` — coverage matrix for all
  connectors × capabilities × OAuth status.
- `apps/docs/docs/architecture/itsm-as-source-of-truth.md`.
- Draft Sonali consultation question list (separate file in repo, plan §WS9).

---

## Live todo snapshot (recreate via TodoWrite after restart)

```json
[
  {"id": "gh-1",                  "content": "Audit current git state, branches, identities in history, working tree", "status": "completed"},
  {"id": "gh-4",                  "content": "Secret scan working tree before any commit/push (gitleaks)", "status": "completed"},
  {"id": "gh-2",                  "content": "Pull live GitHub code-scanning alerts and triage by severity", "status": "completed"},
  {"id": "gh-6",                  "content": "Fix code-scanning issues with minimal diffs (critical/high first)", "status": "completed"},
  {"id": "gh-5",                  "content": "Sync docs/architecture (WS3-WS7) so GitHub matches current state", "status": "pending"},
  {"id": "gh-commit-pre-rewrite", "content": "Commit all fixes/docs with Beenu Arora <beenu@cyble.com> identity (no co-author trailers)", "status": "in_progress"},
  {"id": "gh-3a",                 "content": "Backup current main to refs/heads/backup/pre-rewrite-2026-05-08 on origin", "status": "pending"},
  {"id": "gh-3b",                 "content": "Install git-filter-repo and build authors/message callbacks (strip cursoragent + AiSOC Bot trailers, canonicalize all to beenu@cyble.com)", "status": "pending"},
  {"id": "gh-3c",                 "content": "Run git-filter-repo on a fresh mirror; verify locally that all commits show Beenu Arora <beenu@cyble.com> and no Co-authored-by trailers remain", "status": "pending"},
  {"id": "gh-3d",                 "content": "Force-push rewritten main + tags + branches", "status": "pending"},
  {"id": "gh-3e",                 "content": "Verify GitHub contributors graph shows only beenu", "status": "pending"},
  {"id": "gh-prs",                "content": "Close 19 dependabot PRs with explanation; comment on #37 asking K4R7IK to rebase", "status": "pending"},
  {"id": "gh-7",                  "content": "Verify code-scanning alerts cleared after push; record any remaining", "status": "pending"},
  {"id": "ws7-tests",             "content": "WS7: finish lake endpoint unit tests (POST /lake/sql, GET /lake/schema, helpers)", "status": "pending"},
  {"id": "ws7-verify",            "content": "WS7: run full pytest + mcp build, ensure no regressions", "status": "pending"},
  {"id": "ws8-itsm",              "content": "WS8: bidirectional ITSM (push_case/push_status_change, /v1/inbox/itsm webhook, actions worker)", "status": "pending"},
  {"id": "ws9-docs",              "content": "WS9: connectors/api-coverage.md, architecture/itsm-as-source-of-truth.md, Sonali consultation question list", "status": "pending"}
]
```

---

## Environment

- Repo: `/Users/beenu/Desktop/AiSOC` (branch: `main`)
- Python venv: `services/api/.venv` → `python3.14`
- Activate: `cd services/api && source .venv/bin/activate`
- Test: `python -m pytest tests/test_lake_sql.py tests/test_lake_rate_limit.py -q`
  (currently **69 passed**; smoke check before resuming)
- MCP: `cd services/mcp && pnpm install && pnpm test`
- Git identity (verified): `Beenu Arora <beenu@cyble.com>`

---

## Resume here

1. Re-run `git log -5 --format='%h %an <%ae>%n%(trailers:only=true)'` to
   confirm no `Co-authored-by` trailers in the last few commits.
2. Continue down the GitHub hygiene table above (`gh-3a` → `gh-7`):
   backup, history rewrite via `git filter-repo`, force-push, verify
   contributors, close stale PRs, verify code-scanning alerts cleared.
3. Then resume the plan workstreams: `ws7-tests` → `ws7-verify` → WS8 → WS9.
4. Workspace rule: do not stop until every todo is done.
