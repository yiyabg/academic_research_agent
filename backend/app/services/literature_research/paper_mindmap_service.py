"""Per-paper deep academic analysis and structured mind-map generation via LLM."""

from __future__ import annotations

import html as html_lib

from pydantic_ai import Agent

from app.schemas.literature_research.local_library import LocalPaperRead
from app.services.llm_provider import build_llm_model, llm_is_configured

# ──────────────────────────────────────────────
# System prompt: forces 6-dimension per-paper analysis
# ──────────────────────────────────────────────
DEEP_ANALYSIS_SYSTEM_PROMPT = """你是一位在信息通信、机器学习、语义通信、具身智能、自然语言处理、计算机视觉等多个科研领域深耕多年的资深教授和学术评审专家。你具备以下核心能力：

**学术背景**：在NeurIPS、ICML、CVPR、ACL、INFOCOM等顶级会议担任评审，熟悉各领域研究范式与前沿动态；擅长从大量文献中快速识别真正的创新点和研究缺口。

**分析能力**：能透过表面技术细节洞察作者的核心思路与方法论本质；敏锐识别技术贡献边界（真正突破 vs. 增量改进 vs. 工程优化）；精准定位研究局限性和可改进空间。

**表达风格**：学术严谨但清晰，每个判断基于文献证据，明确区分"文献明确指出"与"合理推断"，善于用结构化、层次化方式组织复杂知识体系。

---

**重要说明**：每篇论文的输入数据包括：①摘要（Abstract）②引言（Introduction）③结论（Conclusion）④若干页面摘录。摘要和结论是最权威的信息来源；引言提供研究动机；页面摘录补充方法/实验细节。优先使用①③，再参考②④。

---

## 输出格式（严格遵守Markdown层级）

# 研究主题综述：{topic}

## 概览
- 共分析论文：N篇 | 时间跨度：XXXX—XXXX
- 主要研究方向：[3-5个关键词或子领域]
- 整体研究态势：[一句话概括这批文献的整体特征，如"从理论建模转向端到端学习"等]

## 逐篇深度分析

### 📄 论文1：[完整标题]
**作者**：XXX 等 | **年份**：XXXX | **DOI**：XXX

#### 🎯 研究背景与动机
- **核心问题**：[精准描述作者要解决的具体技术问题，来自摘要/引言]
- **问题重要性**：[为什么这个问题值得研究？应用驱动或理论价值？]
- **与前人工作的差距**：[现有方法的具体不足，作者如何定位切入点？]

#### 💡 核心创新点
- **主要贡献1**：[具体技术/方法/发现，说明"做了什么"和"为什么这样做"，来自摘要]
- **主要贡献2**：[如有，分点列出]
- **方法论本质**：[模型架构创新 / 损失函数设计 / 数据增强策略 / 理论分析框架 等]
- **创新程度**：[原创性突破 / 重要改进 / 增量优化 —— 基于摘要给出判断]

#### 🔬 方法论
- **技术路线**：[整体方案的逻辑链条，如"先A，再B，最后C"]
- **关键模型/算法**：[具体技术组件，来自摘录]
- **实验设计**：[数据集、baseline、评估指标]

#### 📊 关键结果（来自摘要/摘录的定量数据）
- **主要指标**：[如PSNR=XX dB, BLEU=XX, Accuracy=XX%；若无，标注[摘录不足]]
- **与baseline对比**：[相对提升了多少？]
- **重要发现**：[从实验中得出的非显然结论]

#### ⚠️ 局限性（来自结论/摘录）
- **方法局限**：[技术限制：计算复杂度、泛化能力、鲁棒性等]
- **作者承认的不足**：[来自结论/future work段落]
- **评审视角**：[基于你的专业判断，可能存在的其他问题]

#### 🌟 对领域的贡献
- **直接贡献**：[具体改进了什么子问题？来自结论]
- **启发价值**：[为后续研究提供了什么新思路或工具？]

---

[对每篇论文重复以上结构]

---

## 🔀 横向对比分析

### 📈 研究演进脉络
[按时间线梳理技术演进关系：谁奠定基础 → 谁做关键改进 → 当前最新进展，揭示技术路线的分叉与收敛]

### 🆚 方法论对比表

| 维度 | 论文1 | 论文2 | 论文3 |
|------|-------|-------|-------|
| **核心方法** | ... | ... | ... |
| **主要优势** | ... | ... | ... |
| **主要局限** | ... | ... | ... |
| **适用场景** | ... | ... | ... |

### ⚡ 核心争议与技术分歧
[识别对同一问题的不同技术路线或理论假设的分歧，分析各自合理性]

### 🔍 领域研究缺口与机会
**未充分解决的问题**：
- [ ] [问题1]—— 技术难点在哪？
- [ ] [问题2]

**有潜力的研究方向**：
- [ ] [方向1：具体建议]
- [ ] [方向2]

## 💎 综合洞察
[3-5句凝练的结论：这批文献的整体学术价值、当前主流技术路线的优势与瓶颈、最重要的未解决问题和最有前景的突破方向]

---

## 📋 分析规范
1. **摘要/结论优先**：摘要和结论是最可靠的信息来源，优先从中提取贡献声明和主要结果
2. **证据标注**：明确区分"摘要明确指出"、"结论部分提到"、"摘录推断"
3. **不确定性**：若某维度信息不足，标注"[摘录不足，无法确认]"，绝不猜测
4. **术语规范**：中文为主，专业术语保留英文原文（首次出现中英对照）
5. **深度优先**：宁可对部分维度深入分析，也不要浅尝辄止
"""


OPML_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head><title>{title}</title></head>
  <body>
{outlines}
  </body>
</opml>"""


class PaperMindmapService:
    """Deep academic analysis: per-paper structured breakdown + cross-paper comparison."""

    async def analyze(
        self,
        *,
        papers: list[LocalPaperRead],
        question: str,
        output_format: str = "markdown",
    ) -> tuple[str, str]:
        """Return (content, filename). output_format: markdown | opml"""
        if not papers:
            return "# 无论文可分析\n\n没有检索到符合条件的论文，请调整检索词后重试。", "mindmap.md"

        evidence_text = self._build_rich_evidence(papers)

        if llm_is_configured():
            try:
                markdown_map = await self._deep_analyze_via_llm(
                    question=question,
                    evidence=evidence_text,
                    papers=papers,
                )
            except Exception as exc:
                markdown_map = self._generate_structured_fallback(papers, question, str(exc))
        else:
            markdown_map = self._generate_structured_fallback(papers, question, "LLM未配置")

        if output_format == "opml":
            opml_content = self._markdown_to_opml(markdown_map, question)
            return opml_content, "deep-analysis-mindmap.opml"
        return markdown_map, "deep-analysis-mindmap.md"

    def _build_rich_evidence(self, papers: list[LocalPaperRead]) -> str:
        """Build rich evidence with structured sections (Abstract/Intro/Conclusion) + chunks."""
        parts: list[str] = []
        for i, paper in enumerate(papers, 1):
            authors_str = "; ".join(paper.authors[:5])
            if len(paper.authors) > 5:
                authors_str += f" 等{len(paper.authors)}人"

            # Build structured sections first (highest priority)
            sections: list[str] = []

            if paper.abstract_text:
                abstract_preview = (
                    paper.abstract_text[:800]
                    if len(paper.abstract_text) > 800
                    else paper.abstract_text
                )
                sections.append(f"  📄 摘要（Abstract）：\n  {abstract_preview}")

            if paper.introduction_text:
                intro_preview = (
                    paper.introduction_text[:600]
                    if len(paper.introduction_text) > 600
                    else paper.introduction_text
                )
                sections.append(f"  📖 引言（Introduction前两段）：\n  {intro_preview}")

            if paper.conclusion_text:
                conclusion_preview = (
                    paper.conclusion_text[:600]
                    if len(paper.conclusion_text) > 600
                    else paper.conclusion_text
                )
                sections.append(f"  🎯 结论（Conclusion）：\n  {conclusion_preview}")

            # Add evidence chunks as supplementary context (lower priority)
            evidence_snippets: list[str] = []
            seen_parent_context: set[str] = set()
            for e in paper.evidence[:5]:  # Reduced from 8 to 5 since we have structured sections
                # Retrieval ranks a small child chunk; deep analysis receives
                # its larger PostgreSQL parent section with the same page proof.
                snippet = (e.parent_text or e.text).strip()
                context_key = f"{e.page_number}:{snippet}"
                if context_key in seen_parent_context:
                    continue
                seen_parent_context.add(context_key)
                if len(snippet) > 1200:
                    snippet = snippet[:1200] + "..."
                heading = f" · {e.section_heading}" if e.section_heading else ""
                evidence_snippets.append(f"  [p.{e.page_number}{heading}] {snippet}")

            if sections:
                structured_block = "\n\n".join(sections)
                evidence_block = "\n".join(evidence_snippets) if evidence_snippets else ""
                content_block = (
                    f"{structured_block}\n\n  📚 补充页面摘录：\n{evidence_block}"
                    if evidence_block
                    else structured_block
                )
            else:
                # Fallback: no structured sections, use evidence chunks only
                evidence_block = (
                    "\n".join(evidence_snippets) if evidence_snippets else "  （无可用文本摘录）"
                )
                content_block = f"  页面摘录：\n{evidence_block}"

            parts.append(
                f"【论文{i}】\n"
                f"标题：{paper.title}\n"
                f"作者：{authors_str}\n"
                f"年份：{paper.publication_year or '未知'}\n"
                f"DOI：{paper.doi or 'N/A'}\n"
                f"类型：{paper.bibtex_type}\n\n"
                f"{content_block}"
            )
        return "\n\n" + "=" * 60 + "\n\n".join(parts)

    async def _deep_analyze_via_llm(
        self,
        *,
        question: str,
        evidence: str,
        papers: list[LocalPaperRead],
    ) -> str:
        import asyncio

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
            "对于摘录不足以支撑分析的维度，明确标注[摘录不足]而非编造。"
        )

        try:
            # Add 3-minute timeout protection
            result = await asyncio.wait_for(agent.run(user_prompt), timeout=180.0)
            return result.output
        except TimeoutError:
            return (
                f"# 深度分析超时\n\n"
                f"为 {len(papers)} 篇论文生成深度分析需要较长时间（>3分钟），LLM调用超时。\n\n"
                f"**建议**：\n"
                f"1. 减少论文数量（当前 {len(papers)} 篇，建议 ≤5 篇）\n"
                f"2. 稍后重试\n"
                f"3. 检查 LLM 服务状态和网络连接\n\n"
                f"**降级输出**：以下是元数据概览\n\n"
                + self._generate_structured_fallback(papers, question, "LLM调用超时")
            )
        except Exception as e:
            return (
                f"# 深度分析失败\n\n"
                f"生成过程中遇到错误：{e!s}\n\n"
                f"**降级输出**：以下是元数据概览\n\n"
                + self._generate_structured_fallback(papers, question, f"错误: {e!s}")
            )

    def _generate_structured_fallback(
        self, papers: list[LocalPaperRead], question: str, reason: str
    ) -> str:
        """Structured metadata-based outline when LLM is unavailable."""
        lines = [
            "# 文献分析报告（元数据版）",
            "",
            f"> ⚠️ LLM深度分析不可用（{reason}），以下为元数据概览",
            "",
            f"**研究主题**：{question}  ",
            f"**分析论文数**：{len(papers)} 篇",
            "",
            "---",
            "",
            "## 逐篇概览",
            "",
        ]
        for i, paper in enumerate(papers, 1):
            lines += [
                f"### 论文{i}：{paper.title}",
                "",
                f"- **作者**：{'; '.join(paper.authors[:3])}{'等' if len(paper.authors) > 3 else ''}",
                f"- **年份**：{paper.publication_year or '未知'}",
                f"- **DOI**：{paper.doi or '无'}",
                f"- **类型**：{paper.bibtex_type}",
                f"- **文件**：{paper.relative_source_path}",
                "",
            ]
            for e in paper.evidence[:3]:
                lines.append(f"> p.{e.page_number}: {e.text[:300]}...")
            lines.append("")

        lines += [
            "---",
            "",
            "## 配置说明",
            "",
            "要启用 LLM 深度分析，请确保：",
            "1. `OPENAI_API_KEY` 或 `LLM_PROVIDER` 已正确配置",
            "2. 网络可访问 LLM 服务",
            "3. 重新生成思维导图",
        ]
        return "\n".join(lines)

    def _markdown_to_opml(self, markdown: str, title: str) -> str:
        """Convert Markdown heading/list outline to OPML for Mubu/XMind/Logseq import."""
        lines = markdown.splitlines()

        # Stack: list of (indent_level, is_open)
        stack: list[int] = []  # current open heading levels
        indent_parts: list[str] = []

        def flush_stack_to(target_level: int) -> None:
            while stack and stack[-1] >= target_level:
                lv = stack.pop()
                indent_parts.append("  " * lv + "</outline>")

        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("---"):
                continue

            # Determine heading level
            if stripped.startswith("#### "):
                level, text = 4, stripped[5:]
            elif stripped.startswith("### "):
                level, text = 3, stripped[4:]
            elif stripped.startswith("## "):
                level, text = 2, stripped[3:]
            elif stripped.startswith("# "):
                level, text = 1, stripped[2:]
            elif stripped.startswith("- ") or stripped.startswith("* "):
                level, text = (stack[-1] + 1) if stack else 5, stripped[2:]
            elif stripped.startswith("|"):
                # Table row → leaf node
                level = (stack[-1] + 1) if stack else 5
                text = " | ".join(c.strip() for c in stripped.strip("|").split("|") if c.strip())
            else:
                continue

            flush_stack_to(level)
            escaped = html_lib.escape(text.strip(), quote=True)
            indent_parts.append("  " * level + f'<outline text="{escaped}">')
            stack.append(level)

        # Close all remaining
        while stack:
            lv = stack.pop()
            indent_parts.append("  " * lv + "</outline>")

        return OPML_TEMPLATE.format(
            title=html_lib.escape(title),
            outlines="\n".join(indent_parts),
        )
