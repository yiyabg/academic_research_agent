# 学术论文深度调研 Agent 交付状态

更新时间：2026-08-22

## 结论

Phase 0–6 的独立 V1 已实现并部署在 `academic_research_agent/`，`shopping_agent/` 未承载任何
学术调研实现。公开检索、完整容器栈和控制面已真实验证；项目级第三方 key 已通过真实
`gpt-5.5` Responses 请求，readiness 已开放 `full_research=true`。该 key 未写入 Codex 全局配置，
系统仍以真实 provider 探测为准，不会把 full-research mock 成成功。

最新的执行过程、已实现功能、真实验收证据、未实现部分和下一步计划见：

[`ACADEMIC_RESEARCH_AGENT_EXECUTION_PROGRESS_2026-08-21.md`](ACADEMIC_RESEARCH_AGENT_EXECUTION_PROGRESS_2026-08-21.md)

原审计报告保留为本轮安全/OCR/图表/gold/故障注入之前的历史快照，不再代表最新状态。

## 当前验证摘要

- 后端最终全量门禁：490 passed；旧 Phase-1 生产 mock 已删除；Ruff 0 error。
- 前端：TypeScript 通过，Vitest 30 passed，生产 build 成功，ESLint 0 error。
- Alembic：`0041_research_orgs (head)`；真实组织/成员模型和同步撤权已部署。
- 真实检索：400 raw、300 unique、299 works、三来源各 100，终态 COMPLETED。
- 真实控制：运行中暂停响应 0.026 秒，精确恢复后 COMPLETED；独立取消到 CANCELLED；事件后缀
  重放通过。
- 真实隔离：四用户双组织 API+BFF 矩阵通过；个人项目不共享、跨组织直链 404、撤销成员后即时 404。
- 并发隔离：16 路邀请=`1×201+15×409`，16 路撤销=`1×204+15×404`，撤权后 100 次并发读取全 404；
  4-worker 生产式 localhost backend/BFF P95 约 0.61–0.65 秒，但不冒充独立压测容量结论。
- 生产式冷启动：17 服务独立 Compose、4 个 API 子进程、三研究 Worker/四资源队列、迁移/桶/模型门禁全部通过；
  API、前端、PostgreSQL、authenticated Redis、Qdrant、MinIO、GROBID、ClamAV、Beat、Flower 正常。
- 持续故障：Qdrant/GROBID 各中断 60 秒并完成 6 次能力采样后自动恢复；真实 IO worker SIGKILL
  退出码 137、PID 替换、watchdog 恢复、一次状态迁移和事件重放均通过。
- 多进程观测：生产 metrics 强制 Bearer；未授权 401，256 请求计数增量精确 256，四个 worker PID
  均生成 metric shard。
- 分析并行：每篇论文有独立持久化 Celery shard、稳定任务 ID/输入版本哈希和 PostgreSQL 终态 barrier；
  部署态回滚验证证明 pending 会阻塞且仅在全部终态后推进。
- LLM：OpenAI、DeepSeek、项目级 OpenAI-compatible 三通道已实现凭据隔离；当前第三方
  `gpt-5.5` 实际返回 `OK`，readiness=`healthy/full_research=true`。
- 真实 2 篇预验收已发现并修复“第三方生成 key 误选官方 embeddings”和 arXiv 查询双重引号；
  Crossref/OpenAlex 无许可证 PDF 继续 fail-closed，复跑等待 auth 限流窗口自然恢复。

## 外部阻塞项

- 第三方 `gpt-5.5` 通道已打通，但 2–3 篇预验收和目标 20 篇 full-research E2E 尚未执行。
- 未提供许可 JIF/CAS 数据和跨领域人工 gold dataset。
- 尚缺跨领域双人裁决 gold、20 篇人工证据/图表核验、独立 P95/成本压测、真实 20 篇运行 SIGKILL
  和新模板 commit 三方合并演练。
