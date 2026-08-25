# 学术论文深度调研 Agent：工程分析与实现说明

更新时间：2026-08-21

## 1. 项目基线与独立性

本项目由 Full-Stack AI Agent Template 提交
`3428d9a6214619d3514312886d59a36400747b7d` 独立生成，生成器 0.2.19，运行时固定
CPython 3.12。领域代码集中在 `literature_research` 和前端 `research` 命名空间，没有复用
或堆积到 `shopping_agent`。

保留模板分层：FastAPI route 只处理认证与协议；service 承载规则；repository 负责查询与
flush；PostgreSQL 是真相源；Celery 只执行工作；Next.js 通过同源 BFF 访问后端。

## 2. 总体架构

```text
Next.js Research Workbench -> authenticated BFF -> FastAPI research API
 -> Protocol/Run/Workflow services -> PostgreSQL + transactional outbox
 -> Celery research-io / research-cpu / research-llm
 -> scholarly APIs / GROBID+PyMuPDF / bounded LLM experts
 -> Qdrant evidence+memory / MinIO immutable objects
 -> release gate -> Markdown/OPML/BibTeX/JSONL/CSV/manifest
```

关键不变量：已批准协议的规范 JSON/哈希不可被 Agent 修改；硬约束只有 PASS 才能进入严格
集合；状态和 outbox 同事务；Agent 只能返回 Pydantic Schema 和白名单 evidence ID；原始响应、
全文和导出物内容寻址；对象路径和向量过滤同时包含 tenant/project/run。

## 3. 端到端实现

### 协议与状态机

`ProtocolCompilerService` 将自然语言和显式字段编译为主题 facet、绝对日期窗、文献类型、
期刊/会议规则、质量阈值、数量策略和输出要求。缺字段返回澄清问题；草稿必须显式 approve，
规范序列化后生成 `sha256:` 哈希。

```text
QUEUED -> DISCOVERING -> NORMALIZING -> ENRICHING_METRICS
-> DEDUPLICATING -> HARD_FILTERING -> RELEVANCE_SCORING
-> FULLTEXT_ACQUIRING -> PARSING -> SELECTING
-> ANALYZING -> EVIDENCE_AUDITING -> SYNTHESIZING
-> RENDERING -> RELEASE_CHECKING -> COMPLETED/PARTIALLY_COMPLETED
```

任务携带期望状态和版本，`FOR NO KEY UPDATE` 串行化重复投递，同时允许独立控制表并发写入暂停/
取消请求；支持 `PAUSED` 精确检查点恢复、取消、REST 事件补发与 WebSocket。Beat 每分钟从
PostgreSQL 真相源扫描失联 stage，以短 lease 防重后重新入队，Celery result 不承担恢复真相。
结果不足时停在 `AWAITING_RELAXATION_AUTHORIZATION`，只能接受严格短缺、新建协议或取消。

### 发现、版本与约束

- Crossref、OpenAlex、arXiv 使用统一异步 adapter；exact/facet/broad 查询族保留日期和类型边界。
- 每页保存 cursor、请求指纹、HTTP 元数据、原始记录及可复现 gzip，来源失败单独落账。
- DOI、标题、作者、场馆、日期和标识符保留 provenance；Work/Version/SourceRecord 区分作品、
  预印本/正式版本和来源观察；模糊去重 REVIEW 不会自动吞并。
- JIF/CAS/会议数据只能用带许可、有效期和哈希的管理员快照导入，没有未授权抓取逻辑。
- 每条硬约束保存 PASS/FAIL/UNKNOWN、证据和原因；UNKNOWN fail-closed。
- 相关性按词法、可插拔 embedding、CrossEncoder/facet 执行；不确定进入 REVIEW，排序只作用于
  已满足硬约束集合。

### 全文、解析与证据

全文策略按 publisher OA、Unpaywall、公开仓储和授权连接器排序，许可不明即拒绝。下载器强制
HTTPS，阻断 localhost/显式私网地址，检查最终重定向 URL、大小、MIME 和 PDF 签名。当前学术
运行路径使用 PyMuPDF；配置 `GROBID_URL` 时保存 TEI 并用章节标题增强页级块，故障时显式回退。

解析块保存页码、章节、绝对偏移和文本哈希。EvidenceSpan 要求 quote 是原块精确子串，并绑定
work/version/block、页码、区间、块哈希和文档哈希。Qdrant 查询强制 tenant/project/run 过滤。

### 六专家、重分析与导出

六个 PydanticAI 专家分别处理背景问题、方法流程、架构、实验、结论和局限。论文并发分析，
输入/输出有长度、超时和 Schema 边界；合并拒绝未知 evidence ID，审计证据覆盖、矛盾和无支持
主张。单篇重分析创建递增且不可变的 attempt，成功/失败均落库发事件，不覆盖历史。

同一 CanonicalResearchReport 确定性渲染 Markdown、OPML、BibTeX、JSONL、CSV 和 manifest；
manifest 记录协议、模板、模型、来源/指标快照及产物哈希。发布前从对象存储回读全部对象，验证
格式、大小、SHA-256 和 manifest。门禁还检查协议、硬约束、重复冲突、相关性、证据覆盖、矛盾、
无支持主张和短缺披露，Agent 无权绕过。重分析后可通过独立 LLM 任务生成递增且不可变的 synthesis/
artifact generation；只有对应代发布门禁通过后 API 才列出或下载，旧代不会覆盖。

## 4. 数据、记忆和评测

迁移 `0028`–`0036` 覆盖协议/运行、发现、质量、证据、分析产物、记忆反馈、分析 attempt、
离线评测、运行控制和多代产物。核心实体包括 Project、ProtocolVersion、Run、RunControl、
TaskExecution、Outbox、SourceQuery/
Page/Record、Work/Version/Venue、MetricSnapshot、ConstraintLedger、Evidence、Analysis、Artifact、
ReleaseCheck、Memory/Profile/Policy/Feedback、EvaluationDataset/Result。repository 不 commit。

五层记忆：L0 单次 Agent 上下文；L1 Redis 24h 会话槽；L2 PostgreSQL+Qdrant 项目记忆；L3
显式确认的版本化画像；L4 版本化领域策略。优先级为当前输入 > 批准协议 > 项目 > 画像 > 策略
> 默认，低层记忆不能改变协议或质量底线。

离线评测计算 Recall@Pool、Precision@20、nDCG@20、硬约束合规、pairwise dedup F1、元数据、
证据 precision/coverage、数值、恢复率和产物校验；缺 gold 维度返回 `NOT_EVALUATED`，不会假通过。

## 5. API、前端与部署

API 覆盖项目、协议编译/审批、run 创建/查询/暂停/取消/精确恢复、短缺动作、候选、论文/证据、
单篇重分析、事件/WS、产物多代重生成、指标、记忆、反馈和评测；所有资源先校验 owner/tenant。

前端提供 `/research`、`/research/new` 和运行工作台，包含时间线、可解释漏斗、候选约束原因、
论文/证据/图题面板、短缺卡、产物下载、评测看板和 WS 同步；未知总量不伪造百分比。

基础 Compose 使用兼容的合并 worker；`docker-compose.research.yml` 提供 research-io/cpu/llm、
MinIO 初始化和 GROBID。对象键为：

```text
tenants/{organization|personal}/projects/{project}/runs/{run}/...
```

代码级 V1 已覆盖 Phase 0–6，公开检索与完整容器拓扑已真实联调。生产 LLM 凭据、许可数据和
跨领域 gold dataset 仍属于部署验收，详见 `ACADEMIC_RESEARCH_AGENT_EXECUTION_AUDIT_2026-08-21.md`。
