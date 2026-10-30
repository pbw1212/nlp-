"""Explicit JSON snapshot codec for trusted MAPAS DTOs; never deserialize pickle."""

from dataclasses import fields, is_dataclass, replace
from enum import Enum
from functools import lru_cache
from importlib import import_module

MODULES = (
    "src.memory.agent.models",
    "src.memory.runtime.models",
    "src.memory.task_state.models",
    "src.memory.task_state.maintenance",
    "src.memory.retrieval.evidence",
    "src.memory.core.schema",
    "src.memory.conversation.models",
)


@lru_cache(maxsize=1)
def _types():
    result = {}
    for name in MODULES:
        module = import_module(name)
        for value in vars(module).values():
            if (
                isinstance(value, type)
                and value.__module__ == name
                and (is_dataclass(value) or issubclass(value, Enum))
            ):
                result[name + "." + value.__qualname__] = value
    return result


def encode(value):
    if isinstance(value, Enum):
        return {
            "$enum": type(value).__module__ + "." + type(value).__qualname__,
            "value": value.value,
        }
    if is_dataclass(value) and not isinstance(value, type):
        name = type(value).__module__ + "." + type(value).__qualname__
        if name not in _types():
            raise ValueError("unsupported snapshot type")
        return {
            "$type": name,
            "fields": {f.name: encode(getattr(value, f.name)) for f in fields(value)},
        }
    if isinstance(value, tuple):
        return {"$tuple": [encode(x) for x in value]}
    if isinstance(value, list):
        return [encode(x) for x in value]
    if isinstance(value, dict):
        if not all(isinstance(k, str) for k in value):
            raise ValueError("unsupported snapshot key")
        return {"$dict": [[k, encode(v)] for k, v in value.items()]}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("unsupported snapshot value")


def decode(value):
    if isinstance(value, list):
        return [decode(x) for x in value]
    if not isinstance(value, dict):
        return value
    allowed = _types()
    if set(value) == {"$tuple"}:
        return tuple(decode(x) for x in value["$tuple"])
    if set(value) == {"$dict"}:
        return {k: decode(v) for k, v in value["$dict"]}
    if set(value) == {"$enum", "value"}:
        cls = allowed.get(value["$enum"])
        if cls is None or not issubclass(cls, Enum):
            raise ValueError("unsupported snapshot enum")
        return cls(value["value"])
    if set(value) == {"$type", "fields"}:
        cls = allowed.get(value["$type"])
        if cls is None or not is_dataclass(cls):
            raise ValueError("unsupported snapshot type")
        if set(value["fields"]) != {f.name for f in fields(cls)}:
            raise ValueError("snapshot fields changed")
        return cls(**{k: decode(v) for k, v in value["fields"].items()})
    raise ValueError("invalid snapshot encoding")


def encode_prepared(context, request):
    return encode((context, replace(request, context_overflow_handler=None)))
