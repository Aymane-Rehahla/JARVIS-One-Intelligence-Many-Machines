# third_party/: other people's programs, not in git

Each program is stored with its exact version, source URL and SHA-256 (from the publisher's release page), so it can
be fetched again and checked.

| Folder | Program | Version | Status |
|---|---|---|---|
| `llama.cpp/` | llama-server, Vulkan build for Windows | b11829 | see its README |
| `nats-server/` | NATS server for Windows | v2.15.1 | see its README |
| `node/` | Node.js for the website (portable) | v24.22.1 | SHA-256 d2046d28...95fa5 from nodejs.org |

Only the READMEs and `nats-server/nats.conf` are in git. `setup.ps1` (through `tools/download.py`) downloads all
three and checks each SHA-256.
