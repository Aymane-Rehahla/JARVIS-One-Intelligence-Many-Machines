# reasoning/: the node's reasoning (frozen until step 6D)

The part of this node that notices faults, diagnoses them and names their cause: a trained Sakana Continuous Thought
Machine (CTM), with the hand-written rule brain as its baseline and fallback.

**Status: frozen. This README is the only file here until step 6D.** The Phase 5 training is still running, and trained
weights only load with the exact code that trained them. In 6D the code is copied from the training run's code snapshot
(with the old project name removed, as everywhere in JARVIS), not from the read-only test feed in `_dev/`.

| File (arrives in 6D) | What it does |
|---|---|
| `encoders.py` | turns own readings, peer messages and memory context into the brain's input |
| `ctm/` | our CTM: perception layer, thinking core, heads; its README says what we changed and what won |
| `rule_brain.py` | the hand-written rules the CTM must beat; also the fallback |

- **In:** this machine's public monitoring data, peer messages (on the reasoning's own bus, not NATS), memory context.
- **Out:** assessment, diagnosis, confidence, source machine, evidence, a protective-action proposal (DANGER only) and a
  communication. The farm runtime (`matrix/farm_runtime.py`) runs the brain and publishes its output on
  `farm.<node>.assessment`.
- **The LLM never runs or changes this code.** It reads only what the reasoning publishes.
- **Tests:** in 6D, listed in `tests/DEFERRED.md`.
