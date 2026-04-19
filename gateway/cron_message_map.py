"""
Manages mapping between Telegram message_id and cron job output paths.

Schema:
{
    "<telegram_message_id>": {
        "job_id": str,
        "job_name": str,
        "run_at": str,        # ISO-8601 timestamp
        "session_id": str,
        "output_path": str    # absolute path to the saved output file
    }
}

File location: ~/.hermes/cron/message_map.json
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Max entries to retain — oldest entries are pruned when the map grows beyond this
_MAX_ENTRIES = 500

def _map_path() -> Path:
    """Return the path to the message_map.json file."""
    try:
        from hermes_constants import get_hermes_home
        hermes_home = get_hermes_home()
    except Exception:
        hermes_home = Path(os.path.expanduser("~")) / ".hermes"
    return hermes_home / "cron" / "message_map.json"


def load_message_map() -> Dict[str, Any]:
    """Load and return the message map dict. Returns {} on any error."""
    path = _map_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        logger.warning("cron_message_map: failed to load %s: %s", path, e)
        return {}


def save_message_mapping(
    message_id: str,
    job_id: str,
    job_name: str,
    run_at: str,
    session_id: str,
    output_path: str,
) -> bool:
    """
    Save a mapping from telegram_message_id to cron job metadata.

    Returns True on success, False on failure.
    """
    path = _map_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = load_message_map()
        data[str(message_id)] = {
            "job_id": job_id,
            "job_name": job_name,
            "run_at": run_at,
            "session_id": session_id,
            "output_path": output_path,
        }
        # Prune oldest entries if we exceed the cap
        if len(data) > _MAX_ENTRIES:
            # Remove oldest entries (assumes insertion order, Python 3.7+)
            excess = len(data) - _MAX_ENTRIES
            for old_key in list(data.keys())[:excess]:
                del data[old_key]
        # Atomic write via temp file
        import tempfile
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp", prefix=".mmap_")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, str(path))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        logger.debug("cron_message_map: saved mapping for message_id=%s job=%s", message_id, job_id)
        return True
    except Exception as e:
        logger.warning("cron_message_map: failed to save mapping for message_id=%s: %s", message_id, e)
        return False


def lookup_message(message_id: str) -> Optional[Dict[str, Any]]:
    """
    Look up cron job metadata by Telegram message_id.

    Returns the metadata dict or None if not found.
    """
    try:
        data = load_message_map()
        return data.get(str(message_id))
    except Exception as e:
        logger.warning("cron_message_map: lookup failed for message_id=%s: %s", message_id, e)
        return None


class CronMessageMap:
    """High-level helper wrapping the cron message map functions."""

    def record(
        self,
        message_id: str,
        job_id: str,
        job_name: str,
        run_at: str,
        session_id: str,
        output_path: str,
    ) -> bool:
        """Record a telegram_message_id → cron job mapping."""
        return save_message_mapping(
            message_id=message_id,
            job_id=job_id,
            job_name=job_name,
            run_at=run_at,
            session_id=session_id,
            output_path=output_path,
        )

    def lookup(self, message_id: str) -> Optional[Dict[str, Any]]:
        """Look up cron job metadata by Telegram message_id."""
        return lookup_message(message_id)


def get_output_content(entry: Dict[str, Any]) -> Optional[str]:
    """
    Read and return the output file content for a mapping entry.

    Returns None if the file doesn't exist or cannot be read.
    """
    output_path = entry.get("output_path", "")
    if not output_path:
        return None
    try:
        p = Path(output_path)
        if p.exists():
            return p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        logger.warning("cron_message_map: failed to read output_path=%s: %s", output_path, e)
    return None
