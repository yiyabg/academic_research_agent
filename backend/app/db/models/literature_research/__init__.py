"""SQLAlchemy models for the literature research bounded context."""

from app.db.models.literature_research.analysis import (
    ResearchArtifact,
    ResearchPaperAnalysis,
    ResearchReleaseCheck,
    ResearchSynthesis,
)
from app.db.models.literature_research.discovery import (
    ResearchSearchQuery,
    ResearchSourceFailure,
    ResearchSourcePage,
    ResearchSourceRecord,
    ResearchVenue,
    ResearchWork,
    ResearchWorkVersion,
)
from app.db.models.literature_research.evaluation import (
    ResearchEvaluationDataset,
    ResearchEvaluationResult,
)
from app.db.models.literature_research.evidence import (
    ResearchEvidenceLocator,
    ResearchFigureArtifact,
    ResearchFullTextAcquisition,
    ResearchParsedBlock,
    ResearchParsingResult,
    ResearchRelevanceScore,
)
from app.db.models.literature_research.memory import (
    ResearchFeedbackSample,
    ResearchPolicyVersion,
    ResearchProjectMemory,
    UserResearchProfile,
)
from app.db.models.literature_research.organization import (
    ResearchOrganization,
    ResearchOrganizationMember,
)
from app.db.models.literature_research.outbox import ResearchOutboxEvent
from app.db.models.literature_research.project import ResearchProject
from app.db.models.literature_research.protocol import ResearchProtocolVersion
from app.db.models.literature_research.quality import (
    ResearchConstraintEvaluation,
    ResearchMetricSnapshot,
    ResearchVenueMetricFact,
    ResearchWorkEligibility,
)
from app.db.models.literature_research.run import (
    ResearchRun,
    ResearchRunControl,
    ResearchTaskExecution,
)

__all__ = [
    "ResearchArtifact",
    "ResearchConstraintEvaluation",
    "ResearchEvaluationDataset",
    "ResearchEvaluationResult",
    "ResearchEvidenceLocator",
    "ResearchFeedbackSample",
    "ResearchFigureArtifact",
    "ResearchFullTextAcquisition",
    "ResearchMetricSnapshot",
    "ResearchOrganization",
    "ResearchOrganizationMember",
    "ResearchOutboxEvent",
    "ResearchPaperAnalysis",
    "ResearchParsedBlock",
    "ResearchParsingResult",
    "ResearchPolicyVersion",
    "ResearchProject",
    "ResearchProjectMemory",
    "ResearchProtocolVersion",
    "ResearchReleaseCheck",
    "ResearchRelevanceScore",
    "ResearchRun",
    "ResearchRunControl",
    "ResearchSearchQuery",
    "ResearchSourceFailure",
    "ResearchSourcePage",
    "ResearchSourceRecord",
    "ResearchSynthesis",
    "ResearchTaskExecution",
    "ResearchVenue",
    "ResearchVenueMetricFact",
    "ResearchWork",
    "ResearchWorkEligibility",
    "ResearchWorkVersion",
    "UserResearchProfile",
]
