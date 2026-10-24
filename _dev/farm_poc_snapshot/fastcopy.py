"""Fast copy for the JSON-shaped data that Matrix telemetry, events, and messages use.

``copy.deepcopy`` spent about 85 % of each simulation tick on bookkeeping
(memo tables, reducer lookups) that plain dict/list/number trees do not need.
This copies dicts, lists, tuples, and sets recursively, returns immutable
scalars as they are, and falls back to ``copy.deepcopy`` for anything else.
Unlike deepcopy it does not preserve aliasing inside the copied tree, which
no Matrix consumer relies on.
"""
from copy import deepcopy as _deepcopy

_SCALARS = (str, int, float, bool, type(None))


def json_copy(value):
    if isinstance(value, _SCALARS):
        return value
    if isinstance(value, dict):
        return {key: json_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_copy(item) for item in value]
    if isinstance(value, tuple):
        return tuple(json_copy(item) for item in value)
    if isinstance(value, set):
        return {json_copy(item) for item in value}
    return _deepcopy(value)
