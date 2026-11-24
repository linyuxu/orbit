"""orbit: an AI IDE whose documents are coding-agent sessions.

    cli/        the command lines: `orbit` (people) and `orbit-agent` (agents)
    server/     the orbit server: HTTP + SSE for the app, a browser, agents and other servers
    desk/       sessions, their request queues and cards, groups, the Fleet, agent mail
    drivers/    one adapter per harness (Claude Code, Codex, agy, OpenCode, the Orbit Harness Protocol); conversations started elsewhere
    plugins.py  plugins: folders that add agents, services, commands and secrets without changing Orbit
    machines/   other machines: the token, the remotes registry, their sessions shown as this server's
    workspace/  the user's files and notes: tree, read / save, ± Review, the vault, skills
    services/   beside the sessions: schedules, the calendar, plan usage, the ⌘J shells
    harness_server/  the agent processes, in a daemon that outlives a restart of the server (stdlib only)
    home.py     ~/.orbit
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent  # the repository: orbit/, web/, plugin/, cli/
__version__ = "0.2.0"  # Orbit's version (CHANGELOG.md); pyproject, the app and the page read it from here
API_VERSION = (2, 1)  # the plugin API (major, minor): a plugin's requires_api is checked against it
