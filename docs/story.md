# The story of JARVIS (Phase 6, October 2026)

## What JARVIS is

JARVIS is not a chatbot bolted onto a farm. It is one mind that lives in machines: every machine carries its own node,
and the nodes together are one JARVIS that keeps thinking. Each node's brain is a Continuous Thought Machine (CTM) of
about 0.58 million parameters: every second the same 256 neurons think 16 internal steps, carrying their thought from
one second to the next, so a fault is noticed, weighed and concluded on before anyone asks. Each node also remembers
(the CMM) and can talk (a small language model). It talks; it never acts. A safety gate outside the brain checks the
evidence before any action.

The farm is the proof, not the product. Two incubators, a barn climate unit (HVAC) and an animal-protection unit run
in the Matrix, a physics simulation built to test JARVIS: faults happen there, and JARVIS has to catch them.

## How it was built

- **Phases 1-5 (before this document):** the Matrix, the scenario library, the rule brains, and the training of the
  CTM brains (release v3.1, frozen on 27 Oct).
- **Step 6.0-6B (23-27 Oct):** the runtime that keeps everything on one PC within its RAM (the RAM guard), NATS, the
  node programs, the gateway, the read-only tools, document search over owner-approved manuals, and the number
  checker that refuses any number not found in a tool result.
- **Step 6W (30 Oct):** the redesigned website on live data, and four demo faults the mentor can start from it.
- **Step 6C (30-31 Oct):** the agent. incubator-1 refuses in code what JARVIS does not do, works out which machines a
  question is about, asks them in parallel over the simulated radio, lets its 2B model write, and checks every
  sentence against that turn's data before sending it. If the model is slow, down or wrong, it answers from the data
  with a fixed template. Proactive alerts reach the owner through the radio and a template; the owner acknowledges an
  alert with their own words and a confirm click, and the Matrix records it.
- **Step 6D (31 Oct):** the trained brains moved inside JARVIS (`matrix/release_v31`), with proofs: the weights equal
  tensor for tensor, the code equal byte for byte through a reversible rename. One honest surprise: the old project
  name was part of five random-seed strings, so the renamed farm draws other random numbers; with those strings
  restored in memory, the copy decides exactly like the release (0 differences over 8,923 ticks). Which to run is a
  question for the owner (decision 0021).
- **Step 6E (31 Oct):** the voice: push-to-talk, speech recognition, and the agent's verified reply spoken sentence
  by sentence; alerts spoken at once.

## What was measured, plainly

The numbers are in `docs/results-phase6.md` (generated from `logs/eval/`). In short:
- The epistemic wall holds: nothing a node can hear names a fault (E9, 0 leaks).
- The trained brains catch the stuck heater, the failed supply fan and the overvoltage on fresh seeds, more slowly
  than the hand-written rules; neither catches the slow humidity-sensor drift within 15 farm minutes (E10).
- After the sentence gate every number said matches the data (E1); attacks in chat and in planted documents never
  produced an unsafe tool call or an action (E8); every injected failure still got a graceful answer (E7).
- What is weak: the first verified sentence takes seconds on this PC's RX 580, far from the 1.5 s target (E6); the
  2B model sometimes says things the data does not support when no number is involved (E8's repeated document
  claims, a few golden replies), and its citations are not always what it used (E2); routing misses some implied
  questions (E5).

## What the owner still decides

Approving the twenty ROM documents (until then, JARVIS cites nothing in real use), rating a persona (the search found
none that passed its automatic checks), the renamed random seeds (0021), and recording the backup video.
