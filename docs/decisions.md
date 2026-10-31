# Decisions

The big choices behind JARVIS, one line each. Code comments cite them by number ("decision 0018").

## How JARVIS is built

| # | Decision | Why |
|---|---|---|
| 0006 | One JARVIS, many nodes: each machine carries its own AI node, and the nodes form one JARVIS | a farm grows machine by machine; the system grows with it |
| 0007 | The four nodes live in `nodes/`, one complete folder each, with identical shared code | each node can be moved to its own PC later |
| 0001 | NATS carries every message between programs; node-to-node messages follow simulated radio (ZigBee) rules | one bus, local only, and the radio's limits are part of the test |
| 0013 | One llama.cpp server per node: Qwen 3.5 2B for incubator-1 (the voice), Qwen3-0.6B for the others (replaces 0002) | each node stands alone, as it would on its own machine |
| 0004 | The website reaches the system only through the gateway | one guarded door: accounts, throttled snapshots, a fixed list of actions |
| 0014 | Tools a model can reach are read-only and local to its node, all audited | a model can look, never touch |
| 0015 | The trained CTM brains are installed inside JARVIS, re-saved as plain weights, proven equal to the training release | no dependency on the training folder; nothing executable in the weight files |
| 0021 | The live farm runs the trained brains on an ordinary simulated day, one thread, a fresh memory per run | a fair, repeatable test bench |
| 0017 | The language models keep an f16 key-value cache | measured: the q8_0 cache freed no memory and was 21 % slower |

## Safety and honesty

| # | Decision | Why |
|---|---|---|
| 0003 | The language model talks; it never acts. Machine actions come only from the reasoning, through a safety gate | a model that can be talked into things must not have the power to do them |
| 0008 | Knowledge documents are searched only after the owner approves them; any change sends them back to draft | JARVIS cites only what the owner signed off |
| 0018 | The agent: refusals and routing in code, every sentence checked against the turn's data before it is sent, a template answer from the data when the model fails | answers stay true even when the model is slow, down or wrong |
| 0019 | Proactive alerts travel over the radio and are spoken from a fixed template, not by the model | an alert must be fast and exact |
| 0010 | The project's old name is removed everywhere, history included | one project, one name |

## The demo

| # | Decision | Why |
|---|---|---|
| 0020 | Injected faults come from the farm's own scenario library, one per farm run | real, varied faults; nothing hand-made for the demo |
| 0023 | The farm stays calm until a user injects a fault; chat is real time (closed above 1× speed); 20 faults in 4 families plus a silent machine | the owner hears only about what was broken, and can always talk to JARVIS |
| 0022 | Voice is push-to-talk; a 0.6 s pause ends the question; sentences are spoken as they are checked | reliable in a noisy room, and the first words come sooner |
| 0016 | The voice's emotion detector (emotion2vec+) is cut | the model was 1.12 GB, not the expected 0.35 GB, and needed heavy extra packages; the words alone decide the tone |
| 0009 | A search over prompts, examples and sampling chose the talking model's persona | measured, not guessed; the persona was later rewritten as a warm engineer friend |

## Build-time only

| # | Decision |
|---|---|
| 0005 | Models stored once with hard links, caches on a second drive |
| 0011 | A RAM guard limited what ran while the brains were training |
| 0012 | Early tests ran rule brains before the trained ones were ready |
