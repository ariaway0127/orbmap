"""orbmap live back end: one `claude -p` session per agent, real git worktrees, state on disk.

The UI (index.html) calls these methods through pywebview's js_api (see orbmap.py).
Everything the app needs to survive a restart lives in <workspace>/.atlas/:

    .atlas/state.json        canvas state (written by the UI)
    .atlas/agents.json       agent id -> claude session id, cwd, model, context tokens
    .atlas/wt/<node>/        one git worktree per task / approach (branch task/<id> or exp/<id>)
    .atlas/wt/QA/            the review gate's own worktree (detached at the candidate)
    .atlas/handoff/*.md      handoff notes written when a session gets full
    .atlas/log.jsonl         every call: agent, kind, cost, tokens, result
"""
import json, os, pathlib, re, shutil, subprocess, threading, time, uuid

from prompts import ROLE_PROMPTS, KIND_PROMPTS, SCHEMAS, TOOLS

# on Windows, child processes (git, claude) never open a console window
NO_WINDOW = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}

CLAUDE = shutil.which('claude') or 'claude'
CHAT_KINDS = ('mc_chat', 'card_chat', 'plan_tasks', 'peer_msg')
DEFAULT_MODEL = 'claude-sonnet-5-5'   # a full id, so a new release never switches models silently
CONTEXT_WINDOW = 200_000


class Backend:
    def __init__(self, workspace, emit=None, model=DEFAULT_MODEL):
        self.ws = pathlib.Path(workspace).resolve()
        self.dir = self.ws / '.atlas'
        self.emit = emit or (lambda ev: None)
        self.model = model
        self.locks = {}                      # agent id -> lock (one call at a time per session)
        self.glock = threading.Lock()        # git operations on the main checkout
        self.procs = {}                      # request id -> Popen (for cancel)
        self.stopped = set()                 # request ids the human stopped
        self._init_workspace()
        self.agents = self._read_json('agents.json', {})
        self.settings = {'context_window': CONTEXT_WINDOW, 'handoff_at': 0.85, 'model': model, 'load_user_settings': True, 'git_managed': True,
                         **self._read_json('settings.json', {})}

    # ---------- files ----------
    def _read_json(self, name, default):
        p = self.dir / name
        try:
            return json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            return default

    def _write_json(self, name, data):
        p = self.dir / name
        tmp = p.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
        os.replace(tmp, p)

    def _log(self, rec):
        with open(self.dir / 'log.jsonl', 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')

    # ---------- workspace + git ----------
    def git(self, *args, cwd=None, check=True):
        r = subprocess.run(['git', *args], cwd=cwd or self.ws, capture_output=True, **NO_WINDOW, text=True, encoding='utf-8', errors='replace')
        if check and r.returncode != 0:
            raise RuntimeError(f'git {" ".join(args)} failed: {r.stderr.strip() or r.stdout.strip()}')
        return r.stdout.strip()

    def _init_workspace(self):
        self.ws.mkdir(parents=True, exist_ok=True)
        (self.dir / 'wt').mkdir(parents=True, exist_ok=True)
        (self.dir / 'handoff').mkdir(exist_ok=True)
        valid = (self.ws / '.git').exists() and subprocess.run(['git', 'rev-parse', '--verify', 'HEAD'], cwd=self.ws, capture_output=True, **NO_WINDOW).returncode == 0
        if not valid:
            if (self.ws / '.git').exists() and not (self.ws / '.git' / 'HEAD').exists():
                shutil.rmtree(self.ws / '.git', ignore_errors=True)   # a broken, empty .git left behind
            self.git('init', '-b', 'main')
            self.git('config', 'user.name', 'orbmap')
            self.git('config', 'user.email', 'orbmap@localhost')
            readme = self.ws / 'README.md'
            if not readme.exists():
                readme.write_text('# Workspace\n\nManaged by orbmap.\n', encoding='utf-8')
            self.git('add', '-A'); self.git('commit', '-m', 'Initial commit')
        excl = self.ws / '.git' / 'info' / 'exclude'
        excl.parent.mkdir(parents=True, exist_ok=True)
        if '.atlas/' not in (excl.read_text(encoding='utf-8') if excl.exists() else ''):
            with open(excl, 'a', encoding='utf-8') as f:
                f.write('\n.atlas/\n')

    def main_info(self):
        n = int(self.git('rev-list', '--count', 'main') or 0)
        return {'hash': self.git('rev-parse', '--short', 'main'), 'commits': n,
                'log': self.git('log', '--oneline', '-8', 'main').split('\n')}

    def worktree(self, node_id, base='main', prefix='task'):
        """Create (or reuse) the worktree for a task or approach. Returns its path."""
        wt = self.dir / 'wt' / node_id
        branch = f'{prefix}/{node_id}'
        if wt.exists():
            return str(wt)
        with self.glock:
            exists = self.git('branch', '--list', branch)
            if exists:
                self.git('worktree', 'add', str(wt), branch)
            else:
                self.git('worktree', 'add', '-b', branch, str(wt), base)
        return str(wt)

    def commit_all(self, node_id, message):
        wt = self.dir / 'wt' / node_id
        if not wt.exists():
            return None
        self.git('add', '-A', cwd=wt)
        if not self.git('status', '--porcelain', cwd=wt):
            return None
        self.git('commit', '-m', message, cwd=wt)
        return self.git('rev-parse', '--short', 'HEAD', cwd=wt)

    def branch_of(self, node_id):
        wt = self.dir / 'wt' / node_id
        return self.git('rev-parse', '--abbrev-ref', 'HEAD', cwd=wt) if wt.exists() else None

    def diff_stat(self, node_id, base='main'):
        br = self.branch_of(node_id)
        if not br:
            return ''
        return self.git('diff', '--stat', f'{base}...{br}')

    def sync_from(self, node_id, base='main'):
        """Bring the latest base into a worktree (merge, not rebase: safer with several bots)."""
        wt = self.dir / 'wt' / node_id
        if not wt.exists():
            return {'ok': True, 'skipped': True}
        self.commit_all(node_id, f'{node_id}: work in progress')
        r = subprocess.run(['git', 'merge', '--no-edit', base], cwd=wt, capture_output=True, **NO_WINDOW, text=True, encoding='utf-8', errors='replace')
        if r.returncode != 0:
            subprocess.run(['git', 'merge', '--abort'], cwd=wt, capture_output=True, **NO_WINDOW)
            return {'ok': False, 'conflict': r.stdout[-800:] + r.stderr[-400:]}
        return {'ok': True}

    def _qa_worktree(self):
        qa = self.dir / 'wt' / 'QA'
        if not qa.exists():
            with self.glock:
                self.git('worktree', 'add', '--detach', str(qa), 'main')
        return qa

    def prepare_review(self, node_id, base='main'):
        """Merge base into the candidate, then put the QA worktree on the candidate (detached)."""
        s = self.sync_from(node_id, base)
        if not s['ok']:
            return s
        qa = self.dir / 'wt' / 'QA'
        br = self.branch_of(node_id)
        with self.glock:
            if not qa.exists():
                self.git('worktree', 'add', '--detach', str(qa), br)
            else:
                self.git('checkout', '--detach', '--force', br, cwd=qa)
                self.git('clean', '-fd', cwd=qa)
        return {'ok': True, 'branch': br, 'diff': self.git('diff', '--stat', f'{base}...{br}')}

    def merge(self, node_id, into='main', message=None):
        """Merge a task/approach branch into main (or an approach into its task). Returns new hash."""
        br = self.branch_of(node_id)
        if not br:
            return {'ok': False, 'error': f'no branch for {node_id}'}
        with self.glock:
            if into == 'main':
                cwd = self.ws
                if self.git('rev-parse', '--abbrev-ref', 'HEAD') != 'main':
                    self.git('checkout', 'main')
            else:
                cwd = self.dir / 'wt' / into
                self.commit_all(into, f'{into}: work in progress')
            displaced = []
            for attempt in range(2):
                r = subprocess.run(['git', 'merge', '--no-ff', '-m', message or f'Merge {br}', br], cwd=cwd, capture_output=True, **NO_WINDOW, text=True, encoding='utf-8', errors='replace')
                if r.returncode == 0:
                    break
                subprocess.run(['git', 'merge', '--abort'], cwd=cwd, capture_output=True, **NO_WINDOW)
                blocked = _untracked_in_the_way(r.stdout + r.stderr)
                if attempt or not blocked:
                    return {'ok': False, 'conflict': r.stdout[-800:] + r.stderr[-400:], 'displaced': displaced}
                dest = self.dir / 'displaced' / time.strftime('%Y%m%d-%H%M%S')
                for rel in blocked:
                    src = pathlib.Path(cwd) / rel
                    if src.exists():
                        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(src), str(dest / rel))
                        displaced.append(str((dest / rel).relative_to(self.ws)))
            h = self.git('rev-parse', '--short', 'HEAD', cwd=cwd)
        return {'ok': True, 'hash': h, 'main': self.main_info() if into == 'main' else None, 'displaced': displaced}

    def revert(self, merge_hash):
        with self.glock:
            self.git('revert', '--no-edit', '-m', '1', merge_hash)
        return {'ok': True, 'main': self.main_info()}

    # ---------- agents ----------
    def agent(self, agent_id, role, cwd):
        a = self.agents.get(agent_id)
        if a and a.get('imported'):
            return a                         # imported sessions always run in their own folder
        if not a or a.get('cwd') != str(cwd):
            a = {'id': agent_id, 'role': role, 'cwd': str(cwd), 'session': str(uuid.uuid4()), 'started': False,
                 'ctx_tokens': 0, 'cost': 0.0, 'calls': 0, 'generation': (a or {}).get('generation', 1)}
            self.agents[agent_id] = a
            self._write_json('agents.json', self.agents)
        return a

    def ctx_pct(self, agent_id):
        a = self.agents.get(agent_id)
        return round(100 * a['ctx_tokens'] / self.settings['context_window'], 1) if a else 0

    def call(self, req):
        """Run one turn of an agent's session. req = {id, agent, role, kind, cwd_node?, context, message}.
        Blocks until done (pywebview runs js_api calls in their own thread) and streams progress via emit."""
        agent_id, kind = req['agent'], req['kind']
        known = self.agents.get(agent_id)
        if known and known.get('imported'):
            cwd = known['cwd']                                       # an imported session stays in its own folder
        elif req.get('role') == 'qa' or kind == 'qa_review':
            cwd = self._qa_worktree()                                # QA always talks from the same folder (one session)
        elif req.get('cwd_node') and self.settings.get('git_managed', True):
            cwd = self.worktree(req['cwd_node'], req.get('base', 'main'), req.get('prefix', 'task'))
        else:
            cwd = self.ws
        a = self.agent(agent_id, req.get('role', 'task'), cwd)
        cwd = a['cwd']
        lock = self.locks.setdefault(agent_id, threading.Lock())
        with lock:
            return self._run(a, req, cwd)

    def _run(self, a, req, cwd):
        kind = req['kind']
        opts = self.settings.get('agent_opts', {}).get(a['id'], {})
        if req.get('raw'):
            prompt = req.get('message', '').strip()   # a slash command: it has to be the first thing the session sees
        else:
            prompt = f"{KIND_PROMPTS[kind]}\n\n## Context\n{req.get('context', '').strip()}\n\n## Message\n{req.get('message', '').strip()}"
        cmd = [CLAUDE, '-p', '--output-format', 'stream-json', '--verbose',
               '--json-schema', json.dumps(SCHEMAS[kind]), '--append-system-prompt', ROLE_PROMPTS[a['role']]]
        model = opts.get('model') or a.get('model') or self.settings.get('model')
        if model:
            cmd += ['--model', model]
        if opts.get('effort'):
            cmd += ['--effort', opts['effort']]
        if not self.settings.get('load_user_settings', True):
            cmd += ['--setting-sources', '']           # otherwise CLAUDE.md, skills, MCP and hooks load like in any session
        tools = TOOLS[kind]
        in_root = pathlib.Path(cwd).resolve() == self.ws and not a.get('imported')
        if in_root and a['role'] in ('mc', 'task') and kind in ('mc_chat', 'card_chat', 'peer_msg') and not self.settings.get('root_can_edit'):
            tools = TOOLS['plan_tasks']    # read-only: the root is main, and main only changes through review
        cmd += ['--allowedTools', *tools['allow']]
        deny = list(tools.get('deny', []))
        if a.get('imported') or not self.settings.get('git_managed', True):
            deny = [d for d in deny if not d.startswith('Bash(git')]   # the bot handles git itself, as in its own sessions
        mode = tools.get('mode', 'default')
        if opts.get('mode') == 'plan':
            tools, deny, mode = TOOLS['plan_tasks'], [], 'plan'          # look and plan only
        elif opts.get('mode') == 'bypass' and mode != 'default':
            mode = 'bypassPermissions'                                 # everything allowed, no checks
        if deny:
            cmd += ['--disallowedTools', *deny]
        cmd += ['--permission-mode', mode]
        if not a['started']:
            cmd += ['--session-id', a['session']]
        else:
            cmd += ['--resume', a['session']]
            if a.get('fork_pending'):
                cmd += ['--fork-session']        # first turn after a fork import: the original session stays untouched
        rid = req.get('id') or str(uuid.uuid4())
        t0 = time.time()
        self.emit({'type': 'start', 'req': rid, 'agent': a['id'], 'kind': kind})
        p = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding='utf-8', errors='replace', **NO_WINDOW)
        self.procs[rid] = p
        a['busy_rid'] = rid
        p.stdin.write(prompt); p.stdin.close()
        final, err_lines, texts, outs = None, [], [], []
        for line in p.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if ev.get('type') == 'system' and ev.get('subtype') == 'init' and ev.get('model'):
                a['model_id'] = ev['model']           # the model this session really runs
            if ev.get('type') == 'assistant':
                for c in (ev.get('message') or {}).get('content') or []:
                    if c.get('type') == 'tool_use' and c.get('name') == 'StructuredOutput' and isinstance(c.get('input'), dict):
                        outs.append(c['input'])       # a turn can answer more than once; keep every answer
                    elif c.get('type') == 'tool_use':
                        self.emit({'type': 'tool', 'req': rid, 'agent': a['id'], 'tool': c.get('name'), 'detail': _brief(c.get('input'))})
                    elif c.get('type') == 'text' and c.get('text', '').strip():
                        texts.append(c['text'].strip())
                        self.emit({'type': 'text', 'req': rid, 'agent': a['id'], 'text': c['text'][:400]})
                u = (ev.get('message') or {}).get('usage')
                if u:
                    a['ctx_tokens'] = u.get('input_tokens', 0) + u.get('cache_read_input_tokens', 0) + u.get('cache_creation_input_tokens', 0) + u.get('output_tokens', 0)
            elif ev.get('type') == 'result':
                final = ev
        err_lines = p.stderr.read()
        p.wait()
        self.procs.pop(rid, None)
        a.pop('busy_rid', None)
        a['started'] = True
        a['calls'] += 1
        if final and final.get('session_id') and final['session_id'] != a['session']:
            a.setdefault('previous_sessions', []).append(a['session'])
            a['session'] = final['session_id']    # fork (or the CLI chose a new id)
        a.pop('fork_pending', None)
        if final:
            a['cost'] += final.get('total_cost_usd') or 0
        self._write_json('agents.json', self.agents)
        out = (final or {}).get('structured_output')
        if out is None and final:
            out = _fallback(kind, final.get('result') or '')   # the model answered in plain text instead
        if kind in CHAT_KINDS and len(outs) > 1:
            # several answers in one turn (e.g. a proposal, then "waiting for you"): keep all text and all actions
            says = [o.get('say', '').strip() for o in outs if o.get('say', '').strip()]
            out = {'say': '\n\n'.join(dict.fromkeys(says)), 'actions': [x for o in outs for x in (o.get('actions') or [])]}
        if isinstance(out, dict) and 'say' in out and not (out.get('say') or '').strip() and texts:
            out['say'] = '\n\n'.join(texts)          # the answer was written as plain text only
        ok = out is not None
        if rid in self.stopped:
            self.stopped.discard(rid)
            ok, out = False, None
            final = {'subtype': 'stopped', 'result': 'Stopped by you.'}
        res = {'ok': ok, 'req': rid, 'agent': a['id'], 'kind': kind, 'data': out,
               'error': None if ok else _error_text(final, err_lines),
               'model': a.get('model_id', ''),
               'ctx_pct': self.ctx_pct(a['id']), 'cost': (final or {}).get('total_cost_usd') or 0,
               'secs': round(time.time() - t0, 1), 'session': a['session'], 'generation': a['generation']}
        self._log({**res, 't': time.strftime('%Y-%m-%d %H:%M:%S'), 'message': req.get('message', '')[:300]})
        self.emit({'type': 'end', 'req': rid, 'agent': a['id'], 'ok': ok, 'ctx_pct': res['ctx_pct']})
        return res

    def cancel(self, rid):
        p = self.procs.get(rid)
        if p:
            self.stopped.add(rid)
            p.kill()
        return {'ok': bool(p)}

    def stop_agent(self, agent_id):
        """Stop whatever this agent's session is doing right now."""
        a = self.agents.get(agent_id) or {}
        return self.cancel(a.get('busy_rid')) if a.get('busy_rid') else {'ok': False, 'error': 'not running'}

    def agent_options(self, agent_id, opts=None):
        """Read or change one agent's model / effort / permission mode (model: opus|sonnet|haiku, effort: low..max, mode: auto|plan|bypass)."""
        all_opts = self.settings.setdefault('agent_opts', {})
        cur = all_opts.setdefault(agent_id, {})
        if opts:
            cur.update({k: v for k, v in opts.items() if k in ('model', 'effort', 'mode')})
            self._write_json('settings.json', self.settings)
        return {'model': cur.get('model') or self.settings.get('model') or 'default', 'model_id': (self.agents.get(agent_id) or {}).get('model_id', ''),
                'effort': cur.get('effort') or '',
                'mode': cur.get('mode') or 'auto', 'busy': bool((self.agents.get(agent_id) or {}).get('busy_rid'))}

    def reset_session(self, agent_id):
        """/clear: the next turn starts a fresh session (the old one stays on disk and can still be resumed)."""
        a = self.agents.get(agent_id)
        if not a:
            return {'ok': True}
        a.setdefault('previous_sessions', []).append(a['session'])
        a.update({'session': str(uuid.uuid4()), 'started': False, 'ctx_tokens': 0, 'imported': False if not a.get('imported') else a['imported']})
        self._write_json('agents.json', self.agents)
        return {'ok': True, 'session': a['session']}

    def list_commands(self, agent_id=None):
        """Slash commands this agent's session knows: your skills and custom commands (user and project), like in VS Code."""
        a = self.agents.get(agent_id or '') or {}
        cwd = pathlib.Path(a.get('cwd') or self.ws)
        roots = [(pathlib.Path.home() / '.claude', 'user'), (cwd / '.claude', 'project')]
        if cwd != self.ws:
            roots.append((self.ws / '.claude', 'project'))
        out, seen = [], set()
        for base, src in roots:
            for f in sorted((base / 'commands').rglob('*.md')) if (base / 'commands').exists() else []:
                name = ':'.join(f.relative_to(base / 'commands').with_suffix('').parts)
                if name not in seen:
                    seen.add(name); out.append({'name': name, 'desc': _front(f).get('description', ''), 'kind': 'command', 'source': src})
            for f in sorted((base / 'skills').glob('*/SKILL.md')) if (base / 'skills').exists() else []:
                fm = _front(f)
                name = fm.get('name') or f.parent.name
                if name not in seen:
                    seen.add(name); out.append({'name': name, 'desc': fm.get('description', ''), 'kind': 'skill', 'source': src})
        return out

    def needs_handoff(self, agent_id):
        return self.ctx_pct(agent_id) >= 100 * self.settings['handoff_at']

    def handoff(self, agent_id, context=''):
        """Ask the agent for a handoff note, then start a fresh session that begins by reading it."""
        a = self.agents.get(agent_id)
        if not a:
            return {'ok': False, 'error': 'unknown agent'}
        res = self.call({'agent': agent_id, 'role': a['role'], 'kind': 'handoff', 'cwd_node': None if a['cwd'] == str(self.ws) else pathlib.Path(a['cwd']).name,
                         'context': context, 'message': 'Write the handoff note now.'})
        if not res['ok']:
            return res
        n = a['generation']
        md = self.dir / 'handoff' / f'{agent_id}-{n}.md'
        md.write_text(res['data']['markdown'], encoding='utf-8')
        a.update({'session': str(uuid.uuid4()), 'started': False, 'ctx_tokens': 0, 'generation': n + 1})
        self._write_json('agents.json', self.agents)
        res2 = self.call({'agent': agent_id, 'role': a['role'], 'kind': 'resume_from_handoff', 'cwd_node': None if a['cwd'] == str(self.ws) else pathlib.Path(a['cwd']).name,
                          'context': md.read_text(encoding='utf-8'), 'message': f'You are generation {n + 1} of {agent_id}. Read the handoff above and confirm what you will do next.'})
        return {'ok': res2['ok'], 'file': str(md.relative_to(self.ws)), 'markdown': res['data']['markdown'],
                'summary': (res2.get('data') or {}).get('say', ''), 'ctx_pct': self.ctx_pct(agent_id), 'generation': n + 1}

    # ---------- existing sessions (VS Code / terminal) ----------
    @staticmethod
    def _project_dir(folder):
        enc = ''.join(c if c.isalnum() else '-' for c in str(pathlib.Path(folder).resolve()))
        root = pathlib.Path.home() / '.claude' / 'projects'
        for d in (root.iterdir() if root.exists() else []):
            if d.name.lower() == enc.lower():
                return d
        return None

    def list_sessions(self, folder):
        """Sessions Claude Code saved for this folder, newest first: id, first prompt, last time, message count."""
        d = self._project_dir(folder)
        if not d:
            return []
        known = {a['session']: k for k, a in self.agents.items()}
        out = []
        for f in sorted(d.glob('*.jsonl'), key=lambda x: x.stat().st_mtime, reverse=True)[:60]:
            first, n, last, branch = '', 0, '', ''
            try:
                for line in open(f, encoding='utf-8', errors='replace'):
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue
                    if ev.get('type') != 'user':
                        continue
                    c = (ev.get('message') or {}).get('content')
                    text = c if isinstance(c, str) else ' '.join(x.get('text', '') for x in c or [] if isinstance(x, dict) and x.get('type') == 'text')
                    text = text.strip()
                    if not text or text.startswith('<') or text.startswith('Caveat:'):
                        continue
                    n += 1
                    last = ev.get('timestamp', last)
                    branch = ev.get('gitBranch', branch)
                    if not first:
                        first = text[:160]
            except OSError:
                continue
            if n:
                out.append({'id': f.stem, 'first': first, 'messages': n, 'last': last, 'branch': branch,
                            'kb': round(f.stat().st_size / 1024), 'agent': known.get(f.stem)})
        return out

    _scan_cache = {}

    def _scan_session(self, f):
        """Title, folder, first prompt, last time and size of one saved session (cached by mtime)."""
        st = f.stat()
        key = (str(f), st.st_mtime, st.st_size)
        hit = Backend._scan_cache.get(str(f))
        if hit and hit[0] == key:
            return hit[1]
        info = {'id': f.stem, 'file': str(f), 'title': '', 'ai_title': '', 'cwd': '', 'first': '', 'messages': 0, 'last': '', 'branch': ''}
        try:
            for line in open(f, encoding='utf-8', errors='replace'):
                if '"custom-title"' in line or '"ai-title"' in line:
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue
                    if ev.get('type') == 'custom-title' and ev.get('customTitle'):
                        info['title'] = ev['customTitle']
                    elif ev.get('type') == 'ai-title' and ev.get('aiTitle'):
                        info['ai_title'] = ev['aiTitle']
                    continue
                if '"type":"user"' not in line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if not info['cwd'] and ev.get('cwd'):
                    info['cwd'] = ev['cwd']
                c = (ev.get('message') or {}).get('content')
                text = c if isinstance(c, str) else ' '.join(x.get('text', '') for x in c or [] if isinstance(x, dict) and x.get('type') == 'text')
                text = text.strip()
                if not text or text.startswith('<') or text.startswith('Caveat:'):
                    continue
                info['messages'] += 1
                info['last'] = ev.get('timestamp', info['last'])
                info['branch'] = ev.get('gitBranch', info['branch'])
                if not info['first']:
                    info['first'] = text[:160]
        except OSError:
            pass
        info['name'] = info['title'] or info['ai_title'] or info['first'][:60]
        Backend._scan_cache[str(f)] = (key, info)
        return info

    def all_sessions(self, days=45, limit=60):
        """Every Claude Code session saved on this computer (VS Code or terminal), newest first, with its title.
        orbmap's own bot sessions in this workspace are left out."""
        root = pathlib.Path.home() / '.claude' / 'projects'
        if not root.exists():
            return []
        cutoff = time.time() - days * 86400
        files = [f for d in root.iterdir() if d.is_dir() for f in d.glob('*.jsonl') if f.stat().st_mtime >= cutoff]
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        mine = {a['session'] for a in self.agents.values() if not a.get('imported')}
        known = {a['session']: k for k, a in self.agents.items()}
        out = []
        for f in files:
            if len(out) >= limit:
                break
            if f.stem in mine:
                continue
            info = self._scan_session(f)
            if not info['messages'] or (info['messages'] <= 1 and not info['title'] and not info['ai_title']):
                continue                                   # empty, or a one-shot script call (claude -p)
            cwd = info['cwd']
            if (cwd and ('/.atlas/' in cwd.replace(chr(92), '/') + '/')) or any(info['first'].startswith(k[:40]) for k in KIND_PROMPTS.values()):
                continue                                   # sessions orbmap itself started (any workspace)
            out.append({**{k: info[k] for k in ('id', 'name', 'title', 'ai_title', 'cwd', 'first', 'messages', 'last', 'branch', 'file')}, 'agent': known.get(f.stem)})
        return out

    def import_session(self, agent_id, role, folder, session_id, fork=False):
        """Adopt an existing session as a bot. It keeps its folder; orbmap never moves or rewrites it.
        fork=True: the first turn uses --fork-session, so the original session stays exactly as it was."""
        self.agents[agent_id] = {'id': agent_id, 'role': role, 'cwd': str(pathlib.Path(folder).resolve()), 'session': session_id,
                                 'started': True, 'imported': True, 'fork_pending': bool(fork), 'origin': session_id,
                                 'ctx_tokens': 0, 'cost': 0.0, 'calls': 0, 'generation': 1}
        self._write_json('agents.json', self.agents)
        return {'ok': True, 'agent': agent_id, 'cwd': self.agents[agent_id]['cwd']}

    def session_command(self, agent_id):
        """The command to continue this bot's session yourself (terminal or VS Code)."""
        a = self.agents.get(agent_id)
        if not a:
            return {'ok': False, 'error': 'This bot has not talked to Claude yet.'}
        # PowerShell (the default terminal in VS Code on Windows) and bash both understand this form
        cmd = (f'Set-Location "{a["cwd"]}"; claude --resume {a["session"]}' if os.name == 'nt'
               else f'cd "{a["cwd"]}" && claude --resume {a["session"]}')
        return {'ok': True, 'cwd': a['cwd'], 'session': a['session'], 'command': cmd}

    # ---------- attachments (pasted / dropped files) ----------
    def save_attachment(self, name, data_url):
        """Save a file the human attached in the chat. Returns its absolute path (what the session reads) and its URL."""
        import base64
        head, _, b64 = str(data_url).partition(',')
        raw = base64.b64decode(b64)
        safe = re.sub(r'[^A-Za-z0-9._-]+', '_', pathlib.Path(name).name).strip('._') or 'file'
        if '.' not in safe and 'image/' in head:
            safe += '.' + head.split('image/')[1].split(';')[0].replace('jpeg', 'jpg')
        fname = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}-{safe}"
        d = self.dir / 'attachments'
        d.mkdir(exist_ok=True)
        (d / fname).write_bytes(raw)
        return {'ok': True, 'name': safe, 'path': str(d / fname), 'url': f'/attachments/{fname}'}

    # ---------- state ----------
    def load_state(self):
        p = self.dir / 'state.json'
        return p.read_text(encoding='utf-8') if p.exists() else None

    def save_state(self, text):
        tmp = self.dir / 'state.tmp'
        tmp.write_text(text, encoding='utf-8')
        os.replace(tmp, self.dir / 'state.json')
        return True

    def info(self):
        return {'workspace': str(self.ws), 'main': self.main_info(), 'settings': self.settings,
                'agents': {k: {'ctx_pct': self.ctx_pct(k), 'generation': v['generation'], 'calls': v['calls'], 'cost': round(v['cost'], 3)} for k, v in self.agents.items()}}

    def set_setting(self, key, value):
        self.settings[key] = value
        self._write_json('settings.json', self.settings)
        return self.settings


def _error_text(final, err):
    text = ((final or {}).get('result') or err or '').strip()
    if re.search(r'hit your .*limit|usage limit|rate limit', text, re.I):
        return f'Usage limit: {text}'
    sub = (final or {}).get('subtype', 'no result')
    return f'{sub}: {text[-600:]}' if sub not in ('success',) else (text[-600:] or 'no answer')


def _front(path):
    """name / description from a markdown file's front matter."""
    try:
        text = path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return {}
    if not text.startswith('---'):
        return {}
    out = {}
    for line in text.split('\n')[1:40]:
        if line.strip() == '---':
            break
        m = re.match(r'^(name|description):\s*(.*)$', line)
        if m:
            out[m.group(1)] = m.group(2).strip().strip('"').strip("'")[:240]
    return out


def _untracked_in_the_way(out):
    """Files listed by git under 'untracked working tree files would be overwritten by merge'."""
    m = re.search(r'untracked working tree files would be overwritten by merge:\n(.*?)\nPlease move', out, re.S)
    return [x.strip() for x in m.group(1).splitlines() if x.strip()] if m else []


def _fallback(kind, text):
    """Structured output missing: accept JSON in the text, or wrap plain text where that is safe."""
    text = text.strip()
    if not text:
        return None
    s = text[text.find('{'):text.rfind('}') + 1] if '{' in text else ''
    if s:
        try:
            return json.loads(s)
        except Exception:
            pass
    if kind == 'handoff':
        return {'markdown': text}
    if kind in ('mc_chat', 'card_chat', 'plan_tasks'):
        return {'say': text, 'actions': []}
    if kind == 'resume_from_handoff':
        return {'say': text}
    return None


def _brief(inp):
    if not isinstance(inp, dict):
        return ''
    for k in ('file_path', 'command', 'pattern', 'path', 'description'):
        if inp.get(k):
            return str(inp[k])[:160]
    return ''
