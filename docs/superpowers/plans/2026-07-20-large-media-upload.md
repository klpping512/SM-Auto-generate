# Large Media Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 支持管理员可靠上传最大 2 GiB 视频并显示进度，同时将桌面首批视频素材同步并导入线上公共素材库。

**Architecture:** FastAPI 将 `UploadFile` 以固定块流式写入临时文件，再复用现有素材入库逻辑。前端使用 XHR 上传事件显示逐文件进度；首批数据用 rsync 保留目录同步到 import 区，再调用现有扫描接口。

**Tech Stack:** Python 3.12、FastAPI、原生 JavaScript、Nginx、pytest、rsync

---

### Task 1: 后端流式上传

**Files:**
- Modify: `media_assets.py`
- Modify: `app.py`
- Test: `tests/test_media_api.py`

- [ ] 写测试：模拟只允许分块读取的 `UploadFile`，验证超过 50 MiB 的视频不再被旧上限拒绝。
- [ ] 运行 `pytest -q tests/test_media_api.py`，确认测试因缺少流式函数而失败。
- [ ] 在 `media_assets.py` 增加 2 GiB 视频上限和流式临时文件写入函数；在 `app.py` 调用该函数。
- [ ] 运行 `pytest -q tests/test_media_api.py`，确认上传、超限、去重和清理测试通过。

### Task 2: 前端上传进度

**Files:**
- Modify: `static/assets.html`
- Test: `tests/test_media_api.py`

- [ ] 写静态契约测试，要求页面包含 XHR `upload.onprogress`、进度容器、2 GiB 客户端校验和顺序上传。
- [ ] 运行目标测试，确认因当前 fetch 实现而失败。
- [ ] 实现逐文件 XHR 上传、进度条、当前文件状态和汇总结果。
- [ ] 运行目标测试及 `git diff --check`。

### Task 3: 本地与服务器验证

**Files:**
- Modify: `/etc/nginx/conf.d/salogiflow.conf`（服务器）
- Modify: `/opt/distribution-manager/`（服务器部署副本）

- [ ] 运行上传相关测试并记录结果。
- [ ] 同步上传相关代码至服务器，更新 Nginx 为 2 GiB，执行 `nginx -t`。
- [ ] 重启单进程服务并验证根页面、登录、素材 API 和服务状态。

### Task 4: 首批素材同步与导入

**Files:**
- Source: `/Users/ylanlll/Desktop/视频素材/`
- Destination: `/opt/distribution-manager/static/assets/import/`

- [ ] 统计支持格式的文件数、字节数和 SHA256 重复情况，排除 `._*` 与 `.DS_Store`。
- [ ] rsync 保留子目录并显示传输进度；完成后对比源端和服务器端文件数、总字节数。
- [ ] 通过管理员身份调用扫描导入接口，记录新增、重复和失败数量。
- [ ] 验证公共素材 API 数量、磁盘占用、缩略图和抽样视频元数据。

### Task 5: 文档与交付

**Files:**
- Modify: Obsidian `「南非社媒内容中枢」改进日志.md`

- [ ] 记录目标、原因、实现、文件、测试、服务器验证、Git 状态和剩余事项，避免记录任何密码或令牌。
- [ ] 最终执行公网 HTTP、systemd、Nginx、上传相关测试和素材统计验收。
