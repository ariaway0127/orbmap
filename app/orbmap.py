"""orbmap live app: the same index.html in a desktop window, connected to real Claude sessions.

    py -3.12 app/orbmap.py <workspace folder> [--offline] [--debug] [--browser [--port 8765]]
    pyw -3.12 app/orbmap.py <workspace folder>     (Windows, no console window; output goes to <workspace>/.atlas/app.log)

--browser: no pywebview needed; serves the page and the same API on http://127.0.0.1:<port> and opens your browser.

The workspace is a git repo the bots work in (created if missing). Closing the window keeps everything:
the canvas state, each bot's Claude session and the worktrees are in <workspace>/.atlas/.
Needs: pip install pywebview; the `claude` CLI signed in.
"""
import json, pathlib, sys, threading

try:
    import webview
except ImportError:      # --browser mode works without it
    webview = None

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from backend import Backend  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent


class Api:
    """Everything here is callable from JavaScript as `await pywebview.api.<name>(...)`.
    pywebview runs each call in its own thread, so long Claude calls do not block the window."""

    def __init__(self, workspace):
        self._win = None
        self._b = Backend(workspace, emit=self._emit)
        self._elock = threading.Lock()

    def _emit(self, ev):
        if getattr(self, '_emit_hook', None):
            return self._emit_hook(ev)
        if self._win:
            with self._elock:
                try:
                    self._win.evaluate_js(f'window.atlasLive && atlasLive.onEvent({json.dumps(ev, ensure_ascii=False)})')
                except Exception:
                    pass

    # state
    def load_state(self): return self._b.load_state()
    def save_state(self, text): return self._b.save_state(text)
    def info(self): return self._b.info()
    def set_setting(self, key, value): return self._b.set_setting(key, value)

    # agents
    def call(self, req): return _safe(self._b.call, req)
    def cancel(self, rid): return self._b.cancel(rid)
    def needs_handoff(self, agent_id): return self._b.needs_handoff(agent_id)
    def handoff(self, agent_id, context=''): return _safe(self._b.handoff, agent_id, context)

    def save_attachment(self, name, data_url): return _safe(self._b.save_attachment, name, data_url)
    def list_commands(self, agent_id=None): return _safe(self._b.list_commands, agent_id)
    def agent_options(self, agent_id, opts=None): return _safe(self._b.agent_options, agent_id, opts)
    def stop_agent(self, agent_id): return _safe(self._b.stop_agent, agent_id)
    def reset_session(self, agent_id): return _safe(self._b.reset_session, agent_id)

    # existing sessions
    def list_sessions(self, folder): return _safe(self._b.list_sessions, folder)
    def all_sessions(self, days=45, limit=60): return _safe(self._b.all_sessions, days, limit)
    def import_session(self, agent_id, role, folder, session_id, fork=False): return _safe(self._b.import_session, agent_id, role, folder, session_id, fork)
    def session_command(self, agent_id): return self._b.session_command(agent_id)
    def workspace(self): return str(self._b.ws)

    # git
    def worktree(self, node_id, base='main', prefix='task'): return _safe(lambda: {'ok': True, 'path': self._b.worktree(node_id, base, prefix)})
    def commit(self, node_id, message): return _safe(lambda: {'ok': True, 'hash': self._b.commit_all(node_id, message)})
    def diff_stat(self, node_id, base='main'): return _safe(lambda: {'ok': True, 'diff': self._b.diff_stat(node_id, base)})
    def sync_from(self, node_id, base='main'): return _safe(self._b.sync_from, node_id, base)
    def prepare_review(self, node_id, base='main'): return _safe(self._b.prepare_review, node_id, base)
    def merge(self, node_id, into='main', message=None): return _safe(self._b.merge, node_id, into, message)
    def revert(self, merge_hash): return _safe(self._b.revert, merge_hash)
    def main_info(self): return _safe(self._b.main_info)


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception as e:  # the UI shows the error instead of hanging
        return {'ok': False, 'error': f'{type(e).__name__}: {e}'}


def serve_browser(api, port, offline=False, open_browser=True, block=True):
    """Serve index.html and the API over http (localhost only). Events go to a queue the page polls."""
    import http.server, mimetypes, queue, socketserver, urllib.parse, webbrowser
    events = queue.Queue()
    api._emit_hook = events.put
    page = 'dist/index.offline.html' if offline else 'index.html'

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(ROOT), **k)

        def log_message(self, *a):
            pass

        def _json(self, obj):
            body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.startswith('/wsfile?'):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('p', [''])[0]
                f = pathlib.Path(q).resolve()
                if not f.is_file() or api._b.ws not in f.parents:      # only files inside the workspace
                    return self.send_error(404)
                body = f.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', mimetypes.guess_type(f.name)[0] or 'application/octet-stream')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                return self.wfile.write(body)
            if self.path.startswith('/attachments/'):
                f = api._b.dir / 'attachments' / pathlib.Path(urllib.parse.unquote(self.path.split('?')[0])).name
                if not f.is_file():
                    return self.send_error(404)
                body = f.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', mimetypes.guess_type(f.name)[0] or 'application/octet-stream')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                return self.wfile.write(body)
            if self.path.startswith('/events'):
                out = []
                while not events.empty() and len(out) < 200:
                    out.append(events.get_nowait())
                return self._json(out)
            return super().do_GET()

        def do_POST(self):
            name = urllib.parse.urlparse(self.path).path.rsplit('/', 1)[-1]
            n = int(self.headers.get('Content-Length') or 0)
            args = json.loads(self.rfile.read(n) or b'[]')
            fn = getattr(api, name, None)
            if name.startswith('_') or not callable(fn):
                return self._json({'ok': False, 'error': f'no such call: {name}'})
            return self._json(fn(*args))

    class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    srv = Server(('127.0.0.1', port), Handler)
    url = f'http://127.0.0.1:{srv.server_address[1]}/{page}?live=1&http=1'
    if not block:                      # the desktop window uses the same local server
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return url
    print(f'orbmap (browser mode): {url}\nWorkspace: {api._b.ws}\nCtrl+C to stop.')
    if open_browser:
        webbrowser.open(url)
    srv.serve_forever()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if '--port' in sys.argv:
        port = int(sys.argv[sys.argv.index('--port') + 1])
        args = [a for a in args if a != str(port)]
    else:
        port = 8765
    if not args:
        print(__doc__)
        sys.exit(1)
    if sys.stdout is None:   # started with pythonw / pyw (no console): keep the output in the workspace
        log = pathlib.Path(args[0]).resolve() / '.atlas' / 'app.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(log, 'a', encoding='utf-8', buffering=1)
    api = Api(args[0])
    if '--browser' in sys.argv:
        return serve_browser(api, port, '--offline' in sys.argv, '--no-open' not in sys.argv)
    if webview is None:
        print('pywebview is not installed: pip install pywebview, or run with --browser')
        sys.exit(1)
    # the window loads the page from the same local server as --browser (one code path, and WebView2 cannot open file:// URLs with a query)
    url = serve_browser(api, 0, '--offline' in sys.argv, open_browser=False, block=False)
    webview.create_window('orbmap', url=url, width=1600, height=1000, background_color='#0B0D12', text_select=True)
    webview.start(debug='--debug' in sys.argv, private_mode=False,
                  storage_path=str(pathlib.Path(args[0]).resolve() / '.atlas' / 'webview'))


if __name__ == '__main__':
    main()
