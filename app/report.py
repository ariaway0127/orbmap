"""Work on the orbmap map from a session you continue in a terminal.

The copy at <workspace>/.atlas/report.py knows its workspace. From the app folder, put the workspace first:
python app/report.py <workspace> ...

  report.py [--from T-3] <card> <running|done|blocked|passed|failed|note> ["verdict"] ["summary"]
      progress of a card; the verdict is what the card shows (keywords, at most 14 CJK or 28 Latin characters)
  report.py [--from T-3] approach <card> "<title>" ["<card label: keywords>"] ["<idea>"]
      a new approach (an alternative or a hypothesis to test) under a task, a step or an approach
  report.py [--from T-3] step <card> "<title>" ["<detail>"]
      a new step: after <card> if it is a step, at the end if it is a task
  report.py [--from T-3] task <sub-goal> "<title>" ["<detail>"]
      a new task under a sub-goal
  report.py [--from T-3] ask <MC | bot id | card id | session title> "<question>"
  report.py [--from T-3] tell <MC | bot id | card id | session title> "<note>"
      talk to another session; answers to your questions come back with `replies`
  report.py join ["<a short title for this session>"]
      run INSIDE a Claude Code session (VS Code or terminal): it finds that session and asks orbmap to add it to the map
  report.py --from T-3 context
      the map as you see it in the orbmap chat (goal, tree, policies, recent reports)
  report.py --from T-3 replies
      print the answers that came back to you (and mark them read)
  report.py [--from T-3] <adopt|run|validate|retract|fold|unfold|finish|approve|sendback|tick|round> <card>
      press that button on the card, exactly as in orbmap (adopt an approach, run a task or step, finish a task, approve a gate…)
  report.py [--from T-3] accept proposal          accept MC's pending split into sub-goals
  report.py [--from T-3] answer <bot> <A|B|C|D>   answer the question a bot is waiting on
  report.py [--from T-3] <pause|resume> all       pause or resume every bot
  report.py [--from T-3] <skip|hold|resume> <step>  skip a step (not needed), hold it for later, or put it back in the plan
  report.py [--from T-3] act <file.json>
      any orbmap action(s) as JSON (one object or a list), for example {"type": "plan_steps", "target": "T-3", "steps": [{"title": "..."}]}

orbmap reads <workspace>/.atlas/inbox.jsonl every few seconds and applies what it finds.
"""
import json, os, pathlib, re, sys, time, uuid

STATES = {'done', 'blocked', 'running', 'passed', 'failed', 'note'}
BUTTONS = {'adopt': 'merge', 'run': 'run', 'validate': 'run', 'retract': 'retract', 'fold': 'fold', 'finish': 'finish-task',
           'approve': 'gate-approve', 'sendback': 'gate-reject', 'tick': 'check-item', 'unfold': 'unfold', 'round': 'add-round',
           'accept': 'accept-proposal', 'answer': 'answer', 'pause': 'pause-all', 'resume': 'resume-all',
           'skip': 'skip-step', 'hold': 'hold-step'}
VERBS = {'approach', 'step', 'task', 'ask', 'tell', 'replies', 'act', 'context', 'join', *BUTTONS}


def _arg(a, i, default=''):
    return a[i].strip() if len(a) > i else default


def main(argv):
    frm = ''
    if '--from' in argv:
        i = argv.index('--from')
        frm = _arg(argv, i + 1)
        argv = argv[:i] + argv[i + 2:]
    here = pathlib.Path(__file__).resolve().parent
    if here.name == '.atlas' and not (argv and pathlib.Path(argv[0]).is_dir() and (here.parent / '.atlas').exists() and len(argv) > 1
                                      and (argv[1] in VERBS or (len(argv) > 2 and argv[2] in STATES))):
        argv = [str(here.parent), *argv]          # the copy inside a workspace: no workspace argument needed
    if len(argv) < 2:
        print(__doc__)
        return 1
    ws = pathlib.Path(argv[0]).resolve()
    atlas = ws / '.atlas'
    if not atlas.exists():
        print(f'Not an orbmap workspace: {ws}')
        return 1
    a = argv[1:]
    item = {'t': time.time(), 'from': frm, 'id': uuid.uuid4().hex}
    if a[0] == 'join':
        cwd = pathlib.Path(os.getcwd()).resolve()
        proj = pathlib.Path.home() / '.claude' / 'projects' / re.sub(r'[^A-Za-z0-9]', '-', str(cwd))
        fs = sorted(proj.glob('*.jsonl'), key=lambda f: f.stat().st_mtime, reverse=True) if proj.exists() else []
        if not fs or time.time() - fs[0].stat().st_mtime > 600:
            print('orbmap: run this from inside the Claude Code session you want to add (its folder has no active session).')
            return 1
        item['join'] = {'session': fs[0].stem, 'cwd': str(cwd), 'title': _arg(a, 1) or cwd.name}
        with open(atlas / 'inbox.jsonl', 'a', encoding='utf-8') as f:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')
        print(f"orbmap: asked to add this session ({item['join']['title']}). Tell the human to confirm in orbmap; it opens a small window there.")
        return 0
    if a[0] == 'context':
        f = atlas / 'context' / f'{frm or "MC"}.md'
        print(f.read_text(encoding='utf-8') if f.exists() else 'No map yet: open orbmap on this workspace.')
        return 0
    if a[0] == 'replies':
        box = atlas / 'outbox' / f'{frm or "unknown"}.jsonl'
        if not frm or not box.exists() or not box.read_text(encoding='utf-8').strip():
            print('No answers yet.')
            return 0
        for line in box.read_text(encoding='utf-8').splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            print(f"--- {r.get('from', '?')} answered{(' about: ' + r['about']) if r.get('about') else ''}\n{r.get('text', '')}\n")
        box.rename(box.with_suffix(f'.read-{int(time.time())}'))
        return 0
    if a[0] in VERBS:
        verb = a[0]
        if verb in BUTTONS and len(a) >= 2:
            button = 'resume-step' if verb == 'resume' and a[1].lower() != 'all' else BUTTONS[verb]   # resume <step> vs resume all
            item['press'] = {'button': button, 'card': a[1], 'verb': verb, 'opt': _arg(a, 2)}
        elif verb == 'approach' and len(a) >= 3:
            item['action'] = {'type': 'add_approach', 'target': a[1], 'approaches': [{'title': a[2], 'label': _arg(a, 3), 'idea': _arg(a, 4) or _arg(a, 3)}]}
        elif verb == 'step' and len(a) >= 3:
            item['action'] = {'type': 'insert_steps', 'target': a[1], 'steps': [{'title': a[2], 'detail': _arg(a, 3)}]}
        elif verb == 'task' and len(a) >= 3:
            item['action'] = {'type': 'create_tasks', 'target': a[1], 'tasks': [{'title': a[2], 'detail': _arg(a, 3)}]}
        elif verb in ('ask', 'tell') and len(a) >= 3:
            item['action'] = {'type': verb, 'to': a[1], 'text': a[2]}
        elif verb == 'act' and len(a) >= 2:
            src = pathlib.Path(a[1])
            try:
                data = json.loads(src.read_text(encoding='utf-8') if src.exists() else a[1])
            except (ValueError, OSError) as e:
                print(f'orbmap: that is not valid JSON ({e}). Nothing was sent.')
                return 1
            item['actions'] = data if isinstance(data, list) else [data]
        else:
            print(__doc__)
            return 1
    elif len(a) >= 2 and a[1] in STATES:
        item.update({'card': a[0], 'state': a[1], 'verdict': _arg(a, 2), 'summary': _arg(a, 3)})
    else:
        print(__doc__)
        return 1
    with open(atlas / 'inbox.jsonl', 'a', encoding='utf-8') as f:
        f.write(json.dumps(item, ensure_ascii=False) + '\n')
    what = (item.get('press') and f"{item['press']['verb']} {item['press']['card']}") or item.get('action', {}).get('type') or ('actions' in item and f"{len(item['actions'])} action(s)") or f"{item['card']} → {item['state']}"
    print(f'orbmap: {what} sent; the map updates in a few seconds.'
          + ('' if a[0] != 'ask' else f' Read the answer later with: report.py --from {frm or "<your id>"} replies'))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
