from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping, Tuple

from src.memory.retrieval.applicability_models import (
    ApplicabilityDimension,
    normalize_symbolic_value,
)


ONTOLOGY_VERSION = "applicability_fields_v1"


class ValueSemantics(str, Enum):
    CLOSED = "CLOSED"
    OPEN_TEXT = "OPEN_TEXT"


@dataclass(frozen=True)
class ApplicabilityFieldDefinition:
    field_id: str
    dimension: ApplicabilityDimension
    description: str
    value_semantics: ValueSemantics
    closed_values: Tuple[str, ...] = ()
    aliases: Tuple[str, ...] = ()
    value_aliases: Tuple[Tuple[str, str], ...] = ()
    supports_constraints: bool = True

    def __post_init__(self) -> None:
        field_id = normalize_symbolic_value(self.field_id)
        aliases = tuple(sorted({normalize_symbolic_value(item) for item in self.aliases}))
        closed_values = tuple(
            sorted({normalize_symbolic_value(item) for item in self.closed_values})
        )
        value_aliases = tuple(
            sorted(
                (
                    normalize_symbolic_value(source),
                    normalize_symbolic_value(target),
                )
                for source, target in self.value_aliases
            )
        )
        if not self.description.strip():
            raise ValueError("field description 不能为空")
        if field_id in aliases:
            raise ValueError("field alias 不能等于 canonical field_id")
        if self.value_semantics is ValueSemantics.CLOSED and not closed_values:
            raise ValueError("CLOSED field 必须声明 closed_values")
        if self.value_semantics is ValueSemantics.OPEN_TEXT and closed_values:
            raise ValueError("OPEN_TEXT field 不能声明 closed_values")
        if self.value_semantics is ValueSemantics.CLOSED:
            invalid_targets = {target for _, target in value_aliases} - set(closed_values)
            if invalid_targets:
                raise ValueError(f"value alias target 不在 closed vocabulary: {invalid_targets}")
        object.__setattr__(self, "field_id", field_id)
        object.__setattr__(self, "aliases", aliases)
        object.__setattr__(self, "closed_values", closed_values)
        object.__setattr__(self, "value_aliases", value_aliases)

    @property
    def key(self) -> tuple[ApplicabilityDimension, str]:
        return self.dimension, self.field_id

    def normalize_value(self, value: str) -> str:
        normalized = normalize_symbolic_value(value)
        normalized = dict(self.value_aliases).get(normalized, normalized)
        if self.value_semantics is ValueSemantics.CLOSED and normalized not in self.closed_values:
            raise ValueError(
                f"{self.dimension.value}.{self.field_id} 不支持 value={normalized!r}"
            )
        return normalized

    def prompt_spec(self) -> Mapping[str, object]:
        result: dict[str, object] = {
            "dimension": self.dimension.value,
            "field_id": self.field_id,
            "description": self.description,
            "value_semantics": self.value_semantics.value,
        }
        if self.closed_values:
            result["allowed_values"] = list(self.closed_values)
        return result


class ApplicabilityFieldRegistry:
    """Task 2 request-local ontology；不属于 Task 1 persistent schema。"""

    def __init__(
        self,
        definitions: Iterable[ApplicabilityFieldDefinition],
        *,
        version: str = ONTOLOGY_VERSION,
    ) -> None:
        definitions = tuple(definitions)
        if not version.strip() or not definitions:
            raise ValueError("registry 需要 version 和至少一个 field")
        canonical: dict[tuple[ApplicabilityDimension, str], ApplicabilityFieldDefinition] = {}
        lookup: dict[tuple[ApplicabilityDimension, str], ApplicabilityFieldDefinition] = {}
        for definition in definitions:
            if definition.key in canonical:
                raise ValueError(f"duplicate field ID: {definition.key}")
            canonical[definition.key] = definition
            for name in (definition.field_id, *definition.aliases):
                key = (definition.dimension, name)
                if key in lookup:
                    raise ValueError(f"duplicate field/alias: {key}")
                lookup[key] = definition
        self._version = version
        self._definitions = tuple(
            sorted(definitions, key=lambda item: (item.dimension.value, item.field_id))
        )
        self._canonical = canonical
        self._lookup = lookup

    @property
    def version(self) -> str:
        return self._version

    @property
    def definitions(self) -> Tuple[ApplicabilityFieldDefinition, ...]:
        return self._definitions

    @property
    def output_field_names(self) -> Tuple[str, ...]:
        return tuple(sorted({item.field_id for item in self._definitions}))

    def resolve(
        self, dimension: ApplicabilityDimension | str, field: str
    ) -> ApplicabilityFieldDefinition:
        dimension = ApplicabilityDimension(dimension)
        key = (dimension, normalize_symbolic_value(field))
        try:
            return self._lookup[key]
        except KeyError as exc:
            raise ValueError(f"unsupported applicability field: {dimension.value}.{field}") from exc

    def normalize_values(
        self,
        dimension: ApplicabilityDimension | str,
        field: str,
        values: Iterable[str],
    ) -> tuple[ApplicabilityFieldDefinition, Tuple[str, ...]]:
        definition = self.resolve(dimension, field)
        normalized = tuple(sorted({definition.normalize_value(value) for value in values}))
        return definition, normalized

    def prompt_specs(self) -> Tuple[str, ...]:
        """紧凑 registry contract，减少每次结构化 extraction 的 token 预算。"""
        result = []
        for item in self._definitions:
            values = ",".join(item.closed_values) if item.closed_values else "<evidence_text>"
            result.append(
                f"{item.dimension.value}.{item.field_id}|{item.value_semantics.value}|{values}"
            )
        return tuple(result)


def _closed(
    dimension: ApplicabilityDimension,
    field_id: str,
    description: str,
    values: tuple[str, ...],
    *,
    aliases: tuple[str, ...] = (),
    value_aliases: tuple[tuple[str, str], ...] = (),
) -> ApplicabilityFieldDefinition:
    return ApplicabilityFieldDefinition(
        field_id, dimension, description, ValueSemantics.CLOSED,
        values, aliases, value_aliases,
    )


def _open(
    dimension: ApplicabilityDimension,
    field_id: str,
    description: str,
    *,
    aliases: tuple[str, ...] = (),
    value_aliases: tuple[tuple[str, str], ...] = (),
) -> ApplicabilityFieldDefinition:
    return ApplicabilityFieldDefinition(
        field_id, dimension, description, ValueSemantics.OPEN_TEXT,
        aliases=aliases, value_aliases=value_aliases,
    )


D = ApplicabilityDimension

DEFAULT_APPLICABILITY_FIELD_REGISTRY = ApplicabilityFieldRegistry(
    (
        _closed(D.PERSON, "travel_party", "Who travels with the user.",
                ("solo", "friends", "family", "parents", "children", "grandparents", "colleagues"),
                aliases=("persons", "party"),
                value_aliases=(("alone", "solo"), ("group_travel", "friends"), ("coworkers", "colleagues"))),
        _open(D.PERSON, "companions", "Non-travel companions when the decision explicitly considers them."),
        _closed(D.PERSON, "meal_party", "Who shares a meal with the user.", ("client", "friends", "family")),
        _closed(D.PERSON, "meeting_party", "Who attends a meeting with the user.", ("client", "colleagues")),
        _open(D.PERSON, "passengers", "Passengers in a transport decision."),
        _open(D.PERSON, "social_party", "People in a social gathering."),

        _open(D.LOCATION, "destination", "Named or unresolved trip/delivery destination.",
              value_aliases=(("上海", "shanghai"), ("北京", "beijing"))),
        _closed(D.LOCATION, "environment", "Physical environment category.", ("mountain", "city", "suburban", "airport")),
        _closed(D.LOCATION, "area_noise", "Noise character of an accommodation area.", ("quiet", "busy")),
        _open(D.LOCATION, "location", "Generic location scope when no narrower supported field applies."),
        _closed(D.LOCATION, "office_visit", "Whether an office visit occurs.", ("office", "not_office")),
        _open(D.LOCATION, "workplace", "Workplace/location scope of a work preference."),

        _open(D.TIME, "departure", "Departure time or relative departure window."),
        _open(D.TIME, "duration", "Duration explicitly relevant to the decision."),
        _open(D.TIME, "meeting_time", "Meeting time or candidate time windows."),
        _open(D.TIME, "meal_time", "Meal time such as breakfast or tonight."),
        _open(D.TIME, "run_time", "Exercise/run time window."),
        _open(D.TIME, "focus_window", "Protected focus time window."),
        _open(D.TIME, "day_type", "Day category such as workday or weekend."),
        _open(D.TIME, "season", "Seasonal scope of a preference."),

        _closed(D.TASK, "trip_type", "Trip purpose, not airport transfer scenario.",
                ("business_trip", "personal_trip"),
                aliases=("trip_purpose",),
                value_aliases=(("business", "business_trip"), ("personal", "personal_trip"),
                               ("vacation", "personal_trip"))),
        _open(D.TASK, "activity", "Current requested activity when it discriminates memory scope."),
        _closed(D.TASK, "meal_type", "Meal task category.", ("business_dinner", "personal_dinner")),
        _closed(D.TASK, "meeting_mode", "How a meeting occurs.", ("online", "in_person")),
        _closed(D.TASK, "work_mode", "How work occurs.", ("remote_work", "office_work")),
        _open(D.TASK, "shopping_category", "Product category being purchased."),
        _open(D.TASK, "shopping_context", "Scope over shopping tasks."),

        _closed(D.SCENARIO, "activity", "Situational activity such as hiking or sightseeing.",
                ("hiking", "sightseeing")),
        _closed(D.SCENARIO, "transport_mode", "Transport situation.",
                ("driving", "not_driving", "metro", "long_distance_bus", "train", "taxi")),
        _closed(D.SCENARIO, "trip_type", "Airport/transit scenario, not trip purpose.",
                ("airport_layover", "airport_departure", "airport_transfer")),
        _open(D.SCENARIO, "fallback_activity", "Conditional fallback activity."),

        _closed(D.CONSTRAINT, "payment_source", "Who pays the expense.", ("company", "self", "mixed"),
                aliases=("expense_responsibility",),
                value_aliases=(("company_paid", "company"), ("employer_paid", "company"),
                               ("self_paid", "self"), ("personal_payment", "self"))),
        _closed(D.CONSTRAINT, "parking", "Whether parking is required.", ("required", "not_required")),
        _closed(D.CONSTRAINT, "pet_friendly", "Whether pet-friendly access is required.", ("required", "not_required")),
        _closed(D.CONSTRAINT, "accessibility", "Whether accessibility is required.", ("required", "not_required")),
        _open(D.CONSTRAINT, "allergen", "Allergen or excluded food ingredient."),
        _open(D.CONSTRAINT, "hotel_budget", "Accommodation budget or budget cap."),
        _open(D.CONSTRAINT, "shopping_budget", "Shopping budget cap."),
        _open(D.CONSTRAINT, "layover_duration", "Duration constraint for an airport layover."),
        _closed(D.CONSTRAINT, "noise", "Noise requirement.", ("quiet_required", "noise_acceptable")),
        _open(D.CONSTRAINT, "weather", "Weather condition relevant to a preference."),
        _closed(D.CONSTRAINT, "motion_sickness", "Whether motion sickness is present.", ("present", "absent")),
        _open(D.CONSTRAINT, "transport_mode", "Required or excluded transport mode."),
        _open(D.CONSTRAINT, "vehicle_size", "Vehicle capacity/size requirement."),
        _open(D.CONSTRAINT, "seat", "Required or excluded seat position."),
        _open(D.CONSTRAINT, "meeting_time", "Required or excluded meeting-time constraint."),
    )
)
