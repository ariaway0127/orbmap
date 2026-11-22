# Templates and scenario packs

orbmap separates three things:

| Part | What it is | Where |
| --- | --- | --- |
| **Engine** | Layout, bots, review gate, main versions, persistence | most of `index.html` |
| **Templates** | How the work under a task is organized (steps, approaches, …) | `TEMPLATES` |
| **Scenario pack** | The content: sub-goal splits, step lists, approaches, metrics, briefs, example map | `PACKS.general` |

Everything lives in `index.html`; search for the names above.

## Templates

A container (a task, a step or an approach) has a `mode`. The mode is a key in `TEMPLATES`:

```js
const TEMPLATES = {
  steps: {
    name: 'Steps', icon: '☰', kid: 'step', one: 'step', many: 'steps',
    layout: 'column',                      // children stacked, run in order
    addLabel: '+ Step',
    blurb: 'One step at a time; …',
    plan: (c, by) => planSteps(c.id, by),  // create the first children
    add: (c) => appendStep(c.id),          // the side panel's "+ …" button
    run: (c) => runPlan(c),                // run the container
    done: (c) => visibleKids(c).every(k => k.status === 'done')
  },
  approach: {
    name: 'Approaches', icon: '⑂', kid: 'approach', layout: 'row', …,
    plan: (c, by) => planApproaches(c.id, by), run: (c) => runBranches(c), …
  }
};
```

| Field | Meaning |
| --- | --- |
| `layout` | `'column'` stacks children top to bottom and draws one frame around them; `'row'` puts them side by side |
| `kid` | tier of the child cards (`'step'`, `'approach'`, or a new tier) |
| `icon`, `one`, `many`, `name`, `blurb` | labels used on cards and in the side panel |
| `plan(c, by)` | creates the first children when someone picks this template |
| `add(c)` | adds one more child |
| `run(c)` | an `async` function that does the work; use `sleep()` (it stops by itself after a reset) and `setStatus(bot, …)` |
| `done(c)` | true when nothing is left |

The engine already reads `layout` (layout, frames, arrows) and `run` (`runContainer`) from the registry.

### Adding a template

Example: a **checklist**, where items can be ticked off in any order.

1. Add an entry to `TEMPLATES`:

   ```js
   checklist: {
     name: 'Checklist', icon: '☑', kid: 'step', one: 'item', many: 'items', layout: 'column', addLabel: '+ Item',
     blurb: 'Items in any order; the task is done when every item is ticked.',
     plan: (c, by) => { c.mode = 'checklist'; addSteps(c, ['Item 1', 'Item 2', 'Item 3'], by); c.mode = 'checklist'; },
     add: (c) => appendStep(c.id),
     run: async (c) => { for (const k of visibleKids(c)) { k.status = 'done'; refreshCard(k.id); await sleep(400); } c.done = true; refreshUp(c.id); },
     done: (c) => visibleKids(c).every(k => k.status === 'done')
   }
   ```

2. Offer it when a task has no workflow yet. The choice buttons are built in `cardBody` and `cardViewHtml`; the chat shortcuts in the `chips` list next to them.
3. A few places still branch on the two built-in modes (`n.mode === 'steps'` / `'approach'`): the card body, the side panel actions, the card status pill and the chat commands in `handleChat`. Search for `mode === '` and decide for each one whether your template should behave like `steps`, like `approach`, or differently. Moving these into the registry is a good first contribution.
4. Add a check to `tests/test_ui.py` that plans and runs your template.

### Built-in task templates

| key | layout | kids | done when |
| --- | --- | --- | --- |
| `steps` | column, arrows | steps in order | every step is done |
| `approach` | row | approaches in parallel | you finish the task after adopting |
| `checklist` | column, no arrows | items in any order | every item is ticked or verified |
| `loop` | column, arrows | one step per round | a round's score meets `loop.goal` (`loop.better` is `higher` or `lower`), or `loop.max` rounds |
| `fanout` | split: map on top, parts in a row, reduce below | items with `role` `map` / `part` / `reduce` | the reduce step is done |
| `branch` | tree: decide on top, branches in a row | `role` `decide` / `branch` (each branch has `when`) | the chosen branch (`chosen: true`) is done; the others are `skipped` |
| `matrix` | grid: one row per row value | `role` `cell` with `cell: {r, c}`; settings in `matrix: {rows, cols, metric, better}` | every cell is done |
| `stagegate` | column, arrows | `role` `stage` / `gate` alternating; a gate has `gate: {kind: 'human' \| 'check', criteria}` | every stage and gate is done |

Layouts `split`, `tree` and `grid` are drawn as bands of cards side by side (`bandsOf` in `index.html`); a new template can reuse them by returning its own bands.

## Result templates

A step's `contract` says how its result will be shown; `resultData` holds the result. Both have a `type`, a key in `RESULT_TEMPLATES`. Each template has `wireframe(contract, full)` (drawn before the step runs) and `render(contract, data, full)` (drawn when it is done):

| type | contract (before) | data (after) |
| --- | --- | --- |
| `table` | `columns`, `rows` (row labels) or `row_count` | `columns`, `rows` (arrays of values) |
| `metric` | `metric`, `unit`, `target` | `value`, `unit`, `delta`, `good` |
| `audit` | `items` | `items: [{name, ok, note}]` |
| `diff` | `file`, `note` | `file`, `lines: [{op: '+' \| '-' \| ' ', text}]` |
| `parity` | `tolerance_pct`, `x_label`, `y_label`, `target` | `points: [{x, y, label}]`, `r2` |
| `distribution` | `metric`, `lsl`, `usl` | `groups: [{name, min, q1, median, q3, max}]` |
| `heatmap` | `zones` | `cells: [{name, value}]`, `unit` |
| `compare` | `before`, `after` (labels) | `before`, `after` (image paths in the workspace, or short states) |
| `text` | — | `text` |

Every result also has a one-line `badge`. In live mode bots set the contract when they plan (`plan_steps` → `steps[].result`) and fill the data when they finish a step (`run_step` → `result`); `set_result` changes a contract.

To add a result template, add an entry with `label`, `hint`, `wireframe` and `render`, and add its type to `RESULT_TYPES` in `app/prompts.py` so bots can use it.

## Scenario packs

A pack holds the content the scripted bots use. The engine only reads it through `PACK`:

```js
PACKS.general = {
  name: 'Launch a new product',
  topicRe: /launch|product|…/i,       // goals matching this use decomp.launch, others decomp.generic
  decomp: { launch: [[…], […]], generic: [[…], […]] },   // two alternative splits each
  metrics: { onboard: { name, unit, value, target, dp, ref, model }, … },
  taskTpl: { 'Sub-goal title': [{ t, mode, steps | approach, metric, code, autoNewApproach }] },
  stepSets: { research: [{ t, auto, insert, ask, methods }], … },
  stepOut: [[/regex on step title/, { kind: 'chart' | 'table' | 'todo', label, verify, … }], …],
  stepOutDefault: { … },
  subsForStep: [[/regex/, ['subagent task', …]], …],
  approachSets: { onboard: [{ t, params, outcome, value, reason, lesson, next, auto, subplan, refutes }], … },
  questions: { Q1: { title, background, problem, impact, options: [{ k, label, detail, rec }] }, … },
  restate(goal, key) { … },           // MC's "in my own words" text
  plain: [[/jargon/g, 'plain words'], …]
};
```

Useful scripted behaviors:

| In a step | Effect |
| --- | --- |
| `ask: 'Q1'` | the bot stops at this step and pins brief `Q1` |
| `auto: 'insert', insert: [...]` | after this step the bot inserts more steps on its own |
| `auto: 'compare', methods: [...], pick: i` | the step splits into two methods, then merges into one |

| In an approach | Effect |
| --- | --- |
| `outcome: 'pass' \| 'fail'`, `value` | the validation result against the task metric |
| `auto: 'subplan', subplan: [...]` | the bot opens a sub-plan before validating |
| `refutes: 'Title'` | when it runs, it shows that an adopted approach with this title was wrong, and asks you whether to retract it |
| `drift: true` | scores lower on stability in Arena |

`taskTpl` keys are sub-goal titles, so they must match the titles in `decomp`. Steps with `code: true` (or any approach task) work on main in a worktree and go through the review gate's merge; the others are read-only.

To switch packs, change `const PACK = PACKS.general;`. In phase B the pack becomes the prompt material for real sessions instead of a script.
