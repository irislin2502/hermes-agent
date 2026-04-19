"""
SYS004: Gateway Reply message map.

Tracks Telegram message_id → cron job context so that when Iris replies
to a cron report, Gateway can attach the full output as context.

Schema for ~/.hermes/cron/message_map.json:
{
    "<telegram_message_id>": {
        "job_id":          "<cron job id>",
        "job_name":        "<human-readable job name>",
        "run_at":          "<ISO-8601 timestamp>",
        "session_id":      "<cron session id used for the run>",
        "full_output_path": "<absolute path to the saved full output file>"
    },
    ...
}

The file is written atomically (write-to-tmp then rename) to avoid
corruption when both the cron scheduler thread and the gateway reader
thread access it concurrently.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Maximum number of entries to retain in the map (older entries pruned first).
_MAX_ENTRIES = 500


def _map_path() -> Path:
    """Return the path to message_map.json, creating the directory if needed."""
    try:
        from hermes_constants import get_hermes_home
        hermes_home = get_hermes_home()
    except Exception:
        hermes_home = Path.home() / ".hermes"

    cron_dir = hermes_home / "cron"
    cron_dir.mkdir(parents=True, exist_ok=True)
    return cron_dir / "message_map.json"


def _load_map() -> dict:
    """Load the current message map from disk. Returns {} on any error."""
    path = _map_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        logger.debug("cron_message_map: could not load %s: %s", path, exc)
        return {}


def _save_map(data: dict) -> None:
    """Atomically write *data* to message_map.json."""
    path = _map_path()
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), prefix=".message_map_", suffix=".tmp"
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, str(path))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as exc:
        logger.warning("cron_message_map: could not write %s: %s", path, exc)


def save_message_mapping(
    *,
    message_id: str,
    job_id: str,
    job_name: str,
    run_at: str,
    session_id: str,
    output_path: str,
) -> None:
    """Record a Telegram message_id → cron job mapping.

    Called by the cron scheduler after successfully delivering a cron
    report to Telegram.

    Args:
        message_id:   Telegram message_id (as string) of the sent report.
        job_id:       Internal cron job id.
        job_name:     Human-readable job name.
        run_at:       ISO-8601 timestamp of when the job ran.
        session_id:   Hermes session id for the cron run.
        output_path:  Absolute path to the full output file on disk.
    """
    if not message_id:
        return

    data = _load_map()
    data[str(message_id)] = {
        "job_id": job_id,
        "job_name": job_name,
        "run_at": run_at,
        "session_id": session_id,
        "full_output_path": output_path,
    }

    # Prune oldest entries if over the limit
    if len(data) > _MAX_ENTRIES:
        # Keep only the last _MAX_ENTRIES keys (insertion order in Python 3.7+)
        keys = list(data.keys())
        for old_key in keys[: len(data) - _MAX_ENTRIES]:
            del data[old_key]

    _save_map(data)
    logger.debug(
        "cron_message_map: saved mapping message_id=%s → job=%s", message_id, job_id
    )


def lookup_message(message_id: str) -> Optional[dict]:
    """Look up cron context for a Telegram message_id.

    Returns the dict entry (with keys: job_id, job_name, run_at,
    session_id, full_output_path) if found, or None.
    """
    if not message_id:
        return None
    data = _load_map()
    return data.get(str(message_id))


def get_output_content(entry: dict, max_bytes: int = 200_000) -> Optional[str]:
    """Read and return the full output content for a cron job map entry.

    Args:
        entry:     A dict returned by :func:`lookup_message`.
        max_bytes: Maximum bytes to read from the output file (default 200 KB).
                   Prevents huge outputs from flooding the context window.

    Returns:
        The file contents as a string, or None if the file is missing/unreadable.
    """
    if not entry:
        return None

    output_path = entry.get("full_output_path", "")
    if not output_path:
        return None

    path = Path(output_path)
    if not path.exists():
        logger.debug("cron_message_map: output file not found: %s", output_path)
        return None

    try:
        raw = path.read_bytes()
        if len(raw) > max_bytes:
            # Truncate from the beginning to keep the most recent output
            raw = raw[-max_bytes:]
            content = raw.decode("utf-8", errors="replace")
            content = f"[... output truncated, showing last {max_bytes} bytes ...]\n" + content
        else:
            content = raw.decode("utf-8", errors="replace")
        return content
    except Exception as exc:
        logger.debug("cron_message_map: could not read output file %s: %s", output_path, exc)
        return None
