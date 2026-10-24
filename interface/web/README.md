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
