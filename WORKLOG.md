# WORKLOG

What was done, the evidence, and open issues. Updated every step (plan ground rule 8). Times are local (UTC+1).

## Training pause (24 Oct 2026)

The owner paused the CTM training. While it is paused, the RAM and CPU limits of ground rule 1 are lifted; there are
still no edits in the training's folders. The pause length is counted from the moment the last training work ended.

| What | Time (local) | Evidence |
|---|---|---|
| The three brains stopped ("stopped on request", to relaunch with a fixed acceptance rule) | 23 Oct 22:56:08 | the training's `launcher.log`; the monitor's CPU fell from about 60 % to 36 % at 22:56:11 |
| The training's follow-up steps ran: B1 record and tune, then two smoke runs | 23 Oct 23:05:34 to 24 Oct 01:15:49 | `launcher.log` ("done: B1" at 23:29:46); the last smoke run's `run.log` ("== finished") and `checks-done.txt` |
| **Pause: the last training work ended** | **24 Oct 01:15:49** (00:15:49 UTC) | the last file any training process wrote; the monitor's CPU fell from 34 % to 2 % in the next samples (01:16:03) and stayed there |
| The owner's message arrived; no training process alive | 24 Oct 01:42:55 | process list: only the resource monitor runs; free RAM 9.89 of 15.80 GiB |

## Step 6.0: set up, with the v3 changes (23 Oct 2026)

Status: the "done when" checks pass and the v3 changes are applied (evidence below). The step stops here on purpose,
at the plan's guaranteed stop: **the owner must approve the public sources** (`docs/knowledge/sources-proposal.md`)
before any domain guide is written.

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
| Full suite, with a complete re-read of every file for the old name (`JARVIS_FULL_NAME_SCAN=1 .venv\Scripts\python -m pytest --cov`) | **397 passed, 4 skipped** (the deferred voice tests, D1) in 7 min 42 s; coverage 92 % (verify.py 96 %, knowledge.py 93 %, hash_models.py 89 %, resource_monitor.py 86 %, persona_search 67–99 % per module). A cached rerun of the old-name test after writing this log: 4 passed in 22.5 s |
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

### The v3 changes (owner's message of 23 Oct)

| # | Change | Evidence |
|---|---|---|
| 1 | One JARVIS, many nodes: root `JARVIS/`, mother folder `nodes/`, website package `jarvis-web`, docs, READMEs and website wording | folders renamed before the first commit; `package.json` and both lock files say `jarvis-web`; 42 READMEs rewritten; website visible text says JARVIS and "node"; decisions 0006, 0007 |
| 2 | The old name appears nowhere | `tests/test_no_old_name.py`: file and folder names, every file's content under 50 MB (logs, `.venv` and `node_modules` included), git history and refs; files of 50 MB or more must be manifest-listed models. Fixed: 16 snapshot files (docstrings, API title, default paths, random-seed labels), the website (title, brand, storage keys), configs, docs; the F: cache renamed `F:\JARVIS-cache`; `.venv` rebuilt at the new path; build output regenerated. A full read of 50,952 files (1,048 MB) takes about 6 minutes (Windows Defender scans each file), so the test caches verified files by size and modification time; `JARVIS_FULL_NAME_SCAN=1` reads everything (decision 0010) |
| 3 | Knowledge per node | Each node: its machine manual, plus the shared `owner_rules.md`, `safety_actions.md` (all machines), `farm_layout.md` and `contacts.md` (identical in all four). TOML headers (node, title, kind, version, `status = "draft"`, sources with links). `tools/knowledge.py`: `status`, `approve --by owner` (records the SHA-256 in `rom/approvals.json`), `check --fix` (changed documents go back to draft); only approved, unchanged documents are indexable. `docs/knowledge/simulation_facts.toml`: 161 simulation numbers, each tied by regex to the code; tests prove every fact equals the code, every number in the manuals and shared documents is backed by a fact with the same value, unit and line context, and every fact is used. **All documents are drafts: nothing is approved or indexed yet.** Source proposal for the domain guides: `docs/knowledge/sources-proposal.md` (decision 0008) |
| 4 | Persona search | `tools/persona_search/`: 45 fixed prompts in 7 categories, 3 system prompts, 3 original example sets (6, 7 and 4 exchanges), a 24-setting sampling grid (216 configurations); scoring for character, British tone, concision, no AI disclaimer, and `verify.py` (wrong numbers fail); leaderboard and a blind A/B/C sheet of the top 3 for the owner. `nodes/*/llm/verify.py` (identical in all four). Smoke test with a fake backend; full run deferred as D2 (decision 0009) |
| 5 | Hot-join | Designed into the plan (A6, H1), the comms README and the 6A "done when"; the integration test needs the 6A nodes and NATS, so it is built in 6A/6B; demo step in 6F |
| 6 | Limits while training runs | One test runner at a time was kept (the persona helper ran its own tests; the lead ran none meanwhile). RAM guard in the monitor: stops only programs registered in `logs/guarded.json`, on free RAM below 2 GiB for 3 samples or commit headroom below 1 GiB; creation-time checks prevent touching a reused PID; tested with real processes (decision 0011) |
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

- **Approve (or change) the public sources** per node: `docs/knowledge/sources-proposal.md`. This is the guaranteed stop.
- **emotion2vec+:** download the 1.12 GB model and install FunASR and torch (about 2 GB more on C:), or drop the tone hint.
- **Review and approve the drafts** in each `nodes/<node>/memory/rom/` with `tools\knowledge.py approve <file> --by owner`.
  The manuals carry your requested line, "derived from the farm simulation model, reviewed by the owner"; it becomes
  true when you approve them.
- **Persona-search choices made by the helper**, to confirm:
  - ordinals are checked like other numbers;
  - an unsigned number matches a negative value only next to a direction word;
  - breaking the fourth wall and film lines (including "I'm afraid I can't do that") are hard fails;
  - "-ize" spellings count as American;
  - evidence goes in the user message (or as tool messages, `--evidence-mode tool`, once 6A shows template support).
- **RAM is the real limit:** the training alone pushes free RAM down to about 0.6–1 GB at its peaks (18.6 GB committed). 6A
  loads at most Qwen3-0.6B, registered with the RAM guard.
- **Training CPU:** the three brains run with `--threads 2` each (CPU about 55 %); only noted, the training isn't touched.
- `close_gates` is in Animal Protection's action list but has no gate command (documented; a test watches it).
- The website warns about one 1.1 MB bundle (the 3D farm); npm skipped esbuild's optional install script (builds fine).
- `torchaudio` 2.11.0 resolves next to `torch` 2.14.1; check they work together before 6E.
