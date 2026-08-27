# Codex Coding Prompt：本地论文库查询解析、父子文档与深度分析证据检索重构

请在当前仓库中直接完成一次面向企业级落地的“本地论文检索与深度分析证据可靠性重构”。

仓库：

https://github.com/yiyabg/academic_research_agent

本 Prompt 编写时审计的远端基线提交为：

```text
168ef16ee6405006d4731cb94da626746fd482fb
完善local_research_library.py的结构
```

实际执行时必须以当前工作树和当前 HEAD 为准。先阅读 `AGENTS.md`、检查 `git status`、最近提交、相关代码和测试，再实施。若代码已继续演进，应以实际调用链调整文件位置，但不得跳过本 Prompt 的行为要求。保留用户已有修改，不执行破坏性 Git 操作，不提交、不推送。

本任务必须直接修改代码、创建 Alembic migration、补充测试、运行验证并根据结果修复；不要只输出设计建议。

## 一、先核验现状，不得凭文件名或旧结论猜测

开始修改前，用 `rg` 和实际代码阅读逐项确认以下现状，并在实施计划中用简短文字说明“已确认/已变化”：

1. `frontend/src/components/research/local-paper-library-workbench.tsx` 的普通搜索目前是否仍只发送 `query` 和 `limit`。
2. `LocalPaperSearchRequest` 是否已有 `author`、`doi`、`bibtex_type`、`year_from`、`year_to`，但没有把自然语言 query 解析为这些结构化字段。
3. `LocalPaper` 是否仍没有规范化的期刊/会议名称和关键词字段，而只在 `bibtex_entry` 中保存原始 BibTeX。
4. 同步逻辑是否仍只从 `entry.fields["year"]` 解析年份，没有在缺失时读取 Better BibTeX 的 `date`。
5. `LocalPaperSection` 是否已保存 `content`、`section_type`、`heading`、`page_number/page_end`、`document_version_id`，并作为父文档；`LocalPaperChunk` 是否通过 `section_id` 指向父文档，并作为 Qdrant 检索子块。
6. `local_papers` 是否仍重复保存 `abstract_text`、`introduction_text`、`conclusion_text`，同步时是否仍通过整篇正则再次抽取并截断这些文本。
7. 论文发现检索是否仍在 rerank 前调用严格的 `_cap_chunks_per_paper(... max_per_paper=2)`，并在输出时再限制每篇 2 个 evidence chunk。
8. `LocalPaperAnalysisOrchestrator._prepare()` 是否仍使用：

   ```python
   request.query or request.question
   ```

   并复用公共论文发现 `search()` 一次性处理所有已选 `paper_ids`。
9. 分析 Prompt 是否仍无条件加入摘要、引言、结论，并使用类似：

   ```python
   (evidence.parent_text or evidence.text)[:550]
   ```

   的父段落前缀截断。
10. `focused/comparative/comprehensive` 是否只保存到了 job，但没有真正控制逐篇证据策略、逐篇 Prompt 和综合 Prompt。
11. 分析证据清单是否缺少 `chunk_id`、`section_id`、`document_version_id` 等稳定追溯标识。
12. 最新提交是否已经完成 `local_library_routes/` 子路由拆分以及 staged/background 可恢复执行。若已完成，必须保留并复用，禁止再次重做这部分架构。

先输出一个不超过 12 条的实施计划，然后直接修改。除非出现会改变数据语义的真实阻塞，不要停下来询问已经在本 Prompt 中确定的选择。

## 二、问题定义与本次目标

当前存在三个彼此独立但相互放大的问题：

1. 用户在 query 中写“2026 年发表的 semantic communication 论文”时，“2026 年”仍作为向量/关键词相关性文本，而没有成为 PostgreSQL 硬过滤条件。
2. 深度分析已选择论文后，仍使用“论文发现检索”策略；原主题可能覆盖分析问题，跨论文 MMR 和每篇 2 chunk 上限也会让已选论文丢失或证据不足。
3. 父子文档已经存在，但模型输入只拿父段落开头的少量字符，命中的 child 甚至可能不在截断范围内；同时摘要/引言/结论的重复长字段占用上下文并形成第二套不一致的数据源。

本次目标是形成以下最小、明确、可审计的内部架构：

```text
用户自然语言 query
  -> 确定性查询解析（主题 + 高置信度硬约束）
  -> PostgreSQL 元数据预过滤
  -> 全库混合检索
  -> 论文级排序与多样性选择
  -> 返回论文列表

用户已选 paper_ids + analysis question
  -> 严格加载全部已选论文
  -> 使用 question 在每篇论文内独立检索 child chunks
  -> 回填命中 child 所在的 parent 上下文
  -> 按 token 预算选择证据
  -> 逐篇分析
  -> 跨论文综合
```

不要引入完整 Agentic RAG、多智能体、GraphRAG、Elasticsearch、新向量库或开放式反思循环。允许一次有界补充检索，但总检索轮数必须有限且可测试。

## 三、自然语言约束解析：单搜索框为主，结构化字段为权威

### 3.1 后端是唯一解析权威

新增一个职责单一、无 LLM 依赖的查询解析器，例如：

```text
backend/app/services/literature_research/local_paper_query_parser.py
```

实际命名按仓库规范调整。解析结果必须是显式 Pydantic/dataclass 模型，至少包含：

```text
raw_query
semantic_query
parsed_filters
effective_filters
filter_sources       # parsed | explicit
warnings
```

不得在前端复制正则或实现第二套 NLP。前端只发送原始 query 和用户显式填写的高级筛选值，后端完成解析、合并和校验。

### 3.2 第一版只自动应用高置信度条件

至少支持并测试：

- 精确年份：`2026年发表的...`、`发表于2026年的...`；
- 年份范围：`2024-2026年`、`2024至2026年`、`2024—2026`；
- 明确起止表达：`从2024年起`、`截至2026年`、`2024年及以后`、`2026年及以前`；
- DOI：标准 `10.xxxx/...` 及 `doi:`、`doi.org/` 形式；
- BibTeX 类型：只用受控别名映射，例如“期刊论文/article”“会议论文/inproceedings”；
- 作者：只有出现“作者为/作者/by”等明确槽位提示时才作为硬过滤；
- 期刊/会议：只有出现“发表于/期刊为/会议为/venue”等明确槽位提示时才作为硬过滤；
- BibTeX 关键词：只有出现“关键词为/关键词包含”等明确槽位提示时才作为硬过滤。

主题词默认仍属于 `semantic_query`。例如普通的 `semantic communication` 不能因为某篇 BibTeX 没有 keywords 就被错误排除。

解析器必须避免把以下内容误当成年份：

```text
6G
3D
GPT-4
IEEE 802.11
模型参数 2048
DOI 中的数字
```

将已识别的约束表达从 `semantic_query` 中删除并清理多余标点/空白。例如：

```text
raw_query:      2026年发表的 semantic communication 相关文章
semantic_query: semantic communication
effective:      year_from=2026, year_to=2026
```

### 3.3 显式字段优先且冲突必须可见

合并规则必须确定：

1. API 中显式非空字段优先于 query 自动解析值；
2. `year_from > year_to` 返回 422；
3. 自动解析与显式值冲突时，不静默选择，返回明确的解析 warning，且最终使用显式值；
4. 低置信度作者/venue 表达不得硬过滤，只保留在 `semantic_query` 或 warning 中；
5. 精确年份过滤自然排除 `publication_year IS NULL`，不得从 PDF copyright 猜测发表年份。

### 3.4 稳定 API 输出

为搜索响应增加类型明确的 `query_interpretation`，不要让前端依赖自由格式的 `trace`：

```json
{
  "raw_query": "...",
  "semantic_query": "...",
  "effective_filters": {
    "year_from": 2026,
    "year_to": 2026
  },
  "filter_sources": {
    "year_from": "parsed",
    "year_to": "parsed"
  },
  "warnings": []
}
```

检索审计记录同时持久化 raw query、semantic query、effective filters 和解析 warning。Qdrant embedding、PostgreSQL BM25 与 BGE reranker只能使用清理后的 `semantic_query`，不能继续使用带硬约束噪声的原句。

## 四、元数据规范化

### 4.1 年份

统一年份解析函数：

```text
year -> 若缺失则 date -> 仍缺失则 NULL
```

不得使用 `urldate`、PDF copyright 或文件时间替代发表年份。同步“源文件哈希未变化”的快速路径也必须更新规范化元数据，不能只有完整重建路径更新。

### 4.2 venue 与 keywords

当前模型若确实没有规范化字段，增加最小字段：

```text
venue: nullable text
keywords_json: JSONB list[str]
```

venue 仅从 `journal/journaltitle/booktitle` 的明确来源规范化；不要把 publisher、school、institution 混成期刊名。keywords 从 Better BibTeX 的 `keywords` 解析，去空、去重并保留可展示文本。

相应增加：

- SQLAlchemy 字段；
- Pydantic 请求/响应字段；
- Alembic migration；
- 同步新建、更新和 unchanged 快速路径；
- 元数据过滤测试。

不要为了本地规模引入 Elasticsearch 或新的全文索引扩展。现有 PostgreSQL 足够；只有已有部署默认支持时才使用现有索引能力，不要擅自启用新的数据库 extension。

## 五、摘要、引言、结论：以父 section 为唯一正文事实来源

### 5.1 已确定的数据选择

`LocalPaperSection` 是正文父文档的唯一事实来源，`LocalPaperChunk` 是检索子块。摘要、引言和结论必须按普通父子文档处理，并通过：

```text
section_type = ABSTRACT | INTRODUCTION | CONCLUSION
```

保留其结构语义，不再在 `local_papers` 中保存另一份截断长文本。

必须完成：

1. 新分析链路不再读取 `paper.abstract_text/introduction_text/conclusion_text`；
2. 同步不再运行整篇正则 `extract_structured_sections()` 来复制这三段文本；
3. 不再写入这三个数据库字段；
4. 创建 Alembic migration 删除冗余列；
5. `LocalPaperRead` 如需保持 OpenAPI 兼容，可暂时保留这三个 nullable 字段并标注 deprecated、默认返回 `null`，但不得再作为分析证据或数据库事实来源；
6. 用 `rg` 确认没有运行时调用方后，删除仅服务于旧同步/旧深度分析的死代码和测试；如果仍有兼容调用方，则改为按 active document version 从 `LocalPaperSection` 派生，不得恢复重复持久化。

数据库 migration 前必须用测试证明当前 v7 active document versions 已有带 `section_type` 的父 section。若旧索引缺失，明确要求执行现有增量重建，不要把旧的三个截断字段回填成完整父文档。

### 5.2 Qdrant 与 PostgreSQL 的边界保持不变

Qdrant 继续只保存 child embedding 和定位 payload；PostgreSQL 保存完整 parent section。不得把完整 parent 文本重复写入 Qdrant，也不得建立第二个 collection。

## 六、抽取公共 chunk 检索核心，但建立两种不同选择策略

不要复制 Dense + BM25 + RRF + BGE rerank 实现。把当前 `LocalPaperLibraryService.search()` 中可复用的底层 chunk 检索抽成一个小型内部组件，例如：

```text
LocalPaperChunkRetriever
```

其职责仅为：

```text
给定 owner/library、paper scope、active document versions 和 query
-> PostgreSQL BM25 + Qdrant dense
-> RRF
-> 加载 child + parent + paper
-> substantive filtering
-> BGE rerank
-> 返回带完整 lineage 和分数的 ranked chunks
```

它不得决定最终论文多样性，也不得决定分析 Prompt。

### 6.1 论文发现策略（discovery）

目标是从全库选论文，保留论文级多样性。

将严格的“每篇最多 2 个 chunk 后再 rerank”改为软配额：

1. 按 RRF 顺序先为尽可能多的不同论文各保留 1 个 chunk；
2. rerank 预算仍有剩余时，再按全局 RRF 顺序补入高分 chunk；
3. 使用可配置软上限防止单篇长论文垄断，但当候选论文不足时允许填满剩余 rerank 预算；
4. BGE rerank 后按论文聚合相关性，至少使用最佳 chunk；如使用第二最佳 chunk，只能作为小幅稳定性加成，不能让长论文凭 chunk 数获益；
5. 最终 MMR 只在“每篇论文的代表向量/代表 chunk”之间执行；
6. 搜索结果用于预览的 evidence 仍可保持每篇 1–2 条，这只是 UI 证据数，不能再与 rerank 候选配额共用同一个配置含义。

删除或重命名当前含义重叠的配置，使名称明确区分：

```text
discovery rerank pool soft cap
discovery preview evidence count
analysis evidence token budget/max chunks
```

不要保留多套无人使用的旧配置。

### 6.2 已选论文的证据策略（analysis evidence）

目标是在每篇指定论文中找出回答 `question` 的正文证据，不做跨论文 MMR。

新增独立服务，例如：

```text
LocalPaperEvidenceRetriever
```

要求：

1. 输入必须包含 `paper_ids`、`question`、可选 `query_context`、`mode`；
2. `question` 始终是主检索 query；`query_context` 只能用于消解“上述论文/这些论文”等指代或作为第二个有界 query variant，绝不能覆盖 question；
3. 对每个 `paper_id` 独立限定 PostgreSQL/Qdrant scope；
4. 每篇论文都获得独立召回机会，不允许某篇论文因另一篇分数更高而消失；
5. 不运行跨论文 MMR；
6. 先召回 child，再回填其 parent；
7. 证据选择按 token budget，而不是固定“每篇恰好 2 个 chunk”；
8. 使用相关性、`section_id` 多样性、页码/内容去重做有界贪心选择；高相关的多个互补段落可以来自同一篇论文；重复或同一 parent 的近重复内容不得挤占预算；
9. 设置合理的分析上限，例如默认最多 6 个 child、每篇总证据约 3000–4000 tokens。以现有 BGE tokenizer/已存 token_count 实际计数，不得按字符粗暴估算；
10. 第一轮无实质证据时最多执行一次补充检索。补充检索可使用 `question + query_context` 或明确的受控同义词扩展；不得调用开放式 Agent 循环；
11. 若仍无证据，产生明确的 `INSUFFICIENT_LOCAL_EVIDENCE`，保存已执行 query 和分数摘要，不调用模型编造答案。

### 6.3 parent 上下文必须围绕命中 child

新证据项至少包含：

```text
paper_id
document_version_id
section_id
chunk_id
section_type
section_heading
page_number
page_end（如有）
child_text
context_text
rerank_score
retrieval_pass
```

构造 `context_text` 时：

1. `child_text` 必须始终完整保留；
2. 在 parent 中定位 child，并围绕命中位置按 token budget 向前后扩展；或按同 section 的相邻 child 扩展；
3. 禁止直接取 `parent_text[:N]`；
4. 若无法在 parent 中定位 child，安全回退到完整 child，而不是取 parent 开头；
5. 引用页码以 child locator 为准，不得把跨页 parent 的起始页冒充所有证据页。

普通 `/search` 响应不再需要把完整 `parent_text` 发送到浏览器。为兼容可暂时保留字段但默认不填充；后端分析必须直接从 PostgreSQL 获取 parent。

## 七、接入 staged/background 分析编排

保留最新提交已经实现的 staged/background、超时、重试、持久化、错误脱敏、路由拆分和 Celery 恢复逻辑。只替换证据准备与 Prompt 输入。

### 7.1 修复 `_prepare()`

必须满足：

1. 如果请求有 `paper_ids`，先按 owner、library、`INDEXED`、active document version 严格加载全部 ID，并保持用户选择顺序；不存在或无权访问的 ID 返回明确领域错误，不能静默丢弃；
2. 如果没有 `paper_ids`，才使用 `request.query` 运行 discovery 找论文；
3. 对最终每篇论文调用新的 analysis evidence 检索器，主 query 为 `request.question`；
4. 为每篇选中论文创建持久化 stage，即使该篇证据不足，也要以明确状态进入最终报告；
5. `job.evidence_json` 和 stage `evidence_json` 保存稳定 lineage、证据选择分数、实际 query variants 和 active document version；
6. 不再把 `LocalPaperRead` 的搜索预览结果直接序列化成模型证据包。

### 7.2 三种 mode 必须真正不同

`job.mode` 必须传入证据策略、逐篇 Prompt 和综合 Prompt：

- `focused`：逐篇直接回答用户问题；每个结论引用证据句柄；没有证据就明确“未检索到”；不要强制输出背景、创新、方法、结果、局限六大部分。
- `comparative`：逐篇提取同一问题下可比较字段，再生成横向对比表；缺失值必须显式标注。
- `comprehensive`：在 token budget 内覆盖背景/方法/实验/结果/结论/局限等主要 section 类型，但仍由 child 命中和 parent 上下文提供证据，不读取重复长字段。

删除当前 mode 仅记录但行为相同的情况，并为三个 mode 建立独立测试。

### 7.3 引用约束

给每个证据项分配本篇内稳定句柄，例如 `[E1]`、`[E2]`。模型只能引用这些句柄。逐篇结果生成后必须校验：

- 引用句柄存在；
- 句柄对应当前 paper；
- 最终渲染可映射到 title、page、chunk_id/section_id；
- 未引用或非法引用不得伪装成已证实结论。

不要依赖第三方代理一定支持复杂 JSON Schema；如果当前 Responses 兼容层可靠支持结构化输出，可复用；否则使用简单、可校验的引用句柄协议，不要为此重构整个 LLM provider。

跨论文综合只输入逐篇分析结果和必要的 evidence index，不重新塞入全部原始 parent 文本。现有 staged 单次模型调用小于 120 秒的预算必须保持。

## 八、前端交互：不要铺满筛选框

保留一个主搜索框。增加以下最小交互：

1. 搜索完成后，根据 `query_interpretation` 显示只读条件 chips，例如：

   ```text
   主题：semantic communication
   年份：2026
   解析来源：query
   ```

2. 提供默认折叠的“高级筛选”区域，只放当前后端真正支持的字段：年份范围、作者、DOI、类型、venue、keyword；不要默认铺开所有输入框；
3. 高级筛选值作为显式字段发送，并按后端规则覆盖自动解析；
4. 显示解析 warning，便于用户发现作者/期刊歧义；
5. 为 API body 建立明确 TypeScript 类型，替换 `Record<string, unknown>`；
6. 导出必须复用当前搜索的 raw query 和显式 filters，不能只发送 query 导致导出范围变化；
7. 搜索数量文案必须真实。如果 `total` 仍表示实际返回数量，就显示“返回 N 篇”，不要写“总命中 N 篇”；只有真正计算了过滤后候选总数才能称“总命中”；
8. 不改现有页面整体视觉风格，不新增复杂筛选管理页。

## 九、建议的最小文件边界

以实际项目结构为准，但最终职责至少应清楚：

```text
local_paper_query_parser.py
    自然语言硬约束解析、显式字段合并、解释结果

local_paper_retrieval.py
    可复用的 scoped Dense/BM25/RRF/rerank chunk 核心

local_paper_library.py
    同步和公开 discovery search；不承担分析证据策略

local_paper_evidence.py
    固定 paper scope 的逐篇证据选择、parent 上下文、token budget

local_paper_analysis_orchestrator.py
    staged/background 状态编排，调用 evidence retriever 和 model gateway

paper_analysis_report_service（或现有等价位置）
    mode-specific prompts、引用校验、综合和 Markdown/OPML 渲染
```

不要为了形式创建空类。若已有组件可承担职责，复用并改名；但禁止让新的 evidence route 再调用面向 discovery 的公共 `search()` 作为实现捷径。

最新 `local_library_routes/` 子路由结构已经完成，不再拆一次，也不得改变公共 URL。

## 十、数据库迁移与兼容性

创建一个基于执行时实际 Alembic head 的 migration，至少处理：

- 新增规范化 `venue`、`keywords_json`（若核验后确实不存在）；
- 删除冗余 `abstract_text`、`introduction_text`、`conclusion_text` 持久化列；
- 添加当前过滤真正需要且无需新 extension 的索引；
- downgrade 至少能恢复表结构。

要求：

1. migration 可在已有数据上升级；
2. 不删除 `LocalPaperSection/Chunk` 或 active document versions；
3. 不重建 Qdrant collection；
4. 只有 embedding/chunk payload 真正变化时才提升 ingestion version；本任务的元数据字段和证据选择变化本身不应触发全库重嵌入；
5. migration 后通过现有“手动同步/增量同步”从 BibTeX 更新 year/date、venue、keywords；
6. PostgreSQL 是元数据、parent 正文和恢复状态的事实来源。

## 十一、测试要求

使用 fake embedding/index/reranker/model provider，不调用付费服务。至少覆盖：

### 11.1 查询解析与硬过滤

1. `2026年发表的 semantic communication` 被解析为 `semantic_query=semantic communication` 和精确年份过滤；
2. 2024/2025/NULL 年份论文不会进入 2026 结果；
3. 年份范围和起止表达正确；
4. `6G`、`3D`、`GPT-4`、`IEEE 802.11`、DOI 数字不被误解析为年份；
5. 显式字段覆盖自动解析并产生可见 source/warning；
6. `year_from > year_to` 返回校验错误；
7. Qdrant、BM25、reranker实际收到清理后的 semantic query；
8. BibTeX `year` 缺失时从 `date` 获取年份，但不读取 `urldate`；
9. venue/keywords 新建、unchanged 更新路径和过滤正确；
10. 导出与页面搜索使用同一组有效约束。

### 11.2 discovery 多样性

1. 多篇论文候选时，rerank pool 至少先为不同论文保留一个候选；
2. 某篇论文确有多个强 chunk 且预算有余时，软配额允许额外 chunk；
3. 长论文不能因 chunk 数量获得不合理文档分数；
4. 论文级 MMR 仍能产生多样结果；
5. UI preview evidence 数和 rerank candidate 数是两个独立配置/概念。

### 11.3 深度分析证据

1. 同时传入 `query="vla"` 和 `question="上述论文采用的 LLM backbone 分别是什么"` 时，证据主检索使用 question，不使用 `query or question`；
2. 传入 5 个合法 `paper_ids` 时，5 篇都创建 stage 并获得独立检索，不因全局 MMR 消失；
3. 某一篇的高分 chunk 不会占用另一篇的证据预算；
4. 有多个互补高相关 chunk 时可选择超过 2 个，但不超过 token/max-chunk 上限；
5. parent context 始终包含完整命中 child；构造 child 位于 parent 中后部的案例，证明不再截取 parent 前缀；
6. 每条证据包含 paper/version/section/chunk/page lineage；
7. 无证据时记录 `INSUFFICIENT_LOCAL_EVIDENCE`，最终报告明确列出，模型不编造；
8. focused、comparative、comprehensive 的检索/Prompt/输出结构不同；
9. 模型输出中的非法 `[E#]` 引用被识别并不会作为有效证据发布；
10. 刷新、重试、PARTIAL、staged/background、HTTP 524 脱敏等最新已有测试继续通过。

### 11.4 父子文档与 migration

1. ABSTRACT/INTRODUCTION/CONCLUSION 作为普通 `LocalPaperSection` 并拥有 child；
2. 新分析路径不访问三列重复长文本；
3. 普通搜索不向前端发送完整 parent 文本；
4. Alembic `upgrade head` 成功；在项目可行的测试数据库流程中验证 downgrade/upgrade；
5. 当前 API URL、鉴权和 owner scope 不回归。

## 十二、验证命令

按仓库实际工具执行，至少包括：

```bash
cd backend
uv sync --dev
uv run pytest -q tests/unit/literature_research
uv run ruff check app tests
uv run ruff format --check app tests
uv run ty check
uv run alembic upgrade head

cd ../frontend
bun install --frozen-lockfile
bun run test:run
bun run type-check
bun run lint
bun run format:check
```

若完整测试受当前环境的外部服务或既有无关错误阻断：

1. 先运行与本任务相关的精确测试；
2. 记录完整失败命令和首个根因；
3. 不得把环境失败写成“测试通过”；
4. 不得为了让测试收集成功而修改生产依赖，除非依赖本身就是已证实的仓库缺陷且修改范围得到测试证明。

## 十三、明确不做

本任务不要实现：

- 完整 Agentic RAG 或无限检索反思循环；
- 多智能体；
- GraphRAG；
- Elasticsearch；
- 第二个 Qdrant collection；
- parent 全文向量化副本；
- 依赖 LLM 的默认 query 硬约束解析；
- 新的前端管理后台；
- 对已经完成的 staged/background、路由拆分、错误规范化做无关重写；
- 与本地论文检索/证据分析无关的全系统 RAG 重构。

## 十四、验收标准

完成后必须满足：

1. 用户只用单搜索框输入“2026年发表的 semantic communication”，2024/2025/NULL 年份结果违反率为 0；
2. 响应和审计中能看到 raw query、semantic query、effective filters 及来源；
3. 搜索前端不需要铺开大量筛选框，但用户可以在折叠高级区纠正自动解析；
4. 摘要、引言、结论不再作为 `local_papers` 的重复正文长字段参与新分析；
5. Qdrant 子块 + PostgreSQL 父 section 的父子结构真正用于模型证据；
6. 模型输入始终包含命中的完整 child，不会只拿 parent 开头；
7. discovery 保持论文多样性，analysis 则保证每篇已选论文独立检索；
8. 每篇分析证据不再固定为 2 个，而是在相关性、多样性和 token budget 下自适应选择；
9. 已选 5 篇论文不会因跨论文 MMR 静默减少；
10. evidence query 使用 question，原主题只作上下文；
11. focused/comparative/comprehensive 三种模式有真实行为差异；
12. 每条发布结论能回溯到 `paper_id + document_version_id + section_id + chunk_id + page_number`；
13. 公共 URL、鉴权、staged/background 恢复、错误脱敏和下载功能不回归；
14. migration、后端测试和前端测试通过，或对真实外部阻塞作准确说明。

## 十五、最终交付报告

最终回复必须包含：

1. 修改前确认的根因；
2. 最终两种内部检索策略及为何不需要完整 Agentic RAG；
3. query 自动解析规则和歧义处理；
4. 父子文档如何成为唯一正文来源；
5. discovery 软配额与 analysis 自适应证据预算的实现；
6. 修改文件列表；
7. Alembic migration 内容；
8. 新增/删除/重命名配置；
9. 测试命令与逐项真实结果；
10. Docker 重新构建、迁移、增量同步和启动命令；
11. 仍存在的边界，例如 PDF 文本层/Docling 未解析出具体模型名时只能明确报告证据不足。

不要只给建议，必须完成可运行实现，并以实际测试结果作为完成依据。
