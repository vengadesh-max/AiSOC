---
sidebar_position: 3
title: AiSOC Direct (osqueryd TLS)
description: Ingest osquery telemetry via AiSOC's built-in TLS endpoint — no FleetDM or osctrl required.
---

# AiSOC Direct (osqueryd TLS)

The **AiSOC Direct** connector ingests osquery telemetry from the built-in
`services/osquery-tls` micro-service.  Choose this connector when you want
to point osquery agents straight at AiSOC without running FleetDM or osctrl.

```
osquery agent
    ↓  TLS / mTLS
services/osquery-tls  (port 4040)
    ↓  internal REST
services/connectors   ← this connector
    ↓
services/ingest → alert-fusion → agents
```

## What you get

| Event type | Source | Severity |
|---|---|---|
| FIM events | `/v1/osquery/fim/events` | `high` (delete/move), `medium` (modify), `low` (create) |
| Distributed query results | `/v1/osquery/distributed/results` | `info` by default |

## Prerequisites

1. **Deploy `services/osquery-tls`** — ensure the service is reachable from
   `services/connectors`.

2. **Set `AISOC_TLS_INTERNAL_TOKEN`** on both services to the same secret value.

3. **Enroll osquery agents** using the osquery-tls enrolment secret:

   ```bash
   osqueryd \
     --tls_hostname=<your-aisoc-host>:4040 \
     --config_plugin=tls \
     --config_tls_endpoint=/config \
     --enroll_tls_endpoint=/enroll \
     --enroll_secret_path=/etc/osquery/enroll.secret \
     --logger_plugin=tls \
     --logger_tls_endpoint=/log
   ```

## Schema fields

| Field | Required | Notes |
|---|---|---|
| `tls_url` | ✅ | Base URL of osquery-tls, e.g. `http://osquery-tls:4040` |
| `api_token` | ✅ | Bearer token matching `AISOC_TLS_INTERNAL_TOKEN` |
| `tenant_id` | ❌ | Optional — filter events to one tenant |

## FIM configuration

To enable File Integrity Monitoring, add a FIM pack to the osquery config
delivered by the TLS service.  The AiSOC UI lets you assign pre-built packs
(including `fim-linux-critical`, `fim-macos-critical`) to enrolled tenants.

See the [Osquery TLS Service](./osquery-tls) documentation for pack management
details.

## Troubleshooting

### "Connection test failed"

- Confirm `tls_url` is reachable from `services/connectors` — use
  `curl http://osquery-tls:4040/health` inside the container.
- Check `AISOC_TLS_INTERNAL_TOKEN` matches on both services.

### No FIM events appearing

- Verify the FIM pack is assigned to the tenant in **Settings → Osquery Packs**.
- Check `osqueryd` logs: the `file_events` table requires `--audit_allow_fim_events=true`.

### High cardinality from distributed queries

Distributed query results default to `severity: info`.  Create detection
rules in `detections/endpoint/` to promote interesting rows.
