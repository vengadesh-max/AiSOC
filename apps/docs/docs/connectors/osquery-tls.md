---
sidebar_position: 16
title: Osquery TLS Service
description: Built-in osquery TLS endpoint — enrol agents, deliver configs, receive logs and distributed query results.
---

# Osquery TLS Service

AiSOC ships a built-in **osquery TLS service** (`services/osquery-tls`) so you
can connect raw osquery agents without a third-party fleet manager.  It
implements the osquery TLS plugin API:

| osquery endpoint | Purpose |
|---|---|
| `POST /enroll` | Register a new host, return `node_key` |
| `POST /config` | Deliver osquery config + pack assignments |
| `POST /log` | Receive query results and status logs |
| `POST /distributed/read` | Return pending ad-hoc queries |
| `POST /distributed/write` | Accept ad-hoc query results |

## Quick start

### 1. Start the service

The service starts automatically via Docker Compose:

```bash
docker compose -f docker-compose.dev.yml up osquery-tls
```

Default port: **4040**.

### 2. Enrol an agent

```bash
# Generate an enrolment secret in AiSOC Settings → Osquery Nodes
# Then on the host:
echo "<your-enrolment-secret>" > /etc/osquery/enroll.secret

osqueryd \
  --tls_hostname=<your-aisoc-host>:4040 \
  --config_plugin=tls \
  --config_tls_endpoint=/config \
  --enroll_tls_endpoint=/enroll \
  --enroll_secret_path=/etc/osquery/enroll.secret \
  --logger_plugin=tls \
  --logger_tls_endpoint=/log \
  --distributed_plugin=tls \
  --distributed_tls_read_endpoint=/distributed/read \
  --distributed_tls_write_endpoint=/distributed/write
```

### 3. Assign osquery packs

1. Go to **Settings → Osquery Packs** in the AiSOC UI.
2. Click **Assign Pack** next to any pack in the catalog.
3. Select the target tenant — the config endpoint will include the pack on the
   agent's next check-in.

## Available packs

| Pack ID | Description |
|---|---|
| `fim-linux-critical` | FIM on `/etc`, `/bin`, `/usr/bin`, `/usr/sbin`, `/sbin` |
| `fim-macos-critical` | FIM on `/etc`, `/usr/bin`, `/usr/sbin`, `/Library/LaunchDaemons` |
| `network-connections` | Active listening ports, unusual outbound connections |
| `process-inventory` | Running processes with hashes |
| `user-accounts` | Local user and group enumeration |

## mTLS (optional)

For mutual TLS, mount a CA cert and set `AISOC_TLS_CA_CERT` in the service
environment.  Agents must present a client certificate signed by that CA.

```bash
osqueryd \
  --tls_client_cert=/etc/osquery/client.crt \
  --tls_client_key=/etc/osquery/client.key \
  ...
```

## FIM events

File Integrity Monitoring events appear in **Alerts → FIM Events** after the
`fim-linux-critical` or `fim-macos-critical` pack is assigned.  Each file
event carries:

- `target_path` — absolute file path
- `action` — `created`, `modified`, `deleted`, `moved_from`, `moved_to`
- `md5` / `sha256` — file hashes (on supported kernels)
- `uid` / `gid` / `mode` — POSIX metadata

## Troubleshooting

### Agent fails to enrol

- Check the enrolment secret: `GET /v1/admin/enrolment-secret` (internal API).
- Confirm TLS is valid — osquery validates the server certificate.  In dev,
  pass `--tls_server_certs=/etc/osquery/server.crt` or set
  `--insecure_transport` (dev only).

### Config not updating

Osquery only fetches config at `--config_refresh` interval (default 600 s).
For immediate update, restart osquery or reduce the interval.

### No distributed query results

Ensure `--distributed_plugin=tls` is set and both `read` + `write` endpoints
are configured.
