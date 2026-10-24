"""llm/verify.py: every number Jarvis says must match a tool result of the same turn (plan S18, ground rule 6)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

ROOT = Path(__file__).resolve().parents[2]
NODES = ("incubator-1", "incubator-2", "hvac", "animal-protection")
MODULE_NAME = "jarvis_llm_verify"


def _load_verify() -> ModuleType:
    """verify.py lives in a folder whose name has a hyphen, so it is loaded from its path (once per process)."""
    if MODULE_NAME in sys.modules:
        return sys.modules[MODULE_NAME]
    path = ROOT / "nodes" / "incubator-1" / "llm" / "verify.py"
    spec = importlib.util.spec_from_file_location(MODULE_NAME, path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


verify: Any = _load_verify()

STATUS = [
    {"name": "get_status", "data": {"node": "incubator-1", "farm_time": "14:05", "temperature_c": 37.46,
                                    "humidity_pct": 55.2, "co2_ppm": 812, "fan_rpm": 1187, "heater_power_w": 86,
                                    "ram_bytes": 1207615488, "outside_temperature_c": -2.4,
                                    "rate_c_per_min": -0.2, "since_last_turn_min": 47}},
    {"name": "get_assessment", "data": {"assessment": "NORMAL", "confidence": 0.553,
                                        "evidence": ["CO at 140 ppm near the door", "battery 64 % charged"]}},
]


def _values(text: str) -> list[tuple[float, str | None]]:
    return [(n.value, n.unit) for n in verify.extract_numbers(text)]


# --- extraction --------------------------------------------------------------------------------------------------

def test_the_four_nodes_hold_the_same_verify() -> None:
    copies = {(ROOT / "nodes" / node / "llm" / "verify.py").read_bytes() for node in NODES}
    assert len(copies) == 1


@pytest.mark.parametrize(("text", "expected"), [
    ("37", [(37.0, None)]),
    ("37.46 and .5", [(37.46, None), (0.5, None)]),
    ("-3.5, +0.4 and −2", [(-3.5, None), (0.4, None), (-2.0, None)]),
    ("3,000 eggs and 1,207,615,488 bytes", [(3000.0, None), (1207615488.0, "B")]),
    ("55 %, 55%, 55 percent, 55 per cent", [(55.0, "%")] * 4),
    ("37.5 °C, 37.5C, 37.5 degrees Celsius, 37.5°", [(37.5, "°C")] * 4),
    ("812 ppm, 101 kPa, 1450 rpm", [(812.0, "ppm"), (101.0, "kPa"), (1450.0, "rpm")]),
    ("230 V, 12 volts, 86 W, 2 kW", [(230.0, "V"), (12.0, "V"), (86.0, "W"), (2.0, "kW")]),
    ("8 GB, 512 MB", [(8.0, "GB"), (512.0, "MB")]),
    ("4 min, 4 minutes, 30 s, 30 seconds, 2 h, 2 hours, 250 ms",
     [(4.0, "min"), (4.0, "min"), (30.0, "s"), (30.0, "s"), (2.0, "h"), (2.0, "h"), (250.0, "ms")]),
    ("3.2 m/s, 6 cm, 1.5 L, 1,560 litres, 9 days", [(3.2, "m/s"), (6.0, "cm"), (1.5, "L"), (1560.0, "L"), (9.0, "day")]),
])
def test_extracts_numbers_values_and_units(text: str, expected: list[tuple[float, str | None]]) -> None:
    assert _values(text) == expected


def test_units_need_a_word_boundary() -> None:
    assert _values("5 sheep, 3 hens, 2 lambs, 7 very warm days") == [(5.0, None), (3.0, None), (2.0, None),
                                                                     (7.0, None)]


def test_spans_point_at_the_number_in_the_text() -> None:
    text = "Sir, incubator-1 reads 37.5 °C at 14:05."
    [number] = verify.extract_numbers(text)
    assert text[number.start:number.end] == number.text == "37.5 °C"
    assert number.span == (number.start, number.end) and number.decimals == 1 and not number.signed


def test_digits_inside_words_are_not_numbers() -> None:
    assert verify.extract_numbers("CO2 is fine; Qwen3.5 and bge-small v1.5 are loaded") == []


@pytest.mark.parametrize(("text", "reason"), [
    ("at 14:05", "time"), ("at 14:05:30", "time"), ("at 2 pm", "time"), ("at 7 a.m.", "time"),
    ("on 2026-10-23", "date"), ("on 23/10/2026", "date"), ("on 23 October", "date"), ("on Oct 23rd, 2026", "date"),
    ("in October 2026", "date"),
    ("incubator-1", "name"), ("incubator 2", "name"), ("Incubator-2", "name"), ("fan 3", "name"), ("tray #4", "name"),
])
def test_times_dates_and_names_are_not_quantities(text: str, reason: str) -> None:
    quantities, ignored = verify.scan_numbers(text)
    assert quantities == [] and ignored and {item.reason for item in ignored} == {reason}


def test_list_markers_are_not_quantities_but_the_values_after_them_are() -> None:
    text = "1. Temperature 37.5 °C\n2) Humidity 55 %\n(3) Fan normal"
    quantities, ignored = verify.scan_numbers(text)
    assert [(n.value, n.unit) for n in quantities] == [(37.5, "°C"), (55.0, "%")]
    assert [item.reason for item in ignored] == ["list_marker"] * 3


def test_a_name_word_followed_by_a_unit_is_a_reading_not_a_name() -> None:
    assert _values("the heater 86 W and fan 1450 rpm") == [(86.0, "W"), (1450.0, "rpm")]


# --- verification ------------------------------------------------------------------------------------------------

def test_a_grounded_reply_passes_and_reports_each_match() -> None:
    reply = ("As of 14:05 farm time, sir, incubator-1 holds 37.5 °C at 55 % humidity, CO2 812 ppm, "
             "fan 1,200 rpm; the heater draws 86 W.")
    report = verify.verify_reply(reply, STATUS)
    assert report.ok and report.checked == 5 and report.unmatched == []
    rules = {m.number.text: m.rule for m in report.matched}
    assert rules == {"37.5 °C": "rounded", "55 %": "rounded", "812 ppm": "rounded", "1,200 rpm": "significant_figures",
                     "86 W": "rounded"}
    assert {i.reason for i in report.ignored} == {"time", "name"}
    assert report.matched[0].evidence.path == "get_status.temperature_c"


def test_a_wrong_number_fails_with_its_span() -> None:
    reply = "Sir, incubator-1 is at 38.2 °C."
    report = verify.verify_reply(reply, STATUS)
    assert not report.ok
    [bad] = report.unmatched
    assert reply[bad.start:bad.end] == "38.2 °C"
    assert report.problems() == ["'38.2 °C' (characters 23-30) matches no tool result of this turn"]


@pytest.mark.parametrize(("reply", "ok"), [
    ("37.5 °C", True), ("37 °C", True), ("37.46 °C", True), ("37.4 °C", False), ("40 °C", False),
    ("55 % confidence", True), ("55.3 %", True), ("56 % confidence", False),
    ("1.2 GB", True), ("1.12 GB", True), ("1.3 GB", False),
    ("-2.4 °C outside", True), ("2.4 °C outside", False), ("minus 2.4 °C", True), ("2.4 °C below freezing", True),
    ("+2.4 °C outside", False), ("falling 0.2 °C per minute", True), ("rising 0.2 °C per minute", False),
    ("47 minutes ago", True), ("47 seconds ago", False),
    ("CO near the door is 140 ppm", True), ("the battery holds 64 %", True),
    ("55 °C", False),            # 55 exists, but as a humidity percentage, not a temperature
])
def test_rounding_percentages_conversions_signs_and_units(reply: str, ok: bool) -> None:
    assert verify.verify_reply(reply, STATUS).ok is ok, reply


def test_a_derived_number_fails_on_purpose() -> None:
    evidence = {"temperature_c": 38.0, "setpoint_c": 37.5}
    assert not verify.verify_reply("0.5 °C above the setpoint", evidence).ok


def test_evidence_may_be_any_json_nested_in_lists_and_strings() -> None:
    evidence = {"peers": [{"node": "hvac", "readings": {"ammonia_ppm": [9.5, 11.25]}},
                          {"node": "animal-protection", "events": ["03:12 gate opened 6 times"]}]}
    collected = verify.collect_evidence(evidence)
    assert [(n.value, n.unit) for n in collected.numbers] == [(9.5, "ppm"), (11.25, "ppm"), (6.0, None)]
    assert collected.numbers[1].path == "peers[0].readings.ammonia_ppm[1]"
    assert verify.verify_reply("Ammonia peaked at 11.3 ppm; the gate opened 6 times.", evidence).ok


def test_tool_results_may_be_models_dicts_or_value_unit_objects() -> None:
    tool = verify.ToolResult(name="get_trend", data={"max": {"value": 37.52, "unit": "degrees C"}, "minutes": 30})
    assert verify.verify_reply("a high of 37.5 °C over 30 minutes", [tool]).ok
    assert not verify.verify_reply("a high of 37.5 % over 30 minutes", [tool]).ok
    assert verify.collect_evidence([tool]).numbers[0].path == "get_trend.max.value"


def test_booleans_and_non_finite_values_are_not_evidence() -> None:
    evidence = {"door_open": True, "bad": float("nan"), "worse": float("inf"), "count": 1}
    assert [n.value for n in verify.collect_evidence(evidence).numbers] == [1.0]


def test_allowed_numbers_and_texts_need_no_evidence() -> None:
    report = verify.verify_reply("Tick 48213, day 12 of incubation.", {}, allowed=[48213, "day 12"])
    assert report.ok and report.checked == 0 and {i.reason for i in report.ignored} == {"allowed"}
    assert not verify.verify_reply("Tick 48213, day 12 of incubation.", {}).ok


def test_evidence_is_bounded() -> None:
    big = {"values": list(range(verify.MAX_EVIDENCE_NUMBERS + 5))}
    collected = verify.collect_evidence(big)
    assert collected.truncated and len(collected.numbers) == verify.MAX_EVIDENCE_NUMBERS
    deep: Any = 1.0
    for _ in range(verify.MAX_EVIDENCE_DEPTH + 5):
        deep = [deep]
    assert verify.collect_evidence({"x": deep}).truncated
    report = verify.verify_reply("1", big)
    assert report.ok and report.evidence_truncated


def test_bad_inputs_raise_clear_errors() -> None:
    with pytest.raises(ValueError, match="tolerance"):
        verify.verify_reply("1", {}, tolerance=0.5)
    with pytest.raises(ValueError, match="characters"):
        verify.verify_reply("1 " * verify.MAX_REPLY_CHARS, {})


def test_a_reply_without_numbers_passes_trivially() -> None:
    report = verify.verify_reply("Good evening, sir. All is quiet.", [])
    assert report.ok and report.checked == 0 and report.evidence_numbers == 0


def test_the_report_is_plain_json() -> None:
    report = verify.verify_reply("37.5 °C and 99 ppm", STATUS)
    again = verify.VerifyReport.model_validate_json(report.model_dump_json())
    assert again == report and again.schema_version == 1


# --- properties ----------------------------------------------------------------------------------------------------

finite = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False)


@settings(derandomize=True, max_examples=300)
@given(value=finite, decimals=st.integers(0, 3), thousands=st.booleans())
def test_any_evidence_value_said_with_standard_rounding_passes(value: float, decimals: int, thousands: bool) -> None:
    said = f"{value:,.{decimals}f}" if thousands else f"{value:.{decimals}f}"
    reply = f"As of 14:05 farm time, sir, incubator-2 reads {said} °C."
    report = verify.verify_reply(reply, [{"name": "get_status", "data": {"temperature_c": value}}])
    assert report.ok, (said, report.problems())


@settings(derandomize=True, max_examples=200)
@given(fraction=st.floats(min_value=0.0, max_value=1.0), decimals=st.integers(0, 2))
def test_any_fraction_said_as_a_rounded_percentage_passes(fraction: float, decimals: int) -> None:
    reply = f"I am {fraction * 100:.{decimals}f} % confident, sir."
    assert verify.verify_reply(reply, {"confidence": fraction}).ok


@settings(derandomize=True, max_examples=200)
@given(evidence=st.lists(st.floats(min_value=0.0, max_value=100.0), max_size=20), said=st.integers(1000, 9999))
def test_a_number_absent_from_the_evidence_fails(evidence: list[float], said: int) -> None:
    report = verify.verify_reply(f"The fan turns at {said} rpm, sir.", {"readings": evidence})
    assert not report.ok and [n.value for n in report.unmatched] == [float(said)]


@settings(derandomize=True, max_examples=200)
@given(word=st.sampled_from(["incubator", "Incubator", "INCUBATOR", "fan", "tray", "pen", "sensor"]),
       separator=st.sampled_from(["-", " ", " #"]), index=st.integers(0, 999))
def test_node_names_never_count_as_numbers(word: str, separator: str, index: int) -> None:
    report = verify.verify_reply(f"{word}{separator}{index} is behaving, sir.", [])
    assert report.ok and report.checked == 0 and [i.reason for i in report.ignored] == ["name"]


UNITS = [("°C", "°C"), (" °C", "°C"), ("C", "°C"), (" %", "%"), ("%", "%"), (" ppm", "ppm"), (" kPa", "kPa"),
         (" rpm", "rpm"), (" V", "V"), (" W", "W"), (" GB", "GB"), (" min", "min"), (" s", "s"), (" h", "h"),
         (" m/s", "m/s"), (" cm", "cm"), (" L", "L"), ("", None)]


@settings(derandomize=True, max_examples=300)
@given(value=finite, decimals=st.integers(0, 3), unit=st.sampled_from(UNITS))
def test_extraction_round_trips_formatted_numbers(value: float, decimals: int, unit: tuple[str, str | None]) -> None:
    said = f"{value:.{decimals}f}"
    text = f"reading {said}{unit[0]} now"
    [number] = verify.extract_numbers(text)
    assert number.value == float(said) and number.unit == unit[1] and number.decimals == decimals
    assert text[number.start:number.end] == number.text
