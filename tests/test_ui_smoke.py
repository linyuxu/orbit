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
            code, sess = self.req("POST", "/api/sessions", {"cwd": str(d), "name": name})
            self.req("POST", f"/api/sessions/{sess['id']}/update", {"group": g})
            roots[name] = (sess["id"], os.path.realpath(d))
        for name, page in self.each():
            self.open(page)
            page.evaluate(f"switchTo({json.dumps(roots['op-here'][0])})")
            page.wait_for_timeout(400)
            page.evaluate("openFinder('Files')")
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
              const L = {}, shown = [], opened = [], posted = []; let wins = [], focused = 0;
              const self_ = { addEventListener: (t, f) => { L[t] = f; }, registration: { showNotification: async (t, o) => { shown.push([t, o]); } },
                clients: { matchAll: async () => wins, openWindow: async u => { opened.push(u); }, claim: async () => {} }, skipWaiting: async () => {} };
              new Function('self', 'caches', 'location', src)(self_, {}, { origin: 'https://orbit.test' });
              const run = async (type, extra) => { const ps = []; L[type]({ waitUntil: p => ps.push(p), ...extra }); await Promise.all(ps); };
              const pushed = d => run('push', { data: { json: () => d, text: () => JSON.stringify(d) } });
              await pushed({ title: 'Report ready', body: 'Q3', sid: 'abc 1', tag: 'sched:x:1', url: '/m/#s/abc%201' });
              await pushed({ title: 'Evil', sid: '//evil.example', url: 'https://evil.example/' });
              await pushed(null);
              const tap = n => run('notificationclick', { notification: { data: n.data, close() {} } });
              await tap(shown[0][1]);
              await tap(shown[1][1]);
              wins = [{ url: 'https://orbit.test/m/#s/old', focus: async () => { focused++; }, postMessage: m => posted.push(m) }];
              await tap(shown[0][1]);
              return { shown, opened, posted, focused };
            }""")
            self.assertEqual(r["shown"][0], ["Report ready", {"body": "Q3", "tag": "sched:x:1", "data": {"sid": "abc 1", "url": "/m/#s/abc%201"}, "icon": "/m/icon-192.png"}])
            self.assertEqual(r["shown"][2][0], "Orbit")  # (an empty payload: still a notification, as iOS requires)
            self.assertEqual(r["opened"], ["/m/#s/abc%201", "/m/#s/%2F%2Fevil.example"])  # (this app's pages only)
            self.assertEqual((r["posted"], r["focused"]), ([{"t": "open", "sid": "abc 1"}], 1))
        finally:
            ctx.close()

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
