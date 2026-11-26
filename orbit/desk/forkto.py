"""Fork to another machine or folder (the session menu's "Fork to…" dialog): a hand-over, not a copy.

A fork on its own machine and folder is the desk's (desk.fork: the harness's own fork, or a hand-over into another
harness that reads the source with `orbit-agent history`). Another machine, or another folder (a harness keeps its
conversations per folder), cannot do either: the agent there does not reach this machine's sessions. So this server
(the page's, which reaches both) writes the source's latest turns to a Markdown file on the target machine
(<state>/handover/ here, /tmp/orbit-handover-*.md on another machine), starts the new session there (in the group
asked for, here), and sends it a first request: read that file, check the working tree, report, wait.
"""

from __future__ import annotations

import io
import os
import time
from datetime import datetime

from orbit.machines import federation

TURNS = 60  # the latest turns the hand-over file keeps
ANSWER_MAX = 12_000  # characters of one answer in it


def _norm(path: str, local: bool) -> str:
    """A folder as the dialog writes it (~ for a home), for comparing: this machine's resolved first."""
    import re
    p = os.path.realpath(os.path.expanduser(path)) if local else path
    return re.sub(r"^/(?:home/[^/]+|Users/[^/]+|root)(?=/|$)", "~", p).rstrip("/") or "/"


def crosses(src_machine: str, src_cwd: str, b: dict) -> bool:
    """Whether a fork with body b leaves the source's machine or folder (then: a hand-over across)."""
    machine = str(b["machine"]) if b.get("machine") is not None else src_machine
    if machine != src_machine:
        return True
    cwd = str(b.get("cwd") or "").strip()
    if not cwd:
        return False
    there = federation.local_paths(src_machine, src_cwd) if src_machine else src_cwd
    return _norm(cwd, not machine) != _norm(there, not machine)


def turns_markdown(name: str, machine: str, cwd: str, harness: str, cards: list[dict]) -> str:
    """The source's latest requests and answers, as Markdown (what the new session reads first)."""
    shown = [c for c in cards if not c.get("hidden") and c.get("kind") not in ("note",)][-TURNS:]
    head = [f"# Hand-over: {name}", "",
            f"- From: Orbit session \"{name}\" on {machine or 'this Mac'}, folder `{cwd}`, harness {harness}",
            f"- Written: {datetime.now().strftime('%Y-%m-%d %H:%M')} · its latest {len(shown)} turns (of {len(cards)})", ""]
    body = []
    for c in shown:
        when = datetime.fromtimestamp(c["time"]).strftime("%m-%d %H:%M") if c.get("time") else ""
        body += [f"## #{c.get('id')} · {when} · {c.get('state', '')}", "", "**Request**", "", (c.get("text") or "").strip(), ""]
        ans = (c.get("answer") or "").strip()
        if ans:
            body += ["**Answer**", "", ans[:ANSWER_MAX] + (" …(cut)" if len(ans) > ANSWER_MAX else ""), ""]
        if c.get("error"):
            body += [f"(error: {c['error']})", ""]
    return "\n".join(head + body) + "\n"


def first_request(name: str, src_machine: str, src_cwd: str, file: str, same_folder: bool) -> str:
    where = f"{src_machine or '那台 Mac'}" if src_machine else "这台 Mac"
    return "\n".join([
        f'你在接续 {where} 上的 Orbit session "{name}"（文件夹 {src_cwd}）。只做交接，不要开始新的工作：',
        f"1. 读 {file}：它的最近请求和回答（Orbit 写的交接记录）。",
        "2. 跑 git status / git diff，核对这里的工作区和记录是否一致" +
        ("" if same_folder else "（这是另一台机器或另一个文件夹：原来的改动不一定在这里，先确认有没有、要不要同步）") + "。",
        "3. 用几句话汇报：在做什么、做到哪一步、这里的工作区有哪些未提交改动，然后等我的指令。",
    ])


def fork_across(desk, sid: str, b: dict) -> dict:
    """The new session (its id as this server lists it), started on the target with the hand-over sent."""
    from orbit.workspace import transfer
    fed = desk.federation
    src_remote = federation.split(sid)
    if src_remote:  # the source on another machine: its record from there
        info = next((s for s in (fed.list() if fed else []) if s.get("id") == sid), None)
        if not info:
            raise KeyError(sid)
        code, r = fed.call(src_remote[0], "GET", f"/api/sessions/{src_remote[1]}/cards", rewrite=False)
        if code != 200:
            raise ValueError(f"{src_remote[0]}: {r.get('error') if isinstance(r, dict) else code}")
        cards, src_machine = r.get("cards") or [], src_remote[0]
        name, harness = info.get("name", ""), info.get("harness", "")
        src_cwd = federation.local_paths(src_machine, info.get("cwd", ""))
    else:
        s = desk.get(sid)
        desk._ensure_loaded(s)
        with desk.lock:
            cards = [dict(c) for c in s.cards]
        src_machine, name, harness, src_cwd = "", s.name, s.harness, s.cwd
    machine = str(b.get("machine") or "")
    cwd = str(b.get("cwd") or "").strip() or src_cwd
    opts = {k: str(b.get(k) or "") for k in ("model", "effort")}
    opts.update(harness=str(b.get("harness") or harness), mode=str(b.get("mode") or "default"),
                name=str(b.get("name") or "").strip() or f"{name} (fork{' · ' + machine if machine else ''})")
    group = str(b.get("group") or "")
    md = turns_markdown(name, src_machine, src_cwd, harness, cards).encode()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if machine:  # made there, in a group here; its record put in /tmp there
        if not fed:
            raise ValueError("no other machines")
        code, r = fed.call(machine, "POST", "/api/sessions", {**opts, "cwd": cwd})
        if code != 200 or not isinstance(r, dict) or not r.get("id"):
            raise ValueError(f"{machine}: {r.get('error') if isinstance(r, dict) else code}")
        fed.remember(machine, r)
        new = fed.place(r["id"], group=group or None) or r
        put = transfer.put(desk.state_dir, f"{federation.PFX}{machine}/tmp", f"orbit-handover-{stamp}-{sid.split('~')[-1][:8]}.md", io.BytesIO(md))
        file = federation.local_paths(machine, put["path"])
        rid = federation.split(new["id"])[1]
        code, c = fed.call(machine, "POST", f"/api/sessions/{rid}/send",
                           {"text": first_request(name, src_machine, src_cwd, file, same_folder=False)})
        if code != 200:
            raise ValueError(f"{machine}: the hand-over did not go: {c.get('error') if isinstance(c, dict) else code}")
    else:
        new = desk.create(cwd=cwd, **opts, group=group or (desk.get(sid).group if not src_remote else ""))
        folder = desk.state_dir / "handover"
        folder.mkdir(parents=True, exist_ok=True)
        file = str(folder / f"{stamp}-{sid.split('~')[-1][:8]}.md")
        with open(file, "wb") as f:
            f.write(md)
        desk.send(new["id"], first_request(name, src_machine, src_cwd, file,
                                           same_folder=not src_machine and os.path.realpath(src_cwd) == new.get("cwd")))
    return {**new, "fork_from": sid, "fork_across": True, "handover": file}
