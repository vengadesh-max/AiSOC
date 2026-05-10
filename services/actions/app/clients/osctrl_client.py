"""Async client for issuing osquery live queries via the osctrl REST API.

osctrl exposes distributed query endpoints that allow you to enqueue a SQL
query against a named environment and collect per-node results.  This client
wraps those endpoints for use by the ``osquery_live_query`` playbook step.

API references
--------------
  POST /api/v1/queries          — create a new distributed query
  GET  /api/v1/queries/{uuid}   — fetch query status + results
  GET  /api/v1/nodes            — enumerate nodes (for host resolution)

The osctrl API uses a bearer token issued from the admin console.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.clients.osquery_allowlist import render_query

logger = logging.getLogger(__name__)


class OsctrlError(RuntimeError):
    """Raised when an osctrl API call fails."""


class OsctrlClient:
    """Async REST client for osctrl live queries.

    Parameters
    ----------
    base_url:
        Root URL of the osctrl API, e.g. ``https://osctrl.example.com``.
    api_token:
        Bearer token from the osctrl admin console.
    environment:
        osctrl environment name to target (default ``"default"``).
    verify_tls:
        Whether to verify the server certificate.
    poll_interval:
        Seconds between status polls.
    """

    def __init__(
        self,
        base_url: str,
        api_token: str,
        environment: str = "default",
        verify_tls: bool = True,
        poll_interval: float = 5.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_token = api_token
        self._environment = environment
        self._verify_tls = verify_tls
        self._poll_interval = poll_interval

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_token}",
            "Content-Type": "application/json",
        }

    async def live_query(
        self,
        target_hosts: list[str],
        template: str,
        template_params: dict[str, Any] | None = None,
        timeout_seconds: int = 60,
    ) -> dict[str, Any]:
        """Run an allowlisted query against *target_hosts* via osctrl.

        The query is enqueued once for the whole environment and results are
        filtered to *target_hosts*.  If *target_hosts* is empty the query
        runs against **all** nodes in the environment.

        Returns
        -------
        dict
            ``{"results": {host_identifier: [rows]}, "error": str | None}``

        Raises
        ------
        OsctrlError
            On HTTP failures.
        AllowlistError
            If *template* is not approved.
        """
        sql = render_query(template, **(template_params or {}))

        async with httpx.AsyncClient(
            verify=self._verify_tls, timeout=30.0
        ) as client:
            query_uuid = await self._create_query(client, sql, target_hosts)
            results = await self._poll(client, query_uuid, timeout_seconds)

        return results

    async def _create_query(
        self,
        client: httpx.AsyncClient,
        sql: str,
        target_hosts: list[str],
    ) -> str:
        """POST /api/v1/queries and return the query UUID."""
        payload: dict[str, Any] = {
            "environment": self._environment,
            "query": sql,
        }
        if target_hosts:
            payload["nodes"] = target_hosts

        url = f"{self._base_url}/api/v1/queries"
        try:
            resp = await client.post(url, json=payload, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
            return data["uuid"]
        except httpx.HTTPError as exc:
            raise OsctrlError(f"Failed to create osctrl query: {exc}") from exc

    async def _poll(
        self,
        client: httpx.AsyncClient,
        query_uuid: str,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        """Poll GET /api/v1/queries/{uuid} until complete or timeout."""
        url = f"{self._base_url}/api/v1/queries/{query_uuid}"
        elapsed = 0.0

        while elapsed < timeout_seconds:
            await asyncio.sleep(self._poll_interval)
            elapsed += self._poll_interval
            try:
                resp = await client.get(url, headers=self._headers())
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPError as exc:
                logger.warning("osctrl poll error for %s: %s", query_uuid, exc)
                continue

            status = data.get("status", "")
            if status in ("complete", "completed"):
                rows_by_host: dict[str, list[dict]] = {}
                for entry in data.get("results", []):
                    host = entry.get("node", entry.get("host_identifier", "unknown"))
                    rows_by_host.setdefault(host, []).extend(entry.get("rows", []))
                return {"results": rows_by_host, "error": None}

        return {
            "results": {},
            "error": f"Timeout waiting for osctrl query {query_uuid}",
        }
