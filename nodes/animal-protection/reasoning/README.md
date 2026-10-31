# reasoning/: where this node's reasoning runs, and why it is not in this folder

The part of this node that notices faults, diagnoses them and names their cause is a trained Sakana Continuous Thought
Machine (CTM): the brains of release v3.1, one per machine kind (two incubators each run their own copy of the
incubator brain).

**It runs in the farm runtime, not in this folder** (decisions 0012 and 0015). The brain is part of the farm test bench
the Matrix ticks: `matrix/farm_runtime.py --feed trained` builds each node's brain exactly as the release's exam built
it (its own code, installed by `tools/install_release.py` in `matrix/release_v31/`, the weights proven equal), steps it
every farm second with the machine's own readings, and publishes its output on `farm.<node>.assessment` with
`brain = "ctm"` and the label "live: trained brains". `--feed simulated` runs the older snapshot's rule brains instead
("live: simulated brains"). No code is copied here: one installed copy, proven to decide like the release
(`tools/release_equivalence.py`), is the only one.

- **In (to the brain):** this machine's public monitoring data, its peers' messages on the farm's own bus, the farm
  clock. Its memory notes are hidden (`hide_notes=True`, as in training).
- **Out:** assessment, diagnosis, confidence, evidence, a protective-action proposal and a communication, checked by
  the safety gate before anything acts. This node program reads them from NATS like any other public data.
- **The LLM never runs or changes the reasoning.** It reads only what the reasoning publishes.
- **Tests:** `tests/test_farm_trained.py` (the trained feed), `tests/test_install_release.py` (the install), the
  equivalence evidence in WORKLOG.md (step 6D).
