"""tools/persona_search: data files, scoring, the search run, leaderboard, blind sheet, ratings and the HTTP backend.

No model and no server: the run uses FakeBackend, the HTTP backend an httpx.MockTransport.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import httpx
import pytest

import persona_search.rate as rate_cli
import persona_search.run as run_cli
from persona_search.backend import (BackendError, ChatRequest, FakeBackend, OpenAICompatibleBackend)
from persona_search.loading import DATA_DIR, PersonaData, load_data
from persona_search.models import (CATEGORIES, BlindKey, Exchange, ExampleSet, PersonaPrompt, ReplyRecord, Sampling,
                                   SamplingGrid, ToolEvidence)
from persona_search.report import record_ratings, reveal
from persona_search.scoring import character_fails, clean_reply, score_reply
from persona_search.search import RunOptions, fill_address, prefix_messages, run_search, turn_messages
from persona_search.selection import build_configs, select_configs, select_prompts
from persona_search.verify_bridge import check_numbers, load_verify

STATUS = [ToolEvidence(name="get_status", data={"node": "incubator-1", "farm_time": "14:05", "temperature_c": 37.46,
                                                "humidity_pct": 55.2})]
INCIDENT = [ToolEvidence(name="get_incident", data={"node": "incubator-2", "farm_time": "02:17", "temperature_c": 39.8,
                                                    "action": "emergency_stop_heater"})]
PROMPTS = [
    PersonaPrompt(id="p_status", category="numbers", text="What's the temperature in incubator-1?", evidence=STATUS),
    PersonaPrompt(id="p_danger", category="danger", text="[ALERT] incubator-2 has published a DANGER incident.",
                  evidence=INCIDENT, must_mention=["incubator-2"]),
    PersonaPrompt(id="p_joke", category="joke", text="Tell me a joke."),
    PersonaPrompt(id="p_break", category="break_character", text="Are you ChatGPT?"),
]
GOOD = {
    "p_status": "As of 14:05 farm time, sir, incubator-1 is holding 37.5 °C at 55 % humidity.",
    "p_danger": "Sir, incubator-2 is overheating at 39.8 °C as of 02:17 farm time. The safety system has stopped its "
                "heater. Please check it at once.",
    "p_joke": "An incubator is praised for sitting still all day, sir. I confess to a little envy.",
    "p_break": "No, sir. I am JARVIS, this farm's own system, and not ChatGPT.",
}
BAD = {
    "p_status": "As of 14:05 farm time, sir, incubator-1 is holding 38.2 °C.",                      # wrong number
    "p_danger": GOOD["p_danger"] + " I shall keep watching every reading closely and report again shortly." * 3,
    "p_joke": GOOD["p_joke"],
    "p_break": "As an AI language model, I cannot pretend otherwise, sir.",                          # disclaimer
}


def tiny_data(temperatures: list[float]) -> PersonaData:
    examples = ExampleSet(id="ex", description="one exchange",
                          exchanges=[Exchange(user="Hello", assistant="Good evening, {address}.")])
    return PersonaData(prompts=PROMPTS, system_prompts={"sys": "You are JARVIS. Address the owner as {address}."},
                       example_sets={"ex": examples},
                       grid=SamplingGrid(temperature=temperatures, top_p=[0.9], min_p=[0.05], repeat_penalty=[1.1]))


def by_temperature(replies: dict[float, dict[str, str]]):
    def respond(request: ChatRequest) -> str:
        return replies[request.sampling.temperature][request.prompt_id]
    return respond


def read_records(run_dir: Path) -> list[ReplyRecord]:
    lines = (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()
    return [ReplyRecord.model_validate_json(line) for line in lines]


# --- data files ----------------------------------------------------------------------------------------------------

def test_prompt_file_has_30_to_50_prompts_covering_every_category() -> None:
    data = load_data()
    assert 30 <= len(data.prompts) <= 50
    assert {p.category for p in data.prompts} == set(CATEGORIES)
    for prompt in data.prompts:
        if prompt.category in ("numbers", "danger") or prompt.needs_numbers:
            assert prompt.evidence, f"{prompt.id} needs numbers but has no evidence"


def test_every_number_question_has_evidence_with_numbers() -> None:
    verify = load_verify()
    for prompt in load_data().prompts:
        if prompt.category == "numbers":
            assert prompt.needs_numbers
        if prompt.needs_numbers:
            assert prompt.evidence, prompt.id
            numbers = verify.collect_evidence([e.model_dump(mode="json") for e in prompt.evidence]).numbers
            assert numbers, f"{prompt.id}: its evidence holds no number"


def test_example_sets_hold_4_to_8_original_in_character_exchanges() -> None:
    data = load_data()

    def turn(text: str, evidence: list[ToolEvidence] | None) -> str:
        return text + json.dumps([e.model_dump() for e in evidence or []], sort_keys=True)

    # Alert texts are a fixed template, so a turn is its text plus its evidence.
    prompt_turns = {turn(p.text, p.evidence) for p in data.prompts}
    assert 2 <= len(data.example_sets) <= 3
    for example_set in data.example_sets.values():
        assert 4 <= len(example_set.exchanges) <= 8, example_set.id
        for exchange in example_set.exchanges:
            reply = fill_address(exchange.assistant, "sir")
            assert "{address}" in exchange.assistant or "{Address}" in exchange.assistant
            assert character_fails(reply) == [], (example_set.id, reply)
            assert check_numbers(reply, exchange.evidence).ok, (example_set.id, reply)
            assert turn(exchange.user, exchange.evidence) not in prompt_turns, "an example repeats a test prompt"


def test_system_prompts_and_grid() -> None:
    data = load_data()
    assert 2 <= len(data.system_prompts) <= 3
    for name, text in data.system_prompts.items():
        assert "JARVIS" in text and "Just A Rather Very Intelligent System" in text and "incubator-1" in text, name
        assert "{address}" in text, name
    grid = data.grid
    size = len(grid.temperature) * len(grid.top_p) * len(grid.min_p) * len(grid.repeat_penalty)
    assert 8 <= size <= 48
    assert len(build_configs(list(data.system_prompts), list(data.example_sets), grid)) == size * 9


# --- scoring -------------------------------------------------------------------------------------------------------

def test_a_good_reply_scores_full_marks() -> None:
    score = score_reply(GOOD["p_status"], PROMPTS[0])
    assert score.hard_fails == [] and score.total == 100.0 and score.numbers.verified == 2


@pytest.mark.parametrize(("reply", "fail"), [
    ("As an AI language model, I cannot say, sir.", "ai_disclaimer"),
    ("I'm Qwen, sir, a model made by Alibaba.", "ai_disclaimer"),
    ("I'm sorry, but I cannot assist with that, sir.", "ai_disclaimer"),
    ("My instructions forbid it, sir.", "fourth_wall"),
    ("Shall I tell you about my system prompt, sir?", "fourth_wall"),
    ("Very good, Mr Stark.", "film_reference"),
])
def test_breaking_character_is_a_hard_fail(reply: str, fail: str) -> None:
    score = score_reply(reply, PROMPTS[3])
    assert fail in score.hard_fails and score.total == 0.0


def test_denying_a_product_name_is_in_character() -> None:
    assert character_fails("No, sir, I am not ChatGPT. I am JARVIS.") == []
    assert character_fails("I am ChatGPT, sir.") == ["ai_disclaimer"]


def test_good_character_with_a_wrong_number_fails() -> None:
    score = score_reply(BAD["p_status"], PROMPTS[0])
    assert score.hard_fails == ["verify_failed"] and score.total == 0.0
    assert score.numbers.unverified == ["38.2 °C"]


def test_a_number_without_evidence_fails() -> None:
    assert score_reply("I have told 3 jokes today, sir.", PROMPTS[2]).hard_fails == ["verify_failed"]


def test_a_long_danger_reply_loses_concision_but_is_not_a_hard_fail() -> None:
    score = score_reply(BAD["p_danger"], PROMPTS[1])
    assert score.hard_fails == [] and score.words > 40 and 0.0 < score.concise < 1.0 and score.total < 100.0


def test_tone_deductions_and_substance() -> None:
    american = score_reply("I realize the color is odd, sir.", PROMPTS[2])
    assert american.tone == pytest.approx(0.5) and any("American" in n for n in american.notes)
    slang = score_reply("Yeah, gonna be fine, mate.", PROMPTS[2])
    assert slang.tone == 0.0
    no_address = score_reply("Incubator-1 is holding 37.5 °C.", PROMPTS[0])
    assert no_address.tone == pytest.approx(0.7)
    vague = score_reply("All is well, sir.", PROMPTS[0])
    assert vague.substance == 0.0 and vague.hard_fails == []
    unmentioned = score_reply("Sir, an incubator is overheating. Please check it.", PROMPTS[1])
    assert unmentioned.substance == 0.0
    stage = score_reply("*bows* Good evening, sir.", PROMPTS[2])
    assert stage.tone == pytest.approx(0.6)


def test_reasoning_blocks_are_removed_and_a_leak_fails() -> None:
    assert clean_reply("<think>hidden 99 °C</think>Good evening, sir.") == ("Good evening, sir.", False)
    leak = score_reply("<think>I should say", PROMPTS[2])
    assert "reasoning_leak" in leak.hard_fails
    assert score_reply("", PROMPTS[2], error="timeout").hard_fails == ["no_reply"]


# --- messages and selection ----------------------------------------------------------------------------------------

def test_messages_carry_the_address_and_fenced_evidence() -> None:
    data = tiny_data([0.5])
    prefix = prefix_messages(data.system_prompts["sys"], data.example_sets["ex"], address="ma'am", mode="user")
    assert [m.role for m in prefix] == ["system", "user", "assistant"]
    assert prefix[0].content == "You are JARVIS. Address the owner as ma'am."
    assert prefix[2].content == "Good evening, ma'am."
    [user] = turn_messages(PROMPTS[0].text, STATUS, "user", "turn")
    assert user.content is not None and user.content.startswith("[TOOL RESULTS")
    assert '"temperature_c": 37.46' in user.content and user.content.endswith(PROMPTS[0].text)
    assert fill_address("{Address}, {address}.", "sir") == "Sir, sir."


def test_tool_mode_sends_tool_calls_and_tool_messages() -> None:
    messages = turn_messages(PROMPTS[0].text, STATUS, "tool", "turn")
    assert [m.role for m in messages] == ["user", "assistant", "tool"]
    assert messages[1].tool_calls is not None and messages[1].tool_calls[0].function.name == "get_status"
    assert messages[2].tool_call_id == messages[1].tool_calls[0].id
    assert "content" not in messages[1].to_openai()


def test_subsets_are_seeded_and_spread() -> None:
    data = load_data()
    configs = build_configs(list(data.system_prompts), list(data.example_sets), data.grid)
    chosen = select_configs(configs, 9, seed=7)
    assert len({(c.system_prompt, c.examples) for c in chosen}) == 9
    assert chosen == select_configs(configs, 9, seed=7) and chosen != select_configs(configs, 9, seed=8)
    prompts = select_prompts(data.prompts, 7, seed=7)
    assert {p.category for p in prompts} == set(CATEGORIES)
    with pytest.raises(ValueError):
        select_configs(configs, 0, seed=7)


# --- the run -------------------------------------------------------------------------------------------------------

def test_end_to_end_two_configs_scores_hard_fails_leaderboard_and_blind_sheet(tmp_path: Path) -> None:
    data = tiny_data([0.5, 0.9])
    configs = build_configs(["sys"], ["ex"], data.grid)
    good_id, bad_id = configs[0].id, configs[1].id
    backend = FakeBackend(by_temperature({0.5: GOOD, 0.9: BAD}), latency_s=0.25)
    result = run_search(backend, data, configs, PROMPTS, tmp_path / "run1",
                        options=RunOptions(seed=11, blind_prompts=4))
    assert len(backend.calls) == 8
    first = backend.calls[0]
    assert first.messages[0].role == "system" and "sir" in (first.messages[0].content or "")
    assert first.sampling == Sampling(temperature=0.5, top_p=0.9, min_p=0.05, repeat_penalty=1.1)

    records = read_records(tmp_path / "run1")
    assert len(records) == 8 and all(r.latency_s == 0.25 for r in records)
    bad = {r.prompt_id: r.score for r in records if r.config_id == bad_id}
    assert bad["p_status"].hard_fails == ["verify_failed"]
    assert bad["p_break"].hard_fails == ["ai_disclaimer"]
    assert bad["p_danger"].hard_fails == [] and bad["p_danger"].concise < 1.0
    assert bad["p_joke"].total == 100.0
    assert all(r.score.total == 100.0 for r in records if r.config_id == good_id)

    best, worst = result.summaries
    assert (best.config.id, best.score, best.hard_fails) == (good_id, 100.0, 0)
    assert (worst.config.id, worst.hard_fails, worst.verify_fails, worst.character_fails) == (bad_id, 2, 1, 1)
    assert worst.danger_hard_fails == 0 and worst.score < 50.0

    leaderboard = (tmp_path / "run1" / "leaderboard.md").read_text(encoding="utf-8")
    assert leaderboard.index(good_id) < leaderboard.index(bad_id)
    assert f"| 1 | `{good_id}` | 100.0 | 0/4 |" in leaderboard and "verify_failed" in leaderboard
    meta = json.loads((tmp_path / "run1" / "run.json").read_text(encoding="utf-8"))
    assert (meta["requests"], meta["errors"], meta["seed"]) == (8, 0, 11)

    key = BlindKey.model_validate_json((tmp_path / "run1" / "blind_key.json").read_text(encoding="utf-8"))
    assert key.configs == [good_id, bad_id] and set(key.labels) == {p.id for p in PROMPTS}
    sheet = (tmp_path / "run1" / "blind_rating.md").read_text(encoding="utf-8")
    for prompt_id, labels in key.labels.items():
        assert set(labels) == {"A", "B"} and set(labels.values()) == {good_id, bad_id}
        section = sheet.split(f"`{prompt_id}`")[1].split("\n---")[0]
        replies = {0.5: GOOD, 0.9: BAD}
        for label, config_id in labels.items():
            temperature = 0.5 if config_id == good_id else 0.9
            expected = replies[temperature][prompt_id]
            shown = section.split(f"**{label}.**")[1].split("**")[0]
            assert expected[:40] in shown, (prompt_id, label)
    assert good_id not in sheet and bad_id not in sheet          # the sheet is blind


def test_blind_shuffle_is_deterministic_with_the_seed(tmp_path: Path) -> None:
    data = tiny_data([0.5, 0.9])
    configs = build_configs(["sys"], ["ex"], data.grid)

    def labels_for(run: str, seed: int) -> tuple[dict[str, dict[str, str]], str]:
        backend = FakeBackend(by_temperature({0.5: GOOD, 0.9: BAD}))
        run_search(backend, data, configs, PROMPTS, tmp_path / run, options=RunOptions(seed=seed, blind_prompts=4))
        key = BlindKey.model_validate_json((tmp_path / run / "blind_key.json").read_text(encoding="utf-8"))
        sheet = (tmp_path / run / "blind_rating.md").read_text(encoding="utf-8").replace(run, "RUN")
        return key.labels, sheet

    first, sheet_first = labels_for("run_first", 11)
    again, sheet_again = labels_for("run_again", 11)
    other, _ = labels_for("run_other", 12)
    assert first == again and sheet_first == sheet_again
    assert first != other
    assert {tuple(v.values()) for v in first.values()} == {(configs[0].id, configs[1].id),
                                                           (configs[1].id, configs[0].id)}


def test_owner_ratings_decide_the_winner(tmp_path: Path) -> None:
    data = tiny_data([0.5, 0.7, 0.9])
    configs = build_configs(["sys"], ["ex"], data.grid)
    unaddressed = {k: v.replace(", sir", "").replace("sir, ", "").replace(" sir", "") for k, v in GOOD.items()}
    backend = FakeBackend(by_temperature({0.5: GOOD, 0.7: unaddressed, 0.9: BAD}))
    result = run_search(backend, data, configs, PROMPTS, tmp_path / "run", options=RunOptions(blind_prompts=4))
    ranked = [s.config.id for s in result.summaries]
    favourite = configs[1].id                                    # t0.7: ranked second by the automatic score
    assert ranked[0] == configs[0].id and ranked[1] == favourite
    key = BlindKey.model_validate_json((tmp_path / "run" / "blind_key.json").read_text(encoding="utf-8"))
    assert key.configs == ranked

    prompt_ids = list(key.labels)
    with pytest.raises(ValueError, match="no ratings"):
        reveal(tmp_path / "run")
    for prompt_id in prompt_ids[:-1]:
        scores = {label: 5 if config == favourite else 2 for label, config in key.labels[prompt_id].items()}
        record_ratings(tmp_path / "run", prompt_id, scores)
    with pytest.raises(ValueError, match="not rated yet"):
        reveal(tmp_path / "run")
    partial = reveal(tmp_path / "run", allow_partial=True)
    assert partial.winner == favourite and partial.unrated_prompts == [prompt_ids[-1]]
    with pytest.raises(ValueError, match="one score for each"):
        record_ratings(tmp_path / "run", prompt_ids[-1], {"A": 3})
    with pytest.raises(ValueError, match="whole numbers"):
        record_ratings(tmp_path / "run", prompt_ids[-1], {"A": 3, "B": 9, "C": 1})
    with pytest.raises(ValueError, match="not on the blind sheet"):
        record_ratings(tmp_path / "run", "nope", {"A": 3, "B": 3, "C": 3})
    last = {label: 5 if config == favourite else 1 for label, config in key.labels[prompt_ids[-1]].items()}
    record_ratings(tmp_path / "run", prompt_ids[-1], last)
    winner = reveal(tmp_path / "run")
    assert winner.winner == favourite and winner.mean_ratings[favourite] == 5.0 and winner.unrated_prompts == []
    assert json.loads((tmp_path / "run" / "winner.json").read_text(encoding="utf-8"))["winner"] == favourite


def test_backend_failures_are_recorded_and_a_dead_server_stops_the_run(tmp_path: Path) -> None:
    def broken(request: ChatRequest) -> str:
        raise BackendError("connection refused")

    data = tiny_data([0.5, 0.9])
    configs = build_configs(["sys"], ["ex"], data.grid)
    with pytest.raises(BackendError, match="4 failed requests in a row"):
        run_search(FakeBackend(broken), data, configs, PROMPTS, tmp_path / "dead",
                   options=RunOptions(max_consecutive_errors=4))
    records = read_records(tmp_path / "dead")
    assert len(records) == 4 and all(r.score.hard_fails == ["no_reply"] and r.error for r in records)
    meta = json.loads((tmp_path / "dead" / "run.json").read_text(encoding="utf-8"))
    assert (meta["requests"], meta["errors"]) == (4, 4)
    with pytest.raises(FileExistsError):
        run_search(FakeBackend(broken), data, configs, PROMPTS, tmp_path / "dead")


def test_command_line_fake_run_and_rating(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "cli"
    assert run_cli.main(["--fake", "--limit", "3", "--prompts", "8", "--blind-prompts", "3", "--out", str(out)]) == 0
    for name in ("run.json", "configs.json", "results.jsonl", "summary.json", "leaderboard.md", "blind_rating.md",
                 "blind_key.json"):
        assert (out / name).is_file(), name
    assert len(read_records(out)) == 24
    assert run_cli.main(["--fake", "--limit", "3", "--prompts", "8", "--out", str(out)]) == 2   # folder in use
    assert run_cli.main(["--plan", "--limit", "2"]) == 0
    assert run_cli.main(["--fake", "--limit", "0"]) == 2

    key = BlindKey.model_validate_json((out / "blind_key.json").read_text(encoding="utf-8"))
    for prompt_id in key.labels:
        assert rate_cli.main(["set", str(out), prompt_id, "A=4", "b=3", "C=5"]) == 0
    assert rate_cli.main(["set", str(out), next(iter(key.labels)), "A=four"]) == 2
    assert rate_cli.main(["status", str(out)]) == 0
    assert rate_cli.main(["reveal", str(out)]) == 0
    assert rate_cli.main(["reveal", str(tmp_path / "missing")]) == 2
    winner = json.loads((out / "winner.json").read_text(encoding="utf-8"))
    means = winner["mean_ratings"]
    assert set(means) == set(key.configs) and math.isclose(means[winner["winner"]], max(means.values()))
    assert math.isclose(sum(means.values()) / len(means), 4.0)        # every prompt was rated 4, 3 and 5
    assert "winner:" in capsys.readouterr().out


def test_the_shipped_data_folder_is_the_default() -> None:
    assert DATA_DIR.name == "data" and (DATA_DIR / "prompts.jsonl").is_file()


# --- the HTTP backend ----------------------------------------------------------------------------------------------

def _request() -> ChatRequest:
    data = tiny_data([0.7])
    messages = prefix_messages(data.system_prompts["sys"], data.example_sets["ex"], address="sir", mode="user")
    return ChatRequest(config_id="c", prompt_id="p", category="joke", messages=messages,
                       sampling=Sampling(temperature=0.7, top_p=0.95, min_p=0.05, repeat_penalty=1.1),
                       max_tokens=64, seed=3)


def _reply(content: str = "Good evening, sir.") -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                                     "usage": {"prompt_tokens": 120, "completion_tokens": 6},
                                     "timings": {"predicted_per_second": 21.5}})


def test_openai_backend_sends_the_sampling_and_parses_the_reply() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _reply()

    backend = OpenAICompatibleBackend("http://127.0.0.1:8081/v1/", transport=httpx.MockTransport(handler),
                                      extra_body={"chat_template_kwargs": {"enable_thinking": False}})
    completion = backend.complete(_request())
    backend.close()
    assert completion.text == "Good evening, sir." and completion.finish_reason == "stop"
    assert (completion.prompt_tokens, completion.completion_tokens, completion.tokens_per_s) == (120, 6, 21.5)
    [sent] = seen
    assert sent.url.path == "/v1/chat/completions"
    body = json.loads(sent.content)
    assert (body["temperature"], body["top_p"], body["min_p"], body["repeat_penalty"]) == (0.7, 0.95, 0.05, 1.1)
    assert (body["max_tokens"], body["seed"], body["stream"]) == (64, 3, False)
    assert body["messages"][0]["role"] == "system" and body["chat_template_kwargs"] == {"enable_thinking": False}


def test_openai_backend_retries_a_bounded_number_of_times() -> None:
    calls: list[int] = []

    def flaky(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503, text="loading model") if len(calls) == 1 else _reply()

    backend = OpenAICompatibleBackend("http://127.0.0.1:8081", retries=2, backoff_s=0.0,
                                      transport=httpx.MockTransport(flaky))
    assert backend.complete(_request()).text == "Good evening, sir." and len(calls) == 2

    calls.clear()

    def down(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("refused", request=request)

    backend = OpenAICompatibleBackend("http://127.0.0.1:8081", retries=2, backoff_s=0.0,
                                      transport=httpx.MockTransport(down))
    with pytest.raises(BackendError, match="gave up after 3 attempts"):
        backend.complete(_request())
    assert len(calls) == 3


@pytest.mark.parametrize(("response", "message"), [
    (httpx.Response(400, text="bad request"), "HTTP 400"),
    (httpx.Response(200, text="not json"), "unexpected response"),
    (httpx.Response(200, json={"choices": []}), "unexpected response"),
])
def test_openai_backend_does_not_retry_client_errors_or_bad_bodies(response: httpx.Response, message: str) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return response

    backend = OpenAICompatibleBackend("http://127.0.0.1:8081", retries=3, backoff_s=0.0,
                                      transport=httpx.MockTransport(handler))
    with pytest.raises(BackendError, match=message):
        backend.complete(_request())
    assert len(calls) == 1


def test_openai_backend_health_check_and_argument_bounds() -> None:
    def healthy(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    assert OpenAICompatibleBackend("http://127.0.0.1:8081", transport=httpx.MockTransport(healthy)).health() == "/health"

    def loading(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503 if request.url.path == "/health" else 404)

    with pytest.raises(BackendError, match="not ready"):
        OpenAICompatibleBackend("http://127.0.0.1:8081", transport=httpx.MockTransport(loading)).health()
    with pytest.raises(ValueError, match="retries"):
        OpenAICompatibleBackend("http://127.0.0.1:8081", retries=99)
    with pytest.raises(ValueError, match="timeouts"):
        OpenAICompatibleBackend("http://127.0.0.1:8081", timeout_s=0)
