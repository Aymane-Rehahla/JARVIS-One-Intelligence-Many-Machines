"""The run's human outputs: the leaderboard, the owner's blind rating sheet for the top 3, ratings and the reveal.

Only **eligible** configurations may reach the blind sheet: no verify failure on any prompt (every number and time
they said matched the tool results) and no hard fail on any danger prompt. The top 3 eligible ones by automatic score
go on the sheet; if fewer than 3 qualify the sheet says so and shows fewer, and with fewer than 2 there is no sheet.

Blind sheet: the same seeded subset of prompts for the chosen configurations; for each prompt their replies are
shuffled with a fixed seed and labelled A/B/C, so the order says nothing about the configuration. A reply that failed
any other automatic check is shown with that finding, never silently. The run folder keeps what would give the game
away apart from the sheet:
    blind/blind_rating.md    the owner's sheet (and blind/ratings.json, the owner's ratings)
    sealed/blind_key.json    which configuration is behind each label
    scored/                  results.jsonl, summary.json and leaderboard.md, which name the configurations
The owner rates everything with rate.py before opening scored/ or sealed/, then `rate.py reveal` names the winner
(highest mean rating; ties go to the higher automatic score). The tool only prepares the sheet: the owner decides.
"""
from __future__ import annotations

import json
import random
import statistics
from collections.abc import Mapping, Sequence
from pathlib import Path

from persona_search.models import BlindKey, ConfigSummary, PersonaPrompt, Ratings, ReplyRecord, RunMeta, Winner
from persona_search.scoring import CATEGORY_RULES, TONE_DEDUCTIONS, W_CONCISE, W_SUBSTANCE, W_TONE
from persona_search.selection import select_prompts

ROOT = Path(__file__).resolve().parents[2]
LABELS = "ABC"
SCORED_DIR = "scored"
BLIND_DIR = "blind"
SEALED_DIR = "sealed"
RESULTS = "results.jsonl"
SUMMARY = "summary.json"
LEADERBOARD = "leaderboard.md"
BLIND_SHEET = "blind_rating.md"
BLIND_KEY = "blind_key.json"
RATINGS = "ratings.json"
WINNER = "winner.json"
LEADERBOARD_ROWS = 50
HARD_FAIL_EXAMPLES = 20
EVIDENCE_PREVIEW_CHARS = 400


def _shown(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def _clip(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit - 1] + "…"


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def write_leaderboard(path: Path, meta: RunMeta, summaries: Sequence[ConfigSummary],
                      records: Sequence[ReplyRecord]) -> None:
    """Write leaderboard.md: the ranking, the winner's settings, the scoring rules and the first hard fails."""
    eligible = sum(1 for s in summaries if s.eligible)
    lines = [
        "# Persona search: leaderboard",
        "",
        f"Run `{meta.run_id}` · {len(meta.configs)} configurations × {len(meta.prompts)} prompts = {meta.requests} "
        f"requests ({meta.errors} errors) · backend `{meta.backend}` · seed {meta.seed} · address \"{meta.address}\" · "
        f"evidence as {meta.evidence_mode} messages · {meta.started_utc} → {meta.finished_utc}",
        "",
        f"Reply score: 0 on any hard fail, otherwise 100 × ({W_TONE} tone + {W_CONCISE} concise + "
        f"{W_SUBSTANCE} substance). Configuration score: mean over its prompts. Ranked by score, then fewer hard "
        "fails, then lower latency. **Eligible** for the owner's blind rating: no verify fail and no danger hard fail "
        f"({eligible} of {len(summaries)} configurations). The owner's blind rating of the top 3 eligible decides.",
        "",
        "| Rank | Configuration | Eligible | Score | Hard fails | Verify fails | Character fails | Action claims "
        "| No farm time | Danger fails | Mean words | Mean latency (s) | Tokens/s |",
        "|---:|---|:-:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for rank, s in enumerate(summaries[:LEADERBOARD_ROWS], start=1):
        latency = f"{s.mean_latency_s:.2f}" if s.mean_latency_s is not None else "–"
        speed = f"{s.tokens_per_s:.1f}" if s.tokens_per_s is not None else "–"
        lines.append(f"| {rank} | `{s.config.id}` | {'yes' if s.eligible else 'no'} | {s.score:.1f} | "
                     f"{s.hard_fails}/{s.replies} | {s.verify_fails} | {s.character_fails} | {s.action_fails} | "
                     f"{s.farm_time_fails} | {s.danger_hard_fails} | {s.mean_words:.0f} | {latency} | {speed} |")
    if len(summaries) > LEADERBOARD_ROWS:
        lines.append(f"\n{len(summaries) - LEADERBOARD_ROWS} more configurations in {SUMMARY}.")
    best = next((s for s in summaries if s.eligible), None)
    if best is not None:
        lines += ["", "## Best eligible configuration", "",
                  f"- id: `{best.config.id}`",
                  f"- system prompt: `{best.config.system_prompt}`; examples: `{best.config.examples}`",
                  f"- sampling: temperature {best.config.sampling.temperature}, top_p {best.config.sampling.top_p}, "
                  f"min_p {best.config.sampling.min_p}, repeat_penalty {best.config.sampling.repeat_penalty}",
                  "- score by category: " + ", ".join(f"{k} {v:.1f}" for k, v in best.category_scores.items())]
    else:
        lines += ["", "## No eligible configuration", "",
                  "Every configuration failed verify on some prompt or failed hard on a danger prompt."]
    lines += ["", "## Scoring rules", "",
              "- Hard fails: no reply, leaked reasoning, AI disclaimer or claiming a model/vendor name, breaking the "
              "fourth wall, film names, stock film lines or blocklisted film lines, any number or time that "
              "llm/verify.py cannot match to the prompt's tool results, claiming an action the tool results do not "
              "record, and not stating the farm time where it is required.",
              "- Tone deductions: " + ", ".join(f"{k} −{v}" for k, v in TONE_DEDUCTIONS.items()) + ".",
              "- Word limits: " + ", ".join(f"{k} {v.max_words}" for k, v in CATEGORY_RULES.items()) + ".",
              "- Substance: a verified number when the prompt needs numbers, the farm time when the tool results have "
              "one, and each must-mention term."]
    failed = [r for r in records if r.score.hard_fails][:HARD_FAIL_EXAMPLES]
    if failed:
        lines += ["", f"## Hard fails (first {HARD_FAIL_EXAMPLES})", "",
                  "| Configuration | Prompt | Reasons | Reply |", "|---|---|---|---|"]
        for r in failed:
            shown = r.error if r.error else r.text
            lines.append(f"| `{r.config_id}` | {r.prompt_id} | {', '.join(r.score.hard_fails)} | "
                         f"{_cell(_clip(shown, 100))} |")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _evidence_preview(prompt: PersonaPrompt) -> str:
    if not prompt.evidence:
        return ""
    parts = [f"{item.name} {json.dumps(item.data, ensure_ascii=False)}" for item in prompt.evidence]
    return _clip(" · ".join(parts), EVIDENCE_PREVIEW_CHARS)


def _quote(text: str) -> list[str]:
    return [f"> {line}" if line.strip() else ">" for line in text.strip().splitlines()] or ["> (empty)"]


def blind_labels(prompt_ids: Sequence[str], configs: Sequence[str], seed: int) -> dict[str, dict[str, str]]:
    """For each prompt, a seeded shuffle of the configurations under the labels A, B, C."""
    labels: dict[str, dict[str, str]] = {}
    for prompt_id in prompt_ids:
        order = list(configs)
        random.Random(f"{seed}:blind:{prompt_id}").shuffle(order)
        labels[prompt_id] = {LABELS[index]: config for index, config in enumerate(order)}
    return labels


def write_blind_sheet(out_dir: Path, meta: RunMeta, summaries: Sequence[ConfigSummary],
                      records: Sequence[ReplyRecord], prompts: Sequence[PersonaPrompt], *, n_prompts: int = 12,
                      top_k: int = 3) -> BlindKey | None:
    """Write blind/blind_rating.md and sealed/blind_key.json for the top eligible configurations.

    Returns None (and a sheet that says why) when fewer than two configurations are eligible.
    """
    eligible = [s for s in summaries if s.eligible]
    top = [s.config.id for s in eligible[:min(top_k, len(LABELS))]]
    sheet = out_dir / BLIND_DIR / BLIND_SHEET
    sheet.parent.mkdir(parents=True, exist_ok=True)
    if len(top) < 2:
        sheet.write_text("# Persona search: blind rating\n\n"
                         f"Run `{meta.run_id}`: no blind rating is possible. {len(eligible)} of {len(summaries)} "
                         "configurations passed the automatic checks needed for it (no verify fail on any prompt, no "
                         f"hard fail on a danger prompt); a blind comparison needs at least two. See "
                         f"`{SCORED_DIR}/{LEADERBOARD}`.\n", encoding="utf-8", newline="\n")
        return None
    replies = {(r.config_id, r.prompt_id): r for r in records}
    chosen = [p for p in select_prompts(prompts, n_prompts, meta.seed, salt="blind")
              if all((c, p.id) in replies for c in top)]
    labels = blind_labels([p.id for p in chosen], top, meta.seed)
    key = BlindKey(run_id=meta.run_id, seed=meta.seed, configs=top,
                   auto_scores={s.config.id: s.score for s in eligible[:len(top)]}, labels=labels)
    run_dir = _shown(out_dir)
    letters = " ".join(f"{label}=5" for label in LABELS[:len(top)])
    fewer = (f" Only {len(top)} configurations passed the automatic checks needed for this sheet, so each prompt has "
             f"{len(top)} replies." if len(top) < top_k else "")
    lines = [
        "# Persona search: blind rating",
        "",
        f"Run `{meta.run_id}` · {len(chosen)} prompts · {len(top)} replies per prompt, shuffled for each prompt "
        "(fixed seed), so the letters say nothing about which configuration wrote them." + fewer,
        "",
        "Every configuration on this sheet passed two automatic checks on every prompt of the run: each number and "
        "time it said matched the prompt's tool results (llm/verify.py), and none of its danger replies failed a "
        "hard check. Any other automatic finding is printed under the reply it concerns.",
        "",
        "Score each reply from 1 (poor) to 5 (excellent): does it sound like JARVIS (formal, calm, dry, British), "
        "is it short enough, and would you be happy to hear it? Record one prompt at a time:",
        "",
        f"    .venv\\Scripts\\python tools\\persona_search\\rate.py set {run_dir} {chosen[0].id if chosen else 'ID'} "
        f"{letters}",
        "",
        "When every prompt is rated, reveal the winner:",
        "",
        f"    .venv\\Scripts\\python tools\\persona_search\\rate.py reveal {run_dir}",
        "",
        f"Do not open `{SCORED_DIR}/` or `{SEALED_DIR}/` before you finish: they say which configuration is which.",
    ]
    for number, prompt in enumerate(chosen, start=1):
        lines += ["", "---", "", f"## {number}. `{prompt.id}` · {prompt.category.replace('_', ' ')}", "",
                  f"**Owner:** {prompt.text}"]
        preview = _evidence_preview(prompt)
        if preview:
            lines += ["", f"*Tool results:* `{preview}`"]
        for label, config in labels[prompt.id].items():
            record = replies[(config, prompt.id)]
            shown = record.text if record.error is None else f"(no reply: {record.error})"
            lines += ["", f"**{label}.**", *_quote(shown)]
            if record.score.hard_fails:
                lines += ["", f"*Automatic check failed: {', '.join(record.score.hard_fails)}.*"]
    sheet.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    sealed = out_dir / SEALED_DIR / BLIND_KEY
    sealed.parent.mkdir(parents=True, exist_ok=True)
    sealed.write_text(key.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    return key


def load_key(run_dir: Path) -> BlindKey:
    """Read sealed/blind_key.json. Raises FileNotFoundError or pydantic.ValidationError."""
    return BlindKey.model_validate_json((run_dir / SEALED_DIR / BLIND_KEY).read_text(encoding="utf-8"))


def load_ratings(run_dir: Path) -> Ratings:
    """Read blind/ratings.json, or an empty Ratings if none were recorded yet."""
    path = run_dir / BLIND_DIR / RATINGS
    if not path.exists():
        return Ratings()
    return Ratings.model_validate_json(path.read_text(encoding="utf-8"))


def record_ratings(run_dir: Path, prompt_id: str, scores: Mapping[str, int]) -> Ratings:
    """Record the owner's 1–5 scores for every label of one prompt (re-rating replaces). Raises ValueError."""
    key = load_key(run_dir)
    if prompt_id not in key.labels:
        raise ValueError(f"prompt {prompt_id!r} is not on the blind sheet; it has: {', '.join(key.labels)}")
    expected = set(key.labels[prompt_id])
    if set(scores) != expected:
        raise ValueError(f"give one score for each of {', '.join(sorted(expected))}; got {', '.join(sorted(scores))}")
    ratings = load_ratings(run_dir)
    if any(not ratings.scale_min <= value <= ratings.scale_max for value in scores.values()):
        raise ValueError(f"scores must be whole numbers from {ratings.scale_min} to {ratings.scale_max}")
    ratings.ratings[prompt_id] = dict(sorted(scores.items()))
    path = run_dir / BLIND_DIR / RATINGS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ratings.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    return ratings


def reveal(run_dir: Path, *, allow_partial: bool = False) -> Winner:
    """Map the owner's ratings back to configurations, name the winner and write winner.json. Raises ValueError."""
    key = load_key(run_dir)
    ratings = load_ratings(run_dir)
    rated = [p for p in key.labels if p in ratings.ratings]
    unrated = [p for p in key.labels if p not in ratings.ratings]
    if not rated:
        raise ValueError("no ratings recorded yet; use: rate.py set RUN_DIR PROMPT_ID A=4 B=2 C=5")
    if unrated and not allow_partial:
        raise ValueError(f"{len(unrated)} prompts are not rated yet ({', '.join(unrated)}); rate them or use --partial")
    per_config: dict[str, list[int]] = {config: [] for config in key.configs}
    for prompt_id in rated:
        for label, config in key.labels[prompt_id].items():
            score = ratings.ratings[prompt_id].get(label)
            if score is not None and config in per_config:
                per_config[config].append(score)
    means = {config: round(statistics.fmean(values), 3) for config, values in per_config.items() if values}
    winner = max(means, key=lambda config: (means[config], key.auto_scores.get(config, 0.0)))
    result = Winner(run_id=key.run_id, winner=winner, mean_ratings=means, auto_scores=key.auto_scores,
                    rated_prompts=len(rated), unrated_prompts=unrated)
    (run_dir / WINNER).write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    return result
