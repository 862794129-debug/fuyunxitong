# Progress（2026-10-16）
## 已完成
- 合规预检切片 v0.2（规则引擎、起步规则库、sqlite 回流、CLI）。
- G0 核心数据层（本次）：
  - 统一分镜 JSON Schema（creative_studio/shots/schema.py）：字段按 V1.1 第四节；镜头 ID/顺序唯一、时长与证据区间校验、不确定项标记、版本不可变（编辑生成新版本）。
  - 模型适配器接口（creative_studio/adapters/base.py）：视频理解、图片、视频生成；每个结果带 is_mock、供应商、模型、费用、耗时。
  - Mock 适配器（adapters/mock.py），全部标注 is_mock=True。
  - 镜头级视频任务管理（creative_studio/jobs/video_jobs.py）：幂等键、提交结果不明时先查状态再决定是否重提、单镜头失败隔离、预算上限。
- 测试：47 个单元测试全部通过。**全部为 Mock/本地测试，未调用任何真实模型 API。**
## 未完成
- G0 Web 后端（FastAPI）、数据库（PostgreSQL/SQLAlchemy）、任务队列（Redis/Celery）、上传与存储、前端。
- 真实适配器（Gemini、OpenAI、Nano Banana 2、Seedance）：需按官方文档核实模型 ID、参数、计费。
## 阻塞
- 开发环境无法访问 PyPI，无法安装 FastAPI/SQLAlchemy 并运行其测试。需所有者在 claude.ai 设置 → Capabilities 中允许软件包仓库访问，或改为仅依赖 GitHub CI 验证。
## 下一步
- 解除阻塞后：FastAPI 项目创建与视频上传、FFprobe 元数据、数据库持久化（分镜版本、任务、费用记录）。
