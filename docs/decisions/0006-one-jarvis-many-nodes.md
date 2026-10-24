# 0006: One JARVIS, many nodes

Status: accepted (owner's instruction, plan v3, 23 Oct 2026).

## Context
The project was described as "four Jarvises", one per machine. That hides the idea it exists to prove: intelligence
that grows with every machine added, not a set of separate assistants.

## Decision
- The project is **JARVIS** (Just A Rather Very Intelligent System). Each machine carries its own local AI **node**;
  together the nodes form **one JARVIS** that grows with every machine added.
- The owner always talks to one JARVIS, through **incubator-1**, the node closest to the user. The other nodes answer
  through it.
- The farm simulation is the **test bench** that proves this, not the product.
- **Hot-join** shows the idea at work: a node started while JARVIS runs announces its capabilities, and incubator-1 can
  answer about that machine within seconds, with no restart (integration test from 6A/6B, demo step in 6F).

## Alternatives
- **Keep "four Jarvises":** simpler words, but it suggests four separate assistants.
- **One central AI with remote sensors:** contradicts the per-machine local intelligence the project demonstrates.

## Consequences
- Docs, READMEs and the website say "JARVIS" for the whole and "node" for each machine's AI. The website package is `jarvis-web`.
- The capability registry (A6) must handle nodes joining at any time, not only at start-up.
