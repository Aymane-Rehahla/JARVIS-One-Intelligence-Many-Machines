# 0018: The agent: routing in code, sentence-gated streaming, fallbacks from data

Status: accepted (builder, 30-31 Oct 2026, step 6C; P2 section 3).

## Context
incubator-1's 2B model speaks for the whole farm, but ground rule 5 says every number it says must match a tool
result of the same turn, and the 6C KV-cache probe (decision 0017) showed that the 2B model, given no data, invents
readings and even writes fake tool-result blocks. The speaking path must also stay inside its deadlines when a model,
a peer or the radio fails.

## Decision
`llm/agent.py`, one turn:
1. **Refusals in code, before any model call.** Requests to act on a machine, call emergency services, read the CMM
   or change JARVIS's rules match fixed patterns and get a fixed `refused` reply. The model is never asked.
2. **Routing in code** (`llm/routing.py`): machine names, aliases and topics decide which machines the question is
   about; "both incubators", "one incubator" and farm-wide phrases fan out. incubator-1's own assessment and readings
   are always gathered; each routed peer is asked through the `ask_peer` tool (caller "llm", so the radio rules and
   the audit apply), all in parallel within `GATHER_S`.
3. **The 2B model writes; code verifies each sentence** before it is sent: `verify_reply()` against all of the turn's
   evidence (never the conversation: `recall_conversation` holds the owner's words, not data), plus rules against
   brackets/tags, first-person action claims, tool names, and a machine action said as a fact unless the machines'
   own events, incidents or decisions name it (never a manual, never a reading). A failed sentence stops the stream;
   one regeneration names the unmatched numbers; if that fails too, a fixed sentence built from the data ends the
   reply. Up to `MAX_TOOL_ROUNDS = 2` further read-only tool calls; an invalid call is answered with the error so the
   model can call again once in the next round, and is counted (E4).
4. **Farm time and label** come from the newest fresh data of the turn; a silent peer's remembered answer counts only
   when nothing fresh came back (its own time is quoted with it). If the model left the farm time out, one fixed
   sentence adds it.
5. **Fallbacks are built from data only** (`llm/recovery.py`): model down, deadline or failed verification gives a
   fixed template over the gathered readings and conclusions; a silent peer is described from its last good answer of
   this farm run (`PeerMemory`, bounded, keyed by `feed_id`).
6. **Personality** (`llm/personality.py`) is a deterministic mode from the highest level known in this farm run (the
   turn's conclusions and the alerts heard): calm, plain or dry.
7. **Acknowledging** never acts: the agent finds that machine's current incident in this turn's data and returns an
   `AckOffer` (a token bound to node, incident and farm run, single-use, 60 s); only the owner's click sends it back.
   incubator-1 and the Matrix both refuse a click from another farm run (incident ids start again in every run). By
   voice there is no offer: a spoken reply has no button, so it points to the chat page.
8. **Citations:** a retrieved passage is cited only when the reply shares 3 distinctive words with it (E2's bar).
9. **Bounded turns:** every owner turn, chat or voice, goes through the node's two-at-once limit; a reader that stops
   (a second push-to-talk, a stopping node) cancels the turn's own work on the model.

Deadlines nest (tested): radio 3.0 < ask_peer 3.5 < gather 4.0 < one LLM call 10 (first token 6, measured) < turn 25 <
gateway chat 30 s.

## Alternatives
- Letting the 2B model choose the peers through tool calls only: rejected for routing, kept for extra tools. Routing
  is what E5 measures, and code is deterministic.
- Verifying the whole reply at the end: rejected, the first chunk would wait for the whole answer, and one bad
  number would cost the whole reply.
- Reusing a previous owner answer while incubator-1's own data is unchanged (`context_first.AnswerCache`): not used
  for owner turns, because a turn's data includes peers' answers that the cache key does not cover. Never reusing
  never breaks "reuse only while the data is unchanged".

## Consequences
- A reply can be shorter than the model wanted: anything it cannot back with this turn's data is cut.
- Routing quality depends on the alias lists in `routing.py` (E5 measures it).
