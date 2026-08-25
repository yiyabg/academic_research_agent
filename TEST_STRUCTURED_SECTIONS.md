# 结构化段落提取功能测试指南

## 功能概述

本次修复为本地论文库添加了结构化段落提取功能，在同步时自动提取每篇论文的：
- **Abstract（摘要）**：最多1500字
- **Introduction（引言前两段）**：最多800字  
- **Conclusion（结论）**：最多1000字

这些结构化段落将用于：
1. **深度分析思维导图**：提供更完整的论文上下文
2. **文献问答**：优先引用摘要/结论中的核心论断
3. **跨文献对比**：基于核心观点而非碎片化chunk

---

## 已完成的修复

### 1. 数据库Schema更新 ✅
- 新增3个TEXT字段：`abstract_text`, `introduction_text`, `conclusion_text`
- 迁移文件：`0045_add_structured_sections.py`
- 迁移状态：已应用（`0045_add_structured_sections (head)`）

### 2. 结构化提取逻辑 ✅
- 新增函数：`extract_structured_sections(pages) -> dict`
- 支持中英文论文的段落识别
- 正则匹配：Abstract、Introduction、Conclusion标题
- 智能截断：避免超长文本

### 3. 同步流程集成 ✅
- 在`run_sync()`中调用结构化提取
- 新建和更新论文时都会填充这3个字段
- 与现有chunk提取并行，互不影响

### 4. API Schema更新 ✅
- `LocalPaperRead`新增3个可选字段
- `_paper_read()`方法返回结构化段落
- 前端可通过search/mindmap接口获取

### 5. Mindmap服务优化 ✅
- `_build_rich_evidence()`优先使用结构化段落
- 格式：Abstract(800字) + Introduction(600字) + Conclusion(600字) + 补充chunks
- 单篇论文上下文从600字×8提升到约3500字

### 6. LLM Prompt优化 ✅
- 深度分析：教授级专家人设 + 6维度分析框架
- Q&A：新增"技术分歧"、"研究缺口"、"实用建议"维度
- 强调证据原则：区分"文献明确指出"vs"合理推断"

### 7. 服务重启 ✅
- Backend已重启，代码已加载
- CPU worker已重启
- 数据库迁移已应用

---

## 待诊断问题：思维导图无输出 ⚠️

### 问题现象
- 用户点击"生成思维导图"后，前端显示"分析中..."但始终没有结果
- 后端日志显示LLM调用已启动（`chat gpt-5.5`）但**没有任何后续响应**
- HTTP请求未返回任何状态码（200/500等）

### 日志证据
```
12:44:26.352 POST /api/v1/research/local-library/mindmap
12:44:26.360   BEGIN;
12:44:26.361   SELECT users...  # 数据库查询成功
12:44:26.381   SELECT local_papers...  # 检索到论文
12:44:26.452   agent run
12:44:26.453     chat gpt-5.5  # LLM调用开始
[之后无任何日志，HTTP响应从未发送]
```

### 根本原因推测
1. **LLM超时**：没有设置timeout，LLM提供商可能挂起
2. **响应太大**：深度分析生成的Markdown可能超过某个限制
3. **异常被静默吞没**：Agent库的异常未传播到FastAPI层

### 解决方案

#### 方案A：添加超时保护（推荐）
```python
# 在 paper_mindmap_service.py 的 _deep_analyze_via_llm 中
agent: Agent[str] = Agent(
    model=build_llm_model(),
    system_prompt=system_prompt,
    timeout=180,  # 3分钟超时
)

try:
    result = await asyncio.wait_for(agent.run(user_prompt), timeout=180)
except asyncio.TimeoutError:
    return "# 分析超时\n\n生成深度分析需要较长时间，请稍后重试或减少论文数量。"
```

#### 方案B：降级到简单模式
```python
async def analyze(self, *, papers: list[LocalPaperRead], question: str, output_format: str):
    if not papers:
        return "# 无论文可分析", "mindmap.md"
    
    evidence_text = self._build_rich_evidence(papers)
    
    if llm_is_configured():
        try:
            markdown_map = await asyncio.wait_for(
                self._deep_analyze_via_llm(question=question, evidence=evidence_text, papers=papers),
                timeout=180
            )
        except (asyncio.TimeoutError, Exception) as e:
            # 降级到简单格式
            markdown_map = self._build_fallback_markdown(papers, question)
    else:
        markdown_map = self._build_fallback_markdown(papers, question)
    
    # 转换为OPML...
```

#### 方案C：检查LLM配置
```bash
# 在backend容器中测试LLM连接
docker exec academic_research_agent_backend python -c "
from app.services.llm_provider import llm_is_configured, build_llm_model
from pydantic_ai import Agent

print('LLM configured:', llm_is_configured())
if llm_is_configured():
    model = build_llm_model()
    print('Model:', model)
    
    # 测试简单调用
    agent = Agent(model=model, system_prompt='You are a helpful assistant.')
    result = agent.run_sync('Say hello')
    print('Test result:', result.output[:50])
"
```

---

## 测试步骤

### 测试1：验证数据库字段已添加
```bash
docker exec academic_research_agent_db psql -U postgres -d app -c "
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_name='local_papers' AND column_name IN ('abstract_text', 'introduction_text', 'conclusion_text');
"
```

**期望输出**：
```
    column_name     | data_type
--------------------+-----------
 abstract_text      | text
 introduction_text  | text
 conclusion_text    | text
```

### 测试2：重新同步文献库
1. 访问前端：http://localhost:3000
2. 登录管理员账号
3. 找到"本地论文库（Zotero）"卡片
4. 点击"手动同步/增量重建"
5. 等待同步完成（观察indexed数量）

### 测试3：检查结构化段落是否提取成功
```bash
docker exec academic_research_agent_db psql -U postgres -d app -c "
SELECT 
    title,
    CASE WHEN abstract_text IS NOT NULL THEN length(abstract_text) ELSE 0 END as abstract_len,
    CASE WHEN introduction_text IS NOT NULL THEN length(introduction_text) ELSE 0 END as intro_len,
    CASE WHEN conclusion_text IS NOT NULL THEN length(conclusion_text) ELSE 0 END as conclusion_len
FROM local_papers 
WHERE status='INDEXED'
LIMIT 5;
"
```

**期望输出**：至少部分论文的abstract_len/intro_len/conclusion_len > 0

### 测试4：检索并验证API返回结构化字段
```bash
# 通过API检索论文
curl -X POST http://localhost:3000/api/research/local-library/search \
  -H "Content-Type: application/json" \
  -H "Cookie: access_token=YOUR_TOKEN" \
  -d '{"query":"VLA","limit":1}' | jq '.items[0] | {title, abstract_text, introduction_text, conclusion_text}'
```

**期望输出**：JSON中包含非空的abstract_text等字段

### 测试5：诊断思维导图超时问题
```bash
# 1. 检查LLM配置
docker exec academic_research_agent_backend env | grep -E "LLM_BASE_URL|AI_MODEL|ANTHROPIC|OPENAI"

# 2. 测试LLM连接
docker exec academic_research_agent_backend python -c "
from app.services.llm_provider import llm_is_configured, build_llm_model
print('LLM configured:', llm_is_configured())
if llm_is_configured():
    model = build_llm_model()
    print('Model info:', model)
"

# 3. 监控后端日志
docker logs -f academic_research_agent_backend
# 然后在另一个终端触发mindmap请求，观察日志
```

### 测试6：手动触发思维导图生成（带超时）
前端操作：
1. 输入检索词："VLA"
2. 论文数：3（减少数量避免超时）
3. 输出格式：Markdown
4. 点击"生成思维导图"
5. 等待3分钟，观察是否有输出

---

## 预期效果对比

### 修复前
- **分析上下文**：8个chunk × 600字 = 4800字（碎片化）
- **分析质量**：频繁标注"摘录不足，无法确认"
- **LLM Prompt**：简单任务描述
- **思维导图输出**：❓ 超时/无响应

### 修复后
- **分析上下文**：Abstract(800) + Intro(600) + Conclusion(600) + 3 chunks(1200) ≈ 3200字/篇（结构化）
- **分析质量**：基于论文核心论断，深度分析
- **LLM Prompt**：教授级专家人设 + 6维度分析框架
- **思维导图输出**：待验证（需要添加超时保护）

---

## 紧急修复：添加超时保护

如果测试5确认LLM配置正确但仍超时，立即应用方案A：

```bash
# 1. 修改 paper_mindmap_service.py
vim backend/app/services/literature_research/paper_mindmap_service.py

# 2. 在 _deep_analyze_via_llm 方法中添加：
import asyncio

async def _deep_analyze_via_llm(self, *, question: str, evidence: str, papers: list[LocalPaperRead]) -> str:
    system_prompt = DEEP_ANALYSIS_SYSTEM_PROMPT.replace("{topic}", question)
    agent: Agent[str] = Agent(
        model=build_llm_model(),
        system_prompt=system_prompt,
    )
    user_prompt = (
        f"请对以下 {len(papers)} 篇论文进行深度分析，研究主题：「{question}」\n\n"
        f"论文摘录如下（每篇包含若干页面片段）：\n\n"
        f"{evidence}\n\n"
        "请按照系统提示的格式输出完整的深度分析报告。"
    )
    
    try:
        result = await asyncio.wait_for(agent.run(user_prompt), timeout=180.0)
        return result.output
    except asyncio.TimeoutError:
        return (
            f"# 分析超时\n\n"
            f"为 {len(papers)} 篇论文生成深度分析需要较长时间（>3分钟），"
            f"LLM调用超时。\n\n建议：\n"
            f"1. 减少论文数量（当前{len(papers)}篇，建议≤5篇）\n"
            f"2. 稍后重试\n"
            f"3. 检查LLM服务状态"
        )
    except Exception as e:
        return f"# 分析失败\n\n生成过程中遇到错误：{str(e)}"

# 3. 重启backend
docker restart academic_research_agent_backend
```

---

## 成功标准

- [x] 数据库迁移已应用
- [x] 结构化提取函数已实现
- [x] 同步流程已集成
- [x] API Schema已更新
- [x] Mindmap服务已优化
- [x] LLM Prompt已优化
- [ ] 同步后论文有abstract_text等字段（待测试2验证）
- [ ] 思维导图能成功生成输出（待测试6验证）
- [ ] 深度分析质量显著提升（待用户反馈）

---

**下一步操作**：
1. 执行测试1-4验证结构化提取成功
2. 执行测试5诊断思维导图超时原因
3. 根据诊断结果应用超时保护或修复LLM配置
4. 重新测试思维导图生成
5. 收集用户反馈，评估分析质量提升

**联系方式**：如有问题，查看 `FIXES_CHANGELOG.md` 或后端日志
