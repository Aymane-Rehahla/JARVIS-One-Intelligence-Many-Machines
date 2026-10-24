"""Number verification (plan S18, ground rule 6): every number Jarvis says must match a tool result of the same turn.

`verify_reply(reply, evidence)` pulls every number out of a reply, pulls every number out of the turn's tool results
(any JSON: nested dicts and lists, and numbers written inside strings) and checks that each number said matches one
the tools returned. A reply passes (`VerifyReport.ok`) only when no number is left unmatched.

What counts as a number (`extract_numbers`):
    integers, decimals (37.5, .5), signed values (-3.5, +0.4, U+2212 minus), thousands separators (3,000 and
    1,207,615,488), percentages (55 %, 55%, 55 percent, 55 per cent) and values with a unit: °C, %, ppm, kPa, rpm,
    V, W, kW, GB, MB, B (bytes), ms, s, min, h, day, m/s, cm, L, written as symbols or words (37.5 °C, 37.5C,
    4 minutes, 12 volts). Units are normalised (`UNIT_ALIASES`). Spelled-out numbers ("three") are not read, and
    digits glued after letters (CO2, Qwen3.5, v1) are part of a word, not a number.

What is not a quantity and is never checked (listed in `VerifyReport.ignored` with the reason):
    times (14:05, 14:05:30, 2 pm), dates (2026-10-23, 23/10/2026, 23 October, Oct 23rd, October 2026), list
    markers at the start of a line (1. / 2) / (3)), names whose number is part of the name (incubator-1,
    incubator 2, fan 3, tray 4, when no unit follows; see `NAME_WORDS`), and whatever the caller allows: a number
    value in `allowed` (e.g. a farm tick) or a text in `allowed` (every number inside it, e.g. "Day 12").

When a reply number matches an evidence number (`Match.rule`):
    rounded              the evidence rounded to the decimals the reply wrote: 37.46 → 37.5 or 37 (ties either way)
    significant_figures  a whole number ending in zeros that keeps at least two significant figures: 1,187 → 1,200
                         (37.46 → 40 fails: one significant figure is too coarse to be a reading)
    relative             within `tolerance` of the evidence (default 0.1 %), for float noise and unit conversions
and the evidence value may first be converted (`Match.how`):
    direct    same unit, or one side has no known unit
    percent   a fraction between 0 and 1 said as a percentage: 0.553 → 55 % or 55.3 %
    scaled    a known conversion: bytes → GB or MB (decimal or binary), W ↔ kW, s ↔ min ↔ h, ms ↔ s,
              so 1,207,615,488 bytes may be said as 1.2 GB (or 1.12 GB, binary)
A reply number written with a sign must match the evidence's sign. An unsigned number may match a negative evidence
value only when a direction word sits next to it ("fell by 0.2 °C", "minus 2.4 °C", "0.4 °C below the setpoint").
Evidence units come from key suffixes (`temperature_c` → °C, `humidity_pct` → %, `ram_bytes` → B; keys containing
`_per_` have no unit) or from {"value": ..., "unit": ...} objects; the units must agree unless one side is unknown.

Known limits (by design): matching is by value and unit, not by meaning, so 37 °C said about the wrong incubator
passes when another 37 °C is in the evidence; numbers the model worked out itself (a difference, a sum) fail on
purpose, because a tool must return them; "14.05" is read as a decimal, not as a time.
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

SCHEMA_VERSION = 1
DEFAULT_TOLERANCE = 0.001          # relative; rounding to the written decimals is handled separately
MAX_TOLERANCE = 0.05
MAX_REPLY_CHARS = 20_000           # longer replies are refused, not truncated (Q4)
MAX_EVIDENCE_NUMBERS = 10_000      # evidence beyond this is not read; the report says so (Q4)
MAX_EVIDENCE_DEPTH = 32
DIRECTION_WINDOW_CHARS = 32

# Words that turn a following number into part of a name ("incubator 2", "fan 3"), when no unit follows it.
NAME_WORDS: tuple[str, ...] = (
    "incubator", "node", "server", "slot", "sensor", "probe", "fan", "heater", "room", "zone", "barn", "pen", "house",
    "tray", "camera", "gate", "valve", "shed", "bay", "phase", "step", "line", "unit", "jarvis",
)

# (pattern, canonical unit). Order matters: earlier patterns win, so longer spellings come first.
UNIT_ALIASES: tuple[tuple[str, str], ...] = (
    (r"°\s?C|º\s?C|deg\s?C|degrees?\s+(?:celsius|centigrade|C)|degrees?|celsius|°", "°C"),
    (r"%|percent|per\s?cent", "%"),
    (r"ppm", "ppm"),
    (r"kPa|kilopascals?", "kPa"),
    (r"rpm", "rpm"),
    (r"GiB|GB|gigabytes?", "GB"),
    (r"MiB|MB|megabytes?", "MB"),
    (r"bytes?", "B"),
    (r"kW|kilowatts?", "kW"),
    (r"watts?|W", "W"),
    (r"volts?|V", "V"),
    (r"m/s|metres?\s+per\s+second|meters?\s+per\s+second", "m/s"),
    (r"ms|milliseconds?", "ms"),
    (r"minutes?|mins?", "min"),
    (r"seconds?|secs?|s", "s"),
    (r"hours?|hrs?|h", "h"),
    (r"days?", "day"),
    (r"cm|centimetres?|centimeters?", "cm"),
    (r"litres?|liters?|L", "L"),
    (r"C", "°C"),
)

# Unit implied by an evidence key's suffix (lower case). Keys containing "_per_" are rates and get no unit.
KEY_UNITS: tuple[tuple[str, str], ...] = (
    ("_celsius", "°C"), ("_percent", "%"), ("_seconds", "s"), ("_minutes", "min"), ("_hours", "h"), ("_bytes", "B"),
    ("_litres", "L"), ("_liters", "L"), ("_days", "day"), ("_m_s", "m/s"), ("_mps", "m/s"), ("_pct", "%"),
    ("_ppm", "ppm"), ("_kpa", "kPa"), ("_rpm", "rpm"), ("_kw", "kW"), ("_sec", "s"), ("_min", "min"), ("_cm", "cm"),
    ("_gb", "GB"), ("_mb", "MB"), ("_ms", "ms"), ("_c", "°C"), ("_w", "W"), ("_v", "V"), ("_s", "s"), ("_h", "h"),
    ("_l", "L"),
)

# Unit conversions: (unit said, evidence unit) → factors to try on the evidence value.
SCALES: dict[tuple[str, str], tuple[float, ...]] = {
    ("GB", "B"): (1e-9, 2.0 ** -30), ("MB", "B"): (1e-6, 2.0 ** -20),
    ("GB", "MB"): (1e-3, 2.0 ** -10), ("MB", "GB"): (1e3, 2.0 ** 10),
    ("kW", "W"): (1e-3,), ("W", "kW"): (1e3,),
    ("min", "s"): (1 / 60,), ("h", "s"): (1 / 3600,), ("h", "min"): (1 / 60,),
    ("s", "min"): (60.0,), ("min", "h"): (60.0,), ("s", "ms"): (1e-3,), ("ms", "s"): (1e3,),
}

_UNIT_RES = tuple((re.compile(pattern, re.IGNORECASE), unit) for pattern, unit in UNIT_ALIASES)
_UNIT_ALTERNATION = "|".join(f"(?:{pattern})" for pattern, _ in UNIT_ALIASES)
_NUMBER_RE = re.compile(
    r"(?<![\w.])(?P<sign>[-+−])?"
    r"(?P<digits>[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?![0-9])"
    r"(?:(?P<ordinal>st|nd|rd|th)(?![A-Za-z]))?"
    rf"(?:[   ]?(?P<unit>{_UNIT_ALTERNATION})(?![A-Za-z]))?",
    re.IGNORECASE,
)
_MONTHS = (r"(?:January|February|March|April|May|June|July|August|September|October|November|December"
           r"|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)\.?")
_DATE_RES = (
    re.compile(r"(?<![\w.])[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}"
               r"(?:[T ][0-9]{1,2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]+)?)?(?:Z|[+-][0-9]{2}:?[0-9]{2})?)?(?!\w)"),
    re.compile(r"(?<![\w./])[0-9]{1,2}/[0-9]{1,2}(?:/[0-9]{2,4})?(?![\w/])"),
    re.compile(rf"(?<![\w.])[0-9]{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTHS}(?:,?\s+[0-9]{{4}})?(?!\w)"),
    re.compile(rf"\b{_MONTHS}\s+[0-9]{{1,2}}(?:st|nd|rd|th)?(?:,?\s+[0-9]{{4}})?(?!\w)"),
    re.compile(rf"\b{_MONTHS}\s+[0-9]{{4}}(?!\w)"),
)
_TIME_RES = (
    re.compile(r"(?<![\w.:])[0-9]{1,2}:[0-5][0-9](?::[0-5][0-9])?(?:\s?[ap]\.?m\.?)?(?![\w:])", re.IGNORECASE),
    re.compile(r"(?<![\w.])[0-9]{1,2}\s?(?:[ap]\.m\.|[ap]m)(?![A-Za-z])", re.IGNORECASE),
    re.compile(r"(?<![\w.])[0-9]{1,2}\s?o['’]clock", re.IGNORECASE),
)
_LIST_MARKER_RE = re.compile(r"^[ \t]*(?:[-*•][ \t]+)?\(?(?P<n>[0-9]{1,2})[.)](?=[ \t])", re.MULTILINE)
_DIRECTION_RE = re.compile(
    r"\b(?:fell|fall(?:s|ing|en)?|drop(?:s|ped|ping)?|down|below|under|lower|less|decreas\w*|declin\w*|cool(?:er|ed|ing)"
    r"|colder|minus|negative|short|behind|sank|sinking|reduc\w*)\b",
    re.IGNORECASE,
)

IgnoreReason = Literal["time", "date", "list_marker", "name", "allowed"]
Span = tuple[int, int, IgnoreReason]
MatchHow = Literal["direct", "percent", "scaled"]
MatchRule = Literal["rounded", "significant_figures", "relative"]


class ToolResult(BaseModel):
    """One tool result of the turn: the tool's name and the JSON it returned."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    data: JsonValue


class Number(BaseModel):
    """A number found in text: its value, normalised unit (None if it has none) and where it sits."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    text: str
    value: float
    unit: str | None = None
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    decimals: int = Field(ge=0)
    signed: bool = False

    @property
    def span(self) -> tuple[int, int]:
        """(start, end) character offsets in the text, end exclusive."""
        return self.start, self.end


class IgnoredNumber(BaseModel):
    """A number that is not a quantity (a time, a date, part of a name...) and was therefore not checked."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    number: Number
    reason: IgnoreReason


class EvidenceNumber(BaseModel):
    """A number found in the tool results, with its unit (if known) and where it came from (tool.key[index])."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    value: float
    unit: str | None = None
    path: str


class EvidenceSet(BaseModel):
    """Every number of the turn's tool results; `truncated` is True when the evidence was too big to read in full."""

    model_config = ConfigDict(extra="forbid")
    numbers: list[EvidenceNumber]
    truncated: bool = False


class Match(BaseModel):
    """A reply number and the evidence number it matched, with how the values were compared."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    number: Number
    evidence: EvidenceNumber
    how: MatchHow
    rule: MatchRule
    magnitude: bool = False


class VerifyReport(BaseModel):
    """The result of `verify_reply`. `ok` is True only when every number said matched the evidence."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = SCHEMA_VERSION
    ok: bool
    checked: int
    matched: list[Match]
    unmatched: list[Number]
    ignored: list[IgnoredNumber]
    evidence_numbers: int
    evidence_truncated: bool = False

    def problems(self) -> list[str]:
        """One readable line per unmatched number, for logs and the audit view."""
        return [f"{n.text!r} (characters {n.start}-{n.end}) matches no tool result of this turn" for n in self.unmatched]


def normalise_unit(text: str) -> str | None:
    """Return the canonical unit for a written unit ("degrees Celsius" → "°C"), or None if it is not known."""
    cleaned = text.strip()
    for pattern, unit in _UNIT_RES:
        if pattern.fullmatch(cleaned):
            return unit
    return None


def unit_for_key(key: str) -> str | None:
    """Return the unit implied by an evidence key ("temperature_c" → "°C"), or None."""
    lowered = key.lower()
    if "_per_" in lowered:
        return None
    for suffix, unit in KEY_UNITS:
        if lowered.endswith(suffix) or lowered == suffix[1:]:
            return unit
    return None


def _overlaps(start: int, end: int, spans: Iterable[Span]) -> IgnoreReason | None:
    for span_start, span_end, reason in spans:
        if start < span_end and span_start < end:
            return reason
    return None


def _spans(matches: Iterable[re.Match[str]], reason: IgnoreReason, group: int | str = 0) -> list[Span]:
    return [(m.start(group), m.end(group), reason) for m in matches]


def _masks(text: str, allowed_texts: Sequence[str]) -> list[Span]:
    """Spans that never hold a quantity: times, dates, list markers and the caller's allowed texts."""
    spans: list[Span] = []
    for pattern in _TIME_RES:
        spans += _spans(pattern.finditer(text), "time")
    for pattern in _DATE_RES:
        spans += _spans(pattern.finditer(text), "date")
    spans += _spans(_LIST_MARKER_RE.finditer(text), "list_marker", "n")
    for allowed in allowed_texts:
        if allowed:
            spans += _spans(re.finditer(re.escape(allowed), text, re.IGNORECASE), "allowed")
    return spans


def _name_spans(text: str, name_words: Sequence[str]) -> list[Span]:
    """Digits that follow a name word ("incubator 2"); they count as a name only when no unit follows them."""
    if not name_words:
        return []
    words = "|".join(re.escape(word) for word in name_words)
    pattern = re.compile(rf"\b(?:{words})s?[ \t]*(?:-|#|no\.?|number)?[ \t]*(?P<n>[0-9]{{1,3}})(?![0-9]|[.,][0-9])",
                         re.IGNORECASE)
    return _spans(pattern.finditer(text), "name", "n")


def _parse(match: re.Match[str]) -> Number:
    digits = match.group("digits")
    sign = match.group("sign")
    value = float(digits.replace(",", ""))
    if sign in ("-", "−"):
        value = -value
    unit_text = match.group("unit")
    decimals = len(digits.split(".", 1)[1]) if "." in digits else 0
    return Number(text=match.group(0), value=value, unit=normalise_unit(unit_text) if unit_text else None,
                  start=match.start(), end=match.end(), decimals=decimals, signed=sign is not None)


def scan_numbers(text: str, *, allowed: Iterable[float | int | str] = (),
                 name_words: Sequence[str] = NAME_WORDS) -> tuple[list[Number], list[IgnoredNumber]]:
    """Split the numbers of `text` into quantities (to be checked) and ignored numbers (times, dates, names...)."""
    allowed_items = list(allowed)
    allowed_texts = [item for item in allowed_items if isinstance(item, str)]
    allowed_values = [float(item) for item in allowed_items if not isinstance(item, str)]
    always = _masks(text, allowed_texts)
    names = _name_spans(text, name_words)
    quantities: list[Number] = []
    ignored: list[IgnoredNumber] = []
    for match in _NUMBER_RE.finditer(text):
        number = _parse(match)
        reason = _overlaps(number.start, number.end, always)
        if reason is None and number.unit is None:
            hyphen_name = (number.start >= 2 and not number.signed and text[number.start - 1] == "-"
                           and text[number.start - 2].isalpha())
            reason = "name" if hyphen_name else _overlaps(number.start, number.end, names)
        if reason is None and any(math.isclose(number.value, v, rel_tol=1e-9, abs_tol=1e-12) for v in allowed_values):
            reason = "allowed"
        if reason is None:
            quantities.append(number)
        else:
            ignored.append(IgnoredNumber(number=number, reason=reason))
    return quantities, ignored


def extract_numbers(text: str) -> list[Number]:
    """Return the quantities in `text`, in order: every number except times, dates, list markers and names."""
    return scan_numbers(text)[0]


class _Collector:
    """Walks arbitrary JSON and gathers its numbers, bounded in depth and count."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.numbers: list[EvidenceNumber] = []
        self.truncated = False

    def add(self, value: float, unit: str | None, path: str) -> None:
        if len(self.numbers) >= self.limit:
            self.truncated = True
            return
        self.numbers.append(EvidenceNumber(value=value, unit=unit, path=path))

    def walk(self, node: object, path: str, unit: str | None, depth: int) -> None:
        if self.truncated:
            return
        if depth > MAX_EVIDENCE_DEPTH:
            self.truncated = True
            return
        if isinstance(node, BaseModel):
            node = node.model_dump(mode="json")
        if node is None or isinstance(node, bool):
            return
        if isinstance(node, (int, float)):
            try:
                value = float(node)
            except OverflowError:
                return
            if math.isfinite(value):
                self.add(value, unit, path)
            return
        if isinstance(node, str):
            for number in extract_numbers(node[:MAX_REPLY_CHARS]):
                if math.isfinite(number.value):
                    self.add(number.value, number.unit or unit, path)
            return
        if isinstance(node, Mapping):
            items: Mapping[Any, Any] = node
            unit_field = items.get("unit")
            explicit = normalise_unit(unit_field) if isinstance(unit_field, str) else None
            for key, child in items.items():
                name = str(key)
                child_unit = explicit if (name == "value" and explicit) else (unit_for_key(name) or unit)
                self.walk(child, f"{path}.{name}" if path else name, child_unit, depth + 1)
            return
        if isinstance(node, (list, tuple)):
            sequence: Sequence[Any] = node
            for index, child in enumerate(sequence):
                self.walk(child, f"{path}[{index}]", unit, depth + 1)


def _as_tool_result(item: object) -> ToolResult | None:
    if isinstance(item, ToolResult):
        return item
    if isinstance(item, Mapping):
        mapping: Mapping[Any, Any] = item
        if set(mapping) == {"name", "data"} and isinstance(mapping["name"], str) and mapping["name"]:
            try:
                return ToolResult.model_validate(mapping)
            except ValueError:
                return None
    return None


def collect_evidence(evidence: Mapping[str, Any] | Sequence[Any] | ToolResult, *,
                     max_numbers: int = MAX_EVIDENCE_NUMBERS) -> EvidenceSet:
    """Gather every number of the turn's tool results: a list of ToolResult (or {"name", "data"} dicts) or any JSON."""
    collector = _Collector(max_numbers)
    if isinstance(evidence, ToolResult):
        collector.walk(evidence.data, evidence.name, None, 0)
    elif isinstance(evidence, (list, tuple)):
        items: Sequence[Any] = evidence
        for index, item in enumerate(items):
            tool = _as_tool_result(item)
            if tool is not None:
                collector.walk(tool.data, tool.name, None, 1)
            else:
                collector.walk(item, f"[{index}]", None, 1)
    else:
        collector.walk(evidence, "", None, 0)
    return EvidenceSet(numbers=collector.numbers, truncated=collector.truncated)


def _close(number: Number, target: float, tolerance: float) -> MatchRule | None:
    gap = abs(number.value - target)
    eps = 1e-9 * max(1.0, abs(target), abs(number.value))
    if gap <= 0.5 * 10.0 ** -number.decimals + eps:
        return "rounded"
    if number.decimals == 0 and number.value != 0 and math.isfinite(number.value):
        whole = str(int(abs(number.value)))
        zeros = len(whole) - len(whole.rstrip("0"))
        if zeros and len(whole) - zeros >= 2 and gap <= 0.5 * 10.0 ** zeros + eps:
            return "significant_figures"
    if gap <= tolerance * abs(target) + eps:
        return "relative"
    return None


def _targets(number: Number, evidence: EvidenceNumber, converted: bool) -> list[tuple[float, MatchHow]]:
    if not converted:
        if number.unit is None or evidence.unit is None or number.unit == evidence.unit:
            return [(evidence.value, "direct")]
        return []
    targets: list[tuple[float, MatchHow]] = []
    if number.unit == "%" and evidence.unit is None and abs(evidence.value) <= 1.0:
        targets.append((evidence.value * 100.0, "percent"))
    if number.unit is not None and evidence.unit is not None:
        for factor in SCALES.get((number.unit, evidence.unit), ()):
            targets.append((evidence.value * factor, "scaled"))
    return targets


def _has_direction_word(text: str, number: Number) -> bool:
    before = text[max(0, number.start - DIRECTION_WINDOW_CHARS):number.start]
    after = text[number.end:number.end + DIRECTION_WINDOW_CHARS]
    return bool(_DIRECTION_RE.search(before) or _DIRECTION_RE.search(after))


def match_number(number: Number, evidence: Sequence[EvidenceNumber], *, text: str = "",
                 tolerance: float = DEFAULT_TOLERANCE) -> Match | None:
    """Find the evidence number that `number` states, or None. `text` (the reply) enables direction words."""
    directional = bool(text) and not number.signed and _has_direction_word(text, number)
    for converted in (False, True):
        for candidate in evidence:
            for target, how in _targets(number, candidate, converted):
                rule = _close(number, target, tolerance)
                if rule is not None:
                    return Match(number=number, evidence=candidate, how=how, rule=rule)
                if directional and target < 0:
                    rule = _close(number, -target, tolerance)
                    if rule is not None:
                        return Match(number=number, evidence=candidate, how=how, rule=rule, magnitude=True)
    return None


def verify_reply(reply: str, evidence: Mapping[str, Any] | Sequence[Any] | ToolResult, *,
                 tolerance: float = DEFAULT_TOLERANCE, allowed: Iterable[float | int | str] = (),
                 name_words: Sequence[str] = NAME_WORDS) -> VerifyReport:
    """Check that every number in `reply` matches a number of this turn's tool results (`evidence`).

    `evidence` is a list of ToolResult (or {"name", "data"} dicts), or any JSON. `allowed` lists numbers (values) or
    texts that may appear without evidence, such as a farm tick. Raises ValueError on a bad tolerance or a reply
    longer than MAX_REPLY_CHARS.
    """
    if not 0.0 <= tolerance <= MAX_TOLERANCE:
        raise ValueError(f"tolerance must be between 0 and {MAX_TOLERANCE}, got {tolerance}")
    if len(reply) > MAX_REPLY_CHARS:
        raise ValueError(f"reply is {len(reply)} characters; verify_reply reads at most {MAX_REPLY_CHARS}")
    quantities, ignored = scan_numbers(reply, allowed=allowed, name_words=name_words)
    evidence_set = collect_evidence(evidence)
    matched: list[Match] = []
    unmatched: list[Number] = []
    for number in quantities:
        found = match_number(number, evidence_set.numbers, text=reply, tolerance=tolerance)
        if found is None:
            unmatched.append(number)
        else:
            matched.append(found)
    return VerifyReport(ok=not unmatched, checked=len(quantities), matched=matched, unmatched=unmatched,
                        ignored=ignored, evidence_numbers=len(evidence_set.numbers),
                        evidence_truncated=evidence_set.truncated)
