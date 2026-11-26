# Interaction contract

**Keyboard first, Emacs-flavoured, and the same everywhere.** A user who learns one list, one menu, one dialog and one
panel knows them all. A new feature reuses the patterns below and adds keys only to the table in §8. That table is the
only list of shortcuts: the ⌘/ sheet is generated from the same data, and every button's tooltip names its key
(`Keyboard shortcuts (⌘/)`).

## 1. Principles

| # | Principle | Rule |
|---|---|---|
| 1 | Everything by keyboard | Every action has a key or sits in a ⌘E menu; the mouse is optional |
| 2 | One home position | The current tab's input is home. Esc always leads back to it (§4) |
| 3 | Keys act on what has the keyboard | ⌘E, ⌘F, ⌘W, ⌘T, ⌘⇧[ ⌘⇧] read the focused region first (§3), then the main tab |
| 4 | Same pattern, same keys | Lists, menus, dialogs and panel toggles each behave one way (§5–§7). Movement keys mean the same everywhere (§2) |
| 5 | Never steal focus | Background events (an answer arrives, an agent opens a file, a side tab loads) never move the keyboard; they toast or mark the tab |
| 6 | Never lose input | Unsent drafts survive tab switches, ⌘⇧R and restarts; a failed send puts the text back |
| 7 | Confirm only what is destructive | Closing a working session or deleting a file asks; ⏎ = OK, Esc = Cancel. Everything else acts at once |
| 8 | Focus belongs to the tab | Each tab remembers whether the keyboard was in its main pane or its side pane and gets it back on return, whatever switched the tab (⌘⇧[ ⌘⇧], a click, ⌘1…9, ⌘O, ⌘- ⌘=). A tab not visited yet: where the keyboard is now (the side pane only if the tab has a side tab). Switching never moves the keyboard between panes on its own; ⌘⇧[ ⌘⇧] typed in the side pane switch tabs directly. Lists and dialogs that have the keyboard keep it |
| 9 | Keep the user's focus | A look does not move the work: a click in a list shows its row where the current work stays in front (a file clicked in the Files panel opens beside, as a preview the next click replaces; the keyboard stays in the list). Going into something is explicit: ⏎ or a double-click (the main pane, the keyboard in it) |

## 2. The key handler

- **One global `keydown` listener in the capture phase.** Shortcuts work while an editor, the shell or a side tab has
  focus. A key is handled once, then `preventDefault` + `stopPropagation`.
- **Match ⌥ and bracket keys by `e.code`**, not `e.key`: WebKit turns ⌥ letters into symbols (⌥V = √).
- **Respect IME composition**: ⏎ and Esc that only confirm a candidate (`isComposing` or `keyCode 229`) are ignored by
  every handler.
- **Native-only keys**: the app shell owns Esc, ⌘Z / ⌘⇧Z and ⌘\`; the page never sees them. The shell forwards
  them to the page (`deskEsc`, `deskKey`), and the page routes them like typed keys. A new native shortcut means a change in
  the shell (`main.rs`) and a rebuild.
- **Esc never leaves macOS full screen**: a last window-level handler swallows any Esc that nobody handled.
- **A dialog owns every key**: while a dialog, finder or context menu is up (§7), nothing reaches the view behind it.
  The app's ⌘ shortcuts are suspended (except ⌘C / ⌘V / ⌘X / ⌘A / ⌘Z and the key that opened it, which closes it), and
  so are the app-wide non-⌘ handlers: panel resize (⌃⌥HJKL), ⌃X prefixes, paging and top / end of the view behind.
  Only an editor inside the dialog keeps its own keys. The page tells the shell (`/api/modal`) so native shortcuts wait too.
- **One set of movement keys** for every surface that moves (lists, finders, menus, pickers, the conversation, panels,
  editors):

  | Keys | Meaning |
  |---|---|
  | ↑ ↓ | One row / line |
  | PgUp PgDn · ⌥V ⌃V | One page up / down |
  | Home End · ⌥⇧, ⌥⇧. | First / last row, top / end (Emacs M-< M->) |
  | ⌃L | The current row (lists) or cursor line (editors) to the middle, pressed again to the top, again to the bottom (Emacs recenter-top-bottom; a press more than 1.5 s later starts at the middle) |

  In every one-line text field (the left panel's filter, ⌘O and the other finders, dialog fields, Settings, the find
  bar) the editors' Emacs keys edit the line: ⌃A ⌃E start / end, ⌃F ⌃B a character, ⌥F ⌥B a word, ⌃D ⌥D delete
  forward, ⌫ ⌃H ⌥⌫ ⌃W delete back, ⌃K ⌃U kill to the end / start, ⌃Y yanks what was killed. ↑↓ ⌃N ⌃P stay the list's.

  ⌥⇧, / ⌥⇧. are delivered as Home / End to whatever has the keyboard; an editor handles them itself; with nothing to
  move they scroll the nearest scrollable box (inside the dialog when one is up). Paging and top / end stop at the ends (they never
  wrap).

## 3. Focus regions

| Region | Contains | Notes |
|---|---|---|
| Main panel | Conversation + input, or a file / note editor | Home. `focusMain()` puts the keyboard on the tab's input (or editor) |
| Side tab | An embedded page (`/?embed…`). Each main tab keeps its own list of side tabs, shown as small tabs in the side pane's head (click to switch, × to close); the list survives a reload | Handles its own ⌘F and Esc; ⌘E there opens the menu of the side tab's file (Esc returns to it); forwards the app's other shortcuts (and ⌥⇧[ ⌥⇧]) to the parent page; never opens side tabs, never writes main state, never takes the keyboard unless asked |
| Left panel | Sessions · Files · Notes · Misc lists | Typing filters the list (fuzzy) and the list keeps the keyboard |
| Shell | The ⌘J pty | Esc goes to the shell; Esc Esc leaves it |
| Dialog / finder / menu | Modal (§7) | Traps the keyboard while open |

⌘⇧O switches the keyboard between the main panel and the side tab. Focus place and layout are saved every second
and restored after a reload.

## 4. Esc

The first matching rule wins; then the key is consumed.

1. An image / PDF preview is open → close it.
2. A dialog, finder or menu is open → close it (a dropdown list inside a dialog closes first, not the dialog).
3. The find bar is open → close it, clear the highlights.
4. The shell, side tab or left panel has the keyboard → its own Esc (left panel: back to the input, the panel stays).
5. The input has the keyboard → move to the conversation (↑↓ PgUp PgDn ⌃V ⌥V scroll it, instant steps, no animation).
6. Anywhere else, including after focus was lost → back to the current tab's input.

## 5. Panels and layout

- **Toggle in three states**, for every panel key (⌘\` left panel, ⌘J shell, ⌘⇧I ± Review side tab): hidden → show +
  focus; shown but not focused → focus; focused → hide (focus back home). The left panel reopens on the tab it was on.
- **⌃X 1** closes every panel beside the main one, and every side tab of the current tab (Emacs "only this window").
- **Resize**: drag any split edge, or ⌃⌥H / ⌃⌥L (⌃⌥K / ⌃⌥J vertical) to move the focused panel's edge. The near edge
  moves first if the neighbour can give room, else the other edge. Splits go down to a sliver (minimum 48–120px per
  panel, ratios 5–95%); drag math divides by the page zoom. ⋯ → Reset panel sizes.
- **A request whose agent died** (its process went away mid-request, not Stop) goes on by itself: "Continue where you
  stopped" after 3 s, then 6 s; after 2 tries it waits, paused, for Continue. A request the server cut off when it
  stopped (a restart) goes on after the restart the same way.
- **Long conversations** draw only their latest 40 requests; the earlier ones come in above as you scroll near the top
  (or with "Show N earlier requests"), keeping what is on screen in place. ⌘F and a jump to an early card (bell, ⌘R)
  draw all first. (Hundreds of cards in the page made every switch, resize and side pane slow.)
- **Follow**: the left panel reveals the main tab as you switch, except while the panel has the keyboard (⏎ there
  forces it). Toggle in the panel head.

## 6. Lists

Every list (left-panel lists, the ⌘O finder, menus, the bell list, the shortcut sheet, Schedules) works the same:

| Key | Action |
|---|---|
| ↑ ↓ | Move the keyboard cursor (1px `--focus` outline) |
| PgUp PgDn · ⌃V ⌥V · Home End · ⌥⇧, ⌥⇧. | Page / first / last row (§2) |
| ⌃L | Recenter the current row (§2) |
| typing | Filter (fuzzy, by name / title / folder / group); the list keeps the keyboard |
| ⏎ | Open the row (⌘⏎: open pinned / in the side tab where it applies) |
| ⌘E · right-click | The row's context menu (§7) |
| Esc | Back (to the input, or close the list) |
| ⌘⇧[ ⌘⇧] | Switch the list's tabs (left panel tabs, finder tabs) |

Tree lists (Files, Notes): ← / → fold and unfold, ⏎ on a folder toggles it, ⌃D (as in the Groups panel) or ⌘⌫ moves the row, or the Files selection, to the Trash (asked first). Filtered, a tree is still a tree: the matching files (and folders matched by name) under their folders, each folder on the way open, the matched text bold, the cursor on the best match; a folder folded while filtering stays so until the filter changes. The mouse (principle 9): a click on a file shows it beside (an italic preview tab; a double-click on that side tab, or an edit in it, keeps it), a click on a folder toggles it, a double-click on a file opens it in the main pane with the keyboard in its editor. Sessions: ← / → fold and unfold a group (← on a session goes to its group); a filter looks inside folded groups.

## 7. Menus, dialogs, dropdowns

- **Context menu** (⌘E anywhere = the menu of whatever has the keyboard; right-click = the menu of what is under the
  pointer; empty space in a tree = the folder's menu): ↑↓ ⏎, and an **accelerator letter** per item: the first free
  letter of its label, underlined when the menu was opened from the keyboard; typing it runs the item (also in a menu opened with the mouse, where it is not shown). Submenus (▸) with more than 6 items filter as you type.
  Shortcut hints sit on the right of each row. Any shortcut closes an open menu.
- **Dialog**: modal. Focus cannot leave it (click, Tab, a shortcut); Tab and ⇧Tab cycle inside. ⏎ = OK from any field
  (⏎ on a focused Cancel cancels); Esc = Cancel. The first field has the keyboard, with its text selected. Destructive
  OK is red.
- **Dropdown in a dialog**: button + our own list. ↑↓ / Space / ⏎ open it, ↑↓ move, ⏎ or click picks, Esc closes the
  list only.
- **Finder** (⌘O, ⌘R, ⌘⇧F): a modal list with tabs (Sessions · Files · All Files · Notes · History · Other sessions); the same key closes it. A session is also found by its id: 4 or more characters of it (Orbit's id, or the agent's own conversation id in Other sessions), or a pasted `orbit://session/<id>#n` link, ranked ahead of matches by name.

## 8. Shortcuts

**This table is the contract.** "Reserved" = the key stays bound to nothing else (decisions D3 / D4 in `README.md`).

### Sessions and tabs

| Keys | Action |
|---|---|
| ⌘N | New session: name, folder (the current session's; ↓ lists recent folders), harness, model, mode, group; ⌘⏎ or ⏎ starts it |
| ⌘O | Open: finder with Sessions · Files · All Files · Notes · History · Other sessions tabs (fuzzy; All Files: file names in every open session's folder and in the home folder, this machine's and each reachable machine's (`GET /api/findfiles`: every term in the path below its root, names first, then shorter paths, then the newest; the home through Spotlight on macOS, else a walk with a 1.5 s budget that skips hidden folders, ~/Library and dependency folders), a hit opens in the main pane, or beside from ⌘⌃O; Sessions includes closed named sessions, ⏎ resumes one with its name; Other sessions is everything else that can be resumed, on every machine: closed unnamed sessions, and Claude Code / Codex conversations that are no Orbit session, ⏎ resumes one on its machine). Starts on Files in a file tab, on Notes in a note |
| ⌘⌃O | Open into the side tab |
| ⌘R | Recent: every kind of tab seen lately (sessions with a preview of their last answer, files, notes, threads), open or closed since; a closed file reopens as a preview. In the Groups panel on a group: rename the group |
| ⌘W | Close what has the keyboard: the shown side tab (its neighbour shows), else the file tab, else the session tab: always asked (⏎ closes, Esc keeps it; it says so when the session is working), from ⌘W, its × or the menu alike; ⌘⇧T reopens. |
| ⌘⇧T | Reopen the last closed tab (again: the one before) |
| ⌘1…⌘9 | The groups in left-panel order (its key again: its home) |
| ⌘⇧[ ⌘⇧] | Previous / next tab (in the left panel or a finder: its tabs) |
| ⌘⌃H ⌘⌃L | Move the tab left / right: session and file tabs are alike, in one order per group (the server's: every screen); in the Groups panel: move the group up / down. The mouse does the same: drag a tab along the row; in the Groups panel drag a session (onto another group to move it there) or a group's head (with its sessions): it follows the pointer and the others slide aside, as in a browser's tab strip; Esc puts it back |
| ⌘- ⌘= | Back / forward through visited tabs |
| ⌘E | Context menu of what has the keyboard (tab menu: Rename · Fork · Move to ▸ · Copy link · Copy resume command (`cd <folder> && claude --resume <id>`, `codex resume <thread>`, `agy --conversation <id>`: the same conversation in the agent's own terminal UI) · Shell here · Side tab ▸ · Session ▸ Stop / Resume / Restart / Clone · Close); a file menu has Open · Copy path · Open in external editor · Open in Finder · Close |
| ⌘K | Quick ask: a one-off request to a Fleet agent or any session |
| ⌘I | Notifications: finished requests of other tabs |

### Input and conversation

| Keys | Action |
|---|---|
| ⌘⏎ | Send |
| ⏎ | New line |
| ⌘V / drop | Paste or drop any file: it goes into the input as `![name](path)` (the image shows in place) or `[name](path)` (`features/files.md` §3) |
| @ · / · @@ | Pick a file or folder of the session's folder · a skill or command · another session (`editor.md` §5) |
| ↑ (empty input) | This session's earlier requests, searchable; ⏎ puts one back |
| ⌘E (with a selection) | Quote into this session · Quote to ▸ another · Copy path:lines · Copy (the floating ❝ Quote button does the first) |
| `/mode NAME` | Switch the permission mode (bypass · accept edits · plan · ask); the agent restarts on the same conversation |
| `/model NAME [EFFORT]` · `/effort LEVEL` · `/status` | Model and effort for this session (restart on the same conversation) · what the session runs on |
| ⌘⇧⏎ | Steer: while the agent works, the message joins the running request (⌘⏎ queues it as the next one) |
| Esc | Input ⇄ conversation (§4) |
| ↑↓ PgUp PgDn ⌃V ⌥V · Home End ⌥⇧, ⌥⇧. | Scroll the focused conversation (End sticks to the end again); in the input, ⌥⇧, ⌥⇧. are the input's own start / end |
| ⌘L | Scroll to the latest answer (and stick to the end again) |

### Panels

| Keys | Action |
|---|---|
| ⌘\` | Left panel (three-state toggle) |
| ⌘J | Shell in the session's folder (three-state toggle); Esc Esc back to the input |
| ⌃X 1 | Close every panel beside the main one (and every side tab of this tab) |
| ⌘⇧O | Keyboard: main panel ⇄ side tab |
| ⌥⇧[ ⌥⇧] | Previous / next side tab of this tab (from the main page or inside the side pane). In the left panel's Groups tab (with other machines): its sub-tabs, the machine filter All · This Mac · each machine |
| ⌃⌥H ⌃⌥L · ⌃⌥K ⌃⌥J | Move the focused panel's edge |
| ⌘T | The agent's own terminal (a harness that has one, Harness Protocol 1.1) in the ⌘J pane: show + focus → focus → hide; Esc Esc to the input; closing only detaches |
| ⌘⇧I | ± Review: the session's uncommitted changes in the side tab (three-state toggle); comment on lines, revert a hunk |
| ⌘⌃C · ⌘⇧C | Calendar (D4): a main tab (a view tab, mixed with the others; ⌘⌃C again closes it when in front, ⌘W in it too), today + tomorrow; ↑↓ ⌃N ⌃P PgUp PgDn ⌥⇧, ⌥⇧. move (the note follows among opened meetings); → / ← open / close a meeting (several can be open; Space toggles); ⏎ opens it and writes its note; Esc: from the note to the list, from the list to the session beside; P prep; J join; C copy link; N the note as a file; B the day's blocks; A every block; W week (⌥← ⌥→ page, T this week); R refresh; ⌘E menu. ⌘⇧C: the meetings popover above the status bar (today and tomorrow; ⏎ or a click opens one in the Calendar tab, ⌘⏎ joins, Esc or ⌘⇧C closes) |

### Find, files, notes

| Keys | Action |
|---|---|
| ⌘F | Find in what has the keyboard; ⏎ / ⇧⏎ or ⌘G / ⌘⇧G next / previous; Esc closes |
| ⌘⇧F | Search every session's history and the notes (finder → History) |
| ⌘⇧E | Browse the open file's folders (breadcrumb): ↑↓ ⏎, `..` goes up |
| ⌘⇧D | The journal: this week's note, Monday to Sunday, at today (selected answer text goes under today, linked to its card) |
| ⌘S · ⌃X ⌃S | Save (also auto-save 1.5 s after the last edit and on window blur) |

### Editors (code and Markdown)

| Keys | Action |
|---|---|
| ⌃Space · ⌃G | Set mark · cancel (⌃A ⌃E ⌃F ⌃B ⌃N ⌃P ⌥F ⌥B extend the region while the mark is set) |
| ⌃S · ⌃R | Incremental search (Emacs isearch): open the find bar, then next / previous match; on an empty query, the last search; Esc closes |
| ⌃W · ⌥W · ⌃Y | Cut region · copy region · yank |
| ⌃V · ⌥V | Page down / up |
| ⌃L | Recenter (middle, top, bottom) |
| ⌥⇧, · ⌥⇧. | Top / end of the document (or the view) |
| Tab · ⇧Tab | Indent / outdent in Markdown (Tab never leaves the editor) |
| ⌃/ · ⌘Z ⌘⇧Z | Undo · undo / redo (the shell routes ⌘Z to the editor's own history) |
| ⌃X H | Select all |
| ⌥-click · ⌘-click | Edit a link's text · open it (a plain click opens it) |

### App

| Keys | Action |
|---|---|
| ⌘, | Settings (text weight; interface, conversation, note and code fonts; conversation, note and code sizes) |
| ⌘/ | Shortcut sheet (searchable by key or action) |
| ⌘⇧R | Reload the UI (drafts kept) |
| ⌘⇧= ⌘⇧- ⌘0 | Zoom in / out / reset (default 1.2) |

## 9. Mentions (@@)

- **In the input**, `@@` lists the other open sessions (fuzzy by title, id and group): ↑↓ (⌃N ⌃P) move, ⏎ / Tab insert
  `@@Title-with-dashes`, Esc closes the list only.
- **On send** the request goes to this session with `(also sent to @@x)` appended, and every mentioned session gets it
  with: the source session (title, id), the user's words, the source's last 3 turns (request ≤300, answer ≤400 chars,
  plus `orbit-agent read <id> --last 10`), and how to reply: `orbit-agent mention <source> "…"`, only when the source needs the result.
- **`orbit-agent mention SESSION TEXT`** is an agent's note to another agent, not a task. It tells the receiver not to mention
  back unless asked (no ping-pong).
- **`@@name` in an answer** links to that session.

## 10. Requests on a card

| Button | Action |
|---|---|
| ✎ Edit | The text goes back into the input (a line above it says which request; Esc in the empty input or "cancel" stops). On send a queued request changes in place; a finished one is replaced by the new request |
| ↺ Resend | Send the same text again as a new request |
| Withdraw | Take a request still waiting in the queue out of it |

