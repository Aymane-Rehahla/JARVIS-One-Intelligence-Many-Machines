# interface/web/: the website

React 19, Vite 7, Tailwind 4, shadcn/Radix and react-three-fiber, made in Bolt. It was copied from `Desktop\project` on
23 Oct 2026 without `node_modules`. **From now on the website is edited only here**; the original folder stays as a backup.

Today it runs on its own demo simulation (`src/services/matrix.ts`, `src/hooks/use-demo-farm.ts`) and says so. Step 6W
adds `src/services/live.ts` (a gateway client; every message checked with zod) and a `useFarm()` hook that chooses demo
or live, so the pages don't change.

Node isn't on PATH. Use the portable Node in `C:\Users\Aymane\node\node-v24.22.1-win-x64` (prepend it to PATH):

| Command | What it does |
|---|---|
| `npm ci` | installs the exact packages from `package-lock.json` |
| `npm run dev` | development server (port 5173) |
| `npm run typecheck` | TypeScript check (Q1) |
| `npm run build` | production build into `dist/` |

npm 11 skipped esbuild's optional install script; the typecheck and the build pass without it.

## The 3D models are not in git

`public/assets/farm/` (the incubator, HVAC, silo and chicken models, about 277 MB; `hvac.glb` alone is 120 MB) stays
on disk but is ignored by git. `assets-manifest.json` lists each file's path, size and SHA-256. **The hand-in must carry
that folder separately**: copy it back to `public/assets/farm/` and check the hashes. Without it the farm page still
works: the rooms are drawn without the machine models and a message says the models are missing.

## The mind data (`public/mind/`)

About 3.5 MB of JSON recorded from the real trained brains (release v3.1) in one simulated episode, committed. What
each file holds and how it was made: `interface/MIND.md`. The exporter is `tools/export_mind.py`; it runs only under
the Python the brains were trained with, so pyright excludes it.
