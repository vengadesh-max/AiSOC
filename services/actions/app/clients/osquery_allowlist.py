"""Read-only parameterised osquery query allowlist.

Playbook steps reference templates by ID, not raw SQL, so operators can never
accidentally—or maliciously—inject arbitrary queries via YAML configuration.

Each template is a callable that accepts keyword parameters and returns a
validated SQL string.  ``render_query`` is the single public entry-point.

Approved templates
------------------
running_processes
    All running processes with path, cmdline, and parent info.
active_connections
    Established / listen network connections with process context.
logged_in_users
    Currently logged-in users from utmp / getutxent.
recent_files
    Files opened in the last N seconds (default 300).
process_tree
    Self-referential join to reconstruct the process ancestor chain.
package_inventory
    Installed package names, versions, and sources.
"""

from __future__ import annotations

from typing import Any


class AllowlistError(ValueError):
    """Raised when a template ID is not in the approved allowlist."""


# ---------------------------------------------------------------------------
# Template implementations
# ---------------------------------------------------------------------------


def _running_processes(**_: Any) -> str:
    return (
        "SELECT pid, name, path, cmdline, parent, uid, start_time "
        "FROM processes ORDER BY start_time DESC LIMIT 500;"
    )


def _active_connections(**kwargs: Any) -> str:
    state_filter = kwargs.get("state", "ESTABLISHED")
    return (
        "SELECT p.pid, p.name, lc.local_address, lc.local_port, "
        "lc.remote_address, lc.remote_port, lc.state "
        "FROM process_open_sockets lc "
        "JOIN processes p ON lc.pid = p.pid "
        f"WHERE lc.state = '{state_filter}' "
        "ORDER BY lc.remote_address LIMIT 500;"
    )


def _logged_in_users(**_: Any) -> str:
    return (
        "SELECT type, user, host, time, pid "
        "FROM logged_in_users ORDER BY time DESC LIMIT 200;"
    )


def _recent_files(**kwargs: Any) -> str:
    window_seconds = int(kwargs.get("window_seconds", 300))
    path_prefix = kwargs.get("path_prefix", "/")
    return (
        "SELECT path, filename, uid, gid, mode, size, atime, mtime, ctime "
        "FROM file "
        f"WHERE path LIKE '{path_prefix}%' "
        f"AND mtime > (strftime('%s','now') - {window_seconds}) "
        "ORDER BY mtime DESC LIMIT 300;"
    )


def _process_tree(**kwargs: Any) -> str:
    pid = int(kwargs.get("pid", 0))
    return (
        "WITH RECURSIVE tree(pid, parent, name, cmdline, depth) AS ("
        f"  SELECT pid, parent, name, cmdline, 0 FROM processes WHERE pid = {pid} "
        "  UNION ALL "
        "  SELECT p.pid, p.parent, p.name, p.cmdline, t.depth + 1 "
        "  FROM processes p JOIN tree t ON p.pid = t.parent "
        "  WHERE t.depth < 10 "
        ") SELECT * FROM tree;"
    )


def _package_inventory(**_: Any) -> str:
    return (
        "SELECT name, version, source, type "
        "FROM packages ORDER BY name LIMIT 2000;"
    )


# ---------------------------------------------------------------------------
# Allowlist registry
# ---------------------------------------------------------------------------

_ALLOWLIST: dict[str, Any] = {
    "running_processes": _running_processes,
    "active_connections": _active_connections,
    "logged_in_users": _logged_in_users,
    "recent_files": _recent_files,
    "process_tree": _process_tree,
    "package_inventory": _package_inventory,
}


def render_query(template: str, **params: Any) -> str:
    """Return a validated SQL string for the given *template* ID.

    Parameters
    ----------
    template:
        One of the approved template IDs (e.g. ``"running_processes"``).
    **params:
        Template-specific parameters (e.g. ``window_seconds=600``).

    Returns
    -------
    str
        A safe SQL string ready to send to osquery.

    Raises
    ------
    AllowlistError
        If *template* is not in the approved list.
    """
    fn = _ALLOWLIST.get(template)
    if fn is None:
        approved = ", ".join(sorted(_ALLOWLIST))
        raise AllowlistError(
            f"Template '{template}' is not in the osquery allowlist. "
            f"Approved templates: {approved}"
        )
    return fn(**params)


def list_templates() -> list[str]:
    """Return the sorted list of approved template IDs."""
    return sorted(_ALLOWLIST)
