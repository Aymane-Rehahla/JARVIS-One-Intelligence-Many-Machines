# Getting started: set up, run, and try JARVIS

Windows 10 or 11, PowerShell, from the project folder. About 5 GB of disk for the models.

## 1. Set up once

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install --require-hashes -r requirements.lock
cd interface\web; npm ci; cd ..\..
```

The model weights (`models/`) and the native programs (`third_party/`: llama.cpp and the NATS server) are too large
for git. Where each one comes from, and its SHA-256, is listed in `models/README.md`, `models/MANIFEST.json` and
`third_party/README.md`. Check them with:

```powershell
.venv\Scripts\python tools\hash_models.py check
```

The 3D farm models come as a separate archive (the GitHub release); unpack it with `.venv\Scripts\python tools\farm_assets.py unpack <zip>`.

## 2. Run

```powershell
launcher\start_all.ps1 -NoMonitor     # everything, in order, each one health-checked and restarted if it falls over
launcher\stop_all.ps1                 # everything off
```

Then open <http://127.0.0.1:5173>. Every port is in `launcher/ports.toml`; all of them listen on 127.0.0.1 only.

## 3. Use it

| Page | What to do there |
|---|---|
| Overview | the four machines, their latest readings and JARVIS's view of each |
| Simulator | inject a fault (incubator, HVAC, animal protection, disaster) or silence a machine; reset for a fresh farm |
| Chat | talk to JARVIS by text, or hold to talk; chat closes above 1× speed so answers stay real time |
| JARVIS | each node's CTM brain and memory, and the language-model settings |
| Events & alarms | every assessment and alert, in order |

The farm is deliberately calm: it raises nothing until you inject a fault. A trained brain takes from a few seconds
(fire, overvoltage) to about three farm minutes (a fan failure) to be sure; speed the farm up to 30× to wait less,
then return to 1× to ask about it.

Faults can also be injected without the website, through the gateway:
`POST http://127.0.0.1:8010/faults/supply_fan_failure`, and `POST http://127.0.0.1:8010/sim/reset` for a fresh farm.
The 20 fault names are `ScenarioId` in `interface/contracts/`.

## 4. Test

```powershell
.venv\Scripts\python -m pytest            # about 1 000 tests; heavy model tests are listed in tests/DEFERRED.md
cd interface\web; npm run build
```
