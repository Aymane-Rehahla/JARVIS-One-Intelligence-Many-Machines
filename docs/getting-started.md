# Getting started

Windows 10 or 11, about 8 GB of free disk, an internet connection for the first setup. A GPU with Vulkan drivers
(8 GB) runs the language models; the brains, search and voice run on the CPU. A microphone and speakers for voice.

## 1. Set up (once)

From the project folder, in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

It does everything, and can be run again to resume:

1. finds Python 3.12 (installs it with winget if missing);
2. creates `.venv` and installs the exact, hash-checked Python packages (`requirements.lock`);
3. downloads, with `tools/download.py`, every file git does not carry, each checked against its SHA-256:
   the language models (Qwen 3.5 2B, Qwen3-0.6B), bge-small, the voice models (Silero VAD, Parakeet, Kokoro,
   openWakeWord), llama.cpp (Vulkan build b11829), the NATS server (v2.15.1) and a portable Node.js (v24.22.1);
4. installs the website's packages.

**The 3D farm models** (277 MB, too large for git) come as `JARVIS-farm-assets.zip`, attached to the GitHub release.
Put the zip in the project folder before running setup, or add it later:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1 -Assets <path to JARVIS-farm-assets.zip, or its URL>
```

Without them everything works, but the 3D farm view stays empty.

## 2. Run

```powershell
powershell -ExecutionPolicy Bypass -File .\launcher\start_all.ps1 -NoMonitor   # starts everything, opens the website
powershell -ExecutionPolicy Bypass -File .\launcher\stop_all.ps1               # stops everything
```

The first start takes a minute or two while the models load. The website is <http://127.0.0.1:5173>. Every port is
in `launcher/ports.toml`; everything listens on 127.0.0.1 only.

## 3. Use it

Create an account (two seats; a mentor key gives your questions priority). A short tour shows the pages once.

| Page | What to do there |
|---|---|
| Overview | the four machines, their latest readings and JARVIS's view of each |
| Simulator | inject a fault (incubator, HVAC, animal protection, disaster) or silence a machine; reset for a fresh farm |
| Chat | talk to JARVIS by text, or hold to talk; chat closes above 1× speed so answers stay real time |
| JARVIS | each node's CTM brain and memory, and the language-model settings |
| Events & alarms | every assessment and alert, in order |

The farm is calm until you inject a fault. A trained brain takes from seconds (fire, overvoltage) to about three
farm minutes (a fan failure) to be sure: set the speed to 30× to wait less, then back to 1× to ask about it.

Faults can also be injected without the website: `POST http://127.0.0.1:8010/faults/supply_fan_failure`, and
`POST http://127.0.0.1:8010/sim/reset` for a fresh farm. The 20 fault names are `ScenarioId` in
`interface/contracts/`.

## 4. Test

```powershell
.venv\Scripts\python -m pytest            # about 1 000 tests, a few minutes; heavy model tests are in tests/DEFERRED.md
cd interface\web; ..\..\third_party\node\node-v24.22.1-win-x64\npm.cmd run build
```

The evaluation behind `docs/results.md` is in `tests/llm/eval/` (`run_all.py` writes `tests/llm/eval/report.md`).
