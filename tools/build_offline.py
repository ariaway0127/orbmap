"""Build the offline version: dist/index.offline.html

- Runs the Tailwind CLI v3.4.17 over index.html and inlines the compiled CSS (no cdn.tailwindcss.com needed)
- Removes Google Fonts (falls back to system fonts without a network)
Needs Node.js (npx). The first run downloads tailwindcss@3.4.17.

Usage: python tools/build_offline.py
"""
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "index.html"
OUT = ROOT / "dist" / "index.offline.html"
CDN_TAG = '<script src="https://cdn.tailwindcss.com/3.4.17"></script>'


def main() -> int:
    html = SRC.read_text(encoding="utf-8")
    if CDN_TAG not in html:
        print("Tailwind CDN tag not found; index.html may have changed", file=sys.stderr)
        return 1
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if not npx:
        print("Node.js (npx) is required", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        (td / "in.css").write_text("@tailwind base;\n@tailwind components;\n@tailwind utilities;\n", encoding="utf-8")
        content = SRC.as_posix()
        (td / "tailwind.config.js").write_text(
            "module.exports = { content: [" + repr(content) + "], theme: { extend: {} }, plugins: [] };\n",
            encoding="utf-8",
        )
        cmd = [npx, "-y", "tailwindcss@3.4.17", "-c", str(td / "tailwind.config.js"),
               "-i", str(td / "in.css"), "-o", str(td / "out.css")]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            print(r.stdout, r.stderr, file=sys.stderr)
            return r.returncode
        css = (td / "out.css").read_text(encoding="utf-8")
    html = html.replace(CDN_TAG, "<style>/* Tailwind v3.4.17 (compiled offline) */\n" + css + "\n</style>")
    # remove Google Fonts (preconnect and the font stylesheet)
    html = re.sub(r'\s*<link rel="preconnect" href="https://fonts\.(googleapis|gstatic)\.com"[^>]*>', "", html)
    html = re.sub(r'\s*<link href="https://fonts\.googleapis\.com/css2[^>]*>', "", html)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8", newline="\n")
    left = [u for u in re.findall(r'https?://[^\s"\')]+', html) if "cdn" in u or "fonts.g" in u]
    print(f"OK → {OUT} ({OUT.stat().st_size // 1024} KB, CSS {len(css) // 1024} KB)")
    if left:
        print("Note: external resources remain:", left)
    return 0


if __name__ == "__main__":
    sys.exit(main())
