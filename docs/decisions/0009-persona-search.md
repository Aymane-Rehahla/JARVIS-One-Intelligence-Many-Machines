# 0009: The persona search for the talking model

Status: accepted (owner's instruction, plan v3, point 4). The harness is built now; the full search runs after the
training ends.

## Context
JARVIS's character (formal, calm, dry, British, concise) depends on the system prompt, example exchanges and sampling
settings of a small 2B roleplay model. Picking them by feel is not evidence.

## Decision
- `tools/persona_search/` runs Qwen 3.5 2B with every combination of system-prompt versions, sets of 4–8 example
  exchanges, and sampling settings (temperature, top-p, min-p, repeat penalty).
- It runs 30–50 fixed prompts: small talk, farm status, a DANGER alert, an angry owner, a joke, attempts to break
  character, and questions that need real numbers.
- **Automatic scoring:**
  - in character, formal British tone, concise, no "As an AI";
  - the reply must pass `verify.py`. Good character with wrong numbers fails.
- The owner blind-rates the top three; **the owner's rating decides**.
- Example exchanges are original lines in JARVIS's style. **Nothing is quoted from the films.**

## Alternatives
- **Hand-tune one prompt:** cheaper, but no evidence and no protection against wrong numbers.
- **Fine-tune (C2):** later, only if prompting can't reach a consistent character.

## Consequences
- The full search needs the 2B server, which needs RAM the training is using. It is deferred: `tests/DEFERRED.md`, D2.
- A smoke test with a fake backend runs now.
