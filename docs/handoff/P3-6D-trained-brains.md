# P3 · Step 6D: the trained brains, installed inside JARVIS

**Builder:** Luna, in `C:\Users\Aymane\Desktop\JARVIS-luna`, branch `build-6w6d6e`, after P1 is committed. Read
`docs/handoff/README.md` and **decision 0015** first: the owner chose how this is done, and 0015 is the design.
Your prompt gives you two paths: the **release folder** (release v3.1, read-only) and the **training's Python** (it
has torch 2.14.1; you may run it, never install into it). Never write either path into any file, log you commit,
commit message or report: say "the release folder" and "the training's Python". The **upstream CTM folder** is
inside the training's folders (the prompt gives its path too): read-only, same rule.

**What the release holds** (its README): `brains/<machine>/` with `reasoning_engine_INCv3.1.pt`,
`reasoning_engine_HVACv3.1.pt` and `reasoning_engine_APv3.1.pt`, each with `decide.json` and `stats.npz`;
`code_snapshot/` (the exact code that trained them: packages `farm_poc` and `ctm_training`); `memory/cmm.py`
(byte-identical to `code_snapshot/farm_poc/nodes/cmm.py`). The exam built each node's brain like this
(`ctm_training/practice.py`, `CheckpointBrains`): `torch.set_num_threads(1)`; `load_brain(engine, brains_dir, "cpu")`;
`TrainedNodeBrain(copy.deepcopy(loaded), start_hour, Decider.for_checkpoint(engine), False, hide_notes=True,
hold_calls=True)` with `start_hour = spec.config["weather"]["start_hour"]` (0.0 when absent); then
`NodeRuntime(engine, modes={n: "real" for n in NODE_IDS}, brains=brains, memory_factory=lambda n: CognitiveMemory(n, path))`.
incubator-1 and incubator-2 each get their **own** instance of the incubator brain. `ctm_training/model.py` imports
the upstream library from `<parent of ctm_training>/base ctm/continuous-thought-machines-main` (`models.ctm_rl` and
what it imports).

**Never** edit, move or delete anything in the release folder or the training's folders, and leave no file there:
run their code with `-B` (no `.pyc`), put every database and output in a temporary folder, and prove it with a
listing of those folders (names, sizes, modification times) taken before and after, identical.

## 1. torch

Add `torch==2.14.1` to `requirements.in` (the training's version; a comment: the trained brains, step 6D), regenerate
the hashed lock with the command in its header, and install from the lock. Report the wheel's size and any other
package whose pinned version changed (there should be none; if the resolver wants to change one, stop and report).

## 2. Find exactly what the brains need (`tools/release_probe.py`)

Run under the training's Python with `-B`: build the exam's recipe above from the release's own code (the upstream
folder put on `sys.path` the way `model.py` does it), step 30 ticks, and write (into a temporary folder) the list of
every module file it loaded from `code_snapshot/` and from the upstream folder. That list is what you copy. Add
`LICENSE` (and `NOTICE` if there is one) of the upstream library.

## 3. Install (`tools/install_release.py --release DIR --upstream DIR --modules FILE`)

Into `matrix/release_v31/`:
- **Code:** the probe's modules, at the same relative paths (`farm_poc/...`, `ctm_training/...`, and the upstream
  modules under `base ctm/continuous-thought-machines-main/`, so `model.py`'s own path still works unchanged).
- **The old name** (`tools/name_guard.py`'s `OLD`; never type it) is replaced with a reversible word map: the
  all-lower-case form becomes `testbed`, the capitalised form `Testbed`, the all-upper-case form `TESTBED`. The tool
  refuses (and you stop) if any other spelling of the name occurs, or if "testbed" in any case already occurs in the
  source. **Proof for every file:** applying the reverse map to the installed file gives the release file byte for
  byte. Nothing else in the code changes: not physics, encoders, labels, logic or paths.
- **Engines:** `torch.load` each release engine (`weights_only=False`, as `load_brain` does), check that its `config`
  holds no old name, and `torch.save` only `{"config": saved["config"], "state_dict": saved["state_dict"]}` under the
  same file name in `matrix/release_v31/brains/<machine>/`. **Proof:** the same keys, and `torch.equal` for every
  tensor, and an equal config. `decide.json` and `stats.npz` are copied byte for byte (stop if either contains the
  old name). Nothing else from `brains/` is copied.
- `matrix/release_v31/PROVENANCE.json`: the release's version ("v3.1"), and for every installed file: its release
  SHA-256, its installed SHA-256, and how it was installed (copied, renamed, re-saved). **Never a path outside
  JARVIS.**
- Running the tool twice gives byte-identical files. A test proves it on a small fake release in a temporary folder.
- `pyproject.toml`: exclude `matrix/release_v31` from pyright and coverage (frozen code; it is still in the old-name
  scan). `matrix/release_v31/README.md` says what it is, where it came from, and that it is never edited by hand.

## 4. Prove the copy is the same brain (`tools/release_equivalence.py`)

Run the same thing twice: with the training's Python on the release folder (and the upstream folder), and with
`.venv` on `matrix/release_v31`. For each of the four demo-fault families (P1's mapping), one ordinary library
episode spec, built with the exam's harness (`farm_poc.scenarios.build.build_episode` with a `CheckpointBrains`-style
factory: `hide_notes=True`, `hold_calls=True`, one torch thread). Record for every tick and node: assessment,
diagnosis, confidence (6 decimals), the gate's action and communication, the incident id. **Pass:** every assessment,
diagnosis, gate decision and incident id identical; every confidence within 1e-5. If one episode takes more than 10
minutes, shorten its duration the same way on both sides and say so. **If anything differs, stop:** report the first
differing tick and node; do not tune, do not "fix" the copy.

## 5. The trained feed in the farm runtime

- `matrix/farm_runtime.py --feed simulated|trained` (the launcher's default becomes `trained`; `start_all.ps1 -Feed`;
  the supervisor passes it). `simulated` is the 6A snapshot with rule brains, unchanged, and stays the default in the
  fast tests. `trained` uses `matrix/release_v31` with the brains built exactly as the exam built them (above),
  `FarmAssessment.brain = "ctm"`, the label "live: trained brains".
- The engine runs an ordinary library day's config (a `normal` spec's config for a fixed variant: weather on, its own
  `start_hour`), and the brains get that `start_hour`. Prove that it equals the engine's own hour of day at tick 0.
  Record the variant in decision 0021.
- Each node's CMM is a SQLite file in a gitignored data folder of the farm runtime, fresh for every farm run (a reset
  is a new run, a new `feed_id`).
- Threads: measure ticks per second with 1, 2 and 4 torch threads. Keep 1 unless more is faster **and** step 4's
  equivalence still holds with that setting.
- Report the speed actually reached at 1x, 10x, 30x, 60x and 120x asked (the website shows both).
- **Every Matrix API route works on both feeds:** `/sim/*`, `/links/*`, P1's `/faults/*`, and the new acknowledgement
  route below.

**The owner-acknowledgement route** (Terra's gateway calls it; build exactly this): `POST /alerts/{node}/{incident_id}/ack`,
no body. It marks the current run's owner notifications that the node's brain sent for that incident and that are
delivered but not yet acknowledged, as the Matrix's communications service does when the simulated owner answers
(`matrix/communications.py`: the record's acknowledgement time becomes now, so its status turns "acknowledged" on the
next update, and the brains see the same signal they were trained on). Find how a notification's `sender` names the
node, and test the mapping. 200: `{"node", "incident_id", "acknowledged": <records marked, at least 1>, "farm_time"}`.
404 `{"detail": "..."}` when there is none. A test on the trained feed proves that the incident id the farm runtime
publishes (`farm.<node>.incident`) is the one in the brain's owner notification, so the website's acknowledgement
reaches the right record.

## 6. The node folders

Each node's `reasoning/README.md` and `memory/README.md` (edit in incubator-1, then `sync_nodes.py`) say where its
brain and its CMM run and why (decisions 0012 and 0015). No code copies into the node folders.

## 7. Evidence for the evaluation

- **The safety path without any LLM (E7's brains part, plan P1):** with the trained feed and **no** LLM program at all
  (`start_all.ps1 -NoLlm`), each of the four demo faults injected through the gateway is still caught and the safety
  gate still acts; plus a static test that the farm runtime imports nothing from any node's `llm/`. Put the result in
  your report (the senior merges it into E7).
- **E9, the epistemic wall** (`tests/llm/eval/e9_epistemic_wall.py`): the four faults on the trained feed; record
  every message on every subject a node subscribes to (`farm.<node>.*`, `farm.links`, `farm.sim`, `jarvis.*`,
  `mentor.llm_failures`); collect the forbidden strings in-process from the engine (the scenario and episode ids, the
  family names, the fault modes, every string in its hidden truth); scan with exact tokens. Report every match with
  its context and judge it: a code that the machine itself publishes as a public alarm is telemetry, not a leak.
  Target: 0 leaks.
- **E10, detection part** (`tests/llm/eval/e10_detection.py`): the 4 demo faults × 5 engine seeds that no training,
  judging or exam episode used (compute the library's seeds for those spec lists and prove the five are not among
  them), with the trained brains and with the rule brains on the same release copy: caught (the faulted machine's
  node leaves NORMAL with a diagnosis of that fault's kind; write down the mapping from family to diagnoses), and the
  farm time from injection to detection. The explanation part (the agent's words) is measured after the merge.
  Known open issues the owner accepted (the release README): animal protection's false alerts on a calm farm,
  civil-protection calls, the incubator's response to its own stuck heater. Report what you measure; do not tune.

## Done when (plan 6D, the parts on this branch)

| Check | Evidence |
|---|---|
| The brains are installed inside JARVIS, the old name nowhere, the weights proven equal | step 3's proofs; `PROVENANCE.json`; the full old-name scan (`JARVIS_FULL_NAME_SCAN=1`) and `name_guard.py history` |
| The installed copy decides exactly like the release | step 4 |
| The farm runs "live: trained brains" with the exam's settings, and every route works on it | step 5's tests; the speeds |
| Each of the four demo faults, injected from the website, is caught by the trained brains | a real run: `start_all.ps1 -NoLlm` (trained feed), the faults from the website, what each node said and when |
| E9 and E10 (detection) measured | `logs/eval/E9.json`, `E10.json` |
| pyright, the full suite twice | README rule 14 |

Decision record 0021 (the trained feed: config, threads, CMM files). Your report: `docs/handoff/reports/P3.md`. Then
start P4.
