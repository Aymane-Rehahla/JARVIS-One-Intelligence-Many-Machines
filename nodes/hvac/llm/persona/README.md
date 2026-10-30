# llm/persona/: the speaking model's persona (provisional)

Read by `llm/jarvis_speaker.py` (`load_persona()`): `system_prompt.md` (the fixed start of every prompt), `sampling.json`
(the sampling settings), optional `examples.json` (`[[owner, jarvis], ...]`), and `SOURCE` (where it came from).

**Provisional.** Until the owner rates the persona search's top three (`tools/persona_search/rate.py`), this is the
starting system prompt of `docs/persona-spec.md` (section 2) with the conversation start settings (section 4) and no
example exchanges: the spec's examples carry placeholder numbers, and a 2B model copies numbers from examples. When the
search ends, its top eligible automatic configuration replaces this one (still provisional); the owner's choice
(`winner.json`) replaces that. `SOURCE` always says which is installed.

The persona never decides anything: the agent's code routes, gathers, verifies every number and adds the farm time.
