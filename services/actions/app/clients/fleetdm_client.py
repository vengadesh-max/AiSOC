"""Async client for issuing osquery live queries via the FleetDM REST API.

FleetDM exposes `/api/v1/fleet/queries/run` (synchronous) and the live-query
WebSocket endpoint.  This client uses the **synchronous REST endpoint** which
blocks until all targeted hosts have responded or the timeout is reached,
making it the simplest path for playbook integration.

API references
--------------
  https://fleetdm.com/docs/using-fleet/rest-api#run-live-query
  POST /api/v1/fleet/queries/run
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.clients.osquery_allowlist import render_query

logger = logging.getLogger(__name__)


class FleetDMError(RuntimeError):
    """Raised when a FleetDM API call fails."""


class FleetDMClient:
    """Async REST client for FleetDM live queries.

    Parameters
    ----------
    base_url:
        Root URL of the FleetDM instance, e.g. ``https://fleet.example.com``.
    api_token:
        FleetDM API token (from Settings → API token or service-account token).
    verify_tls:
        Whether to verify the server certificate.
    """

    def __init__(
        self,
        base_url: str,
        api_token: str,
        verify_tls: bool = True,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_token = api_token
        self._verify_tls = verify_tls

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
        """Run an allowlisted query against *target_hosts* via FleetDM.

        Parameters
        ----------
        target_hosts:
            List of FleetDM host identifiers (hostname or numeric host ID).
            An empty list runs against **all** online hosts in FleetDM —
            use with caution on large fleets.
        template:
            Approved allowlist template ID (see ``osquery_allowlist.py``).
        template_params:
            Parameters forwarded to the template renderer.
        timeout_seconds:
            Passed as ``timeout`` to the FleetDM synchronous run endpoint.

        Returns
        -------
        dict
            ``{"results": {host_identifier: [rows]}, "error": str | None}``

        Raises
        ------
        FleetDMError
            On HTTP failures.
        AllowlistError
            If *template* is not approved.
        """
        sql = render_query(template, **(template_params or {}))

        payload: dict[str, Any] = {
            "query": sql,
            "timeout": timeout_seconds,
        }

        if target_hosts:
            # FleetDM accepts a list of host identifiers or host IDs.
            host_ids: list[int] = []
            host_names: list[str] = []
            for h in target_hosts:
                try:
                    host_ids.append(int(h))
                except ValueError:
                    host_names.append(h)

            if host_ids:
                payload["host_ids"] = host_ids
            if host_names:
                payload["hostnames"] = host_names

        url = f"{self._base_url}/api/v1/fleet/queries/run"

        try:
            async with httpx.AsyncClient(
                verify=self._verify_tls, timeout=timeout_seconds + 10.0
            ) as client:
                resp = await client.post(url, json=payload, headers=self._headers())
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            raise FleetDMError(f"FleetDM live query failed: {exc}") from exc

        # FleetDM response shape:
        # {"results": [{"host": {…}, "rows": [{…}], "error": str|null}]}
        rows_by_host: dict[str, list[dict]] = {}
        errors: list[str] = []

        for entry in data.get("results", []):
            host_info = entry.get("host", {})
            host_key = (
                host_info.get("hostname")
                or host_info.get("display_name")
                or str(host_info.get("id", "unknown"))
            )
            if entry.get("error"):
                errors.append(f"{host_key}: {entry['error']}")
                continue
            rows_by_host[host_key] = entry.get("rows", [])

        return {
            "results": rows_by_host,
            "error": "; ".join(errors) if errors else None,
        }
