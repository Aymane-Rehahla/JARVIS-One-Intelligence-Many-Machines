# MIND.md: what JARVIS's mind really is (for the website)

Written 30 Oct 2026 by the senior reviewer, who has read the trained brains, their code and the JARVIS plan. **You, the
website session, cannot see the brains.** Everything you need is here and in `interface/web/public/mind/`, and every number
in that folder was produced from the real weights or from one real recorded run. Where this file says *unknown* or
*not built*, show it that way or leave it out. Never fill a gap with a plausible guess.

Never write a file path outside this repository into the website, and never write the project's old name (the website
guard in JARVIS rejects both).

## 0. What JARVIS is (the owner's words, read this first)

**The product is JARVIS** (Just A Rather Very Intelligent System). It is **not a farm tool, and the website is not "JARVIS Farm"**.

- **JARVIS is a mind that lives in machines.** Each machine carries its own local AI **node**, and together the nodes
  form **one JARVIS**. Adding a machine adds to the same JARVIS: you get *more JARVIS*, not one more isolated assistant.
  The nodes share what they know with each other, and JARVIS sees the bigger picture by connecting what several machines see.
- **The main show is the CTM (Continuous Thought Machine).** It is what lets JARVIS **think continuously**:
  - every node's brain thinks every second, 16 internal steps per second;
  - its state carries over from one second to the next, so a thought never starts from zero;
  - it notices, weighs and concludes on its own, before anyone asks.

  This is what makes JARVIS more than a chatbot.
- **Each node has three parts:**
  - the **CTM**, which thinks;
  - the **CMM**, which remembers;
  - the **language model and the Voice Box**, which talk to the owner. They talk; they never act.
  
  A safety gate outside the brain checks the evidence before any action.
- **The farm is only the proof.** The four machines (two incubators, an HVAC, an animal protection unit) run in
  **the Matrix**, a realistic physics simulation built to test JARVIS: faults happen there, and JARVIS has to catch
  them. Show the farm as *the test bench where JARVIS proves itself*, never as the product.

So the website's identity is JARVIS, with the cloud as its face and the mind (CTM, CMM, Voice Box) as its substance.
The farm pages are the live demonstration. Headlines, titles and the logo say **JARVIS**; "farm" appears only where the
page is about the test bench.

## 1. Corrections to the brief

| The brief says | What is true | Where it is proven |
|---|---|---|
| The CTM is about 0.5B parameters | **About 0.58 million** per brain: incubator 582,117 · HVAC 585,283 · animal protection 595,702. The CTM core alone is 400,402. That is roughly 1,000 times smaller than 0.5B. | `architecture.json` → `parameters` |
| 256 neurons, 16 steps, 32 history moments | Correct, for all three brains. | `architecture.json` → `config` |
| 16 computational steps | The **same 256 neurons with the same weights** run 16 times every farm second. They are not layers and not 16 networks. Adaptive thinking is **off** in the shipped brains, so every thought takes exactly 16 steps. | `internal_steps_per_tick`, `adaptive_thinking: false` |
| 32 history moments | Each neuron privately remembers its last 32 pre-activations. With 16 steps a second, that window is **the previous second's 16 steps plus this second's 16**. The export proves the window slides by exactly 16 each thought. | `memory_meaning`; `history_pre` in every thought |
| Connections between neurons | **Dense**: every neuron feeds every neuron, through one shared two-block network called the synapses. There is no sparse wiring to draw. The export gives, for each neuron, the 6 incoming influences that were strongest at the recorded moments, measured with autograd through the real synapses. Draw those, and say what they are. | `ctm/<node>.json` → `edges`, `edges_meaning` |
| Neurons have positions or layers | The model has **no geometry**. Any 2D or 3D placement is a layout choice, so say so ("positions are a layout; the links and the activity are real"). Laying neurons out by their real links (a force layout on `edges`) is honest; input, hidden and output layers are not, because no neuron is an input or an output neuron. | n/a |
| The CMM interacts with the CTM | In the shipped recipe the brain **does not read the CMM**: its memory-note inputs are blanked (`hide_notes=True`, a design choice: those notes came from the old rule brains). The CMM **records** what the brain concluded and what the machine reported. The brain's own short-term memory is its 32-step trace, carried from second to second. | `architecture.json` → `inputs.notes_hidden` |
| Voice Box | **Not built yet.** The models are chosen and downloaded, but no voice code runs today. Show it as "planned", never as working. | §5 |
| The LLM | **Built for chat (6C, 31 Oct).** Its tools, document search and number checking, and the chat agent that answers the owner in writing (every sentence checked against that turn's data before it is sent). Voice is not built yet. | §6 |
| The tab must show the effort | Effort is **not** a varying step count, because it is always 16. The real effort signals are in every thought: certainty after each step (how sure), the RMS change of the activity after each step (how much the thought is still moving), and the measured time of one thought. | `per_step.certainty`, `per_step.rms_change`, `latency_ms` |

## 2. One thought, in order (what the code really does)

Every farm second, each node's brain does this once:

1. **Readings in.** The node's encoder gives one vector per second. For the incubator brain that is 172 features: 101 numbers and 71
   on/off flags. HVAC has 167 and animal protection 188. The memory notes among them are blanked.
2. **Perception** (fixed maths plus learned statistics). Each number becomes a z-score against the learned normal,
   clipped at ±8. For each one it also adds trends over 10 s, 1 min, 10 min and 30 min, plus the time of day (sine and cosine). The result is
   `inputs.perception_width` numbers (750 for the incubator).
3. **Backbone:** two blocks of Linear, GLU and LayerNorm turn them into **96 features**. This happens once per second.
4. **The CTM core thinks 16 internal steps.** At each step:
   - **Synapses:** the 96 features and the 256 neurons' last outputs go through a shared MLP
     (352 → 512 → GLU → LayerNorm → 512 → GLU → LayerNorm). It produces one new **pre-activation** per neuron.
   - **Each neuron's private model (NLM):** every neuron has its own small MLP with 274 weights. It reads that neuron's last 32
     pre-activations and gives the neuron's **post-activation**, its output. This is what makes a CTM a CTM: neurons have their
     own timing behaviour.
5. **Synchronisation (the readout).** After the 16 steps, the brain measures how strongly pairs of neurons **224–255**
   (the last 32) fired together over the 32-step window. That gives 528 pairs (including each neuron with itself). The model
   allows a learned decay per pair, but **in all three shipped brains every decay is exactly 0**. They never moved from
   their starting value, and why is not known. So all 32 moments count equally for every pair; do not draw different
   decays. The output is these 528 numbers, not individual neurons.
6. **Heads:** five linear readouts of those 528 numbers:
   - assessment: 5 levels, NORMAL, HANDLED_BY_MACHINE, WATCHING, ESCALATE_TO_USER, DANGER;
   - diagnosis;
   - protective action;
   - communication: none, notify the owner, call the fire brigade, call civil protection;
   - source, meaning where the cause is: self, another machine, outside, or the site.
7. **Decision layer** (`decide.json`, no learning). It works like a plant alarm system:
   - prior correction;
   - a threshold;
   - an on-delay, so WATCHING and ESCALATE need several seconds in a row;
   - a hold, so a flicker stays one event.

   Only after this does the node *say* a level. "Held calls" are on: once a brain asks for the fire brigade at DANGER,
   the request stays on while it stays at DANGER.
8. **Outside the brain:** the safety gate checks the evidence before any protective action or call. The brain only
   proposes.

The recurrent state (both 32-step traces) **carries over to the next second**, so each thought starts where the last
one ended.

Each node has its own brain. incubator-1 and incubator-2 load the same incubator weights into two independent instances.

## 3. The data folder: `interface/web/public/mind/` (served at `/mind/...`)

Plain JSON with no secrets. `manifest.json` says when it was made, from which release (v3.1), the engines' SHA-256, the
episode and the list of files. **Label everything from it "Recorded from the real trained brain (release v3.1) in one
simulated farm episode, replayed step by step"**: it is real, but it is not live.

| File | What it holds |
|---|---|
| `architecture.json` | Per brain (`incubator`, `hvac`, `animal_protection`):<br>• config and parameter counts by part;<br>• the input sizes;<br>• the synapse and NLM shapes;<br>• the 32 synchronisation neurons, all 528 pairs and their learned decay rates;<br>• the label lists of the five heads;<br>• the decision layer's settings. |
| `timeline.json` | The whole episode, every second, every node: `[assessment index, confidence]` as the node stated it. Also the episode (family, variant, start hour, the fault schedule with inject and end ticks). Use it for a scrubber ("pick a second"). |
| `ctm/<node>.json` | One per node (incubator-1, incubator-2, hvac, animal-protection). Holds `edges` (`[source neuron, target neuron]`; 256 × 6), `latency_ms` (one whole thought, measured during the run on this PC, CPU, 1 thread), and `thoughts`, keyed by moment. |
| `cmm/<node>.json` | That node's CMM after the run:<br>• row counts per table;<br>• the stored episodes with every kept tick and one example of the readings;<br>• facts, hypotheses and procedures;<br>• the normal profile (hour, key, n, mean, spread);<br>• the read-only ROM entries. |

The `thoughts` moments in `ctm/<node>.json`:
- `calm`: 30 s before the first fault;
- `first_alarm`: the first second the node left NORMAL;
- `peak`: the last time its level rose.

A node that never reacted has only `calm`. That is real too: say "this node stayed calm".

**One thought** (`thoughts.<moment>`):

| Field | Shape | Meaning |
|---|---|---|
| `tick`, `farm_time_s`, `hour_of_day` | | When it happened (farm time, not wall time) |
| `decided` | | What the node said: assessment, diagnosis, confidence, action, communication, evidence |
| `assessment_probabilities` | 5 | The assessment head after the full thought (before the decision layer) |
| `per_step.assessment_probabilities` | 16 × 5 | The assessment head read after each step. The shipped brain only reads it after step 16; reading earlier steps is a real "what if it had stopped here" view. |
| `per_step.certainty` | 16 | 1 − normalised entropy of those probabilities |
| `per_step.rms_change` | 16 | How much the 256 outputs moved at that step (settling) |
| `per_step.recurrent_drive`, `per_step.sensory_drive` | 16 × 256 | How strongly each neuron's next pre-activation depends on the other neurons, and on the 96 input features (norms of the real Jacobians) |
| `per_step.edge_weight` | 16 × 1536 | The signed influence along each of `edges` at that step: + excites, − inhibits |
| `history_pre`, `history_post` | 256 × 32 | Each neuron's last 32 pre- and post-activations at the end of the thought. Columns 0–15 are the previous second's steps 1–16; columns 16–31 are this thought's steps 1–16. So `history_post[n][16 + s]` is neuron n's output at step s + 1. |
| `synchronisation` | 528 | The final readout, in the order of `architecture.json` → `synchronisation.pair_list` |
| `top_diagnoses`, `top_sources` | 3 each | The three most likely diagnoses and causes, with probabilities |
| `unusual_inputs` | ≤ 12 | The readings furthest from normal: `[feature name, z-score]` |
| `replay_max_difference` | | How far the step-by-step replay is from what the brain computed during the run (must be ≤ 1e-5) |

**The step player you asked for maps directly onto this data:**
- step k colours neuron n by `history_post[n][15 + k]`;
- it lights `edges` with `per_step.edge_weight[k-1]`;
- it shows `per_step.assessment_probabilities[k-1]` growing into the decision.

The 32-step history explorer shows `history_pre` and `history_post` as 256 × 32 strips, with the boundary between "previous second" and "this second"
marked at column 16.

### To regenerate (only the owner or the senior reviewer can; it needs the release)

```
<the training's Python> -B tools/export_mind.py --release <release folder> --upstream <upstream CTM folder> --out interface/web/public/mind
```

The two folders are given by the owner and are never written into any file. The script is read-only on both, and
it refuses to publish if its output contains a path or the old name.

## 4. The CMM (memory), from its own code

One SQLite database per node. The brain's decisions and the machine's readings are written into it every second:

- **Working memory (RAM):** open incidents, timers and call status. It resets on restart.
- **Short-term episodes:** salience-gated. Repeated normal seconds are never stored. An interesting span is kept second by
  second. A bare WATCHING must last 30 s to be stored, so blips are forgotten. Each episode keeps the cause the brain
  named and the peers that reported a problem. It closes after 300 s of quiet and is summarised.
- **Long-term facts:** verified facts with source, confidence and recurrence count, including links like "the HVAC's
  compressor failure shows up here". They are stored only when that machine's own device code verified it.
- **Hypotheses:** kept apart from facts. A hypothesis becomes a fact only by verification: a device code, the owner,
  or a repair record. Never because the brain believed it.
- **Normal profile:** what normal looks like for each hour of the day. Only quiet seconds update it (habituation). It
  lives in RAM and reaches the disk at the 02:00 sleep, so the export reads it from RAM (`profile_source`).
- **Procedures:** what was done, in what situation, and how it turned out.
- **ROM:** owner rules and device documents, read-only.
- **Sleep:** at 02:00 farm time, closed episodes become recurrence facts and old tick detail is forgotten.

The recorded episode is short and does not reach 02:00, so `cmm/*.json` shows no sleep step. The **language model never
opens the CMM** (a JARVIS ground rule). It only sees what the reasoning publishes.

## 5. Voice Box: planned, not built

None of this runs today. Only a test that the models load exists, and it has not been run in this phase. Models
chosen in the JARVIS plan (only incubator-1 speaks; the other three nodes have voice installed but switched off):

| Stage | Model | Note |
|---|---|---|
| Wake word | openWakeWord "hey jarvis" | comes after push-to-talk |
| Voice activity | Silero VAD | end of speech after 0.5–0.8 s of silence (the exact value is not measured yet) |
| Speech to text | Parakeet TDT 0.6B v2, int8, via sherpa-onnx | |
| The answer | the language model (§6) | the reply is checked number by number, then streamed |
| Text to speech | Kokoro-82M v1.0 via sherpa-onnx | sentence by sentence as the reply arrives; voice picker (British male `bm_george` default), speed 0.5–2.0 |
| Tone of voice | **cut** (decision 0016) | do not show an emotion or tone stage |

Planned states: `off`, `idle`, `wake`, `listening`, `thinking`, `speaking`, `error`. **Push-to-talk is the main
way to talk.** A DANGER alert interrupts anything being said. Audio comes in at 16 kHz. A missing microphone is shown as
"missing", never as a crash. Unknown: whether this PC has a microphone and speakers connected, and every latency figure.

## 6. The language model: what exists

- **Models:**
  - incubator-1, the node that speaks: Qwen 3.5 2B roleplay, Q8_0 GGUF;
  - incubator-2, HVAC and animal protection: Qwen3-0.6B, Q8_0;
  - all four: bge-small-en-v1.5 (33M parameters) for document search.
- **Servers:** one llama.cpp server per node (Vulkan build for the RX 580).
- **Built (step 6B):** read-only tools, document search over each node's manuals, and the number checker. The checker
  refuses any number in a reply that doesn't come from a tool or a document.
- **Built (step 6C, 31 Oct):** the chat agent on incubator-1: it asks the other nodes, writes with the 2B model, checks
  each sentence against that turn's data before sending it, states the farm time, and falls back to a template built
  from the data when the model fails. The three other nodes summarise their own answers with the 0.6B model, checked
  the same way. Alerts reach the owner from a fixed template.
- **Measured once (31 Oct, one real run):** first piece of a reply 6.1 s, whole reply 7.3 s. The full latency
  measurement (E6) and how well the 2B model picks tools (E4) are not done yet.
- **What the LLM may not do:** it talks; it never acts. No machine commands, no emergency calls, no CMM access.

## 7. The current website's brain view: what is wrong with it

`src/components/jarvis/brain-engine.ts` and `brain-view.tsx` are an **animation driven by the demo**. They are labelled
"illustration", which is honest, but they are not this CTM:

- **Too many steps per thought:** 36 instead of 16.
- **Invented roles:** input, association and output neurons. Every real neuron is the same kind.
- **"An assembly of neurons per conclusion":** false. The real heads are linear readouts of all 528 synchronisation pairs.
- **The intensity comes from fixed constants** (`brain-profile.ts`: `SEVERITY`, `DIFFICULTY`), not from the model.

Replace it with the recorded data, or keep it only as a clearly labelled illustration.

## 8. Unknown or not available

- **Live activations:** no live neuron-activity feed exists. The JARVIS gateway contracts carry assessments and
  evidence, not neurons. Before the deadline the recorded pack is the only real source. A live adapter would be new work in
  JARVIS: ask the owner.
- **The brains inside JARVIS:** they are not installed yet (step 6D). The JARVIS farm currently runs simulated rule brains.
- **Voice and chat latency:** not measured.
- **What the brain "means" by each neuron:** nobody knows, and the model has no neuron labels. Do not name neurons
  ("the temperature neuron"); show their ids (0–255) and their real numbers.

## 9. This export's run (30 Oct 2026)

- **The episode:** an ordinary library episode. Supply overvoltage, variant 1, "shared":
  - both incubators get 267.9 V from tick 539 to tick 760;
  - 1,800 farm seconds, starting at 13:17 farm time;
  - nothing happens to HVAC or animal protection.
- **Run:** 62 s on this PC.
- **Replay check:** every kept thought replayed step by step equals what the brain computed (difference 0.0), and the
  history window slid by exactly 16.
- **The release folder:** unchanged. A listing of 332 entries was identical before and after.

What happened (all from `timeline.json` and `ctm/*.json`):

| Node | Kept thoughts | What it did |
|---|---|---|
| incubator-1 | calm (509), first_alarm (541), peak (545) | Left NORMAL **2 s after the fault**, saying HANDLED_BY_MACHINE / SUPPLY_OVERVOLTAGE. Reached ESCALATE_TO_USER at 545 and notified the owner, naming the site as the cause. Back to NORMAL around tick 1064, with a few short HANDLED_BY_MACHINE blips after. In the first_alarm thought the 16 steps visibly decide: certainty rises from 0.18 to 0.37, and the head moves from NORMAL 0.40 at step 1 to ESCALATE 0.57 at step 16. |
| incubator-2 | the same ticks | The same path; its own instance, its own numbers. |
| hvac | calm only | Stayed NORMAL the whole run. Correct: the fault was not its machine. |
| animal-protection | first_alarm (13), calm (509) | **A false alert:** ESCALATE_TO_USER, WATER_SUPPLY_LOSS at tick 13, before any fault, held until tick 224. This is the release's known open issue (animal protection's false alerts on a calm farm). Show it honestly or leave the node out; never present it as a correct catch. |

Things to read carefully:
- **`decided.confidence`** is the head's top probability. The **stated** level can differ from the head's top class,
  because the decision layer waits a few seconds before ESCALATE. At tick 541 the head already said ESCALATE (0.57)
  while the node still said HANDLED_BY_MACHINE; it escalated 4 s later.
- **Calm thoughts barely change** across the 16 steps: certainty stays about 0.98. That is real: the brain is sure and
  nothing moves. The alarm thoughts are where the steps matter.
- **Speed:** one whole thought (perception, 16 steps, heads, decision) takes **about 7.5 ms** (p50; p95 about 9 ms) on this
  PC's CPU with one thread. `latency_ms` has the exact figures per node.
- **The CMMs after the run:**
  - the incubators each stored one episode (power, SUPPLY_OVERVOLTAGE, peak ESCALATE_TO_USER, the machine's own codes
    OVERVOLTAGE and OVERVOLTAGE_PROTECTION) and one procedure (notify_owner at 545 s, done);
  - animal protection stored the false alert's episode and two owner notifications;
  - HVAC stored nothing, because repeated normal seconds are never stored;
  - no facts or hypotheses were made in this short run.
