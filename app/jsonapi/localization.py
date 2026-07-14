"""Language negotiation and localized JSON:API errors."""

from __future__ import annotations

import re
from collections.abc import Mapping

from app.jsonapi.documents import ErrorObject, JsonScalar
from app.jsonapi.errors import ERROR_CATALOG, ErrorCode, Language
from app.jsonapi.negotiation import _parse_parameterized_value, _parse_quality, _split_quoted

_LANGUAGE_RANGE = re.compile(r"(?:[A-Za-z]{1,8}(?:-[A-Za-z0-9]{1,8})*|\*)\Z")


def _supported_language(language_range: str) -> Language | None:
    primary = language_range.split("-", 1)[0]
    if primary == "ko":
        return "ko"
    if primary == "en":
        return "en"
    return None


def _effective_preference(candidates: list[tuple[int, float, int]]) -> tuple[float, int]:
    highest_specificity = max(candidate[0] for candidate in candidates)
    matching = [candidate for candidate in candidates if candidate[0] == highest_specificity]
    highest_quality = max(candidate[1] for candidate in matching)
    earliest_order = min(candidate[2] for candidate in matching if candidate[1] == highest_quality)
    return highest_quality, earliest_order


def resolve_language(accept_language: str | None) -> Language:
    if accept_language is None or not accept_language.strip(" \t"):
        return "ko"

    entries = _split_quoted(accept_language, ",")
    if entries is None:
        return "ko"

    explicit: dict[Language, list[tuple[int, float, int]]] = {"ko": [], "en": []}
    wildcard: list[tuple[int, float, int]] = []
    for order, entry in enumerate(entries):
        raw_parts = _split_quoted(entry, ";")
        if not raw_parts:
            continue
        language_range = raw_parts[0].strip(" \t").casefold()
        if not _LANGUAGE_RANGE.fullmatch(language_range):
            continue

        parsed = _parse_parameterized_value(entry)
        quality = 0.0
        if parsed is not None:
            _, parameters = parsed
            parsed_quality = _parse_quality(parameters)
            if parsed_quality is not None and not set(parameters).difference({"q"}):
                quality = parsed_quality

        if language_range == "*":
            wildcard.append((0, quality, order))
            continue
        supported = _supported_language(language_range)
        if supported is not None:
            specificity = len(language_range.split("-"))
            explicit[supported].append((specificity, quality, order))

    supported_languages: tuple[Language, ...] = ("ko", "en")
    preferences: dict[Language, tuple[float, int]] = {}
    for language in supported_languages:
        candidates = explicit[language] or wildcard
        if candidates:
            preferences[language] = _effective_preference(candidates)

    selected: Language | None = None
    selected_quality = 0.0
    selected_order = len(entries)
    for language in supported_languages:
        quality, order = preferences.get(language, (0.0, len(entries)))
        if quality > selected_quality or (quality == selected_quality > 0 and order < selected_order):
            selected = language
            selected_quality = quality
            selected_order = order
    return selected or "ko"


def localize_error(
    code: ErrorCode,
    *,
    language: Language,
    status: int | str | None = None,
    source_pointer: str | None = None,
    source_parameter: str | None = None,
    source_header: str | None = None,
    detail_args: Mapping[str, JsonScalar] | None = None,
    detail_override: str | None = None,
) -> ErrorObject:
    message = ERROR_CATALOG[language][code]
    detail = detail_override or message.detail.format(**(detail_args or {}))
    error: dict[str, object] = {
        "code": code,
        "title": message.title,
        "detail": detail,
    }
    if status is not None:
        error["status"] = str(status)

    source = {
        name: value
        for name, value in {
            "pointer": source_pointer,
            "parameter": source_parameter,
            "header": source_header,
        }.items()
        if value is not None
    }
    if source:
        error["source"] = source
    return ErrorObject.model_validate(error)
