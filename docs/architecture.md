# Architecture（2026-10-16）
- `compliance/engine.py`：规则加载与校验（RuleSet）、文本/分镜扫描（ComplianceChecker）、结果结构（CheckResult/Finding）。红=block_recommended，黄=needs_review，无命中=no_issues_found（≠合规）。
- `compliance/rules/supplements_v0.json`：版本化规则库；类目标签沿用平台拒审分类，便于与平台反馈对齐。按类目可新增规则文件（香水等）。
- `compliance/store.py`：sqlite 存预检记录与平台真实结果；`agreement_report` 输出命中/漏报/误报与各平台原因的捕获率（样本<~100/类时仅作参考）。
- `compliance/cli.py`：check / outcome / report 三个命令。
- 分镜 JSON 与 V1.1 规格兼容（扫描 story_description、dialogue、voice_over、on_screen_text、image_prompt、video_prompt、audio_prompt）。
