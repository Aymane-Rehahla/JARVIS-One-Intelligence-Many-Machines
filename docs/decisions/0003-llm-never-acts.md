# 0003: The LLM talks; it never acts

Status: accepted (plan v2, ground rules 2, 3 and 5).

## Context
Language models can be manipulated (prompt injection in a document or a chat) and can invent facts. The farm's safety
must not depend on them, and the project is judged at PhD level.

## Decision
- Machine actions and emergency calls come only from the reasoning, through `safety/gate.py`.
- The only write an LLM can trigger is the owner's acknowledgement of an alert, and only with the owner's words plus a
  confirm click on the website (a gateway token).
- LLM code never opens the CMM and can't import `memory/` or reach machine commands. Permissions are enforced in code,
  not in prompts.

## Alternatives
- **A "propose an action" tool, run after the owner approves:** this was in the 21 Oct tool list. Plan v2 replaced it
  with acknowledgement-only, which is smaller and easier to prove safe.
- **LLM with direct actions:** rejected; unsafe.

## Consequences
- If every LLM program crashes, the farm, the reasoning and the gate keep working (ground rule 5; tested as E7).
- The safety suite (E8) must show 0 unsafe tool calls, 0 actions and 0 CMM access.
