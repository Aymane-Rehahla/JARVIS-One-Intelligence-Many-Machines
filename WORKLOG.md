# WORKLOG

What was done, the evidence, and open issues. Updated every step (plan ground rule 8). Times are local (UTC+1).

## Training pause (24 Oct 2026): over, the training runs again

The owner paused the CTM training; while it was paused, the RAM and CPU limits of ground rule 1 were lifted (there
were still no edits in the training's folders). **The training restarted at 04:55:20 and runs again** (wrap-up B2,
then the exam), so those limits apply again. The owner's correction (24 Oct 13:37): until the owner says the training
is done, no LLM server is started and the full test suite is not run; the lighter 6A work goes first (contracts,
gateway, supervisor code, single test files). After the training, the trained brains and `cmm.py` arrive in a release
folder, and the farm runtime switches from the rule brains to them.

| What | Time (local) | Evidence |
|---|---|---|
| The three brains stopped ("stopped on request", to relaunch with a fixed acceptance rule) | 23 Oct 22:56:08 | the training's `launcher.log`; the monitor's CPU fell from about 60 % to 36 % at 22:56:11 |
| The training's follow-up steps ran: B1 record and tune, then two smoke runs | 23 Oct 23:05:34 to 24 Oct 01:15:49 | `launcher.log` ("done: B1" at 23:29:46); the last smoke run's `run.log` ("== finished") and `checks-done.txt` |
| **Pause: the last training work ended** | **24 Oct 01:15:49** (00:15:49 UTC) | the last file any training process wrote; the monitor's CPU fell from 34 % to 2 % in the next samples (01:16:03) and stayed there |
| The owner's message arrived; no training process alive | 24 Oct 01:42:55 | process list: only the resource monitor runs; free RAM 9.89 of 15.80 GiB |
| **The training restarted** (wrap-up B2, three brains in parallel) | **24 Oct 04:55:20** | `launcher.log` ("start: wrap-up B2 (three brains in parallel)"); the three training processes were created at 04:55:20 |
| **Pause length** | **3 h 39 min 31 s** | 01:15:49 to 04:55:20: the training's end time moves back by this much |
| The owner's correction arrived | 24 Oct 13:37:35 | free RAM 2.50 of 15.80 GiB; the three training processes use about 2.5, 3.5 and 3.8 GB; of ours only the resource monitor runs |

What ran in the pause: the full test suite (6 min 4 s, from 04:34:07 to 04:40:10; `.coverage` written at 04:40:11)
and a short smoke test of the first 6A code (a NATS server, the farm runtime and two nodes, at 04:48, all stopped by
04:49). After 04:55:20 only the independent reviewer ran (read-only checks and single test files); no LLM server was
started at any time.

During the pause the PC restarted at 02:02 and was shut down from 02:54:51 to 03:42:57 (Windows System log, event log
service stop and start). The resource monitor does not start at boot, so **it was not running from 02:01:53 until it was
restarted, with the second review's guard code, at 04:33:39.** Nothing of ours ran in that gap.

**RAM after the restart.** In its first minutes the training alone took the commit headroom to 0.00 GiB (04:56:24 and
04:58:06) and kept free RAM below 2 GiB for up to 53 s; the guard's conditions were met 16 times between 04:56 and 05:13
and it stopped nothing, because nothing of ours was registered (it rewrote an empty `logs/guarded.json` at 05:12).
Since 06:00 the commit headroom has stayed at 5.0 GiB or more. Lesson: start nothing while the training changes phase.

## Step 6.0: set up, with the v3 changes (23 Oct 2026)

Status: the "done when" checks pass, the v3 changes are applied and both independent reviews' findings are fixed
(evidence below). Under plan v3.1 the source list goes to the owner and the work continues while they review it; no
domain guide is written until **the owner approves the public sources** (`docs/knowledge/sources-proposal.md`).

### "Done when" checks (plan section 9, step 6.0)

| Check | Command | Result |
|---|---|---|
| The tree matches section 1 | `.venv\Scripts\python -m pytest tests\test_tree.py` | passes (see the full run below); tracked structure only, so a fresh clone passes too |
| The environment installs from the lock file | `.venv\Scripts\python -m pip install --require-hashes -r requirements.lock`, then `pip check` | done twice: first at the old path (exit 0, 2 min 59 s), then again after the rename to `JARVIS/` from scratch (exit 0, 2 min 14 s); `pip check`: "No broken requirements found" |
| `MANIFEST.json` checks out | `.venv\Scripts\python tools\hash_models.py check` | "checked 394 files (SHA-256): missing 0, changed 0, unlisted 0 / OK" (3,917,157,850 bytes, 10.5 s) |
| `llama-server` lists the RX 580 as a Vulkan device | `third_party\llama.cpp\bin\llama-server.exe --list-devices` | `Vulkan0: AMD Radeon RX 580 2048SP (8192 MiB, 7367 MiB free)`; `--version`: `0.7.0-dev (build 11829, commit 184f65360)` |
| Website typecheck and build | `npm run typecheck`, `npm run build` in `interface\web` (portable Node 24.22.1) | both exit 0 after the v3 wording changes (built in 16.0 s; title "JARVIS \| One intelligence, many machines") |

### Tests, types, coverage

| What | Result |
|---|---|
| Full suite, with a complete re-read of every file for the old name (`JARVIS_FULL_NAME_SCAN=1 .venv\Scripts\python -m pytest --cov`) | first pass: 397 passed, 4 skipped. **After the second review's fixes (24 Oct): 495 passed, 4 skipped** (the deferred voice tests, D1) in 6 min 4 s; coverage 91 % (verify.py 97 %, knowledge.py 92 %, hash_models.py 89 %, resource_monitor.py 88 %, persona_search 69–99 % per module, name_guard.py 69 %, then its command line got a test: the old-name file, 7 passed) |
| pyright 1.1.414 (standard, Windows) on `tools/`, `tests/`, `nodes/`, `interface/gateway/` | **0 errors, 0 warnings** |

### What step 6.0 built (first pass, reviewed)

| Item | Evidence |
|---|---|
| Training left alone | Only reads and copies from the training's folders. The three brain processes kept running throughout; nothing was installed into the training's Python (3.14) |
| Python 3.12 chosen after checking | `.venv` is Python 3.12.10. SQLite 3.49.1 has FTS5. sherpa-onnx 1.13.8, onnxruntime 1.31.0 and openwakeword 0.6.0 install as wheels and import. FunASR dry run: funasr 1.4.16 + torch 2.14.1 + torchaudio 2.11.0 resolve to cp312 Windows wheels (4 pure-Python sources, no compiler needed) |
| Lock file | `requirements.lock`, hashed, pip and setuptools pinned |
| Pre-downloaded models | Hard links (decision 0005), 2 names each, no extra space. Their SHA-256 matches Hugging Face's published hashes: Qwen3-0.6B-Q8_0 `9465e63a…`, Qwen-3.5-2b-roleplay-tuned.Q8_0 `b70a7f0b…`, bge-small `onnx/model.onnx` `828e1496…` |
| Downloads (`F:\JARVIS-cache\downloads\download.log`) | llama.cpp b11829 Vulkan, nats-server v2.15.1, silero_vad.onnx, Kokoro v1.0 and Parakeet TDT 0.6B v2 int8: SHA-256 verified against the publishers' digests; openWakeWord v0.5.1 ×3 (no published digest; hashes recorded) |
| Unpacking | Unpacked files equal the archive listings: llama.cpp 53, Parakeet 5, Kokoro 377; `nats-server --version` prints `v2.15.1`. Voice models downloaded, **not loaded** (ground rule 1) |
| `_dev/farm_poc_snapshot/` | First copy: 88 of 88 files identical to the original, read-only. After the owner-ordered name removal (decision 0010): 88 files, all read-only, tree hash `6b2999ea212bc43b71ea9c6d9d5d5d4b8287d2963b435aa18db2a6543f5f3149` |
| Website in `interface/web/` | 87 of 87 files copied without `node_modules`; `npm ci` exit 0 |
| Tools | `hash_models.py` (validated manifest; paths can't escape `models/`; `write` refuses to bless a changed file without `--accept-changes`), `resource_monitor.py` (CPU, RAM, commit headroom, VRAM per adapter through DXGI and PDH, C: and F: free space; bounded log; single instance by PID and creation time; graceful `--stop`; one failed sample never ends it; RAM guard) |
| Resource monitor running | Restarted from `JARVIS/` through WMI (return value 0) after the code changes |
| Decision records | 0001–0005 (first pass), 0006–0011 (v3) in `docs/decisions/` |

### The independent review of the first pass (Q10) and what was fixed

A fresh reviewer found no blockers, five should-fix items and four nits. All are fixed:

| # | Finding | Fix |
|---|---|---|
| 1 | Manuals contradicted their sources: "room sensors read the workspace air" (they are the cabinet probes; the ambient sensors read the workspace); civil protection triggers; "a single sign is never enough" (only fire needs two); battery takeover at 180 V, not 50 V; duct heat needs "heater off"; the return fan doesn't follow demand | all corrected against the code; `tests/test_rom_quotes.py` and `tests/test_rom_numbers.py` now prove the quotes and every number |
| 2 | The log claimed "free RAM stayed above 4 GB" | corrected: the monitor measured lows of 0.60 GiB, caused by the training itself (below) |
| 3 | Monitor: no per-sample error handling, no stop path, PID reuse, interval not validated, noisy RAM warning, F: not watched | all fixed and tested (stop file + `--stop`, creation-time PID records, ≥ 1 s interval, sustained-RAM and commit-headroom limits, C: and F:) |
| 4 | `test_tree.py` needed untracked folders and the models | rewritten: tracked structure only; model checks skip with a message on a fresh clone |
| 5 | Unrecorded deviations | recorded below |
| nits | `\n` that turned into a line break in a README; manifest path safety; I/O exit codes; pytest priority; fixed hypothesis seeds; marker lookup; ignore rules; GiB labels; README in/out/test lines | all fixed |

### The second independent review (after the v3 changes) and what was fixed

A fresh reviewer checked the v3 work and found twelve items. All are fixed; each fix has tests.

| # | Finding | Fix and evidence |
|---|---|---|
| 1 | `verify.py` read a key `min` or a suffix `_min` as minutes, so `{"min": 36.8}` made a correct "36.8 °C" fail (and bare `s`, `h`, `c`, `v`, `w`, `l` were ambiguous) | statistics (`min`, `max`, `mean`, `avg`, their key endings, and `value`) take the unit of what they summarise: the object's `unit`, its `signal`, or the key above; no unit from ambiguous bare keys; `_min` never means minutes. 21 key cases and 4 evidence shapes tested |
| 2 | Dates and times were masked without range checks ("55/60 %", "3/4", "37:50" passed unchecked), and times were never checked | only valid dates (slash dates need a year) and times are masked; **every time of day said must match a time in the evidence** (to the minute; "2 pm" within 15 min; seconds when said); `states_farm_time()` and a farm-time criterion in the persona scoring (hard fail for farm status, numbers and danger). Report schema 2 |
| 3 | The blind sheet said "every number has already been checked" while showing hard-failed replies; any configuration could reach the top 3 | only configurations with no verify fail and no danger hard fail are eligible (fewer than 3: the sheet says so; fewer than 2: no sheet); a failed check is printed under its reply; the sheet says exactly what was checked; `blind/`, `sealed/` (the key) and `scored/` are separate, and the owner rates before opening the last two |
| 4 | The film safeguard could not catch JARVIS's own film lines; nothing scored claimed actions | `data/film_blocklist.json`: 13 well-known lines trimmed to their distinctive part, stored only as 48 SHA-256 hashes of word 4-grams (no film text in any file; two 4-grams of one line are a hit; tested with a made-up line). A hard fail for claiming an action the evidence does not record (`checks.py`; reworked after the third review, 32 sentences in its test) |
| 5 | RAM guard: PID reuse between check and kill, any program could register, no lock, coarse sampling | one process handle for the identity check and the kill (creation time and image path); only images under `JARVIS/` or the venv's base Python may register; a lock file; 30 s time-based rule; 2 s sampling while something is registered (decision 0011). The running monitor was restarted with the new code at 04:33:39 (it had not run since the PC restarted at 02:02) |
| 6 | The RAM evidence and decision 0011 were out of date ("3 samples", "0.6 GB") | decision 0011 rewritten with the measured run: 5,636 samples, 881 (16 %) below 2 GiB but never two in a row, lowest free RAM 0.03 GiB, lowest commit headroom 1.98 GiB |
| 7 | Knowledge tooling: copies could be approved one by one, approval without a person, unchanged versions, a tautological numbers test | `approve` covers all identical copies at once, needs a terminal and a typed `APPROVE`, and changed content needs a higher version; the numbers test finds every node's documents, reads "two" to "twelve" and "twice", supports count facts (10 added), and checks domain guides for conflicts with the simulation (they must be listed under "Conflicts with the simulation") |
| 8 | The old-name test missed UTF-16 text, the staged tree, committer identities and tag contents | `tools/name_guard.py` + the test: UTF-16 as well as ASCII; git's plain files; the staged tree; **every object in `.git`, reachable or not** (commits with author, committer and message, trees, file versions, tags) and every ref. The first full object scan found the name in 40 unreachable blobs left by staging before the rename (no commit, index or reflog used them); they were pruned with `git gc --prune=now`, after which the history check is clean. The check now runs before every commit and tag (plan v3.1 run rules) |
| 9 | 6D would copy the training's code and checkpoints, which may carry the old name | written into step 6D as an owner decision (plan v3.1): re-save weights-only state dicts with tensor-for-tensor equality proven and new hashes recorded, or load the weights from outside `JARVIS/` |
| 10 | Hot-join was not in 6B's "done when"; the guard's "stays below" reading was not put to the owner | 6B's "done when" now has the full hot-join check (three nodes, start the fourth, ask about it); both are owner decisions in plan section 12, point 0 |
| 11 | The HVAC sources describe poultry-house ventilation while the simulated HVAC is a ducted air handler; a wrong filter line; duct pressure compared with house static pressure | the proposal flags the scope mismatch for the owner, corrects the filter line (the simulation has one), warns that 120 Pa duct pressure is not comparable to the 25–30 Pa house figures, gives the optional extras IDs (I6, H6, A9) and ends with "Your decision" and the approval command; a test pins the 21 IDs |
| 12 | Nits: plan Q1 and the `rom/` row, `node.toml` comment, the website's "its own Jarvis node", `hash_models write` hashing twice | all fixed; the website spells JARVIS one way in its visible text (event source, preview replies, scenario text) and was rebuilt (typecheck and build pass, 11.8 s); `write` hashes each file once (tested) |

### The third independent review (24 Oct) and what was fixed

A fresh reviewer checked the fixes above and found five groups of problems. All are fixed. The training runs again, so
the touched test files were run one at a time: persona harness 69 passed, `test_verify.py` 147 passed,
`test_rom_numbers.py` 186 passed, `test_knowledge.py` + `test_rom_quotes.py` 33 passed; pyright on the changed files:
0 errors. Before the tag: `name_guard.py staged` and `history` clean; `test_no_old_name.py` 7 passed (27 s, with the
content cache: unchanged files were not read again). The full suite and the full re-read are deferred
(`tests/DEFERRED.md`, D4).

| # | Finding | Fix and evidence |
|---|---|---|
| 1 | The claimed-action check missed claims ("I've informed the owner", "ventilation deployed", "taken the liberty of opening…"), flagged offers and questions, read "nothing was started" as a claim, and tied no claim to a machine | rewritten in `tools/persona_search/checks.py`: record nouns carry a kind (ventilation, shutdown, shutters, barrier, alarm, calls to the owner, brigade or civil protection); questions and offers ("shall I…", "I can…") are skipped; more negations count (nothing, nobody, none, neither, nor, no one); only "the safety system/gate/path" may be the one acting; each claim names the machine it mentions; a claim the evidence records is allowed. 32 sentences tested against the shipped evidence and a stopped-heater record |
| 2 | Wrong statements in this log: the monitor "running throughout", the suite's time, the claim-sentence count, the facts count, the hours sampled | corrected: the suite ran 04:34–04:40; the PC restarted at 02:02 and was off 02:54–03:42; the monitor was not running 02:01:53–04:33:39; 32 claim sentences; 171 facts; 15.6 h sampled. Decision 0011 rewritten to match |
| 3 | The guide conflict check compared every number on a guide line with every fact, so plain prose and ranges were reported as conflicts, or hid real ones | each fact a guide can contradict has a case-insensitive `guide_context` (15 in `simulation_facts.toml`); a line is compared only with the facts whose context it matches; a range containing the fact's value agrees; "%" on a humidity line is %RH; hyphen ranges, negative numbers and ISO dates are read correctly. Tested with plain prose, agreeing and disagreeing ranges, hyphen and negative numbers |
| 4 | `verify.py` let wrong times and numbers through: ISO times, offsets such as "+01:00", time words ("half past six", "noon"), spelled numbers with a unit ("thirty-seven degrees") | every stated time, ISO times included, is checked against the evidence times; an offset belongs to its timestamp; time words and spelled numbers with a unit are read and checked; "06:00:00" matches evidence without seconds. The four nodes' copies are identical (checked with `cmp`) |
| 5 | Nits: "24/7" read as a date, counts with no unit | "24/7" is masked; count keys have the unit "count" |

### The v3 changes (owner's message of 23 Oct)

| # | Change | Evidence |
|---|---|---|
| 1 | One JARVIS, many nodes: root `JARVIS/`, mother folder `nodes/`, website package `jarvis-web`, docs, READMEs and website wording | folders renamed before the first commit; `package.json` and both lock files say `jarvis-web`; 42 READMEs rewritten; website visible text says JARVIS and "node"; decisions 0006, 0007 |
| 2 | The old name appears nowhere | `tests/test_no_old_name.py`: file and folder names, every file's content under 50 MB (logs, `.venv` and `node_modules` included), git history and refs; files of 50 MB or more must be manifest-listed models. Fixed: 16 snapshot files (docstrings, API title, default paths, random-seed labels), the website (title, brand, storage keys), configs, docs; the F: cache renamed `F:\JARVIS-cache`; `.venv` rebuilt at the new path; build output regenerated. A full read of 50,952 files (1,048 MB) takes about 6 minutes (Windows Defender scans each file), so the test caches verified files by size and modification time; `JARVIS_FULL_NAME_SCAN=1` reads everything (decision 0010) |
| 3 | Knowledge per node | Each node: its machine manual, plus the shared `owner_rules.md`, `safety_actions.md` (all machines), `farm_layout.md` and `contacts.md` (identical in all four). TOML headers (node, title, kind, version, `status = "draft"`, sources with links). `tools/knowledge.py`: `status`, `approve --by owner` (records the SHA-256 in `rom/approvals.json`), `check --fix` (changed documents go back to draft); only approved, unchanged documents are indexable. `docs/knowledge/simulation_facts.toml`: 161 simulation numbers at first (171 facts after the reviews), each tied by regex to the code; tests prove every fact equals the code, every number in the manuals and shared documents is backed by a fact with the same value, unit and line context, and every fact is used. **All documents are drafts: nothing is approved or indexed yet.** Source proposal for the domain guides: `docs/knowledge/sources-proposal.md` (decision 0008) |
| 4 | Persona search | `tools/persona_search/`: 45 fixed prompts in 7 categories, 3 system prompts, 3 original example sets (6, 7 and 4 exchanges), a 24-setting sampling grid (216 configurations); scoring for character, British tone, concision, no AI disclaimer, and `verify.py` (wrong numbers fail); leaderboard and a blind A/B/C sheet of the top 3 for the owner. `nodes/*/llm/verify.py` (identical in all four). Smoke test with a fake backend; full run deferred as D2 (decision 0009) |
| 5 | Hot-join | Designed into the plan (A6, H1), the comms README and the 6A "done when"; the integration test needs the 6A nodes and NATS, so it is built in 6A/6B; demo step in 6F |
| 6 | Limits while training runs | One test runner at a time was kept (the persona helper ran its own tests; the lead ran none meanwhile). RAM guard in the monitor: stops only programs registered in `logs/guarded.json`, when free RAM stays below 2 GiB for 30 s or commit headroom falls below 1 GiB; identity checked through one process handle (creation time and image), so a reused PID is never touched; tested with real processes (decision 0011, revised after the second review) |
| 7 | Recorded | `docs/phase6-llm-plan.md` is v3 with a changelog; decisions 0006–0011 |

### Differences from the plan (ground rule 8, plan section 10.7)

1. **Hard links instead of copies** for the pre-downloaded models (C: was low: 9.7–14 GB free). Decision 0005.
2. **emotion2vec+ not downloaded:** its `model.pt` is 1.12 GB, not about 0.35 GB, so over the 1 GB rule. FunASR and torch
   wait too.
3. **Files the plan doesn't list:** `pyproject.toml`, `requirements.in`/`.lock`, `.gitignore`, `tests/conftest.py`,
   `tests/tools/`, `tests/voice/`, `models/README.md`, `docs/knowledge/`, `tools/knowledge.py`, `tools/persona_search/`.
4. **Binaries in `bin/` subfolders** of `third_party/llama.cpp/` and `third_party/nats-server/`.
5. **`node.toml` holds no ports:** `launcher/ports.toml` is the single source of truth for ports.
6. **The node's own `README.md` differs per node** (with `node.toml`, `memory/rom/` and `data/`); `sync_nodes.py` (6A)
   must skip it.
7. **The start check is the full SHA-256 check** (about 12 s); `check --quick` (sizes only) is for tests and development.
8. **`rom/` and the LLM:** the LLM never opens the CMM, but its search indexes the approved `memory/rom/` documents.
9. **The old hatching sheet** was folded into the incubator manual; the incubation guide comes from approved public sources.
10. **Random-seed labels in the snapshot** were renamed with the old name, so a seed gives a different (equally valid)
    farm run than the training-era code. Physics, encoders and labels are unchanged (decision 0010).

### Open issues and decisions for the owner

- **Approve (or change) the public sources** per node: `docs/knowledge/sources-proposal.md`, ending with "Your
  decision": approve or strike each ID, and choose the HVAC scope (a: poultry-house sources as background, the air
  handler's troubleshooting from its manual; b: also research air-handler sources). Recorded only by you, with
  `tools\knowledge.py approve-sources`. Under plan v3.1 the work continues while you review it.
- **emotion2vec+:** download the 1.12 GB model and install FunASR and torch (about 2 GB more on C:), or drop the tone hint.
- **Review and approve the drafts** in each `nodes/<node>/memory/rom/` with `tools\knowledge.py approve <file> --by owner`.
  The manuals carry your requested line, "derived from the farm simulation model, reviewed by the owner"; it becomes
  true when you approve them.
- **Persona-search choices**, to confirm:
  - ordinals are checked like other numbers;
  - an unsigned number matches a negative value only next to a direction word;
  - breaking the fourth wall and film lines (including "I'm afraid I can't do that") are hard fails;
  - "-ize" spellings count as American;
  - a reply may say an action happened only when the evidence records it (your spec: never claim actions that didn't
    happen), so "I've stopped its heater" passes when the safety gate's record shows it;
  - not stating the farm time is a hard fail in the farm status, numbers and danger categories;
  - evidence goes in the user message (or as tool messages, `--evidence-mode tool`, once 6A shows template support).
- **The RAM guard's trigger:** it acts when free RAM *stays* below 2 GiB for 30 s (or commit headroom falls below
  1 GiB), not on every dip below 2 GB: the training made 881 one-sample dips in the 15.6 hours the monitor sampled before the pause (decision 0011).
- **6D checkpoints and the old name:** re-save weights-only state dicts (tensor equality proven, new hashes), or load
  the weights from outside `JARVIS/` (plan step 6D).
- **RAM while training runs:** the training alone pushes free RAM down to about 0.03–1 GiB at its peaks (2.5 GiB free
  at 13:37 on 24 Oct). Until you say the training is done, no LLM server starts and the full suite does not run (D3,
  D4 in `tests/DEFERRED.md`).
- **Training CPU:** the three brains run with `--threads 2` each (CPU about 55 %); only noted, the training isn't touched.
- `close_gates` is in Animal Protection's action list but has no gate command (documented; a test watches it).
- The website warns about one 1.1 MB bundle (the 3D farm); npm skipped esbuild's optional install script (builds fine).
- `torchaudio` 2.11.0 resolves next to `torch` 2.14.1; check they work together before 6E.
