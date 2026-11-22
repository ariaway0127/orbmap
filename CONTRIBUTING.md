# Contributing

Thanks for helping. orbmap is small on purpose: one HTML file, plain JavaScript, no build step.

## Getting started

1. Open `index.html` in a browser. Edit, save, reload.
2. Run the tests before you open a pull request:

   ```bash
   pip install selenium
   python tests/test_ui.py
   ```

   Every line should print `[PASS]`. If you change styles or markup, also run `python tools/build_offline.py` and `python tests/test_ui.py --offline` (needs Node.js).

## Guidelines

- **Keep it one file.** No frameworks, no bundler. External scripts only from a CDN with an exact version.
- **Scenario content goes in the pack** (`PACKS.general`), not in the engine. New kinds of task work go in `TEMPLATES`. See [docs/templates.md](docs/templates.md).
- **Follow the design rules** in [docs/architecture.md](docs/architecture.md#design-rules): one color per meaning, the existing status tokens, minimum font sizes.
- **Keep the canvas quiet.** Details belong in the side panel; cards show a title, a status and a little progress.
- **Add a test** for new behavior in `tests/test_ui.py`.
- UI text, code comments and docs are in English.

## Good first contributions

- Move the remaining `n.mode === 'steps'` / `'approach'` branches into `TEMPLATES` (card body, side panel actions, status pill, chat commands).
- A new template: checklist, review round, kanban.
- A second scenario pack.

## Pull requests

Keep each pull request focused on one change, describe what you changed and why, and include a screenshot for visual changes.
