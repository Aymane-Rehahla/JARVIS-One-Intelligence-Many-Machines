# 0019: Proactive alerts go through the radio and are spoken from a template

Status: accepted (builder, 30-31 Oct 2026, step 6C; plan 6D moved to P2 section 4).

## Context
When a machine's reasoning reaches ESCALATE_TO_USER or DANGER, the owner must hear it without asking. Only incubator-1
speaks. The alert must not depend on the language model (ground rule 4: the safety path never depends on the LLM),
and messages between nodes must keep the farm's real radio limits.

## Decision
- Each node watches its **own** farm feed (`llm/alerts.py`, `AlertWatcher`). At an alert level it sends one
  `AlertNotice` to incubator-1 with `PeerNetwork.notify()`: one way, on `jarvis.incubator-1.notice`, under the same
  ZigBee delay, loss and cut-link rules as every other message between nodes. One notice per farm run, node,
  incident and level; a rising level sends again.
- incubator-1 (`AlertDesk`) turns a notice, or its own machine's assessment directly, into an `OwnerAlert` from a
  fixed template filled only with that node's published output (level, diagnosis, evidence, gate decision, farm
  time), within `ALERT_TEMPLATE_S = 0.5 s`, and publishes it on `jarvis.incubator-1.alert`; the gateway sends it to
  every chat socket. The latency from hearing the notice to the alert is recorded.
- The model may reword the alert only when asked (`reword=True`), only within `ALERT_REWORD_S = 2.0 s` in total, and
  only if `verify_reply()` passes; otherwise the template stands (`reworded = false`).

## Alternatives
- incubator-1 subscribing to every node's farm feed directly: rejected, it would bypass the radio (the alert would
  arrive even when the link is cut, which the farm's own reasoning would not see).
- The model writing the alert: rejected (ground rule 4, and its invented numbers, decision 0017).

## Consequences
- A cut radio link delays or loses a peer's alert exactly as it would lose any other message; the farm's safety gate
  still acts on its own.
- Alerts read the same every time; rewording is cosmetic and optional.
