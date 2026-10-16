# AI Creative Studio — 合规预检切片（v0.2，2026-10-16）

给编导团队的内部增效工具的**第一个可运行切片**：在生成昂贵视频之前，对脚本/分镜文本做合规预检，并把"系统预检结果"与"平台真实审核结果"对照起来，用数据校准规则。

## 不是什么
- 不是合规批准。系统永远不输出"合规"，只输出"发现了什么"，必须由指定合规负责人放行。
- 规则库是**起步清单**，未经真实拒审数据校准，不是法律意见。
- 目的是让内容真实、有依据、符合平台政策，不是规避审核。
- 只检查文本；画面检查（敏感身体画面等）尚未实现。

## 运行（仅标准库，Python 3.10+）
    python3 -m unittest discover -s tests -t .
    python3 -m creative_studio.compliance.cli check --input script.txt --content-id V123 --db precheck.db
    python3 -m creative_studio.compliance.cli outcome --db precheck.db --content-id V123 --status rejected --reason "医疗/药品/限制行业"
    python3 -m creative_studio.compliance.cli report --db precheck.db

退出码：发现红色风险返回 2，其余 0。输入可为 .txt 脚本或分镜 JSON（字段见 engine.SCANNED_FIELDS）。

## 业务框架
- 目标：让编导聚焦调研与创新，AI 承担拆解、分镜、提示词、生成、剪辑；衡量标准是有效素材数/人/天、单条合格视频的人工工时与成本、首次合格率，而非生成总数。
- 七层流水线：输入 → 理解 → 创意 → 资产 → 生产 → 合成 → 质检与回流；工作单位是镜头，分镜 JSON 是统一数据契约。
- 经营数据不放在本仓库。
