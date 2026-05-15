# Upstream 差異追蹤文件

> 最後更新：2026-05-15
> 分叉點：2026-04-13（commit `0e60a9dc`）
> 上游超前：**4,339 commits**（fetch 後最新數字，之前估計 914 是舊數據）
> 我們超前：**17 commits**（功能擴充 + bug fix）

---

## 1. 差異統計摘要

| 類型 | 數量 | 說明 |
|------|------|------|
| Bug Fix (`fix:`) | 2,340 | 各種修復 |
| New Feature (`feat:`) | 651 | 新功能 |
| Chore/Refactor/CI | 1,216 | 維護、重構、CI |
| Security | ~15 | 安全修復 |
| Revert | 7 | 回退 |

---

## 2. 我們的 17 個修改（高風險衝突檔案）

### 修改的檔案清單

| 檔案 | 上游後續修改次數 | 風險等級 | 說明 |
|------|---------------|---------|------|
| `run_agent.py` | 347 | 🔴 HIGH | 上游大量改動，衝突風險最高 |
| `gateway/run.py` | 344 | 🔴 HIGH | 同上 |
| `gateway/platforms/telegram.py` | 73 | 🔴 HIGH | Telegram 平台大改 |
| `cron/scheduler.py` | 58 | 🟡 MEDIUM | cron 多次修復 |
| `tools/web_tools.py` | 24 | 🟡 MEDIUM | web 架構重構（plugin 化） |
| `tools/cronjob_tools.py` | 18 | 🟡 MEDIUM | cron tools 有修復 |
| `plugins/memory/holographic/__init__.py` | 2 | 🟢 LOW | 少量改動 |
| `plugins/memory/holographic/retrieval.py` | 0 | ✅ SAFE | 上游未動 |
| `plugins/memory/holographic/store.py` | 1 | ✅ SAFE | 上游幾乎未動 |
| `gateway/cron_message_map.py` | 0 | ✅ SAFE | 我們自行新增的檔案 |
| `tools/agent_activity_log.py` | 0 | ✅ SAFE | 我們自行新增的檔案 |
| `hermes_state.db` | - | ✅ SKIP | binary，不合併 |

### 我們的核心修改內容

1. **SPA Playwright fallback**（`tools/web_tools.py`）：SPA 網站優先用 Playwright，內容不足才 fallback Firecrawl
2. **Cron 去重通知**（`cron/scheduler.py`）：name idempotency check，避免重複建立通知
3. **Pending intents 清理**（`gateway/run.py`）：啟動時自動清理 4h+ stale pending_intents
4. **FTS 軟刪除過濾**（`gateway/run.py`）：search 補上 is_deleted=0 過濾
5. **Memory retrieval fix**（`plugins/memory/holographic/retrieval.py`）：修復 search() 未更新 retrieval_count
6. **Memory trust 調整**（`plugins/memory/holographic/__init__.py`）：LLM 萃取 trust 三級
7. **Gateway reply 系統**（`gateway/cron_message_map.py`，`gateway/run.py`，`cron/scheduler.py`）：cron message_id → reply context
8. **Telegram reply context**（`gateway/platforms/telegram.py`）：訊息對應回覆

---

## 3. 上游重要更新分類

### 🔴 高優先 — 安全修復（應 cherry-pick）

| Commit | 說明 | 影響檔案 |
|--------|------|---------|
| `6af994232` | fix(url-safety): 只允許 http/https scheme | URL 處理 |
| `d6c9711ba` | fix(security): 減少 shell=True 使用 | 多處 subprocess |
| `f6736ced8` | fix(security): 清理 env、遮蔽 quick commands 輸出 | 安全輸出 |
| `04b1fdaec` | security(deps): 5 個依賴加上版本上限 | `pyproject.toml` |
| `691778a08` | fix(cron): 防止 auth-header 外洩 | `cron/scheduler.py` |
| `ec9329ec4` | fix(security): dashboard plugin API 路由需驗證 | dashboard |
| `eacb398f7` | fix(tools): asyncio.gather 加 return_exceptions | `tools/web_tools.py` |

### 🟡 中優先 — 重要 Bug Fix（選擇性 cherry-pick）

| Commit | 說明 | 影響檔案 |
|--------|------|---------|
| `13c72fb48` | fix(tools): browser provider network 加 error handling | tools |
| `4f8aaf104` | perf(run_agent): list+join 累積 prefix 效能改善 | `run_agent.py` |
| `f06d71b93` | fix(url-safety): allow only http/https | URL 驗證 |
| `e407376c5` | fix(cron): normalize partial job records | `cron/scheduler.py` |
| `cbce5e93f` | fix: 所有 bare open() 加 encoding='utf-8' | 全域 |
| `4ceab1689` | fix(compression): protect_first_n 預設 3 | 壓縮邏輯 |
| `55f3262e7` | fix(mcp): pre-compile env-var regex + 統一 interpolation | MCP |
| `5360b5424` | fix(providers): User-Agent on ProviderProfile.fetch_models | providers |
| `c4a21d783` | fix(cli): log swallowed exception in runtime model auto-detection | CLI |

### 🟢 低優先 / 可忽略（平台相關，我們不用）

| 類別 | 說明 |
|------|------|
| WhatsApp、Discord、Slack | 我們只用 Telegram，暫不需要 |
| Yuanbao | 中國平台，我們不用 |
| MiniMax、Novita、Qwen | 我們不用這些 provider |
| Codex Runtime | 特定功能，暫不需要 |
| EVM/Blockchain skill | 我們沒有此需求 |
| Video generation | 我們沒有此需求 |
| Kanban | 我們沒有此需求 |

### 🔵 值得考慮的新功能

| Commit | 說明 | 決策建議 |
|--------|------|---------|
| `6682f91b8` | feat(cron): name-based lookup for job ops | ✅ 有用，可 cherry-pick |
| `dee71a31e` | feat(compression): protect_first_n 可配置 | ✅ 有用 |
| `29d7c244c` | feat(gateway): Telegram clarify 用 inline keyboard | ✅ UX 改善 |
| `e0e4856d4` | feat(skills-hub): HuggingFace skills 作為 trusted tap | ⚪ 可考慮 |
| `8f19078c6` | feat(goals): /subgoal 追加條件 | ⚪ 可考慮 |
| `c1eb2dcda` | feat(security): supply-chain advisory checker | ✅ 安全性改善 |

---

## 4. 合併策略

### 策略一：安全優先 cherry-pick（建議立即執行）

直接 cherry-pick 以下純改單一檔案、無我們修改的 commit：

```bash
# 安全修復（不觸及我們的高衝突檔案）
git cherry-pick 6af994232  # url-safety
git cherry-pick 04b1fdaec  # deps 版本上限
git cherry-pick ec9329ec4  # dashboard auth
```

### 策略二：高衝突檔案手動 merge（分批）

`run_agent.py`、`gateway/run.py`、`gateway/platforms/telegram.py` 衝突多，建議：
1. 先 fetch upstream
2. `git diff HEAD upstream/main -- <file>` 看差異
3. 手動挑選上游改動中不衝突的部分
4. 分成小 commit 逐一整合

### 策略三：web_tools.py 架構重構（需評估）

上游已將 web tools 全面 plugin 化（WebSearchProvider ABC）。我們的 Playwright SPA fallback 是在舊架構上的修改。
- 短期：保持我們現有版本（功能正常）
- 長期：Phase 3 進行時，重寫 web_tools 配合新架構

### 不合併清單

- `hermes_state.db` — binary，跳過
- 平台相關（WhatsApp/Discord/Slack/Yuanbao/MiniMax）— 我們不用
- EVM/Blockchain/Video — 我們沒有此需求

---

## 5. 風險評估

### 直接 rebase on upstream/main 的風險
- **衝突數量**：預估 10+ 個檔案有衝突
- **run_agent.py / gateway/run.py**：347/344 次上游修改，手動解衝突耗時估 4-8 小時
- **建議**：不要直接 rebase，改用 cherry-pick 策略

### 保持現狀的風險
- 安全修復未套用（`url-safety`、`shell=True` 等）
- 上游 bug fix 未受益（encoding、cron normalize 等）
- 分叉距離越來越大，未來合併更困難

### 推薦方案
**Phase 3-4 分步驟進行**：每週 cherry-pick 10-20 個 commit（從安全修復開始），逐步縮短差距，而非一次性 rebase。

---

## 6. 下一步行動

- [ ] **Phase 3-1**：cherry-pick 安全修復（`6af994232`、`d6c9711ba`、`f6736ced8`、`04b1fdaec`）
- [ ] **Phase 3-2**：設定每週 upstream 掃描 cron
- [ ] **Phase 3-3**：web_tools.py 架構遷移評估（配合上游 plugin 化）
- [ ] **Phase 3-4**：處理高衝突檔案（run_agent.py、gateway/run.py）

---

> 此文件由 Builder 自動生成（2026-05-15），來源：`git log HEAD..upstream/main` 分析
