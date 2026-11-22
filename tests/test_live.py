"""End-to-end test of live mode against real Claude Code sessions. It spends real usage (a few dozen short turns).

    py -3.12 tests/test_live.py <scratch folder> [--model haiku|sonnet] [--keep]

Starts app/orbmap.py --browser on a fresh workspace inside <scratch folder>, drives the page with headless Chrome,
and checks: MC split → tasks from chat → a bot plans steps from chat → steps run for real in a worktree → QA reviews and
merges into main (real git) → reload keeps everything and the bot remembers → worker job → hand back / take back →
automatic handoff. Needs: selenium, Chrome, the `claude` CLI signed in.
"""
import json, pathlib, shutil, socket, subprocess, sys, time

from selenium import webdriver

ROOT = pathlib.Path(__file__).resolve().parent.parent
args = [a for a in sys.argv[1:] if not a.startswith('--')]
OUT = pathlib.Path(args[0] if args else ROOT / 'test_shots_live').resolve()
MODEL = sys.argv[sys.argv.index('--model') + 1] if '--model' in sys.argv else 'haiku'
WS = OUT / f'workspace-{time.strftime("%Y%m%d-%H%M%S")}'   # a fresh folder every run
(WS / '.atlas').mkdir(parents=True)
(WS / '.atlas' / 'settings.json').write_text(json.dumps({'model': MODEL}), encoding='utf-8')

with socket.socket() as so:
    so.bind(('127.0.0.1', 0))
    PORT = so.getsockname()[1]
srv = subprocess.Popen([sys.executable, str(ROOT / 'app' / 'orbmap.py'), str(WS), '--browser', '--no-open', '--port', str(PORT)],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
for _ in range(60):
    try:
        socket.create_connection(('127.0.0.1', PORT), timeout=0.5).close()
        break
    except OSError:
        time.sleep(0.25)

opts = webdriver.ChromeOptions()
opts.add_argument('--headless=new')
opts.add_argument('--window-size=1600,1000')
d = webdriver.Chrome(options=opts)
R = []


def check(name, cond, info=''):
    R.append(('PASS' if cond else 'FAIL', name, info))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}{(' — ' + str(info)[:200]) if info and not cond else ''}", flush=True)


def js(s, *a):
    return d.execute_script(s, *a)


def wait_for(expr, timeout=240, step=1.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if js(f'return !!({expr})'):
                return True
        except Exception:
            pass
        time.sleep(step)
    return False


def shot(n):
    OUT.mkdir(parents=True, exist_ok=True)
    d.save_screenshot(str(OUT / f'live_{n}.png'))


def git(*a):
    return subprocess.run(['git', *a], cwd=WS, capture_output=True, text=True).stdout.strip()


def last_bot(node):
    return js(f"const n = state.nodes['{node}']; const m = n.messages.filter(x => x.sender === 'bot').pop(); return m ? m.text : ''")


try:
    url = f'http://127.0.0.1:{PORT}/index.html?live=1&http=1'
    booted = False
    for _ in range(3):                       # the server may still be importing on a cold start
        d.get(url)
        booted = wait_for("typeof state === 'object' && state && document.getElementById('live-badge')", 40)
        if booted:
            break
    check('page boots in live mode', booted, d.execute_script('return document.title'))

    # 1. MC splits the goal (a real session)
    js("openController(); handleChat('I want a tiny Python command-line tool c2f.py that converts Celsius to Fahrenheit, with one unittest. Keep it very small: at most 2 sub-goals.')")
    check('MC proposes a split', wait_for("state.mc.messages.some(m => m.kind === 'proposal' && m.status === 'pending' && m.items.length)"), js('return state.mc.messages.slice(-2)'))
    js("const i = state.mc.messages.findIndex(m => m.kind === 'proposal' && m.status === 'pending'); acceptProposal(i)")
    check('sub-goals created', wait_for("Object.values(state.nodes).some(n => n.tier === 'subgoal')", 10))
    shot('01_split')

    # 2. tasks from chat
    js("openController(); handleChat('Create exactly one task under SG-1 for the whole tool (code and test). Do not create tasks for other sub-goals.')")
    check('MC creates a task from chat', wait_for("state.nodes['T-1']"), js('return state.mc.messages.slice(-3)'))

    # 3. the task bot plans steps from chat
    js("openNode('T-1'); handleChat('Lay this out as exactly 2 steps: 1) write c2f.py, 2) write test_c2f.py with unittest and run it.')")
    check('task bot creates step cards from chat', wait_for("state.nodes['T-1'].mode === 'steps' && kidsOf('T-1').filter(k => k.tier === 'step').length >= 2"), last_bot('T-1'))
    shot('02_steps')

    # 4. run: steps for real, then QA review and merge into main
    js("runContainer('T-1')")
    check('step 1 done for real', wait_for("state.nodes['T-1.1'].status === 'done'", 300), js("return state.nodes['T-1.1']"))
    check('step 2 done for real', wait_for("kidsOf('T-1').filter(k => k.tier === 'step').every(k => k.status === 'done')", 400))
    check('files exist in the task worktree', (WS / '.atlas' / 'wt' / 'T-1' / 'c2f.py').exists())
    check('QA finishes the review', wait_for("state.nodes.QA.mrs.some(m => ['verified', 'failed'].includes(m.status))", 400), js('return state.nodes.QA.mrs'))
    verdict = js("return state.nodes.QA.mrs[state.nodes.QA.mrs.length - 1].status")
    if verdict == 'failed':
        print('QA failed the first review; asking the bot to fix it', flush=True)
        js("openNode('T-1'); handleChat('Fix what QA found, then send this task to review again.')")
        check('after a fix QA merges', wait_for("state.nodes.QA.mrs.some(m => m.status === 'verified')", 600), js('return state.nodes.QA.mrs'))
    check('main has the merge (real git)', 'Merge' in git('log', '--oneline', '-5', 'main') or 'MR-' in git('log', '--oneline', '-5', 'main'), git('log', '--oneline', '-5', 'main'))
    check('c2f.py is on main', (WS / 'c2f.py').exists())
    check('canvas main hash = git main hash', wait_for(f"state.main.hash === '{git('rev-parse', '--short', 'main')}'", 20), js('return state.main'))
    check('task marked done', wait_for("state.nodes['T-1'].done", 20))
    shot('03_merged')

    # 5. close and reopen: the canvas and the sessions come back
    time.sleep(1.5)
    d.get(url)
    check('reopen restores the canvas', wait_for("typeof state === 'object' && state && state.nodes['T-1'] && state.nodes['T-1'].done", 30))
    js("openNode('T-1'); handleChat('In one short sentence: which file names did you create earlier in this task?')")
    check('bot remembers after reopen (same session)', wait_for("/c2f/i.test((state.nodes['T-1'].messages.filter(m => m.sender === 'bot').pop() || {}).text || '')", 240), last_bot('T-1'))

    # 6. worker job from chat
    js("openNode('T-1'); handleChat('Use submit_job to send the BATCH worker this job: count the lines of c2f.py in the workspace root and report the number.')")
    check('worker session finishes a job', wait_for("(state.nodes['W-BATCH'].jobs || []).some(j => j.live && j.status === 'done')", 300), js("return state.nodes['W-BATCH'].jobs"))

    # 7. hand back / take back
    js("liveHandBack('T-1')")
    check('hand back marks the bot external', wait_for("botById('T-1').external === true", 15))
    js("openNode('T-1'); handleChat('hello')")
    check('orbmap does not talk to a handed-back session', wait_for("state.nodes['T-1'].messages.some(m => /handed back/i.test(m.text))", 15))
    js("liveTakeBack('T-1')")
    check('take back', js("return !botById('T-1').external"))

    # 8. automatic handoff (small window so it triggers)
    js("atlasApi.set_setting('context_window', 26000)")
    time.sleep(1)
    js("openNode('T-1'); handleChat('Reply with just OK.')")
    check('handoff runs and a new session reads it back', wait_for("botById('T-1').gen >= 2", 300), js("return botById('T-1')"))
    check('handoff note written', any((WS / '.atlas' / 'handoff').glob('T-1-*.md')))
    js("atlasApi.set_setting('context_window', 200000)")

    # 9. existing sessions can be listed for import
    n = js("return atlasApi.list_sessions(arguments[0]).then(r => r.length)", str(WS))
    check('sessions of the workspace are listed for import', (n or 0) >= 1, n)
    shot('04_end')
finally:
    fails = [r for r in R if r[0] == 'FAIL']
    print(f'\n{len(R) - len(fails)}/{len(R)} passed', flush=True)
    try:
        cost = sum(json.loads(l).get('cost', 0) for l in open(WS / '.atlas' / 'log.jsonl', encoding='utf-8'))
        print(f'API-equivalent cost of this run: ${cost:.2f} ({MODEL})')
    except OSError:
        pass
    d.quit()
    srv.terminate()
    sys.exit(1 if fails else 0)
