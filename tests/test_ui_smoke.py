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

import test_skeleton

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # (the suite runs without it: these are skipped)
    sync_playwright = None

READY = "() => typeof S !== 'undefined' && S.groups && S.groups.length > 0 && S.sessions.size > 0"


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


def load_tests(loader, tests, pattern):  # (its own tests only: not the skeleton's again, which it builds on)
    return unittest.TestSuite(UiSmokeTest(n) for n in loader.getTestCaseNames(UiSmokeTest) if n in vars(UiSmokeTest))


if __name__ == "__main__":
    unittest.main()
