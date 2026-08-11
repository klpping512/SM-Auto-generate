# 批22执行指令：云端真实时效 Hook 百条补库

日期：2026-08-11  
执行环境：本地项目 `/Users/ylanlll/Desktop/distribution-manager`；生产环境 `/opt/distribution-manager`  
执行方：Codex / opencode 均可，必须按本文逐项回证  
总指挥验收：对抗式复核，不接受“模型调用成功”“数量达到”作为质量证明  
目标：云端至少形成 **100 条真实、时效、可播放、逐条具备视觉证据的 `timely_event` Hook**

---

## 〇、不可变口径

### 0.1 当前生产真相

- 云端页面目前显示 15 条 Hook，其中 14 条是 Buffalo 原素材生成的 `generic_logistics` 常青 Hook。
- 唯一一条 `timely_event` 是错误假阳性：卡片写“南非港口现场作业画面”，实际画面是文字/品牌标题卡，并非港口作业。
- 云端诊断：错误 Hook 为 `clip_id=1`、`asset_id=305`、7.6 秒；选中 2 个镜头；无 ASR、无 OCR；planner 非缓存调用 2 次，文本 critic 非缓存调用 1 次。
- 因此当前有效时效 Hook 基线必须记为 **0**，不能记为 1；14 条常青 Hook 不计入本批 100 条目标。

### 0.2 “真实时效 Hook”定义

一条 Hook 只有同时满足以下全部条件才计数：

1. `hook_kind='timely_event'`、`review_status='confirmed'`、`clip_status='ready'`。
2. 时长 4–14 秒，代理文件存在，`ffprobe` 可读取视频流且时长与数据库基本一致。
3. 母片属于已授权信源；授权记录、来源页、发布时间和母片关系可追溯。
4. 来源发布时间距验收时不超过 30 天；至少 70 条不超过 10 天。页面出现“超30天”的条目不计数。
5. 画面中确实出现标题和 `what_happened` 所描述的主体、动作或现场状态。
6. 标题页、纯文字卡、Logo/赞助商卡、主播、演播室、地图、信息图、片头片尾、静态空镜不得入库。
7. 母片标题只能作为事件线索，不能代替像素证据；不能因为来源标题含“港口”就把任意画面写成港口作业。
8. 必须通过一次不携带母片标题的盲视觉审核，再通过一次文本事实审核；两次审核任一失败、超时、空返回或 JSON 非法均不得确认。
9. 必须保留视觉证据清单：至少 3 个分散时间点的帧、帧哈希、审核版本和接受理由；不得只保存模型结论。
10. 同一母片的重叠时间段只能计 1 条；相同视频 SHA、相同事件、近似相同画面不得拆分凑数。

### 0.3 明确不计数

- 14 条 `generic_logistics` 常青 Hook。
- `review_required`、`rejected`、`pending`、缺文件、无视觉审核记录的 Hook。
- 只靠母片标题、摘要、旁白或模型猜测成立的 Hook。
- 超过 30 天、发布时间缺失、把抓取时间冒充发布时间的 Hook。
- 同一片段改标题后的重复 Hook。

---

## 一、最终硬验收指标

| 指标 | 最低要求 |
|---|---:|
| 合格 `timely_event` Hook | 100 |
| 0–10 天新鲜 Hook | 70 |
| 11–30 天 Hook | 最多 30 |
| 不同母片 | 50 |
| 不同父热点 | 30 |
| 已授权来源占比 | 100% |
| 视觉审核通过率 | 100% |
| 文本事实审核通过率 | 100% |
| 可播放文件完整率 | 100% |
| 标题页/Logo/主播等假阳性 | 0 |
| 重叠或重复片段 | 0 |

场景覆盖目标：港口不少于 15 条、边境/清关不少于 15 条、道路/干线/中断不少于 25 条、仓储/设施不少于 15 条、配送/末端不少于 10 条；其余 20 条可由任何真实物流现场补足。场景数量不足时继续拓展授权信源，不得给不相关画面强贴标签。

---

## 二、执行总顺序

严格按以下顺序执行，不得先跑大批量补库再修门禁：

1. 冻结现有时效 Hook，备份云端数据库和相关代码。
2. 下架当前错误 Hook，只删派生 Hook，保留母片、镜头和审计数据。
3. 实现真正的多帧视觉 critic 与本地硬门禁。
4. 新增百条目标审计脚本和视觉证据报告脚本。
5. 完成本地测试和 4 条定向真机样本。
6. 部署代码到云端，先跑 20 条试点批。
7. 对试点 20 条做 100% 视觉证据复核；0 假阳性后才允许扩量。
8. 每批新增 20 条，逐批验收、逐批备份，直到合格总数 ≥100。
9. 完成最终全量审计、浏览器播放抽检和服务回证。

---

## 三、阶段 A：冻结、备份和错误 Hook 下架

### A1｜冻结规模化任务

在正式修复部署前，将云端 `.env` 的 `HOTSPOT_HOOK_SYNC_ENABLED` 临时设为 `0`，重启应用并确认调度器返回 `disabled`。记录原值，试点 20/20 通过前不得恢复。此期间不运行全量预热、`--zero-hook-ready` 重策展或任何会新增 `timely_event` 的任务；允许只抓取元数据，但不得自动下载、策展或写 `confirmed`。

注意：`scripts/run_authorized_hotspot_prewarm.py` 会在进程内强制把该开关设为 `1`，因此冻结期禁止无 `--media-ids` 运行它，不能认为 `.env=0` 可以挡住显式操作命令。

### A2｜云端备份

创建独立备份目录，至少保存：

- `/opt/distribution-manager/data/logiflow.db`
- `/opt/distribution-manager/hotspot_hook_curator.py`
- `/opt/distribution-manager/hotspot_hook_selection_sop.py`
- `/opt/distribution-manager/model_router.py`
- `/opt/distribution-manager/app.py`
- `/opt/distribution-manager/scripts/reprocess_hotspot_hook_source.py`

备份目录命名：

```text
/opt/backups/distribution-manager-batch22-before-visual-hook-YYYYMMDD-HHMMSS/
```

SQLite 必须使用 Python `sqlite3.Connection.backup()` 或停服务后复制，不得在服务写入中直接复制主库。

### A3｜下架错误 Hook

- 目标：当前错误 `event_clip_id=1`。
- 优先在“内容资产”页面点击“删除此 Hook”；或调用既有管理员接口 `DELETE /api/hotspot-events/1`。
- 只删除派生 Hook，保留 `asset_id=305` 母片、镜头分析和来源记录，供回归测试。
- 删除后回证：`timely_event=0`、`generic_logistics=14`，页面不再显示该错误卡片。

不得清空整个热点素材库，不得删除 14 条 Buffalo 常青 Hook，不得重新导入旧热点数据库。

---

## 四、阶段 B：修复视觉事实审核链路

### B1｜新增独立视觉审核角色

**文件**：`model_router.py`

新增角色 `hook_visual_critic`：

- capabilities：`text + vision`
- 默认模型：当前可用的 MiMo 视觉模型
- `enable_thinking:false`
- timeout 90 秒
- max_tokens 900
- 不与 `planner_text`、文本 `critic` 共用 job budget

同步更新角色白名单、默认路由、路由配置测试和模型配置 UI 测试。云端 `model_role_configs` 必须新增对应路由；不得把 API Key 写入代码、文档或 Git。

### B2｜真实多帧视觉审核

**主文件**：`hotspot_hook_curator.py`  
**建议新增文件**：`hotspot_hook_visual_audit.py`

在 `_parse()` 生成候选之后、现有 `_audit_hooks()` 文本审核之前增加视觉审核：

```text
planner 候选
  → 本地时长/连续性/重叠门禁
  → 从实际视频提取 3 帧
  → hook_visual_critic 盲视觉审核
  → 文本 critic 对齐来源事实
  → confirmed + materialize
```

每条候选至少提取：

- `start + 0.4s`
- `midpoint`
- `end - 0.4s`

不足 5 秒时仍须保证 3 个互不相同的时间点。使用实际母片或候选临时剪辑提帧，不得把数据库中的 `description` 当图片。临时帧审核后删除，只在证据中保存 SHA256 和相对时间点。

盲视觉审核请求中**不得携带**母片标题、热点标题、来源摘要、planner 的地点结论或 logistics question。输入只能包含：

- 3 张实际帧；
- 候选的开始/结束时间；
- “描述你实际看到的对象、动作、场景类型，判断是否为标题页/Logo/主播/地图/空镜”的中性任务。

严格 JSON 输出：

```json
{
  "accepted": true,
  "scene_type": "port|border|road|warehouse|delivery|other|non_event",
  "visible_objects": ["仅列实际可见对象"],
  "visible_actions": ["仅列实际可见动作"],
  "is_title_or_logo_card": false,
  "is_anchor_or_studio": false,
  "is_map_or_infographic": false,
  "supports_visible_event": true,
  "reason": "不超过80字"
}
```

以下任一情况必须拒绝：

- `accepted != true`
- `supports_visible_event != true`
- 三个非事件布尔字段任一为 true
- `scene_type='non_event'`
- `visible_objects` 与 `visible_actions` 同时为空
- 模型超时、网络失败、空内容、非法 JSON
- 任一提取帧不存在或无法解码

### B3｜修正文本 critic 的事实前提

**文件**：`hotspot_hook_curator.py` 的 `_audit_prompt()` / `_audit_hooks()`

删除或改写当前错误前提：

```text
母片标题是已验证事件事实
```

新口径：

```text
母片标题和来源摘要只是待核对的来源线索，不能证明候选帧中出现了对应地点、主体或动作。
```

文本 critic 必须同时看到：

- planner 候选标题和 `what_happened`
- 盲视觉审核产生的可见对象、动作、场景类型
- 已授权来源的标题、发布时间和事件范围

只有“画面可见事实”和“来源事件身份”不矛盾时才接受。画面是普通标题卡时，即使来源标题包含 TNPA、港口、Transport Month，也必须拒绝。

### B4｜收紧空结果修复

**文件**：`hotspot_hook_curator.py` 的 `_has_safe_hook_window()` 和 `_empty_result_repair_instruction()`

- `_has_safe_hook_window()` 只能决定“是否值得再调用一次 planner”，不得写入 `confirmed`。
- 空结果修复产生的候选必须完整经过多帧视觉审核和文本审核。
- 没有 transcript/OCR 不等于自动拒绝，但此时地点、机构、事件身份不得只靠模型视觉猜测。
- 视觉审核不能证明具体地点时，标题只能描述可见场景；但若无法与真实时效事件建立可靠关联，则不得作为 `timely_event`，也不得自动降级为 `generic_logistics`。

### B5｜证据落库

每条通过的时效 Hook 在 `evidence_json` 中新增：

```json
{
  "visual_audit": {
    "status": "accepted",
    "prompt_version": "hotspot-hook-visual-audit-v1",
    "scene_type": "port",
    "frame_offsets_ms": [400, 3800, 7200],
    "frame_sha256": ["...", "...", "..."],
    "visible_objects": ["..."],
    "visible_actions": ["..."],
    "model": "...",
    "cache_hit": false
  },
  "text_audit": {
    "status": "accepted",
    "prompt_version": "hotspot-hook-grounding-audit-v5"
  }
}
```

不得存 Base64 图片、API Key、代理节点或模型完整原始回复。`review_status='confirmed'` 只能在两个审核状态均为 `accepted` 后设置。

### B6｜调用点改造

必须同步修改两个真实调用点：

- `app.py` 热点素材处理链路调用 `curate_hook_clips()` 的位置
- `scripts/reprocess_hotspot_hook_source.py`

向策展器提供安全的 `static_root` / 母片路径，使视觉审核读取真实文件。测试调用可使用显式临时目录和测试帧；不得在策展器中依赖当前工作目录猜路径。

---

## 五、阶段 C：专项测试

### C1｜必须新增的测试

主要文件：

- `tests/test_hotspot_hook_curation.py`
- `tests/test_hook_curator_json.py`
- 新增 `tests/test_hotspot_hook_visual_audit.py`
- `tests/test_reprocess_hotspot_hook_source.py`
- `tests/test_model_router.py`

至少覆盖：

1. 画面为标题/品牌卡，文本描述谎称港口作业：视觉审核拒绝。
2. 母片标题包含“南非港口”，帧中无港口对象和动作：拒绝。
3. 三帧均为真实港口吊机/集装箱/卡车作业，来源事件一致：接受。
4. 第一帧是标题卡、后两帧是现场：允许模型判断，但 `what_happened` 只能描述现场帧；纯标题卡时间不得进入最终 Hook。
5. 帧文件缺失、损坏、无法解码：拒绝，不降级确认。
6. 视觉模型超时、空回复、非法 JSON：拒绝，不调用文本 critic。
7. 视觉审核通过、文本事实审核拒绝：最终 0 Hook。
8. 视觉审核拒绝时不得写 `review_status='confirmed'`、不得 materialize 代理文件。
9. 空结果修复候选仍必须经过视觉审核。
10. source title 不再被提示词声明为“已验证画面事实”。
11. job budget、缓存 key 和 prompt version 独立；旧文本审核缓存不能复用为视觉审核结果。
12. 同一 Hook 三帧哈希和审核版本正确写入 evidence。

### C2｜测试命令

```bash
python3 -m py_compile \
  hotspot_hook_curator.py \
  hotspot_hook_visual_audit.py \
  model_router.py \
  scripts/reprocess_hotspot_hook_source.py

pytest -q \
  tests/test_hotspot_hook_curation.py \
  tests/test_hook_curator_json.py \
  tests/test_hotspot_hook_visual_audit.py \
  tests/test_reprocess_hotspot_hook_source.py \
  tests/test_model_router.py
```

随后运行热点、素材、聊天视频相关回归组和全量测试。任何新增失败必须修复；既有失败必须在改动前基线逐条复现，不得只写“与本次无关”。

### C3｜四条真机定向样本

部署前必须用实际视频验证：

1. 当前 `asset_id=305` 错误标题卡：必须 0 Hook。
2. 一条纯主播/演播室视频：必须 0 Hook。
3. 一条真实港口作业视频：至少 1 条，且标题与画面一致。
4. 一条真实道路/卡车中断视频：至少 1 条，且不存在地点或因果臆断。

四条结果必须附三帧证据图、模型审核状态和最终数据库状态。

### C4｜云端部署门禁

四条定向样本和本地测试全部通过后才允许部署：

1. 先同步三份改进日志，精确暂存本批代码并完成本地提交。
2. 先在云端保存阶段 A 的代码和数据库备份，再把本次明确变更的文件上传到临时目录。
3. 比对本地与上传文件 SHA256；一致后再安装到 `/opt/distribution-manager`，保持既有属主和权限。
4. 在云端执行 `py_compile` 和专项测试；不得用“本地通过”替代云端导入检查。
5. 通过管理员模型路由接口或一个不包含密钥的幂等配置脚本写入 `hook_visual_critic` 路由；路由只引用现有密钥环境变量名。
6. 重启 `salogiflow.service`，确认 active、首页 200、`/assets.html` 200。
7. 保持定时全量 Hook 同步关闭；试点只允许显式 `--media-ids` 小批运行。

任何上传、导入或重启失败都立即用阶段 A 备份恢复，不得在部分文件已更新的状态下继续补库。

---

## 六、阶段 D：新增百条目标审计工具

### D1｜审计脚本

新增：`scripts/audit_timely_hook_target.py`

脚本默认只读，输出 JSON 汇总，不输出密钥、Cookie、完整模型原文。至少检查：

- qualified total / 按新鲜度分层
- 不同母片数、不同热点数、不同 SHA 数
- scene 分布
- 授权状态
- `visual_audit.status`
- `text_audit.status`
- review/clip 状态
- 文件存在性和 `ffprobe`
- 4–14 秒时长
- 时间段重叠
- 重复视频/重复帧哈希
- 缺失发布时间、超 30 天、抓取时间冒充发布时间
- 标题页/Logo/主播等拒绝标记

命令：

```bash
python3 scripts/audit_timely_hook_target.py --target 100 --json
```

退出码：全部硬门禁通过且数量 ≥100 才返回 0；数量不足或任一假阳性风险均返回非 0。

### D2｜视觉证据报告

新增：`scripts/build_hook_visual_evidence_report.py`

对每条合格 Hook 输出一张联系表：开始、中间、结束三帧；同时生成 manifest，包含 Hook ID、母片 ID、来源时间、时长、scene、帧哈希、视觉审核和文本审核状态。

```bash
python3 scripts/build_hook_visual_evidence_report.py \
  --qualified-only \
  --output /opt/distribution-manager/reports/batch22-hook-evidence
```

报告属于内部审计资料，不提交包含私有视频帧的产物到 Git。

---

## 七、阶段 E：来源扩量与漏斗目标

100 条合格 Hook 的上游容量目标：

| 漏斗阶段 | 目标 |
|---|---:|
| 近 30 天已授权视频候选 | ≥200 |
| 成功下载母片 | ≥150 |
| 完成镜头分析母片 | ≥120 |
| 视觉审核通过候选 | ≥110 |
| 最终合格时效 Hook | ≥100 |

执行原则：

- 只使用已配置且企业授权的频道/直接媒体源；不绕过版权或平台授权。
- 同一频道单次元数据读取上限仍遵守脚本 1–12 条限制，通过增加授权频道和多轮日期窗口扩量，不改成无界抓取。
- 优先南非官方港口、铁路、公路、边境、海关、仓储、配送和可信新闻现场源。
- 对 403/失效 URL 最多刷新元数据并重试 2 次；仍失败则记录并跳过，不把失败来源当作已处理成功。
- 先解决代理订阅有效期；预计不足以覆盖整轮执行时，先更新订阅再开批量任务。
- 不下载旧数据库、第三方库存素材或未经授权视频来凑 100 条。

为 `scripts/run_authorized_hotspot_prewarm.py` 新增 `--fetch-only` 参数并补测试：该模式只运行信源抓取、授权记录同步和候选汇总，绝不调用 `scheduler.prewarm_authorized_hotspot_media()`。没有这个参数前，禁止把现有脚本当成“只抓元数据”命令。

受控元数据扩量命令：

```bash
cd /opt/distribution-manager
sudo -u logiflow .venv/bin/python scripts/run_authorized_hotspot_prewarm.py \
  --channel-video-limit 12 \
  --fetch-only
```

实际下载和分析必须另行使用 `--media-ids` 指定小批；不得第一步直接跑全量。

---

## 八、阶段 F：分批补库

### F1｜试点批

1. 选择 20–30 条不同来源、近 10 天的已授权母片。
2. 每次最多处理 5 条，ffmpeg 并发不超过 2，模型策展串行或并发不超过 2。
3. 每完成 5 条运行一次审计；累计得到 20 条合格 Hook 后停止试点。
4. 为 20 条生成完整证据联系表，20/20 逐条看图；随机播放不少于 10 条完整 Hook。
5. 任一条画面与标题不符，立即停止扩量，整批标记待复核；修门禁后重新处理，不允许只手删错条继续跑。

复用镜头分析的重策展命令：

```bash
sudo -u logiflow .venv/bin/python scripts/reprocess_hotspot_hook_source.py \
  --zero-hook-ready \
  --skip-analysis \
  --max-media 5
```

只有现有镜头分析版本可信时才允许 `--skip-analysis`；标题卡误标、缺三帧或视觉标签低质量的母片必须重跑分析，不得复用错误描述。

### F2｜规模批

试点 20/20 通过后，按合格 Hook 增量分批：

- 批 A：20 → 40
- 批 B：40 → 60
- 批 C：60 → 80
- 批 D：80 → 100+

每批必须：

1. 开始前做 SQLite 备份。
2. 记录候选母片 ID，禁止与前批重复。
3. 处理过程中每 5 条输出一次聚合进度。
4. 结束后运行审计脚本。
5. 为新增 Hook 生成三帧证据报告并 100% 看图。
6. 随机播放本批至少 30%，且每个 scene 至少播放 2 条。
7. 0 假阳性后才开始下一批。

禁止通过降低置信度、取消 critic、把常青 Hook 改成 timely、手写 `confirmed`、复制时间段、修改发布时间等方式追数量。

---

## 九、运行保护与停止条件

出现以下任一情况立即停止当前批：

- 发现 1 条标题/画面不符。
- 视觉审核或文本审核异常时仍写入 `confirmed`。
- 同一片段或同一帧哈希重复计数。
- 代理失效导致连续 3 个来源下载失败。
- 模型连续 3 次超时、空回复或非法 JSON。
- 服务器磁盘可用空间低于 20%。
- 内存使用持续超过 85%，或出现 OOM/服务重启。
- `salogiflow.service`、`sing-box-hotspot.service` 非 active。
- 首页或 `/assets.html` 非 HTTP 200。

停止后保留现场日志和本批数据库备份，不自动回滚前面已验收通过的批次。当前批若存在假阳性，只移除当前批派生 Hook，保留母片与审计记录供修复。

---

## 十、最终对抗验收

### 10.1 数据验收

执行：

```bash
python3 scripts/audit_timely_hook_target.py --target 100 --json
```

必须回报实际值：

- qualified timely total
- 0–10 天 / 11–30 天数量
- distinct assets / hotspots / source SHA
- scene 分布
- 视觉审核、文本审核、文件、时长、授权、重复、超期失败数
- generic total（单列展示，不计入 qualified timely total）

### 10.2 文件验收

- 100% 文件存在。
- 100% `ffprobe` 通过。
- 100% 时长 4–14 秒。
- 100% 证据联系表包含 3 帧。
- 0 个重复帧哈希集合。

### 10.3 浏览器验收

在公网“内容资产”页：

1. 查看“热点 Hook 素材”分区（不含下方常青开场池），页面数量 ≥100。
2. 随机播放 30 条，覆盖每个 scene。
3. 每条核对画面、标题、画面发生、物流切入、所属热点和发布时间。
4. 不得出现“超30天”。
5. 不得出现标题卡、Logo 卡、主播、地图或与文案无关画面。
6. 常青 Hook 仍单独展示，不与时效 Hook 混计。

### 10.4 链路验收

至少用 5 个不同物流话题完成 Hook 匹配探针：港口、边境、道路中断、仓储、末端配送各 1 个。检查选中的 Hook 与用户话题强相关，不因库存量增加恢复“万能 Hook”复用。

本批目标是 Hook 库质量与数量，不强制生成 5 条正式成片；如触发成片，仅作额外验证，不得把成片成功替代 Hook 真实性验收。

### 10.5 服务验收

- `salogiflow.service` active
- `sing-box-hotspot.service` active
- 首页 HTTP 200
- `/assets.html` HTTP 200
- 活跃素材处理任务 0
- 活跃热点媒体任务 0
- 无孤儿 ffmpeg / yt-dlp 进程

---

## 十一、回归与提交纪律

1. 先同步三份改进日志，再提交代码：
   - `/Users/ylanlll/Desktop/klpping/知识库/项目/Distribution Manager/改进日志.md`
   - `/Users/ylanlll/Desktop/klpping/知识库/项目/Distribution Manager/月度改进日志/2026-08.md`
   - `docs/改进日志/2026-08.md`
2. 精确暂存本批文件，不得把工作区其他未提交内容带入。
3. 至少拆为三次提交：
   - 视觉 critic + 门禁 + 测试
   - 审计/证据工具
   - 云端百条补库验收日志
4. 不 push，除非用户另行明确授权。
5. 生产密钥、订阅地址、代理节点、Cookie、数据库和内部视频帧不得提交 Git。
6. 执行回报必须附实际 commit、测试输出、云端备份路径、审计 JSON 和浏览器抽检结果。

---

## 十二、最终回报模板

```markdown
# 批22执行回报

## 1. 代码修复
- hook_visual_critic：
- 多帧提取：
- 文本 critic 前提修正：
- confirmed 双审核门禁：

## 2. 测试
- py_compile：
- 专项测试：
- 回归组：
- 全量测试：
- 基线失败复现：

## 3. 云端补库漏斗
- 近30天已授权候选：
- 下载成功母片：
- 分析完成母片：
- 视觉审核通过候选：
- 最终合格 timely Hook：

## 4. 最终质量
- 0–10天：
- 11–30天：
- distinct assets：
- distinct hotspots：
- scene 分布：
- 文件缺失：
- ffprobe 失败：
- 重叠/重复：
- 标题页/Logo/主播假阳性：

## 5. 浏览器验收
- 页面时效 Hook 数：
- 实际播放数量：
- 画面/文案不符数量：
- “超30天”数量：

## 6. 服务与备份
- DB 备份：
- 服务状态：
- 首页/assets HTTP：
- 活跃后台任务：

## 7. Git
- 提交：
- push：未 push

## 8. 尚未完成
- 如最终合格数 <100，本批不得回报“完成”，必须写明差额和阻塞来源。
```

---

## 十三、总指挥判定

只有以下结论同时成立，批22才可判定 PASS：

```text
qualified_timely_hooks >= 100
false_visual_claims == 0
hooks_older_than_30_days == 0
missing_or_unplayable_files == 0
duplicate_or_overlapping_hooks == 0
unauthorized_sources == 0
visual_audit_coverage == 100%
text_audit_coverage == 100%
```

数量达到但存在 1 条类似“标题卡被写成港口现场”的错误，整批仍判 FAIL。数量不足但质量正确，状态只能是“进行中”，不得把常青 Hook、候选媒体或已分析母片计入 100 条。
