# SA-LogiFlow 项目协作规则

## Obsidian 同步

完成任何会修改本项目代码、配置、测试或产品行为的任务后，必须同步更新：

`/Users/ylanlll/Desktop/klpping/知识库/项目/Distribution Manager/改进日志.md`

每次追加一条记录时，标题必须使用精确时间格式：

`YYYY-MM-DD HH:mm:ss CST｜改动主题`

不得只写日期；必须记录小时、分钟、秒和时区。每条记录至少包含：

- 本次目标
- 改动前的问题或现状
- 已完成改动（逐项说明改了什么、为何这样改、用户可见结果）
- 涉及的主要文件
- 构建、测试和浏览器验证结果
- Git 提交和推送状态（如适用）
- 尚未完成或需要人工操作的事项

任何改动都必须先完成说明和知识库同步，再进行 Git 提交或推送。即使只是配置、文案、测试或协作规则调整，也不得省略记录。

如果修改了 `docs/` 下的需求、功能介绍或产品说明，同时将对应 Markdown 文档同步到：

`/Users/ylanlll/Desktop/klpping/知识库/项目/Distribution Manager/`

同步时不得记录 API Key、Token、Cookie、密码、代理凭据或其他敏感信息。
