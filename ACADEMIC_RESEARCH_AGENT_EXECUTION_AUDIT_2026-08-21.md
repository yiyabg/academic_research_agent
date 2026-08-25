# 通用学术论文深度调研 Agent 方案执行与验收报告

更新时间：2026-08-21（Asia/Shanghai）

独立项目：`/home/cumt/lly/ai_agent/full-stack-ai-agent-template/academic_research_agent`

方案源：`/home/cumt/lly/ai_agent/full-stack-ai-agent-template/通用学术论文深度调研agent系统.md`

## 1. 当前结论

已在 Full-Stack AI Agent Template 上新建独立的 `academic_research_agent`，没有把学术业务堆入
`shopping_agent`。Phase 0–6 的 V1 代码、数据库、API、异步任务、前端工作台和部署拓扑均已落地；
公开学术检索、筛选、暂停/恢复/取消、事件重放、故障看门狗和完整容器栈已做真实运行验证。

当前部署是“检索能力可用、LLM 深度研究显式关闭”的诚实降级状态：没有配置
`OPENAI_API_KEY` 时，readiness 返回 `search_only=true`、`full_research=false`，创建
`full_research` 运行返回 503，而不是用 mock 或空报告伪装成功。完整分析/证据综合/多格式发布的
实现和自动测试已经完成，但生产 LLM、许可指标和人工 gold 数据仍需部署方提供，因此不能把这些
外部验收项写成已经完成。

## 2. 方案执行进程

| 阶段 | 状态 | 已执行内容 |
|---|---|---|
| Phase 0 基线与边界 | 完成 | 冻结模板 commit `3428d9a...`、Python 3.12 和 ADR；新建独立 Agent；领域代码集中在 `literature_research`/`research` 命名空间 |
| Phase 1 协议与工作流 | 完成 | 协议编译、规范哈希、显式批准、幂等 run、状态机、事务 outbox、WebSocket/REST 事件重放、严格短缺授权 |
| Phase 2 多源发现 | 完成并真实联调 | Crossref、OpenAlex、arXiv；查询族、分页停止规则、原始快照、provenance、Work/Version 聚合和模糊去重 |
| Phase 3 质量与硬约束 | 完成 | 授权指标快照导入、期刊/会议语义分离、PASS/FAIL/UNKNOWN 账本、UNKNOWN fail-closed、严格数量不足不降质 |
| Phase 4 相关性、全文、证据 | 代码完成，公开检索已联调 | 词法/本地 embedding/CrossEncoder/facet，合法全文策略，MinIO，PyMuPDF+GROBID，页码/区间/文档哈希证据定位，Qdrant 隔离 |
| Phase 5 深度分析与发布 | 代码和自动测试完成 | 六个结构化专家、evidence 白名单、主张审计、综合、发布门禁、六类确定性产物、单篇重分析和多代重生成 |
| Phase 6 记忆、评测、前端 | 完成 | L0–L4 记忆、反馈、离线 gold 评测引擎、研究工作台、BFF、能力选择、暂停/恢复/取消、下载和评测 UI |
| 生产部署 | 完成（受外部能力门禁） | PostgreSQL、Redis、Qdrant、MinIO、GROBID、API、Next.js、三类 Worker、Beat、Flower 全部启动；Alembic head=0036 |

## 3. 关键实现细节

### 3.1 协议和语义边界

- 自然语言课题被编译为版本化 `ResearchProtocolVersion`，包含主题 facet、绝对日期窗、文献类型、
  来源、语言、期刊/会议质量规则、数量策略和输出要求。
- 协议必须由用户显式 approve；run 固定批准版本及 `sha256:` 规范哈希，Agent 无权改变。
- JIF、CAS 分区、会议级别、日期、相关性和数量分别建模；会议不会套用期刊 JIF，未知指标不会
  由模型猜测，也不会按 PASS 处理。
- `validate_only`、`search_only`、`full_research` 三种执行模式真正改变状态路径；前端默认选择
  当前可用的 `search_only`。

### 3.2 可恢复确定性工作流

- PostgreSQL 的 `research_runs.state/state_version` 是业务真相，Celery 只执行携带期望状态的任务。
- `FOR NO KEY UPDATE` 串行化重复 stage 投递，避免重复副作用；独立的
  `research_run_controls` 表允许长阶段执行时并发提交暂停/取消请求。
- 暂停写入 `paused_from`，恢复回到准确阶段；失败恢复读取 `failure.stage`，不再一律从头运行。
- 每分钟看门狗查询 PostgreSQL 中超过阈值且无有效 lease 的活动 run，写短 lease 后重新入队。
- Beat 的 outbox、恢复和 RAG 定时任务显式路由到实际监听的 `research-io/cpu` 队列。
- outbox 与状态同事务，REST `after_sequence` 和 WebSocket 均支持断线后的事件后缀重放。

### 3.3 检索、版本和严格筛选

- 三个公开 adapter 使用统一异步接口、限流/重试/分页规则；每页保留请求指纹、cursor、响应元数据、
  原始记录和快照哈希。
- DOI、标题、作者、venue、日期和来源标识分别归一化并保留 provenance；arXiv、Crossref 和正式版
  聚合为一个 Work 下的多个 Version，模糊冲突进入 REVIEW。
- 每篇每项硬约束保存判定、观察值、期望值、原因和证据引用；只有全部硬约束 PASS 才进入严格集合。
- 相关性采用词法召回、本地 MiniLM embedding、CrossEncoder 和 must-have facet；数量不足只披露
  shortfall，不自动放宽任何阈值。

### 3.4 全文、证据和专家

- 全文只能来自明确允许的 publisher OA、Unpaywall、公开仓储或授权连接器；下载要求 HTTPS，
  拒绝 localhost/显式私网、超限内容、错误 MIME 和伪 PDF。
- 原文以哈希寻址保存到 MinIO；GROBID TEI 与 PyMuPDF 页文本用于章节和页级块，GROBID 故障时
  显式回退。
- EvidenceLocator 绑定 work/version/block、页码、绝对字符区间、精确 quote、块哈希和文档哈希；
  quote 不是原块子串或 Agent 引用未授权 evidence ID 时拒绝落库。
- 六专家分别处理背景问题、方法流程、架构、实验、结论和局限，Pydantic Schema 限制输入输出；
  审计器检查证据覆盖、证据是否跨 claim 移用、矛盾和无支持主张。

### 3.5 不可变产物和发布门禁

- CanonicalResearchReport 确定性渲染 Markdown、OPML、BibTeX、JSONL、CSV 和
  `run_manifest.json`。
- manifest 记录 generation、协议、模板 commit、模型、来源/指标快照、数量和各产物 SHA-256。
- 发布前从对象存储回读并复核格式、大小、哈希和 manifest；同时检查协议变化、硬约束、重复冲突、
  相关性、证据覆盖、矛盾、无支持主张和短缺披露。
- 单篇重分析新增 immutable attempt，并把运行标记为需要重生成；WF-5 接口通过独立 LLM 队列创建
  递增 generation。未通过发布门禁的 generation 即使知道 ID 也不能由列表/下载 API 获取；旧代不覆盖。

### 3.6 隔离、前端与部署

- 所有 API 先校验 owner；对象键包含 organization/personal、project、run；Qdrant collection/filter
  同时限制 tenant/project/run。
- Next.js 提供项目、协议、新运行、运行工作台、漏斗、候选、论文详情、证据、图题、短缺动作、
  暂停/恢复/取消、单篇重分析、新代产物、下载和评测看板。
- Compose 拆分 `research-io`、`research-cpu`、`research-llm`；CPU worker 使用 solo pool 避免
  PyTorch/tokenizer fork 死锁；模型由一次性 init 下载到共享 cache，运行 Worker 离线读取。
- GROBID 使用 JDK cgroup 兼容参数；MinIO bucket 初始化为私有；迁移是启动前 one-shot gate。

## 4. 已实现功能清单

- 任意课题协议编译、澄清、版本化、哈希和批准。
- Crossref/OpenAlex/arXiv 真实发现、快照、规范化、provenance、去重与版本族。
- 授权指标导入和三态硬约束账本。
- 本地 embedding、CrossEncoder、facet 相关性和严格 shortfall。
- 合法全文策略、对象存储、GROBID/PyMuPDF、页级证据与向量隔离。
- 六专家、evidence 白名单、审计、综合、发布门禁。
- 六类导出、manifest、稳定哈希、回读验证、多 generation 和旧代保留。
- 单篇重分析、不可变 attempt、幂等异步任务。
- 暂停、取消、精确恢复、失败重试、失联看门狗和事件重放。
- L0–L4 记忆、反馈、离线评测 API 与 UI。
- 独立研究 BFF、工作台、能力降级提示和完整 Compose 拓扑。
- 注册/立即登录显式事务提交，消除成功响应后的可见性竞态。

## 5. 验收证据

### 5.1 自动测试与构建

| 门禁 | 结果 |
|---|---|
| 后端最终全量 pytest | 414 passed，4 skipped |
| 学术领域 + health/OpenAPI 回归 | 105 passed |
| Ruff | 0 error |
| 前端 TypeScript | 通过 |
| 前端 Vitest | 5 files / 28 tests passed |
| ESLint | 0 error；29 条模板既有 warning |
| Next.js production build | 成功；118 个静态页面生成，research 动态页/BFF 已生成 |
| Alembic | `0036_versioned_outputs (head)`；migrate container exit 0 |

最终代码删除了仅供 Phase 1 演示的生产 `mock_pipeline.py` 及对应旧测试，真实 workflow 不引用 mock。

### 5.2 真实公开学术来源 smoke

`backend/scripts/live_scholarly_smoke.py` 的部署实测：

- Crossref：HTTP 200，100 records，163342 bytes。
- OpenAlex：HTTP 200，100 records，2369651 bytes。
- arXiv：HTTP 200，100 records，260170 bytes。

### 5.3 真实检索流水线 E2E

脚本：`backend/scripts/live_research_pipeline_e2e.py`

运行 `1e130dce-e7d5-4d97-8dc7-83453baed1a7`：

- 终态 `COMPLETED`，state_version=8，candidate_count=299，strict_count=6。
- 6 个查询全部成功并达到停止规则，6 页、400 raw records、300 unique records。
- 299 Work、300 Version；Crossref/OpenAlex/arXiv 各 100 条。
- embedding=`sentence-transformers/all-MiniLM-L6-v2`，CrossEncoder=
  `cross-encoder/ms-marco-MiniLM-L6-v2`。
- 未配置 LLM 时创建 `full_research` 返回 503。

### 5.4 真实控制面 E2E

脚本：`backend/scripts/live_research_control_e2e.py`

运行 `b687309f-...` 在 `DISCOVERING` 中收到暂停请求：

- 暂停 API 响应 0.026 秒，没有被长 stage 行锁阻塞。
- `DISCOVERING → PAUSED`，`paused_from=DISCOVERING`。
- 恢复后 `PAUSED → DISCOVERING → ... → COMPLETED`。
- `after_sequence` 成功返回 8 个缺失事件。
- 独立运行 `7545115f-...` 达到 `CANCELLED`。
- search-only 调用产物重生成返回 409，执行模式隔离正确。

### 5.5 真实部署状态

| 服务 | 本机端口/状态 |
|---|---|
| FastAPI | `58000`，healthy |
| Next.js | `53000`，healthy |
| PostgreSQL | `55432`，healthy |
| Redis | `56379`，healthy |
| Qdrant | `56333/56334`，healthy |
| MinIO | `59000/59001`，healthy，私有 bucket |
| GROBID | `58070`，`/api/isalive=true` |
| Worker | research-io/cpu/llm 三队列 healthy |
| Beat/Flower | running |

readiness 对 database、Redis、Qdrant、S3 和三个队列全部返回 healthy；LLM 明确返回 unavailable。
看门狗在 `research-io` 实际 received 并 `succeeded: 0`。修复路由后清除了 2664 条仅由旧错误路由
产生的默认队列周期任务，随后 `celery=0`、`research-io=0`。

## 6. 尚未实现或尚未完成生产验收

以下内容不会被标记为“已完成”：

1. **真实 full_research 运行**：代码和 fixture 测试完成，但部署没有 `OPENAI_API_KEY`，所以尚未用
   生产模型完成论文级分析、综合、发布和多代重生成 E2E。
2. **许可质量数据**：JIF/CAS/会议快照导入、有效期和审计已经实现，但仓库不能附带 Clarivate/
   CAS 等受限数据；当前没有真实许可快照。
3. **像素级图表工程**：已有页码、图/表编号、caption、evidence ID 和文档哈希；尚无完整 bbox、
   图片裁剪、表格单元结构重建和 plot digitization。
4. **扫描 PDF/OCR 与恶意文件扫描**：当前 PDF 路径检查大小、MIME、签名和哈希，但生产级 OCR、
   杀毒/沙箱解析和 DNS 解析后的 SSRF pinning 尚未完成。
5. **人工 gold dataset**：评测引擎和阈值已实现，仓库只有 fixture；Recall@Pool、Precision@20、
   nDCG、证据 precision、数值 accuracy 等不能在缺少用户标注集时宣称达标。
6. **生产规模与故障注入**：已验证重复投递锁、真实暂停恢复、看门狗执行和容器健康；尚未完成
   20 篇 full-research 的 worker kill、外部服务长时间中断、并发多租户和成本/P95 压测。
7. **模板既有技术债**：前端仍有 29 条非 research 专属 warning，Pydantic 有 v3 前的 deprecation
   warning；不影响当前构建，但应在模板升级窗口处理。

## 7. 下一步计划

1. 由部署方注入生产 `OPENAI_API_KEY`，先跑 2–3 篇小型 full-research，再跑目标 20 篇；逐项人工
   核验证据、manifest、generation、成本和发布门禁。
2. 导入合法的 JIF/CAS/会议指标快照，执行 journal-only 与 conference-only 两类协议验收。
3. 建设跨领域、跨语言、预印本/正式版分层的人工 gold dataset，所有 `NOT_EVALUATED` 维度保持
   未通过，直到样本量和阈值满足方案要求。
4. 增加 PDF 像素 bbox、图片裁剪、表格结构、OCR、恶意文件扫描和 DNS/IP 固定下载 transport。
5. 做 20 篇全链路故障注入：Worker SIGKILL、Redis/Qdrant/MinIO/GROBID 暂停、重复投递、WS 断线、
   多租户并发，并记录 recovery rate、P95 latency 和 cost/run。
6. 清理模板既有 ESLint/Pydantic warning，并在下一模板 commit 上执行三方合并演练。

## 8. 常用复验命令

```bash
cd /home/cumt/lly/ai_agent/full-stack-ai-agent-template/academic_research_agent

docker compose --env-file backend/.env \
  -f docker-compose.yml \
  -f docker-compose.research.yml \
  -f docker-compose.frontend.yml up -d

cd backend
DEBUG=true uv run pytest -q
uv run ruff check app tests scripts
uv run python scripts/live_scholarly_smoke.py
uv run python scripts/live_research_pipeline_e2e.py http://127.0.0.1:58000
uv run python scripts/live_research_control_e2e.py http://127.0.0.1:58000
```

## 9. 关键入口

- `ACADEMIC_RESEARCH_AGENT_IMPLEMENTATION.md`：实现结构和不变量。
- `backend/app/services/literature_research/`：真实领域 pipeline、证据、发布和记忆服务。
- `backend/app/clients/scholarly/`：公开学术来源 adapter。
- `backend/app/agents/literature_research/`：六类有界专家。
- `backend/app/worker/tasks/literature_research_tasks.py`：stage、重分析、产物重生成和恢复任务。
- `backend/app/api/routes/v1/literature_research/`：认证且隔离的 HTTP/WebSocket API。
- `frontend/src/app/[locale]/(dashboard)/research/`：研究工作台。
- `docker-compose.research.yml`：独立研究运行拓扑。
