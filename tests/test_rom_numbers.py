"""Every number in the nodes' knowledge documents agrees with the farm simulation (owner's point 3).

`docs/knowledge/simulation_facts.toml` lists each simulation number with the code it comes from. These tests check:
1. every fact equals what the code says (read from the snapshot by regex, never imported);
2. every number in the machine manuals and shared documents, in digits or spelled out ("two" to "twelve", "twice"), is
   backed by a fact with the same value, unit and line context;
3. every fact is used, so the registry can't go stale;
4. in a domain guide, a number about something the simulation also has (a fact's case-insensitive `guide_context`
   matches its line, written for guide prose) must equal the simulation's value or be part of a range that contains
   it, or sit in the guide's "## Conflicts with the simulation" section: the machine manual wins and the conflict is
   noted there. On a humidity line a plain "%" counts as %RH.
Numbers are read in digits ("36-38 °C" gives both ends, "-10 °C" is negative, an ISO date is not a range) and spelled
out. Every node's `memory/rom/*.md` is found automatically; copies with the same name must be identical and are
checked once. Quoted rules (blockquotes) are checked word for word by test_rom_quotes.py instead.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

from knowledge import parse_document

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "_dev" / "farm_poc_snapshot"
FACTS: list[dict[str, Any]] = tomllib.loads(
    (ROOT / "docs" / "knowledge" / "simulation_facts.toml").read_text(encoding="utf-8"))["fact"]
CONFLICTS = "## Conflicts with the simulation"
UNITS: tuple[tuple[str, str], ...] = (  # (regex, canonical unit), longest first
    (r"°C", "°C"), (r"%\s?(?:RH\b|relative\s+humidity)", "%RH"), (r"%/m", "%/m"), (r"%", "%"), (r"ppm", "ppm"),
    (r"kPa", "kPa"), (r"hPa", "hPa"), (r"Pa\b", "Pa"), (r"kW\b", "kW"), (r"W\b", "W"), (r"V\b", "V"),
    (r"Hz\b", "Hz"), (r"A\b", "A"), (r"L/min", "L/min"), (r"L\b", "L"), (r"kg\b", "kg"), (r"m/s", "m/s"),
    (r"mm/h", "mm/h"), (r"cm\b", "cm"), (r"m³/h", "m³/h"), (r"m³", "m³"), (r"m²", "m²"), (r"m\b", "m"),
    (r"rpm\b", "rpm"), (r"°", "°"), (r"minutes?\b|min\b", "min"), (r"hours?\b|h\b", "h"), (r"days?\b", "day"),
    (r"seconds?\b|s\b", "s"),
)
_DIGITS = r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)"
NUMBER = re.compile(rf"(?<![\w.,\-−]){_DIGITS}(?![\w])")
RANGE_END = re.compile(rf"(?<=\d)\s?-\s?{_DIGITS}(?![\w])")            # "36-38 °C": the 38
NEGATIVE = re.compile(rf"(?:^|(?<=[\s(]))[-−]{_DIGITS}(?![\w])")        # "-10 °C"
ISO_DATE = re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}\b")                      # not a range
RANGE = re.compile(r"\s*(?:to|–|×)\s*[\d,.]+\s?")
RANGE_JOIN = re.compile(r"^\s*(?:to|–|-|and)\s*$")
HUMIDITY_LINE = re.compile(r"humid|\bRH\b", re.IGNORECASE)
WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
         "eleven": 11, "twelve": 12, "twice": 2}
WORD = re.compile(r"(?<![\w-])(" + "|".join(WORDS) + r")(?![\w])", re.IGNORECASE)


def discover(nodes_dir: Path = ROOT / "nodes") -> dict[str, list[Path]]:
    """Every node's knowledge documents, grouped by file name."""
    found: dict[str, list[Path]] = {}
    for path in sorted(nodes_dir.glob("*/memory/rom/*.md")):
        found.setdefault(path.name, []).append(path)
    return found


COPIES = discover()
DOCS = {name: paths[0] for name, paths in COPIES.items()}       # identical copies are checked once
KIND = {name: parse_document(path).meta.kind for name, path in DOCS.items()}
STRICT = sorted(name for name, kind in KIND.items() if kind != "domain guide")
GUIDES = sorted(name for name, kind in KIND.items() if kind == "domain guide")


@dataclass(frozen=True)
class Token:
    doc: str
    line_no: int
    line: str
    value: float
    unit: str
    section: str          # the "## " heading the number sits under ("" before the first one)
    start: int = 0
    end: int = 0
    range: tuple[float, float] | None = None      # both ends, when the number is one end of a range ("55–65 %")


def code_value(fact: dict[str, Any]) -> float:
    text = (SNAPSHOT / fact["file"]).read_text(encoding="utf-8")
    match = re.search(fact["pattern"], text)
    assert match, f"{fact['id']}: pattern not found in {fact['file']}"
    if "count_pattern" in fact:                                 # a count: how many items the code lists
        return float(len(re.findall(fact["count_pattern"], match.group(1))))
    x = float(match.group(1))
    y = float(match.group(2)) if match.lastindex and match.lastindex >= 2 else 0.0
    return float(eval(fact.get("expr", "x"), {"__builtins__": {}}, {"x": x, "y": y}))  # noqa: S307 - our own registry


def _unit_at(text: str, position: int) -> str:
    rest = text[position:]
    rest = rest[1:] if rest[:1] in (" ", " ", "-") else rest
    for pattern, unit in UNITS:
        if re.match(pattern, rest):
            return unit
    return ""


def _line_tokens(doc: str, line_no: int, line: str, section: str) -> list[Token]:
    clean = ISO_DATE.sub(lambda m: " " * len(m.group(0)), re.sub(r"`[^`]*`", lambda m: " " * len(m.group(0)), line))
    found: list[Token] = []
    for match in NUMBER.finditer(clean):
        unit = _unit_at(clean, match.end())
        if not unit:
            follow = RANGE.match(clean, match.end())
            if follow:
                unit = _unit_at(clean, follow.end() - 1) if clean[follow.end() - 1] == " " else _unit_at(clean, follow.end())
        found.append(Token(doc, line_no, line, float(match.group(1).replace(",", "")), unit, section, match.start(),
                           match.end()))
    for pattern, sign in ((RANGE_END, 1.0), (NEGATIVE, -1.0)):
        for match in pattern.finditer(clean):
            found.append(Token(doc, line_no, line, sign * float(match.group(1).replace(",", "")),
                               _unit_at(clean, match.end()), section, match.start(1), match.end()))
    for match in WORD.finditer(clean):
        found.append(Token(doc, line_no, line, float(WORDS[match.group(1).lower()]), _unit_at(clean, match.end()),
                           section, match.start(), match.end()))
    found.sort(key=lambda t: t.start)
    ranged = list(found)
    for i, (a, b) in enumerate(zip(found, found[1:])):           # "55–65 %", "20 to 25 ppm", "between 3 and 4"
        joined = RANGE_JOIN.match(clean[a.end:b.start])
        if joined and (joined.group(0).strip() != "and" or re.search(r"\bbetween\s*$", clean[:a.start])):
            bounds = (min(a.value, b.value), max(a.value, b.value))
            ranged[i] = replace(ranged[i], range=bounds, unit=a.unit or b.unit)
            ranged[i + 1] = replace(ranged[i + 1], range=bounds)
    return ranged


def tokens(doc: str, path: Path) -> list[Token]:
    """Every number in the document body with its unit; front matter, blockquotes and code spans left out."""
    text = path.read_text(encoding="utf-8")
    body = re.sub(r"\A\+\+\+.*?\+\+\+\n", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.DOTALL)
    found: list[Token] = []
    section = ""
    for line_no, line in enumerate(body.splitlines(), start=1):
        if line.startswith("## "):
            section = line.strip()
        if line.lstrip().startswith(">"):
            continue
        found += _line_tokens(doc, line_no, line, section)
    return found


def backing(token: Token) -> list[dict[str, Any]]:
    return [f for f in FACTS if token.doc in f["docs"] and abs(float(f["value"]) - token.value) < 1e-9
            and f["unit"] == token.unit and re.search(f["context"], token.line)]


def _same_unit(fact: dict[str, Any], token: Token) -> bool:
    if fact["unit"] == token.unit:
        return True
    return fact["unit"] == "%RH" and token.unit == "%" and bool(HUMIDITY_LINE.search(token.line))


def conflicts(token: Token) -> list[dict[str, Any]]:
    """For a domain guide: the facts this number is about (a fact's `guide_context` matches its line and the units
    agree) when it contradicts all of them. A range agrees when it contains the fact's value."""
    same = [f for f in FACTS if "guide_context" in f and _same_unit(f, token) and re.search(f["guide_context"],
                                                                                              token.line)]
    low, high = token.range or (token.value, token.value)
    return [] if any(low - 1e-9 <= float(f["value"]) <= high + 1e-9 for f in same) else same


def unnoted_conflicts(doc: str, path: Path) -> list[str]:
    return [f"line {t.line_no}: {t.value:g} {t.unit} but the simulation says "
            + ", ".join(f"{f['value']:g} {f['unit']} ({f['id']})" for f in found) + f" in: {t.line.strip()[:110]}"
            for t in tokens(doc, path) if t.section != CONFLICTS and (found := conflicts(t))]


@pytest.mark.parametrize("fact", FACTS, ids=[f["id"] for f in FACTS])
def test_every_fact_matches_the_simulation_code(fact: dict[str, Any]) -> None:
    assert abs(code_value(fact) - float(fact["value"])) < 1e-9, f"{fact['id']}: code says {code_value(fact)}"


def test_fact_ids_are_unique_and_documents_known() -> None:
    ids = [f["id"] for f in FACTS]
    assert len(ids) == len(set(ids))
    assert all(doc in STRICT for f in FACTS for doc in f["docs"])


def test_every_document_is_found_and_copies_are_identical() -> None:
    assert {"incubator_manual.md", "hvac_manual.md", "animal_protection_manual.md", "owner_rules.md",
            "safety_actions.md", "farm_layout.md", "contacts.md"} <= set(DOCS)
    for name, paths in COPIES.items():
        assert len({p.read_bytes() for p in paths}) == 1, f"the copies of {name} differ: {paths}"


@pytest.mark.parametrize("doc", STRICT)
def test_every_number_in_the_document_is_a_simulation_fact(doc: str) -> None:
    unbacked = [f"line {t.line_no}: {t.value:g} {t.unit or '(no unit)'} in: {t.line.strip()[:110]}"
                for t in tokens(doc, DOCS[doc]) if not backing(t)]
    assert unbacked == [], "\n".join(unbacked)


def test_every_fact_is_used_by_a_document() -> None:
    used = {f["id"] for doc in STRICT for t in tokens(doc, DOCS[doc]) for f in backing(t)}
    unused = [f["id"] for f in FACTS if f["id"] not in used]
    assert unused == [], f"facts no document uses (stale registry or a context that never matches): {unused}"


def test_domain_guides_note_every_conflict_with_the_simulation() -> None:
    """None is written yet (they wait for the owner's source approval); each one joins this check automatically."""
    problems = [f"{doc} {issue}" for doc in GUIDES for issue in unnoted_conflicts(doc, DOCS[doc])]
    assert problems == [], "\n".join(problems)


def test_a_wrong_number_would_be_caught(tmp_path: Path) -> None:
    """The check itself works: a manual line with a number the simulation doesn't have fails, in digits or words."""
    wrong = tmp_path / "hvac_manual.md"
    text = DOCS["hvac_manual.md"].read_text(encoding="utf-8")
    wrong.write_text(text.replace("about 850 kPa", "about 900 kPa"), encoding="utf-8")
    assert [t for t in tokens("hvac_manual.md", wrong) if not backing(t)]
    assert "serving three zones" in text
    wrong.write_text(text.replace("serving three zones", "serving four zones"), encoding="utf-8")
    assert [(t.value, t.unit) for t in tokens("hvac_manual.md", wrong) if not backing(t)] == [(4.0, "")]


def _guide(path: Path, lines: list[str]) -> Path:
    path.write_text('+++\nnode = "hvac"\ntitle = "Ventilation guide"\nkind = "domain guide"\nversion = 1\n'
                    'status = "draft"\nsources = [ { title = "A public source", link = "https://example.org/x" } ]\n'
                    "+++\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_a_guide_in_plain_prose_that_contradicts_the_simulation_must_note_it(tmp_path: Path) -> None:
    """The review's case: guide wording, not the manuals' table style (37.5 °C, 55 %RH and 70 %RH in the farm)."""
    contradicting = ["Set the incubator to 37.8 °C with 60 % relative humidity until day 18.",
                     "Raise the humidity to 65 % for the last three days."]
    guide = _guide(tmp_path / "incubation_guide.md", ["# Incubation", *contradicting,
                                                      "Turn the eggs at least 3 times a day."])
    found = unnoted_conflicts("incubation_guide.md", guide)
    assert len(found) == 3, found                              # 37.8 °C, 60 %RH and 65 %: the turning minimum agrees
    assert any("incubator.temperature_setpoint" in f for f in found)
    assert any("incubator.humidity_setpoint" in f for f in found)
    assert any("incubator.lockdown_humidity" in f for f in found)
    noted = _guide(guide, ["# Incubation", "See below.", "", CONFLICTS, "",
                           *(line + " The machine manual wins." for line in contradicting)])
    assert unnoted_conflicts("incubation_guide.md", noted) == []           # noted in their section: allowed


def test_agreeing_ranges_and_unrelated_numbers_pass_in_a_guide(tmp_path: Path) -> None:
    guide = _guide(tmp_path / "incubation_guide.md", [
        "# Incubation",
        "Forced-air incubation runs at 37.2–37.8 °C (99–100 °F); keep the humidity at 50-60 % until lockdown.",
        "During lockdown, raise the humidity to 65 to 75 %.",
        "Store eggs at 15 °C and 75 % relative humidity before setting them.",       # storage, not incubation
        "Panting starts near 30 °C; heat stress is serious above it.",
        "An oxygen-deficient atmosphere has less than 19.5 % oxygen.",
        "Clean the fan blades every 30 days; 12 birds per square metre is a common stocking density.",
    ])
    assert unnoted_conflicts("incubation_guide.md", guide) == []


def test_hyphen_ranges_negative_numbers_and_humidity_units_are_read() -> None:
    line = "Keep it at 36-38 °C, never -10 °C, and 60 % relative humidity; 2026-10-23 is a date, not a range."
    seen = [(t.value, t.unit, t.range) for t in _line_tokens("x.md", 1, line, "")]
    assert seen == [(36.0, "°C", (36.0, 38.0)), (38.0, "°C", (36.0, 38.0)), (-10.0, "°C", None), (60.0, "%RH", None)]
