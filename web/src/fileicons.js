"use strict";
/* ===================================================================== file and folder icons (ui.md §6) */
/* A file: a 12 × 14 page outline with a folded corner, stroked in its type's colour, a tiny mono glyph inside (the
   same icon on file tabs). A folder: a 15 × 12 outline, grey closed, blue open (colours from tokens: dark mode swaps). */
const FILE_TYPES = [
  // [extensions or exact names, colour, glyph ("" = none, null = the extension)]
  [["py", "pyi", "pyw"], "#3572A5", "py"],
  [["js", "mjs", "cjs", "jsx"], "#d4a017", "js"],
  [["ts", "tsx", "mts", "cts"], "#3178c6", "ts"],
  [["md", "markdown", "mdx"], "#5b7083", "md"],
  [["json", "jsonl", "jsonc"], "#c08b30", "{}"],
  [["html", "htm", "xml", "svg", "xhtml"], "#e34c26", "<>"],
  [["css", "scss", "sass", "less"], "#7b4fd6", "#"],
  [["sh", "bash", "zsh", "fish"], "#3f9142", "$"],
  [["yaml", "yml", "toml", "ini", "cfg", "conf", "env"], "#a0522d", "≡"],
  [["png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "avif", "heic", "tiff"], "#c2185b", "▣"],
  [["sql", "sqlite", "db"], "#00758f", "db"],
  [["rs"], "#b7410e", "rs"],
  [["ipynb"], "#f37626", "nb"],
  [["csv", "tsv", "parquet", "pkl", "pickle", "feather", "arrow"], "#2e7d32", "▤"],
  [["lock"], "#8e8e93", "⊘"],
  [["dockerfile", "makefile", "containerfile", "justfile"], "#5b7083", "⚙"],  // exact names (any case)
  [["txt", "log", "text", "out"], "#8e8e93", "≣"],
];
const FILE_TYPE = new Map(FILE_TYPES.flatMap(([keys, color, glyph]) => keys.map(k => [k, { color, glyph }])));
function fileType(path) {
  const name = basename(path || "").toLowerCase();
  if (FILE_TYPE.has(name)) return FILE_TYPE.get(name);  // Dockerfile, Makefile…
  if (name.endsWith(".lock") || name === "package-lock.json" || name === "uv.lock" || name === "cargo.lock") return FILE_TYPE.get("lock");
  const ext = name.includes(".") ? name.slice(name.lastIndexOf(".") + 1) : "";
  return FILE_TYPE.get(ext) || { color: "#9a9aa0", glyph: "" };
}
function fileIcon(path) {
  const { color, glyph } = fileType(path);
  const text = glyph ? `<text x="6" y="11.2" text-anchor="middle" font-size="${glyph.length > 1 ? 4.6 : 5.5}" font-weight="600" font-family="SF Mono, ui-monospace, Menlo, Orbit Mono, monospace" fill="${color}" stroke="none">${esc(glyph)}</text>` : "";
  return `<svg class="ficon" width="12" height="14" viewBox="0 0 12 14" fill="none" stroke="${color}" stroke-width="1" stroke-linejoin="round" aria-hidden="true">` +
    `<path d="M1.5 .5h6l3 3v10h-9z"/><path d="M7.5 .5v3h3"/>${text}</svg>`;
}
function folderIcon(open) {
  return `<svg class="dicon${open ? " open" : ""}" width="15" height="12" viewBox="0 0 15 12" stroke-width="1" stroke-linejoin="round" aria-hidden="true">` +
    `<path d="M1 2a1 1 0 0 1 1-1h3.6l1.4 1.5H13a1 1 0 0 1 1 1V10a1 1 0 0 1-1 1H2a1 1 0 0 1-1-1z"/></svg>`;
}
