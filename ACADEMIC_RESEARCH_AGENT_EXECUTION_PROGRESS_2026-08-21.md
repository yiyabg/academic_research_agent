# 通用学术论文深度调研 Agent 方案执行进度

更新时间：2026-08-22（Asia/Shanghai）

项目目录：`/home/cumt/lly/ai_agent/full-stack-ai-agent-template/academic_research_agent`

方案文件：`/home/cumt/lly/ai_agent/full-stack-ai-agent-template/通用学术论文深度调研agent系统.md`

## 1. 当前结论

该实现不是在通用聊天界面外包一层提示词，也没有继续堆入 `shopping_agent`。学术调研系统已经以
独立 `academic_research_agent` 项目落地，具有独立数据库模型、43 个 Alembic revision、认证 API、
确定性状态机、事务 outbox、三类 Celery worker/四个资源队列、对象存储、向量库、恶意文件扫描、PDF/OCR 解析、
图表裁剪、证据账本、发布门禁、前端研究工作台和离线评测服务。

当前部署可真实执行公开学术检索、控制面和生成式 LLM 流程。第三方 OpenAI-compatible key 仅保存在
`academic_research_agent/backend/.env` 并仅注入本项目容器；没有修改 Codex 的
`~/.codex/config.toml` 或 `~/.codex/auth.json`。容器内真实 Responses 请求已由 `gpt-5.5` 返回 `OK`，
readiness 为 `search_only=true/full_research=true`。系统不会调用 mock 或生成空分析冒充成功。因此
Phase 0–6 的工程骨架和主要功能已落地，但 20 篇论文、许可指标与人工 gold 等最终验收尚未全部达成。

## 2. 方案执行进程

| 阶段 | 工程实现 | 真实部署验收 | 当前说明 |
|---|---|---|---|
| Phase 0 冻结与基线 | 完成 | 完成 | 模板 commit `3428d9a6214619d3514312886d59a36400747b7d`；5 个 ADR；独立项目和命名空间 |
| Phase 1 协议与运行骨架 | 完成 | 完成 | 协议编译/批准、哈希锁定、幂等 run、状态机、outbox、暂停/取消/恢复、事件重放均已实测 |
| Phase 2 发现、元数据与去重 | 完成 | DOI 限额公开源 E2E 与轻量论文集导出 E2E 完成；人工阈值验收部分完成 | 每个 run 只执行 1 次含 facets/同义词和原生类型过滤的 Crossref 搜索并截取 35 个去重 DOI，随后 OpenAlex 按 DOI 单篇补全；`search_only` 可冻结严格 Top-N 并导出四种元数据文件；未知类型 fail-closed；跨领域自建 gold 尚缺 |
| Phase 3 质量指标与约束 | 完成 | 规则门禁和管理 UI 已部署；许可数据待提供 | JIF/CAS/会议分离、授权 CSV 导入、精确 fact/year 来源审计、三态账本和 UNKNOWN fail-closed 已完成；真实 Clarivate/CAS 快照不能随仓库分发 |
| Phase 4 相关性、全文与证据 | 完成 | 公开检索、扫描、OCR、解析和真实 GPT 通道均已实测 | 本地 embedding/CrossEncoder、HTTPS/DNS pin、ClamAV、PyMuPDF/Tesseract/GROBID、bbox Evidence 均已运行 |
| Phase 5 深度分析与导出 | 完成 | 目标 2 篇真实预验收通过；8 产物部署审计通过；生产 20 篇 E2E 未完成 | 历史真实运行以 `1 succeeded + 1 failed_terminal` 越过 barrier，严格发布 1 篇、1,653 条证据；当前 8 产物合同、manifest 和数据库权威审计已在 PostgreSQL/MinIO 实测 |
| Phase 6 记忆、反馈与评测 | 完成 | 画像/记忆/L1/gold UI 已部署；真实分层 gold 部分完成 | L0–L4、L1 草案恢复、画像确认、反馈写入→索引→未来建议检索、gold 触发、最低样本量、分级 nDCG 和组织协作已实现 |
| 部署与恢复 | 完成 | 开发栈故障注入和独立生产式冷启动均完成 | 17 服务生产 Compose、4 API worker、三研究 worker/四资源队列、迁移/模型/桶初始化门禁和私有数据服务已实测 |

## 3. 本轮新增和完善的实现

### 3.1 全文下载与恶意文件安全

- 全文 URL 只允许 HTTPS 443，拒绝凭据 URL、localhost、私网、保留地址和非 PDF/超限响应。
- 每次 DNS 解析先校验公共 IP，再由自定义 HTTP transport 将 TCP 连接固定到已校验地址；重定向逐跳
  重做校验，防止 DNS rebinding 和重定向 SSRF。
- PDF 在进入 MinIO 前通过 ClamAV INSTREAM 扫描；扫描器不可用、命中病毒或 PDF 含主动内容时
  fail-closed。
- 获取账本保存解析 IP、重定向链、扫描引擎/签名/时间；旧的未扫描文档不能进入分析或发布。
- 容器实测：普通 PDF=`CLEAN`，EICAR=`INFECTED/Eicar-Test-Signature`，arXiv 下载记录公共解析 IP。

### 3.2 OCR、解析质量与证据坐标

- PyMuPDF 提取页级 layout block 和 bbox；原生文本不足时按页使用 Tesseract OCR。
- GROBID 提供章节结构；解析质量账本记录页覆盖、字符数、OCR 页数、caption 链接率、解析器版本和
  `PASSED/PARSING_LOW_CONFIDENCE`。
- 每个 block 和 EvidenceLocator 记录页码、bbox、提取方法、字符区间、块哈希和文档哈希。
- 解析质量未通过的文档不会被 selection 选择；删除了标题+摘要伪装全文的 fallback。
- 容器内生成的纯图片 PDF 实测 OCR：1/1 页、1771 字符、bbox 完整、质量 `PASSED`。

### 3.3 图表、表格和精确数值审计

- 通过 caption、页面图像、vector drawing 和表格区域定位图/表 bbox，裁剪为 PNG 存入对象存储并
  保存 SHA-256。
- 表格保存 cell matrix；`TABLE_EXACT` 数值必须逐字存在于审计后的单元格或源 artifact。
- plot digitization 必须记录坐标标定和误差，否则不能作为精确数值来源。
- 发布门禁比较解析出的 caption 数量与已验证 artifact 数量，缺少裁图或哈希即阻止发布。
- 容器实测表格裁剪得到 `Agent A=95.2%`、`Agent B=91.0%` 及确定性图片哈希，状态 `VERIFIED`。

### 3.4 真实人工基准与评测诚实性

- gold dataset 新增 `DRAFT`、`EXTERNAL_BENCHMARK`、`ADJUDICATED` 三种状态。
- `ADJUDICATED` 必须有 provenance 且至少两名标注者；外部 benchmark 必须声明评审方法和局限，
  不能冒充双人独立标注。
- relevance 保留 0–3 分级；nDCG 使用 `2^rel-1` gain，不再把所有相关样本压成二值。
- 所有关键指标设置最低样本量；样本不足返回 `NOT_EVALUATED`，不能用一个样本得到虚假 PASS。
- 新增 NIST TREC-COVID Complete 严格导入器，流式读取 257 MB 历史 `metadata.csv`，只保留 qrels
  所需行；缺标题映射、许可引用或样本不足时拒绝生成可评测数据集。
- 官方 qrels 实测共 69,318 行；topic 1 为 1,647 条（0/1/2 分别 948/362/337）；topic 50 为
  889 条，并将真实文件中的 1 条 `-1` 显式列为 unassessable 后排除，不偷换成不相关 0。
- 对应 CORD-19 元数据为 257 MB，当前代理下载预计超过一小时；绕过代理的 1 MB range 请求在
  60 秒内也未完成。两次下载均已停止并删除 `/tmp` 半文件，因此本轮没有虚报“真实 benchmark
  已导入数据库”。

### 3.5 可恢复工作流和故障注入

- production watchdog 从 PostgreSQL 真相源认领 lease 过期运行并重投当前 stage。
- worker 停止期间创建隔离 validate-only run，人工老化其 checkpoint，watchdog 认领 1 个运行；恢复
  worker 后原任务和重投任务共同到达，最终仅一次状态迁移：`COMPLETED/state_version=1`。
- 事件序列为 1..3 连续，含 1 个恢复事件，`after_sequence` 返回正确后缀。
- 故障实验发现陈旧任务虽无副作用但会被 Celery 记为失败；已修复为成功结果
  `status=STALE_IGNORED`，最终容器日志无 DB session error、无 unexpected exception。
- Redis 中断：readiness HTTP 503、`search_only=false`；恢复后 Redis/worker/readiness 自动健康。
- MinIO 中断：readiness HTTP 503、对象存储 unhealthy；恢复后自动 healthy。
- ClamAV 中断：搜索仍 HTTP 200/`search_only=true`，恶意扫描 unavailable、`full_research=false`；
  恢复后扫描 ping 成功。
- Qdrant 中断：readiness HTTP 503、vector store unhealthy；恢复后自动回到 ready/healthy。
- GROBID 中断：搜索仍 HTTP 200/`search_only=true`，解析 unavailable、`full_research=false`；恢复后
  PyMuPDF/Tesseract/GROBID 版本探针全部 healthy。
- 新增宿主级持续中断验收器并在当前部署实测：Qdrant 和 GROBID 各保持中断 60 秒、每 10 秒采样，
  各 6 个窗口样本全部维持上述 fail-closed/安全降级语义；含收敛和恢复的总墙钟分别为 77.78 秒和
  88.15 秒，脚本无论成功或异常都会在 `finally` 恢复精确服务。
- 新增真实 worker SIGKILL 验收：先对精确 `research-worker-io` 临时关闭自动重启，再发送 SIGKILL；
  Docker 确认退出码 137。worker 停止期间创建持久化 validate-only run，PostgreSQL watchdog 重投，
  恢复后 PID 已替换且 restart policy 回到 `unless-stopped`；运行最终 `COMPLETED/state_version=1`，
  事件 1..3 连续、恰好 1 个 recovery event、后缀重放 1 条，最终版总耗时 19.69 秒。
- 部署态双用户隔离：outsider 项目列表不含 owner 项目；对 owner 的 run、events、candidates、
  artifacts、pause、cancel 全部返回 404，owner 运行仍为 `COMPLETED`。

### 3.6 真实组织模型、活动上下文与同步撤权

- 核查确认旧实现的 `organization_id` 只有可空字段和对象/Qdrant 命名空间用途：没有组织表、成员表、
  成员 API 或可到达的组织项目创建链路。因此旧状态只算字段占位，不能算组织隔离落地。
- 新增 `research_organizations`、`research_organization_members` 和迁移 `0041_research_orgs`；角色为
  `OWNER/MEMBER`，组织创建与 OWNER 成员在同一事务中落库，组织 slug 和成员关系由数据库唯一约束保护。
- 新增组织创建、列表、成员列表、按已注册邮箱邀请、撤销成员 API；非成员统一得到 404，成员管理仅
  OWNER 可执行，OWNER 自身不能被删除。
- 项目创建同时支持 body `organization_id` 与 `X-Research-Organization-ID` 活动上下文；二者不一致
  时拒绝请求。个人项目仅 owner 可访问；组织项目和所有下游 run/protocol/candidate/evidence/artifact/
  evaluation/memory 接口均在请求时检查当前成员关系。
- 项目/运行列表可按活动组织过滤；对象存储 key 和 Qdrant payload/filter 继续使用同一个组织 ID，形成
  API、PostgreSQL、MinIO、Qdrant 四层一致租户边界。
- 撤销成员的 204 曾暴露 FastAPI yield dependency 收尾提交竞态；已改为事务提交完成后再响应，保证
  后续请求立即失权。组织创建、邀请和项目创建也使用相同的响应前 durability boundary。
- 前端新增持久化活动组织选择器、创建组织、OWNER 成员管理和个人/组织研究空间切换；Next.js BFF
  明确转发 `X-Research-Organization-ID`。
- 最终镜像部署态四用户双组织矩阵通过：组织成员可访问彼此创建的项目/运行；同组织个人项目仍 404；
  A/B 组织双向直链、outsider 事件和活动组织探测均 404；撤销后 project/run/list 立即全部 404；
  Next.js 代理 owner 列表 200、outsider 列表 404。
- PostgreSQL `ON CONFLICT DO NOTHING ... RETURNING` 和原子 `DELETE ... RETURNING` 消除了并发邀请/
  撤销的唯一约束与 ORM stale-delete 500。最终镜像 16 路竞争稳定为邀请 `1×201 + 15×409`、撤销
  `1×204 + 15×404`，撤销后的 100 个并发读取全部 404。
- 单机突发基线（不是生产容量结论）：backend authorized/denied 各 100 个样本的 P95 为
  1474.88/1466.99 ms，Next.js BFF authorized/denied 各 50 个样本为 1470.99/1472.65 ms；当前是单
  Uvicorn development topology，说明生产多 worker、独立压测机和容量目标仍需验收。

### 3.7 独立生产拓扑与冷启动门禁

- 核查发现旧 `make prod` 仍是模板通用拓扑：缺少 MinIO、GROBID、ClamAV、迁移服务和三类独立
  研究 worker，Redis 要求密码但应用 broker URL 未一致使用密码，不能作为 Phase 0–6 的生产部署证据。
- 已将 `docker-compose.prod.yml` 改为独立、不可与开发文件叠加的 17 服务拓扑：4 个 Uvicorn worker、
  `research-io/cpu/llm` 三类 worker 和 `paper-analysis` 独立论文队列、Beat、Flower、PostgreSQL、authenticated Redis、Qdrant、MinIO、
  GROBID、ClamAV、Next.js，以及 config/migration/bucket/model 四个一次性启动门禁。
- 数据服务不发布主机端口；运行时容器无源码 bind mount。后端网络保留 scholarly/LLM 所需出站 HTTPS；
  模型初始化器使用 host network 和独立下载代理，API/worker 仅可选择性使用带内部 `NO_PROXY` 的
  `RUNTIME_HTTPS_PROXY`，避免把数据库、Redis、对象存储等内部流量送往代理。
- 新增 fail-closed 生产拓扑检查器，拒绝占位密钥、localhost/example 域名、未认证 Redis、单 worker/
  reload API、缺失服务、私有服务暴露端口、内部网络阻断外部源或模型下载代理泄漏。
- 使用独立项目 `academic_research_prodcheck`、独立卷和 58100/53100/55655 端口完成全新冷启动。首次
  冷缓存暴露 bridge 容器无法访问 `127.0.0.1:7890` 代理，修复后真实下载 embedding/CrossEncoder，
  输出 `research models ready`，并由门禁启动 CPU worker。
- 当时生产式栈为 `0041_research_orgs (head)`，4 个 API 子进程、3 个研究 worker/4 个资源队列和所有
  基础服务 healthy。当前开发部署 readiness=`ready/search_only=true/full_research=true`；项目级
  OpenAI-compatible 通道已用真实 Responses 请求验证，不再把 `configured` 误报成可执行。
- 最终镜像四用户双组织 E2E 再次通过。相同并发基线在 4-worker 拓扑下 backend authorized/denied
  P95 为 645.35/608.83 ms，BFF authorized/denied 为 629.43/609.13 ms，撤权后 denied 为
  259.52 ms；仍只是单机 localhost 授权端点基线，不是独立压测机或 20 篇成本结论。
- 继续核查发现多 Uvicorn worker 若未设置 `PROMETHEUS_MULTIPROC_DIR`，scrape 可能只命中一个随机
  进程。生产 app 现会在 master 启动前清理/创建独占 multiprocess 目录，instrumentator 使用
  `MultiProcessCollector` 聚合；生产拓扑门禁同时拒绝缺目录生命周期的多 worker 配置。
- 生产 `/metrics` 现强制使用独立 Bearer token，不能依赖未知的外部反向代理规则。隔离四 worker
  部署实测：未授权 scrape=401；256 个并发独立连接全部返回预期 401，授权 scrape 的
  `http_requests_total` 从 0 精确增加到 256；metric shard PID 为 `[10,11,12,13]`，证明四进程均写入且
  聚合无漏计/重复计数。

### 3.8 每篇论文持久化分析分片与真实 LLM 能力探测

- `ANALYZING` 协调器不再在单个 Celery task 内用内存 `asyncio.gather` 批处理全部论文；它先为每个
  Work 持久化一条 `ANALYZE_PAPER` execution，再以稳定 ID `research:{run}:analyze:{work}` 投递到
  `paper-analysis` 队列，单篇失败只重试该 shard。
- shard 输入哈希锁定 protocol/work、schema、prompt、model 版本；每篇分析独立事务提交并记录 attempt、
  outbox 和终态，worker 崩溃不会丢失已成功论文。
- PostgreSQL barrier 只有在 `SUCCEEDED + FAILED_TERMINAL + BLOCKED == total` 时才能推进
  `EVIDENCE_AUDITING`；pending/running shard 均阻止推进。回滚式部署验证以 3 个 shard 证明 pending
  会阻塞，终态为 `1 succeeded + 2 failed_terminal` 后才推进，验证事务最终回滚且数据库无残留。
- 新增 secret-safe `verify_llm_connectivity.py`（保留旧文件为兼容入口）。OpenAI 和 DeepSeek 使用
  无生成成本的 `models.retrieve`；只声明 Responses 兼容的第三方网关使用 16-token 上限的
  `responses.create` 并缓存 300 秒，只有凭据、网络和模型权限同时通过才开放 `full_research`。
- LLM provider 边界支持 `openai`、`deepseek`、`openai_compatible`。三者严格使用各自凭据；模型
  identity 对第三方 endpoint 做 SHA-256 指纹，防止更换网关后错误复用分析 shard/manifest。
- 当前机器 DNS 将第三方域名解析为 `0.0.0.0`，但容器到其公开 IP 的 TLS 1.3 直连正常；已增加只在
  本项目 Compose 生效的可选 `LLM_GATEWAY_HOST/IP` 映射。修复后真实请求返回
  `provider=openai_compatible/model=gpt-5.5/output=OK`，readiness 探针为 `responses.create/healthy`。
- 修复 API 重启后因未挂载 `models_cache` 而访问 Hugging Face 卡住的问题：开发研究 overlay 现在和
  生产拓扑一样挂载共享缓存并设置 `HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE`，实测重新启动后 ready=200。

### 3.9 真实 full-research 预验收暴露的问题

- 新增 `live_full_research_preflight.py`，创建一次性用户/项目并以目标 2 篇真实执行全流程，终态检查
  analyzed/evidence/六类 artifact，不允许把搜索成功当作 full-research 成功。
- 第一次运行发现生成 key 非空会错误触发官方 OpenAI embeddings；已增加独立
  `RESEARCH_EMBEDDING_PROVIDER=local|openai`，默认明确使用本地 `all-MiniLM-L6-v2`，不再根据
  `OPENAI_API_KEY` 猜测。修复后已越过相关性阶段，模型版本写入运行进度。
- 对当前第三方网关实测 `text-embedding-3-small` 得到 HTTP 404；该 key 当前只能证明 Responses
  能力，不能用于官方 OpenAI embeddings。官方 embeddings 需要官方 Platform key，或网关明确实现
  `/embeddings` 后另行接入。
- 第二次运行发现 arXiv planner 表达式被适配器重复加引号，API 实际收到 `all:""topic""`，导致
  100 条 arXiv 结果全部偏题；已改为逐个 Boolean term 生成一次 `all:"term"`，三类表达式有固定回归。
- Crossref/OpenAlex 的相关 PDF URL 在没有可核验 license/Unpaywall 依据时仍保持 fail-closed，没有
  为通过 E2E 而降低版权门槛。第三次运行在创建前触发正常 auth IP 限流，未清理限流键；等待窗口
  自然恢复后继续验证；后续已由 3.10 的 DOI 配额链路真实预验收取代该旧检索路径。

### 3.10 DOI 配额链路与真实 2 篇预验收

- 按用户确认的固定流程重构 discovery：每次运行只执行 1 次 Crossref 关键词搜索，规范化 DOI、去重并
  截断到 35 个；OpenAlex 只调用免费单篇 `/works/doi:{doi}`，不再做第二轮关键词搜索或 cursor 分页。
- 全文阶段只处理相关性通过的 DOI；每个唯一 DOI 最多调用 1 次 Unpaywall，再执行许可证判断、HTTPS/DNS
  pin、PDF 类型/主动内容校验、ClamAV 和对象存储。单篇 403、失效链接或错误 MIME 被隔离，不终止整批。
- 真实运行 `10bf4aa7-34a6-45f6-938e-15dc375a0fbc` 的 PostgreSQL 账本记录：Crossref 关键词搜索 1 次、
  DOI 候选 35、OpenAlex 精确查询 35、精确匹配 34、Unpaywall 唯一 DOI 查询 15；得到 33 个规范候选、
  15 个相关候选、6 篇解析通过并只创建目标数 2 个分析 shard。
- 两个 shard 中 1 个成功、1 个因模型生成白名单外 evidence ID 被严格终止；成功结果进入综合并以
  `PARTIALLY_COMPLETED` 发布。最终严格论文 1 篇、EvidenceLocator 1,653 条、artifact 6 个，格式为
  Markdown、OPML、BibTeX、JSONL、CSV、manifest。
- 发布快照为 `allowed=true/partial=true/blockers=[]`，最低相关性约 0.9894、evidence coverage=1.0、
  figure audit failure=0。修复了旧门禁把未进入报告候选纳入最低相关性/图表统计的范围错误；图表规则按
  方案要求只保证入选论文的核心 hash-bound artifact，不机械要求每个装饰 caption 都成功裁图。
- section、figure、audit 现在都会在确定性 evidence boundary 失败时，将验证错误反馈给同一专家并只做
  一次字段级返工；仍失败才交给论文 shard 重试/终态。prompt 版本升级为 `2026-08-22.2`，避免旧输出复用。
- analysis barrier 完成时会同步刷新新旧 shard 计数键，避免运行进度保留创建时的 0 值。

### 3.11 LLM token/cost 用量账本与协议预算门禁

- 新增 `LLMBudgetPolicy`，将每个有界 LLM 操作（相关性批次、单篇分析或综合）的 `max_requests`、输入 token、输出 token、总 token
  和可选美元成本硬上限写入待批准协议；任一预算变化都会改变协议哈希，运行后不能静默修改。
- 每个结构化专家调用同时使用 PydanticAI provider-side `UsageLimits` 和本地累计门禁；section、figure、
  audit 的定向返工也计入同一次操作，超过已批准请求/token 上限会明确失败，不能继续消耗后伪报成功。
- provider 返回的 `requests/input/output/cache/details` 按 expert 名称记录。单篇 shard 成功时写入
  `ResearchTaskExecution.output_json`；失败和重试写入 `error_json.attempt_history`，因此失败尝试不会被
  后续成功覆盖或从费用审计中消失。
- PostgreSQL analysis barrier 汇总全部成功/失败尝试到 `run.progress.analysis_llm_usage`；相关性和综合
  阶段分别写入 `relevance_llm_usage`、`synthesis_llm_usage`；最终 `run_manifest.json` 保存三者总计与
  `by_agent` 分解。前端运行页显示请求数、
  输入/输出/总 token 和成本状态。
- 第三方 OpenAI-compatible 网关若没有返回可靠价格，成本明确记为 `cost_status=UNAVAILABLE` 且
  `cost_usd=null`，不会伪造 `$0`。用户一旦设置美元硬上限，网关不提供成本即 fail-closed；不设置美元
  上限时仍完整记录 token，可在网关账单侧对账。
- 新建协议页已增加预算输入和批准前回显。后端新增 4 个专门回归，覆盖按专家累计、token 越界、成本
  缺失 fail-closed 和失败重试聚合；本轮没有启动新的 35 DOI/论文分析运行，避免为验证账本额外消耗额度。
- 最终镜像已重新构建并部署 API、三类研究 worker 和前端。readiness 仍为
  `ready/search_only=true/full_research=true`，三 worker/四队列、PostgreSQL、Redis、Qdrant、MinIO、
  GROBID、ClamAV 均健康；新建调研页面在端口 53000 可访问。

### 3.12 Phase 4 证据化 LLM Facet 判定从定义变为真实工作流

- 继续按方案原文反查发现：旧实现虽然定义了 `relevance` 结构化专家和 `FacetJudgement` Schema，正式
  `RELEVANCE_SCORING` 却只执行 lexical、local embedding 与 CrossEncoder，专家从未被调用。这属于
  “代码结构存在、第三阶段没有落地”的真实缺口，不能以六专家类已声明冒充 Phase 4 完成。
- `full_research` 现在在本地 A/B 阶段通过后执行证据化阶段 C；按最多 10 篇一批调用 relevance 专家，
  输入只含协议 facet 和各论文自己的 title/abstract 元数据证据。`search_only` 有固定回归保证不构造、
  不调用 LLM，继续保持零生成调用。
- 输出必须恰好覆盖批次内每个 `work_id` 和协议指定的全部 must/should facet；`SUPPORTED` 必须引用该篇
  论文自身的 metadata evidence ID，禁止遗漏、重复、伪造 ID 或跨论文移动证据。边界失败只允许同批次
  一次定向返工，二次失败即拒绝阶段结果。
- 最终严格判定按方案门禁执行：must-have `NOT_SUPPORTED` 为 FAIL，`UNCERTAIN` 为 REVIEW，触发排除
  为 FAIL，`centrality != CENTRAL` 为 FAIL，整体分数低于协议 facet 加权门槛为 FAIL；只有全部满足才
  PASS。不会用模型分数覆盖或伪造本地 lexical/embedding/CrossEncoder 分数。
- 新增迁移 `0042_relevance_facets`，完整 judgement、rationale 和 evidence ID 写入
  `research_relevance_scores.facet_judgement_json`；候选 API 和论文详情抽屉可查看逐 facet 状态、中心性、
  分数、理由与证据 ID。
- `RELEVANCE_SCORING` 改由 `research-llm` 预算队列执行；请求/token/cost 纳入
  `relevance_llm_usage`、运行审计卡和最终 manifest。相关性模型 identity 与 prompt
  `2026-08-22.1` 一并持久化。
- 修正预算循环语义：PydanticAI provider-side usage limit 会转换为领域
  `ResearchLLMBudgetExceeded`；阶段或单篇 shard 一旦超出批准预算，立即以
  `FAILED_TERMINAL/LLM_BUDGET_EXCEEDED` 结束，不再作为可重试错误重复消耗额度。
- 自动验证新增 11 个回归，覆盖 10+1 分批、论文内证据隔离、一次返工、四种严格判定、search-only
  零 LLM、full-research 入账、LLM 队列和预算终止。数据库已迁移至 0042，最终镜像已部署；readiness
  为 `ready/search_only=true/full_research=true`。为控制额度，本轮只执行 16-token 能力探针，没有启动
  新研究运行或真实 facet 批次；真实输出质量仍留待轮换 key 后的 3 篇验收。

### 3.13 Phase 0 协议草案专家从定义变为可选、预算受控工作流

- 继续反查六专家的正式调用点后确认：`protocol` 专家此前只有 Schema、prompt 和类定义，没有服务、API
  或 UI 入口，属于“已声明但未落地”。现已新增显式付费入口
  `POST /api/v1/research/projects/{project_id}/protocols:advise-and-compile`；原
  `protocols:compile` 保持纯确定性且零 LLM 调用，用户可以自主选择是否消耗额度。
- 协议专家只接收课题和已有语义字段，只能返回 `topic_definition`、`research_questions`、
  `must_have_facets` 和歧义；Pydantic Schema 强制 `approval_requested=false`。用户已显式填写的语义字段
  优先，日期、时区、文献类型、来源、质量约束、目标数量、输出规则和 LLM 预算不会接受模型覆盖。
- 建议结果仍进入原确定性编译器。模型报告的每个歧义都转换为阻塞 `ProtocolIssue`，协议状态保持
  `NEEDS_CLARIFICATION`，不能批准；无歧义时也只保存 `DRAFT`，仍须用户以协议哈希明确批准。
- provider、endpoint 指纹化 model identity、prompt `2026-08-22.1`、输出 Schema 版本和完整 token/cost
  用量写入 `draft_advice_provenance`，并纳入不可变协议哈希。建议调用受协议中的 provider-side 和本地
  LLM 预算双门禁保护，超额同步返回 HTTP 429 / `LLM_BUDGET_EXCEEDED`，不会自动重试形成额度循环。
- 新建调研页现有两个明确分离的按钮：“确定性编译（不调用 LLM）”与“AI 建议并编译（会调用 LLM）”；
  批准前会展示研究问题、必备 facet、模型/prompt 来源及本次用量，用户可返回修改并生成新版本。
- 新增 3 个回归，覆盖 provenance/歧义进入哈希并阻塞、模型只补空缺语义字段且硬字段不变、预算超限映射
  429。相关 OpenAPI、预算、relevance 定向回归合计 32 项通过；Ruff 0 error，TypeScript 通过，前端
  30 项测试通过，ESLint 0 error/29 个既有 warning，Next.js production build 成功。
- 已重建并部署 API、`research-io`、`research-cpu`、`research-llm` 和前端；新路由在运行容器内确认注册，
  API 与前端 healthy。为控制额度且鉴于旧 key 必须轮换，本轮没有调用真实协议 LLM，真实输出质量留待
  key 轮换后的人工触发验收。

### 3.14 Phase 6 记忆与反馈从“只写入”变为真实读取闭环

- 生产调用链审计确认：此前 `resolve_memory_context()` 和 Qdrant 项目记忆检索虽已定义，但协议生成没有
  调用它们；反馈能够写 PostgreSQL/Qdrant，却不会影响后续草案，因此不能把原状态称为完整闭环。
- 新增协议记忆上下文服务：先按组织/项目隔离在 Qdrant 语义检索，再回 PostgreSQL 校验记录归属与有效期，
  同时合并最近项目记忆以覆盖异步索引延迟；Qdrant 异常时显式降级为 PostgreSQL 最近记录，而不是失败重试。
- 后续 AI 协议建议现会读取最新确认的 L2 用户画像、L3 项目纠错记忆、当前有效的 L4 策略版本和最后一个
  已批准协议，并按“当前输入 > 已批准协议 > 项目记忆 > 用户画像 > 策略 > 默认值”解析。确定性编译入口仍
  保持零 LLM、零记忆依赖。
- `draft_advice_provenance` 新增检索模式、记忆 ID、画像/策略版本与哈希、已批准协议哈希、被忽略字段及降级
  错误类型；完整记忆文本不写 provenance。所有影响草案的来源继续进入不可变协议哈希。
- 记忆在进入 prompt 前递归剔除硬约束/审批语义与疑似凭据字段；项目记忆、画像和策略写入端也递归拒绝这些
  字段，确保记忆不能放宽日期、来源、质量、预算、批准和发布边界。
- 论文详情页新增“标记核心相关/标记排除”，反馈会带标题、规范标题、DOI、arXiv ID 等论文身份写为项目
  纠错记忆并排队索引；它只影响未来建议，不会篡改当前已批准协议。
- 新增/更新定向回归覆盖语义检索、PostgreSQL 回退、优先级、隔离、递归过滤、身份化反馈和 provenance；
  后端 27 项通过，Ruff 0 error，TypeScript 通过，前端 30 项通过，ESLint 0 error/29 个既有 warning，
  Next.js production build 成功。
- 新镜像已部署；调研创建页 HTTP 200，未认证反馈与 AI 建议入口均返回 401。为控制额度，本轮未触发 GPT，
  也未写入伪造演示数据。

### 3.15 Phase 3 指标来源管理从后端接口补齐为可操作界面

- 对照方案“管理后台查看指标来源”核查发现，管理员专用的授权 CSV 导入、PostgreSQL 快照、MinIO 原文件、
  有效期、许可声明和 SHA-256 已真实实现，但前端没有入口，属于后端完成、交付面未完成。
- 新增 `/admin/research-metrics` 管理页和 Admin 导航入口；管理员可以上传最大 20 MiB 的 CSV，填写来源名、
  版本、有效期、许可引用与授权范围，并必须显式确认使用权。页面明确列出 CSV 必需/可选列，禁止暗示抓取
  非官方指标站点。
- 来源审计卡展示状态、指标名、有效窗口、许可引用、授权范围、导入时间、payload SHA-256 和私有对象键；
  空状态明确说明缺失指标为 `UNKNOWN`，不得进入严格结果集。
- 通用浏览器 API client 现原生透传 `FormData`，不会错误地把 multipart 内容 JSON 序列化；Next.js BFF 继续
  只从 HttpOnly cookie 取访问令牌并原样转发 multipart boundary。后端对无效 CSV/许可输入由模糊 500 改为
  明确 422，未认证和非管理员仍由原依赖门禁拒绝。
- Ruff 通过；指标解析测试前 2 个纯用例通过，涉及对象存储的后续用例在宿主沙箱既有线程限制处停住，未
  反复重跑或虚报整组通过。生产镜像构建完成 TypeScript、ESLint 和 Next.js 静态生成，新页面在 120 个页面
  清单中；部署后页面 HTTP 200，后端与 BFF 未认证接口均为 401，API/前端 healthy、三个研究 worker 正常。
- 本轮没有导入 mock 指标，也没有调用 GPT。真实 JCR/CAS/会议许可快照仍须由用户或机构合法提供。

### 3.16 Phase 5 manifest 从空 provenance 字段变为发布门禁

- 生产装配核查确认：`RunManifest` 和 `ArtifactService` 虽声明了 `source_snapshot_hashes` 与
  `metric_snapshot_ids`，但 `ResearchPipelineStages.render_generation()` 从未传入，两项在实际导出中会
  永远落为空数组；原测试只证明渲染器能生成 manifest，没有覆盖生产装配，属于真实壳层缺口。
- discovery repository 新增按 run 查询 `research_source_pages.raw_sha256`，得到实际不可变原始响应对象哈希；
  quality repository 新增从 `research_constraint_evaluations` 查询非空 `metric_snapshot_id`，得到本次约束账本
  真正引用过的授权指标快照。两者均在 SQL 层去重、排序。
- `render_generation()` 现显式收集并传递两组 provenance；`ArtifactService.render_all()` 将其改为必填参数，
  再排序去重后写入 manifest。源快照哈希元素新增 64 位小写 SHA-256 Schema 校验，非法值不能生成工件。
- 持久化工件审计在发布前重新从 PostgreSQL 查询期望 provenance，并与回读的 `run_manifest.json` 逐项比较；
  任一来源哈希或指标快照 ID 缺失/多出/错序都会产生 artifact validation error，继而以
  `ARTIFACT_INVALID` 阻止发布，而不是只记录日志。
- 新增生产调用链回归并将 artifact 测试改为无宿主线程依赖：artifact/manifest/发布门禁 11 项、评测/质量
  9 项、OpenAPI 6 项，共 26 项通过；全后端 Ruff 通过。首次镜像构建因 GHCR 匿名 token EOF 未进入项目
  编译，按预定上限唯一重试后成功。
- 后端镜像已切换，API 与三个研究 worker 已重建；未认证工件 API 返回 401。本轮没有调用 GPT，也没有改写
  旧 generation；既有导出只有在用户显式重新生成新 generation 后才会获得新的 provenance 门禁结果。

### 3.17 Phase 6 从 API-only 补齐为用户可操作的反馈与评测闭环

- 继续核查发现：L1 会话记忆、L2 用户画像、L3 项目记忆、版本化 gold 数据集和运行评测均有后端 API，
  但除单篇相关性反馈和结果看板外没有前端调用；用户无法从研究工作台确认画像、查看记忆、创建 gold 或
  触发评测，Redis L1 也完全不参与新建协议页，属于“后端存在但产品链未落地”。
- 新增 `/research/governance`：用户可查看当前画像版本，以通用 JSON 确认偏好并生成不可变新版本；可按当前
  个人/组织空间选择项目，查看 PostgreSQL 中当前有效记忆，新增查询词、纳排纠错、展示偏好或工件备注，
  保存后进入现有项目隔离 Qdrant 异步索引队列。
- 页面明确展示记忆安全边界；后端现有递归校验继续拒绝 credential-shaped 字段以及 constraints、time_scope、
  quantity_policy、quality_floor、approved_protocol_hash，UI 不提供任何“从记忆批准协议”的操作。
- 新建协议页现为每个浏览器草案生成随机 L1 session UUID，先按用户命名空间从 Redis 恢复，再对全部协议
  输入做 800ms 防抖保存，TTL 为 24 小时；若协议编译已创建项目后失败，刷新会复用同一可访问项目，避免
  重复创建。L1 payload 不含批准状态；批准并成功启动 run 后清除本地 session ID。
- 运行页新增 gold 数据集控制面：可以创建不可变 `DRAFT/ADJUDICATED/EXTERNAL_BENCHMARK` 版本、查看
  状态/hash/样本量，并选择已裁决或外部 benchmark 执行评测。DRAFT 不能评测；非 DRAFT 必须通过后端
  provenance 规则，ADJUDICATED 至少两名标注者，页面明确禁止把合成样本冒充真实 gold。
- TypeScript 0 error；前端 6 文件/30 项测试通过；Prettier 通过；ESLint 0 error/29 个模板既有 warning；
  Next.js production build 成功并生成 122 个页面。新镜像已部署，`/research/governance` 与
  `/research/new` 均 HTTP 200，画像与 L1 API 未认证均 401，API/前端 healthy、三个研究 worker 正常。
- 本轮没有调用 GPT，也没有写入画像、记忆或 gold 演示数据。真实画像内容和人工 gold 仍必须由用户明确
  提交，不能由部署脚本代填。

### 3.18 Phase 6 L4 策略版本从下游过滤补齐为写入门禁和管理员界面

- L4 复核发现：协议建议服务会在进入 prompt 前剔除策略中的硬语义，但 `PolicyVersionCreate` 写入 Schema
  只拒绝凭据字段，管理员仍可把无效的 constraints/time_scope 等内容存入 PostgreSQL；同时策略创建与列表
  只有 API，没有管理员操作入口。
- 策略写入端现与项目记忆使用同一递归硬语义边界：任何层级的 constraints、time_scope、quantity_policy、
  quality_floor、approved_protocol_hash 均返回 422；api_key/password/cookie/secret/token/credential 等字段仍
  被递归拒绝。下游 prompt 清洗继续保留，形成写入端和消费端双门禁。
- 新增 `/admin/research-policies` 和 Admin 导航标签；应用管理员可以创建不可变 policy key/version，填写通用
  strategy JSON 与有效起止时间，并查看全部历史版本的状态、内容、SHA-256 和创建时间。页面没有覆盖、删除
  或协议批准操作；当前有效版本仍由 PostgreSQL 按 key、有效期和最高版本确定。
- Memory resolver/protocol context 定向回归 9 项、OpenAPI 6 项通过，全后端 Ruff 通过；TypeScript 与定向
  ESLint 通过；Next.js production build 成功并生成 124 个页面，仍只有 29 个模板既有 warning。
- API、三个研究 worker 和前端已用最终镜像重建；管理员策略页 HTTP 200，策略 API 未认证为 401，API/前端
  healthy。本轮没有调用 GPT，也没有创建演示策略版本。

### 3.19 Phase 6 部署态零 GPT 闭环与写事务可见性修复

- 新增 `backend/scripts/live_research_phase6_e2e.py`，不使用 mock server，也不调用 GPT：脚本在运行中的
  `app` 容器内创建唯一临时身份，通过真实 API 和 Next.js BFF 验证后，在 `finally` 中按本次 UUID 清理
  PostgreSQL、Redis 和 Qdrant fixture；两次失败路径和最终成功路径均输出 `fixtures_removed=true`。
- 首轮真实调用暴露出协议写接口的部署竞态：FastAPI yield dependency 的隐式 commit 可能在成功响应对客户端
  可见后才执行，导致“compile 返回 200 后立即 approve”偶发 404。协议 compile/advise/approve、画像确认、
  策略创建、gold 数据集创建和评测持久化现均在返回成功响应前显式 commit；新增 3 项路由级回归覆盖 7 个写入口。
- 定向验证共 17 项通过，Ruff 0 error。后端镜像重新构建，API 与 `research-io/research-cpu/research-llm`
  三个 worker 均用新镜像重建；不是仅依赖宿主 bind mount 的临时修补。
- 新镜像上的最终部署 E2E 已通过：`validate_only` 到 `COMPLETED`；前端 BFF 写入 Redis L1，TTL 86400 秒且
  跨用户不泄漏；PostgreSQL L2 记忆由真实 Celery CPU worker 写入项目隔离 Qdrant collection；生产
  `ResearchProtocolMemoryContextService` 返回 `semantic_plus_recent`，命中 L2 memory、最新 L3 profile v2、
  L4 policy v1，并保留 approved protocol 的更高优先级。
- 同一 E2E 证明普通用户不能创建 L4 policy、凭据/硬语义递归写入返回 422、外部用户访问项目记忆和 gold
  返回 404、DRAFT gold 评测返回 409、ADJUDICATED 要求至少两名标注者；1 条合成控制样本的不足指标保持
  `NOT_EVALUATED`，没有被包装为质量通过。该样本明确标记为 deployment fixture，测试结束已删除。
- 本轮没有调用 readiness 或任何模型 API，因此 GPT token 消耗为 0；最终输出为
  `phase6_deployed_e2e_ok`，清理后的反查同时证明 `postgresql_removed=true`、`redis_removed=true`、
  `qdrant_removed=true`。

### 3.20 正式数据准备材料与最小真实 GPT 连通性

- 新增 `evaluation/DATA_PREPARATION_GUIDE.md` 和
  `evaluation/templates/venue_metrics_snapshot_template.csv`，把授权 JCR/CAS/CCF/CORE 快照、双人独立
  Gold、第三人裁决以及 CORD-19/TREC-COVID Round 5 配对过程落实为可执行步骤；现有 Gold 模板继续只以
  `DRAFT` 起步，不能把占位内容冒充正式数据。
- 修正 Gold 生命周期说明：数据集是不可变版本，完成裁决后必须创建带新版本号和 provenance 的
  `ADJUDICATED` 数据集，不能原地修改 DRAFT。
- CORD-19 指南固定使用 2020-07-16 metadata 与 TREC-COVID Complete Round 5 最终累计 qrels，记录官方
  SHA-1、许可和 mapping report；明确禁止混用 2022 最终 metadata 或 chronological qrels。
- 经用户明确允许小额真实调用后，只执行一次 `verify_llm_connectivity.py` 最小探针：provider 为
  `openai_compatible`，请求/返回模型 `gpt-5.5`，`max_output_tokens=16`、`store=false`，返回 `OK`；没有启动
  readiness 或论文分析。该结果证明当前 key 可用，不改变“已暴露 key 正式运行前应轮换”的安全结论。

### 3.21 Phase 3/5 从快照 ID 和通用 CSV 补齐为逐事实审计产物

- Alembic `0043_metric_fact_provenance` 将 venue metric fact 的 `metric_year` 提升为数据库非空约束；迁移
  若发现历史空年份会明确失败，不允许猜年份。当前数据库迁移前反查空年份为 0，升级后 schema 反查为
  `is_nullable=NO`。
- 每项 venue 约束账本现同时保存 `metric_snapshot_id`、精确 `metric_fact_id` 和 `metric_year`；查询只采用
  不晚于 run `as_of_date` 的事实，并按有效窗口、导入时间和年度确定性选取。
- Phase 5 必需产物由 6 个补齐为 8 个：新增 `exclusions.csv` 和
  `venue_metrics_snapshot.csv`。前者解释每个未入选 canonical work 的第一失败边界，后者只导出本次 run
  真正引用过的授权指标事实、年份、许可、哈希和 evidence reference。
- 发布前不只验证文件存在和自身哈希，还从 PostgreSQL 权威约束/相关性/全文/解析/分析账本重新计算两个
  CSV 并逐字节比较；旧内容、漏项、额外项或错年份均以 `ARTIFACT_INVALID` 阻止发布。
- 指标导入写接口与其他写入口一样在 201 返回前显式 commit，避免上传成功后立即查询偶发不可见。

### 3.22 Phase 2 文献类型与 facets 从“协议中保存”修复为实际执行

- 核查确认原实现存在两个真实缺口：Crossref wire request 只带日期、不带 `publication_types`；未知
  Crossref/OpenAlex 类型被默认映射为 `journal_article`。同时 QueryPlanner 虽记录 `facet_coverage`，但
  `query.bibliographic` 只有宽泛 topic。
- Crossref 现按官方同名 filter 的 OR 语义，在唯一一次请求中发送
  `type:journal-article,type:proceedings-article` 等协议映射，并对响应再次执行本地 allowlist；OpenAlex 搜索和
  DOI singleton 补全也执行 source-native 类型门禁。图书章节、标准、学位论文等有显式映射，其他值保持
  `DocumentType.UNKNOWN`，且协议禁止把 UNKNOWN 声明为允许类型，最终硬约束 fail-closed。
- QueryPlanner 现把 must/should facet 名称与描述、synonym concept/terms 组成去重、引号化、最长 1000 字符
  的单次 Crossref bibliographic query；排除 facet 因 Crossref 没有受支持的负查询合同，继续在后置相关性
  门禁执行并在 query provenance 中记录，未被错误发送成正关键词。
- 新增 `backend/scripts/verify_discovery_contract.py`。新容器部署态输出
  `crossref_keyword_searches=1`、`candidate_limit=35`、两个原生类型 filter、facet coverage，以及
  Crossref/OpenAlex 越界类型接收数均为 0、未知规范类型为 `unknown`。
- 全后端测试在显式 `--network none` 容器中完成：539 passed、4 skipped；因此回归没有调用 GPT 或外部
  学术 API。Ruff 0 error；前端 production build/TypeScript 通过并生成 124 个页面。数据库已到
  `0043_metric_fact_provenance (head)`；API、前端、三个研究 worker、beat 和 Flower 均用新镜像重建。
- 重建后再次执行 Phase 6 零 GPT 部署 E2E，API+BFF/Redis/PostgreSQL/Celery/Qdrant 全链路通过，最终
  `postgresql_removed=true`、`redis_removed=true`、`qdrant_removed=true`，没有遗留测试 fixture。

### 3.23 Phase 3/5 八产物在真实 PostgreSQL/MinIO 上闭环

- 新增 `backend/scripts/live_research_artifact_audit_e2e.py`。脚本在单个 PostgreSQL 事务中创建一篇严格
  入选论文、一篇硬约束排除论文，以及一条明确标注为 E2E 合成数据的授权年度指标 fact；全过程不调用
  检索 API 或 LLM，不能被当作正式 JIF/CAS 数据。
- 脚本通过生产 `ArtifactService` 向当前 MinIO 写入 Markdown、OPML、BibTeX、JSONL、papers CSV、
  exclusions CSV、venue metrics CSV 和 manifest 共 8 个对象，再逐个回读、验证哈希和 schema，并从
  PostgreSQL 权威账本重算两个审计 CSV。实测保留排除原因 `COMPARISON_FAILED` 和指标
  `metric_fact_id/metric_year=2025`。
- 首轮真实验收发现 `list_artifacts(released_only=False, generation=None)` 仍错误要求 release check，导致
  发布门禁无法审计刚生成但尚未发布的 generation。仓储现仅在 `released_only=True` 时应用 release check，
  并新增回归测试，避免单元 mock 再次掩盖该生产分支。
- 部署态篡改测试将数据库 ledger 的指标 observed value 从 8.2 改为 9.1，旧
  `venue_metrics_snapshot.csv` 被明确拒绝为 `authoritative ledger mismatch`，证明不是只校验文件自哈希。
  `finally` 回滚事务并按精确 key 删除对象；结束反查 `postgresql_fixture_roots=0`、`minio_objects=0`。
- 全后端套件再次在 `--network none` 临时容器中运行：540 passed、4 skipped、223 warnings；Ruff 0 error。
  后端镜像已重建，API、三个研究 worker、Beat、Flower 均由新镜像重建；新镜像再次输出
  `phase35_artifact_audit_e2e_ok`。API/前端 healthy，readiness 为
  `search_only=true/full_research=true`，3 个 worker 覆盖 4 个研究队列。

### 3.24 轻量 search-only 论文检索、严格排序与四格式导出

- 原 `search_only` 虽然会跳过 PDF/LLM，却错误以“已获取并解析全文”为最终选择前提，并在选择后直接
  结束，因此不能导出一批纯元数据高相关论文。现仅在该执行模式下改为从 `eligible=True` 且
  relevance `PASS` 的首选 WorkVersion 选择，按 CrossEncoder → embedding → lexical 的稳定次序取
  `target_count`；full-research 仍沿用原有全文/证据选择逻辑。
- 最终选择以 `catalog_selection`（rank、work_id、version_id、score）冻结在运行的 PostgreSQL
  `progress_json` 中；不引入新表或迁移。状态流从 `SELECTING` 转入 `RENDERING`，然后根据严格数量终态为
  `COMPLETED` 或 `PARTIALLY_COMPLETED`，仍不自动放宽约束。
- 新增独立 `CatalogArtifactService` 和 `CatalogResearchReport`，不修改 full-research 的 8 产物、manifest、
  证据审计或发布门禁。它只持久化并回读 `research_catalog.md`、`papers.csv`、`references.bib`、
  `research_catalog.opml`；所有 Markdown/OPML 均明确声明仅含元数据、来源、硬约束和相关性排序，未获取
  PDF、未做证据/图表/深度分析。
- 已结束的 search-only run 允许通过现有 artifact API 下载这四个不可变文件，但 full-research 仍必须通过
  原 release check；深度分析重生成接口仍拒绝 search-only。新建页将模式标为“检索、严格筛选、排序与导出”，
  运行页显示 metadata-only 边界。
- 新增 `backend/scripts/live_search_only_catalog_e2e.py`。最终新镜像部署态以三条合成但真实入库的严格候选
  执行真实 `SELECTING → RENDERING → COMPLETED`，选中 Top-2，写入/回读 MinIO 的四种文件，确认
  `llm_calls=0`、PDF/解析/分析行数为 0；`finally` 后 PostgreSQL fixture 根和 MinIO 对象均为 0。该脚本
  不伪装为公开源或正式指标验收。
- 全后端回归在 `--network none` 容器中通过：544 passed、4 skipped、223 warnings；Ruff 0 error。前端
  Docker production build/TypeScript 通过，生成 124 页，仅保留既有 29 个 lint warning。后端、前端及
  三个研究 worker 已以新镜像替换，运行服务 healthy。

### 3.25 桌面端论文检索入口可见性修复

- 现场验收发现，虽然 `/research`、`/research/new` 页面已部署，桌面 Header 的导航数组仅包含“仪表盘”和
  “聊天”；`Research` 只存在于窄屏侧栏抽屉，导致桌面用户无法从已登录主页发现论文检索工作台。
- 已将 `Research`（`LibraryBig` 图标、`ROUTES.RESEARCH`）加入桌面 Header 的常驻导航。生产前端镜像已
  重建，容器为 healthy，`/research` 返回 HTTP 200；登录后点击该入口，再点击“新建调研”即可进入
  `search_only` 表单。
- 论文调研项目、草案与运行 API 采用用户令牌隔离。未登录或浏览器持有过期 refresh token 时，受保护接口会
  正确返回 401；用户应先注册/登录（必要时清除该站点旧 Cookie）再使用检索工作台。该限制与通用 Chatbot
  的在线状态相互独立。

### 3.26 HTTP 局域网访问的 UUID、认证预检与 Chat WebSocket 修复

- 浏览器 Console 已确认研究新建页的实际崩溃为 `TypeError: crypto.randomUUID is not a function`，而不是
  翻译或后端 500。以 `http://LAN-IP` 打开时，部分浏览器不提供 secure-context 专属的
  `crypto.randomUUID()`；原页面在创建本地草案 session 时未做能力检测，因此触发 Dashboard error boundary。
- 新增 `frontend/src/lib/client-id.ts`：优先使用 `crypto.randomUUID`，否则以 `getRandomValues` 或兼容随机
  bytes 生成 RFC 4122 格式的不透明 client id。新建调研、启动运行、反馈重分析、产物重生成和手工记忆均改用
  该 helper，HTTP 局域网模式不再因 UUID API 缺失而崩溃。
- `AuthGuard` 现始终先通过 `/api/auth/me` 验证 HTTP-only Cookie 后再挂载受保护页面，不再信任 localStorage
  中可能过期的 `isAuthenticated`，避免失效会话抢先触发 projects/runs/organizations 的 401 请求风暴。
- Console 中 Chat 的 `ws://localhost:58000` 失败是浏览器将 localhost 解释为客户端机器所致；当前局域网
  部署已将构建期 `NEXT_PUBLIC_WS_URL` 更新为 `ws://192.168.31.145:58000`。生产前端重建后确认该地址和
  client-id fallback 均存在于静态 bundle，容器 healthy，`/research/new` 返回 HTTP 200。

### 3.27 登录 429 被误渲染为 React 500 的根因审计与修复

- 用户提供的 `/login` Console 显示了确定因果链：认证 API 达到 429 → FastAPI 返回
  `detail.error.message` 嵌套对象 → 前端 `ApiError.message` 把对象透传到 LoginForm state → JSX 尝试渲染
  `{error}` 对象 → `Minified React error #31` → 页面错误边界显示 500。它不是后端 500，也不是翻译问题。
- 认证默认限流为每 IP 5 次/15 分钟；现场 Redis 确认已满的键是
  `rl:auth:ip:172.26.0.14`，即 Next.js 前端容器 IP。旧 BFF 不转发客户端地址，导致所有经前端登录的用户
  共享该计数桶，反复点击登录/重试会共同触发 429。
- `apiErrorMessage`/`ApiError` 现递归把 string、FastAPI validation array 和
  `detail.error.message` 归一为 string；Login、Register、上传与所有复用 `ApiError` 的 UI 不会再把任意 JSON
  对象交给 React 渲染。新增前端回归用例覆盖限流错误对象及 malformed payload fallback。
- 后端新增 `TRUSTED_PROXY_CIDRS`；仅当直接 peer 位于明确配置的 Docker CIDR 时才接受 BFF 传来的
  `X-Forwarded-For`，其他直连客户端无法伪造限流身份。当前部署只信任 `172.16.0.0/12`；BFF 已在登录/注册
  转发客户端 IP。现场用保留测试 IP 的无效注册请求得到预期 422，确认创建独立限流键后立刻删除该测试键。
- 新 API 和前端容器均已以新镜像重建、healthy；`/api/v1/health`、`/login`、`/research/new` 均为 HTTP 200。
  无网络临时容器内 Ruff 0 error，认证/限流/可信代理定向回归 26 passed。浏览器 `content_main.js` 为扩展异常，
  favicon 404 为非阻断资源，均不参与上述 500 因果链。

## 4. 方案最终 12 条验收矩阵

状态定义：`通过`=已有真实部署或足够强的自动验收证据；`部分通过`=代码已实现但缺生产数据、凭据
或人工样本；`未通过`=尚无可接受证据。

| # | 最终验收要求 | 状态 | 证据或缺口 |
|---:|---|---|---|
| 1 | 任意课题生成可确认协议版本 | 通过 | 确定性编译和可选 LLM 建议、ambiguity、哈希、approve API 与前端流程已测试/部署；新镜像实测 compile 后立即 approve 无事务竞态 |
| 2 | 日期、venue、JIF、分区、相关性、数量语义分离 | 通过 | 独立 schema/ledger/constraint engine；会议不套 JIF；UNKNOWN fail-closed |
| 3 | 每个候选有来源、去重簇、硬约束和相关性判定 | 通过 | 最新配额 E2E：1 次 Crossref、35 DOI、35 次 OpenAlex 精确查询、33 Work；新部署验证证明 facets/同义词进入 wire query、源端类型双门禁和未知类型 fail-closed |
| 4 | 严格不足时不自动降质 | 通过 | 状态机 shortfall 与 release gate；固定失败用例覆盖 |
| 5 | 每篇最终论文八类分析均有论文内证据 | 部分通过 | 六专家、evidence 白名单、审计和真实 GPT 通道均完成；缺目标论文集的真实 20 篇 E2E 与人工逐篇核验 |
| 6 | 图表结果定位到页码、编号和原文件哈希 | 部分通过 | bbox/crop/table/numeric 容器实测通过；尚未对目标 20 篇真实论文人工逐图核验 |
| 7 | 预印本与正式版不重复/混淆 | 通过 | Work/Version 模型、DOI/标题作者归并、冲突 REVIEW 和测试覆盖 |
| 8 | 暂停、取消、重试、断点恢复、事件重放 | 通过 | 控制面真实 E2E + watchdog/worker outage + 连续序列/后缀重放 |
| 9 | Markdown/OPML/BibTeX/manifest 稳定可重现 | 部分通过 | 8 个必需产物已在真实 PostgreSQL/MinIO 完成生成、回读、权威账本重算、篡改阻断和零残留 E2E；仍缺生产 20 篇 full-research 产物人工导入验收 |
| 10 | 检索、相关性、元数据、证据、端到端均有人工 gold | 部分通过 | 版本/哈希/provenance/裁决人数/DRAFT fail-closed 已做部署 E2E，TREC-COVID 外部人工检索基准链路完成；跨领域/中文、证据、数值的双人裁决集尚缺 |
| 11 | 用户、项目、组织、对象存储、向量检索隔离 | 通过 | 真实组织/成员模型、活动上下文、同步撤权、四用户双组织 API+BFF E2E、对象 key 与 Qdrant tenant/project/run 过滤均通过 |
| 12 | 领域代码独立并可三方合并升级模板 | 部分通过 | 代码集中于独立命名空间；上游 main 仍等于冻结 commit `3428d9a`，当前没有更新 commit 可做真实三方合并 |

结论：7 条通过，5 条部分通过，0 条被无证据标成通过。剩余 5 条需要 20 篇真实论文运行、许可指标、
人工裁决数据或新的模板上游 commit，原方案的最终迁移目标当前尚未完全达成。

## 5. 当前部署与质量门禁

### 5.1 数据库与运行服务

- Alembic：`0043_metric_fact_provenance (head)`；venue metric fact 的 `metric_year` 为数据库级 NOT NULL。
- API：healthy，端口 58000。
- 前端：healthy，端口 53000。
- PostgreSQL、Redis、Qdrant、MinIO、GROBID、ClamAV：恢复后均 healthy。
- `research-io`、`research-cpu`、`research-llm`：3 个 worker；连同 `paper-analysis` 共 4 个队列 healthy。
- readiness：`status=ready`、`search_only=true`、`full_research=true`；LLM 为
  `openai_compatible/gpt-5.5/healthy`，真实探针为 `responses.create`。
- 开发栈固定使用 `docker compose --env-file backend/.env ...`，否则 Compose 端口插值会回退到默认值；
  当前端口为 DB 55432、Redis 56379、Qdrant 56333/56334、MinIO 59000/59001、GROBID 58070。

### 5.2 最终回归

- 后端 pytest：544 passed、4 skipped、223 warnings；完整套件在 `--network none` 容器中执行，包含 DOI
  类型/facet wire contract、OpenAlex singleton 类型门禁、未知类型 fail-closed、Unpaywall 去重、
  单篇下载失败隔离、发布范围、相关性/分析专家定向返工、facet 证据隔离、shard 进度一致性及 LLM
  用量/预算终止回归。
- Ruff：0 error。
- 前端 Vitest：6 files / 30 tests passed。
- TypeScript：通过。
- ESLint：0 error，29 个模板既有 warning。
- Next.js production build：成功，124 个静态页面，research governance、管理员指标/策略页面与 BFF
  路由已生成。
- 新固化后端镜像的 Phase 6 零 GPT E2E：通过；API+BFF、Redis、PostgreSQL、Celery CPU worker、Qdrant、
  memory resolver、Profile/Policy/gold 治理全部命中真实部署，测试 fixture 自动清理。
- 新固化后端镜像的 Phase 3/5 零 GPT E2E：8 个产物实际写入/回读 MinIO，排除和指标账本与 PostgreSQL
  逐字节交叉核验，数据库 mutation 被阻断；事务和对象均自动清理且反查为 0。
- 新固化镜像的轻量 search-only E2E：`SELECTING → RENDERING → COMPLETED` 实际写入/回读四种元数据产物，
  Top-N 冻结、零 LLM、零 PDF/解析/分析行，并自动清理 PostgreSQL/MinIO fixture。
- 现有 warning：Pydantic `json_encoders` v3 弃用提示，以及一个既有 AsyncMock resource warning；
  均未导致测试失败，但应在模板升级窗口清理。

## 6. 已实现功能汇总

- 课题协议编译、澄清、版本化、哈希、批准和三种执行模式；其中 search-only 可在不调用 LLM、不给 PDF
  赋予分析结论的前提下，冻结严格 Top-N 并导出 Markdown、CSV、BibTeX、简化 OPML。
- 配额固定的 Crossref 单次 facets/同义词检索和原生类型双门禁 → 35 DOI 去重 → OpenAlex DOI 单篇补全
  与类型复核 → Unpaywall DOI 单篇定位 → 许可证/PDF/ClamAV 验证，并保存查询与原始响应 provenance。
- 授权指标快照导入、管理员来源/许可/哈希审计页、精确 metric fact/year、期刊/会议策略、三态硬约束
  账本和严格 shortfall。
- BM25/embedding/CrossEncoder/facet 相关性判定。
- full-research 证据化 LLM Facet 第三阶段、10 篇批处理、逐论文 metadata 证据白名单、中心性门禁和
  持久化 judgement；search-only 明确不调用 LLM。
- HTTPS/DNS pin 全文获取、许可策略、ClamAV、MinIO、OCR、GROBID/PyMuPDF、解析质量门禁。
- 页码/bbox/字符区间/块哈希/文档哈希 EvidenceLocator 和 Qdrant 隔离。
- 六类结构化专家均已进入正式工作流；protocol 是用户显式触发、预算受控且不可自行批准的草案建议，
  relevance/analysis/figure/audit/synthesis 执行证据白名单、矛盾/覆盖审计及一次性定向返工。
- 协议哈希锁定的单次 LLM 操作预算、provider-side token 门禁、按专家/失败重试持久化用量、运行聚合、
  前端审计卡和 manifest 导出；第三方成本缺失明确为 `UNAVAILABLE`。
- 每篇论文独立 Celery shard、稳定任务 ID/版本输入哈希、独立事务重试和 PostgreSQL 终态 barrier。
- 图/表定位裁剪、表格 cells、精确数值来源校验和 plot calibration 合同。
- Markdown、OPML、BibTeX、JSONL、papers CSV、exclusions CSV、实际使用指标 CSV、manifest，共 8 个
  必需产物，多 generation、数据库权威重算、回读哈希和发布门禁。
- manifest 绑定本次运行的原始来源对象 SHA-256 与实际指标快照 ID，并在发布前回读数据库交叉核验。
- PostgreSQL 状态机、事务 outbox、幂等投递、暂停/取消/恢复、watchdog 和事件重放。
- L0–L4 记忆、Redis L1 24 小时草案恢复、用户画像/项目记忆操作面、反馈写入→Celery/Qdrant 索引→后续
  协议建议语义检索闭环、严格优先级与 provenance、版本化人工 gold 创建/触发、最低样本量、分级 nDCG
  和评测看板；该闭环已有自动清理的部署态 API+BFF E2E。
- 真实组织/成员持久化、OWNER 管理、活动组织 header、个人/组织访问语义、即时撤权和前端组织管理。
- 独立 Next.js 研究工作台和经冷启动验证的 17 服务生产 Docker Compose 研究拓扑。
- OpenAI、DeepSeek、项目级 OpenAI-compatible 三 provider 工厂、凭据隔离、真实可达性探针和
  endpoint 指纹化模型 identity；Codex ChatGPT 登录与项目 API key 完全分离。

## 7. 尚未实现或尚未完成验收

1. 项目级第三方 `gpt-5.5` Responses、`full_research` readiness、目标 2 篇预验收和后续运行的
   token 用量账本均已部署；尚未完成目标 20 篇 full-research E2E 与逐篇人工验收。第三方网关是否返回
   可核验美元成本只能在轮换 key 后的新运行中确认；缺失时系统会标为 `UNAVAILABLE`。本次 2 shard 中
   1 个被 evidence boundary 终止；定向返工已补齐，但为控制额度没有再启动第二次真实运行。
   新增的 relevance Facet LLM 阶段已完成自动测试和部署，但同样尚未用轮换后的 key 做真实 3 篇质量验收。
2. 未提供合法的 JIF/CAS/会议指标快照，严格质量筛选不能用真实许可数据验收；格式、许可、年度和上传步骤
   已写入 `evaluation/DATA_PREPARATION_GUIDE.md`，不能由系统生成伪正式快照代替。
3. 未完成每个主要领域 20–50 个请求、每池 100–200 篇候选的双人独立标注、第三人仲裁及
   Cohen's kappa/Krippendorff's alpha；TREC-COVID 只能作为外部英文生物医学检索基准。
4. 未对 20 篇真实论文逐篇人工核验八类分析、每篇 3 个 claim、1 张主要图和 1 个精确数值。
5. 双用户、四用户双组织、并发邀请/撤权、4-worker 生产式冷启动、Qdrant/GROBID 持续中断和
   validate-only 真实 SIGKILL E2E 已完成；仍缺独立压测机容量门槛、cost/run/20 篇规模压测，以及
   真实 20 篇 full-research 运行中的 worker SIGKILL 验收。
6. 未在后续模板 commit 上实际执行三方合并演练；2026-08-22 再次以 `git ls-remote` 核对，远端
   `origin/main` 仍为冻结基线 `3428d9a6214619d3514312886d59a36400747b7d`，不存在可合并的新提交。
7. 诊断期间曾意外回显项目第三方 GPT key；本轮检查 Compose 合并配置时又使当前值出现在工具输出中。
   代码和文档没有保存或复述该值，但该 key 现在必须在第三方网关侧立即撤销并轮换，继续只放在
   `academic_research_agent/backend/.env`；后续禁止使用会展开 environment 的 Compose config 输出。
这些缺口不能通过继续生成 mock 数据诚实解决；剩余最终验收需要轮换后的部署凭据、许可数据、人工标注
时间、独立压测环境或新的模板基线。

## 8. 下一步计划

1. 先以真实 Crossref/OpenAlex 课题运行新的 search-only 轻量链路，确认公开源响应 → 去重 → 严格
   Top-N → 四格式下载的端到端行为，并人工核对至少 3 篇 metadata；该步骤不调用 GPT 或下载 PDF。随后再在
   第三方网关轮换曾被终端回显的 GPT key，用新建协议页明确批准单次请求/token/可选美元上限，
   运行 3 篇验收并核对 Facet judgement、run progress、失败 attempt history、网关账单和 manifest，
   重点验证 relevance `2026-08-22.1` 与 analysis `2026-08-22.2` 的定向返工；通过后再扩展到 20 篇并
   执行人工逐篇验收。
2. 导入已获许可且带年份/有效期/hash 的 JIF、CAS、会议快照，分别运行 journal-only 和
   conference-only 协议，确认 UNKNOWN 不进入严格集合。
3. 在高速链路下载 2020-07-16 CORD-19 `metadata.csv`，运行现有流式导入器，将 NIST topic 1/50
   外部 benchmark 导入项目并报告 Recall@Pool、Precision@20、分级 nDCG；保留 pooling 局限声明。
4. 启动跨领域/跨语言双人标注与第三人仲裁，补齐相关性、约束、去重、元数据、证据、图表和数值
   gold；样本量不足的指标继续保持 `NOT_EVALUATED`。
5. 在当前已验证的 production 多 worker 拓扑之外增加独立压测机并设定容量目标，补做真实 20 篇
   worker SIGKILL、多租户持续并发与 P95/cost/run 压测；已有 localhost burst/持续中断只作为基线。
6. 在新的模板 commit 上执行一次三方合并，记录领域命名空间的冲突数量与处理步骤。

## 9. 关键复验入口

- `backend/scripts/live_research_pipeline_e2e.py`：公开学术来源检索 E2E。
- `backend/scripts/live_search_only_catalog_e2e.py`：零 GPT 的 strict metadata Top-N、四格式 MinIO 导出、
  零 PDF/解析/分析行与自动清理部署验收。
- `backend/scripts/live_research_control_e2e.py`：暂停、恢复、取消和事件重放。
- `backend/scripts/live_research_fault_injection.py`：worker outage、watchdog、重复投递和 checkpoint 恢复。
- `backend/scripts/live_research_isolation_e2e.py`：部署态双用户读取/控制越权验证。
- `backend/scripts/live_research_organization_isolation_e2e.py`：四用户双组织协作、跨租户直链、角色管理、
  即时撤权和 Next.js 代理 header 转发矩阵。
- `backend/scripts/live_research_organization_concurrency.py`：并发邀请/撤权确定性、撤权后并发读取和
  backend/BFF P50/P95 基线。
- `backend/scripts/live_research_phase6_e2e.py`：零 GPT 的 L1–L4、BFF、Celery/Qdrant、协议优先级、gold
  fail-closed 与跨用户隔离部署验收；所有临时 fixture 在 `finally` 自动清理。
- `backend/scripts/verify_discovery_contract.py`：零网络验证单次/35 DOI 配额、facet query、Crossref/OpenAlex
  原生类型双门禁和 unknown fail-closed。
- `backend/scripts/live_research_artifact_audit_e2e.py`：零 GPT、真实 PostgreSQL/MinIO 的 8 产物回读、排除
  原因、精确指标 fact/year、数据库权威重算、篡改阻断和零残留验证。
- `backend/scripts/verify_research_production_topology.py`：17 服务、密钥、端口、队列、worker、网络、
  代理和持久卷的生产 Compose fail-closed 门禁。
- `backend/scripts/run_research_dependency_outage_e2e.py`：Qdrant/GROBID 持续中断逐样本能力断言与
  `finally` 自动恢复。
- `backend/scripts/run_research_worker_sigkill_e2e.py`：真实 SIGKILL 137、watchdog、重复投递、PID/
  restart policy 恢复和事件后缀重放。
- `backend/scripts/live_research_multiprocess_metrics_e2e.py`：生产 `/metrics` Bearer 401、四 PID 分片和
  请求计数精确增量验证。
- `backend/scripts/verify_analysis_shard_barrier.py`：每篇论文持久化 shard、终态 barrier 和事务回滚验证。
- `backend/scripts/verify_llm_connectivity.py`：不输出密钥的当前 provider 真实 Responses API 连通性/模型权限验证。
- `backend/scripts/live_full_research_preflight.py`：目标 2 篇的真实 full-research、证据和六类产物预验收。
- `backend/scripts/verify_document_safety.py`：ClamAV、EICAR 和 DNS pin 下载。
- `backend/scripts/verify_parsing_quality.py`：扫描 PDF OCR 与 bbox。
- `backend/scripts/verify_figure_artifacts.py`：图表裁剪、cells 和精确数值。
- `backend/scripts/import_trec_covid_gold.py`：NIST qrels + CORD-19 metadata 流式导入。
- `evaluation/ANNOTATION_GUIDE.md`：自建人工 gold 的标注与裁决规范。
- `evaluation/DATA_PREPARATION_GUIDE.md`：正式指标快照、双人 Gold 与 CORD-19/TREC-COVID 准备流程。

## 10. 外部数据依据

## 11. 2026-08-22 登录后跳转与认证稳定性修复

- 浏览器显示 `Successfully logged in` 时，部署日志同时确认 `POST /api/v1/auth/login` 与其后的
  `GET /api/v1/auth/me` 均为 200。因此该现象不是账号密码、Cookie 写入或 API 鉴权失败。
- 根因位于登录页的前端软路由：登录页挂载时的匿名 `/auth/me` 校验与 `router.push()` 跳转存在竞态，
  使登录成功提示已显示、但页面仍停留在 `/login`。
- `frontend/src/hooks/use-auth.ts` 已改为登录成功后执行 `window.location.replace(destination)`；它保留
  原有目的地规则（管理员到仪表盘，普通用户到聊天），但以一次携带新 Cookie 的完整页面请求进入受保护
  页面，避免使用仍在协调中的登录页路由状态。用户可从顶部“研究”进入论文检索工作台。
- 待部署后由浏览器实测一次：登录成功后应自动离开 `/login`；若仍失败，需保存当次浏览器 Console 和
  Network 中 login 请求后的首个导航请求，而不是反复输入密码触发限流。

## 12. 2026-08-22 登录目的地复核

- 复查部署日志后确认：用户每次 `POST /api/v1/auth/login` 都是 200，随后出现
  `GET /api/v1/agent/models` 200；后者只由聊天页 `ChatControls` 挂载时发起。这证明上一版的
  `window.location.replace()` 已经执行并进入过 `/chat`，并非登录接口没有成功。
- 问题是我错误保留了模板的默认目的地：普通用户登录后被送往 `/chat`，而不是本项目应使用的
  `/research/new` 论文检索工作台。现在登录成功统一跳转至 `ROUTES.RESEARCH_NEW`。
- 同时清除最后一个 `crypto.randomUUID()` 调用：`chat-store.ts` 原本仅以 `"randomUUID" in crypto`
  判断，在 HTTP/LAN 的非安全上下文中该属性可存在但不可调用，仍会引发页面异常；现统一复用已有的
  `createClientId()` 安全降级实现。
- 前端生产构建已通过、容器已重建并健康；`/api/health` 返回 200，运行中 bundle 已核验含有
  `location.replace('/research/new')` 对应代码。仍需用户浏览器刷新后的一次真实登录来完成最终 UI 验收。

## 13. 2026-08-23 HTTP 登录闪退根因与修复

- 浏览器截图表明登录后确实短暂进入 `/research/new`，但该页 `/api/auth/me` 为 401，认证守卫随即回到
  `/login`。
- 用临时账号完成了部署态 Cookie 链路测试（账号随后已删除）：BFF 登录为 200，携带 Cookie 的
  `/api/auth/me` 为 200；但登录响应的 `access_token` 与 `refresh_token` 都带 `Secure` 属性。用户以
  `http://192.168.31.145:53000` 访问时浏览器必然拒收这些 Cookie，故真实浏览器随后没有 Cookie 可发送。
- 根因是 Next.js standalone 的登录路由在**构建期**按 production 默认值固化了 Cookie 策略；Compose 的
  `NODE_ENV=development` 运行时覆盖并不能改变已编译路由。
- 已在 `frontend/Dockerfile` 把 `COOKIE_SECURE` 作为构建参数传给 Next build；本地/LAN Compose 明确设置
  `COOKIE_SECURE=false`，生产 Compose 明确设置 `COOKIE_SECURE=true`。重建后以新的隔离临时账号复测通过：
  `register=201`、`login=200`、携带 Cookie 的 `/api/auth/me=200`，两枚 Cookie 均不含 `Secure`；该账号已
  删除。现在只剩用户浏览器的一次真实登录 UI 验收。

- NIST TREC-COVID Complete：50 个主题、Round 5 文档集和累积人工 qrels：
  `https://ir.nist.gov/covidSubmit/data.html`
- TREC-COVID qrels：`https://ir.nist.gov/covidSubmit/data/qrels-covid_d5_j0.5-5.txt`
- AllenAI CORD-19 历史发布：
  `https://ai2-semanticscholar-cord-19.s3-us-west-2.amazonaws.com/historical_releases.html`

## 14. 2026-08-23 本地 Zotero 论文库一期

> 2026-08-23 前端可见性修复：初版工作台在状态接口出现 401/403/503 等任意错误时直接隐藏整个卡片，导致用户只能看到原有“新建论文调研”表单，无法判断故障。现已改为始终渲染“本地论文库（Zotero）”卡片，并在卡片内明确展示“需要应用管理员权限”或“本地论文库状态读取失败”的具体错误；同步按钮会在前端身份不足时禁用，后端仍以 `CurrentAppAdmin` 作最终鉴权。前端镜像已重新构建并强制重建容器，启动健康。

> 权限配置：经部署所有者明确授权，实际使用账号已被设置为活动应用管理员（`role=admin`、`is_app_admin=true`）。未修改其他账号、文献数据或源目录；浏览器刷新后 `/auth/me` 会取得新权限。

### 已实现

- 新增独立的私有本地论文库数据域：`local_paper_libraries`、`local_papers`、`local_paper_chunks`、
  `local_paper_sync_runs` 和 `local_paper_quarantine_items`；不复用 `rag_documents` 或全局 RAG 集合。
- 新增 Alembic `0044_local_paper_library`，已在当前 PostgreSQL 实例实际迁移；首位发起同步的 app admin
  成为库所有者。所有状态、同步、检索、问答与导出接口都要求 `CurrentAppAdmin`，且再次核验 owner；
  非所有者不能读取、检索或触发同步。
- 新增可选 Compose overlay `docker-compose.local-library.yml`。当前机器的 `backend/.env` 已设置
  `LOCAL_PAPER_LIBRARY_HOST_PATH=/home/cumt/lly/我的文库`，app 与 `research-worker-cpu` 均挂载为
  `/local-paper-library:ro`。Docker inspect 实测两处均为 `rw=false`。
- 手动同步通过现有 `research-cpu` Celery worker 执行，不引入目录监听；worker 的任务注册已实测。按 SHA-256
  增量更新，源文件消失会标记 `MISSING` 并从向量检索中移除，审计记录不删除。
- Better BibTeX 解析优先使用 `file` 中的相对路径，回退到唯一文件名；重复 DOI/重复内容、缺失附件、
  未匹配源、非支持输入、抽取失败和空文本均写入待核验清单。`.zip`、`.prop` 等不解压、不执行。
- 支持 PDF（PyMuPDF 页码文本）和浏览器保存的静态 HTML。HTML 仅通过标准库解析文本，忽略
  `script/style/noscript/template/svg/canvas`，绝不执行其中脚本；HTML 的可引用证据页码为 1。
- 使用独立 `local_papers_<owner>` Qdrant collection，固定本地
  `sentence-transformers/all-MiniLM-L6-v2`、384 维向量，不调用 OpenAI 或第三方 Embedding。
- 工作台 `/research/new` 已新增“本地论文库（Zotero）”卡片：同步状态/待核验、关键词与元数据检索、
  页码证据、Markdown/CSV/BibTeX/OPML 下载，以及有证据问答。LLM 不可用时只返回本地证据，不生成无证据回答。
- 首次发布后发现旧浏览器会话不含新增加的 `is_app_admin` 字段，前端误将管理员卡片隐藏；现已兼容既有
  `role=admin` 会话，同时保留后端 `CurrentAppAdmin` 与 owner 的最终鉴权。当前数据库的聚合核查为
  `admin/is_app_admin=true` 账号 1 个、普通账号 6 个；前端已重新构建并恢复 healthy。
- 新增 3 个单元测试，验证 Better BibTeX 附件解析、HTML 脚本剔除和本地向量 provider 配置；实际运行 `3 passed`。
  后端 Ruff/py_compile 与前端 Docker/Next TypeScript production build 均通过；前后端当前均为 healthy。

### 已核查的真实源库

- `/home/cumt/lly/我的文库` 含根目录 `我的文库.bib`、308 条 BibTeX 记录、294 个 PDF 附件、129 个 HTML
  附件。附件字段可解析为 294 PDF + 129 HTML 路径。
- 2 个附件路径当前失效（一个缺失的本地 PDF 与一个 `nature.com` URL 形式条目）；首次同步会将其记为
  `UNMATCHED_BIBTEX`，不会中断其余论文。
- HTML 文件质量不齐：129 个中 24 个静态正文超过 2,000 字符，另一些只有壳页/元数据文本；系统保留其
  来源与待核验状态，不会将低质量 HTML 伪装为完整 PDF 全文。

### 尚未执行（刻意保留）

- 尚未代替用户点击第一次真实同步：这是为了严格保留“首位导入管理员即唯一 owner”的产品语义，避免由
  工程操作意外占用用户的私有文库所有权。
- 未实施外部 Crossref/OpenAlex/Unpaywall 检索、外部 PDF 下载和八类逐篇深度分析；一期本地库不会调用它们。
- 尚未完成用户要求的至少 20 篇人工元数据—附件—Top-K—页码抽查；应在首次同步完成后执行。

### 下一步计划

1. 用户以目标 app admin 登录 `http://192.168.31.145:53000/research/new`，在“本地论文库（Zotero）”中点击
   “手动同步/增量重建”；等待状态从 `QUEUED/RUNNING` 变为 `READY`。
2. 用“semantic communication”“VLA”等主题抽查 Top-K，核验显示的 PDF 页码/HTML p.1 与原文件；处理
   待核验清单中的两个缺失附件和壳页 HTML。
3. 人工抽查至少 20 篇后，再决定是否将在线发现和八类深度分析接入同一页码证据数据域。
