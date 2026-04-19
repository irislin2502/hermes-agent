"""
Agent Activity Logger — 供排查 gateway timeout 問題使用。

記錄每個 tool 的開始時間、結束時間、耗時、結果摘要。
日誌寫入 ~/.hermes/logs/activity.jsonl（按日期 rotate）。

使用方式：
  from tools.agent_activity_log import log_tool_start, log_tool_end, log_api_call
"""

import json
import os
import time
import threading
from datetime import datetime, timezone
from pathlib import Path

_log_lock = threading.Lock()
_log_dir = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "logs"


def _get_log_path() -> Path:
    _log_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    return _log_dir / f"activity_{date_str}.jsonl"


def _write(record: dict):
    """Append a JSON record to today's log file (thread-safe)."""
    try:
        record["ts"] = datetime.now(timezone.utc).isoformat()
        line = json.dumps(record, ensure_ascii=False)
        with _log_lock:
            with open(_get_log_path(), "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass  # 絕對不能讓 log 錯誤影響主流程


# ── 工具呼叫追蹤 ──────────────────────────────────────────────────────────────

_active_tools: dict[str, float] = {}  # tool_call_id -> start_time


def log_tool_start(tool_name: str, args_preview: str, tool_call_id: str = ""):
    """Tool 開始執行時呼叫。"""
    start = time.time()
    key = tool_call_id or tool_name
    _active_tools[key] = start
    _write({
        "event": "tool_start",
        "tool": tool_name,
        "args_preview": args_preview[:200],
        "tool_call_id": tool_call_id,
        "thread": threading.current_thread().name,
    })


def log_tool_end(tool_name: str, result_preview: str, tool_call_id: str = "",
                 error: str = ""):
    """Tool 結束時呼叫。"""
    key = tool_call_id or tool_name
    start = _active_tools.pop(key, None)
    duration = round(time.time() - start, 2) if start else None
    _write({
        "event": "tool_end",
        "tool": tool_name,
        "tool_call_id": tool_call_id,
        "duration_s": duration,
        "result_preview": result_preview[:200],
        "error": error,
        "thread": threading.current_thread().name,
    })


# ── API 呼叫追蹤 ──────────────────────────────────────────────────────────────

def log_api_call_start(provider: str, model: str, api_call_count: int,
                       token_estimate: int = 0):
    """API call 開始時呼叫。"""
    _write({
        "event": "api_start",
        "provider": provider,
        "model": model,
        "api_call_count": api_call_count,
        "token_estimate": token_estimate,
    })


def log_api_call_end(provider: str, model: str, api_call_count: int,
                     duration_s: float, error: str = ""):
    """API call 完成或失敗時呼叫。"""
    _write({
        "event": "api_end",
        "provider": provider,
        "model": model,
        "api_call_count": api_call_count,
        "duration_s": round(duration_s, 2),
        "error": error,
    })


# ── Terminal 指令追蹤 ─────────────────────────────────────────────────────────

def log_terminal_start(command: str, timeout: int, task_id: str = ""):
    """Terminal 指令開始執行。"""
    _write({
        "event": "terminal_start",
        "command": command[:300],
        "timeout": timeout,
        "task_id": task_id,
        "thread": threading.current_thread().name,
    })


def log_terminal_heartbeat(command_preview: str, elapsed_s: int):
    """_wait_for_process 心跳（每 10s 一次）。"""
    _write({
        "event": "terminal_heartbeat",
        "command_preview": command_preview[:100],
        "elapsed_s": elapsed_s,
    })


def log_terminal_end(command: str, exit_code: int, duration_s: float,
                     timed_out: bool = False):
    """Terminal 指令結束。"""
    _write({
        "event": "terminal_end",
        "command": command[:300],
        "exit_code": exit_code,
        "duration_s": round(duration_s, 2),
        "timed_out": timed_out,
    })


# ── Gateway Timeout 診斷 ──────────────────────────────────────────────────────

def log_gateway_timeout(session_key: str, idle_secs: float, timeout: float,
                        last_activity: str, iteration: int, tool: str):
    """Gateway 偵測到 idle timeout 時呼叫。"""
    _write({
        "event": "gateway_timeout",
        "session_key": session_key,
        "idle_secs": round(idle_secs, 1),
        "timeout": timeout,
        "last_activity": last_activity,
        "iteration": iteration,
        "tool": tool,
    })


# ── 日誌查看工具 ──────────────────────────────────────────────────────────────

def tail_log(n: int = 50, date: str = "") -> list[dict]:
    """讀取最近 n 筆 log 記錄。"""
    if not date:
        date = datetime.now().strftime("%Y%m%d")
    log_path = _log_dir / f"activity_{date}.jsonl"
    if not log_path.exists():
        return []
    lines = log_path.read_text(encoding="utf-8").strip().split("\n")
    records = []
    for line in lines[-n:]:
        try:
            records.append(json.loads(line))
        except Exception:
            pass
    return records


def summarize_timeouts(date: str = "") -> list[dict]:
    """找出所有 timeout 事件和最近發生的長時間 tool。"""
    records = tail_log(n=9999, date=date)
    timeouts = [r for r in records if r.get("event") == "gateway_timeout"]
    slow_tools = [r for r in records
                  if r.get("event") == "tool_end"
                  and (r.get("duration_s") or 0) > 60]
    slow_terminals = [r for r in records
                      if r.get("event") == "terminal_end"
                      and (r.get("duration_s") or 0) > 30]
    return {
        "timeouts": timeouts,
        "slow_tools": slow_tools,
        "slow_terminals": slow_terminals,
    }
