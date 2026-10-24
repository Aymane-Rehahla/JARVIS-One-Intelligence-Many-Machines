# interface/: what people see

| Folder | Step | What it is |
|---|---|---|
| `web/` | 6.0 copied, 6W live | the website (React, Vite, Tailwind), copied from `Desktop\project` |
| `gateway/` | 6A, 6W | the website's only door into the system (FastAPI on 127.0.0.1:8010) |
| `contracts/` | 6A | JSON Schemas of every message, exported from the Python models |

The website checks every message with zod against `contracts/`; a test runs the same sample messages through pydantic and zod (Q2).
