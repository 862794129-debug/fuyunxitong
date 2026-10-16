# 项目工作协议（CLAUDE.md 与 AGENTS.md 内容必须一致）

适用于所有在本仓库工作的 AI 代理（Claude Code、Codex）和人。

## 分工
- Claude：功能代码、测试、文档，以分支 + PR 提交。
- Codex：部署配置与上线操作（Docker Compose、CI/CD、服务器）。
- 正式环境发布必须由仓库所有者批准。

## 硬性规则
1. 完成的定义：可运行、有测试、测试通过。禁止只交伪代码或提示词。
2. 不得把 Mock 成功说成真实模型调用成功；真实 API 测试与 Mock 测试分开标注。
3. 密钥、令牌、账号密码、客户数据、未授权视频、经营财务数据不得提交到仓库，也不得粘贴到 AI 对话。只放服务器 .env 或 CI 密钥库；仓库只保留 .env.example（变量名）。
4. 不擅自新增模型/付费供应商；不擅自开通付费云资源或批量消耗模型额度。
5. 小步提交，保持主分支随时可运行。
6. 合规预检模块只给风险提示，系统永远不输出"合规通过"，必须人工放行。

## 每次任务收尾必须更新
- docs/progress.md：完成内容、真实测试结果、未完成、阻塞、下一步
- docs/decisions.md：决策、原因、日期、弃用选项
- docs/architecture.md：模块、数据流、接口变化
- docs/todo.md：按优先级的待办
- .env.example（如有新变量）

## 运行测试
    python3 -m unittest discover -s tests -t .
