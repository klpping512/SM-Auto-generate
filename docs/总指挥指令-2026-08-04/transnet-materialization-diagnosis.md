# Transnet 物料化定性诊断（P0）

> 2026-08-05 · 对照实验 + 分支处置  
> 约束：未改门禁 / 阈值 / 授权逻辑；仅做物料化诊断与供给侧结论。

---

## 1. 环境与样本

| 项 | 值 |
|---|---|
| yt-dlp | `2026.07.04`（`pip install -U yt-dlp` 后仍为此版，无更新） |
| 代理 | `SA_HOTSPOT_PROXY=http://127.0.0.1:7897` |
| Transnet 样本 | media **856** `U1AvMFUzfgk`（本轮抓取最新之一） |
| CNBC 对照 | media **850** `RK8Jw-yc53Q`（本轮已成功物料化） |
| 频道 | `https://www.youtube.com/channel/UCxTpqUzbY43I6g7U9ExpAlw` |

原始输出：`docs/总指挥指令-2026-08-04/_probe/`

---

## 2. Step A 对照结果

| 命令 | 结果 |
|---|---|
| `yt-dlp --proxy … -F <Transnet 856>` | **FAIL** · `ERROR: [youtube] U1AvMFUzfgk: This video is not available` |
| `yt-dlp --proxy … -F <CNBC 850>` | **OK** · 列出 144p–1080p 等完整格式 |
| `yt-dlp -F <Transnet 856>`（**直连、无代理**） | **仍 FAIL** · 同一句 `not available` |
| 升级 yt-dlp 后复测 856 | **仍 FAIL**（版本未变 / 无效） |

**分支判定：CNBC 能列格式 + Transnet 报 not available → 病因 ≠ 代理出口被封。**  
直连同样失败 → 进一步排除「仅代理路径坏掉」。yt-dlp 升级无效 → 排除「版本过旧」主因。

---

## 3. Transnet 近 20 条可下载性（进一步）

| 指标 | 值 |
|---|---|
| 可 `-F` 列出格式 | **13 / 20** |
| 不可用（同报 not available） | **7 / 20**（含本轮 856/857/858 及若干「Know Your Ports」短片） |
| 直播/首播/会员字段（flat） | 失败条目 `live_status`/`availability` 均为空；**未见明确 premiere/members 标记** |
| 冒烟下载（可下样本） | `AHIY0QfW5qU`《A Day in the Life of a Dredge Master》· `yt-dlp -f worst` **成功** · ~25.4 MB mp4 |

不可用标题样例（最近侧）：Khayelitsha School / Safety Proficiency Day / Umlazi Community / 部分 Know Your Ports。  
可下标题样例（略旧侧）：Christening ceremony、Breaking Boundaries 系列、**Day in the Life of a Dredge Master / Lighthouse Technician / Marine Pilot**、Transport Month、all-women marine crew 等——**现场/港口运营画面密度明显高于本轮失败的 3 条。**

---

## 4. 病因结论（最终）

**判定码：`VIABLE_WITH_RECENCY_FILTER`（不作 `UNVIABLE_AS_MOTHER`）**

| 假设 | 结论 |
|---|---|
| 代理被 YouTube 封 | ❌ 否（同代理 CNBC 正常；直连 Transnet 仍挂） |
| yt-dlp 版本过旧 | ❌ 否（升级无新版本；可下样本已成功下载） |
| 整台 Transnet 作母片不可行 | ❌ 否（近 20 条 **65% 可下**，且含强物流现场片） |
| 本轮抓到的「最新条」本身受限/不可用 | ✅ **是**（最新约 7 条对当前抽取路径全部 `not available`） |

根因归类：**信源供给侧的「最新窗口失效」**，不是全局代理病，也不是频道整体不可用。  
本轮 H-hit 里 Transnet=0，是因为默认「每频道最近 N 条」正好落在不可用窗口；频道更深处仍有可物料化的港口/海事实拍。

---

## 5. 已执行修复 / 未执行项

| 动作 | 状态 |
|---|---|
| 对照诊断（Transnet vs CNBC，同代理） | ✅ 完成 |
| 直连复测 | ✅ 完成 |
| yt-dlp 升级尝试 | ✅ 已尝试，无版本变化、失败条未恢复 |
| 可下样本冒烟下载 | ✅ 完成（证明物料化链路对 Transnet **可通**） |
| 改门禁 / 授权 / 阈值 | ❌ 按约束未改 |
| 改 `.env` 频道集 | ❌ 未改（等总指挥定「加深拉取 / 换源 / za-stock」配比） |
| Step B 替代实拍源探测 | ⏭ **未触发**（仅 `UNVIABLE_AS_MOTHER` 时执行；本次未达该阈值） |

### 建议供给侧处置（供总指挥圈定，未自动改配置）

1. **保留 Transnet 在定稿 5 台内**，但抓取侧加深窗口：例如对该频道单独提高 `HOTSPOT_YOUTUBE_CHANNEL_VIDEO_LIMIT` 或实现「跳过 not available、向更旧条目回填」——让可下的 Day-in-the-Life / 港口运营片进入母片池。  
2. **不要**因本轮 856–858 失败把 Transnet 整台砍掉。  
3. **并行**仍建议放行 za-stock 免版权管线：现场画面供给不应单绑 YouTube 官方号的最新窗口。

---

## 6. Step B 状态

**未执行。** 触发条件未满足。  
若总指挥仍要「不等加深拉取、先扩实拍候选表」，可另开指令做港口/货代/卡车/仓储频道探测（存活 + `yt-dlp -F` + 现场画面粗命中）。

---

## 7. 一句话给总指挥

**代理没封；yt-dlp 也不是主因；Transnet 能下、能出港口实拍——只是「最近几条」对抽取器不可用。**  
配比建议：**加深/回填 Transnet 拉取 + 放行 za-stock**，而不是立刻换掉头号资产。
