# 0015: The trained brains are installed inside JARVIS, re-saved as plain weights

Status: accepted (owner decision, 27 Oct 2026; the plan's step 6D left this choice to the owner).

## Context
The training ended on 27 Oct and its release v3.1 holds what the owner chose to ship: three reasoning engines
(`reasoning_engine_INCv3.1.pt`, `reasoning_engine_HVACv3.1.pt`, `reasoning_engine_APv3.1.pt`), each with its decision
layer (`decide.json`) and normal statistics (`stats.npz`), the exact code that trained them (`code_snapshot/`) and
`cmm.py`. The brains run with `hold_calls=True` and `hide_notes=True`. The engines' saved metadata and the code carry
the project's old name, which may not appear anywhere inside JARVIS (0010). The model code also imports part of the
upstream Continuous Thought Machines library (Apache 2.0), which the release does not contain.

## Decision
- **Inside JARVIS.** The code the brains need is copied into `matrix/release_v31/` with the old name replaced by a
  fixed, reversible word map: names only, never physics, encoders, labels or logic. The copy holds only the modules
  the brains really import (found by running the release recipe and listing what it loaded), plus the needed upstream
  CTM modules with their licence, at the relative path the model code expects.
- **Plain weights.** Each engine is re-saved as `{"config": ..., "state_dict": ...}` only, under its release file name.
  The install proves, tensor by tensor (`torch.equal`), that the weights and the config are those of the release, and
  records the release and installed SHA-256 of every file in `matrix/release_v31/PROVENANCE.json` (hashes, never the
  release's location).
- **Proven the same.** The same seed and the same injected fault give the same assessments, diagnoses and gate
  decisions, tick by tick, with the release run as it is and with the installed copy (`tools/release_equivalence.py`).
- **Where they run.** As in 0012, the four brains run inside the farm runtime, so they keep the timing they were
  trained with; the node programs are the language side. The farm runtime has two feeds: `simulated` (the 6A snapshot
  and rule brains, kept for fast tests) and `trained` (this install, "live: trained brains", the launcher's default).
  torch is pinned to the training's version, 2.14.1 (CPU).

## Alternatives
- Load the engines from the release folder outside JARVIS: less work, but JARVIS would depend on a folder outside the
  repository, and the hand-in copy would need it too.
- Copy the code into each node's `reasoning/` and `memory/` folders, as plan section 1.2 drew it: the node programs do
  not run the brains (0012), so four copies there would be dead code. Each node's `reasoning/README.md` and
  `memory/README.md` say where its brain and CMM run.

## Consequences
- JARVIS runs on its own, and the name test covers the copied code and the re-saved engines.
- Swapping a brain later means installing a new release with the same tool; a release is never overwritten.
- If the equivalence check ever fails, the trained feed is not used until the difference is explained (no tuning).
