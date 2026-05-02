"""Shared JSON type aliases for PoundCake contracts."""

from __future__ import annotations

from typing import TypeAlias

from pydantic import JsonValue

JSONPrimitive: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JsonValue
JSONObject: TypeAlias = dict[str, JSONValue]
JSONArray: TypeAlias = list[JSONValue]
