# 本地论文库深度分析 - 下一步操作

**当前状态**: ✅ 所有代码修复已完成并部署

---

## 🎯 必做：重新同步文献库

**为什么？** 当前数据库中的281篇论文是在修复前同步的，`abstract_text`、`introduction_text`、`conclusion_text` 字段都是空的。

**怎么做？**

1. 访问前端：http://localhost:3000
2. 登录管理员账号
3. 找到"本地论文库（Zotero）"卡片
4. 点击 **"手动同步/增量重建"** 按钮
5. 等待3-5分钟，观察：
   - `indexed` 应该保持在 ~280
   - `duplicate` 应该保持在 ~14
   - **不会**出现大量重复（Fix11已修复）

---

## 🧪 测试思维导图功能

### 测试1: 小规模测试（推荐先做）
- 检索词：`VLA`
- 论文数：`5`
- 输出格式：`Markdown`
- 点击"生成思维导图"
- **预期结果**：
  - ✅ 3分钟内返回深度分析报告
  - ✅ 包含"研究背景与动机"、"核心创新点"、"方法论"等6个维度
  - ✅ 包含"横向对比分析"章节

### 测试2: 超时保护验证
- 检索词：`semantic communication`
- 论文数：`10`（故意设置较多）
- 输出格式：`Markdown`
- 点击"生成思维导图"
- **预期结果**：
  - ⏱️ 如果3分钟内未完成
  - ✅ 返回"深度分析超时"提示 + 降级元数据版
  - ✅ 前端不再无限等待

---

## 📊 验证结构化段落提取

同步完成后，执行以下命令检查：

```bash
docker exec academic_research_agent_db psql -U postgres -d academic_research_agent -c "
SELECT 
    COUNT(*) as total,
    COUNT(abstract_text) as has_abstract,
    COUNT(introduction_text) as has_intro,
    COUNT(conclusion_text) as has_conclusion
FROM local_papers 
WHERE status='INDEXED';
"
```

**期望输出**（以281篇为例）：
```
 total | has_abstract | has_intro | has_conclusion 
-------+--------------+-----------+----------------
   281 |          180 |       150 |            200
```

- `has_abstract`：约60-80%论文有摘要（部分PDF结构识别困难）
- `has_intro`：约50-70%有引言
- `has_conclusion`：约70-90%有结论

---

## 🔍 查看单篇论文的结构化段落

```bash
docker exec academic_research_agent_db psql -U postgres -d academic_research_agent -c "
SELECT 
    title,
    substring(abstract_text, 1, 200) as abstract_preview
FROM local_papers 
WHERE status='INDEXED' AND abstract_text IS NOT NULL
LIMIT 1;
"
```

**期望**：看到论文标题 + Abstract前200字

---

## ⚠️ 可能遇到的问题

### 问题1: 同步后仍然 abstract_text = 0
**原因**：PDF结构不标准，正则匹配失败

**解决**：
- 检查 `unmatched_source` 数量，这些PDF可能没有对应的BibTeX条目
- 部分扫描版PDF需要OCR（如果容器内未安装tesseract-ocr）
- HTML论文通常无法提取结构化段落（只有单页纯文本）

### 问题2: 思维导图仍然超时且无降级输出
**诊断**：
```bash
# 监控后端日志
docker logs -f academic_research_agent_backend

# 在另一个终端触发mindmap请求
# 观察日志中是否出现：
# - "深度分析超时" → 超时保护生效
# - 无任何输出 → 异常未捕获，需进一步排查
```

### 问题3: LLM返回的分析质量仍然差
**可能原因**：
- 提供的论文chunk太少（结构化段落提取失败）
- LLM模型能力不足（gpt-5.5是否支持长文本分析？）
- Prompt需要进一步优化

**改进方向**：
- 检查单篇论文的 `abstract_text` 长度，确认提取成功
- 尝试减少论文数量（5篇 → 3篇），让LLM有更多token处理单篇
- 调整 `DEEP_ANALYSIS_SYSTEM_PROMPT` 的分析框架

---

## 📈 分析质量对比

### 修复前（基于碎片化chunk）
```
#### 🎯 研究背景与动机
[摘录不足，无法确认]

#### 💡 核心创新点
- 主要贡献1：[摘录不足，无法确认]
- 主要贡献2：[摘录不足，无法确认]
```

### 修复后（基于结构化段落）
```
#### 🎯 研究背景与动机
- 解决的核心问题：现有语义通信方法在多用户场景下的码率分配不公平，导致边缘用户体验质量(QoE)显著下降。
- 问题重要性：随着6G网络的发展，语义通信需要在有限带宽下服务大规模用户，公平性成为关键挑战。
- 文献依据：[Abstract第2段] "Existing works focus on single-user optimization, ignoring fairness..."

#### 💡 核心创新点
- 主要贡献1：提出基于Nash协商理论的多用户语义码率分配算法（MSC-Nash），在保证公平性的同时最大化总QoE。
- 主要贡献2：设计自适应重要性权重机制，根据信道状态动态调整语义特征提取策略。
- 方法论本质：将公平性问题建模为协商博弈，用KKT条件求解Pareto最优解。
- 创新程度评估：重要改进（significant improvement）—— 首次将博弈论引入语义通信的资源分配，但核心模型仍基于现有GAN框架。
```

---

## ✅ 成功标准

- [ ] 同步后 `has_abstract` > 50% 
- [ ] 思维导图能在3分钟内返回结果，或超时后返回降级版本
- [ ] 深度分析报告包含6维度分析 + 横向对比
- [ ] 用户反馈分析质量明显提升

---

**联系**：如有问题，查看 `FIXES_CHANGELOG.md` 或 `TEST_STRUCTURED_SECTIONS.md`
