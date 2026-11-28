"""The page itself, in real browsers (Chromium and WebKit): the paths that broke before and that only a browser shows.
A group's home stays in front after a reload; ⌘- goes back across groups; the icon picker searches (Chinese too) and
Enter takes the first match; a home widget is dragged by its head without selecting text; Space folds a group in the
left panel; the phone's conversation wears its group's tint.

Skipped without Playwright or its browsers. Run:
  uv run --with playwright python -m unittest discover -s tests -p test_ui_smoke.py -v
  (first time: uv run --with playwright playwright install chromium webkit)
ORBIT_UI_BROWSERS=chromium (or webkit) runs one of them.
"""

import json
import os
import time
import unittest
from pathlib import Path

import test_skeleton

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # (the suite runs without it: these are skipped)
    sync_playwright = None

ED = "#fbody .host:not([hidden]) .cm-content"  # (the file in front's editor; the composer has one too)
def png(path, w, h):
    """A plain PNG of w×h (a gradient), for the image viewer."""
    import struct, zlib
    rows = b"".join(b"\x00" + b"".join(bytes([x * 255 // w, y * 255 // h, 160]) for x in range(w)) for y in range(h))
    chunk = lambda k, d: struct.pack(">I", len(d)) + k + d + struct.pack(">I", zlib.crc32(k + d) & 0xffffffff)
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


READY = "() => typeof S !== 'undefined' && S.groups && S.groups.length > 0"  # (the groups come with the sessions)


@unittest.skipIf(sync_playwright is None, "Playwright is not installed (uv run --with playwright …)")
class UiSmokeTest(test_skeleton.SkeletonTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pw = sync_playwright().start()
        cls.browsers = {}
        for name in os.environ.get("ORBIT_UI_BROWSERS", "chromium,webkit").split(","):
            try:
                cls.browsers[name.strip()] = getattr(cls.pw, name.strip()).launch()
            except Exception:  # (not installed: `playwright install …`)
                pass
        if not cls.browsers:
            cls.pw.stop()
            super().tearDownClass()
            raise unittest.SkipTest("no Playwright browser installed (playwright install chromium webkit)")

    @classmethod
    def tearDownClass(cls):
        for b in cls.browsers.values():
            b.close()
        cls.pw.stop()
        super().tearDownClass()

    # ------------------------------------------------------------------ helpers
    def each(self):
        """(browser name, a fresh page on Orbit) per browser: its own context, so nothing is remembered between them.
        A failure names its browser (the page's title says which)."""
        for name, b in self.browsers.items():
            ctx = b.new_context(viewport={"width": 1280, "height": 860})
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            try:
                yield name, page
            finally:
                ctx.close()
            self.assertEqual(errors, [], f"page errors in {name}")

    def open(self, page, path="/"):
        page.goto(f"http://127.0.0.1:{self.port}{path}")
        page.wait_for_function(READY if path == "/" else "() => typeof S !== 'undefined'", timeout=15000)
        page.wait_for_timeout(300)

    def group(self, name, **style):
        code, g = self.req("POST", "/api/groups", {"name": name})
        self.assertEqual(code, 200, g)
        if style:
            self.req("POST", f"/api/groups/{g['id']}/style", style)
        return g["id"]

    def session_in(self, gid, name):
        s = self.new_session(name)
        self.req("POST", f"/api/sessions/{s['id']}/update", {"group": gid})
        return s["id"]

    def groups(self):
        return {g["id"]: g for g in self.req("GET", "/api/sessions")[1]["groups"]}

    # ------------------------------------------------------------------ tests
    def test_home_stays_in_front_after_reload(self):
        gid = self.group("Reloaded", color="#2a78d6")
        self.session_in(gid, "a session")
        state = "() => ({file: S.file, group: S.group, home: !!document.querySelector('#xview.home'), pill: document.querySelector('#grouppill').classList.contains('active')})"
        for name, page in self.each():
            self.open(page)
            page.evaluate(f"openHome({json.dumps(gid)})")
            page.wait_for_timeout(400)
            want = {"file": "orbit:home", "group": gid, "home": True, "pill": True}
            self.assertEqual(page.evaluate(state), want)
            page.reload()
            page.wait_for_function(READY, timeout=15000)
            page.wait_for_timeout(800)  # (the tabs' layout comes from the server after the page: it must not drop it)
            self.assertEqual(page.evaluate(state), want)

    def test_home_statistics(self):
        """A group's home starts with its statistics: this week's numbers and who asked, the days with requests, agent
        time by session; a hover on a day says its count."""
        gid = self.group("Counted home")
        sid = self.session_in(gid, "busy one")
        helper = self.session_in(gid, "helper")
        self.req("POST", f"/api/sessions/{sid}/send", {"text": "count the files"})
        self.assertTrue(self.wait_card(sid, 1, ("completed",)))
        self.req("POST", f"/api/sessions/{sid}/send", {"text": '(from session "helper") and the folders',
                                                       "origin": {"kind": "agent", "from": {"sid": helper, "name": "helper"}}})
        self.assertTrue(self.wait_card(sid, 2, ("completed",)))
        for name, page in self.each():
            self.open(page)
            page.evaluate(f"HS.data.clear(); openHome({json.dumps(gid)})")
            page.wait_for_selector(".kpis .kpi", timeout=10000)
            got = page.evaluate("""() => ({tiles: document.querySelectorAll('.kpis .kpi').length,
                first: document.querySelector('.kpis .kv').textContent, who: !!document.querySelector('.sbar .sseg'),
                days: document.querySelectorAll('svg.heat rect.hc').length, lit: document.querySelectorAll('svg.heat rect.hc:not(.s0)').length,
                titles: [...document.querySelectorAll('.hw header .t')].slice(0, 3).map(e => e.textContent)})""")
            self.assertEqual(got["titles"], ["This week", "Activity", "Agent time"], name)
            self.assertEqual((got["tiles"], got["first"], got["who"]), (5, "2", True), name)
            row = page.evaluate("[document.querySelector('.collab .crow').innerText.replace(/\\s+/g, ' '), document.querySelector('.collab .clink').dataset.hopen]")
            self.assertEqual(row, ["helper → busy one ×1", helper], name)  # (agents working together; a click opens the sender)
            self.assertGreater(got["days"], 70, name)
            self.assertEqual(got["lit"], 1, name)  # (today)
            cell = page.query_selector("svg.heat rect.hc:not(.s0)")
            bb = cell.bounding_box()
            page.mouse.move(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
            page.wait_for_timeout(200)
            self.assertIn("2 requests", page.evaluate("document.querySelector('#ctip').textContent"), name)

    def test_windows_each_look_at_their_own_group(self):
        """Several windows (?win=, the app's New Window): the tabs are shared, what a window looks at is its own and
        stays its own after a reload; ?group= starts it there. ⌘1 is the left panel; ⌘2 no longer switches groups."""
        a, b = self.group("Win A"), self.group("Win B")
        self.session_in(a, "in A"), self.session_in(b, "in B")
        for name, page in self.each():
            self.open(page)
            page.evaluate(f"switchGroup({json.dumps(a)})")
            page.wait_for_timeout(300)
            other = page.context.new_page()
            other.goto(f"http://127.0.0.1:{self.port}/?win=w7&group={b}")
            other.wait_for_function(READY, timeout=15000)
            other.wait_for_timeout(500)
            self.assertEqual(other.evaluate("[S.group, WIN, location.search]"), [b, "w7", "?win=w7"], name)  # (the address forgets the group)
            self.assertEqual(page.evaluate("S.group"), a, name)
            for p in (page, other):
                p.reload()
                p.wait_for_function(READY, timeout=15000)
                p.wait_for_timeout(500)
            self.assertEqual((page.evaluate("S.group"), other.evaluate("S.group")), (a, b), name)
            with page.context.expect_page() as opened:  # (a browser's new window: a tab of its own)
                page.evaluate("newWindow()")
            nw = opened.value
            nw.wait_for_function(READY, timeout=15000)
            nw.wait_for_timeout(400)
            self.assertEqual(nw.evaluate("S.group"), a, name)
            self.assertTrue(nw.evaluate("WIN.startsWith('w')"), name)
            nw.close(); other.close()
            shown = page.evaluate("S.left.shown")
            page.evaluate("deskKey('M:Digit1')")
            page.wait_for_timeout(200)
            self.assertNotEqual(page.evaluate("S.left.shown"), shown, name)
            page.evaluate("deskKey('M:Digit2')")
            page.wait_for_timeout(200)
            self.assertEqual(page.evaluate("S.group"), a, name)

    def test_ipad_touch(self):
        """On a touch screen (an iPad: Chromium emulating one, its touches sent as a finger's): a long press is the menu,
        held then moved a mouse's press-and-move (a drag), a double tap a double-click; upright, the left panel floats
        over the work and a tap on the scrim puts it away. A Mac (no touch) runs none of it."""
        gid = self.group("Touch")
        self.session_in(gid, "touched one")
        ua = "Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
        ctx = self.browsers["chromium"].new_context(viewport={"width": 834, "height": 1194}, is_mobile=True, has_touch=True, user_agent=ua)
        try:
            page = ctx.new_page()
            cdp = ctx.new_cdp_session(page)
            touch = lambda kind, x, y: cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": [] if kind == "touchEnd" else [{"x": x, "y": y}]})
            self.open(page)
            self.assertEqual(page.evaluate("[location.pathname, document.body.classList.contains('touch'), document.body.classList.contains('tnarrow')]"), ["/", True, True])
            page.evaluate(f"switchGroup({json.dumps(gid)}); S.left.shown = true; S.left.tab = 'sessions'; applyLeft(); renderLeft(); 0")
            page.wait_for_timeout(300)
            self.assertEqual(page.evaluate("getComputedStyle(document.querySelector('#left')).position"), "absolute")
            bb = page.query_selector("#plist .prow.ss").bounding_box()
            x, y = bb["x"] + 60, bb["y"] + bb["height"] / 2
            touch("touchStart", x, y); page.wait_for_timeout(650); touch("touchEnd", x, y); page.wait_for_timeout(300)
            self.assertTrue(page.evaluate("!!document.querySelector('.menu .row')"))  # (a long press: the row's menu)
            page.keyboard.press("Escape")
            page.evaluate("window.__ev = []; for (const t of ['mousedown', 'mousemove', 'mouseup']) addEventListener(t, e => { if (!e.isTrusted) __ev.push(t) }, true)")
            touch("touchStart", x, y); page.wait_for_timeout(650)
            for k in range(1, 5):
                touch("touchMove", x, y + k * 12); page.wait_for_timeout(30)
            touch("touchEnd", x, y + 48); page.wait_for_timeout(400)
            ev = page.evaluate("__ev")
            self.assertEqual((ev.count("mousedown"), ev.count("mouseup")), (1, 1), ev)
            self.assertGreaterEqual(ev.count("mousemove"), 3, ev)
            page.evaluate("S.left.shown = true; applyLeft(); 0"); page.wait_for_timeout(200)
            page.touchscreen.tap(800, 600); page.wait_for_timeout(300)  # (the scrim)
            self.assertFalse(page.evaluate("S.left.shown"))
            page.evaluate("window.__dd = 0; document.querySelector('#tabs').addEventListener('dblclick', () => __dd++)")
            tb = page.query_selector("#tabs .tab").bounding_box()
            tx, ty = tb["x"] + 30, tb["y"] + tb["height"] / 2
            page.touchscreen.tap(tx, ty); page.wait_for_timeout(120); page.touchscreen.tap(tx, ty); page.wait_for_timeout(400)
            self.assertEqual(page.evaluate("__dd"), 1)  # (a double tap: one double-click)
        finally:
            ctx.close()
        for name, page in self.each():  # (a Mac: nothing of it)
            self.open(page)
            self.assertEqual(page.evaluate("[document.body.classList.contains('touch'), !!document.querySelector('#lscrim')]"), [False, False], name)

    def test_back_goes_across_groups(self):
        a, b = self.group("Hist A"), self.group("Hist B")
        sa, sb = self.session_in(a, "in A"), self.session_in(b, "in B")
        for name, page in self.each():
            self.open(page)
            page.evaluate(f"switchGroup({json.dumps(a)})")
            page.wait_for_timeout(300)
            self.assertEqual(page.evaluate("[S.group, S.current]"), [a, sa])
            page.evaluate(f"switchGroup({json.dumps(b)})")
            page.wait_for_timeout(300)
            page.evaluate(f"openHome({json.dumps(b)})")
            page.wait_for_timeout(300)
            page.evaluate("focusMain()")
            page.keyboard.press("Meta+Minus")  # (B's home → B's session)
            page.wait_for_timeout(300)
            self.assertEqual(page.evaluate("[S.group, S.file, S.current]"), [b, None, sb])
            page.keyboard.press("Meta+Minus")  # (→ back into A)
            page.wait_for_timeout(300)
            self.assertEqual(page.evaluate("[S.group, S.current]"), [a, sa])
            page.keyboard.press("Meta+Equal")
            page.wait_for_timeout(300)
            self.assertEqual(page.evaluate("S.group"), b)

    def test_icon_picker_searches_and_enter_picks(self):
        gid = self.group("Icons")
        for name, page in self.each():
            self.open(page)
            page.evaluate(f"openHome({json.dumps(gid)})")
            page.wait_for_timeout(400)
            page.click("#xbody [data-hact=style]")
            page.wait_for_selector(".ej", timeout=10000)
            self.assertGreater(page.evaluate("document.querySelectorAll('.ej').length"), 1000)
            self.assertIn("Suggested", page.evaluate("[...document.querySelectorAll('.ej-chip')].map(x => x.textContent).join(' ')"))
            for q, first in (("火箭", "🚀"), ("rocket", "🚀"), ("股票", "💹")):
                page.fill(".ej-q", q)
                page.wait_for_timeout(250)
                self.assertEqual(page.evaluate("document.querySelector('.ej').textContent"), first, q)
            page.fill(".ej-q", "猫")
            page.wait_for_timeout(250)
            self.assertNotIn("🔥", page.evaluate("[...document.querySelectorAll('.ej')].map(x => x.textContent).join('')"))
            page.fill(".ej-q", "火箭")
            page.wait_for_timeout(250)
            page.press(".ej-q", "Enter")  # (the first match, the dialog still open)
            page.wait_for_timeout(200)
            self.assertTrue(page.is_visible(".dialog"))
            self.assertEqual(page.inner_text(".ej-cur"), "🚀")
            page.click(".dialog [data-ok]")
            page.wait_for_timeout(600)
            self.assertEqual(self.groups()[gid].get("icon"), "🚀")
            self.req("POST", f"/api/groups/{gid}/style", {"icon": ""})

    def test_widget_drag_by_its_head_selects_no_text(self):
        gid = self.group("Dragged")
        self.session_in(gid, "a session")
        start = [{"id": "sessions", "kind": "sessions", "span": 3}, {"id": "activity", "kind": "activity", "span": 3},
                 {"id": "pins", "kind": "pins", "span": 6}]
        for name, page in self.each():
            self.req("POST", f"/api/groups/{gid}/home", {"widgets": start})
            self.open(page)
            page.evaluate(f"openHome({json.dumps(gid)})")
            page.wait_for_timeout(500)
            head = page.locator('#xbody .hw[data-w="sessions"] > header').bounding_box()
            other = page.locator('#xbody .hw[data-w="activity"]').bounding_box()
            x, y = head["x"] + 40, head["y"] + head["height"] / 2
            page.mouse.move(x, y)
            page.mouse.down()
            to = other["x"] + other["width"] * 0.8
            for i in range(1, 21):  # (through the text of the cards on the way, ending on the head's line)
                page.mouse.move(x + (to - x) * i / 20, y + (0 if i == 20 else (i % 3) * 20))
                page.wait_for_timeout(15)
            self.assertEqual(page.evaluate("String(getSelection())"), "", "text got selected while dragging")
            page.mouse.up()
            page.wait_for_timeout(600)
            order = [w["id"] for w in self.groups()[gid]["home"]["widgets"]]
            self.assertEqual(order, ["activity", "sessions", "pins"], name)

    def test_a_card_gets_its_own_look(self):
        gid = self.group("Looks")
        self.session_in(gid, "a session")
        start = [{"id": "sessions", "kind": "sessions", "span": 3}, {"id": "pins", "kind": "pins", "span": 3, "icon": "📌", "color": "#1baf7a"}]
        for name, page in self.each():
            self.req("POST", f"/api/groups/{gid}/home", {"widgets": start})
            self.open(page)
            page.evaluate(f"openHome({json.dumps(gid)})")
            page.wait_for_timeout(500)
            pins = '#xbody .hw[data-w="pins"]'
            self.assertIn("wtint", page.get_attribute(pins, "class"))
            self.assertEqual(page.inner_text(f"{pins} header .gicon"), "📌")
            page.click('#xbody .hw[data-w="sessions"] [data-wmenu]')
            page.click("text=Icon and background…")
            page.wait_for_selector(".ej", timeout=10000)
            page.fill(".ej-q", "火箭")
            page.wait_for_timeout(250)
            page.press(".ej-q", "Enter")
            page.click('.dialog .lk-c[data-c="#eb6834"]')
            page.click(".dialog [data-ok]")
            page.wait_for_timeout(700)
            w = next(x for x in self.groups()[gid]["home"]["widgets"] if x["id"] == "sessions")
            self.assertEqual((w.get("icon"), w.get("color")), ("🚀", "#eb6834"), name)
            self.assertEqual(page.inner_text('#xbody .hw[data-w="sessions"] header .gicon'), "🚀")

    def test_a_card_resizes_with_the_mouse(self):
        gid = self.group("Resized")
        self.session_in(gid, "a session")
        start = [{"id": "sessions", "kind": "sessions", "span": 3}, {"id": "pins", "kind": "pins", "span": 3}]
        card = '#xbody .hw[data-w="sessions"]'
        widget = lambda: next(x for x in self.groups()[gid]["home"]["widgets"] if x["id"] == "sessions")
        for name, page in self.each():
            self.req("POST", f"/api/groups/{gid}/home", {"widgets": start})
            self.open(page)
            page.evaluate(f"openHome({json.dumps(gid)})")
            page.wait_for_timeout(500)
            b = page.locator(card).bounding_box()
            x, y = b["x"] + b["width"] - 1, b["y"] + b["height"] / 2  # (its right edge, to the row's end)
            page.mouse.move(x, y)
            page.mouse.down()
            for i in range(1, 16):
                page.mouse.move(x + 560 * i / 15, y + (i % 3) * 5)
                page.wait_for_timeout(10)
            self.assertEqual(page.evaluate(f"document.querySelector('{card}').dataset.size"), "The whole row")
            self.assertEqual(page.evaluate("String(getSelection())"), "")
            page.mouse.up()
            page.wait_for_timeout(600)
            self.assertEqual(widget()["span"], 6, name)
            b = page.locator(card).bounding_box()
            x, y = b["x"] + b["width"] / 2, b["y"] + b["height"] - 1  # (its bottom edge, down 150px)
            h0 = page.evaluate(f"document.querySelector('{card} .wb').offsetHeight")
            page.mouse.move(x, y)
            page.mouse.down()
            for i in range(1, 11):
                page.mouse.move(x, y + 150 * i / 10)
                page.wait_for_timeout(10)
            page.mouse.up()
            page.wait_for_timeout(600)
            self.assertAlmostEqual(widget()["h"], h0 + 150, delta=3)
            page.dblclick(f"{card} .hrz.y")  # (its natural height again)
            page.wait_for_timeout(600)
            self.assertNotIn("h", widget())

    def test_quote_from_a_file_goes_to_the_session_beside(self):
        gid = self.group("Quoting")
        main, beside = self.session_in(gid, "Main one"), self.session_in(gid, "Beside one")
        f = os.path.join(self.work, "quoted.md")
        Path(f).write_text("line one\nline two\nline three\n")
        side_input = "(() => { const fr = sideFrame(); return fr ? fr.contentWindow.eval('typeof input === \\'undefined\\' ? null : input.value') : null; })()"
        for name, page in self.each():
            self.open(page)
            page.evaluate(f"switchGroup({json.dumps(gid)}); openFile({json.dumps(f)}, {{pinned: true}})")
            page.wait_for_timeout(1000)
            page.evaluate("closeAllSides()")  # (the tabs' layout is shared: the other browser's side tabs are here too)
            page.evaluate(f"openSide({{kind: 'session', sid: {json.dumps(beside)}}}, {{focus: false}})")
            page.wait_for_timeout(2500)
            page.evaluate(f"(() => {{ const v = EDITORS.get({json.dumps(f)}).ed.view; v.focus(); v.dispatch({{selection: {{anchor: 9, head: 17}}}}); }})()")
            page.wait_for_timeout(400)
            page.dispatch_event("#quotebtn", "mousedown")  # (the Quote button at the selection)
            page.wait_for_timeout(1000)
            self.assertEqual(page.evaluate(side_input), "> quoted.md:2\n> line two\n\n".replace("quoted.md", page.evaluate(f"agentPath({json.dumps(f)})")), name)
            self.assertTrue(page.evaluate("sideFocused()"))
            self.assertEqual(page.evaluate(f"S.drafts[{json.dumps(main)}] || ''"), "")  # (not the main session's)
            page.evaluate("closeAllSides()")  # none beside: a menu; the chosen one opens beside with the quote
            page.wait_for_timeout(400)
            page.evaluate("quoteBeside({text: 'line three', ref: 'quoted.md:3', at: () => null, file: true})")
            page.wait_for_timeout(300)
            page.click(".menu >> text=Main one")
            page.wait_for_timeout(3000)
            self.assertEqual(page.evaluate("S.side && S.side.sid"), main)
            self.assertEqual(page.evaluate(side_input), "> quoted.md:3\n> line three\n\n", name)

    def test_lightbox_zooms_pans_and_closes(self):
        pic = os.path.join(self.tmp.name, "wide.png")
        png(pic, 1600, 400)  # (wide: zoomed in a little, dark above and below it)
        state = "[+LB.s.toFixed(3), +LB.fit.toFixed(3), lbOn()]"
        wheel = "LB.el.dispatchEvent(new WheelEvent('wheel', {deltaX: %s, deltaY: %s, ctrlKey: %s, clientX: 400, clientY: 300, bubbles: true, cancelable: true}))"
        for name, page in self.each():
            self.open(page)
            page.evaluate(f"openLightbox(rawUrl({json.dumps(pic)}))")
            page.wait_for_timeout(500)
            s, fit, on = page.evaluate(state)
            self.assertEqual((s, on), (fit, True))
            page.evaluate(wheel % (0, -100, "true"))  # (a pinch: ctrl + wheel)
            s2 = page.evaluate("LB.s")
            self.assertGreater(s2, fit * 2)
            x0 = page.evaluate("LB.x")
            page.evaluate(wheel % (50, 0, "false"))  # (two fingers: a pan)
            self.assertLess(page.evaluate("LB.x"), x0)
            # WebKit's pinch: gesture events, and the ctrl + wheel twins ignored meanwhile (once, not twice)
            ratio = page.evaluate("""(() => { const ev = n => { const e = new Event(n, {bubbles: true, cancelable: true});
                Object.defineProperty(e, 'scale', {value: 1.5}); Object.defineProperty(e, 'clientX', {value: 300}); Object.defineProperty(e, 'clientY', {value: 200}); return e; };
                const s0 = LB.s; LB.el.dispatchEvent(ev('gesturestart')); LB.el.dispatchEvent(ev('gesturechange'));
                LB.el.dispatchEvent(new WheelEvent('wheel', {deltaY: -100, ctrlKey: true, clientX: 300, clientY: 200, bubbles: true, cancelable: true}));
                LB.el.dispatchEvent(ev('gestureend')); return +(LB.s / s0).toFixed(3); })()""")
            self.assertEqual(ratio, 1.5)
            page.mouse.click(640, 430)  # (on the zoomed picture: it stays)
            page.wait_for_timeout(400)
            self.assertTrue(page.evaluate("lbOn()"))
            page.keyboard.press("Digit0")
            self.assertEqual(page.evaluate(state)[:2], [fit, fit])
            page.keyboard.press("Equal")
            self.assertGreater(page.evaluate("LB.s"), fit)
            page.keyboard.press("Digit0")
            page.mouse.dblclick(640, 430)  # (fit ↔ 2.5×)
            page.wait_for_timeout(400)
            self.assertAlmostEqual(page.evaluate("LB.s"), fit * 2.5, places=2)
            page.keyboard.press("Minus")
            page.keyboard.press("Minus")
            page.keyboard.press("Minus")
            page.wait_for_timeout(100)
            page.mouse.click(3, 3)  # (the dark area: closes, zoomed or not)
            page.wait_for_timeout(500)
            self.assertFalse(page.evaluate("lbOn()"), name)
            page.evaluate(f"openLightbox(rawUrl({json.dumps(pic)}))")
            page.wait_for_timeout(300)
            page.keyboard.press("Escape")
            self.assertFalse(page.evaluate("lbOn()"))

    def test_a_picture_window_open_in_the_app_shows_in_the_lightbox(self):
        pic = os.path.join(self.tmp.name, "small.png")
        png(pic, 200, 100)
        for name, b in self.browsers.items():
            ctx = b.new_context(viewport={"width": 1280, "height": 860})
            ctx.add_init_script("window.__ORBIT_APP__ = true;")  # (as in the Mac app's WebView, where window.open does nothing)
            page = ctx.new_page()
            try:
                self.open(page)
                page.evaluate(f"window.open(rawUrl({json.dumps(pic)}))")
                page.wait_for_timeout(400)
                self.assertTrue(page.evaluate("lbOn()"), name)
                self.assertIn("small.png", page.evaluate("LB.img.src"))
            finally:
                ctx.close()

    def test_a_file_tab_moves_to_another_group(self):
        a, b = self.group("Tabs A"), self.group("Tabs B")
        self.session_in(a, "in A")
        self.session_in(b, "in B")
        f = os.path.join(self.work, "moved.txt")
        Path(f).write_text("hello\n")
        tabs = lambda page, g: page.evaluate(f"(S.ftabs[{json.dumps(g)}] || []).map(t => t.path)")
        for name, page in self.each():
            self.open(page)
            page.evaluate(f"switchGroup({json.dumps(a)}); openFile({json.dumps(f)}, {{pinned: true}})")
            page.wait_for_timeout(800)
            page.evaluate(f"moveFileTab({json.dumps(f)}, {json.dumps(a)}, {json.dumps(b)})")  # (its menu's Move to)
            page.wait_for_timeout(300)
            self.assertEqual((tabs(page, a), tabs(page, b)), ([], [f]), name)
            self.assertNotEqual(page.evaluate("S.file"), f)  # (it left the group in front)
            # by a drag in the Groups panel: its row under the other group's head
            page.evaluate("S.left.shown = true; applyLeft(); setPanelTab('sessions'); focusPanel()")
            page.wait_for_timeout(300)
            row = page.locator(f"#plist .prow[data-k={json.dumps('f:' + b + ':' + f)}]")
            head = page.locator(f"#plist .prow[data-k={json.dumps('g:' + a)}]")
            rb, hb = row.bounding_box(), head.bounding_box()
            page.mouse.move(rb["x"] + 60, rb["y"] + rb["height"] / 2)
            page.mouse.down()
            ty = hb["y"] + hb["height"] * 0.9
            for i in range(1, 16):
                page.mouse.move(rb["x"] + 60, rb["y"] + rb["height"] / 2 + (ty - rb["y"] - rb["height"] / 2) * i / 15)
                page.wait_for_timeout(15)
            page.mouse.up()
            page.wait_for_timeout(600)
            self.assertEqual((tabs(page, a), tabs(page, b)), ([f], []), name)
            page.evaluate(f"closeGroupTab({{gid: {json.dumps(a)}, path: {json.dumps(f)}}})")  # (the next browser starts as this one did)

    def test_open_finds_notes_by_title_and_text(self):
        code, n = self.req("POST", "/api/notes/create", {"name": "Projects/Quarterly plan"})
        self.assertEqual(code, 200, n)
        self.req("POST", "/api/file", {"path": n["path"], "text": "# Quarterly plan\n\nShip the zebra migration first.\n", "force": True})
        for name, page in self.each():
            self.open(page)
            page.evaluate("openFinder('Notes')")
            page.wait_for_selector(".finder .ptab.cur")
            self.assertEqual(page.inner_text(".finder .ptab.cur"), "Notes")
            page.fill(".finder .fq", "quarterly")
            page.wait_for_timeout(300)
            self.assertIn("Quarterly plan", page.inner_text(".finder .flist .frow"), name)
            page.fill(".finder .fq", "zebra")  # (in its text only: the server's search)
            page.wait_for_timeout(900)
            self.assertIn("Search in Notes", page.inner_text(".finder .flist"))
            self.assertIn("zebra", page.inner_text(".finder .flist").lower())
            page.press(".finder .fq", "Escape")
            page.evaluate("BINDINGS.get('MS:KeyF')()")
            page.wait_for_selector(".finder .ptab.cur")
            self.assertEqual(page.inner_text(".finder .ptab.cur"), "History")  # (⌘⇧F still opens History)
            page.press(".finder .fq", "Escape")

    def test_a_site_beside_with_its_bar(self):
        site = f"http://localhost:{self.port}/api/version"  # (another origin than the page's 127.0.0.1)
        for name, page in self.each():
            self.open(page)
            page.evaluate("closeAllSides()")
            page.evaluate("openFinder(0)")
            page.wait_for_selector(".finder .fq")
            self.assertEqual(page.evaluate("urlLead('example.com/a').map(x => x.detail)"), ["https://example.com/a"] * 2)  # (a bare host: https)
            self.assertEqual(page.evaluate("urlLead('notes.md').length"), 0)  # (a file's name is no address)
            page.fill(".finder .fq", site)  # (a web address typed: Open beside first)
            page.wait_for_timeout(300)
            self.assertEqual(page.inner_text(".finder .flist .frow .fl"), "Open beside")
            page.press(".finder .fq", "Enter")
            page.wait_for_timeout(1200)
            self.assertEqual(page.evaluate("S.side && [S.side.kind, S.side.url]"), ["web", site])
            page.evaluate("closeAllSides()")
            page.evaluate(f"openSide({{kind: 'web', url: {json.dumps(site)}, title: 'localhost'}}, {{focus: false}})")
            page.wait_for_timeout(1200)
            fr = page.locator("#sideframes iframe:not([hidden])")
            self.assertIn("allow-same-origin", fr.get_attribute("sandbox"))  # (a site keeps its own origin: its sign-in)
            self.assertEqual(fr.get_attribute("referrerpolicy"), "no-referrer")
            self.assertTrue(page.is_visible("#sideweb"))
            self.assertIn(f"localhost:{self.port}", page.inner_text("#sideweb .sw-host"))
            page.evaluate("window.__copied = null; copyText = t => { window.__copied = t; }")
            page.click("#sideweb [data-sw=copy]")
            self.assertEqual(page.evaluate("window.__copied"), site)
            page.evaluate("closeAllSides()")
            code = self.req("POST", "/api/show", {"html": "<p>mine</p>", "title": "Mine"})[1]  # (an agent's page: the strict sandbox, no bar)
            page.evaluate(f"openSide({{kind: 'web', url: {json.dumps(code['url'])}, title: 'Mine'}}, {{focus: false}})")
            page.wait_for_timeout(800)
            self.assertNotIn("allow-same-origin", page.locator("#sideframes iframe:not([hidden])").get_attribute("sandbox"))
            self.assertFalse(page.is_visible("#sideweb"), name)
            page.evaluate("closeAllSides()")

    def test_all_files_finds_other_folders_and_the_home(self):
        from orbit.workspace import findfiles
        home = Path(self.tmp.name) / "findhome"
        (home / "papers").mkdir(parents=True, exist_ok=True)
        (home / "papers" / "home_only_draft.txt").write_text("from home\n")
        other = Path(self.tmp.name) / "otherproj"
        (other / "src").mkdir(parents=True, exist_ok=True)
        (other / "src" / "other_feature.py").write_text("x = 1\n")
        self.session_in(self.group("Elsewhere"), "here")
        code, s2 = self.req("POST", "/api/sessions", {"cwd": str(other), "name": "Other project"})
        self.assertEqual(code, 200, s2)
        code, n = self.req("POST", "/api/notes/create", {"name": "Finder note"})
        os.environ[findfiles.HOME_ENV] = str(home)  # (never the real home)
        findfiles._TREES.clear()
        try:
            for name, page in self.each():
                self.open(page)
                page.evaluate("openFinder('All Files')")
                page.wait_for_selector(".finder .ptab.cur")
                self.assertEqual(page.inner_text(".finder .ptab.cur"), "All Files")
                page.fill(".finder .fq", "other_feature")  # (another session's folder)
                page.wait_for_selector(".finder .flist .frow .fl")
                page.wait_for_timeout(500)
                self.assertEqual(page.inner_text(".finder .flist .frow .fl"), "other_feature.py", name)
                page.press(".finder .fq", "Enter")
                page.wait_for_timeout(800)
                self.assertEqual(page.evaluate("S.file"), os.path.realpath(other / "src" / "other_feature.py"))
                page.evaluate("openFinder('All Files')")
                page.wait_for_selector(".finder .fq")
                page.fill(".finder .fq", "home_only")  # (the home)
                page.wait_for_timeout(900)
                self.assertEqual(page.inner_text(".finder .flist .frow .fl"), "home_only_draft.txt")
                self.assertIn("home", page.inner_text(".finder .flist .frow .tag"))
                page.press(".finder .fq", "Enter")
                page.wait_for_timeout(800)
                self.assertEqual(page.evaluate("S.file"), str(home / "papers" / "home_only_draft.txt"))
                page.evaluate("BINDINGS.get('MS:KeyF')()")  # (⌘⇧F: still History)
                page.wait_for_selector(".finder .ptab.cur")
                self.assertEqual(page.inner_text(".finder .ptab.cur"), "History")
                page.press(".finder .fq", "Escape")
                page.evaluate(f"openFile({json.dumps(n['path'])}, {{pinned: true}})")  # (⌘O in a note: Notes)
                page.wait_for_timeout(800)
                page.evaluate("BINDINGS.get('M:KeyO')()")
                page.wait_for_selector(".finder .ptab.cur")
                self.assertEqual(page.inner_text(".finder .ptab.cur"), "Notes")
                page.press(".finder .fq", "Escape")
        finally:
            os.environ.pop(findfiles.HOME_ENV, None)

    def test_the_files_panel_has_every_folder_and_the_places(self):
        a, b = self.group("Files A"), self.group("Files B")
        roots = {}
        for name, g in (("fa-one", a), ("fa-two", a), ("fb-three", b)):
            d = Path(self.work) / name
            (d / "src").mkdir(parents=True, exist_ok=True)
            (d / "src" / f"{name}.py").write_text("x = 1\n")
            (d / "README.md").write_text("# r\n")
            code, sess = self.req("POST", "/api/sessions", {"cwd": str(d), "name": name})
            self.req("POST", f"/api/sessions/{sess['id']}/update", {"group": g})
            roots[name] = (sess["id"], str(d))
        kinds = lambda page: page.evaluate("PROWS.filter(r => r.kind === 'root' || r.kind === 'fsec').map(r => r.kind === 'fsec' ? '#' + r.name : r.name + (r.open ? '+' : ''))")
        for name, page in self.each():
            self.open(page)
            page.evaluate(f"switchTo({json.dumps(roots['fa-one'][0])})")
            page.wait_for_timeout(400)
            page.evaluate("S.follow = true; S.left.shown = true; applyLeft(); setPanelTab('files'); focusPanel()")
            page.wait_for_timeout(1200)
            ks = kinds(page)
            self.assertEqual(ks[:3], ["#Files A", "fa-one+", "fa-two"], name)  # (the group's folders, the current one first and open)
            self.assertIn("#Other groups", ks)
            self.assertNotIn("fb-three", ks)  # (folded)
            self.assertIn("#Places", ks)
            self.assertIn("Home", ks)
            page.click("#plist .prow.fsec.fold")  # (Other groups unfolds)
            page.wait_for_timeout(500)
            self.assertIn("fb-three", kinds(page))
            two = roots["fa-two"][1]
            page.evaluate(f"S.pcursor = 'r:' + {json.dumps(two)}; renderLeft()")
            page.keyboard.press("ArrowRight")  # (open its tree)
            page.wait_for_timeout(800)
            self.assertEqual(page.evaluate(f"PROWS.filter(r => r.root === {json.dumps(two)} && r.kind !== 'root').map(r => r.rel)"), ["src", "README.md"])
            page.keyboard.press("ArrowDown")
            page.keyboard.press("ArrowRight")
            page.keyboard.press("ArrowDown")
            page.keyboard.press("Enter")  # (its file opens: another session's folder)
            page.wait_for_timeout(800)
            self.assertEqual(page.evaluate("S.file"), os.path.realpath(two) + "/src/fa-two.py")
            page.evaluate("focusPanel()")
            page.keyboard.press("ArrowLeft")
            self.assertTrue(page.evaluate("S.pcursor").endswith("|src"))  # (← up a level)
            page.evaluate("focusMain(); followPanel()")  # (follow: the file in front, in its root)
            self.assertEqual(page.evaluate("S.pcursor"), "f:" + os.path.realpath(two) + "|src/fa-two.py")
            page.evaluate("focusPanel()")
            page.evaluate("S.pcursor = 'r:~'; renderLeft()")
            page.keyboard.press("ArrowRight")  # (this Mac's home: a level at a time)
            page.wait_for_timeout(1200)
            self.assertGreater(page.evaluate("PROWS.filter(r => r.kind === 'ldir' || r.kind === 'lfile').length"), 0)
            page.keyboard.press("ArrowLeft")
            page.keyboard.type("fa-")  # (the open folders' files together, each with its root)
            page.wait_for_timeout(400)
            hits = page.evaluate("PROWS.map(r => r.rel)")
            self.assertIn("src/fa-one.py", hits)
            self.assertIn("src/fa-two.py", hits)
            page.keyboard.press("Escape")

    def test_follow_follows_the_focused_pane(self):
        g = self.group("Follow")
        roots = {}
        for name in ("fo-main", "fo-side"):
            d = Path(self.work) / name
            (d / "docs").mkdir(parents=True, exist_ok=True)
            (d / "docs" / "communication.md").write_text("# c\n\nhello\n")
            code, sess = self.req("POST", "/api/sessions", {"cwd": str(d), "name": name})
            self.req("POST", f"/api/sessions/{sess['id']}/update", {"group": g})
            roots[name] = (sess["id"], os.path.realpath(d))
        side_file = roots["fo-side"][1] + "/docs/communication.md"
        for name, page in self.each():
            self.open(page)
            page.evaluate(f"switchTo({json.dumps(roots['fo-main'][0])})")
            page.wait_for_timeout(400)
            page.evaluate("closeAllSides(); S.follow = true; S.left.shown = true; applyLeft(); setPanelTab('files')")
            page.wait_for_timeout(800)
            page.evaluate("focusMain(); followPanel()")
            self.assertEqual(page.evaluate("S.pcursor"), "r:" + roots["fo-main"][1], name)  # (the main session's folder)
            page.evaluate(f"openSide({{kind: 'file', path: {json.dumps(side_file)}}}, {{focus: true}})")  # (a file beside, with the keyboard)
            page.wait_for_timeout(2000)
            self.assertTrue(page.evaluate("sideFocused()"))
            self.assertEqual(page.evaluate("S.pcursor"), "f:" + roots["fo-side"][1] + "|docs/communication.md", name)
            self.assertEqual(page.evaluate("filesRoots().cur"), roots["fo-side"][1])
            page.click("#ptabs [data-ptab=files]")  # (from the side tab to the left panel: the side's file stays the one shown)
            page.wait_for_timeout(500)
            self.assertEqual(page.evaluate("S.pcursor"), "f:" + roots["fo-side"][1] + "|docs/communication.md", name)
            page.evaluate("focusMain()")  # (back to the main pane: its folder again)
            page.wait_for_timeout(500)
            self.assertEqual(page.evaluate("S.pcursor"), "r:" + roots["fo-main"][1], name)
            page.evaluate("setPanelTab('sessions')")  # (the Groups tab: the session beside, when it has the keyboard)
            page.evaluate(f"openSide({{kind: 'session', sid: {json.dumps(roots['fo-side'][0])}}}, {{focus: true}})")
            page.wait_for_timeout(2000)
            self.assertEqual(page.evaluate("S.pcursor"), f"s:{g}:{roots['fo-side'][0]}", name)
            page.evaluate("closeAllSides(); setPanelTab('files')")

    def test_cmd_shift_o_goes_to_an_agent_page_beside_and_back(self):
        g = self.group("Side keys")
        sid = self.session_in(g, "main")
        url = self.req("POST", "/api/show", {"html": "<h1>agent page</h1>", "title": "Agent page"})[1]["url"]
        for name, page in self.each():
            self.open(page)
            page.evaluate(f"switchTo({json.dumps(sid)}); closeAllSides()")
            page.wait_for_timeout(300)
            page.evaluate(f"openSide({{kind: 'web', url: {json.dumps(url)}, title: 'Agent page'}}, {{focus: false}})")
            page.wait_for_timeout(1200)
            page.evaluate("focusMain()")
            page.keyboard.press("Meta+Shift+KeyO")
            page.wait_for_timeout(300)
            self.assertTrue(page.evaluate("sideFocused()"), name)
            page.keyboard.press("Meta+Shift+KeyO")  # (pressed in the agent's page: its key bridge brings it to Orbit)
            page.wait_for_timeout(300)
            self.assertFalse(page.evaluate("sideFocused()"), name)
            page.evaluate("closeAllSides()")

    def test_open_files_has_every_session_folder(self):
        a, b = self.group("Open A"), self.group("Open B")
        roots = {}
        for name, g in (("op-here", a), ("op-there", b)):
            d = Path(self.work) / name
            (d / "lib").mkdir(parents=True, exist_ok=True)
            (d / "lib" / "shared_util.py").write_text("x = 1\n")
            (d / f"{name}-only.txt").write_text("y\n")
            (d / f"{name.replace('-', '')}_pkg" / "mod").mkdir(parents=True, exist_ok=True); (d / f"{name.replace('-', '')}_pkg" / "mod" / "m.py").write_text("z\n")
            code, sess = self.req("POST", "/api/sessions", {"cwd": str(d), "name": name})
            self.req("POST", f"/api/sessions/{sess['id']}/update", {"group": g})
            roots[name] = (sess["id"], os.path.realpath(d))
        for name, page in self.each():
            self.open(page)
            page.evaluate(f"switchTo({json.dumps(roots['op-here'][0])})")
            page.wait_for_timeout(400)
            page.evaluate("openFinder('Files & Folders')")
            page.wait_for_selector(".finder .ptab.cur")
            page.wait_for_timeout(800)
            page.fill(".finder .fq", "op-there-only")  # (a file of another group's session folder)
            page.wait_for_timeout(300)
            self.assertIn("op-there-only.txt", page.inner_text(".finder .flist .frow"), name)
            page.fill(".finder .fq", "shared_util")  # (in both: this session's folder first)
            page.wait_for_timeout(300)
            self.assertIn("op-here/lib", page.inner_text(".finder .flist .frow"))
            page.fill(".finder .fq", "op-there shared")  # (the folder's name counts)
            page.wait_for_timeout(300)
            self.assertIn("op-there/lib", page.inner_text(".finder .flist .frow"))
            page.press(".finder .fq", "Enter")
            page.wait_for_timeout(800)
            self.assertEqual(page.evaluate("S.file"), roots["op-there"][1] + "/lib/shared_util.py")
            # a folder: the Files panel on it, open, the keyboard there
            page.evaluate(f"switchTo({json.dumps(roots['op-here'][0])}); openFinder('Files & Folders')")
            page.wait_for_timeout(600)
            page.fill(".finder .fq", "mod")
            page.wait_for_timeout(300)
            self.assertIn("folder", page.inner_text(".finder .flist .frow"), name)
            self.assertIn("ophere_pkg", page.inner_text(".finder .flist .frow"), name)  # (this session's folder first)
            page.press(".finder .fq", "Enter")
            here, k = roots["op-here"][1], f"d:{roots['op-here'][1]}|ophere_pkg/mod"
            page.wait_for_function(f"() => S.left.shown && S.left.tab === 'files' && S.pcursor === {json.dumps(k)}", timeout=5000)  # (under this session's folder, nested in another's or not)
            page.wait_for_selector(f"#plist .prow.kc[data-k={json.dumps(k)}]")
            self.assertTrue(page.evaluate(f"PROWS.some(r => r.k === {json.dumps(f'f:{here}|ophere_pkg/mod/m.py')})"), name)  # (open, its folders on the way too)
            self.assertEqual(page.evaluate("document.activeElement && document.activeElement.id"), "plist", name)

    def test_send_the_input_to_another_session(self):
        g = self.group("Send to")
        a, b = self.session_in(g, "st-source"), self.session_in(g, "st-target")
        for name, page in self.each():
            self.open(page)
            page.evaluate(f"switchTo({json.dumps(a)})")
            page.wait_for_timeout(400)
            words = f"喝杯水 · {name}"
            page.evaluate(f"input.focus(); input.insertAt({json.dumps(words)}, 0)")
            page.keyboard.press("Meta+Alt+Enter")  # (to another session: a menu of them)
            page.wait_for_selector(".menu")
            page.click(".menu >> text=st-target")
            page.wait_for_timeout(800)
            card = self.req("GET", f"/api/sessions/{b}/cards")[1]["cards"][-1]
            self.assertEqual(card["text"], words, name)  # (as typed: no "(from session …)")
            self.assertEqual(card["origin"]["label"], "You, from st-source")
            self.assertEqual(page.evaluate("[S.current, input.value]"), [a, ""])  # (this tab stays; its input is empty)
            page.evaluate(f"input.insertAt('second', 0)")
            page.click("#send", button="right")  # (the send button's right-click: the same menu)
            page.wait_for_selector(".menu")
            self.assertIn("st-target", page.inner_text(".menu"))
            page.keyboard.press("Escape")
            page.evaluate("input.value = ''; saveDraft()")

    def test_space_folds_a_group_in_the_left_panel(self):
        gid = self.group("Foldme")
        sid = self.session_in(gid, "inside")
        row = f"#plist .prow[data-k='s:{gid}:{sid}']"
        for name, page in self.each():
            self.open(page)
            page.evaluate("S.left.shown = true; applyLeft(); setPanelTab('sessions'); focusPanel()")  # (a fresh profile: the panel is hidden)
            page.evaluate(f"S.pcursor = 'g:' + {json.dumps(gid)}; renderLeft()")
            self.assertTrue(page.is_visible(row))
            page.keyboard.press("Space")
            page.wait_for_timeout(150)
            self.assertFalse(page.query_selector(row))
            self.assertEqual(page.evaluate("S.filter"), "")  # (not typed into the filter)
            page.keyboard.press("Space")
            page.wait_for_timeout(150)
            self.assertTrue(page.is_visible(row))
            page.evaluate(f"S.pcursor = {json.dumps(f's:{gid}:{sid}')}; renderLeft()")
            page.keyboard.press("Space")  # (on a row in it: its group folds, the cursor on the group)
            page.wait_for_timeout(150)
            self.assertFalse(page.query_selector(row))
            self.assertEqual(page.evaluate("S.pcursor"), "g:" + gid)

    def test_emacs_motion_keys_are_arrows_everywhere(self):
        """⌃F ⌃B ⌃P ⌃N are → ← ↑ ↓ in the lists and trees (the left panel, Schedules); where text is edited they stay
        the editor's (the composer with text, the code editor); the empty composer's ⌃P is its ↑ (earlier requests)."""
        gid = self.group("Emacs keys")
        sid = self.session_in(gid, "emacs-a")
        sid2 = self.session_in(gid, "emacs-b")
        self.req("POST", f"/api/sessions/{sid}/send", {"text": "an earlier request"})
        self.wait_card(sid, 1, ("completed",))
        f = Path(self.work) / "emacs.txt"
        f.write_text("one\ntwo\nthree\n")
        row = f"#plist .prow[data-k='s:{gid}:{sid}']"
        x = self.req("POST", "/api/schedules", {"when": "every 2h", "text": "a", "name": "E1", "sid": sid})[1]
        y = self.req("POST", "/api/schedules", {"when": "every 3h", "text": "b", "name": "E2", "sid": sid})[1]
        p_ = self.req("POST", "/api/schedules", {"when": "daily 9am", "text": "c", "name": "E3", "sid": sid})[1]
        self.req("POST", f"/api/schedules/{p_['id']}/update", {"enabled": False})
        try:
            for name, page in self.each():
                page.add_init_script("try { localStorage.removeItem('orbit.schedFold'); } catch (e) {}")
                self.open(page)
                # the left panel: ⌃B folds the group, ⌃F unfolds it, ⌃N ⌃P move
                page.evaluate("S.left.shown = true; applyLeft(); setPanelTab('sessions'); focusPanel()")
                page.evaluate(f"S.pcursor = 'g:' + {json.dumps(gid)}; renderLeft()")
                self.assertTrue(page.is_visible(row), name)
                page.keyboard.press("Control+b"); page.wait_for_timeout(150)
                self.assertFalse(page.query_selector(row), name)
                page.keyboard.press("Control+f"); page.wait_for_timeout(150)
                self.assertTrue(page.is_visible(row), name)
                page.keyboard.press("Control+n"); page.wait_for_timeout(100)
                first = page.evaluate("S.pcursor")
                self.assertTrue(first.startswith(f"s:{gid}:"), first)
                page.keyboard.press("Control+n"); page.keyboard.press("Control+p"); page.wait_for_timeout(100)
                self.assertEqual(page.evaluate("S.pcursor"), first)
                self.assertEqual(page.evaluate("S.filter"), "")  # (nothing typed into the filter)
                # Schedules: ⌃N to the next card, ⌃F opens a fold
                page.evaluate("openFile('orbit:schedules', { pinned: true }); 0")
                page.wait_for_selector("#xbody .scard")
                page.evaluate(f"XV.cur = {json.dumps(x['id'])}; renderX(); document.querySelector('#xbody').focus(); 0")
                ks = page.evaluate("XV.rows.map(r => r.k)")
                page.keyboard.press("Control+n")
                self.assertEqual(page.evaluate("XV.cur"), ks[ks.index(x["id"]) + 1])
                page.evaluate("XV.cur = 'fold:off'; renderX(); document.querySelector('#xbody').focus(); 0")
                page.keyboard.press("Control+f")
                page.wait_for_selector(f"#xbody .scard[data-xk='{p_['id']}']", timeout=5000)
                # the composer: empty, ⌃P is ↑ (its earlier requests); with text, ⌃B is the text area's own
                page.evaluate(f"switchTo({json.dumps(sid)}); 0")
                page.wait_for_timeout(400)
                page.evaluate("input.value = ''; input.focus(); 0")
                page.keyboard.press("Control+p")
                page.wait_for_function("() => modal && modal.kind === 'bell'", timeout=5000)
                page.keyboard.press("Escape")
                page.evaluate("input.value = 'abc'; input.focus(); inputEd.view.dispatch({ selection: { anchor: 3 } }); 0")
                page.keyboard.press("Control+b")  # (the composer is an editor: its own ⌃B moves the caret)
                self.assertEqual(page.evaluate("inputEd.view.state.selection.main.head"), 2, name)
                self.assertFalse(page.evaluate("!!modal"))
                # the code editor: ⌃N is its own line down
                page.evaluate(f"openFile({json.dumps(str(f))}, {{pinned: true}}); 0")
                page.wait_for_function(f"() => EDITORS.get({json.dumps(str(f))}) && EDITORS.get({json.dumps(str(f))}).ed", timeout=8000)
                page.evaluate(f"(() => {{ const v = EDITORS.get({json.dumps(str(f))}).ed.view; v.focus(); v.dispatch({{selection: {{anchor: 0}}}}); }})()")
                page.keyboard.press("Control+n")
                self.assertEqual(page.evaluate(f"(() => {{ const v = EDITORS.get({json.dumps(str(f))}).ed.view; return v.state.doc.lineAt(v.state.selection.main.head).number; }})()"), 2, name)
        finally:
            for it in list(self.desk.scheduler.items):
                self.desk.scheduler.delete(it["id"])

    def test_a_folders_menu_makes_folders_and_sessions(self):
        """The Files tree's folder menu: New file, New folder, New session here (the dialog with that folder), Open in
        Terminal, Copy relative path."""
        base = Path(self.work) / "menu-dir"
        base.mkdir(exist_ok=True)
        for name, page in self.each():
            self.open(page)
            page.evaluate(f"folderMenu({json.dumps(str(base))}, {{ at: {{ x: 300, y: 200 }} }}); 0")
            page.wait_for_selector(".menu .row")
            page.screenshot(path=f"/tmp/orbit-foldermenu-{name}.png", clip={"x": 250, "y": 150, "width": 360, "height": 420})
            labels = page.eval_on_selector_all(".menu .row .lbl", "els => els.map(e => e.textContent)")
            for want in ("New file…", "New folder…", "New session here…", "Open in Terminal", "Copy path", "Open in Finder"):
                self.assertIn(want, labels, name)
            page.click(".menu >> text=New folder…")
            page.wait_for_selector(".dialog input")
            page.fill(".dialog input", f"made-{name}")
            page.keyboard.press("Enter")
            for _ in range(50):
                if (base / f"made-{name}").is_dir():
                    break
                page.wait_for_timeout(100)
            self.assertTrue((base / f"made-{name}").is_dir(), name)
            page.evaluate(f"folderMenu({json.dumps(str(base))}, {{ at: {{ x: 300, y: 200 }} }}); 0")
            page.click(".menu >> text=New session here…")
            page.wait_for_selector(".dialog")
            page.wait_for_timeout(300)
            cwd = page.evaluate("[...document.querySelectorAll('.dialog input')].map(i => i.value).find(v => v.includes('menu-dir'))")
            self.assertTrue(cwd and cwd.rstrip("/").endswith("menu-dir"), (name, cwd))
            page.keyboard.press("Escape")
            # another machine's folder: that machine and its path written as there
            self.assertEqual(page.evaluate("(() => { let got; const old = newSession; newSession = (g, p) => { got = p; }; newSessionIn('/@gpu-box/home/me/proj'); newSession = old; return got; })()"),
                             {"machine": "gpu-box", "cwd": "~/proj"})

    def test_the_files_tree_is_a_file_manager(self):
        """The Files tab: ⌥W copy · ⌃W cut · ⌃Y paste into the cursor's folder; a file dragged onto a folder moves there; a
        closed folder held under a drag opens. (The Mac's clipboard is replaced: the user's is never touched.)"""
        from orbit.workspace import transfer
        old = transfer._jxa
        transfer._jxa = lambda script, args=None: {"count": 1, "paths": []} if args is None else {"count": 2}
        top = Path(self.work) / "fm"
        sid = None
        try:
            for name, page in self.each():
                d = top / name
                (d / "box").mkdir(parents=True, exist_ok=True)
                (d / "shut" / "inner").mkdir(parents=True, exist_ok=True)
                (d / "a.txt").write_text("A")
                (d / "b.txt").write_text("B")
                code, s = self.req("POST", "/api/sessions", {"cwd": str(d), "name": f"fm-{name}"})
                self.assertEqual(code, 200, s)
                self.open(page)
                page.evaluate(f"switchTo({json.dumps(s['id'])}); S.left.shown = true; applyLeft(); setPanelTab('files'); focusPanel(); 0")
                root = str(d)
                page.wait_for_function(f"() => PROWS.some(r => r.k === 'f:{root}|a.txt')", timeout=8000)
                go = lambda k: page.evaluate(f"S.pcursor = {json.dumps(k)}; renderLeft(); focusPanel(); 0")
                # ⌥W on a.txt, ⌃Y on box: a copy in box
                go(f"f:{root}|a.txt"); page.keyboard.press("Alt+w")
                go(f"d:{root}|box"); page.keyboard.press("Control+y")  # (box is empty: the tree shows empty folders too)
                self.wait_disk(d / "box" / "a.txt")
                self.assertTrue((d / "a.txt").exists())
                # ⌃W on b.txt (dimmed), ⌃Y on box: moved
                go(f"f:{root}|b.txt"); page.keyboard.press("Control+w")
                page.wait_for_selector(".prow.cutrow")
                go(f"d:{root}|box"); page.keyboard.press("Control+y")
                self.wait_disk(d / "box" / "b.txt")
                self.wait_gone(d / "b.txt")
                # a drag: a.txt held over the closed folder "shut" opens it, dropped in its "inner": moved there
                page.wait_for_function(f"() => PROWS.some(r => r.k === 'f:{root}|a.txt') && !PROWS.some(r => r.k === 'f:{root}|b.txt')", timeout=8000)
                page.evaluate(f"""(() => {{
                  const el = k => [...plist.querySelectorAll('.prow[data-k]')].find(x => x.dataset.k === k);
                  window.__dt = new DataTransfer();
                  el('f:{root}|a.txt').dispatchEvent(new DragEvent('dragstart', {{ bubbles: true, dataTransfer: window.__dt }}));
                  el('d:{root}|shut').dispatchEvent(new DragEvent('dragover', {{ bubbles: true, cancelable: true, dataTransfer: window.__dt }}));
                }})(); 0""")
                page.wait_for_function(f"() => PROWS.some(r => r.k === 'd:{root}|shut/inner')", timeout=3000)  # (it opened)
                page.evaluate(f"""(() => {{
                  const t = [...plist.querySelectorAll('.prow[data-k]')].find(x => x.dataset.k === 'd:{root}|shut/inner');
                  t.dispatchEvent(new DragEvent('dragover', {{ bubbles: true, cancelable: true, dataTransfer: window.__dt }}));
                  t.dispatchEvent(new DragEvent('drop', {{ bubbles: true, cancelable: true, dataTransfer: window.__dt }}));
                }})(); 0""")
                self.wait_disk(d / "shut" / "inner" / "a.txt")
                self.wait_gone(d / "a.txt")
                self.assertEqual(sorted(os.listdir(d / "box")), ["a.txt", "b.txt"])
                if name == "chromium":
                    page.screenshot(path="/tmp/orbit-files-manager.png", clip={"x": 0, "y": 0, "width": 420, "height": 700})
        finally:
            transfer._jxa = old

    def test_the_files_tree_selects_many(self):
        """Multi-select in the Files tab: ⌘-click two files, drag them (a real mouse drag) onto a folder: both move; ⇧↓
        selects from the cursor, ⌥W ⌃Y copies them; ⌘⌫ sends the selection to the Trash (the test's own)."""
        from orbit.workspace import files as wfiles, transfer
        old_jxa, old_trash = transfer._jxa, wfiles.trash
        transfer._jxa = lambda script, args=None: {"count": 1, "paths": []} if args is None else {"count": 2}
        trash = Path(self.work) / "trash-many"; trash.mkdir(exist_ok=True)
        wfiles.trash = lambda p: os.rename(wfiles._abs(p), trash / f"{time.time_ns()}-{os.path.basename(p)}")
        try:
            for name, page in self.each():
                d = Path(self.work) / "many" / name
                (d / "box").mkdir(parents=True, exist_ok=True); (d / "copies").mkdir(exist_ok=True)
                for f in ("c.txt", "d.txt", "e.txt", "f.txt", "g.txt"):
                    (d / f).write_text(f)
                s = self.req("POST", "/api/sessions", {"cwd": str(d), "name": f"many-{name}"})[1]
                self.open(page)
                page.evaluate(f"switchTo({json.dumps(s['id'])}); S.left.shown = true; applyLeft(); setPanelTab('files'); focusPanel(); 0")
                root = str(d)
                row = lambda f: page.locator(f".prow[data-k='f:{root}|{f}']")
                page.wait_for_function(f"() => PROWS.some(r => r.k === 'f:{root}|c.txt')", timeout=8000)
                row("c.txt").click(modifiers=["Meta"]); row("d.txt").click(modifiers=["Meta"])
                self.assertEqual(page.evaluate("FSEL.keys.size"), 2, name)
                self.assertEqual(page.locator(".prow.msel").count(), 2)
                row("c.txt").drag_to(page.locator(f".prow[data-k='d:{root}|box']"))  # (a real drag: the browser's own)
                self.wait_disk(d / "box" / "c.txt"); self.wait_disk(d / "box" / "d.txt")
                self.wait_gone(d / "c.txt"); self.wait_gone(d / "d.txt")
                # ⇧↓ from e.txt: e and f, copied into copies
                page.wait_for_function(f"() => !PROWS.some(r => r.k === 'f:{root}|c.txt') && PROWS.some(r => r.k === 'f:{root}|e.txt')", timeout=8000)
                page.evaluate(f"S.pcursor = 'f:{root}|e.txt'; renderLeft(); focusPanel(); 0")
                page.keyboard.press("Shift+ArrowDown")
                self.assertEqual(sorted(page.evaluate("[...FSEL.keys.values()].map(p => p.split('/').pop())")), ["e.txt", "f.txt"])
                page.keyboard.press("Alt+w")
                page.evaluate(f"S.pcursor = 'd:{root}|copies'; renderLeft(); focusPanel(); 0")
                page.keyboard.press("Control+y")
                self.wait_disk(d / "copies" / "e.txt"); self.wait_disk(d / "copies" / "f.txt")
                self.assertTrue((d / "e.txt").exists())
                # ⌘⌫ on a selection of two: one question, both to the Trash
                page.wait_for_function(f"() => PROWS.some(r => r.k === 'f:{root}|g.txt')", timeout=8000)
                page.evaluate(f"selClear(); S.pcursor = 'f:{root}|f.txt'; renderLeft(); focusPanel(); 0")
                page.keyboard.press("Shift+ArrowDown")
                page.keyboard.press("Meta+Backspace")
                page.wait_for_selector(".dialog")
                self.assertIn("2 items", page.inner_text(".dialog .title"))
                page.click(".dialog [data-ok]")
                self.wait_gone(d / "f.txt"); self.wait_gone(d / "g.txt")
                self.assertTrue((d / "e.txt").exists())
                # ⌃D: the row to the Trash (asked first), in the filter too when the caret is at its end
                page.wait_for_function(f"() => !PROWS.some(r => r.k === 'f:{root}|g.txt')", timeout=8000)
                page.evaluate(f"S.filter = 'e.tx'; S.filterPos = 4; renderLeft(); S.pcursor = 'f:{root}|e.txt'; renderLeft(); focusPanel(); 0")
                page.keyboard.press("Control+d")
                page.wait_for_selector(".dialog")
                self.assertIn("e.txt", page.inner_text(".dialog .title"))
                page.click(".dialog [data-ok]")
                self.wait_gone(d / "e.txt")
                page.evaluate("S.filter = ''; renderLeft()")
                # and in the Notes tab
                n = self.req("POST", "/api/notes/create", {"name": f"Doomed {name}"})[1]["path"]
                page.evaluate("setPanelTab('notes'); focusPanel(); 0")
                page.wait_for_function(f"() => PROWS.some(r => r.rel === {json.dumps(f'Doomed {name}.md')})", timeout=8000)
                page.evaluate(f"S.pcursor = PROWS.find(r => r.rel === {json.dumps(f'Doomed {name}.md')}).k; renderLeft(); focusPanel(); 0")
                page.keyboard.press("Control+d")
                page.wait_for_selector(".dialog")
                page.click(".dialog [data-ok]")
                self.wait_gone(n)
        finally:
            transfer._jxa, wfiles.trash = old_jxa, old_trash

    def test_ctrl_g_cancels_like_esc(self):
        """⌃G is Esc (Emacs keyboard-quit): it closes a menu and a dialog, clears the Files selection; in an editor it is
        the editor's own (the selection dropped, the keyboard kept)."""
        d = Path(self.work) / "ctrlg"
        d.mkdir(exist_ok=True)
        for f in ("one.txt", "two.txt"):
            (d / f).write_text("line one\nline two\n")
        s = self.req("POST", "/api/sessions", {"cwd": str(d), "name": "ctrl-g"})[1]
        root, f = str(d), str(d / "one.txt")
        for name, page in self.each():
            self.open(page)
            page.evaluate(f"folderMenu({json.dumps(root)}, {{ at: {{ x: 300, y: 200 }} }}); 0")
            page.wait_for_selector(".menu .row")
            page.keyboard.press("Control+g")
            page.wait_for_function("() => !document.querySelector('.menu')", timeout=3000)
            page.evaluate(f"newFolderIn({json.dumps(root)}); 0")
            page.wait_for_selector(".dialog input")
            page.fill(".dialog input", "never-made")
            page.keyboard.press("Control+g")
            page.wait_for_function("() => !document.querySelector('.dialog')", timeout=3000)
            self.assertFalse((d / "never-made").exists(), name)
            # the Files selection
            page.evaluate(f"switchTo({json.dumps(s['id'])}); S.left.shown = true; applyLeft(); setPanelTab('files'); focusPanel(); 0")
            page.wait_for_function(f"() => PROWS.some(r => r.k === 'f:{root}|one.txt')", timeout=8000)
            page.locator(f".prow[data-k='f:{root}|one.txt']").click(modifiers=["Meta"])
            page.locator(f".prow[data-k='f:{root}|two.txt']").click(modifiers=["Meta"])
            self.assertEqual(page.evaluate("FSEL.keys.size"), 2)
            page.keyboard.press("Control+g")
            self.assertEqual(page.evaluate("FSEL.keys.size"), 0, name)
            # an editor: its own ⌃G (the selection dropped), the keyboard stays there
            page.evaluate(f"openFile({json.dumps(f)}, {{pinned: true}}); 0")
            page.wait_for_function(f"() => EDITORS.get({json.dumps(f)}) && EDITORS.get({json.dumps(f)}).ed", timeout=8000)
            page.evaluate(f"(() => {{ const v = EDITORS.get({json.dumps(f)}).ed.view; v.focus(); v.dispatch({{selection: {{anchor: 0, head: 8}}}}); }})()")
            page.keyboard.press("Control+g")
            self.assertEqual(page.evaluate(f"(() => {{ const m = EDITORS.get({json.dumps(f)}).ed.view.state.selection.main; return m.empty; }})()"), True, name)
            self.assertTrue(page.evaluate("!!(document.activeElement && document.activeElement.closest('.cm-editor'))"), name)

    def test_fork_to_opens_a_dialog(self):
        """The session menu's Fork to… opens the New session dialog as a fork: its name, folder and harness the
        source's; another folder makes a hand-over there (the new session reads the turns written for it)."""
        src = self.new_session("fork-src")
        self.req("POST", f"/api/sessions/{src['id']}/send", {"text": "the first request"})
        self.wait_card(src["id"], 1, ("completed",))
        for name, page in self.each():
            other = Path(self.work) / f"fork-dest-{name}"
            other.mkdir(exist_ok=True)
            self.open(page)
            page.evaluate(f"switchTo({json.dumps(src['id'])}); tabMenu({json.dumps(src['id'])}, {{ at: {{ x: 400, y: 120 }} }}); 0")
            page.click(".menu >> text=Fork to…")
            page.wait_for_selector(".dialog .title")
            self.assertIn("Fork “fork-src” to", page.inner_text(".dialog .title"))
            vals = page.evaluate("[...document.querySelectorAll('.dialog input')].map(i => i.value)")
            self.assertIn("fork-src (fork)", vals)
            folder = page.locator(".dialog input").nth(1) if "fork-src (fork)" == vals[0] else None
            cwd_i = vals.index(next(v for v in vals if v.rstrip("/").endswith("work")))
            box = page.locator(".dialog input").nth(cwd_i)
            box.fill(str(other))
            page.keyboard.press("Meta+Enter")
            page.wait_for_function("() => !document.querySelector('.dialog')", timeout=8000)
            page.wait_for_function(f"() => S.current && S.sessions.get(S.current) && S.sessions.get(S.current).name === 'fork-src (fork)' && S.current !== {json.dumps(src['id'])}", timeout=8000)
            new = page.evaluate("S.current")
            c = self.wait_card(new, 1, ("completed",))
            self.assertIn("handover", c["text"])
            self.assertEqual(os.path.realpath(self.desk.get(new).cwd), os.path.realpath(other))
            if name == "chromium":
                page.evaluate(f"tabMenu({json.dumps(src['id'])}, {{ at: {{ x: 400, y: 120 }} }}); 0")
                page.click(".menu >> text=Fork to…")
                page.wait_for_selector(".dialog .title")
                page.screenshot(path="/tmp/orbit-forkto.png")
                page.keyboard.press("Escape")

    def test_a_click_in_the_left_panel_keeps_the_keyboard_there(self):
        """A click on a row of the left panel opens it (a file: beside) and the keyboard stays in the panel (↓ moves
        there); a double-click goes into what it opened (the session's input, the file's editor in the main pane)."""
        gid = self.group("Clicks")
        a, b = self.session_in(gid, "click-a"), self.session_in(gid, "click-b")
        d = Path(self.work) / "clicks"; d.mkdir(exist_ok=True); (d / "k.txt").write_text("one\ntwo\n"); (d / "l.txt").write_text("x")
        files_sid = self.req("POST", "/api/sessions", {"cwd": str(d), "name": "click-files"})[1]["id"]
        for name, page in self.each():
            self.open(page)
            page.evaluate("S.left.shown = true; applyLeft(); setPanelTab('sessions'); 0")
            page.evaluate(f"switchGroup({json.dumps(gid)}); renderLeft(); 0")
            row = f"#plist .prow[data-k='s:{gid}:{b}']"
            page.wait_for_selector(row)
            page.click(row)
            page.wait_for_function(f"() => S.current === {json.dumps(b)}", timeout=5000)
            self.assertEqual(page.evaluate("document.activeElement && document.activeElement.id"), "plist", name)
            before = page.evaluate("S.pcursor")
            page.keyboard.press("ArrowUp")
            self.assertNotEqual(page.evaluate("S.pcursor"), before)  # (the key went to the panel)
            page.dblclick(row)
            page.wait_for_timeout(300)
            self.assertNotEqual(page.evaluate("document.activeElement && document.activeElement.id"), "plist", name)
            # the Files tab: a click opens the file, the keyboard stays; a double-click goes into its editor
            page.evaluate(f"switchTo({json.dumps(files_sid)}); setPanelTab('files'); focusPanel(); 0")
            frow = f"#plist .prow[data-k='f:{d}|k.txt']"
            page.wait_for_selector(frow)
            page.click(frow)  # (shown beside: test_a_file_clicked_in_the_tree_shows_beside)
            page.wait_for_function(f"() => S.side && S.side.path === {json.dumps(str(d / 'k.txt'))}", timeout=5000)
            self.assertEqual(page.evaluate("document.activeElement && document.activeElement.id"), "plist", name)
            page.dblclick(frow)
            page.wait_for_function(f"() => S.file === {json.dumps(str(d / 'k.txt'))}", timeout=5000)
            page.wait_for_function("() => !!(document.activeElement && document.activeElement.closest && document.activeElement.closest('.cm-editor'))", timeout=5000)

    def test_the_files_tree_answers_the_mouse_and_keeps_its_scroll(self):
        """A click opens a folder even from the composer (the panel taking the keyboard redrew the rows under the
        press: the click hit none), and while the panel redraws during the press (an agent's step); a double-click
        leaves it open. The tree scrolled away from its cursor stays there through redraws; a key brings the cursor back."""
        d = Path(self.work) / "mouse-tree"
        for i in range(70):
            (d / f"f{i:02d}.txt").parent.mkdir(parents=True, exist_ok=True); (d / f"f{i:02d}.txt").write_text("x")
        for n in ("alpha", "beta", "gamma"):
            (d / n).mkdir(exist_ok=True); (d / n / "inside.txt").write_text("i")
        sid = self.req("POST", "/api/sessions", {"cwd": str(d), "name": "mouse-tree"})[1]["id"]
        root = str(d)
        is_open = lambda page, n: page.evaluate(f"PROWS.some(r => r.k === {json.dumps(f'f:{root}|{n}/inside.txt')})")
        for name, page in self.each():
            self.open(page)
            page.evaluate(f"switchTo({json.dumps(sid)}); S.left.shown = true; applyLeft(); setPanelTab('files'); 0")
            page.wait_for_function(f"() => PROWS.some(r => r.k === 'd:{root}|alpha')", timeout=8000)
            row = lambda n: f"#plist .prow[data-k='d:{root}|{n}']"
            page.evaluate("focusMain()")  # (the keyboard in the composer: the click moves it to the panel)
            page.click(row("alpha"))
            page.wait_for_timeout(150)
            self.assertTrue(is_open(page, "alpha"), name)
            box = page.locator(row("beta")).bounding_box()  # a redraw during the press
            page.mouse.move(box["x"] + 40, box["y"] + box["height"] / 2); page.mouse.down()
            page.evaluate("renderLeft()"); page.mouse.up()
            page.wait_for_timeout(150)
            self.assertTrue(is_open(page, "beta"), name)
            page.dblclick(row("gamma"))
            page.wait_for_timeout(250)
            self.assertTrue(is_open(page, "gamma"), name)
            # scrolled away from the cursor: redraws keep the place
            page.evaluate(f"S.pcursor = {json.dumps(f'd:{root}|alpha')}; renderLeft(); plist.scrollTop = plist.scrollHeight; 0")
            y = page.evaluate("plist.scrollTop")
            self.assertGreater(y, 200, name)
            page.evaluate(f"renderLeft(); refreshTree({json.dumps(root)}, true); 0")
            page.wait_for_timeout(400)
            self.assertEqual(page.evaluate("plist.scrollTop"), y, name)
            page.evaluate("focusPanel()"); page.keyboard.press("ArrowDown")  # (a key: the cursor in view again)
            page.wait_for_timeout(100)
            self.assertLess(page.evaluate("plist.scrollTop"), y, name)

    def test_a_file_clicked_in_the_tree_shows_beside(self):
        """A click on a file in the Files panel shows it beside, a preview (the next click takes its place), the
        session staying in front and the keyboard in the panel; a double-click on the side tab keeps it; a double-click
        on the row opens it in the main pane (its preview beside gone). Side tabs wear their main tab's marks and have
        a menu. ⌘O's Sessions lists the groups; ⌘R names each tab's group and lists the group after its latest tab."""
        gid = self.group("Beside files")
        other = self.group("Elsewhere grp")
        d = Path(self.work) / "beside"
        d.mkdir(exist_ok=True)
        for n in ("one.py", "two.md", "three.txt"):
            (d / n).write_text(n + "\n")
        sid = self.req("POST", "/api/sessions", {"cwd": str(d), "name": "beside-s", "group": gid})[1]["id"]
        osid = self.session_in(other, "elsewhere-s")
        row = lambda n: f"#plist .prow[data-k='f:{d}|{n}']"
        tabs = "[...document.querySelectorAll('#sidetabs .stab')].map(t => t.querySelector('.nm').innerText.trim() + (t.classList.contains('preview') ? '*' : ''))"
        for name, page in self.each():
            page.add_init_script("try { localStorage.removeItem('orbit.recent'); } catch (e) {}")
            self.req("POST", f"/api/sessions/{sid}/side", {"op": "clear"})  # (the server's: the last browser's gone)
            self.open(page)
            page.evaluate(f"switchGroup({json.dumps(other)}); switchTo({json.dumps(osid)}); 0")
            page.wait_for_timeout(200)
            page.evaluate(f"switchGroup({json.dumps(gid)}); switchTo({json.dumps(sid)}); S.left.shown = true; applyLeft(); setPanelTab('files'); focusMain(); 0")
            page.wait_for_selector(row("one.py"))
            page.click(row("one.py"))
            page.wait_for_function("() => S.side && S.side.path && S.side.path.endsWith('one.py')", timeout=5000)
            self.assertEqual(page.evaluate("[S.file, S.current]"), [None, sid], name)  # (the session stays in front)
            page.wait_for_timeout(600)  # (the frame loaded: the keyboard still in the panel)
            self.assertEqual(page.evaluate("document.activeElement && document.activeElement.id"), "plist", name)
            self.assertEqual(page.evaluate(tabs), ["one.py*"], name)
            page.click(row("two.md"))
            page.wait_for_timeout(300)
            self.assertEqual(page.evaluate(tabs), ["two.md*"], name)  # (in the preview's place)
            page.dblclick("#sidetabs .stab")
            page.wait_for_timeout(200)
            page.click(row("three.txt"))
            page.wait_for_timeout(300)
            self.assertEqual(page.evaluate(tabs), ["two.md", "three.txt*"], name)
            self.assertTrue(page.evaluate("!!document.querySelector('#sidetabs .stab .fi *')"), name)  # (its file mark)
            page.click("#sidetabs .stab.active", button="right")
            page.wait_for_selector(".menu >> text=Open as a main tab")
            page.keyboard.press("Escape")
            page.dblclick(row("one.py"))
            page.wait_for_function(f"() => S.file === {json.dumps(str(d / 'one.py'))}", timeout=5000)
            page.evaluate(f"switchTo({json.dumps(sid)}); 0"); page.wait_for_timeout(200)
            page.evaluate("openSide({ kind: 'session', sid: " + json.dumps(osid) + " }, { focus: false })")
            page.wait_for_timeout(200)
            self.assertTrue(page.evaluate("!!document.querySelector('#sidetabs .stab.active .smark .hlogo')"), name)  # (a session's mark: its agent, its state on it)
            # ⌘O: the groups among the sessions; chosen, that group in front
            page.evaluate("openFinder('Sessions')")
            page.wait_for_selector(".finder .frow")
            self.assertIn("group", page.inner_text(".finder .flist .frow"), name)  # (groups first)
            self.assertTrue(page.evaluate("!!document.querySelector('.finder .frow .gi .gicon')"), name)  # (a group's mark)
            self.assertTrue(page.evaluate("!!document.querySelector('.finder .frow .smark .hlogo')"), name)  # (a session: its agent, its state on it)
            page.keyboard.type("Elsewhere grp")
            page.wait_for_timeout(200)
            first = page.inner_text(".finder .flist .frow")
            self.assertIn("group", first, name)
            page.keyboard.press("Enter")
            page.wait_for_function(f"() => S.group === {json.dumps(other)} && S.current === {json.dumps(osid)}", timeout=3000)
            # ⌘R: the tab's group named, and the group itself after it
            page.evaluate("openRecent()")
            page.wait_for_selector(".finder .frow")
            rows = page.evaluate("[...document.querySelectorAll('.finder .frow')].map(r => r.innerText.replace(/\\s+/g, ' '))")
            self.assertTrue(any("Beside files" in r and "group" in r for r in rows), rows)
            self.assertIn("group", rows[0])  # (groups first)
            page.keyboard.press("Escape")
            page.click("#grouppill .gcaret")  # the group menu: each group with its mark, as in the Groups panel
            page.wait_for_selector(".menu .row .mic .gicon")
            page.keyboard.press("Escape")
            page.evaluate(f"closeGroupTab({{gid: {json.dumps(gid)}, path: {json.dumps(str(d / 'one.py'))}}})")
            page.wait_for_timeout(300)
        for s_ in (sid, osid):  # (the tests after this one see the panel as before)
            self.req("POST", f"/api/sessions/{s_}/close")
        for g in (gid, other):
            self.req("POST", f"/api/groups/{g}/delete")

    def test_a_filtered_files_tree_is_still_a_tree(self):
        """Filtering the Files panel keeps the tree: the matches under their folders, each folder on the way open, the
        rest gone; a folder matched by its name too; the cursor on the best match (⏎ opens it); a folder folded while
        filtering stays folded until the filter changes, and the tree without a filter is as it was."""
        d = Path(self.work) / "ftree"
        for rel in ("src/ui/fileops.js", "src/ui/left.js", "src/server/handler.py", "docs/fileops-notes.md", "tests/unit/test_x.py", "assets/fileopsicons/a.png"):
            (d / rel).parent.mkdir(parents=True, exist_ok=True); (d / rel).write_text("x")
        sid = self.req("POST", "/api/sessions", {"cwd": str(d), "name": "ftree"})[1]["id"]
        root = str(d)
        rows = "PROWS.filter(r => r.root === %s).map(r => r.kind[0] + ':' + (r.rel || '') + (r.open ? '+' : '') + '@' + r.depth)" % json.dumps(root)
        for name, page in self.each():
            self.open(page)
            page.evaluate(f"switchTo({json.dumps(sid)}); S.left.shown = true; applyLeft(); setPanelTab('files'); focusPanel(); 0")
            page.wait_for_function(f"() => PROWS.some(r => r.k === 'd:{root}|src')", timeout=8000)
            page.evaluate("S.filter = 'fileops'; renderLeft()")
            got = page.evaluate(rows)
            self.assertEqual(got, ["r:+@0", "d:assets+@1", "d:assets/fileopsicons+@2", "f:assets/fileopsicons/a.png@3", "d:docs+@1",
                                   "f:docs/fileops-notes.md@2", "d:src+@1", "d:src/ui+@2", "f:src/ui/fileops.js@3"], name)
            self.assertEqual(page.evaluate("S.pcursor"), f"f:{root}|src/ui/fileops.js", name)  # (the best match)
            self.assertIn("<b>", page.inner_html(f"#plist .prow[data-k='f:{root}|src/ui/fileops.js'] .nm"), name)
            page.evaluate(f"S.pcursor = 'd:{root}|src'; renderLeft()"); page.keyboard.press("ArrowLeft")  # folded, in this filtered tree
            self.assertNotIn("f:src/ui/fileops.js@3", page.evaluate(rows), name)
            self.assertIn("d:src@1", page.evaluate(rows), name)
            page.evaluate("S.filter = ''; renderLeft()")  # no filter: the tree as it was (src never opened by hand)
            self.assertIn("d:src@1", page.evaluate(rows), name)
            self.assertNotIn("d:src/ui@2", page.evaluate(rows), name)

    def test_the_week_note_is_empty_and_its_title_is_its_name(self):
        """⌘⇧D: this week's note, empty; its title (its name) above the text and apart from it (⌘A in the text never
        takes it); edited, the file is renamed, and ⌘⇧D still opens it."""
        info = self.req("POST", "/api/notes/week")[1]
        start = info["path"]
        for name, page in self.each():
            self.open(page)
            page.wait_for_function("() => !!S.vault && !!JOURNAL.folder", timeout=5000)
            page.evaluate("weekNote()")
            page.wait_for_selector("#fbody .host:not([hidden]) .ntitle")
            cur = page.evaluate("S.file")
            self.assertEqual(page.input_value("#fbody .host:not([hidden]) .ntitle"), Path(cur).stem, name)
            self.assertEqual(page.evaluate("EDITORS.get(S.file).ed.getDoc()"), "", name)  # (empty: no day headings)
            page.evaluate("EDITORS.get(S.file).ed.focus()")
            page.keyboard.type("plans")
            page.keyboard.press("Meta+a")
            page.keyboard.type("only this")  # (⌘A took the text, not the title)
            self.assertEqual(page.input_value("#fbody .host:not([hidden]) .ntitle"), Path(cur).stem, name)
            page.fill("#fbody .host:not([hidden]) .ntitle", f"Launch week {name}")
            page.press("#fbody .host:not([hidden]) .ntitle", "Enter")
            want = str(Path(cur).with_name(f"Launch week {name}.md"))
            page.wait_for_function(f"() => S.file === {json.dumps(want)}", timeout=5000)
            self.wait_disk(want)
            self.assertFalse(Path(cur).exists(), name)
            page.wait_for_timeout(1800)  # (saved)
            self.assertEqual(Path(want).read_text(), "only this", name)
            page.evaluate("closeFile(S.file)"); page.wait_for_timeout(200)
            page.evaluate("weekNote()")  # still this week's
            page.wait_for_function(f"() => S.file === {json.dumps(want)}", timeout=5000)
            page.evaluate("closeFile(S.file)")
            Path(want).write_text(""); os.rename(want, start)  # (the next browser starts as this one did)

    def test_recent_has_the_folders_opened_from_open(self):
        """A folder opened from ⌘O (Files & Folders) is in ⌘R; chosen there, the Files panel on it again. Moving
        through the Files panel adds none."""
        d = Path(self.work) / "recent-folders"
        (d / "deep" / "pkgfold").mkdir(parents=True, exist_ok=True); (d / "deep" / "pkgfold" / "a.py").write_text("a")
        (d / "other").mkdir(exist_ok=True); (d / "other" / "b.py").write_text("b")
        sid = self.req("POST", "/api/sessions", {"cwd": str(d), "name": "recent-folders"})[1]["id"]
        k = f"d:{os.path.realpath(d)}|deep/pkgfold"
        for name, page in self.each():
            page.add_init_script("try { localStorage.removeItem('orbit.recent'); } catch (e) {}")
            self.open(page)
            page.evaluate(f"switchTo({json.dumps(sid)}); openFinder('Files & Folders')")
            page.wait_for_timeout(600)
            page.fill(".finder .fq", "pkgfold"); page.wait_for_timeout(300)
            page.press(".finder .fq", "Enter")
            page.wait_for_function(f"() => S.pcursor === {json.dumps(k)}", timeout=5000)
            page.evaluate(f"S.pcursor = {json.dumps(f'd:{os.path.realpath(d)}|other')}; renderLeft(); openRow(prow())")  # (moving in the panel: none)
            page.evaluate("focusMain(); openRecent()")
            page.wait_for_selector(".finder .frow")
            rows = page.evaluate("[...document.querySelectorAll('.finder .frow')].map(r => r.innerText.replace(/\\s+/g, ' '))")
            self.assertTrue(any(r.startswith("pkgfold") and "folder" in r for r in rows), rows)
            self.assertFalse(any(r.startswith("other ") for r in rows), rows)
            page.fill(".finder .fq", "pkgfold"); page.wait_for_timeout(200)
            page.press(".finder .fq", "Enter")
            page.wait_for_function(f"() => S.pcursor === {json.dumps(k)} && document.activeElement.id === 'plist'", timeout=5000)

    def test_a_pasted_image_goes_into_attachments_beside_the_document(self):
        """An image (or any file) pasted or dropped into a Markdown document: into the attachments folder beside it,
        linked from it; a wiki link to an attachment opens it, never a new note."""
        d = Path(self.work) / "paste-doc"; d.mkdir(exist_ok=True)
        doc = d / "README.md"; doc.write_text("# Readme\n")
        for name, page in self.each():
            self.open(page)
            page.evaluate(f"openFile({json.dumps(str(doc))}, {{pinned: true}})")
            page.wait_for_function("() => EDITORS.get(S.file) && EDITORS.get(S.file).ed", timeout=5000)
            page.evaluate(f"""dropIntoEditor(S.file, null, [{{ file: new File([new Uint8Array([137,80,78,71,13,10,26,10,{len(name)}])], "shot-{name}.png", {{ type: "image/png" }}) }},
                                                      {{ file: new File(["%PDF-1.4"], "spec {name}.pdf", {{ type: "application/pdf" }}) }}])""")
            page.wait_for_function(f"() => EDITORS.get(S.file).ed.getDoc().includes('attachments/shot-{name}.png')", timeout=5000)
            text = page.evaluate("EDITORS.get(S.file).ed.getDoc()")
            self.assertIn(f"[spec {name}.pdf](<attachments/spec {name}.pdf>)", text, name)
            self.assertTrue((d / "attachments" / f"shot-{name}.png").exists(), name)
            self.assertTrue((d / "attachments" / f"spec {name}.pdf").exists(), name)
            # in the vault: a wiki link to an image opens it (no "x.png.md" note made)
            vault = Path(page.evaluate("S.vault"))
            n = self.req("POST", "/api/notes/create", {"name": f"Pics {name}"})[1]["path"]
            r = self.req("POST", "/api/attach", {"doc": n, "name": f"wiki-{name}.png", "data": "iVBORw0KGgo="})[1]
            page.evaluate(f"openWiki({json.dumps(f'wiki-{name}.png')}, {json.dumps(n)})")
            page.wait_for_function(f"() => S.file === {json.dumps(r['path'])}", timeout=5000)
            self.assertFalse(any(vault.rglob(f"wiki-{name}.png.md")), name)
            page.evaluate(f"openWiki('missing-{name}.png', {json.dumps(n)})"); page.wait_for_timeout(300)
            self.assertFalse(any(vault.rglob(f"missing-{name}.png.md")), name)

    def test_no_favorites_and_no_group_digits(self):
        """No ★ Favorites view: the Groups panel and the group menu list the groups only; no ⌘ digit on a group (⌘1 is
        the left panel, a window per group: ⌘⇧N), its mark starts the row."""
        for name, page in self.each():
            self.open(page)
            first = page.evaluate("topGroups()[0].id")
            page.evaluate("S.left.shown = true; applyLeft(); setPanelTab('sessions'); 0")
            page.wait_for_selector("#plist .prow.grp")
            self.assertNotIn("Favorites", page.inner_text("#plist"), name)
            self.assertIsNone(page.query_selector(f"#plist .prow.grp[data-k='g:{first}'] .ix"), name)
            page.click("#grouppill .gcaret")
            page.wait_for_selector(".menu .row")
            self.assertNotIn("Favorites", page.inner_text(".menu"), name)
            page.keyboard.press("Escape")

    def test_earlier_cards_come_in_only_when_the_scrolling_stops(self):
        """A long conversation draws its latest 40 requests; the earlier ones come in above once the user's scrolling
        stops, the view kept where it was. Never during the scroll: WebKit drops a scrollTop written during a
        trackpad's momentum, and the view ran up to the first request."""
        sid = self.new_session("long-scroll")["id"]
        for i in range(56):
            c = self.req("POST", f"/api/sessions/{sid}/send", {"text": f"request {i} " + " ".join(f"w{k}" for k in range(120))})[1]
            self.wait_card(sid, c["id"], ("completed",))
        first_in_view = """(() => { const t = conv.getBoundingClientRect().top;
            const el = [...document.querySelectorAll('#cards .card')].find(c => c.getBoundingClientRect().bottom > t + 1); return el && el.id })()"""
        for name, page in self.each():
            self.open(page)
            page.evaluate(f"switchTo({json.dumps(sid)}); 0")
            page.wait_for_function("() => CONV.from === 16", timeout=5000)
            page.evaluate("""(() => {  // every scrollTop written while the user scrolls, counted
                const d = Object.getOwnPropertyDescriptor(Element.prototype, 'scrollTop'); window.WRITES = 0;
                Object.defineProperty(conv, 'scrollTop', { get() { return d.get.call(this); },
                    set(v) { if (CONVL.userScrolling()) WRITES++; d.set.call(this, v); }, configurable: true }); })()""")
            page.mouse.move(700, 400)
            for _ in range(80):  # up past the top of what is drawn, without a pause
                page.mouse.wheel(0, -600)
                page.wait_for_timeout(20)
                if page.evaluate("conv.scrollTop") < 50:
                    break
            self.assertEqual(page.evaluate("CONV.from"), 16, name)  # (still scrolling: nothing put in above)
            self.assertLess(page.evaluate("conv.scrollTop"), 50, name)
            seen = page.evaluate(first_in_view)
            where = "document.getElementById(%r).getBoundingClientRect().top - conv.getBoundingClientRect().top" % seen
            y = page.evaluate(where)
            page.wait_for_function("() => CONV.from === 0", timeout=3000)  # stopped: the earlier ones in
            self.assertLess(abs(page.evaluate(where) - y), 2, name)  # (the same card in the same place)
            self.assertGreater(page.evaluate("conv.scrollTop"), 1000, name)
            self.assertEqual(page.evaluate("WRITES"), 0, name)

    def wait_disk(self, p, timeout=8):
        end = time.time() + timeout
        while time.time() < end and not Path(p).exists():
            time.sleep(0.1)
        self.assertTrue(Path(p).exists(), f"{p} never appeared")

    def wait_gone(self, p, timeout=8):
        end = time.time() + timeout
        while time.time() < end and Path(p).exists():
            time.sleep(0.1)
        self.assertFalse(Path(p).exists(), f"{p} is still there")

    def test_phone_usage_has_openai(self):
        self.new_session("usage")
        code, e = self.req("POST", "/api/settings", {"live_budget": 10})
        self.assertEqual(code, 200, e)
        try:
            for name, b in self.browsers.items():
                ctx = b.new_context(viewport={"width": 390, "height": 844}, has_touch=True)
                page = ctx.new_page()
                try:
                    page.goto(f"http://127.0.0.1:{self.port}/m/")
                    page.wait_for_function("() => typeof S !== 'undefined' && S.sessions && S.sessions.size > 0", timeout=15000)
                    page.evaluate("loadUsage()")
                    page.wait_for_timeout(800)
                    self.assertIn("OpenAI $10.00 left", page.evaluate("usageHint().replace(/<[^>]+>/g, '')"), name)  # (the Misc row)
                    text = page.evaluate("(() => { const p = usagePage(); return p.body.textContent; })()")  # (the Usage page)
                    self.assertIn("OpenAI", text)
                    self.assertIn("$10.00 left", text)
                    self.assertIn("0 calls", text)
                finally:
                    ctx.close()
        finally:
            self.req("POST", "/api/settings", {"live_budget": 0})

    def test_phone_turns_notifications_on_and_a_tap_opens_the_session(self):
        """Misc → Notifications with the browser's push parts stubbed (no real push service): the switch asks, subscribes
        with the server's key, the server keeps it; Send a test reaches the (local, fake) push service encrypted; a
        notification's tap (sw.js's message) opens its session, and a cold start at /m/#s/<id> does too."""
        import test_push
        from orbit.services import push, webpush as wp
        fake = test_push.FakePushService()
        push.ALLOW_EXTRA = (fake.base,)
        sid = self.new_session("pushed")["id"]
        try:
            for name, b in self.browsers.items():
                sub, d = test_push.device(fake.base, name=name)
                stub = """(() => {
                  const SUB = %s, st = { sub: null, opts: null };
                  window.__push = st;
                  Object.defineProperty(navigator, "standalone", { get: () => true, configurable: true });
                  let perm = "default";
                  if (!window.Notification) window.Notification = function () {};
                  Object.defineProperty(Notification, "permission", { get: () => perm, configurable: true });
                  Notification.requestPermission = async () => (perm = "granted");
                  if (!window.PushManager) window.PushManager = function () {};
                  const fsub = { endpoint: SUB.endpoint, options: {}, toJSON: () => SUB, unsubscribe: async () => { st.sub = null; return true; } };
                  const reg = { pushManager: { getSubscription: async () => st.sub,
                    subscribe: async o => { st.opts = o; fsub.options = o; st.sub = fsub; return fsub; } } };
                  Object.defineProperty(navigator.serviceWorker, "ready", { get: () => Promise.resolve(reg), configurable: true });
                  navigator.serviceWorker.getRegistration = async () => reg;
                })()""" % json.dumps(sub)
                ctx = b.new_context(viewport={"width": 390, "height": 844}, has_touch=True)
                ctx.add_init_script(stub)
                page = ctx.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                try:
                    page.goto(f"http://127.0.0.1:{self.port}/m/")
                    page.wait_for_function("() => typeof S !== 'undefined' && S.sessions && S.sessions.size > 0", timeout=15000)
                    page.evaluate("switchTab('misc')")
                    page.wait_for_selector("[data-open=push]")
                    page.wait_for_timeout(400)
                    self.assertIn("off", page.inner_text("[data-open=push]"), name)
                    page.click("[data-open=push]")
                    page.wait_for_selector("[data-push-toggle]")
                    page.screenshot(path=f"/tmp/orbit-push-off-{name}.png")
                    page.click("[data-push-toggle]")
                    page.wait_for_selector("[data-push-test]", timeout=8000)
                    key = self.req("GET", "/api/push")[1]["publicKey"]
                    self.assertEqual(page.evaluate("bytesB64u(__push.opts.applicationServerKey)"), key)
                    self.assertEqual(page.evaluate("__push.opts.userVisibleOnly"), True)
                    subs = self.req("GET", "/api/push")[1]["subscriptions"]
                    self.assertEqual([s["label"] for s in subs], ["browser" if name == "chromium" else page.evaluate("deviceLabel()")])
                    n = len(fake.got)
                    page.click("[data-push-test]")
                    for _ in range(80):
                        if len(fake.got) > n:
                            break
                        page.wait_for_timeout(100)
                    self.assertEqual(len(fake.got), n + 1, (name, page.evaluate("[PUSH.err, document.querySelector('#toast') && document.querySelector('#toast').textContent]")))
                    page.wait_for_function("() => /accepted/.test((document.querySelector('.plog') || {}).textContent || '')", timeout=8000)
                    msg = json.loads(wp.decrypt(fake.got[-1]["body"], d, sub["keys"]["auth"]))
                    self.assertEqual(msg["title"], "Orbit")
                    self.assertIn("accepted", page.inner_text(".plog"))
                    page.fill("#pr-text", "Call the dentist")
                    page.fill("#pr-when", "30m")
                    page.click("[data-push-remind]")
                    page.wait_for_selector(".prem", timeout=8000)
                    it = next(x for x in self.desk.scheduler.items if x["text"] == "Call the dentist")
                    self.assertEqual((it["notify"], it["spec"]["kind"]), (True, "once"))
                    page.screenshot(path=f"/tmp/orbit-push-on-{name}.png")
                    # turn off: the server forgets this device
                    page.click("[data-push-toggle]")
                    page.wait_for_function("() => !document.querySelector('[data-push-test]')", timeout=8000)
                    self.assertEqual(self.req("GET", "/api/push")[1]["subscriptions"], [])
                    self.desk.scheduler.delete(it["id"])
                    # a tap while the app runs: sw.js posts {t: "open", sid}
                    page.evaluate(f"navigator.serviceWorker.dispatchEvent(new MessageEvent('message', {{ data: {{ t: 'open', sid: {json.dumps(sid)} }} }})); 0")
                    page.wait_for_timeout(300)
                    self.assertEqual(page.evaluate("[S.current, AREA.cur, location.hash]"), [sid, "sessions", "#s/" + sid])
                    # iOS: a tap with no window (the app only comes to the front) or a held-back message: the entry sw.js wrote
                    other = self.new_session(f"other-{name}")["id"]
                    page.evaluate("loadAll()")
                    page.wait_for_function(f"() => S.sessions.has({json.dumps(other)})", timeout=8000)
                    page.evaluate(f"openSession({json.dumps(other)}); 0")
                    put = f"caches.open('orbit-push').then(c => c.put('/m/__open', new Response(JSON.stringify({{ sid: {json.dumps(sid)}, at: Date.now() }}))))"
                    page.evaluate(f"{put}.then(() => document.dispatchEvent(new Event('visibilitychange')))")
                    page.wait_for_function(f"() => S.current === {json.dumps(sid)}", timeout=8000)
                    self.assertFalse(page.evaluate("caches.open('orbit-push').then(c => c.match('/m/__open')).then(r => !!r)"))  # (taken once)
                    page.evaluate(f"openSession({json.dumps(other)}); 0")
                    page.evaluate(put)
                    cold0 = ctx.new_page()  # (a cold start at the app's own address: the entry opens it once the sessions are in)
                    cold0.goto(f"http://127.0.0.1:{self.port}/m/")
                    cold0.wait_for_function(f"() => typeof S !== 'undefined' && S.current === {json.dumps(sid)}", timeout=15000)
                    # a declarative notification's tap (iOS): /m/?session=<sid>, whatever the address was; the query is dropped
                    cold1 = ctx.new_page()
                    cold1.goto(f"http://127.0.0.1:{self.port}/m/?session={sid}&at=1#s/{other}")
                    cold1.wait_for_function(f"() => typeof S !== 'undefined' && S.current === {json.dumps(sid)}", timeout=15000)
                    self.assertEqual(cold1.evaluate("location.search"), "")
                    # a tap with the app closed: it starts at /m/#s/<sid>
                    cold = ctx.new_page()
                    cold.goto(f"http://127.0.0.1:{self.port}/m/#s/{sid}")
                    cold.wait_for_function(f"() => typeof S !== 'undefined' && S.current === {json.dumps(sid)}", timeout=15000)
                finally:
                    ctx.close()
                self.assertEqual(errors, [], name)
        finally:
            push.ALLOW_EXTRA = ()
            fake.stop()

    def test_the_service_worker_shows_and_routes_notifications(self):
        """sw.js's push and notificationclick handlers, run in a sandbox (a fake `self`): the notification it shows, a
        tap with no window (opens /m/#s/<id>), with one (focus, then {t: "open"}), and a payload naming another site."""
        b = next(iter(self.browsers.values()))
        ctx = b.new_context()
        page = ctx.new_page()
        try:
            page.goto(f"http://127.0.0.1:{self.port}/api/version")
            r = page.evaluate("""async () => {
              const src = await (await fetch('/m/sw.js')).text();
              const L = {}, shown = [], opened = [], posted = [], box = {}; let wins = [], focused = 0;
              const caches_ = { open: async name => ({ put: async (k, r) => { box[name + " " + k] = JSON.parse(await r.text()); } }), keys: async () => [] };
              const self_ = { addEventListener: (t, f) => { L[t] = f; }, registration: { showNotification: async (t, o) => { shown.push([t, o]); } },
                clients: { matchAll: async () => wins, openWindow: async u => { opened.push(u); }, claim: async () => {} }, skipWaiting: async () => {} };
              new Function('self', 'caches', 'location', src)(self_, caches_, { origin: 'https://orbit.test' });
              const run = async (type, extra) => { const ps = []; L[type]({ waitUntil: p => ps.push(p), ...extra }); await Promise.all(ps); };
              const pushed = d => run('push', { data: { json: () => d, text: () => JSON.stringify(d) } });
              await pushed({ title: 'Report ready', body: 'Q3', sid: 'abc 1', tag: 'sched:x:1', url: '/m/#s/abc%201' });
              await pushed({ title: 'Evil', sid: '//evil.example', url: 'https://evil.example/' });
              await pushed(null);
              // declarative (push.py with an HTTPS address): a browser that takes it itself (its event carries the proposed
              // notification) is left to show it; elsewhere it is shown here, the session read from navigate
              const decl = { web_push: 8030, notification: { title: 'Decl', body: 'b', navigate: 'https://orbit.test/m/?session=s%202&at=1' } };
              await run('push', { data: { json: () => decl }, notification: { title: 'Decl' } });
              const declShown = shown.length;
              await pushed(decl);
              await pushed({ web_push: 8030, notification: { title: 'Elsewhere', navigate: 'https://evil.example/m/?session=x' } });
              const tap = n => run('notificationclick', { notification: { data: n.data, close() {} } });
              await tap(shown[0][1]);
              await tap(shown[1][1]);
              wins = [{ url: 'https://orbit.test/m/#s/old', focus: async () => { focused++; }, postMessage: m => posted.push(m) }];
              const first = JSON.parse(JSON.stringify(box));
              await tap(shown[0][1]);
              return { shown, opened, posted, focused, first, declShown };
            }""")
            self.assertEqual(r["shown"][0], ["Report ready", {"body": "Q3", "tag": "sched:x:1", "data": {"sid": "abc 1", "url": "/m/#s/abc%201"}, "icon": "/m/icon-192.png"}])
            self.assertEqual(r["shown"][2][0], "Orbit")  # (an empty payload: still a notification, as iOS requires)
            self.assertEqual(r["declShown"], 3)  # (the browser shows a declarative one itself: nothing here)
            self.assertEqual((r["shown"][3][0], r["shown"][3][1]["data"]["sid"]), ("Decl", "s 2"))
            self.assertEqual((r["shown"][4][0], r["shown"][4][1]["data"]["sid"]), ("Elsewhere", ""))  # (another site's navigate: no session)
            self.assertEqual(r["opened"], ["/m/#s/abc%201", "/m/#s/%2F%2Fevil.example"])  # (this app's pages only)
            self.assertEqual((r["posted"], r["focused"]), ([{"t": "open", "sid": "abc 1"}], 1))
            self.assertEqual(r["first"]["orbit-push /m/__open"]["sid"], "//evil.example")  # (the last tap's session, for the page)
            taps = [l for l in self.req("GET", "/api/push")[1]["log"] if l["outcome"] == "tap" and l["via"] in ("open", "focus")]
            notes = {l["via"] for l in self.req("GET", "/api/push")[1]["log"] if l["outcome"] == "tap"}
            self.assertTrue({"received", "tapped"} <= notes, notes)  # (the device's own record: a push came, a tap came)
            self.assertEqual([(t["sid"], t["via"], t["windows"]) for t in taps[-3:]], [("abc 1", "open", 0), ("//evil.example", "open", 0), ("abc 1", "focus", 1)])
        finally:
            ctx.close()

    def test_schedules_are_cards_active_first(self):
        """The Schedules tab: one card per job, the active ones first; "Paused and finished" and "Archived" folded
        until opened (remembered); a card's grid says When / Wakes / Next / Runs; Task and journal unfold (the journal
        read then); the keyboard moves card to card and opens a fold."""
        sid = self.new_session("sched-cards")["id"]
        mk = lambda **b: self.req("POST", "/api/schedules", {"sid": sid, **b})[1]
        a1 = mk(when="every 2h", text="Check **CI** and summarize", name="CI digest")
        a2 = mk(when="daily 08:20", text="Morning news", name="Morning news", done_notify={"title": "早间新闻总结已完成"})
        w = mk(when="every 5m", text="new PRs", name="PR watch", check="echo hi")
        p_ = mk(when="weekdays 9am", text="paused one", name="Paused job")
        self.req("POST", f"/api/schedules/{p_['id']}/update", {"enabled": False})
        gone = mk(when="every 1h", text="old job", name="Old job")
        j = Path(self.desk.scheduler.journal(gone))
        j.write_text(j.read_text() + "\n- 2026-11-25: did the **thing**\n")
        self.req("POST", f"/api/schedules/{gone['id']}/delete")
        try:
            for name, b in self.browsers.items():
                ctx = b.new_context(viewport={"width": 1280, "height": 900})
                page = ctx.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                try:
                    page.add_init_script("try { localStorage.removeItem('orbit.schedFold'); } catch (e) {}")
                    self.open(page)
                    page.evaluate("openFile('orbit:schedules', { pinned: true }); 0")
                    page.wait_for_selector("#xbody .scard", timeout=8000)
                    page.wait_for_timeout(200)
                    page.screenshot(path=f"/tmp/orbit-sched-top-{name}.png")
                    names = page.eval_on_selector_all("#xbody .scards > .scard .snm", "els => els.map(e => e.textContent)")
                    self.assertEqual(set(names), {"CI digest", "Morning news", "PR watch"}, name)  # (the active ones; the rest folded)
                    folds = page.eval_on_selector_all("#xbody .sfold", "els => els.map(e => e.textContent.replace(/\\s+/g, ' ').trim())")
                    self.assertEqual(folds[0], "Paused and finished 1")
                    self.assertRegex(folds[1], r"^Archived \d+$")  # (other tests' removed jobs are archived too)
                    card = page.locator(f"#xbody .scard[data-xk='{a1['id']}']")
                    grid = card.locator(".sgrid").inner_text()
                    for k in ("WHEN", "WAKES", "NEXT", "RUNS", "ID"):
                        self.assertIn(k, grid.upper())
                    self.assertIn("every 2h", grid)
                    self.assertIn("sched-cards", grid)
                    self.assertIn("早间新闻总结已完成", page.locator(f"#xbody .scard[data-xk='{a2['id']}'] .sgrid").inner_text())
                    self.assertIn("no agent", page.locator(f"#xbody .scard[data-xk='{w['id']}'] .sgrid").inner_text())
                    self.assertEqual(card.locator(".spill").inner_text(), "active")
                    # Task and journal
                    card.locator("summary").click()
                    page.wait_for_function(f"() => document.querySelector(\"#xbody .scard[data-xk='{a1['id']}'] .stask strong\")")
                    # the folds: a click opens one (remembered); the keyboard too
                    page.click("#xbody .sfold[data-fold=off]")
                    page.wait_for_selector(f"#xbody .scard[data-xk='{p_['id']}']")
                    self.assertEqual(page.locator(f"#xbody .scard[data-xk='{p_['id']}'] .spill").inner_text(), "paused")
                    self.assertEqual(page.evaluate("JSON.parse(localStorage.getItem('orbit.schedFold')).off"), False)
                    page.evaluate("XV.cur = 'fold:arch'; renderX(); document.querySelector('#xbody').focus(); 0")
                    page.keyboard.press("Enter")
                    arch = page.locator(f"#xbody .scard[data-xk='a:{gone['id']}']")
                    arch.wait_for(timeout=5000)
                    self.assertEqual(arch.locator(".spill").inner_text(), "archived")
                    self.assertIn("REMOVED", arch.locator(".sgrid").inner_text().upper())
                    arch.locator("summary").click()
                    page.wait_for_function(f"() => /did the/.test((document.querySelector(\"#xbody .scard[data-xk='a:{gone['id']}'] .stask\") || {{}}).textContent || '')", timeout=5000)
                    page.wait_for_timeout(200)
                    page.screenshot(path=f"/tmp/orbit-sched-cards-{name}.png", full_page=False)
                    # ↓ from the first card goes to the next
                    page.evaluate(f"XV.cur = {json.dumps(a1['id'])}; renderX(); document.querySelector('#xbody').focus(); 0")
                    first = page.evaluate("XV.rows.map(r => r.k)")
                    page.keyboard.press("ArrowDown")
                    self.assertEqual(page.evaluate("XV.cur"), first[first.index(a1["id"]) + 1], (first, a1["id"], page.evaluate("document.activeElement.id || document.activeElement.className")))
                finally:
                    ctx.close()
                self.assertEqual(errors, [], name)
        finally:
            for it in list(self.desk.scheduler.items):
                self.desk.scheduler.delete(it["id"])

    def test_phone_schedules_are_cards_active_first(self):
        """The phone's Misc → Schedules, as the desktop's: active cards first, "Paused and finished" and "Archived"
        folded until opened; a card's grid; Task and journal unfold (the journal read then)."""
        sid = self.new_session("phone-sched")["id"]
        mk = lambda **b: self.req("POST", "/api/schedules", {"sid": sid, **b})[1]
        a1 = mk(when="daily 08:20", text="Morning **news**", name="Morning news", done_notify={"title": "早间新闻总结已完成"})
        a2 = mk(when="every 2h", text="CI", name="CI digest")
        p_ = mk(when="weekdays 9am", text="paused", name="Paused job")
        self.req("POST", f"/api/schedules/{p_['id']}/update", {"enabled": False})
        gone = mk(when="every 1h", text="old", name="Old job")
        j = Path(self.desk.scheduler.journal(gone))
        j.write_text(j.read_text() + "\n- did the **thing**\n")
        self.req("POST", f"/api/schedules/{gone['id']}/delete")
        try:
            for name, b in self.browsers.items():
                ctx = b.new_context(viewport={"width": 390, "height": 844}, has_touch=True)
                # (Playwright's WebKit freezes the page on a real pushManager.getSubscription: no Web Push here)
                ctx.add_init_script("try { localStorage.removeItem('orbit.schedFold'); } catch (e) {} try { delete window.PushManager; } catch (e) {}")
                page = ctx.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                try:
                    page.goto(f"http://127.0.0.1:{self.port}/m/")
                    page.wait_for_function("() => typeof S !== 'undefined' && S.sessions && S.sessions.size > 0", timeout=15000)
                    page.evaluate("switchTab('misc')")
                    page.wait_for_selector("[data-open=schedules]")
                    page.wait_for_timeout(500)
                    page.click("[data-open=schedules]")
                    page.wait_for_selector(".scard")
                    page.wait_for_timeout(300)
                    page.screenshot(path=f"/tmp/orbit-msched-top-{name}.png")
                    names = page.eval_on_selector_all(".view .scards:first-of-type > .scard .snm, .scards > .scard .snm", "els => [...new Set(els.map(e => e.textContent))]")
                    self.assertEqual(names[:2], ["Morning news", "CI digest"] if a1["next"] < a2["next"] else ["CI digest", "Morning news"], name)
                    self.assertNotIn("Paused job", names)
                    self.assertNotIn("Old job", names)
                    card = page.locator(f".scard[data-sch='{a1['id']}']")
                    grid = card.locator(".sgrid").inner_text().upper()
                    for k in ("WHEN", "WAKES", "NEXT", "RUNS", "PHONE"):
                        self.assertIn(k, grid)
                    card.locator("summary").click()
                    page.wait_for_function(f"() => document.querySelector(\".scard[data-sch='{a1['id']}'] .stask strong\")")
                    page.click("[data-fold=off]")
                    page.wait_for_selector(f".scard[data-sch='{p_['id']}']")
                    page.click("[data-fold=arch]")
                    arch = page.locator(f".scard[data-sch='a:{gone['id']}']")
                    arch.wait_for()
                    self.assertEqual(arch.locator(".spill").inner_text(), "archived")
                    arch.locator("summary").click()
                    page.wait_for_function(f"() => /did the/.test((document.querySelector(\".scard[data-sch='a:{gone['id']}'] .stask\") || {{}}).textContent || '')", timeout=5000)
                    arch.scroll_into_view_if_needed()
                    page.wait_for_timeout(200)
                    page.screenshot(path=f"/tmp/orbit-msched-folds-{name}.png")
                finally:
                    ctx.close()
                self.assertEqual(errors, [], name)
        finally:
            for it in list(self.desk.scheduler.items):
                self.desk.scheduler.delete(it["id"])

    def test_phone_side_shows_a_website_without_the_token(self):
        sid = self.new_session("phone side")["id"]
        site = f"http://localhost:{self.port}/api/version"  # (another origin than the page's 127.0.0.1)
        self.req("POST", f"/api/sessions/{sid}/side", {"op": "open", "spec": {"kind": "web", "url": site, "title": "Site"}})
        for name, b in self.browsers.items():
            ctx = b.new_context(viewport={"width": 390, "height": 844}, has_touch=True)
            page = ctx.new_page()
            try:
                page.goto(f"http://127.0.0.1:{self.port}/m/?token=t0k3n")
                page.wait_for_function("() => typeof S !== 'undefined' && S.sessions && S.sessions.size > 0", timeout=15000)
                page.evaluate(f"openSession({json.dumps(sid)})")
                page.wait_for_timeout(600)
                page.click("#cseg [data-v=side]")
                page.wait_for_timeout(1200)
                fr = page.locator("#sideview iframe.sframe")
                self.assertEqual(fr.get_attribute("src"), site, name)  # (no ?token= to another site)
                self.assertIn("allow-same-origin", fr.get_attribute("sandbox"))
                self.assertEqual(fr.get_attribute("referrerpolicy"), "no-referrer")
                self.assertIn(f"localhost:{self.port}", page.inner_text("#sideview .sweb"))
                self.assertEqual(page.evaluate("withToken('https://example.com/x')"), "https://example.com/x")
                self.assertIn("token=t0k3n", page.evaluate("withToken('/show/abc')"))  # (this server's own: as before)
            finally:
                ctx.close()

    def test_phone_conversation_wears_its_group_tint(self):
        hued = self.group("Hued", color="#1baf7a")
        plain = self.group("Plain")
        sh, sp = self.session_in(hued, "coloured"), self.session_in(plain, "plain")
        for name, b in self.browsers.items():
            with self.subTest(browser=name):
                ctx = b.new_context(viewport={"width": 390, "height": 844}, has_touch=True)
                page = ctx.new_page()
                try:
                    page.goto(f"http://127.0.0.1:{self.port}/m/")
                    page.wait_for_function("() => typeof S !== 'undefined' && S.sessions && S.sessions.size > 0", timeout=15000)
                    page.evaluate(f"openSession({json.dumps(sh)})")
                    page.wait_for_timeout(500)
                    self.assertEqual(page.evaluate("[document.querySelector('#vconv').classList.contains('gtint'), getComputedStyle(document.querySelector('#vconv')).getPropertyValue('--gc').trim()]"),
                                     [True, "#1baf7a"])
                    self.assertNotEqual(page.evaluate("getComputedStyle(document.querySelector('#vconv'), '::after').backgroundImage"), "none")
                    page.evaluate(f"openSession({json.dumps(sp)})")
                    page.wait_for_timeout(300)
                    self.assertFalse(page.evaluate("document.querySelector('#vconv').classList.contains('gtint')"))
                finally:
                    ctx.close()

    def test_a_script_that_fails_to_load_says_so(self):
        """(#31) A page script that cannot be fetched, or dies at its top level (a duplicate top-level const), shows
        a notice on the page: once per script."""
        self.new_session("guarded")
        for name, b in self.browsers.items():  # (its own pages: the errors here are on purpose)
            ctx = b.new_context(viewport={"width": 1280, "height": 860})
            page = ctx.new_page()
            try:
                page.route("**/src/dupe-test.js", lambda r: r.fulfill(status=200, content_type="text/javascript", body="const S = 1;"))
                # while the page loads: a picture that fails and an error with no file (a ResizeObserver loop): no notice
                ctx.add_init_script("""document.addEventListener('DOMContentLoaded', () => {
                    const im = document.createElement('img'); im.src = '/api/raw?path=/no/such.png'; document.body.appendChild(im);
                    window.dispatchEvent(new ErrorEvent('error', {message: 'ResizeObserver loop completed with undelivered notifications.'})); });""")
                self.open(page)
                page.wait_for_timeout(500)
                self.assertEqual(page.evaluate("document.querySelectorAll('#scripterr .scripterr-line').length"), 0, name)
                page.evaluate("""() => { for (const src of ['/src/missing-test.js', '/src/dupe-test.js', '/src/dupe-test.js']) {
                    const sc = document.createElement('script'); sc.src = src; document.head.appendChild(sc); } }""")
                page.wait_for_selector("#scripterr .scripterr-line:nth-child(2)", timeout=5000)
                page.wait_for_timeout(300)
                lines = page.evaluate("[...document.querySelectorAll('#scripterr .scripterr-line')].map(x => x.textContent)")
                self.assertEqual(len(lines), 2, (name, lines))  # (the same script twice: one notice)
                self.assertTrue(any(x.startswith("missing-test.js did not load (it could not be fetched)") for x in lines), lines)
                self.assertTrue(any(x.startswith("dupe-test.js did not load") for x in lines), lines)
                page.click("#scripterr .scripterr-line button")
                self.assertEqual(page.evaluate("document.querySelectorAll('#scripterr .scripterr-line').length"), 1)
                page.wait_for_timeout(1600)  # (the page has run a while: its errors are no load failures)
                page.evaluate("setTimeout(() => { throw new Error('later') }, 0)")
                page.wait_for_timeout(200)
                self.assertEqual(page.evaluate("document.querySelectorAll('#scripterr .scripterr-line').length"), 1)
            finally:
                ctx.close()

    def test_file_links_in_a_note_open(self):
        """(#31) A note's links to files: beside it (relative), ~/… and file:// open; text in Orbit, anything else in its
        default app; a link's context menu."""
        import os
        root = os.path.join(self.work, "notes")
        os.makedirs(os.path.join(root, "sub"), exist_ok=True)
        os.makedirs(os.path.join(root, "Attachments"), exist_ok=True)
        other, pdf = os.path.join(root, "sub", "other note.md"), os.path.join(root, "Attachments", "paper.pdf")
        Path(other).write_text("# Other\n")
        Path(pdf).write_bytes(b"%PDF-1.4\n%\xe2\xe3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n")
        note = os.path.join(root, "index.md")
        Path(note).write_text(f"# Index\n\n[the other](sub/other%20note.md) and [the paper](Attachments/paper.pdf#page=2)\n\n"
                              f"[absolute](file://{other}) and [web](https://example.com/x)\n")
        for name, page in self.each():
            self.open(page)
            page.evaluate("window.__ext = []; openExternal = (p, how) => { window.__ext.push([p, how]); }; 0")  # (a function's value: Playwright would call it)
            page.evaluate(f"openFile({json.dumps(note)}, {{ pinned: true }})")
            page.wait_for_selector(ED, timeout=10000)
            page.wait_for_timeout(500)
            page.click(ED + " >> text=the other")  # (a plain click on a link in a note opens it)
            page.wait_for_function(f"() => S.file === {json.dumps(other)}", timeout=5000)
            page.evaluate(f"openFile({json.dumps(note)}, {{ pinned: true }})")
            page.wait_for_timeout(300)
            page.evaluate(f"openDocLink('Attachments/paper.pdf#page=2', {json.dumps(note)})")
            page.wait_for_function("() => window.__ext.length > 0", timeout=5000)
            self.assertEqual(page.evaluate("window.__ext[0]"), [pdf, "default"])  # (not text: its default app)
            page.evaluate(f"openDocLink({json.dumps('file://' + other)}, {json.dumps(note)})")
            page.wait_for_function(f"() => S.file === {json.dumps(other)}", timeout=5000)
            self.assertEqual(page.evaluate(f"linkPath('../x.md', {json.dumps(note)})"), os.path.join(self.work, "x.md"))
            self.assertEqual(page.evaluate(f"linkPath('~/a b.md', {json.dumps(note)})"), "~/a b.md")
            page.evaluate(f"openFile({json.dumps(note)}, {{ pinned: true }})")
            page.wait_for_timeout(400)
            page.click(ED + " >> text=the paper", button="right")  # (its own menu)
            page.wait_for_timeout(200)
            text = page.evaluate("document.body.innerText")
            for item in ("Open in Orbit", "Reveal in Finder", "Copy path"):
                self.assertIn(item, text)
            page.keyboard.press("Escape")


def load_tests(loader, tests, pattern):  # (its own tests only: not the skeleton's again, which it builds on)
    return unittest.TestSuite(UiSmokeTest(n) for n in loader.getTestCaseNames(UiSmokeTest) if n in vars(UiSmokeTest))


if __name__ == "__main__":
    unittest.main()
