# 批14 · Qwen 全量清理 + TTS 单轨化 · opencode 执行指令

> 总指挥：ylanlll ｜ 执行：opencode/qcoder ｜ 日期：2026-08-07
> **用户拍板**：彻底删除 Qwen TTS（音色 Qwen Cherry + MiMo→Qwen 兜底链），全库 Qwen 残留清干净。
> 前置：与批13（质量修复 A-E 块）独立，可先后或并行；本批是**纯删除 + 文案对齐，零新功能**，红线是不碰任何功能逻辑（除 TTS 单轨化本身）。
> 全库模型路由已全 MiMo（planner/critic/chat/video_evaluator/tts/asr），Qwen 已无任何在用模型角色。

---

## 一、背景与决策

批4 #15 只清了 `video_evaluator` 模型角色层的内部文案（model_router / video_quality/video_evaluator / asset_processing 三个文件），调用方用户可见文案、TTS、注释、归档脚本未动。用户确认：**Qwen TTS 彻底删除，其余 Qwen 残留全清**。

删除后后果（用户已接受）：TTS 变 MiMo 单轨，MiMo TTS 不可用时**直接失败**（不再悄悄回退 Qwen 音色），交付链路健壮性降一级。

---

## 二、交付物清单

| 块 | 内容 | 主要改动文件 |
|---|---|---|
| F1 | TTS 单轨化（删 Qwen TTS + fallback 链） | `video_renderer.py` / `app.py` / `routes/config_routes.py` / `ai_engine.py` |
| F2 | 前端清理（音色列表 + 回退门禁 + config UI） | `static/common.js` / `static/video-project.html` / `static/config.html` |
| F3 | 质检/旁白/注释名实不符清理（非 TTS） | `video_generation.py` / `hotspot_*` / `scripts/*` |
| F4 | 归档脚本 + 测试清理 | `scripts/archive/` / `tests/` |

---

## 三、逐块指令

### 块 F1：TTS 单轨化（后端）

#### F1-1 `video_renderer.py`

1. **删常量**：`:31` `QWEN_TTS_URL`、`:32` `VOICES = {"Cherry"}`。`MIMO_TTS_VOICE` 保留。
2. **删函数** `normalize_tts_voice`（`:65-68`）——历史音色归一化职责并入 `resolve_tts_selection`。
3. **`tts_voice_options`**（`:71-96`）：删 `qwen_available` 参数、`qwen_ok`、`:85-95` 的 qwen 展开循环；函数只返回 MiMo 默认一项。
4. **`synthesize_tts_preview`**（`:99-129`）：`:117-120` 删 else 分支，直接 `synthesize_mimo_tts(cleaned, resolved_voice, output)`。
5. **`resolve_tts_selection`**（`:132-156`）整体重写。**铁律：历史数据兼容必须先于任何 strict 校验**，否则旧项目存了 `provider=qwen` 会抛错导致重渲染失败：

```python
def resolve_tts_selection(
    provider: str | None,
    voice: str | None,
    *,
    strict: bool = False,
) -> tuple[str, str]:
    """Resolve provider/voice pair. Qwen is retired; legacy 'qwen'/'Cherry'
    normalize to MiMo so historical projects can still re-render."""
    normalized_provider = (provider or os.environ.get("TTS_PROVIDER", "mimo") or "mimo").strip().lower()
    if normalized_provider in {"qwen", "dashscope"}:
        normalized_provider, voice = "mimo", ""
    candidate = str(voice or "").strip()
    if normalized_provider == "mimo":
        allowed = {MIMO_TTS_VOICE, "mimo_default", ""}
        if candidate and candidate not in allowed:
            # 历史遗留音色（如 Cherry）统一回落默认，不抛错
            return "mimo", MIMO_TTS_VOICE
        return "mimo", candidate or MIMO_TTS_VOICE
    if strict:
        raise ValueError(f"未知 TTS 服务商：{normalized_provider}")
    return "mimo", MIMO_TTS_VOICE
```

6. **删函数** `synthesize_qwen_tts`（`:445-479`）；`:483-486` 注释删掉含 Qwen 的一句。
7. **`synthesize_scene_voiceover`**（`:553-663`）：
   - 删 `:569-570` `fallback_enabled` / `fallback_provider` 环境变量读取。
   - meta 初始化（`:574-584`）：删 qwen 分支与 `fallback_used`/`fallback_reason` 字段；`model`、`voice`、`style` 直接用 MiMo 值。
   - 主合成（`:632-645`）：删 `else: synthesize_qwen_tts(...)`；`:642-645` 整段 fallback 判断（`if provider != "mimo" or not fallback_enabled ...`) 改为直接 `raise`。
   - **删整个 fallback 块**（`:647-661`，从 `# Recoverable MiMo failure → temporary Qwen fallback.` 到 fallback 身份重新缓存）。
   - `_is_recoverable`（`:607-620`）若删除后无调用方则一并删（只被 fallback 分支用）。
   - 保留 cache 逻辑与 `local_macos` 分支（`:622-625`）。
   - 签名/行为：MiMo 失败不再回退，直接抛错冒泡。
8. **渲染报告聚合** `:1592` `"fallback_used": any(item.get("fallback_used") for item in tts_reports)` → 删该行。
9. **注释/docstring 改中立**：`:1` "Qwen TTS and deterministic FFmpeg..." → "TTS and deterministic FFmpeg..."；`:44`、`:1054`、`:1099`、`:1411` 的 Qwen TTS 注释改 "TTS"/"MiMo" 表述。

#### F1-2 `app.py`

- `:151` 删 `os.environ.setdefault("TTS_FALLBACK_ENABLED", "0")`（`:150` TTS_PROVIDER=mimo 保留）。
- `:157-160` 删 DashScope key 加载块（`key = os.environ.get("DASHSCOPE_API_KEY", "")` → `ai_engine.set_api_key(key)` → 日志）。
- `:4339` 删 `result["dashscope_key"] = ...`；`:4342-4344` 删 `tts_fallback_provider` / `tts_fallback_enabled`（含 `:4343` 注释）。
- `:4352` `tts_ok` 简化为 `bool(result["mimo_api_key"])`。
- `:4354-4357` 改 `video_renderer.tts_voice_options(mimo_available=result["mimo_api_key"])`（删 `qwen_available` 参数）。
- `:1673` / `:1687` / `:3596` 注释里 Qwen 字样改中立（TTS 语速/已获准画面范围）。

#### F1-3 `routes/config_routes.py`

- **删端点** `/api/config/apikey`（`:18-26`）与 `/api/config/dashscope-key`（`:29-37`）；保留 `/api/config/mimo-key`。
- 删除后 grep 确认 `ai_engine.set_api_key` 无其他调用方（F1-4 会删它）。

#### F1-4 `ai_engine.py`

- 删 `:88-91` 的 `DASHSCOPE_API_KEY = ""`、`QWEN_BASE_URL`、`QWEN_MODEL = "qwen-plus"`。
- 删 `set_api_key`（`:94-97`）。
- `chat_model_available`（`:100-108`）：删 DASHSCOPE 兜底分支，只保留 `model_router.key_is_available("chat_text")` 与 `MIMO_API_KEY`。

---

### 块 F2：前端清理

#### F2-1 `static/common.js`

- `:210` 删 qwen 音色项。
- `:223` 删 `if (raw === 'Cherry') return {tts_provider: 'qwen', voice: 'Cherry'};`。
- `:232` 删 `if (key === 'qwen') return 'Qwen';`。
- `:414-417`：删 `fallbackWarn` 与 `tts.fallback_used ? ' · 回退已使用' : ''` 后缀；`formatRenderProvenance` 不再读 `fallback_used`。
- 检查 `:239` / `:251` 的 provider 排序，qwen 已无数据源，可顺手简化。

#### F2-2 `static/video-project.html`

- `:132` 删 `let fallbackConfirmed=false;`。
- `:548-552` 删 `ttsFallbackUsed()` 函数。
- `:554-560` `gatedDownload` 去掉门禁判断，直接返回 `<a>` 下载标签（调用处 `:578` 不变）。
- `:579` 删 `fallbackGate` 定义；`:585` 删 `${fallbackGate}` 占位。
- `:612` 删回退拦截分支；`:707` 删 `if(ttsFallbackUsed()&&!fallbackConfirmed){...}`。
- 清理仅被回退门禁使用的 CSS（`.fallback-gate`、`.tts-fallback-warn` 等，若无其他用途）。

#### F2-3 `static/config.html`

- `:177` 删 `saveDashscopeKey` 函数；删页面中 dashscopeKey 输入框 + 保存按钮 + 调用。
- `:179` `loadCapabilities`：删 `${mark(c.dashscope_key)} 百炼 Key` 与 `${c.tts_fallback_enabled?'；可恢复异常回退 '+escapeHtml(c.tts_fallback_provider||'qwen'):''}` 段。

---

### 块 F3：质检/旁白/注释名实不符清理（非 TTS）

按文件逐处改**人类可读字符串**为中立表述；**不改函数名/变量名/常量/逻辑**。用户可见错误提示优先。

- `video_generation.py`：`:317` "Qwen found no high/medium defect" → "The evaluator found no..."; `:468` "（例如 Qwen 质检产物）" → "（例如质检产物）"。**注意 `:351` / `:1188` 与 `routes/video_generation_routes.py:421` 三处用户可见文案已在批13 块E2 指令中**——若批13 未执行，本批一并改；若已执行，grep 验证即可，不重复改。
- `hotspot_video_planner.py:659` "A Qwen + critic approved set" → "A MiMo + critic approved set"。
- `hotspot_hook_curator.py:355` docstring "由内置 Qwen 从母片..." → "由内置策展模型从母片..."。
- `hotspot_video_sources.py:228` "pre-download Qwen intake" → "pre-download intake"。
- `hotspot_preview_narration.py`：全文件 Qwen 表述（`:1` docstring、`:49/:54/:58/:76/:85/:94/:96` 错误信息、`:237/:267-268/:432/:461/:486`）→ 改 "旁白规划"/"Critic" 中立（该角色实际为 mimo-v2.5-pro）。
- `scripts/run_dual_library_preview.py:37`、`scripts/reprocess_hotspot_hook_source.py:103/:147`、`scripts/run_authorized_hotspot_prewarm.py:25/:34`、`scripts/audit_existing_dual_preview.py:1/:26` → 改中立。

---

### 块 F4：归档脚本 + 测试清理

#### F4-1 归档脚本删除（git rm，Qwen 配置死脚本）

```
scripts/archive/configure_qwen37_text_routes.py
scripts/archive/configure_minimax_text_routes.py
scripts/archive/configure_mimo_video_evaluator.py
scripts/archive/configure_mimo_vision_tagger.py
```

其余 archive 目录脚本 docstring 含 Qwen（如 `run_video_quality_mvp.py`、`ab_compare_render_params.py`）**不动**——归档区保留历史，不做过期清理。

#### F4-2 测试清理

- **删整文件** `tests/test_mimo_tts_fallback.py`（测的正是已删的 fallback 链）。
- `tests/test_media_assets.py`：`:77-97` 改 voice options 断言（仅 MiMo 默认一项）；`:117` / `:149` 删 `synthesize_qwen_tts` 用例；`:156-157` 删 `normalize_tts_voice` 断言（函数已删）。
- `tests/test_video_generation_ui.py`：`:112` "MiMo → Qwen" 断言删除/改；`:174` "Qwen Cherry" → 改断言 MiMo 默认音色；`:175` `"fallback_used" in common` 断言删除。
- `tests/test_delivery_loop_ui.py`：`:27` / `:34` 断言"不出现"Qwen TTS 字样，删除后仍绿可保留；`:51` `resolve_tts_selection` 断言保留。
- `tests/test_media_api.py`：`:204` fixture 删 `"fallback_used": False`。
- `tests/test_upload_api.py:87`、`tests/test_ai_chat_platforms.py:270`：monkeypatch `app.ai_engine.DASHSCOPE_API_KEY` → 删（别名已删）。
- `tests/test_model_router.py:130` 的 `DASHSCOPE_API_KEY` 是路由配置样例字符串，**不动**。

---

## 四、测试要求

在现有测试上新增/修改，全部并入 `pytest`：

1. **历史兼容（新增）**：`resolve_tts_selection("qwen", "Cherry", strict=True)`、`resolve_tts_selection("qwen", "", strict=True)`、`resolve_tts_selection("mimo", "Cherry", strict=True)` 全部返回 `("mimo", "mimo_default")` 且**不抛错**——旧项目 qwen provider 重渲染不炸。
2. **单轨行为（新增）**：`synthesize_scene_voiceover` 在 MiMo 合成抛错时直接冒泡（不再回退）；meta 无 `fallback_used` 字段。
3. **前端 smoke**：`test_video_generation_ui` / `test_delivery_loop_ui` 更新后绿；config 页无百炼 Key 输入引用；验收页无回退门禁引用。
4. **全量回归**：与基线（854 passed / 8 baseline failed）对比，**8 个存量失败必须逐条一致，不得新增**。

---

## 五、提交

- 提交前缀：`refactor(tts):`（F1/F2 TTS 单轨化）、`chore(cleanup):`（F3/F4 文案/脚本/测试清理）。分块提交。
- 完成后 push 到 `codex/semantic-assets-mvp`（origin 落后一并推）。
- 回写改进日志 `docs/改进日志/`。

---

## 六、总指挥验收清单（执行完由总指挥逐条跑）

- [ ] `grep -rn "Qwen\|qwen\|QWEN\|fallback_used\|fallback_provider" video_renderer.py video_generation.py routes/ app.py ai_engine.py static/ hotspot_video_planner.py hotspot_hook_curator.py hotspot_preview_narration.py hotspot_video_sources.py scripts/*.py` → **0 命中**（`scripts/archive/` 除外）。
- [ ] 历史 job（DB 中 `tts_provider=qwen`）重渲染不报错，旁白正常出 MiMo 音色。
- [ ] TTS 试听正常（仅 MiMo）；config 页无百炼 Key 输入；验收页无回退门禁。
- [ ] `pytest` 全绿，8 个存量失败与基线一致。
- [ ] 提交已 push。
