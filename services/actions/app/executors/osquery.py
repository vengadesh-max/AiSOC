"""Executor for the ``osquery_live_query`` action type.

Routes live osquery queries to one of three backends depending on the
``backend`` key in ``ActionRequest.parameters``:

* ``"osctrl"``      — osctrl REST API (``OsctrlClient``)
* ``"fleetdm"``     — FleetDM REST API (``FleetDMClient``)
* ``"aisoc_direct"``— AiSOC built-in osquery TLS service (``AiSOCDirectClient``)

Required parameters
-------------------
backend : str
    One of ``"osctrl"``, ``"fleetdm"``, or ``"aisoc_direct"``.
template : str
    Allowlist template ID from ``osquery_allowlist``.
target_hosts : list[str]
    List of host identifiers to target.

Backend-specific parameters
----------------------------
osctrl:
    osctrl_url, osctrl_token, osctrl_environment (optional)
fleetdm:
    fleetdm_url, fleetdm_token
aisoc_direct:
    aisoc_tls_url, aisoc_tls_token

Optional parameters
-------------------
template_params : dict
    Extra keyword arguments forwarded to the query template renderer.
timeout_seconds : int
    Maximum seconds to wait for results (default 60).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog

from app.clients.aisoc_direct_client import AiSOCDirectClient, AiSOCDirectError
from app.clients.fleetdm_client import FleetDMClient, FleetDMError
from app.clients.osctrl_client import OsctrlClient, OsctrlError
from app.clients.osquery_allowlist import AllowlistError
from app.executors.base import BaseExecutor, _SIM_FUNNEL_CTA
from app.models.action import ActionRequest, ActionResult, ActionStatus, BlastRadius

logger = structlog.get_logger()

_VALID_BACKENDS = {"osctrl", "fleetdm", "aisoc_direct"}


def _build_client(params: dict[str, Any]) -> OsctrlClient | FleetDMClient | AiSOCDirectClient:
    backend = params.get("backend", "")
    if backend == "osctrl":
        url = params.get("osctrl_url", "")
        token = params.get("osctrl_token", "")
        if not (url and token):
            raise ValueError("osctrl backend requires 'osctrl_url' and 'osctrl_token' parameters")
        return OsctrlClient(
            base_url=url,
            api_token=token,
            environment=params.get("osctrl_environment", "default"),
        )
    if backend == "fleetdm":
        url = params.get("fleetdm_url", "")
        token = params.get("fleetdm_token", "")
        if not (url and token):
            raise ValueError("fleetdm backend requires 'fleetdm_url' and 'fleetdm_token' parameters")
        return FleetDMClient(base_url=url, api_token=token)
    if backend == "aisoc_direct":
        url = params.get("aisoc_tls_url", "")
        token = params.get("aisoc_tls_token", "")
        if not (url and token):
            raise ValueError(
                "aisoc_direct backend requires 'aisoc_tls_url' and 'aisoc_tls_token' parameters"
            )
        return AiSOCDirectClient(base_url=url, api_token=token)
    raise ValueError(
        f"Unknown osquery backend '{backend}'. Must be one of: {sorted(_VALID_BACKENDS)}"
    )


class LiveQueryExecutor(BaseExecutor):
    """Run an allowlisted osquery query against target hosts via a configured backend.

    Supports osctrl, FleetDM, and the AiSOC built-in osquery TLS service.
    Falls back to simulation mode if no backend credentials are supplied.
    """

    async def execute(self, request: ActionRequest) -> ActionResult:  # noqa: C901
        params = request.parameters or {}
        backend = params.get("backend", "")
        template = params.get("template", "")
        target_hosts: list[str] = params.get("target_hosts", [])
        template_params: dict[str, Any] = params.get("template_params", {})
        timeout_seconds: int = int(params.get("timeout_seconds", 60))

        log = logger.bind(
            action_id=str(request.action_id),
            backend=backend,
            template=template,
            target_hosts=target_hosts,
        )

        # --- simulation mode when no backend or credentials are set ---
        if not backend or not template:
            missing = []
            if not backend:
                missing.append("backend")
            if not template:
                missing.append("template")
            note = (
                f"[SIMULATION] osquery_live_query skipped — missing: {', '.join(missing)}."
                + _SIM_FUNNEL_CTA
            )
            log.info("osquery live query simulated", reason="missing_parameters")
            return ActionResult(
                action_id=request.action_id,
                status=ActionStatus.COMPLETED,
                blast_radius=BlastRadius.LOW,
                output={"simulation": True, "note": note, "results": {}},
                completed_at=datetime.utcnow(),
            )

        if backend not in _VALID_BACKENDS:
            return ActionResult(
                action_id=request.action_id,
                status=ActionStatus.FAILED,
                blast_radius=BlastRadius.LOW,
                error=f"Unknown backend '{backend}'. Must be one of: {sorted(_VALID_BACKENDS)}",
                completed_at=datetime.utcnow(),
            )

        try:
            client = _build_client(params)
        except ValueError as exc:
            return ActionResult(
                action_id=request.action_id,
                status=ActionStatus.FAILED,
                blast_radius=BlastRadius.LOW,
                error=str(exc),
                completed_at=datetime.utcnow(),
            )

        log.info("running osquery live query")
        try:
            outcome = await client.live_query(
                target_hosts=target_hosts,
                template=template,
                template_params=template_params,
                timeout_seconds=timeout_seconds,
            )
        except AllowlistError as exc:
            log.warning("allowlist rejection", template=template)
            return ActionResult(
                action_id=request.action_id,
                status=ActionStatus.FAILED,
                blast_radius=BlastRadius.LOW,
                error=f"Query template '{template}' is not on the osquery allowlist: {exc}",
                completed_at=datetime.utcnow(),
            )
        except (OsctrlError, FleetDMError, AiSOCDirectError) as exc:
            log.error("osquery live query failed", error=str(exc))
            return ActionResult(
                action_id=request.action_id,
                status=ActionStatus.FAILED,
                blast_radius=BlastRadius.LOW,
                error=str(exc),
                completed_at=datetime.utcnow(),
            )

        query_error = outcome.get("error")
        status = ActionStatus.COMPLETED if not query_error else ActionStatus.FAILED

        log.info(
            "osquery live query completed",
            host_count=len(outcome.get("results", {})),
            error=query_error,
        )

        return ActionResult(
            action_id=request.action_id,
            status=status,
            blast_radius=BlastRadius.LOW,
            output={
                "backend": backend,
                "template": template,
                "results": outcome.get("results", {}),
            },
            error=query_error,
            completed_at=datetime.utcnow(),
        )
