# safety/: the gate (copied in step 6D)

`gate.py` is the only way an action reaches a machine. It allows only this node's own machine, only in DANGER, only
actions from the approved list, and only with deterministic evidence checked in code; fire needs at least two
independent signs. It also limits owner notifications and emergency calls. The full rules are in
`memory/rom/safety_actions.md`.

**Status: frozen until 6D** (copied from the training run's code snapshot). The LLM code may not import this folder or
reach machine commands; a test in `tests/llm/safety/` checks that (E8).

- **In:** the reasoning's proposal (assessment, action, communication) and the evidence it rests on.
- **Out:** a machine command through the Matrix command lifecycle, an owner notification or an emergency call, or a
  logged refusal.
- **Tests:** in 6D (`tests/DEFERRED.md`).
