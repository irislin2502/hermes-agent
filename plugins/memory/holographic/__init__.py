"""hermes-memory-store — holographic memory plugin using MemoryProvider interface.

Registers as a MemoryProvider plugin, giving the agent structured fact storage
with entity resolution, trust scoring, and HRR-based compositional retrieval.

Original plugin by dusterbloom (PR #2351), adapted to the MemoryProvider ABC.

Config in $HERMES_HOME/config.yaml (profile-scoped):
  plugins:
    hermes-memory-store:
      db_path: $HERMES_HOME/memory_store.db   # omit to use the default
      auto_extract: false
      default_trust: 0.5
      min_trust_threshold: 0.3
      temporal_decay_half_life: 0
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List

from agent.memory_provider import MemoryProvider
from tools.registry import tool_error
from .store import MemoryStore
from .retrieval import FactRetriever

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool schemas (unchanged from original PR)
# ---------------------------------------------------------------------------

FACT_STORE_SCHEMA = {
    "name": "fact_store",
    "description": (
        "Deep structured memory with algebraic reasoning. "
        "Use alongside the memory tool — memory for always-on context, "
        "fact_store for deep recall and compositional queries.\n\n"
        "ACTIONS (simple → powerful):\n"
        "• add — Store a fact the user would expect you to remember.\n"
        "• search — Keyword lookup ('editor config', 'deploy process').\n"
        "• probe — Entity recall: ALL facts about a person/thing.\n"
        "• related — What connects to an entity? Structural adjacency.\n"
        "• reason — Compositional: facts connected to MULTIPLE entities simultaneously.\n"
        "• contradict — Memory hygiene: find facts making conflicting claims.\n"
        "• update/remove/list — CRUD operations.\n\n"
        "IMPORTANT: Before answering questions about the user, ALWAYS probe or reason first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "search", "probe", "related", "reason", "contradict", "update", "remove", "list"],
            },
            "content": {"type": "string", "description": "Fact content (required for 'add')."},
            "query": {"type": "string", "description": "Search query (required for 'search')."},
            "entity": {"type": "string", "description": "Entity name for 'probe'/'related'."},
            "entities": {"type": "array", "items": {"type": "string"}, "description": "Entity names for 'reason'."},
            "fact_id": {"type": "integer", "description": "Fact ID for 'update'/'remove'."},
            "category": {"type": "string", "enum": ["user_pref", "project", "tool", "general"]},
            "tags": {"type": "string", "description": "Comma-separated tags."},
            "trust_delta": {"type": "number", "description": "Trust adjustment for 'update'."},
            "min_trust": {"type": "number", "description": "Minimum trust filter (default: 0.3)."},
            "limit": {"type": "integer", "description": "Max results (default: 10)."},
        },
        "required": ["action"],
    },
}

FACT_FEEDBACK_SCHEMA = {
    "name": "fact_feedback",
    "description": (
        "Rate a fact after using it. Mark 'helpful' if accurate, 'unhelpful' if outdated. "
        "This trains the memory — good facts rise, bad facts sink."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["helpful", "unhelpful"]},
            "fact_id": {"type": "integer", "description": "The fact ID to rate."},
        },
        "required": ["action", "fact_id"],
    },
}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_plugin_config() -> dict:
    from hermes_constants import get_hermes_home
    config_path = get_hermes_home() / "config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml
        with open(config_path) as f:
            all_config = yaml.safe_load(f) or {}
        return all_config.get("plugins", {}).get("hermes-memory-store", {}) or {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# MemoryProvider implementation
# ---------------------------------------------------------------------------

class HolographicMemoryProvider(MemoryProvider):
    """Holographic memory with structured facts, entity resolution, and HRR retrieval."""

    def __init__(self, config: dict | None = None):
        self._config = config or _load_plugin_config()
        self._store = None
        self._retriever = None
        self._min_trust = float(self._config.get("min_trust_threshold", 0.3))

    @property
    def name(self) -> str:
        return "holographic"

    def is_available(self) -> bool:
        return True  # SQLite is always available, numpy is optional

    def save_config(self, values, hermes_home):
        """Write config to config.yaml under plugins.hermes-memory-store."""
        from pathlib import Path
        config_path = Path(hermes_home) / "config.yaml"
        try:
            import yaml
            existing = {}
            if config_path.exists():
                with open(config_path) as f:
                    existing = yaml.safe_load(f) or {}
            existing.setdefault("plugins", {})
            existing["plugins"]["hermes-memory-store"] = values
            with open(config_path, "w") as f:
                yaml.dump(existing, f, default_flow_style=False)
        except Exception:
            pass

    def get_config_schema(self):
        from hermes_constants import display_hermes_home
        _default_db = f"{display_hermes_home()}/memory_store.db"
        return [
            {"key": "db_path", "description": "SQLite database path", "default": _default_db},
            {"key": "auto_extract", "description": "Auto-extract facts at session end", "default": "false", "choices": ["true", "false"]},
            {"key": "default_trust", "description": "Default trust score for new facts", "default": "0.5"},
            {"key": "hrr_dim", "description": "HRR vector dimensions", "default": "1024"},
        ]

    def initialize(self, session_id: str, **kwargs) -> None:
        from hermes_constants import get_hermes_home
        _hermes_home = str(get_hermes_home())
        _default_db = _hermes_home + "/memory_store.db"
        db_path = self._config.get("db_path", _default_db)
        # Expand $HERMES_HOME in user-supplied paths so config values like
        # "$HERMES_HOME/memory_store.db" or "~/.hermes/memory_store.db" both
        # resolve to the active profile's directory.
        if isinstance(db_path, str):
            db_path = db_path.replace("$HERMES_HOME", _hermes_home)
            db_path = db_path.replace("${HERMES_HOME}", _hermes_home)
        default_trust = float(self._config.get("default_trust", 0.5))
        hrr_dim = int(self._config.get("hrr_dim", 1024))
        hrr_weight = float(self._config.get("hrr_weight", 0.3))
        temporal_decay = int(self._config.get("temporal_decay_half_life", 0))

        self._store = MemoryStore(db_path=db_path, default_trust=default_trust, hrr_dim=hrr_dim)
        self._retriever = FactRetriever(
            store=self._store,
            temporal_decay_half_life=temporal_decay,
            hrr_weight=hrr_weight,
            hrr_dim=hrr_dim,
        )
        self._session_id = session_id

    def system_prompt_block(self) -> str:
        if not self._store:
            return ""
        try:
            total = self._store._conn.execute(
                "SELECT COUNT(*) FROM facts"
            ).fetchone()[0]
        except Exception:
            total = 0
        if total == 0:
            return (
                "# Holographic Memory\n"
                "Active. Empty fact store — proactively add facts the user would expect you to remember.\n"
                "Use fact_store(action='add') to store durable structured facts about people, projects, preferences, decisions.\n"
                "Use fact_feedback to rate facts after using them (trains trust scores)."
            )
        return (
            f"# Holographic Memory\n"
            f"Active. {total} facts stored with entity resolution and trust scoring.\n"
            f"Use fact_store to search, probe entities, reason across entities, or add facts.\n"
            f"Use fact_feedback to rate facts after using them (trains trust scores)."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not self._retriever or not query:
            return ""
        try:
            results = self._retriever.search(query, min_trust=self._min_trust, limit=5)
            if not results:
                return ""
            lines = []
            for r in results:
                trust = r.get("trust_score", r.get("trust", 0))
                lines.append(f"- [{trust:.1f}] {r.get('content', '')}")
            return "## Holographic Memory\n" + "\n".join(lines)
        except Exception as e:
            logger.debug("Holographic prefetch failed: %s", e)
            return ""

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        # Holographic memory stores explicit facts via tools, not auto-sync.
        # The on_session_end hook handles auto-extraction if configured.
        pass

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [FACT_STORE_SCHEMA, FACT_FEEDBACK_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if tool_name == "fact_store":
            return self._handle_fact_store(args)
        elif tool_name == "fact_feedback":
            return self._handle_fact_feedback(args)
        return tool_error(f"Unknown tool: {tool_name}")

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        if not self._config.get("auto_extract", False):
            return
        if not self._store or not messages:
            return
        self._auto_extract_facts(messages)

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        """Called before context compression — snapshot project state to disk.

        Writes the current in-progress task summary to the active project's
        progress.md so that state survives context compression without loss.
        Also triggers LLM-based fact extraction so facts are persisted before
        context is compressed.
        Returns an empty string (no contribution to the compressor prompt).
        """
        try:
            self._flush_project_state(messages)
        except Exception as e:
            logger.debug("Holographic on_pre_compress project flush failed: %s", e)
        # Auto-extract facts before compression if configured
        if self._config.get("auto_extract", False) and self._store and messages:
            try:
                self._auto_extract_facts(messages)
            except Exception as e:
                logger.debug("Holographic on_pre_compress auto-extract failed: %s", e)
        return ""

    def _flush_project_state(self, messages: List[Dict[str, Any]]) -> None:
        """Extract structured project state snapshot and append to compress log."""
        from pathlib import Path
        from hermes_constants import get_hermes_home
        from datetime import datetime

        index_path = get_hermes_home() / "projects" / "_compress_log.md"

        # Build a brief digest of the last few turns
        assistant_msgs = []
        user_msgs = []
        for msg in reversed(messages[-30:]):
            role = msg.get("role", "")
            content = msg.get("content", "")
            if not isinstance(content, str) or not content.strip():
                continue
            if role == "assistant" and len(assistant_msgs) < 5:
                assistant_msgs.append(content.strip())
            elif role == "user" and len(user_msgs) < 3:
                user_msgs.append(content.strip())

        if not assistant_msgs and not user_msgs:
            return

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

        # Detect in-progress tasks from recent assistant messages
        in_progress = []
        completed = []
        todos = []
        key_facts = []
        for msg in reversed(assistant_msgs):
            lines = msg.split("\n")
            for line in lines:
                l = line.strip()
                if not l:
                    continue
                if any(kw in l.lower() for kw in ["正在", "進行中", "working on", "in progress", "開始"]):
                    in_progress.append(l[:120])
                elif any(kw in l.lower() for kw in ["完成", "done", "✅", "finished", "已"]):
                    completed.append(l[:120])
                elif any(kw in l.lower() for kw in ["待辦", "todo", "下一步", "next step", "[ ]"]):
                    todos.append(l[:120])
                elif any(kw in l.lower() for kw in ["發現", "注意", "重要", "關鍵", "error", "issue", "bug", "found"]):
                    key_facts.append(l[:120])

        # Fallback: use first assistant message excerpt
        if not in_progress and not completed:
            in_progress = [assistant_msgs[0][:200]] if assistant_msgs else ["（無法判斷）"]

        entry = f"\n## {timestamp} — pre-compression snapshot\n"
        entry += "### 當前專案狀態\n"
        entry += "- 正在進行：" + (in_progress[0] if in_progress else "（無）") + "\n"
        entry += "- 最近完成：" + (completed[0] if completed else "（無）") + "\n"
        if len(completed) > 1:
            for c in completed[1:3]:
                entry += f"  - {c}\n"

        entry += "### 關鍵事實\n"
        if key_facts:
            for f in key_facts[:3]:
                entry += f"- {f}\n"
        else:
            # Use recent user request as context
            if user_msgs:
                entry += f"- 用戶請求：{user_msgs[0][:150]}\n"
            else:
                entry += "- （本次對話無明顯新事實）\n"

        entry += "### 待辦事項\n"
        if todos:
            for t in todos[:3]:
                entry += f"- {t}\n"
        else:
            entry += "- （請查閱 todo 清單）\n"

        entry += "\n"

        # Append to compress log
        existing = index_path.read_text(encoding="utf-8") if index_path.exists() else "# Hermes Compression Log\n\n"
        index_path.write_text(existing + entry, encoding="utf-8")
        logger.info("Holographic: flushed structured project state to %s", index_path)

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        """Mirror built-in memory writes as facts."""
        if action == "add" and self._store and content:
            try:
                category = "user_pref" if target == "user" else "general"
                self._store.add_fact(content, category=category)
            except Exception as e:
                logger.debug("Holographic memory_write mirror failed: %s", e)

    def shutdown(self) -> None:
        self._store = None
        self._retriever = None

    # -- Tool handlers -------------------------------------------------------

    def _handle_fact_store(self, args: dict) -> str:
        try:
            action = args["action"]
            store = self._store
            retriever = self._retriever

            if action == "add":
                fact_id = store.add_fact(
                    args["content"],
                    category=args.get("category", "general"),
                    tags=args.get("tags", ""),
                )
                return json.dumps({"fact_id": fact_id, "status": "added"})

            elif action == "search":
                results = retriever.search(
                    args["query"],
                    category=args.get("category"),
                    min_trust=float(args.get("min_trust", self._min_trust)),
                    limit=int(args.get("limit", 10)),
                )
                return json.dumps({"results": results, "count": len(results)})

            elif action == "probe":
                results = retriever.probe(
                    args["entity"],
                    category=args.get("category"),
                    limit=int(args.get("limit", 10)),
                )
                return json.dumps({"results": results, "count": len(results)})

            elif action == "related":
                results = retriever.related(
                    args["entity"],
                    category=args.get("category"),
                    limit=int(args.get("limit", 10)),
                )
                return json.dumps({"results": results, "count": len(results)})

            elif action == "reason":
                entities = args.get("entities", [])
                if not entities:
                    return tool_error("reason requires 'entities' list")
                results = retriever.reason(
                    entities,
                    category=args.get("category"),
                    limit=int(args.get("limit", 10)),
                )
                return json.dumps({"results": results, "count": len(results)})

            elif action == "contradict":
                results = retriever.contradict(
                    category=args.get("category"),
                    limit=int(args.get("limit", 10)),
                )
                return json.dumps({"results": results, "count": len(results)})

            elif action == "update":
                updated = store.update_fact(
                    int(args["fact_id"]),
                    content=args.get("content"),
                    trust_delta=float(args["trust_delta"]) if "trust_delta" in args else None,
                    tags=args.get("tags"),
                    category=args.get("category"),
                )
                return json.dumps({"updated": updated})

            elif action == "remove":
                removed = store.remove_fact(int(args["fact_id"]))
                return json.dumps({"removed": removed})

            elif action == "list":
                facts = store.list_facts(
                    category=args.get("category"),
                    min_trust=float(args.get("min_trust", 0.0)),
                    limit=int(args.get("limit", 10)),
                )
                return json.dumps({"facts": facts, "count": len(facts)})

            else:
                return tool_error(f"Unknown action: {action}")

        except KeyError as exc:
            return tool_error(f"Missing required argument: {exc}")
        except Exception as exc:
            return tool_error(str(exc))

    def _handle_fact_feedback(self, args: dict) -> str:
        try:
            fact_id = int(args["fact_id"])
            helpful = args["action"] == "helpful"
            result = self._store.record_feedback(fact_id, helpful=helpful)
            return json.dumps(result)
        except KeyError as exc:
            return tool_error(f"Missing required argument: {exc}")
        except Exception as exc:
            return tool_error(str(exc))

    # -- Auto-extraction (on_session_end) ------------------------------------

    def _auto_extract_facts(self, messages: list) -> None:
        """Extract facts from conversation using LLM-based analysis.

        Primary method: call LLM with a structured prompt to identify
        durable facts worth storing (preferences, decisions, configs, discoveries).
        Falls back to English regex patterns if LLM call fails.
        """
        # Try LLM-based extraction first
        try:
            extracted = self._llm_extract_facts(messages)
            if extracted > 0:
                logger.info("LLM auto-extracted %d facts from conversation", extracted)
                return
        except Exception as e:
            logger.warning("LLM-based fact extraction failed, falling back to regex: %s", e)

        # Fallback: English regex patterns
        self._regex_extract_facts(messages)

    def _llm_extract_facts(self, messages: list) -> int:
        """Call LLM to extract facts from the last 20 messages. Returns count of facts added."""
        from agent.auxiliary_client import call_llm

        # Take last 20 messages with role+content
        recent = []
        for msg in messages[-20:]:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if not isinstance(content, str) or not content.strip():
                continue
            if role in ("user", "assistant", "system"):
                recent.append({"role": role, "content": content.strip()[:500]})

        if not recent:
            return 0

        # Format conversation for the prompt
        conv_text = "\n".join(
            f"[{m['role']}]: {m['content']}" for m in recent
        )

        system_prompt = (
            "你是一個記憶萃取助手。分析以下對話片段，萃取值得長期記憶的事實。\n"
            "只萃取具體、可驗證、對未來有用的事實（偏好、決策、設定、發現）。\n"
            "不萃取任務進度、暫時狀態、對話過程、問候語。\n"
            "\n"
            "【系統操作類事實的特別規則】\n"
            "以下類型的事實只有在 trust_score >= 0.6 時才應存入，否則直接跳過：\n"
            "  - CUA 操作記錄（截圖、點擊、GUI 操作步驟）\n"
            "  - Agent prompt 修改記錄（Director/Builder/Inspector 的 prompt 更新）\n"
            "  - Scheduler 更新（cron job 設定變更）\n"
            "  - 純技術操作記錄（非決策性質，只是描述「做了什麼動作」）\n"
            "如果是系統操作但沒有長期參考價值（如一次性的 GUI 點擊、已完成的臨時任務），直接不萃取。\n"
            "\n"
            "為每個事實評估信任度（trust_score）：\n"
            "  - 0.9：高確定性事實（使用者明確陳述的偏好、已確認的設定、明確的決策）\n"
            "  - 0.7：中高確定性（有明確證據支持、直接觀察到的行為、已驗證的技術設定）\n"
            "  - 0.6：系統操作類中等確定性（有長期參考價值的工具設定、架構決策）\n"
            "  - 0.5：中等確定性（推斷或間接資訊）— 系統操作類不得使用此分數\n"
            "用繁體中文輸出，格式為 JSON 陣列：\n"
            '[{"content": "事實描述", "category": "user_pref|project|tool|general", "tags": "tag1,tag2", "trust_score": 0.9}]\n'
            "若無值得萃取的事實，輸出空陣列 []。\n"
            "只輸出 JSON，不要有任何其他文字。"
        )

        user_prompt = f"以下是對話內容：\n\n{conv_text}\n\n請萃取值得長期記憶的事實："

        response = call_llm(
            task="flush_memories",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=1024,
        )

        raw = response.choices[0].message.content.strip()

        # Parse JSON response
        # Strip markdown code fences if present
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        facts = json.loads(raw)
        if not isinstance(facts, list):
            return 0

        extracted = 0
        for fact in facts:
            if not isinstance(fact, dict):
                continue
            content = fact.get("content", "").strip()
            if not content:
                continue
            category = fact.get("category", "general")
            if category not in ("user_pref", "project", "tool", "general"):
                category = "general"
            tags = fact.get("tags", "")
            raw_trust = fact.get("trust_score", None)
            trust = float(raw_trust) if raw_trust is not None else None
            # Enforce: system operation facts (tool category) must have trust >= 0.6
            _sys_op_keywords = ("cua", "截圖", "點擊", "prompt 修改", "scheduler", "cron", "gui 操作",
                                "agent prompt", "director", "builder", "inspector")
            if trust is not None and trust < 0.6:
                content_lower = content.lower()
                if any(kw in content_lower for kw in _sys_op_keywords):
                    logger.debug("Skipping system-op fact with low trust (%.1f): %s", trust, content[:80])
                    continue
            try:
                self._store.add_fact(content, category=category, tags=tags, trust_score=trust)
                extracted += 1
            except Exception as e:
                logger.debug("Failed to store extracted fact: %s", e)

        return extracted

    def _regex_extract_facts(self, messages: list) -> None:
        """Fallback: English regex-based fact extraction (original logic)."""
        _PREF_PATTERNS = [
            re.compile(r'\bI\s+(?:prefer|like|love|use|want|need)\s+(.+)', re.IGNORECASE),
            re.compile(r'\bmy\s+(?:favorite|preferred|default)\s+\w+\s+is\s+(.+)', re.IGNORECASE),
            re.compile(r'\bI\s+(?:always|never|usually)\s+(.+)', re.IGNORECASE),
        ]
        _DECISION_PATTERNS = [
            re.compile(r'\bwe\s+(?:decided|agreed|chose)\s+(?:to\s+)?(.+)', re.IGNORECASE),
            re.compile(r'\bthe\s+project\s+(?:uses|needs|requires)\s+(.+)', re.IGNORECASE),
        ]

        extracted = 0
        for msg in messages:
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if not isinstance(content, str) or len(content) < 10:
                continue

            for pattern in _PREF_PATTERNS:
                if pattern.search(content):
                    try:
                        self._store.add_fact(content[:400], category="user_pref")
                        extracted += 1
                    except Exception:
                        pass
                    break

            for pattern in _DECISION_PATTERNS:
                if pattern.search(content):
                    try:
                        self._store.add_fact(content[:400], category="project")
                        extracted += 1
                    except Exception:
                        pass
                    break

        if extracted:
            logger.info("Regex fallback auto-extracted %d facts from conversation", extracted)


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    """Register the holographic memory provider with the plugin system."""
    config = _load_plugin_config()
    provider = HolographicMemoryProvider(config=config)
    ctx.register_memory_provider(provider)
