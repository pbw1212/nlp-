"""Task 2A recall and Task 2B structured applicability baselines."""

from src.memory.retrieval.applicability import ApplicabilityEngine
from src.memory.retrieval.applicability_models import (
    ApplicabilityAssessment,
    ApplicabilityContext,
    ApplicabilityDecision,
    ApplicabilityDimension,
    ApplicabilityResult,
    ApplicabilityStatus,
    CandidateScopeRule,
    CandidateScopeState,
    ConstraintOperator,
    ContextKnowledgeState,
    ContextValue,
    DimensionAssessment,
    MemoryApplicabilityProfile,
)
from src.memory.retrieval.applicability_ontology import (
    DEFAULT_APPLICABILITY_FIELD_REGISTRY,
    ApplicabilityFieldDefinition,
    ApplicabilityFieldRegistry,
    ValueSemantics,
)
from src.memory.retrieval.models import (
    RecallCandidate,
    RecallRequest,
    RecallResult,
    RecallScoreBreakdown,
    RecallTrace,
)
from src.memory.retrieval.dense import DenseEmbeddingRetriever
from src.memory.retrieval.embedding import EmbeddingProviderIdentity, FastEmbedEmbeddingProvider
from src.memory.retrieval.hybrid import HybridRRFMemoryRetriever
from src.memory.retrieval.protocols import EmbeddingProvider, MemoryRetriever
from src.memory.retrieval.recall import MemoryRecallEngine
from src.memory.retrieval.graph import PersonalizedGraphMemoryRetriever
from src.memory.retrieval.rerank import CandidateReranker, OpenAICompatibleCandidateReranker
from src.memory.retrieval.interpretation_models import (
    ContextInterpretation,
    ContextSource,
    MemoryScopeInterpretation,
)
from src.memory.retrieval.interpretation_provider import (
    OpenAICompatibleSemanticInterpretationProvider,
    SemanticInterpretationProvider,
    build_semantic_interpretation_provider,
)
from src.memory.retrieval.interpreters import ContextInterpreter, MemoryScopeInterpreter
from src.memory.retrieval.semantic_pipeline import (
    InterpretedApplicabilityResult,
    SemanticRetrievalPipeline,
)
from src.memory.retrieval.evidence import (
    CandidateEvidence,
    ConflictAnnotation,
    ConflictAssessment,
    ConflictDetector,
    ConflictStatus,
    DeterministicEvidencePipeline,
    DistilledEvidence,
    EvidenceBundle,
    EvidencePipeline,
    EvidencePipelineConfig,
    EvidenceSufficiency,
    EvidenceSufficiencyStatus,
    ScopeRuleEvidence,
)

from src.memory.retrieval.facade import DefaultMemoryRetrievalFacade, MemoryRetrievalFacade

__all__ = [
    "ApplicabilityAssessment",
    "ApplicabilityContext",
    "ApplicabilityDecision",
    "ApplicabilityDimension",
    "ApplicabilityEngine",
    "ApplicabilityFieldDefinition",
    "ApplicabilityFieldRegistry",
    "ApplicabilityResult",
    "ApplicabilityStatus",
    "CandidateScopeRule",
    "CandidateScopeState",
    "ConstraintOperator",
    "ContextKnowledgeState",
    "ContextInterpretation",
    "ContextInterpreter",
    "ContextSource",
    "ContextValue",
    "DEFAULT_APPLICABILITY_FIELD_REGISTRY",
    "DefaultMemoryRetrievalFacade",
    "DimensionAssessment",
    "DenseEmbeddingRetriever",
    "EmbeddingProvider",
    "EmbeddingProviderIdentity",
    "FastEmbedEmbeddingProvider",
    "HybridRRFMemoryRetriever",
    "MemoryRecallEngine",
    "PersonalizedGraphMemoryRetriever",
    "MemoryRetrievalFacade",
    "MemoryRetriever",
    "MemoryApplicabilityProfile",
    "MemoryScopeInterpretation",
    "MemoryScopeInterpreter",
    "OpenAICompatibleSemanticInterpretationProvider",
    "RecallCandidate",
    "RecallRequest",
    "RecallResult",
    "RecallScoreBreakdown",
    "RecallTrace",
    "CandidateReranker",
    "OpenAICompatibleCandidateReranker",
    "SemanticInterpretationProvider",
    "SemanticRetrievalPipeline",
    "ValueSemantics",
    "InterpretedApplicabilityResult",
    "build_semantic_interpretation_provider",
    "CandidateEvidence",
    "ConflictAnnotation",
    "ConflictAssessment",
    "ConflictDetector",
    "ConflictStatus",
    "DeterministicEvidencePipeline",
    "DistilledEvidence",
    "EvidenceBundle",
    "EvidencePipeline",
    "EvidencePipelineConfig",
    "EvidenceSufficiency",
    "EvidenceSufficiencyStatus",
    "ScopeRuleEvidence",
]
