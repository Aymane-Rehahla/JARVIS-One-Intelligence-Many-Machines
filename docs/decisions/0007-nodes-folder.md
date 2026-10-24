# 0007: The folder `nodes/`

Status: accepted (owner's instruction, plan v3).

## Context
The four per-machine folders lived in `jarvis/`, and the project root was named after the old project. With one JARVIS
made of nodes (0006), a folder called `jarvis/` holding four "Jarvises" says the wrong thing.

## Decision
- The project root is `JARVIS/`.
- The mother folder is `nodes/`, holding `incubator-1`, `incubator-2`, `hvac` and `animal-protection`.
- Each node folder keeps the same anatomy (reasoning, memory, llm, voice, safety, comms).

## Alternatives
- **`jarvis/nodes/`:** one more level for no gain.
- **`machines/`:** confuses the AI node with the machine it supervises, which lives in `matrix/`.

## Consequences
- Paths in code, tests and docs use `nodes/<node>/`.
- The rename happened before the first commit, so the history starts clean.
