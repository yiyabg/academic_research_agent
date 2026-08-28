必须以执行时的当前工作树为准。若某问题已在更新提交中修复，应通过代码和测试确认，不要重复实现。

# 一、工作原则

开始修改前必须：

1. 完整阅读根目录 `AGENTS.md`；
2. 执行并记录：

```bash
git status --short --branch
git rev-parse HEAD
git log -5 --oneline
```

3. 检查当前工作树和相关测试；
4. 给出不超过 10 条的简短实施计划；
5. 随后直接修改代码、测试和部署文档。

约束：

* 保留用户已有改动；
* 不执行 `git reset --hard`、`git checkout --`、`git clean` 等破坏性操作；
* 不提交、不推送代码；
* 不调用真实付费模型；
* 不静默修改数据安全策略；
* 不修改已经可能应用到生产数据库的 0052、0053 迁移语义；
* 如果确实需要新数据库字段，创建 0054，但本任务应优先避免新增字段；
* 不引入 GraphRAG、Agentic RAG、自主循环、多智能体、Elasticsearch、新向量库或工作流引擎；
* 不重构整个 literature research 子系统；
* 不用“代码可编译”代替测试通过；
* 如果当前环境无法执行数据库或 Docker 测试，必须明确标记为未验证，不能宣称完成。

# 二、已确认的当前架构

必须保留下面的职责区别：

## 2.1 论文发现检索

```text
LocalPaperLibraryService.search()
```

它是在经过 PostgreSQL 元数据过滤后的候选论文集合中执行跨论文检索：

```text
PostgreSQL 元数据预过滤
→ 跨论文 Dense + BM25
→ RRF
→ BGE rerank
→ 论文级多样性选择
→ 返回论文列表
```

该接口的目标是“找到哪些论文相关”，不是逐篇论文内部深挖证据。

## 2.2 深度分析证据检索

深度分析必须保持：

```text
选定 paper_ids
→ 每篇论文独立使用 question 检索正文
→ child chunk 命中
→ PostgreSQL parent section 扩展
→ 每篇结构化分析
→ 跨论文综合
```

分析路径不得重新使用跨论文 MMR，也不得让某篇选定论文因为全局排名较低而消失。

# 三、当前已确认的缺陷

以下问题均需要按本 Prompt 修复或通过测试证明已经修复。

## 3.1 查询解析器存在实际失败

当前：

```text
2026年发表的 semantic communication
```

实际可能被解析为：

```json
{
  "semantic_query": "发表的 semantic communication",
  "effective_filters": {
    "year_from": 2026
  }
}
```

正确结果必须是：

```json
{
  "semantic_query": "semantic communication",
  "effective_filters": {
    "year_from": 2026,
    "year_to": 2026
  }
}
```

当前中文“期刊论文”“会议论文”也会因为 `\b` 不适用于连续中文文本而解析失败。

当前自动解析器仅覆盖年份、DOI、部分 BibTeX 类型；作者、venue、keywords 只有显式 API 字段，没有可靠的自然语言解析。

## 3.2 新分析 retriever 不是真正 BM25

`local_paper_retrieval.py` 当前 lexical 路径存在以下问题：

* 对数据库结果先做 `lexical_rows[:bm25_limit]`；
* 没有对完整候选范围计算 BM25；
* 只判断完整 query 是否为字符串子串；
* 命中统一赋值 `1.0`；
* 与 discovery search 已有的 `BM25Okapi` 实现不一致。

这会导致深度分析正文细节过度依赖向量检索，并漏掉数据库后部的关键词证据。

## 3.3 父子文档仍被二次字符截断

当前分析链路已经能够：

```text
child chunk
→ LocalPaperSection.content
→ 以 child 所在位置为中心扩展 parent 上下文
```

但仍存在：

```python
target_context_chars = 1500
context = context_text[:800]
```

因此父文档确实发挥了作用，但最终进入模型的上下文仍然过短，而且使用字符数而不是 token 预算。

## 3.4 orchestrator 的 evidence payload 不一致

当前不同路径分别假设：

```python
stage.evidence_json["paper"]
```

或：

```python
{
    "paper_id": ...,
    "paper_title": ...,
    "evidence": ...
}
```

由此导致：

* background paper stage 无法正常提交；
* synthesis/partial report 可能显示“未命名论文”；
* 某些代码引用未导入的 `LocalPaperRead`；
* 测试仍使用旧 payload，掩盖新实现问题。

## 3.5 query-only 和 legacy 路径存在未绑定导入

当分析请求没有 `paper_ids`、只提供 query 时，相关 `LocalPaper`、`LocalPaperLibrary` 或 `sql_select` 导入只存在于另一个条件分支，可能触发运行时错误。

废弃的 `/mindmap` 兼容路由也会经过该分支。

## 3.6 mode 没有真正生效

前端允许：

```text
focused
comparative
comprehensive
```

job 也保存了 mode，但新的 staged orchestrator 没有按 mode 构建逐篇分析和综合 Prompt。

不得继续让三个选项产生基本相同的报告。

## 3.7 background 模式当前不可用

除 payload 不一致外，还需要检查：

* background recovery task 是否被路由到实际监听的 Celery queue；
* paper stage 是否保存并提交 `provider_response_id`；
* poll 是否一次只查询一次；
* worker 重启恢复；
* synthesis stage 的 background 生命周期；
* `BACKGROUND_NOT_SUPPORTED` 是否被正确规范化。

background 必须继续保持显式可选，默认 staged。

## 3.8 前端未展示约束解释

后端已经返回 `query_interpretation`，但前端类型和页面没有完整展示：

* `semantic_query`；
* `effective_filters`；
* `filter_sources`；
* `warnings`。

用户无法确认系统究竟将哪些表达解释成了硬约束。

## 3.9 运维配置和文档存在错误

需要检查并修复：

* `backend/.env.example` 的 ingestion version 与代码默认值不一致；
* 容器设置了 `POSTGRES_HOST=db`，但没有始终覆盖 `POSTGRES_PORT=5432`；
* 宿主机映射端口 `55432` 不应成为容器内部端口；
* `DEPLOYMENT.md` 中部分 API 缺少 `/api/v1`；
* 部署文档使用单独 systemd/uvicorn，未反映完整 Docker Compose 拓扑；
* `verify_refactoring.py` 使用已移除的 `asyncio.coroutine`；
* `verify_refactoring.py` 引用不存在的 `settings.database_url`，正确设置名称应按当前配置类核实；
* “五篇论文分析小于 30 秒”等未测量指标必须删除。

# 四、具体修改要求

## 4.1 修复确定性查询解析

修改：

```text
backend/app/services/literature_research/local_paper_query_parser.py
```

要求支持并测试：

```text
2026年发表的 semantic communication
2026年的 semantic communication 论文
发表于2026年的语义通信论文
2024-2026年的论文
2024至2026年发表的论文
从2024年起的论文
2024年及以后的论文
截至2026年的论文
papers published in 2025 about AI
```

语义：

* 精确年份：同时设置 `year_from` 和 `year_to`；
* 范围：设置上下界；
* “及以后/起”：设置下界；
* “截至/以前”：设置上界；
* 显式 API 参数优先于自然语言解析；
* 年份范围非法时不得执行模糊搜索，应返回稳定的验证错误或结构化 warning；
* 从 semantic query 中删除完整约束短语，不能残留“发表的”；
* `6G`、`3D`、`GPT-4`、`IEEE 802.11`、`RFC 2616` 不得识别为年份；
* query 同时包含 `6G` 和真实年份时，仍应解析真实年份，不能因为 query 中存在 `6G` 就禁用全部年份解析；
* 中文 BibTeX 类型不能依赖英文单词边界 `\b`。

对作者、venue、keywords 只解析高置信度的显式标记，例如：

```text
作者: 张三
author: Zhang Wei
期刊: IEEE TWC
venue: IEEE TCCN
关键词: semantic communication, VLA
keywords: semantic communication, VLA
```

不要尝试把任意人名、机构名或普通名词猜成硬约束。

## 4.2 修复元数据过滤

在 `LocalPaperLibraryService.search()` 中：

* DOI 使用规范化后的稳定匹配；
* keywords 列表必须真正实现“匹配任意一个关键词”，不能只读取 `keywords[0]`；
* exact year 默认排除 `publication_year IS NULL`；
* venue、author、DOI、keywords 条件必须参数化，不能拼接原始 SQL；
* 显式筛选值覆盖自动解析值；
* `query_interpretation` 必须反映最终实际执行的条件；
* `total` 必须是去重后、应用 limit 前的真实论文数量；
* `items.length` 才是当前返回数量；
* metadata-only 查询不得被当成 hybrid 查询；
* 保持 owner、library、INDEXED、active version 约束不变。

## 4.3 统一真正的 lexical retrieval

不要在 discovery 和 analysis 中维护两套含义不同的“BM25”。

将以下通用逻辑提取到合适的共享模块，优先放入现有：

```text
local_paper_retrieval.py
```

共享内容至少包括：

* `_bm25_tokens`；
* scoped BM25 corpus；
* BM25 cache key；
* `BM25Okapi` 排名；
* Dense/BM25 的 RRF 融合。

要求：

1. 对 metadata/paper/version scope 内的全部有效 lexical rows 建立语料；
2. 在计算分数之后取 BM25 top-k；
3. 禁止在评分前使用：

```python
lexical_rows[:bm25_limit]
```

4. 禁止用完整 query 子串判断伪装成 BM25；
5. discovery search 与 analysis evidence 使用同一 lexical 语义；
6. 仅 active document version 可参与；
7. PostgreSQL 仍是 chunk/section 权威数据源；
8. Qdrant 中的孤立 point 不得进入最终证据；
9. reranker 返回数量不一致时必须失败并记录错误，不能使用 `zip(..., strict=False)` 静默截断。

## 4.4 发现检索采用软配额

保留论文级 MMR，但替换“重排前每篇固定最多 2 chunk”的绝对硬裁剪。

实现有界两阶段分配：

```text
阶段1：按 RRF 顺序尽量给不同论文至少一个 rerank 候选
阶段2：用剩余 rerank budget 按全局相关性继续补充
```

要求：

* 保留每篇论文的安全上限，防止单篇超长 PDF 占满候选集；
* 安全上限应成为最后保护，而不是每篇固定只能有 2 个候选；
* 不能让候选论文数超过总 rerank budget；
* 论文级 MMR 仍只负责最终 discovery 论文多样性；
* 用单元测试证明长文档不会垄断，同时第二个高相关 chunk 仍有机会进入 reranker。

不要把该逻辑用于已选择论文的深度分析。

## 4.5 完善逐篇分析证据选择

`LocalPaperEvidenceRetriever` 必须：

* 对每个 paper_id 独立检索；
* 主查询始终是 `questionquestion`；
* `query_context` 只作为一次补充检索上下文；
* 不执行跨论文 MMR；
* child 候选经过真实 Dense + BM25 + RRF + BGE；
* 低于现有 rerank 阈值的 chunk 不得仅因来自新 section 就被选中；
* 内容去重；
* section 多样性是软约束；
* 最大证据数是安全上限，不是主要预算控制方式。

新增最少配置：

```env
LOCAL_PAPER_ANALYSIS_MIN_EVIDENCE_PER_PAPER=2
LOCAL_PAPER_ANALYSIS_MAX_EVIDENCE_PER_PAPER=6
LOCAL_PAPER_ANALYSIS_EVIDENCE_TOKEN_BUDGET=4000
```

补充检索最多一次，触发条件：

```text
通过阈值的证据少于 MIN_EVIDENCE_PER_PAPER
```

补充检索应：

* 使用 `question + query_context`；
* 与第一轮候选合并；
* 按 chunk_id/content hash 去重；
* 重新排序和预算选择；
* 不用无限循环；
* 不调用 LLM 改写 query。

## 4.6 按 token 构建 parent context

必须删除：

```python
target_context_chars = 1500
context_text[:800]
```

改为：

1. 完整保留命中 child；
2. 在 parent section 中定位 child；
3. 优先按段落或句子边界向前后扩展；
4. 对每篇论文的所有证据统一计算 token 预算；
5. 使用项目已有 token 工具；若没有合适实现，可使用已安装的 `tiktoken` 和固定 encoding；
6. token 计数器必须可注入或可单元测试；
7. 不能从 parent 开头机械截断；
8. child 无法在 parent 中定位时，至少保留完整 child；
9. 不把摘要、引言、结论无条件附加到 focused 上下文。

每条 evidence 必须保存：

```text
paper_id
document_version_id
section_id
chunk_id
section_type
section_heading
page_number
child_text
context_text
rerank_score
retrieval_pass
```

## 4.7 建立唯一的 typed evidence payload

为分析 stage 建立一个小型 Pydantic schema 或明确的 typed dataclass，避免继续传递无约束 dict。

统一 JSON 结构为：

```json
{
  "paper": {
    "id": "uuid",
    "title": "paper title",
    "citekey": "citekey",
    "document_version_id": "uuid"
  },
  "question": "analysis question",
  "queries_used": ["..."],
  "insufficient_evidence": false,
  "evidence": [
    {
      "paper_id": "uuid",
      "document_version_id": "uuid",
      "section_id": "uuid",
      "chunk_id": "uuid",
      "section_type": "METHOD",
      "section_heading": "3 Method",
      "page_number": 4,
      "child_text": "...",
      "context_text": "...",
      "rerank_score": 0.87,
      "retrieval_pass": 1
    }
  ]
}
```

以下组件必须统一使用该 contract：

* staged paper analysis；
* background paper submission；
* synthesis；
* partial report；
* artifact；
* audit/stage result；
* tests。

禁止一部分代码读取：

```python
paper_title
```

另一部分读取：

```python
evidence_json["paper"]["title"]
```

## 4.8 修复分析 orchestrator

要求：

* 将公共 model/import 放到正确模块作用域；
* query-only 分支可正常运行；
* 有 `paper_ids` 时严格限定 owner/library/INDEXED/active version；
* 如果请求中的 ID 集合与可用论文集合不一致，返回安全的 `INVALID_PAPER_SCOPE`，不能静默删除某篇论文；
* 没有 paper_ids 时：

  * 用 `request.query` 做论文 discovery；
  * 用 `request.question` 做逐篇正文 evidence retrieval；
* 每个选定论文创建一个 PAPER stage；
* 最后创建一个 SYNTHESIS stage；
* 成功 stage 不重复执行；
* 单篇失败只重试该篇一次；
* 重复 Celery 消息不能重复生成 artifact；
* synthesis 已成功但 finalize 中断时，重试应从已持久化 synthesis result 完成 finalize；
* PostgreSQL 是恢复依据；
* paper stage 成功时保存真实 latency；
* job、stage 和 turn 中的 `source_versions_json/evidence_json` 类型注解与实际 JSON 结构一致；
* 最终报告保留真实论文标题，不能出现由 payload 不一致造成的“未命名论文”。

## 4.9 实现三种 mode 的真实差异

`focused`：

* 只回答用户 question；
* 逐篇给出明确答案、证据句柄、页码；
* 不强制生成背景、创新点、完整综述；
* 证据不足时逐篇写 `[摘录不足]`。

`comparative`：

* 逐篇提取与 question 相关的同一组字段；
* synthesis 输出横向比较表；
* 不得把不同论文的证据互相归属。

`comprehensive`：

* 背景；
* 问题定义；
* 方法；
* 模型/系统结构；
* 实验设置；
* 主要结果；
* 局限；
* 与其他论文的关系。

mode 必须同时影响：

* paper-stage prompt；
* synthesis prompt；
* 最终报告结构。

为三个 mode 增加测试，证明生成的 system/user prompt 和报告结构不同。

## 4.10 修复 background 可选路径

默认配置保持：

```env
LOCAL_PAPER_ANALYSIS_EXECUTION_MODE=staged
LOCAL_PAPER_ANALYSIS_ALLOW_EPHEMERAL_PROVIDER_STORAGE=false
```

仅修复现有 background 生命周期，不增加新组件：

* 使用统一 evidence payload；
* paper stage 可以成功调用 `responses.create(background=True)`；
* 先保存 `provider_response_id` 再调度 poll；
* 每次 poll 只 retrieve 一次；
* 使用 countdown 调度下一次；
* 禁止 `while + sleep`；
* recovery task 必须路由到实际监听的 `research-llm` queue；
* worker 重启后从 PostgreSQL 恢复；
* synthesis background stage 使用已持久化逐篇结果；
* deadline 后 cancel；
* `store=false` 保持不变；
* 未授权临时存储时明确返回 `BACKGROUND_STORAGE_NOT_ALLOWED`；
* provider 不支持 background 时返回 `BACKGROUND_NOT_SUPPORTED`；
* 不运行真实 provider 测试，全部使用 fake AsyncOpenAI client。

## 4.11 清理废弃摘要字段

保留数据库/API 字段以兼容旧客户端，但：

* 新同步继续写入 `NULL`；
* API 始终返回 `null`；
* 当前 staged/background 分析不得读取这些字段；
* `PaperMindmapService` 若只用于旧逻辑，应明确标记 legacy；
* 移除其活动路径中对 deprecated 字段的依赖；
* ABSTRACT、INTRODUCTION、CONCLUSION 应作为正常 `LocalPaperSection` 参与 child/parent 切分和问题相关召回；
* 不为它们建立额外长文本副本；
* 不把完整 parent 复制到 Qdrant。

## 4.12 前端最小修改

保持单一搜索框为主要入口，不增加常驻的大型筛选表单。

新增一个默认折叠的“高级筛选”，仅包含：

* 年份起止；
* 作者；
* DOI；
* venue；
* BibTeX 类型；
* keywords。

要求：

* 用户不打开高级筛选时，后端仍可自动解析高置信度约束；
* 显式输入覆盖自动解析；
* 在搜索结果上方显示解析结果 chips：

  * 语义查询；
  * 年份；
  * 作者；
  * DOI；
  * venue；
  * 类型；
  * keywords；
  * filter source；
* warnings 使用简短中文提示；
* TypeScript 类型增加 `QueryInterpretation`；
* `total` 显示真实命中数；
* `items.length` 显示当前展示数；
* 论文条目显示 venue，keywords 可折叠；
* 不展示原始 provider 异常；
* staged 状态继续显示已分析论文数/总数；
* background 状态只在管理员明确启用时使用；
* 页面刷新、WebSocket 断开后的 HTTP 恢复不得回归。

## 4.13 修复运维配置

更新 `backend/.env.example`，至少包含：

```env
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_EXPOSE_PORT=5432

LOCAL_PAPER_INGESTION_VERSION=docling-parent-child-bge-v7

LOCAL_PAPER_ANALYSIS_EXECUTION_MODE=staged
LOCAL_PAPER_ANALYSIS_ALLOW_EPHEMERAL_PROVIDER_STORAGE=false
LOCAL_PAPER_ANALYSIS_STAGE_TIMEOUT_SECONDS=105
LOCAL_PAPER_ANALYSIS_STAGE_MAX_RETRIES=1
LOCAL_PAPER_ANALYSIS_MAX_CONCURRENCY=2
LOCAL_PAPER_ANALYSIS_REASONING_EFFORT=low
LOCAL_PAPER_ANALYSIS_PAPER_MAX_OUTPUT_TOKENS=1200
LOCAL_PAPER_ANALYSIS_SYNTHESIS_MAX_OUTPUT_TOKENS=1600
LOCAL_PAPER_ANALYSIS_MIN_EVIDENCE_PER_PAPER=2
LOCAL_PAPER_ANALYSIS_MAX_EVIDENCE_PER_PAPER=6
LOCAL_PAPER_ANALYSIS_EVIDENCE_TOKEN_BUDGET=4000
```

Docker Compose 内部的 app、migrate、Celery worker 必须显式覆盖：

```env
POSTGRES_HOST=db
POSTGRES_PORT=5432
```

宿主机暴露端口只能由：

```env
POSTGRES_EXPOSE_PORT=55432
```

控制。

当前单个 analysis job 内可并发两个模型请求。为了避免一个 `research-worker-llm --concurrency=4` 同时运行四个 job、造成最多八个上游调用，当前单 worker 生产拓扑应将 LLM worker concurrency 调整为 1，或者提供等价的全局限制。不要新增分布式锁组件。

## 4.14 修复验证脚本和部署文档

修复：

```text
backend/verify_refactoring.py
```

要求：

* Python 3.12 可运行；
* 不使用 `asyncio.coroutine`；
* 使用当前真实 Settings 属性；
* 默认不调用模型；
* 支持 `--skip-db`；
* DB 不可用时明确显示未验证；
* 返回正确退出码；
* 校验查询解析、模块导入、schema、Alembic revision；
* 不把失败显示成成功。

更新 `DEPLOYMENT.md`：

* API 使用 `/api/v1/research/local-library/...`；
* 使用当前 Docker Compose 拓扑；
* 说明 CLI sync 只是入队；
* 说明必须检查 `research-worker-cpu`；
* 说明 migration、备份、同步、验收顺序；
* 说明容器内部端口与宿主机暴露端口；
* 删除未经测量的性能承诺；
* 不把 `alembic downgrade -1` 写成无条件安全回滚；
* 同步会清空 deprecated 字段，因此部署前必须备份数据库；
* production 默认 staged；
* background 是实验性显式可选能力。

# 五、测试要求

必须新增或修复以下测试。

## 5.1 查询解析

覆盖：

1. 中文精确年份；
2. 英文精确年份；
3. 中文年份范围；
4. 起始年份；
5. 截止年份；
6. `6G/GPT-4/3D/IEEE 802.11`；
7. 同时包含 6G 和真实年份；
8. 中文 BibTeX 类型；
9. DOI；
10. 显式值覆盖自动解析；
11. author/venue/keywords 的显式标记解析；
12. semantic query 清理。

## 5.2 检索

覆盖：

1. metadata filter 先于 Qdrant；
2. exact year 结果违反率为 0；
3. NULL year 不进入精确年份结果；
4. keywords 不只使用第一个值；
5. BM25 在完整 scope 评分后取 top-k；
6. 位于数据库返回后部的关键词 chunk 可以被召回；
7. Dense + BM25 + RRF；
8. reranker 数量不匹配时失败；
9. discovery 软配额；
10. search 仍是跨论文 discovery。

## 5.3 evidence retrieval

覆盖：

1. 每篇论文独立调用 retriever；
2. 所有选定论文都得到结果或明确不足状态；
3. question 是主查询；
4. query_context 只用于一次补充检索；
5. 两轮结果合并去重；
6. 无跨论文 MMR；
7. token budget；
8. child 位于 parent 中间时保留双向上下文；
9. 低分新 section 不会被错误接受；
10. 完整 lineage。

## 5.4 orchestrator

使用 fake provider 覆盖：

1. staged 三篇论文完成并综合；
2. 某篇失败只重试该篇一次；
3. 某篇最终失败生成 PARTIAL；
4. query-only discovery 分支；
5. 显式 paper_ids 顺序；
6. 非法 paper scope；
7. typed evidence payload；
8. 三种 mode；
9. synthesis 标题正确；
10. 重复任务不重复 finalize；
11. synthesis 成功后 finalize 恢复；
12. HTTP 524 规范化；
13. 用户响应和 artifact 不包含原始 provider 错误。

## 5.5 background

使用 fake AsyncOpenAI 覆盖：

1. submit 保存 response_id；
2. 多次独立 poll；
3. worker 重启恢复；
4. deadline cancel；
5. duplicate poll 幂等；
6. background synthesis；
7. storage 未授权；
8. provider 不支持；
9. recovery task queue routing。

## 5.6 前端

覆盖：

1. query interpretation chips；
2. 高级筛选默认折叠；
3. 显式筛选请求；
4. total/展示数量；
5. staged 进度；
6. background 状态；
7. PARTIAL；
8. 脱敏错误；
9. 刷新恢复；
10. artifact 下载。

# 六、必须运行的验证

后端至少运行：

```bash
cd backend
uv sync --frozen
uv run python -c "from transformers import PreTrainedModel; from sentence_transformers import SentenceTransformer, CrossEncoder; print('imports OK')"
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv run alembic heads
uv run python verify_refactoring.py --skip-db
```

如果有可用测试 PostgreSQL，再运行：

```bash
uv run alembic upgrade head
uv run python verify_refactoring.py
```

前端使用仓库现有 `bun.lock`，不要生成或提交 `package-lock.json`：

```bash
cd frontend
bun install --frozen-lockfile
bun run type-check
bun run lint
bun run test:run
```

若项目已有构建门禁，还应运行：

```bash
bun run build
```

测试不得调用真实模型、真实第三方代理或付费 API。

# 七、明确不做

本次不要实现：

* GraphRAG；
* 完整 Agentic RAG；
* 多智能体检索；
* LLM 自主反复改写 query；
* 新向量数据库；
* Elasticsearch；
* Webhook；
* 动态 provider capability 数据库；
* 新监控仪表盘；
* 大规模前端视觉重构；
* 将 parent 文档复制到 Qdrant；
* 删除公共 `/mindmap` 接口；
* 删除数据库中的兼容字段；
* 修改与本地论文库无关的 research pipeline。

# 八、验收标准

只有满足以下条件才能宣布完成：

1. `2026年发表的 semantic communication` 同时产生 `year_from=2026` 和 `year_to=2026`；
2. semantic query 精确清理为 `semantic communication`；
3. 中文期刊/会议类型解析通过；
4. analysis lexical retrieval 使用真实 BM25；
5. search 保持跨论文 discovery；
6. selected-paper analysis 保持逐篇独立 evidence retrieval；
7. 所有选定论文均被验证，不被跨论文 MMR 丢弃；
8. parent context 使用 token budget，不存在 800/1500 字符硬截断；
9. staged/background/synthesis 使用同一 typed payload；
10. query-only 和 legacy 路径无运行时 NameError；
11. 三种 mode 输出策略真实不同；
12. staged 模式默认可用；
13. background 默认关闭，显式启用时 fake 生命周期测试通过；
14. 前端展示实际解析出的硬约束；
15. PostgreSQL 容器内部始终连接 `db:5432`；
16. `verify_refactoring.py` 可运行；
17. 后端和前端发布门禁通过；
18. 不存在当前修改引入的 Ruff、类型或测试错误；
19. 没有使用真实付费模型；
20. 文档只报告实际执行并通过的测试。

# 九、最终交付报告

完成后给出：

1. 初始根因；
2. discovery 与 analysis 两种检索策略的最终边界；
3. 查询解析修复；
4. BM25 修复；
5. 软配额实现；
6. 父子文档和 token budget；
7. typed evidence payload；
8. staged/background 修复；
9. mode 差异；
10. 前端修改；
11. 运维配置修改；
12. 修改文件列表；
13. 数据库迁移情况；
14. 实际运行的测试命令；
15. 每条命令的通过/失败结果；
16. 尚未验证的运行环境事项；
17. 正确的 Docker 构建、迁移、启动、同步和验收命令。

不要只输出建议或伪造“部署成功报告”。必须完成实际代码修改，并以真实测试结果作为完成依据。
