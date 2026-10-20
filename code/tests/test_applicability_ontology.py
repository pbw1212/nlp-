from __future__ import annotations

import pytest

from src.memory.retrieval.applicability_models import (
    ApplicabilityContext,
    ApplicabilityDimension,
    ContextKnowledgeState,
    ContextValue,
)
from src.memory.retrieval.applicability_ontology import (
    DEFAULT_APPLICABILITY_FIELD_REGISTRY,
    ApplicabilityFieldDefinition,
    ApplicabilityFieldRegistry,
    ValueSemantics,
)


def test_registry_rejects_duplicate_field_id_and_dimension_mismatch():
    definition = ApplicabilityFieldDefinition(
        "payment", ApplicabilityDimension.CONSTRAINT, "payer",
        ValueSemantics.CLOSED, ("company", "self"),
    )
    with pytest.raises(ValueError, match="duplicate field ID"):
        ApplicabilityFieldRegistry((definition, definition))
    with pytest.raises(ValueError, match="unsupported applicability field"):
        DEFAULT_APPLICABILITY_FIELD_REGISTRY.resolve(
            ApplicabilityDimension.LOCATION, "payment_source"
        )


def test_closed_value_validation_and_known_alias_normalization():
    definition, values = DEFAULT_APPLICABILITY_FIELD_REGISTRY.normalize_values(
        ApplicabilityDimension.CONSTRAINT, "expense_responsibility", ("self_paid",)
    )
    assert definition.field_id == "payment_source"
    assert values == ("self",)
    with pytest.raises(ValueError, match="不支持 value"):
        DEFAULT_APPLICABILITY_FIELD_REGISTRY.normalize_values(
            ApplicabilityDimension.CONSTRAINT, "payment_source", ("parents",)
        )


def test_open_values_use_nfkc_casefold_and_registry_aliases():
    definition, values = DEFAULT_APPLICABILITY_FIELD_REGISTRY.normalize_values(
        ApplicabilityDimension.LOCATION, "destination", (" 上海 ", "ＴＯＫＹＯ")
    )
    assert definition.value_semantics is ValueSemantics.OPEN_TEXT
    assert values == ("shanghai", "tokyo")


def test_missing_unknown_and_ambiguous_are_distinct():
    missing = ApplicabilityContext(())
    unknown = ApplicabilityContext((ContextValue(
        ApplicabilityDimension.LOCATION,
        "destination",
        ContextKnowledgeState.UNKNOWN,
    ),))
    assert missing != unknown
    with pytest.raises(ValueError, match="至少两个"):
        ContextValue(
            ApplicabilityDimension.LOCATION,
            "destination",
            ContextKnowledgeState.AMBIGUOUS,
            ("shanghai",),
        )


def test_registry_output_vocabulary_is_closed_and_versioned():
    assert DEFAULT_APPLICABILITY_FIELD_REGISTRY.version == "applicability_fields_v1"
    assert "payment_source" in DEFAULT_APPLICABILITY_FIELD_REGISTRY.output_field_names
    assert "hotel_search" not in DEFAULT_APPLICABILITY_FIELD_REGISTRY.output_field_names
