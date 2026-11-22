# Architecture

orbmap is one HTML file with plain JavaScript and Tailwind CSS. There is no framework and no build step. This page is a map of that file.

## Files

```
index.html              the whole app: markup, styles, scenario pack, engine, UI
tests/test_ui.py        end-to-end tests (Selenium, headless Chrome)
tools/build_offline.py  inlines Tailwind so the app works without internet → dist/index.offline.html
docs/                   this guide, templates.md, screenshots
```

## Inside index.html

In order:

1. **Styles**: design tokens on `:root` (status colors, elevation shadows), card tiers, zoom levels, bot animations.
2. **Markup**: status bar, top bar (bot pills), canvas layers (edges, cards, bots, trails), side panel, minimap, overlays (search, report, away mode, local setup).
3. **Scenario pack** (`PACKS.general`) and **templates** (`TEMPLATES`). See [templates.md](templates.md).
4. **State builders**: `buildBlankState`, `buildExampleState`, and pure data helpers (`addTask`, `addSteps`, `addApproach`, `makeBot`…).
5. **Layout**: `measure` / `place` walk the tree recursively. Rows sit side by side, columns stack. Manual drags are stored as offsets.
6. **Rendering**: cards (`cardBody`, `applyChrome`), edges (`renderEdges`, step frames), bots (`renderBots`, cloud bodies via `blobPath`), the side panel (`renderDrawer`).
7. **Execution engine**: `runPlan` (steps), `runBranches` / `runApproach` (approaches), questions (`askUser` / `answerQuestion`), workers (`workerJob`, `runJob`), the review gate (`requestMerge`, `reviewFlow`), main versions (`bumpMain`, `broadcastMain`).
8. **Working methods**: playbooks, Arena, interrogation, blast radius, decision log, catch-up, pause and resume, attacking the premise, automatic handoffs.
9. **Input**: chat intent rules (`NEG_RE`, `ASK_RE`, `DO_RE`, `YES_RE`, the per-card command rules in `handleChat`), confirm cards, search, undo, keyboard and pointer events.

## State

Everything is in one object, `state`, saved to `localStorage` (`agent-atlas-state-v1`) after every change:

| Key | Contents |
| --- | --- |
| `nodes` | every card by id: `GOAL`, `SG-n`, `T-n`, steps `T-n.k`, approaches `A-nx`, plus the review gate `QA` and the workers `W-CLOUD` / `W-BATCH` (stations, not drawn as cards) |
| `bots` | sessions: `id`, `homeId`, `color`, `status`, `dockedNodeId`, `ctx` (context %), `question`, `handoffs`, `subs` (subagents) |
| `main` | version, hash and log of the shared result |
| `mc` | MC's chat, policies, reports and session |
| `guardrails`, `decisions` | automatic checks; the append-only decision log |
| `usage`, `camera`, `seq` | simulated usage, view, id counters |

**Local setup → Download** exports the state as `project_spatial_state.json`, the format a back end would read.

## Flows and resets

Flows are `async` functions that wait with `sleep(ms)`. Every reset (example, new goal, import) bumps `state.epoch`, and `sleep` never resolves for an old epoch, so old flows simply stop.

A container being run is in the `running` set, which guards against running it twice. When an approach opens a sub-plan, it steps out of `running` so the sub-plan can run.

## Bots on the canvas

- Task bots dock on the card being worked on. The flight follows an arc, and the cloud body stretches along the flight.
- Subagents (`mini` clones) and worker or review clones (`fieldAgent`) fly to their own cards and are recalled with `fieldDone`. The review gate handles one merge at a time (`qaTurn` / `qaRelease`); the others queue next to their cards.
- Bots outside the view are not drawn, and looping animations pause.

## Design rules

- One color, one meaning: `--ask` yellow = waiting for you, `--fail` red = failed, `--run` / `--accent` blue = progress and primary actions, `--ok` green = passed. A bot's identity color is used only on the bot and its name tag.
- Elevation shows the level: steps lowest, goal highest.
- Semantic zoom: near shows everything, mid keeps title and status, far keeps a large title and the status color.
- Single-color line icons (`ICO`) in the side panel, minimum font size 10.5px on cards and 11px in the panel, buttons at least 28px tall.

## Phase B (planned)

```
pywebview window ── index.html (same UI)
        │  state JSON
Python dispatcher ── one `claude -p` session per bot (--session-id / --resume)
        │            MC is the only session that sees the whole tree
        └── workers: PowerShell / Python jobs on exp/<id> branches, results as JSON
```

The **Local setup** overlay in the app shows a sketch of each piece.
