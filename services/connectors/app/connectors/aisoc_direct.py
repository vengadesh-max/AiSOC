"""AiSOC Direct osqueryd TLS connector.

This connector speaks directly to ``services/osquery-tls`` — the built-in
osquery TLS service that ships with AiSOC.  Use this when you do not run
osctrl or FleetDM and instead point osquery agents straight at AiSOC using
the ``--tls_hostname`` / ``--config_plugin=tls`` flags.

Architecture
------------
::

    osquery agent
        ↓  TLS (mTLS optional)
    services/osquery-tls   ← FastAPI (port 4040)
        ↓  REST
    services/connectors    ← this connector pulls events + enrols nodes

The osquery-tls service persists:
- ``OsqueryNode``      — enrolled hosts
- ``FimEvent``         — file-integrity events from scheduled FIM queries
- ``DistributedQuery`` — ad-hoc query results (snapshots)

This connector normalises all of the above into the AiSOC event schema and
forwards them to ``services/ingest``.

Auth
----
The connector authenticates to the osquery-tls REST API using a shared
bearer token (``AISOC_TLS_INTERNAL_TOKEN`` env var, configured on both
services).  No OAuth — both services are internal to the same deployment.

Schema fields
-------------
``tls_url``
    Base URL of the osquery-tls service, e.g. ``http://osquery-tls:4040``.
``api_token``
    Internal bearer token (matches ``AISOC_TLS_INTERNAL_TOKEN`` on the
    osquery-tls side).
``tenant_id``
    Optional tenant filter — if set, only events from this tenant are
    pulled.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from app.connectors.base import BaseConnector, Capability, ConnectorSchema, Field

logger = structlog.get_logger()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _fim_severity(action: str | None) -> str:
    """Map osquery FIM action to AiSOC severity."""
    if action in ("deleted", "moved_from"):
        return "high"
    if action in ("modified", "moved_to"):
        return "medium"
    return "low"


class AiSOCDirectConnector(BaseConnector):
    """Pull events from the built-in AiSOC osquery TLS service."""

    connector_id = "aisoc_direct"
    capabilities: list[Capability] = [Capability.POLL]

    @classmethod
    def schema(cls) -> ConnectorSchema:
        return ConnectorSchema(
            name="aisoc_direct",
            label="AiSOC Direct (osqueryd TLS)",
            description=(
                "Ingest osquery telemetry from the built-in AiSOC TLS service. "
                "Use when osquery agents point directly at AiSOC (no FleetDM / osctrl)."
            ),
            category="edr",
            fields=[
                Field(
                    name="tls_url",
                    label="osquery-TLS Service URL",
                    type="string",
                    required=True,
                    placeholder="http://osquery-tls:4040",
                    description="Base URL of the AiSOC osquery-tls micro-service.",
                ),
                Field(
                    name="api_token",
                    label="Internal API Token",
                    type="string",
                    required=True,
                    secret=True,
                    description=(
                        "Bearer token matching AISOC_TLS_INTERNAL_TOKEN on the "
                        "osquery-tls service."
                    ),
                ),
                Field(
                    name="tenant_id",
                    label="Tenant ID (optional)",
                    type="string",
                    required=False,
                    description="If set, only pull events for this tenant.",
                ),
            ],
            oauth=None,
            default_poll_interval_seconds=120,
        )

    # ------------------------------------------------------------------
    # BaseConnector interface
    # ------------------------------------------------------------------

    async def _get(self, path: str, params: dict | None = None) -> Any:
        """Authenticated GET against the osquery-tls REST API."""
        url = self.auth_config["tls_url"].rstrip("/") + path
        token = self.auth_config["api_token"]
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                url,
                params=params or {},
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            return resp.json()

    async def test_connection(self) -> bool:
        try:
            data = await self._get("/health")
            return data.get("status") == "ok"
        except Exception as exc:  # noqa: BLE001
            logger.warning("aisoc_direct connection test failed", error=str(exc))
            return False

    async def poll(self) -> list[dict[str, Any]]:
        """Fetch unseen FIM events and distributed query results."""
        events: list[dict[str, Any]] = []

        tenant_id: str | None = self.auth_config.get("tenant_id") or None
        params: dict[str, Any] = {}
        if tenant_id:
            params["tenant_id"] = tenant_id

        # --- FIM events ---------------------------------------------------
        try:
            fim_resp = await self._get("/v1/osquery/fim/events", params)
            for row in fim_resp.get("events", []):
                events.append(self._normalise_fim(row))
        except Exception as exc:  # noqa: BLE001
            logger.error("aisoc_direct: FIM poll failed", error=str(exc))

        # --- Distributed query results (snapshots) ------------------------
        try:
            dq_resp = await self._get("/v1/osquery/distributed/results", params)
            for row in dq_resp.get("results", []):
                events.append(self._normalise_dq(row))
        except Exception as exc:  # noqa: BLE001
            logger.error("aisoc_direct: distributed query poll failed", error=str(exc))

        return events

    # ------------------------------------------------------------------
    # Normalisers
    # ------------------------------------------------------------------

    def _normalise_fim(self, row: dict[str, Any]) -> dict[str, Any]:
        action = row.get("action", "")
        severity = _fim_severity(action)
        ts = row.get("created_at") or row.get("time") or _now_iso()

        return {
            "source": "aisoc_direct",
            "source_event_id": str(row.get("id", "")),
            "event_type": "fim",
            "severity": severity,
            "timestamp": ts,
            "tenant_id": row.get("tenant_id"),
            "raw": row,
            "host": row.get("host_identifier", ""),
            "summary": (
                f"FIM {action}: {row.get('target_path', '')} "
                f"on {row.get('host_identifier', '')}"
            ),
            "details": {
                "path": row.get("target_path", ""),
                "action": action,
                "md5": row.get("md5", ""),
                "sha256": row.get("sha256", ""),
                "uid": row.get("uid"),
                "gid": row.get("gid"),
                "mode": row.get("mode", ""),
            },
        }

    def _normalise_dq(self, row: dict[str, Any]) -> dict[str, Any]:
        ts = row.get("fetched_at") or row.get("created_at") or _now_iso()
        query_name = row.get("query_name", "unknown")
        host = row.get("host_identifier", "")

        return {
            "source": "aisoc_direct",
            "source_event_id": str(row.get("id", "")),
            "event_type": "osquery_result",
            "severity": "info",
            "timestamp": ts,
            "tenant_id": row.get("tenant_id"),
            "raw": row,
            "host": host,
            "summary": f"osquery result [{query_name}] from {host}",
            "details": {
                "query_name": query_name,
                "columns": row.get("columns", {}),
                "action": row.get("action", "snapshot"),
            },
        }
