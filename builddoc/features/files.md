# Files: the editors and dropped files

The Files module: the tree, the editors, save and conflicts, the notes vault (§0), and how files dragged in from
Finder land.

## 0. Files, editor, notes (the contract)

- **Editor**: CodeMirror 6, Obsidian-style live preview for Markdown (marks hidden off the cursor line, KaTeX math,
  images, tables as a grid: one line per cell, scroll sideways in their own box, frozen header). Emacs keys: ⌃Space
  mark, ⌃W / ⌥W / ⌃Y, ⌃V / ⌥V page (by physical key: WebKit types √ for ⌥V), ⌃L recenter (middle, top, bottom),
  ⌃X ⌃S save, ⌃X H select all.
- **Links**: bare `https://` URLs and `[text](url)` open on click (⌥-click edits, ⌘-click works too); `[ ]` / `[x]`
  (with or without `- `) are clickable checkboxes; `==highlight==`; `#tags` as chips.
- **Save**: auto-save 1.5 s after the last edit and on window blur; refused when the disk changed since opening →
  banner: Reload from disk · Overwrite with mine · Save mine as a copy (`<name> (mine).md`).
- **Tree operations** (right-click / ⌘E; empty space = the folder): New file / note, New folder, Rename, Delete
  (to the Trash: the vault's `.trash`, else `~/.Trash`). All paths stay inside the root (reject `/`, `..`).
- **Breadcrumb**: the path above a file is clickable (folder → list it; file name → its siblings); ⌘⇧E from keys.
- **Notes vault** = `<data root>/notes` (its own pseudo-session `notes`; plain directory walk, it is git-ignored):
  - Obsidian links: `[[name]]` (same folder first, then shortest path, then by file name for moved targets),
    `[[folder/name]]`, `[[name#h|alias]]`, `[[a\|b]]` inside tables, `![[img.png]]` embeds, relative `[x](../a.md)`.
    A missing `[[note]]` is offered for creation at the vault root.
  - `[[` autocomplete; "Linked mentions" (backlinks) under each note; renaming updates every link to it.
  - **⌘⇧D** = the journal: this week's note, Monday to Sunday (a Sunday belongs to the week before). When the vault
    has Obsidian's Daily notes set up (`.obsidian/daily-notes.json`: folder, moment format, template), it lives where
    Obsidian keeps daily notes, dated by the week's Monday (e.g. `Journal/YYYYMM/YYYY-MM-DD.md` = `Journal/202611/
    2026-11-16.md` for the week of the 16th): a new one from the template (`{{title}}`, `{{date}}`, `{{time}}`,
    `{{date:FORMAT}}`), then `## Mon 11-16` … `## Sun 11-22`; entries go under today's heading. Else
    per the vault's `Organization Guide.md`: `Journal/<YYYY>/Week of <YYYYMMDD>.md`, dated
    by Monday; an existing week note within the last 6 days is reused; new ones from the guide's weekly template
    (Focus · Daily Work with `### Mon MM/DD`… · Projects in Flight · Ideas · Carry-overs · Reflections).
  - Text selected in an answer + ⌘⇧D, and agents' `orbit-agent note "<text>"`, are appended under today's section,
    linked back `[title #id](orbit://session/<id>#<n>)`; never a rewrite.
  - Note tabs sit next to the session in front and are restored after a reload.
- **Look**: notes are warm paper (`#faf9f5`), inline code as warm chips (`#f3eee4` / `#5b4630`), bold headings;
  ⌘, Settings: text weight, fonts for the interface, the conversation, notes and code (every installed family via AppKit) with conversation, note and code sizes; the
  full contract is `../ui.md` §7.

## 1. Markdown editor: live preview

CodeMirror 6 with an Obsidian-style live preview (`web/cm/livepreview.js`); the contract is `../editor.md` §1 (the
same editor is the input). **The source is the document**: every rendering is a view of it, and the raw form shows
only where the cursor is: on a block mark itself (heading `#`, bullet), inside or touching an inline construct, on
the line of a quote or rule, in the lines of a table or formula. A click on an image zooms it (lightbox); on a
formula or table it puts the cursor in its source. Images are inline, at most 260 × 180 unless a width is given.

| Source | Off the cursor line |
|---|---|
| `# ` heading marks, `**` `*` `_` `~~` `` ` `` | hidden; the text keeps its style |
| `- ` / `* ` bullet | `•` (a task item shows only its checkbox) |
| `:rocket:` shortcodes | the emoji |
| `[text](url)` | `text` as a link (a plain click opens it, ⌥-click edits, ⌘-click works too); bare `https://` URLs open on click |
| `[[name]]`, `[[name\|alias]]`, `[[name#h]]` | `name` / `alias` as a link (click opens, a missing note is created at the vault root) |
| `![alt\|300](src)`, `![[img.png\|300]]` | the image (width optional); on the cursor line the source with the image after it |
| `![[note]]`, `![[file.pdf]]` | a link chip "↪ name" |
| `$x$` `\(x\)`, `$$…$$` `\[…\]` (one line or several) | KaTeX, inline / centred block |
| a table | a grid: one line per cell (no wrap), scrolls sideways in its own box, header frozen, inline Markdown and `[[a\|b]]` in cells |
| `[ ]` / `[x]` at a line start, with or without `- ` / `1.` | a checkbox; a click toggles the source; a done line is struck through, `--ink-3` |
| `==text==` | highlighted (`#fff3a3`) |
| `#tag` (not all digits, not in code or links) | a chip (`--chip-bg` / `--chip-fg`, pill) |
| `> ` quote mark, `---` | hidden behind a 3px `--line` bar · a hairline |
| fenced code | always source, on `--code-bg`; nothing inside it renders (no math, tags or links) |

Image sources: `http(s):` and `data:` as they are; a path relative to the document's folder (or absolute) via
`/api/raw`; `![[name]]` via `/api/embed?name=&from=`, resolved like Obsidian (the linking note's folder first, else
the shortest path, else by file name when the target moved; outside the vault relative to the document).

Documents over 400 KB keep the plain styled source. A rendering that throws leaves the source and logs to
`~/.orbit/page.log` (the editor never goes blank).

## 2. Notes: links follow renames, deletes go to `.trash`

- Renaming or moving a vault file (a note or an attachment) rewrites every wiki link that meant it: a bare name stays
  bare, a path stays a path, `#heading` / `|alias` / `\|` in tables are kept. A link that resolved to another file of
  the same name is left alone.
- Delete in the vault moves the file to `<vault>/.trash` (Obsidian's place); elsewhere to the system Trash.

## 3. Dropped and pasted files

**Any file**, any size (in the app; a browser upload is limited to 100 MB). Where it lands decides:

| Target | Result |
|---|---|
| A Markdown editor (drop or ⌘V of a file / image) | Copied beside the document and linked at the drop point: in the vault into `attachments/` as `![[name]]` (an image) or `[[name]]`; elsewhere into `assets/` beside the file as `![name](assets/…)` / `[name](assets/…)`. Images and several files go on lines of their own below the line dropped on; the same bytes dropped again reuse the file; a clipboard image is named `Pasted image <YYYYMMDDHHMMSS>.png` |
| A code editor | The paths as text at the drop point |
| The session view (conversation or input) | Into the input as `![name](path)` / `[name](path)` (`<path>` when it holds spaces); the agent reads the files from there |
| Anywhere else (title bar, left panel, file header) | The files open in tabs |
| A side tab | Its own page decides, by the same rules |

In the input a file goes in as Markdown at the cursor: `![name](path)` for an image (it renders in place, inline, like
in a note) or `[name](path)` for any other file; the agent reads the path. A sent request shows those images and file
links in place in its bubble; an answer's image with a local path is served by the server.

**Why the shell is involved**: WKWebView never hands the page a file drop (Tauri takes it), and a browser `File`
has no path. The app shell forwards `WindowEvent::DragDrop` as `orbitDrop({type: enter|over|drop|leave, paths, x, y})`
in CSS px before the page zoom; the page converts with `x / zoom × rectScale()` and picks the target with
`elementFromPoint`. While dragging, the target is outlined (the composer, or the editor dashed). In a plain browser
the editors and the session view take `File` objects (uploaded to `~/.orbit/uploads/`), and a drop elsewhere never
navigates the page away.

## 4. API

| Route | Body / query | Returns |
|---|---|---|
| `GET /api/embed` | `name`, `from` | the resolved file's bytes (404 if none) |
| `POST /api/attach` | `doc`, and `src` (a path) or `name` + `data` (base64) | `{path, link, image}` |
| `POST /api/files/rename` | `path`, `new` | `{path, links}`: `links` = notes rewritten (vault only) |
| `POST /api/files/trash` | `path` | vault → `.trash`, else the system Trash |

## 5. Acceptance

- Each row of §1 renders off the cursor line and shows its source on it; a checkbox click changes only `[ ]` ↔ `[x]`.
- `$5 and $10` is not math; `#123` is not a tag; nothing in fenced code renders.
- Drop a PNG and a PDF from Finder on a note (in the app, WebKit): both copied into `attachments/`, linked on their own
  lines below the drop line, the image shows. Drop them on the input: two quoted-if-needed paths, a thumbnail and a chip.
- Rename a note linked as `[[Name]]`, `[[folder/Name|x]]` and `[[Name\|y]]` in a table: all three follow.
