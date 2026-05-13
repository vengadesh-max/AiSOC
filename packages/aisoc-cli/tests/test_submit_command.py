"""CLI surface tests for ``aisoc submit``.

These tests pin the contract the founder-flow quickstart relies on:

* ``aisoc submit <file>`` is registered and discoverable via ``--help``.
* The CLI POSTs to ``{ingest_url}/v1/ingest/batch`` with an ``X-Tenant-ID``
  header and the canonical ingest envelope shape (connector_id,
  connector_type, source_format, events).
* Fixture-level overrides (a file with ``connector_id`` / ``connector_type``
  / ``source_format`` keys) win over the matching CLI flags.
* The CLI surfaces accepted / rejected counts on success, and exits non-zero
  on transport errors, 4xx responses, or non-JSON responses.

We use ``httpx.MockTransport`` to intercept the HTTP call without standing
up a real ingest service. If these tests break, the quickstart video will
desync from the CLI.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from click.testing import CliRunner

from aisoc_cli import main as cli_main
from aisoc_cli.main import cli


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _write_fixture(tmp_path: Path, payload: Any, name: str = "alert.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class _CapturingTransport(httpx.MockTransport):
    """Mock transport that records each request and returns a canned response."""

    def __init__(self, response_factory):
        self.requests: list[httpx.Request] = []
        super().__init__(self._handler)
        self._response_factory = response_factory

    def _handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._response_factory(request)


def _install_transport(
    monkeypatch: pytest.MonkeyPatch,
    response_factory,
) -> _CapturingTransport:
    """Patch ``httpx.Client`` so the CLI uses the mock transport."""
    transport = _CapturingTransport(response_factory)
    real_client = httpx.Client

    def _client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(cli_main.httpx, "Client", _client_factory)
    return transport


def test_submit_help_lists_command(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0, result.output
    assert "submit" in result.output


def test_submit_help_shows_options(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["submit", "--help"])
    assert result.exit_code == 0, result.output
    for flag in ("--ingest-url", "--tenant-id", "--connector-id", "--connector-type", "--source-format"):
        assert flag in result.output


def test_submit_posts_envelope_with_tenant_header(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_path = _write_fixture(
        tmp_path,
        {
            "events": [
                {"uuid": "abc", "eventType": "user.session.start"},
                {"uuid": "def", "eventType": "user.session.start"},
            ]
        },
    )

    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"accepted": 2, "rejected": 0, "request_id": "req-001"},
        )

    transport = _install_transport(monkeypatch, respond)

    result = runner.invoke(
        cli,
        ["submit", str(fixture_path), "--ingest-url", "http://localhost:8081"],
    )
    assert result.exit_code == 0, result.output
    assert len(transport.requests) == 1

    req = transport.requests[0]
    assert req.method == "POST"
    assert str(req.url) == "http://localhost:8081/v1/ingest/batch"
    assert req.headers["X-Tenant-ID"] == "00000000-0000-0000-0000-000000000001"
    assert req.headers["Content-Type"].startswith("application/json")

    body = json.loads(req.content.decode("utf-8"))
    assert body["connector_id"] == "aisoc-cli-submit"
    assert body["connector_type"] == "okta_system_log"
    assert body["source_format"] == "json"
    assert len(body["events"]) == 2

    assert "accepted: 2" in result.output
    assert "request_id: req-001" in result.output


def test_submit_accepts_bare_list_payload(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_path = _write_fixture(tmp_path, [{"uuid": "1"}, {"uuid": "2"}])

    transport = _install_transport(
        monkeypatch,
        lambda _: httpx.Response(200, json={"accepted": 2, "rejected": 0}),
    )

    result = runner.invoke(cli, ["submit", str(fixture_path)])
    assert result.exit_code == 0, result.output
    body = json.loads(transport.requests[0].content.decode("utf-8"))
    assert len(body["events"]) == 2


def test_submit_wraps_single_object_payload(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_path = _write_fixture(tmp_path, {"uuid": "only"})

    transport = _install_transport(
        monkeypatch,
        lambda _: httpx.Response(200, json={"accepted": 1, "rejected": 0}),
    )

    result = runner.invoke(cli, ["submit", str(fixture_path)])
    assert result.exit_code == 0, result.output
    body = json.loads(transport.requests[0].content.decode("utf-8"))
    assert body["events"] == [{"uuid": "only"}]


def test_submit_fixture_overrides_beat_cli_flags(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_path = _write_fixture(
        tmp_path,
        {
            "connector_id": "fixture-conn",
            "connector_type": "splunk_enterprise",
            "source_format": "raw_json",
            "events": [{"uuid": "x"}],
        },
    )

    transport = _install_transport(
        monkeypatch,
        lambda _: httpx.Response(200, json={"accepted": 1, "rejected": 0}),
    )

    result = runner.invoke(
        cli,
        [
            "submit",
            str(fixture_path),
            "--connector-id",
            "cli-conn",
            "--connector-type",
            "okta_system_log",
            "--source-format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    body = json.loads(transport.requests[0].content.decode("utf-8"))
    assert body["connector_id"] == "fixture-conn"
    assert body["connector_type"] == "splunk_enterprise"
    assert body["source_format"] == "raw_json"


def test_submit_uses_cli_flags_when_fixture_has_no_overrides(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_path = _write_fixture(tmp_path, {"events": [{"uuid": "x"}]})

    transport = _install_transport(
        monkeypatch,
        lambda _: httpx.Response(200, json={"accepted": 1, "rejected": 0}),
    )

    result = runner.invoke(
        cli,
        [
            "submit",
            str(fixture_path),
            "--tenant-id",
            "deadbeef-0000-4000-8000-000000000000",
            "--connector-id",
            "demo-cli",
            "--connector-type",
            "okta_system_log",
        ],
    )
    assert result.exit_code == 0, result.output
    req = transport.requests[0]
    assert req.headers["X-Tenant-ID"] == "deadbeef-0000-4000-8000-000000000000"
    body = json.loads(req.content.decode("utf-8"))
    assert body["connector_id"] == "demo-cli"


def test_submit_env_overrides_default_url_and_tenant(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_path = _write_fixture(tmp_path, {"events": [{"uuid": "x"}]})
    monkeypatch.setenv("AISOC_INGEST_URL", "http://ingest-worker:8080")
    monkeypatch.setenv("AISOC_TENANT_ID", "11111111-1111-4111-8111-111111111111")

    transport = _install_transport(
        monkeypatch,
        lambda _: httpx.Response(200, json={"accepted": 1, "rejected": 0}),
    )

    result = runner.invoke(cli, ["submit", str(fixture_path)])
    assert result.exit_code == 0, result.output
    req = transport.requests[0]
    assert str(req.url) == "http://ingest-worker:8080/v1/ingest/batch"
    assert req.headers["X-Tenant-ID"] == "11111111-1111-4111-8111-111111111111"


def test_submit_returns_nonzero_on_4xx(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_path = _write_fixture(tmp_path, {"events": [{"uuid": "x"}]})
    _install_transport(
        monkeypatch,
        lambda _: httpx.Response(400, text="missing X-Tenant-ID"),
    )

    result = runner.invoke(cli, ["submit", str(fixture_path)])
    assert result.exit_code == 1, result.output
    assert "Ingest service returned 400" in result.output


def test_submit_returns_nonzero_on_transport_error(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_path = _write_fixture(tmp_path, {"events": [{"uuid": "x"}]})

    def boom(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    _install_transport(monkeypatch, boom)

    result = runner.invoke(cli, ["submit", str(fixture_path)])
    assert result.exit_code == 1, result.output
    assert "Ingest service unreachable" in result.output
    assert "aisoc serve" in result.output


def test_submit_rejects_invalid_json(
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    bad = tmp_path / "broken.json"
    bad.write_text("{ not json", encoding="utf-8")

    result = runner.invoke(cli, ["submit", str(bad)])
    assert result.exit_code != 0, result.output
    assert "not valid JSON" in result.output


def test_submit_rejects_empty_event_list(
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    fixture_path = _write_fixture(tmp_path, {"events": []})

    result = runner.invoke(cli, ["submit", str(fixture_path)])
    assert result.exit_code != 0, result.output
    assert "empty" in result.output.lower()


def test_submit_rejects_non_object_event(
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    fixture_path = _write_fixture(tmp_path, {"events": ["just-a-string"]})

    result = runner.invoke(cli, ["submit", str(fixture_path)])
    assert result.exit_code != 0, result.output
    assert "not a JSON object" in result.output


def test_submit_real_lateral_movement_fixture(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Smoke-test the actual repo fixture so the demo path stays wired up."""
    repo_root = Path(__file__).resolve().parents[3]
    fixture_path = repo_root / "examples" / "alerts" / "lateral-movement.json"
    if not fixture_path.exists():
        pytest.skip("lateral-movement fixture is created in this PR; run from repo root")

    transport = _install_transport(
        monkeypatch,
        lambda _: httpx.Response(200, json={"accepted": 2, "rejected": 0, "request_id": "req-9"}),
    )

    result = runner.invoke(cli, ["submit", str(fixture_path)])
    assert result.exit_code == 0, result.output
    body = json.loads(transport.requests[0].content.decode("utf-8"))
    assert len(body["events"]) == 2
    assert body["events"][0]["actor"]["alternateId"] == "alice@example.com"
