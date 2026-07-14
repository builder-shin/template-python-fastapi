"""JSON:API request content negotiation."""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import Header

from app.jsonapi.errors import JsonApiException
from app.jsonapi.responses import JSONAPI_MEDIA_TYPE

_QUALITY_VALUE = re.compile(r"(?:0(?:\.\d{0,3})?|1(?:\.0{0,3})?)\Z")
_TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")


def _split_quoted(value: str, delimiter: str) -> list[str] | None:
    parts: list[str] = []
    current: list[str] = []
    quoted = False
    escaped = False

    for character in value:
        if escaped:
            current.append(character)
            escaped = False
            continue
        if quoted and character == "\\":
            current.append(character)
            escaped = True
            continue
        if character == '"':
            current.append(character)
            quoted = not quoted
            continue
        if character == delimiter and not quoted:
            parts.append("".join(current))
            current = []
            continue
        current.append(character)

    if quoted or escaped:
        return None
    parts.append("".join(current))
    return parts


def _unquote_parameter(value: str) -> str | None:
    if not value.startswith('"'):
        return value if _TOKEN.fullmatch(value) else None
    if len(value) < 2 or not value.endswith('"'):
        return None

    unquoted: list[str] = []
    escaped = False
    for character in value[1:-1]:
        if escaped:
            unquoted.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == '"':
            return None
        else:
            unquoted.append(character)
    if escaped:
        return None
    return "".join(unquoted)


def _parse_parameterized_value(value: str) -> tuple[str, dict[str, str]] | None:
    parts = _split_quoted(value, ";")
    if not parts:
        return None

    token = parts[0].strip(" \t").casefold()
    if not token:
        return None

    parameters: dict[str, str] = {}
    for raw_parameter in parts[1:]:
        parameter = raw_parameter.lstrip(" \t")
        if "=" not in parameter:
            return None
        raw_name, raw_value = parameter.split("=", 1)
        if raw_name != raw_name.strip(" \t") or raw_value != raw_value.strip(" \t"):
            return None
        name = raw_name.casefold()
        if not _TOKEN.fullmatch(name) or _unquote_parameter(raw_value) is None or name in parameters:
            return None
        parameters[name] = raw_value
    return token, parameters


def _parse_quality(parameters: dict[str, str]) -> float | None:
    raw_quality = parameters.get("q", "1")
    if not _QUALITY_VALUE.fullmatch(raw_quality):
        return None
    return float(raw_quality)


def validate_content_type(content_type: str | None) -> None:
    if content_type is not None:
        parsed = _parse_parameterized_value(content_type.strip(" \t"))
        if parsed is not None:
            media_type, parameters = parsed
            if media_type == JSONAPI_MEDIA_TYPE and set(parameters).issubset({"profile"}):
                return
    raise JsonApiException(
        status_code=415,
        code="UNSUPPORTED_MEDIA_TYPE",
        source_header="Content-Type",
    )


def validate_accept(accept: str | None) -> None:
    if accept is None or not accept.strip(" \t"):
        return

    entries = _split_quoted(accept, ",")
    qualities_by_specificity: dict[int, list[float]] = {}
    if entries is not None:
        for entry in entries:
            parsed = _parse_parameterized_value(entry)
            if parsed is None:
                continue
            media_range, parameters = parsed
            quality = _parse_quality(parameters)
            if quality is None:
                continue
            if media_range == JSONAPI_MEDIA_TYPE and set(parameters).difference({"q", "profile"}):
                qualities_by_specificity.setdefault(2, []).append(0.0)
                continue
            allowed_parameters = {"q", "profile"} if media_range == JSONAPI_MEDIA_TYPE else {"q"}
            if set(parameters).difference(allowed_parameters):
                continue
            specificity = {
                JSONAPI_MEDIA_TYPE: 2,
                "application/*": 1,
                "*/*": 0,
            }.get(media_range)
            if specificity is not None:
                qualities_by_specificity.setdefault(specificity, []).append(quality)

    for specificity in (2, 1, 0):
        if specificity in qualities_by_specificity:
            if max(qualities_by_specificity[specificity]) > 0:
                return
            break

    raise JsonApiException(
        status_code=406,
        code="NOT_ACCEPTABLE",
        source_header="Accept",
    )


def require_jsonapi_accept(
    accept: Annotated[str | None, Header()] = None,
) -> None:
    validate_accept(accept)
