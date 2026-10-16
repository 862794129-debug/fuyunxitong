# Architecture（2026-10-16）
- `compliance/engine.py`：规则加载与校验（RuleSet）、文本/分镜扫描（ComplianceChecker）、结果结构（CheckResult/Finding）。红=block_recommended，黄=needs_review，无命中=no_issues_found（≠合规）。
- `compliance/rules/supplements_v0.json`：版本化规则库；类目标签沿用平台拒审分类，便于与平台反馈对齐。按类目可新增规则文件（香水等）。
- `compliance/store.py`：sqlite 存预检记录与平台真实结果；`agreement_report` 输出命中/漏报/误报与各平台原因的捕获率（样本<~100/类时仅作参考）。
- `compliance/cli.py`：check / outcome / report 三个命令。
- 分镜 JSON 与 V1.1 规格兼容（扫描 story_description、dialogue、voice_over、on_screen_text、image_prompt、video_prompt、audio_prompt）。

## G0 核心数据层（2026-10-16）
- `shots/schema.py`：Storyboard（不可变版本，parent_version_id 串联历史）→ Shot（V1.1 字段）→ SourceEvidence / UnknownMark。`storyboard_json_schema()` 供模型结构化输出与 API 校验。
- `adapters/base.py`：VideoAnalyzer / ImageGenerator / VideoGenerator 抽象；CallMeta 统一记录 provider、model_id、is_mock、cost_usd、latency_ms。异常 SubmissionOutcomeUnknown 表示"可能已扣费"。
- `adapters/mock.py`：确定性 Mock，仅验证管线连通。
- `jobs/video_jobs.py`：VideoJobManager。幂等键 = project:version:shot:attempt；UNKNOWN 状态禁止新提交，必须先 poll；NOT_FOUND 时用同一键重提；预算上限 BudgetExceeded。当前为内存态，G0 持久化时落库。
- 数据流：参考视频 → VideoAnalyzer → Storyboard v1 →（编辑）v2… → ImageGenerator 首帧 → VideoJobManager/VideoGenerator 按镜头生成。
