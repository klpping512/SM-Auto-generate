# 南非热点专题包工作台验收记录

日期：2026-07-24

## 已验证

- SQLite 专题包字段、`hotspot_signals` 幂等写入和 JSON 容错通过数据库测试。
- 新闻 RSS 与 YouTube 元数据会写入信号表；抓取结果包含专题包、信号和媒体候选统计。
- 专题包列表、详情、确认、合并和单媒体准备 API 通过权限测试；确认专题包不会自动下载媒体。
- 热点页响应包含专题包接口、来源信号和三类媒体区域；运行中的 `http://127.0.0.1:8080/hotspots.html` 返回 HTTP 200。
- 视频跟进可从 `hotspot_id` 定位本专题包事件片段，且 60 秒分镜只使用专题包热点片段与 Buffalo 自有视频。

## 测试证据

- 数据层：1 passed。
- 聚类与抓取：17 passed。
- API：6 passed。
- 抓取回归：21 passed。
- 页面契约：12 passed。
- 视频联动：8 passed。

## 未完成验收

- 完整回归在 32 passed 后被 `tests/test_douyin_adapter.py::test_short_circuit_when_not_logged_in` 阻断。本机未安装 Playwright Chromium，失败发生在适配器启动浏览器阶段，非专题包变更引入。
- 未完成浏览器可视化截图与交互点击验收；当前环境无法使用浏览器控制会话。
- 未创建 Git 提交或推送，避免把当前工作区原有未提交改动一并纳入。
