"""PydanticAI expert definitions; none owns workflow or persistence."""
# ruff: noqa: RUF001 - Chinese prompts intentionally use Chinese punctuation

import json
from typing import Any, Generic, TypeVar

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.usage import RunUsage

from app.schemas.literature_research.analysis import (
    AnalysisSection,
    AuditReport,
    FacetJudgementBatch,
    FigureInterpretation,
    ProtocolDraftAdvice,
    SynthesisOutput,
)
from app.services.literature_research.llm_usage import (
    ResearchLLMBudgetExceeded,
    active_usage_limits,
    has_reported_usage,
    record_active_usage,
)
from app.services.llm_provider import build_llm_model

COMMON = """你是学术论文分析专家，不是工作流控制器。只使用输入内容和 evidence_id。
每个事实性结论必须绑定证据；无证据输出 UNKNOWN/NOT_REPORTED。不得修改日期、指标、
阈值、数量目标，不得批准协议、搜索网页或决定最终发布。数值保留单位、比较对象和条件。
只输出给定结构化 Schema。"""

PROTOCOL_PROMPT_VERSION = "2026-08-22.1"

PROMPTS = {
    "protocol": COMMON + "\n仅起草主题定义、研究问题和 facet；approval_requested 必须为 false。",
    "relevance": COMMON
    + "\n对 papers 批次逐篇、逐 facet 判定支持度和中心性；每篇只能引用其自身 evidence_id。"
    + "必须返回且只返回每个输入 work_id 一次；不得仅根据 venue 判断。",
    "analysis": COMMON
    + "\n仅分析指定 section；区分作者声明、实验观察和评审推断。"
    + "\nclaim.evidence_ids 只能逐字复制输入 evidence 中存在的 evidence_id。",
    "figure": COMMON
    + "\n联合图题与邻近正文；禁止猜测不可见数值或显著性。"
    + "\nTABLE_EXACT/TEXT_EXACT 的 extracted_values 只能逐字复制输入 figure.exact_numeric_values；"
    + "没有可复制数值时必须使用 NOT_EXTRACTED 且 extracted_values 为空。",
    "audit": COMMON
    + "\n逐 claim 判定支持、部分支持、不支持或矛盾；不得修改原 claim。"
    + "\n每个审计项只能引用该 claim 自带的 evidence_ids，不得跨 claim 移动证据。",
    "synthesis": COMMON + "\n仅综合已审计论文，不得引入新论文或输入外事实。",
}

OutputT = TypeVar("OutputT", bound=BaseModel)


class StructuredExpert(Generic[OutputT]):
    def __init__(self, name: str, output_type: type[OutputT]) -> None:
        self.name = name
        model = build_llm_model()
        self.agent = Agent[None, OutputT](
            model=model,
            output_type=output_type,
            instructions=PROMPTS[name],
            name=f"literature_{name}_expert",
            retries=1,
        )

    async def run(self, payload: dict[str, Any]) -> OutputT:
        run_usage = RunUsage()
        try:
            result = await self.agent.run(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                usage_limits=active_usage_limits(request_limit=3),
                usage=run_usage,
            )
        except UsageLimitExceeded as exc:
            if has_reported_usage(run_usage):
                record_active_usage(self.name, run_usage)
            raise ResearchLLMBudgetExceeded(str(exc)) from exc
        except Exception:
            if has_reported_usage(run_usage):
                record_active_usage(self.name, run_usage)
            raise
        record_active_usage(self.name, result.usage)
        return result.output


class LiteratureResearchExperts:
    """Exactly six semantic experts from the approved migration design."""

    def __init__(self) -> None:
        self.protocol = StructuredExpert("protocol", ProtocolDraftAdvice)
        self.relevance = StructuredExpert("relevance", FacetJudgementBatch)
        self.analysis = StructuredExpert("analysis", AnalysisSection)
        self.figure = StructuredExpert("figure", FigureInterpretation)
        self.audit = StructuredExpert("audit", AuditReport)
        self.synthesis = StructuredExpert("synthesis", SynthesisOutput)
