# JARVIS: pitch deck (8 slides)

Brief for the slide maker: 16:9, one idea per slide, large type, black/white with one orange accent (#ff5a1f).
Screenshots come from the live site (http://127.0.0.1:5173). No Iron Man or Marvel images, music or logos.

---

## 1. The problem
**One failed machine at night can cost a farm a whole hatch.**
- Incubators, ventilation and water lines keep birds and eggs alive around the clock.
- A stuck heater or a dead fan does its damage within hours, often while the farmer sleeps.
- Cloud monitoring needs steady internet, and rural internet drops exactly when the power does.

## 2. Who has it
**Small and mid-size poultry farms far from reliable internet.**
- [Survey: "We asked N farmers / owners; M said …". Use only real answers. Delete this line if there are none.]
- They need two things: to be told early, and to be able to ask "what's wrong?" in plain words.

## 3. The product
**One intelligence, many machines.**
- Every machine gets its own AI node, which reads its sensors every second and judges whether something is wrong.
- One calm voice speaks for all of them, by voice or by text.
- Runs offline on one PC with a mid-range GPU.
- Screenshot: the Overview page, with the 3D farm and the four machines.

## 4. Demo
**Inject a fire and watch JARVIS notice.**
- Still 1: Simulator → Disaster → Fire.
- Still 2: the spoken alert banner, "Sir, danger at …".
- Still 3: the chat answering "What should I do?" with a cited manual.
- Still 4: the Wi-Fi switched off, and JARVIS still answering.

## 5. How it works
- **Each node's brain:** a Continuous Thought Machine trained on a physics simulation of the farm. It proposes an action, and a separate safety gate must approve it.
- **The talking side:**
  - an agent with typed, read-only tools;
  - hybrid search over approved manuals (keywords plus vectors);
  - every number checked before it is said or shown.
- **The models:** small local language models (Qwen 3.5 2B and Qwen3-0.6B on llama.cpp); Parakeet for listening and Kokoro for speaking.
- **The design rule:** JARVIS talks; it never switches a machine.
- Diagram: 4 nodes → NATS → incubator-1 (the one voice) → gateway → website.

## 6. What works today vs. in progress
| Works (measured) | In progress |
|---|---|
| 46 of 46 tool calls valid | Voice reply ~8.5 s (target 2.5 s) |
| 44 of 44 numbers grounded after checking | Manual search Recall@4 0.891 (target 0.9) |
| 0 unsafe actions under attack prompts | Routing questions to the right node 88 % (target 95 %) |
| 12 of 12 graceful answers with the models failing | A slow humidity drift goes unnoticed |
| Alerts spoken in 1.44 s | Real sensors (today: simulation) |

## 7. Next three months
- Real sensors on one incubator and one ventilation unit at a local farm; one small PC per node.
- Retrain the brains on real data; fix the measured misses.
- Spoken replies under 3 s; Darija and French speech; a one-click installer.
- Weekly tests with farmers.

## 8. Team
**Aymane Rehahla, solo builder.**
- Designed the four-node architecture and trained the CTM brains.
- Built the agent, the voice pipeline, the gateway and the website, with Claude Code as coding assistant.
- Built on Sakana AI's Continuous Thought Machine research.
