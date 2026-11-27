# Markdown editor (the input, notes, Markdown files)

**One live-preview editor (CodeMirror 6, Typora / Obsidian style) is used for three surfaces; options switch the
extras on.** What you type is the Markdown source; it renders in place as soon as a construct is complete and shows
its raw form only where the cursor is. The code editor (other file types) shares the key set (§4) but not the preview.

| Surface | Options | Extras |
|---|---|---|
| Input (composer) | `onSubmit`, `onFiles`, `placeholder` | ⌘⏎ sends; completions (§5); drafts |
| Markdown file tab / side tab | `notes`, `clickLinks`, `onLink`, `imgUrl` | tasks, `==highlight==`, `#tags`; ⌘S, auto-save (§6) |
| Notes (vault) | the above + `wiki` | `[[note]]`, `[[note#heading|alias]]`, `![[img.png]]` embeds |
| Calendar meeting note | the above | auto-saved; Esc back to the list |

## 1. What renders

| Construct | Rendered as | Raw shown when |
|---|---|---|
| `# … ######` headings | `cm-h cm-h1…6` lines (bold, h1 1.6em); the `## ` mark hidden | the cursor is on the mark itself (not the whole line: the text must not shift under the mouse) |
| `- ` / `* ` bullet | a `•` widget | the cursor is on the mark itself |
| `1. ` ordered item | as typed | always |
| `> ` quote | quote bar, mark hidden | the cursor is on the line |
| `**b**` `*i*` `~~s~~` `` `c` `` | strong / em / strike / chip; marks hidden | the cursor is inside or touching that construct |
| `[text](url)`, bare `https://` | link (click opens: web in Chrome, files per ⌘, → Open files in) | the cursor is inside it |
| `![alt](desk-img:x.png)` / image URL | the image (max 260 × 180, rounded, lightbox on click) | the cursor is inside it |
| `$…$` `\(…\)` / `$$…$$` `\[…\]` | KaTeX inline / display (centred), as soon as it is closed | the cursor is strictly inside it (not just after the closing `$`); never inside code |
| GFM table | a rendered table (block widget, whole lines) | the cursor is in the table |
| Fenced / indented code | `cm-codeblock`, no inner rendering | — |
| `---` | a rule | the cursor is on it |
| `:shortcode:` | the Unicode emoji or the workspace's custom emoji picture | the cursor is inside it |
| `- [ ]` / `- [x]` (files, notes) | a checkbox (click toggles `[ ]` ⇄ `[x]` in the source); a done line is struck | the cursor is on the box |
| `==text==`, `#tag` (files, notes) | highlight, tag chip (not in headings or code) | the cursor is inside it |
| `[[note]]` (notes) | a dotted link with the alias or name; click opens the note | the cursor moved in by keys |

Rule: rendering must never move text that is under the mouse. Block marks (bullets, heading marks) stay rendered while
their line is edited; only the cursor on the mark itself reveals it.

## 2. Font and size

Notes and Markdown files use the note font and note size (⌘, : any installed family, 8–40 px, default Inconsolata 15 px) on the
`--note-bg` paper; the input uses the UI font at the conversation size. See `ui.md` §7.

## 3. Robustness

- A rendering error never blanks the editor: decorations are built in a guard; on an exception the text stays, that
  update has no preview, and the error is logged with the doc length, selection and stack.
- A save never writes an empty buffer over a non-empty file: the server refuses it; ⌘S asks ("Save empty"),
  auto-save never does it.

## 4. Keys (all editors)

| Key | Action |
|---|---|
| ⌃Space · ⌃G | Set the mark (movement then extends the selection) · drop it |
| ⌃A ⌃E ⌃F ⌃B ⌃N ⌃P ⌥F ⌥B, arrows | Move; extend while the mark is set |
| ⌃W · ⌥W · ⌃Y | Cut · copy · yank (paste) |
| ⌃V · ⌥V | Page down · up (extend with the mark) |
| ⌃L | Recenter the cursor line (middle → top → bottom) |
| ⌥⇧, · ⌥⇧. | Start · end of the document |
| ⌃S · ⌃R | Incremental search: open the find bar, then next · previous match; Esc closes |
| ⌘Z ⌘⇧Z · ⌃/ ⌃_ | Undo / redo · undo |
| ⌘F | Find bar |

The active line's background is translucent: an opaque one hides the selection layer.

## 5. The input only

| Feature | Behavior |
|---|---|
| Send | ⌘⏎ sends, ⏎ is a new line; a failed send puts the text back |
| Paste / drop | images and files are saved (session images folder) and inserted as `![](desk-img:…)` / a file link; the agent gets the paths |
| `/` | the session folder's skills by name, then by description; `/mode …` |
| `@` | files and folders of the session folder (git-tracked + untracked), fuzzy; `@../`, `@~/`, `@/` browse folders outside it (the session's machine's), the part after the last `/` filtering; a folder picked goes on into it. `../` is offered at `@`, `@.`, `@..` |
| `@@` | the other sessions (title, key, group); see `interaction.md` §11 |
| ↑ in the empty input | the input history (searchable) |
| Drafts | per session, kept across tab switches, ⌘⇧R and restarts |
| Quote | ❝ Quote from a card, a file or a side tab inserts `> path:lines` + the text into the right input |

## 6. File tabs only

⌘S writes atomically; a file changed on disk since it was opened gets a conflict banner (overwrite / reload), never a
silent overwrite. Auto-save after an idle pause. A selection shows ❝ Quote and ⌘E opens its menu (quote to a session,
copy path:lines). A Markdown file opens in the preview editor with the keyboard in it.
