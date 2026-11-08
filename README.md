# JARVIS: one intelligence, many machines

JARVIS is a local AI system that looks after machines. Every machine gets its own small AI "node"; together the nodes
act as **one JARVIS** that you can talk to by text or voice. Everything runs offline on one ordinary PC.

To give JARVIS a real job, it watches a **simulated poultry farm** (the *Matrix*): two egg incubators, the barn's
climate control (HVAC) and an animal-protection unit (water and feed). You can break the farm on purpose, a stuck
heater, a dead fan, a barn fire, and watch JARVIS notice by itself, warn you, and explain what went wrong.

> The farm is a physics simulation, not a real farm. All results below are measured in that simulation.

## What happens, in one minute

1. Each machine's node runs a trained **Continuous Thought Machine** (CTM, a neural network design from Sakana AI)
   that reads the machine's sensors every second and decides: *normal*, *watching*, *tell the owner*, or *danger*.
2. Each node keeps a **memory** (the CMM) of what it concluded: incidents, what normal looks like.
3. The nodes talk to each other over a message bus, so a question about any machine can be answered by one voice.
4. A **language model** (Qwen, running locally on llama.cpp) turns the data into a short spoken or written answer.
   Every number it says is checked against the real readings before it reaches you.
5. **Voice:** speech-to-text (Parakeet) in, text-to-speech (Kokoro) out.
6. **Safety:** the language model only talks. It can never switch a machine on or off.

JARVIS talks like a warm, experienced engineer friend: relaxed when all is well, short and serious when something
is wrong.

## Try it (Windows)

You need Windows 10 or 11, about 8 GB of free disk, and ideally a GPU with Vulkan drivers and 8 GB of memory.

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1                        # once: packages, models (3.8 GB), programs
powershell -ExecutionPolicy Bypass -File .\launcher\start_all.ps1 -NoMonitor  # starts everything, opens the website
```

Setup can be run again at any time: it resumes and skips what is already in place.

Open <http://127.0.0.1:5173>, create an account (two people can use JARVIS at once; a mentor key gives priority),
and follow the short tour that appears on first sign-in. Then:

- **Simulator** page: pick a fault and inject it. Nobody tells JARVIS; it has to notice.
- **Chat** page: ask "what's wrong with the HVAC?", or just say hello. Hold to talk for voice.
- **JARVIS** page: watch each machine's brain think, and see which language models do the talking.
- **Events & alarms**: the timeline of every assessment and alert.

`launcher\stop_all.ps1` stops everything. More detail, and how to add the 3D farm models, in [docs/getting-started.md](docs/getting-started.md).

## What was measured

| Result | Value |
|---|---|
| Faults caught by the trained brains, on simulation seeds never used in training | 15 of 20 |
| Numbers in answers that matched the live data | 44 of 44 |
| Unsafe actions under attack prompts | 0 |
| Time from a fault alert to JARVIS speaking it | 1.44 s |
| Not solved yet | a slow humidity-sensor drift (0 of 5, rules miss it too); spoken replies take about 8 s |

Every check, target and number is in [docs/results.md](docs/results.md).

## Built with

Python, PyTorch, the Continuous Thought Machine (Sakana AI), llama.cpp with Qwen 3.5 2B and Qwen3-0.6B, NVIDIA
Parakeet TDT 0.6B, Silero VAD, Kokoro, sherpa-onnx, ONNX Runtime with bge-small-en-v1.5, SQLite FTS5, NATS, FastAPI,
Pydantic, TypeScript, React, Vite, Tailwind CSS, zod, Three.js and React Three Fiber.

Hardware it was built on: AMD Ryzen 5 5600G, 16 GB RAM, AMD Radeon RX 580 (8 GB).

## Where things are

| Folder | What is in it |
|---|---|
| `nodes/` | the four machine nodes (identical code, one folder each): brain, memory, tools, language model, voice |
| `matrix/` | the simulated farm and the trained CTM brains |
| `interface/` | the website, the gateway (the website's only door into the system) and the message schemas |
| `launcher/` | start and stop everything; every port is in `ports.toml` |
| `tools/` | model checks, knowledge approval, small maintenance scripts |
| `tests/` | `pytest` runs every test |
| `docs/` | the details: how it works, results, decisions, setup |
| `_dev/` | the farm code the brains were trained with, kept read-only for reference |

## Learn more

- [docs/getting-started.md](docs/getting-started.md): set up, run, use and test it
- [docs/how-it-works.md](docs/how-it-works.md): the parts, the programs, how a fault becomes an alert
- [docs/results.md](docs/results.md): what was measured, what met its target and what did not
- [docs/decisions.md](docs/decisions.md): the big choices, one line each

Built by Aymane Rehahla.

##Note: due to a power outage i missed the deadline by few minutes, if you are curious to try it, the video i GitHub has the missing test video and here is the demo link: 
the video : https://drive.google.com/file/d/1CJqo7Z9tVlQ80B3htKrrFPWZJKh5vgV0/view?usp=sharing
old link expired because of a machine restart
the demo : https://rays-followed-chapel-liverpool.trycloudflare.com/
