"""Apply raw SQL migrations under ``services/api/migrations``.

This is a lightweight, idempotent migration runner. Each migration is executed
inside a transaction and tracked in an ``aisoc_schema_migrations`` table so it
will not be re-applied on subsequent runs. Migrations themselves are written
defensively (``CREATE TABLE IF NOT EXISTS``, ``ALTER TABLE … ADD COLUMN IF NOT
EXISTS``) so partial re-applies are safe.

Run via:

    python -m app.scripts.run_migrations
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from sqlalchemy import text

from app.db.database import engine

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


CREATE_MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS aisoc_schema_migrations (
    name        TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


async def _raw_execute(conn, sql: str) -> None:
    """Execute SQL on the underlying asyncpg connection.

    SQLAlchemy's ``text()`` path uses asyncpg's prepared-statement protocol,
    which cannot handle multi-statement SQL (``cannot insert multiple commands
    into a prepared statement``). The raw asyncpg ``execute()`` uses the simple
    query protocol and accepts multi-statement scripts, which is what every
    file under ``services/api/migrations`` ships.
    """
    raw = await conn.get_raw_connection()
    asyncpg_conn = raw.driver_connection
    await asyncpg_conn.execute(sql)


async def _applied(conn) -> set[str]:
    res = await conn.execute(text("SELECT name FROM aisoc_schema_migrations"))
    return {row[0] for row in res}


async def _record(conn, name: str) -> None:
    await conn.execute(
        text("INSERT INTO aisoc_schema_migrations(name) VALUES (:n) ON CONFLICT DO NOTHING").bindparams(n=name)
    )


async def _apply_one(name: str, sql: str) -> tuple[str, bool, str | None]:
    """Apply a single migration in its own transaction.

    Returns (name, ok, error). Failures are recorded but don't crash the runner
    so later migrations (which may not depend on the failing one) can still
    apply. The deploy log will surface which migrations failed.
    """
    try:
        async with engine.begin() as conn:
            await _raw_execute(conn, sql)
            await _record(conn, name)
        return name, True, None
    except Exception as exc:  # noqa: BLE001 — we intentionally continue on failure
        return name, False, str(exc)


async def main() -> None:
    if not MIGRATIONS_DIR.exists():
        logger.warning("migrations dir not found: %s", MIGRATIONS_DIR)
        return

    files = sorted(p for p in MIGRATIONS_DIR.iterdir() if p.suffix == ".sql")
    logger.info("Found %d migration files", len(files))

    async with engine.begin() as conn:
        await _raw_execute(conn, CREATE_MIGRATIONS_TABLE)

    async with engine.connect() as conn:
        already = await _applied(conn)

    pending = [p for p in files if p.name not in already]
    logger.info("%d migrations already applied; %d pending", len(already), len(pending))

    failures: list[tuple[str, str]] = []
    for path in pending:
        sql = path.read_text(encoding="utf-8")
        name, ok, err = await _apply_one(path.name, sql)
        if ok:
            logger.info("✓ applied %s", name)
        else:
            logger.error("✗ failed %s: %s", name, err)
            failures.append((name, err or ""))

    if failures:
        logger.warning("%d migrations failed; see logs above", len(failures))


if __name__ == "__main__":
    asyncio.run(main())
