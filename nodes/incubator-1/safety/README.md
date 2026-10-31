# safety/: where the gate is (decision 0015)

The safety gate is the only way an action reaches a machine. It allows only the node's own machine, only in DANGER,
only actions from the approved list, and only with deterministic evidence checked in code; fire needs at least two
independent signs. It also limits owner notifications and emergency calls. The full rules are in
`memory/rom/safety_actions.md`.

**Where it runs:** the gate, the reasoning brains and their memory run inside the Matrix's farm runtime
(`matrix/farm_runtime.py`, from the installed release in `matrix/release_v31/`, decisions 0012, 0015 and 0021), not in
this node. This folder holds no code: no node or LLM code may import the gate or reach machine commands, and the
static checks of E8 (`tests/llm/eval/e8_safety.py`) and `tests/llm/safety/` check that.

- **In (in the farm runtime):** the reasoning's proposal (assessment, action, communication) and its evidence.
- **Out:** a machine command through the Matrix command lifecycle, an owner notification or an emergency call, or a
  logged refusal; the node sees only the published assessment and its gate decisions.
- **Tests:** `tests/test_farm_trained.py`, `tests/test_farm_runtime.py`, and the 6D real run in WORKLOG.md.
