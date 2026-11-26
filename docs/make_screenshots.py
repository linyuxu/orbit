"""The README's pictures (docs/images/*.png), made from a demo, never from anyone's own Orbit.

A throwaway home (HOME in a temporary folder: ~/projects/shop-api and ~/projects/forecast, a notes vault), an Orbit
server of its own on another port, the fake Claude Code and Codex of the tests (tests/fake_*.py: no API calls), the
fake Claude playing the scripted turns below. Then a browser (Playwright, Chromium) takes the pictures.

Run: uv run --with playwright python docs/make_screenshots.py
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "docs" / "images"
PORT = 8797

SCRIPT = [
    {"match": "Refactor the order totals", "steps": [
        {"tool": "Read", "input": {"file_path": "app/orders.py"}, "out": "…"},
        {"tool": "Grep", "input": {"pattern": "def total", "path": "app"}, "out": "app/orders.py:14\napp/payments.py:31"},
        {"text": "Totals are computed twice, once in orders.py and again in payments.py, and the two disagree on rounding. I'll make one function the source of truth."},
        {"tool": "Edit", "input": {"file_path": "app/orders.py"}, "out": "ok"},
        {"tool": "Edit", "input": {"file_path": "app/payments.py"}, "out": "ok"},
        {"tool": "Bash", "input": {"command": "pytest -q tests/test_orders.py"}, "out": "12 passed in 0.41s"}],
     "answer": "Done: order totals now come from one place.\n\n"
               "**What changed**\n\n"
               "- `app/orders.py`: `order_total()` sums line items in `Decimal` and rounds once, half-even, at the end.\n"
               "- `app/payments.py`: `charge()` calls `order_total()` instead of its own float loop (it rounded each line, "
               "so a 3-line order could be off by a cent).\n"
               "- `tests/test_orders.py`: two new cases, a 3-line order and a discounted one.\n\n"
               "```python\ndef order_total(lines: list[Line]) -> Decimal:\n"
               "    raw = sum((l.price * l.qty for l in lines), Decimal(0))\n"
               "    return raw.quantize(CENT, rounding=ROUND_HALF_EVEN)\n```\n\n"
               "`pytest -q`: 12 passed. Nothing is committed yet: ± Review shows the diff."},
    {"match": "Add idempotency keys", "steps": [
        {"tool": "Read", "input": {"file_path": "app/payments.py"}, "out": "…"},
        {"tool": "Bash", "input": {"command": "pytest -q -k payments"}, "out": "running…"}],
     "answer": "Working on it.", "hold": 600},
    {"match": "Why did the weekly forecast", "steps": [
        {"tool": "Bash", "input": {"command": "python -m forecast.backtest --weeks 8"}, "out": "MAPE 6.1% → 11.4% (week 47)"},
        {"tool": "Read", "input": {"file_path": "data/holidays.csv"}, "out": "…"}],
     "answer": "Week 47 is Thanksgiving week and the holiday table stops at 2025, so the model treated it as an ordinary "
               "week. Extending `data/holidays.csv` brings the backtest back to **6.3%** MAPE. Want me to add a check "
               "that fails when the table is about to run out?"},
    {"match": "Summarize the open PRs", "steps": [
        {"tool": "Bash", "input": {"command": "gh pr list --state open"}, "out": "#41 #43 #44"}],
     "answer": "Three open PRs: **#41** caching for product search (approved, waiting on CI), **#43** a migration that "
               "adds `orders.refunded_at` (needs a review), **#44** a dependency bump (safe to merge)."},
]

FILES = {
    "projects/shop-api/README.md": "# shop-api\n\nOrders, payments and the product catalogue.\n",
    "projects/shop-api/pyproject.toml": "[project]\nname = \"shop-api\"\nversion = \"0.4.0\"\n",
    "projects/shop-api/app/__init__.py": "",
    "projects/shop-api/app/main.py": "from app import orders, payments\n\n\ndef create_app():\n    ...\n",
    "projects/shop-api/app/orders.py": ("from decimal import Decimal, ROUND_HALF_EVEN\n\nCENT = Decimal(\"0.01\")\n\n\n"
                                        "def order_total(lines):\n    total = 0.0\n    for l in lines:\n"
                                        "        total += round(l.price * l.qty, 2)\n    return total\n"),
    "projects/shop-api/app/payments.py": "from app.orders import order_total\n\n\ndef charge(order):\n    ...\n",
    "projects/shop-api/app/catalogue.py": "def search(q):\n    ...\n",
    "projects/shop-api/tests/test_orders.py": "def test_total():\n    ...\n",
    "projects/shop-api/tests/test_payments.py": "def test_charge():\n    ...\n",
    "projects/forecast/README.md": "# forecast\n\nWeekly demand forecasts.\n",
    "projects/forecast/forecast/model.py": "def fit(df):\n    ...\n",
    "projects/forecast/forecast/backtest.py": "def run(weeks=8):\n    ...\n",
    "projects/forecast/data/holidays.csv": "date,name\n2025-11-27,Thanksgiving\n",
    ".orbit/data/notes/Projects/shop-api.md": "# shop-api\n\n- [x] one source of truth for totals\n- [ ] idempotency keys\n",
    ".orbit/data/notes/Ideas.md": "# Ideas\n\nA nightly backtest that posts its MAPE to the group's home.\n",
}


def req(method, path, body=None):
    r = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method, headers={"Content-Type": "application/json"},
                               data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.loads(resp.read() or b"{}")


def until(fn, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.1)
    raise TimeoutError(fn)


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "Dev", "GIT_AUTHOR_EMAIL": "dev@example.com",
                        "GIT_COMMITTER_NAME": "Dev", "GIT_COMMITTER_EMAIL": "dev@example.com"})


def main() -> None:
    from playwright.sync_api import sync_playwright
    root = Path(os.path.realpath(tempfile.mkdtemp(prefix="orbit-demo-")))  # (resolved: /var → /private/var on macOS, so ~ shows)
    home = root / "home" / "dev"
    for rel, text in FILES.items():
        (home / rel).parent.mkdir(parents=True, exist_ok=True)
        (home / rel).write_text(text)
    shop, fc = home / "projects/shop-api", home / "projects/forecast"
    for p in (shop, fc):
        git(p, "init", "-q", "-b", "main"); git(p, "add", "."); git(p, "commit", "-qm", "first")
    (shop / "app/orders.py").write_text(FILES["projects/shop-api/app/orders.py"].replace(
        "    total = 0.0\n    for l in lines:\n        total += round(l.price * l.qty, 2)\n    return total\n",
        "    raw = sum((l.price * l.qty for l in lines), Decimal(0))\n    return raw.quantize(CENT, rounding=ROUND_HALF_EVEN)\n"))
    (shop / "tests/test_orders.py").write_text("def test_total():\n    ...\n\n\ndef test_three_lines():\n    ...\n")
    script = root / "script.json"
    script.write_text(json.dumps(SCRIPT))
    py = shlex.quote(sys.executable)
    env = {**os.environ, "HOME": str(home), "ORBIT_NO_NOTIFY": "1", "FAKE_SCRIPT": str(script), "FAKE_MODEL": "claude-opus-4-5",
           "ORBIT_CLAUDE_BIN": f"{py} {shlex.quote(str(REPO / 'tests/fake_claude.py'))}",
           "ORBIT_CODEX_BIN": f"{py} {shlex.quote(str(REPO / 'tests/fake_codex.py'))}",
           "ORBIT_CLAUDE_PROJECTS": str(root / "claude-projects"), "ORBIT_CODEX_SESSIONS": str(root / "codex-sessions"),
           "ORBIT_HARNESS_SERVER": "0"}
    for k in ("ORBIT_HOME", "ORBIT_STATE_DIR", "ORBIT_PORT", "ORBIT_VAULT"):
        env.pop(k, None)
    srv = subprocess.Popen([sys.executable, "-m", "orbit", "--port", str(PORT), "--state-dir", str(home / ".orbit")],
                           cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        until(lambda: _up())
        seeded = [x["id"] for x in req("GET", "/api/sessions")["groups"]]  # (a new Orbit's own: Main, Projects, …)
        g = {n: req("POST", "/api/groups", {"name": n})["id"] for n in ("Shop API", "Forecasting", "Ops")}
        for gid in seeded:
            req("POST", f"/api/groups/{gid}/delete")
        for n, color in (("Shop API", "#2a78d6"), ("Forecasting", "#1baf7a"), ("Ops", "#d29d00")):
            req("POST", f"/api/groups/{g[n]}/style", {"color": color})
        mk = lambda name, cwd, group, harness="claude": req("POST", "/api/sessions", {"cwd": str(cwd), "name": name, "group": g[group], "harness": harness})
        orders = mk("orders refactor", shop, "Shop API")
        idem = mk("idempotency", shop, "Shop API")
        prs = mk("PR triage", shop, "Shop API")
        fcs = mk("forecast drift", fc, "Forecasting")
        mk("feature ideas", fc, "Forecasting", "codex")
        mk("nightly checks", home / "projects", "Ops")
        send = lambda s, text: req("POST", f"/api/sessions/{s['id']}/send", {"text": text})
        send(orders, "Refactor the order totals so orders.py and payments.py agree; add tests for a 3-line order.")
        send(fcs, "Why did the weekly forecast get worse last week?")
        send(prs, "Summarize the open PRs and what each needs.")
        until(lambda: _done(orders) and _done(fcs))
        send(idem, "Add idempotency keys to POST /payments so a retried request never charges twice.")
        time.sleep(1.5)
        OUT.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            page = b.new_page(viewport={"width": 1440, "height": 880}, device_scale_factor=2)
            page.add_init_script("try { localStorage.setItem('orbit.capsFolded', 'false'); } catch (e) {}")
            page.goto(f"http://127.0.0.1:{PORT}/")
            page.wait_for_function("() => typeof S !== 'undefined' && S.groups && S.groups.length", timeout=20000)
            # 1. the main window: Groups, a conversation, ± Review beside it
            page.evaluate(f"switchGroup({json.dumps(g['Shop API'])}); switchTo({json.dumps(orders['id'])}); "
                          "S.left.shown = true; applyLeft(); setPanelTab('sessions'); 0")
            page.wait_for_timeout(800)
            page.evaluate("toggleReview()"); page.wait_for_timeout(2500)
            page.evaluate("focusMain()"); page.wait_for_timeout(300)
            page.screenshot(path=str(OUT / "main.png"))
            # 2. the Files panel filtered (still a tree), a file previewed beside
            page.evaluate("closeAllSides(); setPanelTab('files'); 0"); page.wait_for_timeout(800)
            page.evaluate("focusPanel(); S.filter = 'order'; renderLeft()"); page.wait_for_timeout(400)
            page.evaluate(f"openSide({{kind: 'file', path: {json.dumps(str(shop / 'app/orders.py'))}, preview: true}}, {{focus: false}})")
            page.wait_for_timeout(2000)
            page.screenshot(path=str(OUT / "files.png"))
            page.evaluate("S.filter = ''; renderLeft(); closeAllSides(); 0")
            # 3. ⌘O: groups first, then sessions with their status and agent
            page.evaluate("setPanelTab('sessions'); openFinder('Sessions')"); page.wait_for_timeout(800)
            page.screenshot(path=str(OUT / "open.png"))
            page.keyboard.press("Escape")
            # 4. the group's home
            page.evaluate("openHome()"); page.wait_for_timeout(1500)
            page.screenshot(path=str(OUT / "home.png"))
            # 5. the phone
            phone = b.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=3, is_mobile=True, has_touch=True)
            phone.goto(f"http://127.0.0.1:{PORT}/m/")
            phone.wait_for_timeout(2500)
            phone.screenshot(path=str(OUT / "phone.png"))
            b.close()
        print("wrote", sorted(p.name for p in OUT.glob("*.png")))
    finally:
        try:
            req("POST", f"/api/sessions/{idem['id']}/stop")
        except Exception:  # noqa: BLE001
            pass
        srv.terminate()
        try:
            srv.wait(10)
        except subprocess.TimeoutExpired:
            srv.kill()
        shutil.rmtree(root, ignore_errors=True)


def _up() -> bool:
    try:
        req("GET", "/api/sessions")
        return True
    except OSError:
        return False


def _done(s) -> bool:
    cs = req("GET", f"/api/sessions/{s['id']}/cards")["cards"]
    return bool(cs) and cs[-1]["state"] == "completed"


if __name__ == "__main__":
    main()
