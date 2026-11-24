# Visual system

**Quiet chrome, content leads: neutral grays, a near-black accent, color only for state, selection, links and diffs.**
The look is Codex-like for the app and Obsidian-like (warm paper) for notes. Every value below is a CSS variable or a
fixed number. No feature introduces a new color, size or radius; it picks one from here.

## 1. Principles

| # | Principle | In practice |
|---|---|---|
| 1 | Content leads, chrome stays quiet | Thin title bar and status bar, no heavy borders; one reading column |
| 2 | No boxes around content rows | Lists, tabs and the answer are flat; only the request bubble, code, dialogs and menus have a surface |
| 3 | Color means state | Neutral by default; green / amber / red only for states, blue only for links and the keyboard cursor |
| 4 | Status in words, color as a hint | `● running exec: …`, never a bare colored dot where the state matters |
| 5 | Hints on hover / focus | Row actions (rename, delete, copy, link) are hidden or dimmed until hover or keyboard focus |
| 6 | Calm motion | No smooth-scroll animation, no jumps; a conversation at its end stays at its end (new cards, resizes) until the user scrolls up |
| 7 | One of each | One icon set, one menu row style, one dialog style, one dropdown style |
| 8 | Dark mode = variable swap | Follows the system (`prefers-color-scheme`); no component has its own dark rule |
| 9 | UI text is English and short | `⌘⏎ send · ⏎ new line`; conversation content stays as written (any language) |
| 10 | Visible focus | `:focus-visible` = 2px `--accent` outline, offset 2px (exception: the conversation scroller, which shows no frame) |

## 2. Color tokens

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#f9f9f8` | `#17181b` | Page background, input fields, neutral chips |
| `--surface` | `#ffffff` | `#1f2125` | Title bar, status bar, composer, dialogs, menus |
| `--panel` | `#f5f4ef` | `#1b1c1f` | The left panel: faint warm paper, a shade under the page (not white) |
| `--ink` | `#1d1d1f` | `#e6e6e3` | Primary text |
| `--ink-2` | `#545458` | `#b4b8bf` | Secondary text, list rows |
| `--ink-3` | `#6e6e73` | `#7d838c` | Meta, hints, inactive tabs (4.6:1 on the panel: small text stays readable) |
| `--line` | `#e8e8e6` | `#2e3136` | Hairlines, borders |
| `--accent` | `#1d1d1f` | `#8b97ff` | Send button, focus ring, OK buttons, active composer border |
| `--on-accent` | `#fff` | `#14152b` | Text on `--accent` (dark: dark text, 6.8:1 on the light accent) |
| `--accent-soft` | `#efefed` | `#262a45` | Group chip |
| `--req-bg` | `#f1f1ef` | `#23263a` | Request bubble |
| `--code-bg` | `#f4f4f2` | `#26282d` | Code blocks |
| `--ok` / `--ok-soft` | `#1a7f37` / `#ecf6ef` | `#4cc172` / `#1d3326` | Completed, ready |
| `--run` / `--run-soft` | `#8a6100` / `#f8f1df` | `#e3b341` / `#3a3020` | Thinking, running |
| `--err` / `--err-soft` | `#c4292d` / `#fbeeee` | `#ff7b72` / `#3d2222` | Error, paused, not ready, danger |
| `--wait` / `--wait-soft` | `#6b6b70` / `#f1f1f0` | `#9aa0a8` / `#2a2c31` | Queued |
| `--focus` | `#7d97c4` | `#5b7bb5` | Keyboard cursor in lists (1px inset outline) |
| `--sel` / `--sel-focus` | `#e6ebf3` / `#dbe5f5` | `#2a3140` / `#33405a` | Selected row / selected row in the focused list |
| `--link` | `#2563eb` | `#6ea0ff` | Links |
| `--add` / `--del` / `--warn` | `#1a7f37` / `#cf222e` / `#9a6700` | `#3fb950` / `#f85149` / `#d29922` | Diffs, warnings |
| `--chip-bg` / `--chip-fg` | `#f3eee4` / `#5b4630` | `#3a342b` / `#e9d8bd` | Inline code in notes (warm chip) |
| `--note-bg` / `--note-ink` | `#faf9f5` / `#2f2b24` | `#1f2125` / `#e6e6e3` | Notes and Markdown files (warm paper) |

Tree tokens: `--row` 30px; `--guide` `#e3e3df` / `#2e3136`; `--folder`, `--folder-line`, `--folder-open`, `--folder-open-line` (§6 Folder icon).
File-type colours (§6 File icon) are fixed in both schemes.

Fixed (no token): favorite star `#d4a017`; dialog backdrop `rgba(0,0,0,.18)`; image lightbox
`rgba(20,20,22,.86)`; row hover `rgba(0,0,0,.04)`; small-button hover `rgba(0,0,0,.07)`.

### State → color

| State | Color | Shape |
|---|---|---|
| Card: queued | `--wait` | 6px dot + word |
| Card: thinking / running | `--run` | 6px dot pulsing (opacity 1 → .25, 1.2 s) + word + current tool |
| Card: paused | `--err` | dot + word + Continue button |
| Card: completed | `--ok` | dot + word |
| Card: error / cancelled | `--err` | dot + word |
| Session dot (tab, menu, status bar): ready / working / not ready / stopped / starting | `--ok` / `--run` / `--err` / hollow 1.5px `--ink-3` ring / `--ink-3` pulsing | 7px dot; a stopped session's name in lists is italic `--ink-3` |

## 3. Type

Fonts are settings (⌘, → Appearance; `PREFS_DEFAULT` in `web/src/settings.js`). Defaults: the interface in Inter 12
(built in, "Orbit Sans"), the conversation in Consolas 14 (Microsoft's, not shipped: where it is missing, the built-in
Inconsolata, made after it), notes in Inconsolata 15, code in Inconsolata 14 ("Orbit Mono"); Chinese from the system
(PingFang, YaHei, Noto CJK), else the built-in "Orbit CJK". Text weight (the whole app): **Regular** (default) = the
system's font smoothing; **Thin** / **Light** = `-webkit-font-smoothing: antialiased`, thinner grayer strokes;
**Medium** / **Bold** move every weight up.

| Size | Use |
|---|---|
| 10px (`--fs-xs`) | Badges (counts) |
| 11px | Meta (request id / time), field labels, menu shortcut hints, follow-up label, composer hint |
| 12px | All chrome: tabs, buttons, menus, lists, status bar, tool steps, tables, code |
| 13.5px | Request bubble, composer, follow-ups |
| 14px / 1.65 | Reading: body and answers (dense enough for long answers); ⌘, Conversation size changes it (8–40 px; requests and follow-ups follow at −0.5 px) |
| 15px | Empty-state heading |

Answer headings are relative to 14px and kept flat (bold carries the hierarchy): h1 1.3em, h2 1.15em, h3 1.05em,
line height 1.35, margins 1.2em above and .45em below.
Weights: 400 text, 500 current row / chips, 600 titles, active tab, buttons. Notes use the font and size chosen in
Settings (⌘,): any installed font; size 8–40 px (a slider for 10–28, or typed), default 15 px (Inconsolata, built in).

## 4. Sizes, radius, elevation

| Element | Value |
|---|---|
| Reading column `--col` | Full (the default): `min(100%, 2000px)`, the whole pane; Comfortable (Settings → Appearance → Reading width): `min(100%, 900px)`, centred. The input follows it; padding 28 / 22 / 24px; 40px between cards |
| Calendar tab | drawn in the page (no frame) over the main pane; the list centred, 900px at most. Head `Calendar · today N · tomorrow M` and quiet buttons: Attach session (drawn caret) · Week · open in Calendar.app · refresh (icons). Content carries the colour, the chrome is hairlines and tints. Days as `TODAY` / `TOMORROW` (11px 600, .06em, `--ink-3`, margin 18 / 12 / 4px) with `N blocks · show`. A meeting row: 44px, no box, a hairline (`--hover`) below; time `09:00–09:30` (UI font 500 12px, tabular figures, `--ink-2`, 84px); the organizer's initial (22px, `--r-s`, `--sel` with `--av-fg`; a photo when one is known); title 13.5px 600; badges as coloured text, no pill: `now` (`--ok`) / `overlap` (`--warn`), 600 11px; `organizer · accepted/total` (12px `--ink-3`); Join (a quiet text button). The meeting now / next: its time in `--accent` 600, nothing louder; past dimmed; hover a faint tint; keyboard row: a 3px `--focus` bar on the left + a 9% `--focus` tint, radius 0 8px 8px 0. Opened in place: its body starts under the title (138px), a faint tint: a line (day · time · calendar · place · repeats), people chips (accepted: a green border; declined: 45% and struck), Invitation (`<details>`), Related sessions, Prep · Copy link; the note box under it (`--surface`, `--line`, `--r-m`; head `Notes · ⏎ to write` · Edit · Open as file). Schedules use the same keyboard row and times |
| Meetings popover | ⌘⇧C, or a click on the status bar's meeting: a popover right above that meeting (not a dialog), up to 520px wide, `--surface`, `--r-m`, `--menu-shadow`. Today's and tomorrow's meetings under `TODAY` / `TOMORROW` heads, as compact Calendar rows (34px: time · initial · title · `now` / `in 24m` · organizer · accepted/total · a quiet Join). The cursor starts on the current or next meeting. A click or ⏎ opens that meeting in the Calendar tab; ⌘⏎ or Join joins; right-click shows its menu. The foot has Open the Calendar and its keys. Esc, ⌘⇧C again or a click elsewhere closes it; the status bar's meeting shows as pressed while it is open |
| Meeting reminder | fixed bottom-right card (`--surface`, `--menu-shadow`): 📅 time title, `in N min`, Join (primary) · ✨ Prep · Open · × ; 10 minutes |
| Plugin left tab, plugin main tab | A plugin's left-panel tab: after the built-in ones (by order), its icon when it gives one (title on hover), a badge pill (9.5px 600, `--accent`; warn `--run`, error `--err`); its page fills the panel. A plugin main tab: its icon + title in the tab row, an unread dot like a session's; its page fills the main pane |
| Agent terminal (⌘T) | The ⌘J pane showing the agent's own terminal: head `the agent's terminal · <session> (<program>)`, Detach instead of Kill, no Open in iTerm. A session that needs the user there: an amber `!` pill on its tab (title: what it waits for) |
| Status bar plugin items | Right of the status bar, before Used: each a 11px text button (`--ink-2`; warn `--run`, error `--err`; hover `--btn-hover`), title: its tooltip · the plugin; a click opens its panel (side pane) or URL. More menu: the plugins' panels and menu actions, after a separator |
| Status bar meeting | 📅 time title + a chip: `25m` (`--hover`), `soon` ≤ 10 min (`--run-soft` / `--warn`), `now` (`--ok-soft` / `--ok`) |
| Title bar | 38pt at the default interface size and zoom. Tab heights, insets and control spacing scale with Interface size. The desktop layout reserves at least 32pt for the native window buttons. Its lead: ☰ (a 14px glyph in a 28px button) · the 17px logo · the group pill, 16px and 14px apart, all centred on the bar, as are the app's window buttons (`TRAFFIC_Y`). |
| Control ladder | list row 30px (`--row`) · group head, panel tab, chip, status bar 26px · every small control (`.btn.sm`, `.qbtn`, `.ibtn.sm`, find field) 26px, so a head's buttons are one height · menu row 28px · dialog button, title-bar button, Settings field and dropdown 30px · a button in a row is a quiet text button (`.btn.quiet`: its border on hover) |
| Panel heads | 40px at the default size. The left panel head, its tabs and its filter row scale with Interface size. |
| Radius | `--r-s` 6px (buttons, rows, inputs, chips) · `--r-m` 10px (cards, code, dialogs, menus, composer) · 999px pills |
| `--shadow` | light `0 1px 2px rgba(0,0,0,.04), 0 4px 16px rgba(0,0,0,.04)`; dark `0 1px 2px rgba(0,0,0,.3), 0 6px 20px rgba(0,0,0,.3)` |
| Menu shadow | `0 10px 30px rgba(0,0,0,.14), 0 1px 3px rgba(0,0,0,.06), 0 0 0 .5px rgba(0,0,0,.16)` (a fine edge, no border; dark: deeper, a light edge) |
| Page zoom | default 1 (⌘⇧= / ⌘⇧- in 0.1 steps, ⌘0 resets) |

## 5. Icons

One set: Lucide-style line icons, 16×16 viewBox, stroke 1.5, `currentColor`, round caps and joins, rendered by one
helper `I(name, size)`. No emoji or characters as UI icons. Emoji appear only inside content.

## 6. Components

| Component | Spec |
|---|---|
| Tab | After a browser's tabs. The title bar is white (`--strip` = `--surface`, in dark mode too); tabs sit on its foot under a 6px gap (`--bar` − 6px), 12.5px, up to 210px with ellipsis, padding 12 / 30px: the session's status mark (the same marks as the left panel) or the file's icon · name · its × at the right (always there; the unsaved dot in its place). Inactive: bare, `--ink-2`, a 15px hairline between neighbours (none beside the active or hovered tab), on hover a `--hover` tint 12px less tall (`--bar` − 12px) with all corners rounded. Active: the page's colour (`--bg`), `--ink` 500, top corners 9px, a 1px `--line` outline on its top and sides, open at its foot into the content below (the strip's line stops at its sides); when the tabs wrap to a second row it is a lifted pill instead. Drag a tab to reorder it: it follows the pointer, lifted (a soft shadow), and the others slide aside (.15s) to open its place; released, it settles there; Esc puts it back; ⌘⌃H ⌘⌃L do the same. The title bar holds only the tabs and the ± badge (`+added −deleted` lines of the uncommitted changes, 12px 600 `--ink-3`, no border; `± N` files when no line counts): notifications, the next meeting and ⋯ live in the status bar |
| Request bubble | Right-aligned, `--req-bg`, radius 14px, padding 12 / 18 / 8px, max 86% wide, 13.5px; meta inside at its foot, right-aligned 11px `--ink-3`, dimmed (.6) until hover: `link · copy · edit · resend` (icon + word; `withdraw` while queued) then `#id · M/D, time`. Right-click an answer (no selection), when plugins declare answer actions: Copy answer, then those actions (their first line of output as a toast). A request a plugin sent starts with its origin: a plug icon and `via <label>` (11px `--ink-3`; the label a `--link` link when it gave a URL; title: the plugin) |
| Answer | No box, full column, 14px; head line 12px: dot + state word in the state's color (Queued, Thinking, Running, Done, Paused, Error, Cancelled; 500) + current tool in `--ink-3` |
| Answer timeline | The turn as it happened: what the agent said between tool calls in full (Markdown, `--ink-2`), each run of tool calls as one group, then the answer (`--ink`). The group running now is open; the rest fold; a click on a group is remembered |
| Answer process | The timeline is one fold under the head line: a 12px `--ink-3` line, caret · `Working` / `Worked for 3m 12s` · `N messages` · `N steps` · `N subagents`; open, its body under a 2px `--line` rule (12px in). Open while the request runs (a click folds it), folded once the answer is there (a click opens it); a card that ended without an answer keeps it open. Each click is remembered per card; Show in conversation (subagents bar) opens it |
| Tool steps | One folded summary line per group (12px `--ink-3`): ▸ `N steps · ran 10 commands, read 3 files` (what the steps did, by kind, top 3; tool names in the tooltip); open = list max 220px high with a 2px `--line` left rule; done mark `--ok`, current `--run`. Only the caret turns when it opens |
| Subagent | Its own block in the timeline: `--surface`, 1px `--line`, radius 8, padding 4 / 8px; head = caret · step mark · type (600 `--ink`) · task (`--ink-2`, ellipsis) · at the right `N steps · what they did · state` (11px `--ink-3`; `running in the background` until its report comes); open = what it said (12px `--ink-2`) and its tool rows, then "Report" (11px `--ink-3`) over its report (Markdown, 1px smaller, `--ink-2`, max 320px, scrolls). Open while it runs, folded when done |
| Subagents bar | Above the composer, only while a subagent of this session runs (foreground or background, any request): the composer's width, 1px `--line`, `--surface`, `--r-m`, 6px above it. One 26px row each: a caret · a pulsing `--run` dot · type (600 `--ink`) · task (`--ink-2`) · what it runs now (mono 11px `--ink-3`) · steps · elapsed (11px tabular, ticking), right. More than 4: three, then `N more running · show all`. A click opens the row there, one at a time (the caret turns): under it, aligned with its type, what it says and runs as in its card (mono summaries, the current step pulsing), a scroll of its own (at most 260px or 36vh) that follows the latest unless scrolled up; at its foot, right, `Show in conversation` (11px `--ink-3`) shows it open in its card (scrolled to the middle, a brief `--focus` ring). A second click folds it. The input keeps the keyboard; the details stay selectable. A background subagent whose agent's process ended (a restart) is marked ended, never left running |
| Conversation scroll | Per session, kept across resizes, new answers and reloads: stuck to the end (it follows every new line), or held where the user scrolled to (the card at the top of the view and its offset). Only the user changes which: their own scrolling (wheel, trackpad, scroll bar, page keys) holds it; ⌘L, Latest or sending a request sticks it to the end again |
| Request bubble | Plain text as written, its code drawn as code (fenced blocks: mono on a 6% `--ink` tint; `inline` the same, small); more than 12 lines fold (a fade, `Show all N lines`, kept per card) |
| Status marks | One set for a session wherever it is listed (left panel, tabs, ⌘O, @@, the status bar, related sessions): working / starting, or subagents still at work in the background (`agents` > 0, after its request ended), a spinning `--run` ring; needs you a filled `--run` !; stopped a hollow 7px ring; not ready ✕ `--err`; else how its last request ended (✓ `--ok`, ✕ `--err`, pause `--run`, stopped-circle `--ink-3`); ready with nothing asked a small `--ink-3` dot. Drawn icons (12px), never characters |
| Follow-up | Below the answer: `--surface`, 1px dashed `--line`, `--r-m`, 13.5px `--ink-2`; label "↳ Follow-up" 11px 600 |
| On its own (an `auto` card) | No bubble (not the user's words; no edit, resend, link): on the left, a 2px left rule (`--run` 40% into `--line`), a 12px `--ink-3` head (bell, "On its own" 600 `--ink-2`, "· after a background agent finished", the time at the right) and the task in the reading font; then the answer part as a request's |
| Card footer | 12px `--ink-3`: elapsed · steps · compactions. Shown while the request runs; on a finished card only on hover (its space stays) |
| File reference | Inline code that names a file (`push_to_s3.py:104`, `lib/x.py`, `X.java:165-167, 183` = its first range): a quiet 1px dotted `--ink-3` underline, pointer cursor; hover = `--link` colour; click opens it in the side pane at that line (⌘-click: a tab of its own); right-click: Open in side pane · Open in new tab · Open in <⌘, editor> · Copy path · Copy text |
| Code block | `--code-bg`, `--r-m`, padding 14 / 16px, 12px mono, scrolls sideways; Copy button top-right (11px, appears solid on hover) |
| Table | 12px, bottom hairlines only, header 2px rule and `--ink-2` 600, padding 7 / 14px, scrolls sideways in its own box |
| Image | max 100%, `--r-s`, 1px `--line` border, zoom-in cursor → lightbox |
| Composer | `--surface` card, 1px `--line` (→ `--accent` while focused), `--shadow`, `--r-m`, grows up to 260px; placeholder "Message the agent…" (keys and @@ in its tooltip). Bottom row, left to right: attach · mode chip (`--surface`, 1px `--line` ring, 500: `bypass` with a `--warn` ⚡, `accept edits`, `plan`, `ask`) · what the request will run on, spaced 12px, no dots (model by its name, `Claude Opus 5.5 High`; `ctx 362k` = the context the last model call read; `⟲N` compactions; 12px `--ink-3`, hidden below 420px) · Send at the right = a 30px round `--accent` button with an up arrow, disabled at .4 opacity while empty |
| Button | `--surface`, 1px `--line`, `--r-s`, 12px `--ink-2`; hover: `--ink` text, `--ink-3` border. Primary: `--accent` fill, white. Danger: `--err` fill |
| Menu / popover | After Chrome's on macOS: `--menu-bg` (94% opaque, blurred 30px behind), radius 10px, padding 5px, menu shadow, at least 220px wide; rows 24px, radius 5px, 13px `--ink`, text 16px in (row padding 11px); hover, selection and focus = `--menu-sel` (a quiet grey); shortcut hint right-aligned 12px `--ink-3`, submenu chevron `--ink-2`; `<hr>` separators `--menu-line`, inset 16px to the text, 5px above and below; items grouped by purpose; no red rows. A submenu's first row sits level with the row it opens from. The accelerator letter is underlined (`--ink-3`, 1.5px) only in a menu opened from the keyboard (⌘E, →); with the mouse, none (the letters still work) |
| Dialog | Backdrop `rgba(0,0,0,.18)`; box 18vh from the top, 380px wide (a form 580, shortcut sheet 860; Settings is its own layout, §7), never past the window's foot: taller, it scrolls and its buttons stay in sight (vh divided by the zoom), `--surface`, `--r-m`, `--shadow`, padding 14 / 16px; title 12px 600; inputs `--bg` + 1px `--line`, `--r-s`, padding 7 / 10px (→ `--accent` border on focus); field label 11px `--ink-3`; error line 11px `--err`; buttons right-aligned: Cancel (bordered), OK (primary or danger, naming its key when it is not plain ⏎: `Start ⌘⏎`); an optional key hint (11px `--ink-3`) at the left of the button row |
| Dropdown in a dialog | Never a native `<select>`: a button + our own list in the menu style, a small filled ▾ (`--ink-3`) at the right. A field that takes typing and offers recent values (the New session folder) is a combo box: the same ▾, and ↓ opens the list |
| Segmented control | A choice among 2–4 short options: `--bg` track, 1px `--line` ring, padding 2px; options 26px, 12px `--ink-2`; current = `--surface`, `--ink`, 600, a ring and a 1px shadow. ←→ move while it has the keyboard |
| Toast | Bottom-centre (110px from the bottom), `--ink` background, white 12px, `--r-s`, opacity .92; a clickable toast is underlined |
| Left-panel list | Flat. Rows 30px (`--row`): group head 12px 600 (its key ⌘2… as a key cap: an 18px cell, 1px `--line` border, radius 5, `--surface`, a 1px `--line` shadow below, 600 10.5px `--ink-2`; `--ink-3` border on the current group; ★ ⚡ + take the same cell without a cap, so every name starts at 34px; a fold chevron right after the name (10px `--ink-3`, faint until hover / cursor; right-pointing when folded: its sessions hidden, a filter still looks inside; ← → on the group row, or a click on the chevron; kept per browser); `running/total` right 11px `--ink-3`; empty groups hidden but the current one), row 12px `--ink-2`, indented 28px; a session row (one line, 30px), indented to its group's name (34px): a status mark (12px: ✓ `--ok` done, ✕ `--err` failed / not ready, ‖ `--run` paused, ⊘ stopped, a spinning `--run` ring while working, a filled `--run` ! when it needs you; blank before its first request) · its agent's mark (14px: Claude Code, Codex, agy by their makers' marks, brands.js; a plugin harness by its own icon, else its initial; faded when stopped) · name (`name · key` for twins; 650 when unread; italic `--ink-3` when stopped) · ★ · machine pill · when it last moved, right (11px `--ink-3`). Its tooltip (on hover, and on the keyboard row): name, folder, agent · model, how the last request ended and its answer's first line; hover `--hover`; current = `--sel`, `--ink`, 500; keyboard cursor = 1px `--focus` inset ring on every row, the row's own radius. Panel tabs: flat buttons, no track, 26px 600 12px `--ink-3`; current = `--surface` + 1px `--line` ring; a plugin's tab shows its icon. Filter box: 28px, 1px `--line` (long text gives way at its start: the caret and the last characters stay in sight), `--surface`, 4px 8px, a short hint (`Filter sessions`; the long one on hover) that ends inside the padding, the same gap both sides; the buttons beside it 28px, icon centred; `--ink` border while typing (the keyboard stays in the list). Tree head: the folder, 11px mono `--ink-3` (`Notes · ~/…`). Misc: one row per tab (Calendar, Schedules): 13px icon, name, hint right (the next meeting `10:00 weekly sync` / `off` / `none`; the next run time); ⏎ opens it as a main tab |
| Left panel, more | Drag a session to reorder it, or onto another group (its row or its sessions) to move it; drag a group's head to move the group with its sessions; the dragged rows follow the pointer, lifted, and the others slide aside; Esc puts them back. A stopped session shows Resume where its time is, on hover. The details strip at the panel's foot (11.5px, a hairline above): for the keyboard row or the hovered session, its mark · how it stands and its last answer's start, then folder · agent · model (`--ink-3`); tooltips are only for cut-off names. Names start at 34px on every tab (Sessions, Files, Notes, Misc). The keyboard cursor on the current row: one deeper tint, no ring |
| Panel heads | 40px heads (file, Review, shell, Calendar, Schedules): secondary actions are quiet 26px text buttons (`.qbtn`, icon + word) or 26px icon buttons; at most one primary. A side tab's file head is one slim 32px row (the path, Save, ⋯) under the side tab strip. The status bar sits at the window's foot, under the shell |
| Row actions | Schedules: on hover or the keyboard row, Run now · Pause/Resume · Edit · ⋯ (26px icons, after the next run time); Calendar: a ⋯ after Join (its room kept). Each list's foot names at most four keys |
| Empty session | `Nothing asked yet` (15px), a line, its folder · agent · model (11.5px `--ink-3`), then three small buttons: `/` a skill or command · `@` a file · `⌘J` a shell here |
| File tree row (Files / Notes) | 30px (`--row`), 13.5px; per depth a 14px indent guide (1px `--guide` line at 6px); then a 14px twisty cell (10px chevron, `--ink-3`, rotates 90° when open, .12s); then an icon cell 18px + 4px gap; name ellipsized; git status letter right-aligned (600 11px mono: M `--warn`, A/U `--add`, D `--del`) and the name tinted the same (D struck through); folders `--ink`, files `--ink-2`; keyboard row = 1px inset `--focus` outline, current file = `--sel` |
| Folder icon | 15 × 12 folder outline: closed `--folder` fill, `--folder-line` stroke; open `--folder-open` / `--folder-open-line` (light: `#e6e6e2` / `#a6a6a0`, `#dbe5f5` / `#7d97c4`; dark swaps them) |
| File icon | 12 × 14 page outline with a folded corner, stroked in the type colour, a 5.5px 600 mono glyph inside: py `#3572A5` "py" · js `#d4a017` · ts `#3178c6` · md `#5b7083` · json `#c08b30` "{}" · html/xml `#e34c26` "<>" · css `#7b4fd6` "#" · sh `#3f9142` "$" · yaml/toml/ini `#a0522d` "≡" · images `#c2185b` "▣" · sql `#00758f` "db" · rs `#b7410e` · ipynb `#f37626` "nb" · csv/parquet/pkl `#2e7d32` "▤" · lock `#8e8e93` "⊘" · Dockerfile/Makefile `#5b7083` "⚙" · txt/log `#8e8e93` "≣" · other `#9a9aa0`, no glyph. The same icon on file tabs |
| Tree head | the folder as `~/…` path, 11px `--ink-3`, above the rows (the branch, or the note count, at the right); the filter box above it |
| Tooltip | a name cut off by its box (tree row, session preview, tab, tree head, a finder row's name or path) shows its whole text — for a file the full `~/…` path — in a `--surface` box with `--menu-shadow`, 12px, max 640px, wrapping anywhere; 4px below the name (above if no room). On hover after 350ms; in the left panel on the keyboard row at once; in a finder on the selected row once the keys rest (500ms); never when the text fits; gone on scroll, click, blur or leaving the panel |
| Session row | two lines, min 40px: state dot · name (12px) · star · machine tag · folder name (11px `--ink-3`, right); under the name a preview, 11px `--ink-3`, one line, ellipsis: the last answer as plain text ("Failed: …" when it failed), "Working: <request>" while one runs, else the folder path (fainter) |
| Machine tag | Another machine's session, on its row and tab: its name, 10px, `--accent-soft` fill, `--ink-2`, radius 4, padding 0 5px; .5 opacity and struck through while the machine is unreachable. This machine's sessions carry none |
| Machine filter | Above the session rows, only when there are other machines: chips All · This Mac · each machine (22px, 11px `--ink-2`, 1px `--line` ring, radius 11); the current one filled `--ink` on `--surface` text; an unreachable machine's chip at .55; ⌥⇧[ ⌥⇧] step through them |
| Status bar | 26px, `--surface`, top hairline, 12px `--ink-2`; segments split by 1px `--line`: dot + state (a quiet mono chip: 11px `--mono`, `--bg`) · harness (model, context and mode sit next to the input); on the right plan usage (`Used`, then every harness, always, in a steady order: its name, a 44px bar, % and its reset time; red ≥ 90 %, amber ≥ 75 %; the session's own harness name a shade darker; narrow, the reset times go first; hover: every window; click: details) · the folder · 🔔 notifications · next meeting · ⋯ (22px quiet icon buttons; their popovers open upwards); chips neutral (`--bg`, `--ink-2`, `--r-s`) |
| Jump to latest | Pill fixed at bottom-right (28px from the right, 120px from the bottom), `--surface`, `--shadow`; shown only when scrolled up |
| Empty state | Centred 18vh from the top, `--ink-3`; heading 15px 600 `--ink-2`; always says what to do next |
| Notes / Markdown files | `--note-bg` paper, `--note-ink`, inline code as `--chip-bg` / `--chip-fg` chips, bold headings, note font from Settings |

## 7. Settings (⌘,)

**A sidebar of sections, a pane of rows** (like ChatGPT's and Codex's settings). The dialog is 780 × 580 (radius 14,
`--surface`, a deep shadow): a 200px sidebar (`--bg`, hairline right) with × at the top and one item per section
(15px icon + label, 34px, radius 8; hover `--hover`, the current one `--btn-hover` and 600), then the pane: the
section's title (17px 600, hairline under it) and its rows. A row is the label (13px `--ink`) over what it does (12px
`--ink-3`), the control at the right; rows are split by hairlines; a row whose control is wide (the font list) stacks it
below. A sub-heading (11px 600 uppercase `--ink-3`) splits a section in parts. Every change applies at once (no OK /
Cancel); × or Esc closes; the last section shown opens next time. Appearance is per device (localStorage; side tabs read
it at load and on change); Open files in, Calendar and Machines are the server's.

Controls: a switch (36 × 20, `--line` off, `--ok` on, white knob); a segmented control; a dropdown; a size (slider with
`--link` fill + a number field + `px`); small buttons (Copy URL, Remove, Add).

| Section | Rows |
|---|---|
| General | Open files in (dropdown: JetBrains · VS Code · Side tab · Default app; another machine's files always open in Orbit) · Calendar (switch) · Keyboard shortcuts (Show all, ⌘/) |
| Appearance | Text weight (Full · Light; default Light) · Conversation size (10–28 px slider, 8–40 typed, step 0.5; default 13.5) · Fonts (default: the built-in Inter for the interface and conversation, Inconsolata for code and notes; "· built in", first in every list; Defaults → Reset) · Interface (`--font`) · Interface size (10–15 px, default 12: `--ui-k` = size / 12 scales every interface font size, written for 12; code blocks and diffs keep the code size) · Conversation (`--read-font`: answers, requests, the input; default: the interface font) · Note (`--note-font`) · Code (`--mono`: code files, code in answers and notes, the ⌘J terminal; presets SF Mono, Menlo, Monaco, Consolas; default Inconsolata). A font this machine lacks falls back to one Orbit ships (`web/vendor/fonts`, SIL OFL, fetched only when used): "Orbit Sans" (Inter) for the sans fonts, "Orbit Mono" (Inconsolata, made after Consolas) for the monospaced; Chinese: the built-in fonts' is "Orbit CJK" (Noto Sans SC, i.e. Source Han Sans; `vendor/fonts/orbit-cjk.css`, 95 slices by unicode-range, 4.3 MB in all, a page fetches only the slices of its characters; emoji and symbols left to the system's colour ones), a system font's the system's (PingFang SC, Microsoft YaHei, Noto Sans CJK SC), else Orbit CJK. SF Pro, SF Mono, PingFang and Consolas themselves may not be shipped (Apple's and Microsoft's licences) — each a button showing the font in itself; a click opens the list under the row (filter, presets, every installed family, each shown in itself; ↑↓ pick, ⏎/Esc close; one open at a time) · Note size (default 15; note font default Inconsolata) · Code size (code files and the terminal; default 14) · a preview (Latin, CJK, inline code) |
| Machines | This server: HTTPS for other machines and the phone (switch; its URL with Copy URL; iPhone…: a QR code of the phone's app URL and the three steps to the home screen) · Plain HTTP on the network (switch; its URL with Copy URL, New token…) · Other machines: each with its state, URL and sessions, Remove; Add (URL + name) |
| Plugins | Refresh · per plugin (builddoc/plugins.md): Loaded / Not loaded / Disabled (a dot: green, red, grey; the error or description; its folder) · Agents · each Service (dot, state, since, error; Log toggles the last 200 lines below, Restart) · each secret (Set / **Not set**; a password field, Save, Clear: this machine only) · For agents (`orbit-agent p NAME`, the hint) |
| About | The server this window shows (version · plugin API) · Connect to another server (the app) · Notes folder · Agents |

Keys: ↑↓ (⌃N ⌃P) in the sidebar pick a section, ⏎ or → go into its pane; Tab cycles everything; ←→ move a segmented
control; in the font filter ↑↓ / PgUp PgDn / ⌥⇧, ⌥⇧. move in the list; ⏎ in the Add fields adds; ⌘, or Esc closes.
