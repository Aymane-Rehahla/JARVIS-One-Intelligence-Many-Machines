# 0012: The 6A test feed stubs the snapshot's CTM module and runs rule brains

Status: accepted (step 6A, within plan section 9: the test feed runs "live: simulated brains" until 6D).

## Context
The farm runtime (`matrix/farm_runtime.py`) runs the read-only snapshot of the farm (`_dev/farm_poc_snapshot/`). Its
`NodeRuntime` imports `nodes/ctm_brain.py` at the top of the file, and that module imports torch. The JARVIS
environment (Python 3.12, `requirements.lock`) has no torch, and the plan does not add it before 6D: the trained brains
and `cmm.py` arrive in a release folder after the training (owner's message of 24 Oct). Installing torch now would be a
download of over 1 GB not listed in plan section 5 (plan 10.4: ask first), and the snapshot must not be edited.

## Decision
- Before the snapshot is imported, `farm_runtime.load_snapshot()` puts a stand-in module under the name
  `farm_poc_snapshot.nodes.ctm_brain`. Its `NodeBrainConfig` and `NodeCTMBrain` raise `RuntimeError` when called, so a
  CTM can never be built by mistake and nothing pretends to be one.
- The test feed runs every node in the snapshot's default `"simulated"` mode, that is the rule brains, and labels every
  message "live: simulated brains" (honest labels).
- The snapshot stays unchanged: no file is edited and no `__pycache__` is written into it (`sys.dont_write_bytecode`).
- `tests/test_farm_runtime.py` checks all three: only `RuleBrain`s run, the stub refuses, the snapshot has no
  `__pycache__`.

## Alternatives
- **Install torch in the JARVIS environment now:** a large unlisted download for a module the test feed never uses.
  Rejected.
- **Edit the snapshot to import the CTM module lazily:** the snapshot is read-only. Rejected.
- **Run the test feed on the training's Python 3.14, which has torch:** that environment belongs to the training
  (ground rule 1), and JARVIS runs on its own. Rejected.

## Consequences
- The 6A-6C feed shows rule-brain reasoning only. Step 6D replaces the snapshot with the released brains and `cmm.py`
  ("live: trained brains"); the stub is then removed, and decision 0012 is superseded.
- If the snapshot's `NodeRuntime` ever needs anything else from the CTM module, the import fails loudly at start, not
  silently at run time.
