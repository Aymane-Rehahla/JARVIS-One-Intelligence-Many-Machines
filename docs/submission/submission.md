# Submission: copy-paste text and plan (deadline Sat 31 Oct, 23:59 Algiers)

Every number below was measured in this project (docs/results-phase6.md, decisions 0019 and 0023). Nothing is invented.

## 1. Project name
JARVIS: One Intelligence, Many Machines

## 2. One-line summary (20 words)
Offline AI that watches every machine on a poultry farm, spots faults, and explains them to the farmer by voice.

## Project description (141 words)
Small poultry farms depend on incubators, ventilation and water lines. If one fails at night, a flock or a hatch can be
lost within hours, and cloud monitoring fails exactly when rural internet does.

JARVIS gives every machine its own AI node. Each runs a trained Continuous Thought Machine that reads its sensors every
second and judges whether something is wrong. One calm voice speaks for all of them: ask "what's wrong with the HVAC?"
by voice or text, and JARVIS answers from live data, cites the machine's manual and checks every number before saying
it. It never switches a machine itself.

Everything runs offline on one PC with a mid-range GPU. Today the farm is a physics simulation: inject a fire or a fan
failure, and watch JARVIS notice. If the live link is down, the video shows it working.

## 3. Live demo link
The Cloudflare tunnel URL. It is not set up yet; see "The live link" below.

## 4. Code repository
The tracked repo is only ~12 MB. The 5 GB on disk is `models/` (3.7 GB) and `third_party/` (118 MB), which git
already ignores. So: `git push`, set the repo to Public, then open it in a private window. The 3D models are in
`JARVIS-farm-assets.zip`; attach it to a GitHub Release.

## 5. Demo video
YouTube, Unlisted. Script below.

## 6. Team members and roles
Aymane Rehahla: Solo builder. Designed the four-node architecture, trained the CTM brains on the farm simulation, and
built the agent, voice pipeline, gateway and website, with Claude Code as coding assistant.

## 7. Tech stack
Python, PyTorch, Continuous Thought Machine (Sakana AI architecture), llama.cpp, Qwen 3.5 2B, Qwen3-0.6B, NVIDIA
Parakeet TDT 0.6B, Silero VAD, Kokoro TTS, sherpa-onnx, ONNX Runtime, bge-small-en-v1.5, SQLite FTS5, NATS, FastAPI,
Pydantic, TypeScript, React, Vite, Tailwind CSS, zod, Three.js, React Three Fiber, Cloudflare Tunnel, Claude Code

## 8. What happens next (94 words)
Move from simulation to a real farm: fit sensors to one incubator and one ventilation unit at a local poultry farm, and
run each node on its own small PC. Retrain the CTM brains on that real data and fix the misses we measured, such as a
slow humidity drift that goes unnoticed. Cut spoken replies from about 8 seconds to under 3 by answering status
questions straight from the data. Add Darija and French speech. Package a one-click installer and test it every week
with farmers. We will keep building after the programme.

## Earlier work
The Continuous Thought Machine is Sakana AI's architecture (Darlow et al., 2025, open source). Its neurons keep a short
memory of their own activity and reason over internal "thought" steps. The team demonstrated it on image
classification, maze solving and sorting, and showed that the idea works. We asked whether it could do a real job. We
turned it into a live monitoring brain: one CTM per machine type, trained on our farm simulation. Each one reads
sensors every second, keeps a memory of incidents, and proposes actions that a separate safety gate must approve.
[FILL IN: what existed before the programme, for example "the farm simulation and the first CTM training runs".]
These pretrained models are used as published: Qwen 3.5 2B, Qwen3-0.6B, Parakeet TDT 0.6B, Kokoro, Silero VAD and
bge-small-en-v1.5.

---

## Video script (3:00)
Record with OBS (Display Capture plus mic) at 1080p, with the browser zoomed to 125–150 %. Trim in Clipchamp, which is
built into Windows. Each spoken answer takes about 8 s: cut the wait and add a small caption, "8 s wait trimmed".
Don't hide the wait.

| Time | On screen | You say |
|---|---|---|
| 0:00–0:20 | Chat page. Hold the talk button: "JARVIS, how are the incubators?" It answers. | Nothing. Let it speak. |
| 0:20–0:45 | Overview, the 3D farm. | "A stuck heater or a dead fan can cost a farm a whole hatch overnight. Cloud monitoring fails when rural internet does. JARVIS runs on one PC, offline." |
| 0:45–1:25 | Simulator → Disaster → Fire → Inject. Wait for the spoken alert. Ask "What should I do?" | "I start a fire in the simulated barn. Nobody tells JARVIS. Its brains have to notice it." |
| 1:25–1:50 | Reset. Inject HVAC → Supply fan. Ask "What's wrong with the HVAC?" | "Every number it says is checked against the live data first." |
| 1:50–2:10 | Speed 60×: the chat closes, and the Events page keeps filling. | "Sped up, it stops chatting, but every machine's brain keeps watching." |
| 2:10–2:35 | Turn Wi-Fi off on camera. Ask a question; it answers. Show Task Manager → GPU. | "No internet. It still answers, from this GPU." |
| 2:35–2:55 | JARVIS page: the cloud, then the LLM panel. | "Four machines, four brains, one voice. It talks; it never touches a machine." |
| 2:55–3:00 | The URL on screen. | "Try it at …" |

Don't use Marvel or Iron Man clips, music or images: YouTube can block the video. Upload it as Unlisted, then check it
in a private window and on your phone's data.

## The live link (the tie-break: a working product)
- **The problem:** the page calls the gateway at `127.0.0.1:8010`, so a judge opening a tunnel URL can't reach JARVIS.
  The fix is to serve the site and the gateway from one address: a proxy, the gateway's `--origin`, and one tunnel.
  Claude can do this; ask.
- **A stable address:** a quick tunnel (`cloudflared tunnel --url …`) gets a new URL after every reboot. A named tunnel
  on a domain you own (`cloudflared service install <token>`) keeps its URL.
- **Recovering from power cuts:** set the BIOS to "Restore on AC Power Loss = On". Turn on auto-login (`netplwiz`). Add
  a Task Scheduler entry, "At log on", that runs `launcher\start_all.ps1 -NoMonitor`. Turn sleep off, and pause
  Windows Update.
- **Seats:** only 2 people can use JARVIS at a time, so a third judge sees "at capacity". Say so in the description, or
  raise `SEATS` for judging week.
- **An uptime alert:** an UptimeRobot monitor on `https://<your-url>/health`.

## Order for tonight
1. Save a draft now with sections 1, 2, the description, 6 and 7.
2. Push the repo and make it Public.
3. Set up the tunnel.
4. Record and upload the video by 20:45.
5. Make the deck and add "What happens next".
6. Final submit by 21:30. Press Save after each field.
