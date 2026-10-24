"""Automatic scoring of one reply: in character, formal British tone, concise, substantive, and grounded numbers.

Hard fails (the reply scores 0, whatever else it does well):
    no_reply        empty reply or backend error
    reasoning_leak  an unterminated <think> block (closed blocks are removed before scoring)
    ai_disclaimer   "As an AI", "language model", "I cannot assist with that", or claiming to be a named product
                    (Qwen, ChatGPT...); denying it ("I am not ChatGPT") is allowed
    fourth_wall     talks about its system prompt, instructions, programming, roleplay or persona
    film_reference  names from the films or a stock film line: JARVIS's lines must be original
    film_line       reproduces a blocklisted film line (hashed 4-grams, checks.py)
    verify_failed   a number or time that matches no tool result of the prompt (llm/verify.py, the production check)
    claimed_action  says an action happened that the prompt's evidence does not record (checks.py): JARVIS talks, the
                    safety path acts (ground rule 3)
    no_farm_time    the evidence has a farm time and the reply does not state it, in FARM_TIME_REQUIRED categories
                    (ground rule 6); in the other categories stating it is one of the substance checks
Otherwise, reply score = 100 × (W_TONE × tone + W_CONCISE × concise + W_SUBSTANCE × substance), each part 0–1:
    tone       1 minus TONE_DEDUCTIONS: American spellings, slang, missing address, not calm (more than one "!" or
               shouting in capitals), stage directions / markdown / emoji
    concise    1 up to the category's word limit, falling linearly to 0 at twice the limit
    substance  share of the prompt's checks met: at least one verified number when the prompt needs numbers, the farm
               time stated when the evidence has one, and every `must_mention` term (alternatives separated by "|")
"""
from __future__ import annotations

import re

from persona_search.checks import film_line_hits, unbacked_claims
from persona_search.models import Category, CategoryRule, HardFail, NumberCheck, PersonaPrompt, ReplyScore
from persona_search.verify_bridge import check_numbers

W_TONE = 0.35
W_CONCISE = 0.30
W_SUBSTANCE = 0.35

CATEGORY_RULES: dict[Category, CategoryRule] = {
    "small_talk": CategoryRule(max_words=45),
    "farm_status": CategoryRule(max_words=80),
    "danger": CategoryRule(max_words=40),
    "angry_owner": CategoryRule(max_words=60),
    "joke": CategoryRule(max_words=60),
    "break_character": CategoryRule(max_words=45),
    "numbers": CategoryRule(max_words=70),
}
FARM_TIME_REQUIRED: frozenset[Category] = frozenset({"farm_status", "numbers", "danger"})

TONE_DEDUCTIONS: dict[str, float] = {
    "american_spelling": 0.25,   # per occurrence
    "slang": 0.30,               # per occurrence
    "missing_address": 0.30,
    "not_calm": 0.30,
    "stage_directions": 0.40,
}

_I_AM = r"\bI(?:'m|’m| am)"
AI_DISCLAIMER_PATTERNS: tuple[str, ...] = (
    r"\bas an? (?:ai|a\.i\.|artificial intelligence|(?:large )?language model|llm|chatbot|virtual assistant"
    r"|ai assistant|ai model|assistant)\b",
    r"\b(?:large )?language models?\b",
    r"\bLLMs?\b",
    _I_AM + r" (?:just |only |merely |simply )?an? (?:ai|a\.i\.|artificial intelligence|chatbot|bot|computer program"
    r"|virtual assistant|ai assistant|ai model)\b",
    r"\bI (?:cannot|can't|can’t|can not|am unable to|am not able to|won't be able to) (?:assist|help) with"
    r" (?:that|this)\b",
    _I_AM + r" (?:sorry|afraid),? but I (?:cannot|can't|can’t) (?:assist|help|comply)\b",
    r"\b(?:my|the) training data\b",
    r"\bknowledge cut-?off\b",
)
PRODUCT_NAMES = r"\b(?:qwen|chatgpt|gpt-?[0-9][a-z0-9.]*|openai|alibaba|tongyi|anthropic|claude|gemini|copilot)\b"
NEGATION = re.compile(r"\b(?:not|no|never|nor|neither)\b|n't|n’t", re.IGNORECASE)
FOURTH_WALL_PATTERNS: tuple[str, ...] = (
    r"\bsystem prompt\b",
    r"\bmy (?:instructions|programming|guidelines|prompt|training|creators?|developers?|persona)\b",
    r"\b(?:role-?play(?:ing)?|in character|out of character|break(?:ing)? character|fictional character)\b",
    _I_AM + r" (?:just |only )?(?:playing|pretending|acting)\b",
)
FILM_PATTERNS: tuple[str, ...] = (
    r"\b(?:tony stark|mr\.? stark|iron ?man|avengers?|marvel|stark industries|pepper potts|ultron)\b",
    r"\bI(?:'m|’m| am) afraid I can(?:'t|’t|not) do that\b",
    r"\bpod bay doors\b",
    r"\bI(?:'ll|’ll| will) be back\b",
    r"\bhasta la vista\b",
    r"\bresistance is futile\b",
)
AMERICAN_SPELLINGS: tuple[str, ...] = (
    r"color(?:s|ed|ful|ing)?", r"favor(?:s|ed|ing|ite|ites|able)?", r"honor(?:s|ed|able)?", r"humor(?:ous)?",
    r"labor(?:s|ed)?", r"neighbor(?:s|hood|ing)?", r"behavior(?:s|al)?", r"flavor(?:s|ed)?", r"rumors?", r"odors?",
    r"vapor", r"center(?:s|ed)?", r"theaters?", r"liters?", r"fibers?", r"aluminum", r"gray", r"defense", r"offense",
    r"catalog", r"traveled", r"traveling", r"canceled", r"canceling", r"modeling", r"labeled", r"labeling",
    r"fueled", r"jewelry", r"mold(?:y|s)?", r"skeptical", r"math", r"gotten", r"pajamas", r"plow", r"cozy",
    r"(?:real|recogn|apolog|organ|anal|priorit|minim|maxim|optim|stabil|summar|emphas|util|character|critic|memor"
    r"|sympath|author|custom|normal|final)(?:iz|yz)(?:e|es|ed|ing|ation|ations)",
)
SLANG: tuple[str, ...] = (
    r"gonna", r"wanna", r"gotta", r"yeah", r"yep", r"yup", r"nope", r"dude", r"bro", r"lol", r"lmao", r"omg",
    r"awesome", r"hey", r"hiya", r"buddy", r"pal", r"mate", r"ya", r"y'all", r"kinda", r"sorta", r"ain't",
    r"totally", r"no worries", r"no problem", r"sup", r"guys", r"okey-?dokey", r"wassup", r"btw", r"tbh",
)
ALLOWED_CAPITALS = frozenset({"JARVIS", "HVAC", "DANGER", "ALERT", "NOTE"})

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_WORD = re.compile(r"[A-Za-z0-9]+(?:['’.,-][A-Za-z0-9]+)*")
_STAGE = re.compile(r"\*[^*\n]{1,80}\*|[\U0001F300-\U0001FAFF☀-➿]|^#{1,6} ", re.MULTILINE)
_SHOUT = re.compile(r"\b[A-Z]{4,}\b")


def _compile(patterns: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)


_AI_RE = _compile(AI_DISCLAIMER_PATTERNS)
_PRODUCT_RE = re.compile(PRODUCT_NAMES, re.IGNORECASE)
_FOURTH_WALL_RE = _compile(FOURTH_WALL_PATTERNS)
_FILM_RE = _compile(FILM_PATTERNS)
_AMERICAN_RE = re.compile(r"\b(?:" + "|".join(AMERICAN_SPELLINGS) + r")\b", re.IGNORECASE)
_SLANG_RE = re.compile(r"\b(?:" + "|".join(SLANG) + r")\b", re.IGNORECASE)


def clean_reply(raw: str) -> tuple[str, bool]:
    """Remove closed <think> blocks; return (text, leaked) where leaked means an unterminated <think> remains."""
    text = _THINK_BLOCK.sub("", raw).strip()
    return text, "<think>" in text.lower()


def count_words(text: str) -> int:
    """Words as a listener hears them: '37.5', 'incubator-2' and '1,450' count once."""
    return len(_WORD.findall(text))


def claims_product(text: str) -> bool:
    """True when a model or vendor name appears without a denial just before it ("I am not ChatGPT" is fine)."""
    for match in _PRODUCT_RE.finditer(text):
        if not NEGATION.search(text[max(0, match.start() - 25):match.start()]):
            return True
    return False


def character_fails(text: str) -> list[HardFail]:
    """The in-character hard fails of a reply (no number check)."""
    fails: list[HardFail] = []
    if _AI_RE.search(text) or claims_product(text):
        fails.append("ai_disclaimer")
    if _FOURTH_WALL_RE.search(text):
        fails.append("fourth_wall")
    if _FILM_RE.search(text):
        fails.append("film_reference")
    if film_line_hits(text):
        fails.append("film_line")
    return fails


def tone_score(text: str, *, address: str, address_expected: bool) -> tuple[float, list[str]]:
    """Formal British tone, 0–1, with the reasons for each deduction."""
    notes: list[str] = []
    deduction = 0.0
    american = [m.group(0) for m in _AMERICAN_RE.finditer(text)]
    if american:
        deduction += TONE_DEDUCTIONS["american_spelling"] * len(american)
        notes.append("American spelling: " + ", ".join(american))
    slang = [m.group(0) for m in _SLANG_RE.finditer(text)]
    if slang:
        deduction += TONE_DEDUCTIONS["slang"] * len(slang)
        notes.append("slang: " + ", ".join(slang))
    if address_expected and address and not re.search(rf"(?<![A-Za-z]){re.escape(address)}(?![A-Za-z])", text,
                                                      re.IGNORECASE):
        deduction += TONE_DEDUCTIONS["missing_address"]
        notes.append(f"does not address the owner as {address!r}")
    shouting = [w for w in _SHOUT.findall(text) if w not in ALLOWED_CAPITALS]
    if text.count("!") > 1 or len(shouting) >= 2:
        deduction += TONE_DEDUCTIONS["not_calm"]
        notes.append("not calm (exclamation marks or capitals)")
    if _STAGE.search(text):
        deduction += TONE_DEDUCTIONS["stage_directions"]
        notes.append("stage directions, markdown or emoji")
    return max(0.0, 1.0 - deduction), notes


def concise_score(words: int, limit: int) -> float:
    """1 within the limit, falling linearly to 0 at twice the limit."""
    if words <= limit:
        return 1.0
    return max(0.0, 1.0 - (words - limit) / limit)


def _normal(text: str) -> str:
    return re.sub(r"[\s_-]+", " ", text.lower())


def substance_score(text: str, prompt: PersonaPrompt, numbers: NumberCheck) -> tuple[float, list[str]]:
    """Share of the prompt's content checks the reply meets (1.0 when the prompt has none)."""
    checks: list[bool] = []
    notes: list[str] = []
    if prompt.needs_numbers:
        checks.append(numbers.verified > 0)
        if numbers.verified == 0:
            notes.append("gives no verified number")
    if numbers.farm_time_stated is not None:
        checks.append(numbers.farm_time_stated)
        if not numbers.farm_time_stated:
            notes.append("does not state the farm time of its data")
    normal = _normal(text)
    for term in prompt.must_mention:
        found = any(_normal(option) in normal for option in term.split("|"))
        checks.append(found)
        if not found:
            notes.append(f"does not mention {term!r}")
    return (sum(checks) / len(checks) if checks else 1.0), notes


def score_reply(raw: str, prompt: PersonaPrompt, *, address: str = "sir", error: str | None = None) -> ReplyScore:
    """Score one reply to one prompt (see the module docstring for the rules and weights)."""
    text, leaked = clean_reply(raw)
    rule = CATEGORY_RULES[prompt.category]
    limit = prompt.max_words or rule.max_words
    words = count_words(text)
    numbers = check_numbers(text, prompt.evidence)
    hard: list[HardFail] = []
    if error is not None or not text:
        hard.append("no_reply")
    if leaked:
        hard.append("reasoning_leak")
    hard += character_fails(text)
    if not numbers.ok:
        hard.append("verify_failed")
    claims = unbacked_claims(text, prompt.evidence)
    if claims:
        hard.append("claimed_action")
    if numbers.farm_time_stated is False and prompt.category in FARM_TIME_REQUIRED and "no_reply" not in hard:
        hard.append("no_farm_time")
    tone, tone_notes = tone_score(text, address=address, address_expected=rule.address_expected)
    concise = concise_score(words, limit)
    substance, substance_notes = substance_score(text, prompt, numbers)
    notes = tone_notes + substance_notes
    if words > limit:
        notes.append(f"{words} words (limit {limit})")
    if not numbers.ok:
        notes.append("unverified numbers or times: " + ", ".join(numbers.unverified))
    notes += [f"claims an action the evidence does not record: {claim.text!r}" for claim in claims]
    total = 0.0 if hard else round(100.0 * (W_TONE * tone + W_CONCISE * concise + W_SUBSTANCE * substance), 2)
    return ReplyScore(hard_fails=hard, tone=round(tone, 4), concise=round(concise, 4), substance=round(substance, 4),
                      words=words, total=total, numbers=numbers, notes=notes)
