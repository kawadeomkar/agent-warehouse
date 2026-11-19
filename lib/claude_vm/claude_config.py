"""What of this machine's Claude Code just code carries to a VM beside the sessions (docs/architecture/custom-code.md
§7.6): the auto memory of each repository the VM has a checkout of. ``claude_config_plan`` is the one entry point,
each part a function of its own.

Pure, as the session handoff's decisions are (``sessions.py``): ``claude_config_info`` reads this machine's
configuration directory, ``git_checkouts_info`` the checkouts at each end, and ``claude_config_carry`` writes on
the VM what this decides. A refusal is a ``ValueError``.
"""

from __future__ import annotations

import re

from claude_vm.sessions import claude_project_folder, git_repository, github_repository

# The note just code writes beside a carried memory folder's notes, and the line that indexes it in MEMORY.md.
NOTE = "carried-by-just-code.md"
NOTE_LINE = (
    f"- [Carried by just code]({NOTE}) - where the paths of the machine these notes were copied from are on this VM"
)
# The settings carried by default: the model and effort the person works at, preferences - a name, a level, a switch
# that names no path and runs nothing - and what commits made there say of Claude. Each is an item of its own.
SETTINGS = (
    "model",
    "modelSettings",
    "effortLevel",
    "alwaysThinkingEnabled",
    "outputStyle",
    "language",
    "theme",
    "switchModelsOnFlag",
    "skipWorkflowUsageWarning",
    "agentPushNotifEnabled",
    "syncClaudeAiSkills",
    "autoMemoryEnabled",
    "attribution",
    "includeCoAuthoredBy",
)
# Settings carried part by part, each part an item of its own: the person's permission policy and auto mode's rules.
# The rest of each stays here: permissions.additionalDirectories names this machine's folders, and
# autoMode.environment describes this machine's environment to auto mode's classifier.
SETTINGS_PARTS = {"permissions": ("allow", "ask", "deny", "defaultMode"), "autoMode": ("allow", "soft_deny")}
# Left unless claude_settings_sync_keys names them: each runs a program, which here names this machine's own.
SETTINGS_PROGRAMS = ("hooks", "statusLine", "fileSuggestion")
# Never, whatever claude_settings_sync_keys says: a credential or what makes one (env above all: an ANTHROPIC_API_KEY
# there outranks the claude.ai login and ends Remote Control), this machine's paths, what the claude_code role or the
# copy owns, plugins (installed by their own step), MCP trust, and a safety question.
SETTINGS_NEVER = frozenset(
    {
        "env",
        "apiKeyHelper",
        "awsAuthRefresh",
        "awsCredentialExport",
        "gcpAuthRefresh",
        "otelHeadersHelper",
        "pluginConfigs",
        "sandbox",
        "sshHostCommands",
        "forceLoginMethod",
        "forceLoginOrgUUID",
        "autoMemoryDirectory",
        "plansDirectory",
        "autoUpdatesChannel",
        "cleanupPeriodDays",
        "remoteControlAtStartup",
        "enabledPlugins",
        "extraKnownMarketplaces",
        "enableAllProjectMcpServers",
        "enabledMcpjsonServers",
        "disabledMcpjsonServers",
        "allowedMcpServers",
        "deniedMcpServers",
        "skipDangerousModePermissionPrompt",
    }
)
# A settings key as Claude Code names them.
_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
# A plugin's ID, as an argument to `claude plugin install`: its name and its marketplace's, neither starting with "-".
_PLUGIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}@[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
# A marketplace's GitHub repository, as `claude plugin marketplace add` takes one: owner/name.
_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}")
# The user-level extensions, each a folder or file at the top of ~/.claude: skills (but the account's synced ones),
# subagents, commands, output styles, workflows, themes and keybindings.
EXTENSIONS = ("skills/", "agents/", "commands/", "output-styles/", "workflows/", "themes/", "keybindings.json")
_EXTENSION = re.compile(
    r"(?:(?:skills|agents|commands|output-styles|workflows|themes)"
    r"(?:/[^./\x00-\x1f\x7f][^/\x00-\x1f\x7f]{0,254}){1,5}|keybindings\.json)"
)
# The global instructions, and the folder of rules beside them.
INSTRUCTIONS = "CLAUDE.md"
RULES = "rules/"
# A path the note may name: absolute, and nothing that would end the Markdown code span it is set in or a line.
_NOTE_PATH = re.compile(r"/[^`\x00-\x1f\x7f\ud800-\udfff]{0,1023}")
# A memory file's path under its folder, as claude_config_info lists them: components with no dot first and no
# control character, at most 5 deep. Held again here: the writer is handed it as a path under the VM's ~/.claude.
_FILE = re.compile(r"[^./\x00-\x1f\x7f][^/\x00-\x1f\x7f]{0,254}(?:/[^./\x00-\x1f\x7f][^/\x00-\x1f\x7f]{0,254}){0,4}")


def _vm_checkouts(there):
    """{owner/repo: the VM's checkout of it}: the one, or of several the one named after the repository, else none."""
    by_repo = {}
    for checkout in there or []:
        repo = github_repository(checkout.get("origin"))
        if repo:
            by_repo.setdefault(repo, []).append(checkout)
    chosen = {}
    for repo, found in by_repo.items():
        named = [item for item in found if str(item.get("name", "")).lower() == repo.split("/")[1]]
        pick = found if len(found) == 1 else named
        if len(pick) == 1:
            chosen[repo] = pick[0]
    return chosen


def claude_config_paths(repo_paths, there, more=()):
    """The directories here to ask git about for the memory plan, each once, in order: every path Claude Code's
    ``githubRepoPaths`` lists for a repository the VM has a checkout of, then ``more`` (the copied sessions' first
    directories)."""
    repos = set(_vm_checkouts(there))
    paths = []
    for repo, listed in sorted((repo_paths or {}).items()):
        if str(repo).lower() in repos:
            paths.extend(str(path) for path in listed or [] if path)
    paths.extend(str(path) for path in more or [] if path)
    return list(dict.fromkeys(paths))


def memory_note(here_checkout, there_checkout, here_memory, there_memory):
    """The note a carried memory folder gets on the VM: where the other machine's paths are now, or "" when a path
    is not one the note can name as it is."""
    paths = (here_checkout, there_checkout, here_memory, there_memory)
    if not all(isinstance(path, str) and _NOTE_PATH.fullmatch(path) for path in paths):
        return ""
    return (
        "---\n"
        "name: carried-by-just-code\n"
        'description: "Where the paths of the machine just code copied these notes and sessions from are on this VM"\n'
        "metadata:\n"
        "  type: reference\n"
        "---\n"
        "\n"
        "agent-warehouse's just code copied this repository's auto memory, and its Claude Code sessions, from another\n"
        "machine. Older notes and resumed sessions name that machine's paths:\n"
        "\n"
        f"- `{here_checkout}` is `{there_checkout}` here.\n"
        f"- `{here_memory}` is this folder, `{there_memory}`.\n"
        "- A scratchpad or worktree of that machine's, under its temporary folder, is not here. What was committed in\n"
        "  it reaches this checkout through git alone.\n"
        "- That machine's own SSH hosts, keys and tools may not be reachable from here.\n"
        "\n"
        "**How to apply:** read a path from before the copy through this list before acting on it. just code writes\n"
        "this file again on each copy while it is as just code wrote it.\n"
    )


def claude_memory_plan(memory, here, there, here_dir, dest, note=True, unread=()):
    """Which of this machine's auto memory folders go to which of the VM's checkouts, and the files that carries.

    ``memory`` is claude_config_info's: each folder under this machine's ``projects/`` whose ``memory/`` holds a
    file, to its files. ``here`` is git_checkouts_info's reading of the checkouts here Claude Code has seen,
    ``there`` its listing of the VM's projects, ``here_dir`` and ``dest`` the Claude Code configuration directory
    here and on the VM. A repository goes to the VM's checkout of it whose GitHub origin names it, of two the one
    named after it; its memory folder here is the folder of the checkout here whose origin names it, Claude Code's
    name for that checkout's top level, and of two such folders that both hold notes, neither.

    Returns ``folders`` (repository, ``here`` folder, ``there`` folder, the checkout at each end), ``files`` (each
    file to carry: its path in the mirror, which is its path under ``here_dir``, and its path under ``dest``),
    ``scopes`` (the folders on the VM the writer manages, where a file gone here is removed there while it is as
    written), ``generated`` (each folder's note when ``note``) and ``appended`` (MEMORY.md's line for it),
    ``ambiguous`` (a repository whose notes are in more than one folder here), ``shared`` (a repository whose folder
    here is the one Claude Code names another checkout's by too, a path that differs only where it is no letter or
    digit: whose notes they are is unclear, and neither goes) and ``held``, the paths on the VM nothing is removed at
    or below: what of a folder carried claude_config_info could not read here (``unread``, paths under ``here_dir``).
    """
    vm_checkouts = _vm_checkouts(there)
    candidates, tops = {}, {}
    for item in here or []:
        if not item.get("exists") or not item.get("git") or not item.get("toplevel"):
            continue
        try:
            folder = claude_project_folder(item["toplevel"])
        except ValueError:
            continue
        tops.setdefault(folder, set()).add(item["toplevel"])
        repo = git_repository(item.get("origin"))
        if repo in vm_checkouts and folder in (memory or {}):
            candidates.setdefault(repo, {})[folder] = item["toplevel"]
    shared = []
    for repo, found in candidates.items():
        for folder in [folder for folder in found if len(tops.get(folder, ())) > 1]:
            shared.append({"repository": repo, "folder": folder})
            found.pop(folder)
    candidates = {repo: found for repo, found in candidates.items() if found}
    # Of two folders here of one repository, the one that holds notes: an emptied one is no rival.
    for repo, found in candidates.items():
        holding = {folder: source for folder, source in found.items() if memory[folder]}
        if len(found) > 1 and len(holding) == 1:
            candidates[repo] = holding
    plan = {
        "folders": [],
        "files": [],
        "scopes": [],
        "generated": [],
        "appended": {},
        "ambiguous": [],
        "shared": sorted(shared, key=lambda item: item["repository"]),
        "held": [],
    }
    for repo in sorted(candidates):
        found = candidates[repo]
        if len(found) > 1:
            plan["ambiguous"].append({"repository": repo, "folders": sorted(found)})
            continue
        (folder, source), checkout = next(iter(found.items())), vm_checkouts[repo]
        try:
            target = claude_project_folder(checkout["path"])
        except ValueError:
            continue
        scope = f"projects/{target}/memory/"
        plan["folders"].append(
            {"repository": repo, "here": folder, "there": target, "source": source, "checkout": checkout["path"]}
        )
        plan["scopes"].append(scope)
        mine = f"projects/{folder}/memory/"
        plan["held"] += [f"{scope}{path[len(mine) :]}" for path in unread or [] if str(path).startswith(mine)]
        for entry in memory[folder]:
            if not _FILE.fullmatch(str(entry.get("path", ""))):
                continue
            plan["files"].append(
                {"source": f"projects/{folder}/memory/{entry['path']}", "target": f"{scope}{entry['path']}"}
            )
        # An emptied folder has no notes to explain: its scope alone, so the copies there are removed.
        text = (
            memory_note(source, checkout["path"], f"{here_dir}/projects/{folder}/memory/", f"{dest}/{scope}")
            if note and memory[folder]
            else ""
        )
        if text:
            plan["generated"].append({"target": f"{scope}{NOTE}", "content": text})
            if any(entry.get("path") == "MEMORY.md" for entry in memory[folder]):
                plan["appended"][f"{scope}MEMORY.md"] = NOTE_LINE
    return plan


def claude_settings_refused(extra):
    """The names of ``extra`` (claude_settings_sync_keys) just code never carries, sorted: a tier-0 setting, one
    carried part by part, or a name that is no settings key."""
    return sorted(
        {
            str(key)
            for key in extra or []
            if str(key) in SETTINGS_NEVER or str(key) in SETTINGS_PARTS or not _KEY.fullmatch(str(key))
        }
    )


def claude_settings_asked(extra=()):
    """What claude_config_info reads values of: the settings carried by default, ``extra`` that just code may carry,
    and the parts of SETTINGS_PARTS. Never a value of anything else."""
    keys = list(dict.fromkeys([*SETTINGS, *(str(key) for key in extra or [] if not claude_settings_refused([key]))]))
    return {"keys": keys, "parts": {key: list(parts) for key, parts in SETTINGS_PARTS.items()}}


def claude_settings_plan(found, managed=(), extra=()):
    """The settings items to carry, from claude_config_info's ``settings``: each to its value, and every item this run
    manages, so that one gone here is removed there while still as carried.

    ``managed`` names the top-level settings the claude_code role writes (claude_code_settings' keys,
    autoUpdatesChannel, env): never carried, so the person's own claude_code_settings wins, and handed to the writer,
    which removes an item an earlier run carried that is no longer one of ``items`` - a setting ``extra`` no longer
    names - while it is still as carried, but never one of ``managed``. Where this machine's file could not be read
    there are no items, so nothing there changes, nor is removed for seeming gone here. ``left`` names what of
    this machine's settings stays here and why: ``role``, ``program`` (SETTINGS_PROGRAMS, unless named in ``extra``),
    ``never`` (SETTINGS_NEVER and the parts of SETTINGS_PARTS not carried) and ``other`` (a setting just code does
    not know).
    """
    found = found or {}
    asked = claude_settings_asked(extra)
    managed = {str(key) for key in managed or []}
    items = [key for key in asked["keys"] if key not in managed]
    items += [f"{key}.{part}" for key, parts in asked["parts"].items() if key not in managed for part in parts]
    if found.get("unreadable"):
        items = []
    values = found.get("values") or {}
    present = [str(key) for key in found.get("present") or []]
    left = {"role": [], "program": [], "never": [], "other": []}
    for key in present:
        if key in SETTINGS_NEVER:
            left["never"].append(key)
        elif key in managed:
            left["role"].append(key)
        elif key in asked["keys"] or key in asked["parts"]:
            continue
        elif key in SETTINGS_PROGRAMS:
            left["program"].append(key)
        else:
            left["other"].append(key)
    left["never"] += [str(part) for part in found.get("parts_left") or []]
    return {
        "values": {item: values[item] for item in items if item in values},
        "items": items,
        "managed": sorted(managed),
        "left": {kind: sorted(names) for kind, names in left.items()},
        "unreadable": bool(found.get("unreadable")),
    }


def claude_plugins_plan(here, there):
    """Which of this machine's plugins to install on the VM, and which marketplaces to add for them.

    ``here`` is claude_config_info's ``plugins``: the user-scope plugins enabled here, and each marketplace's GitHub
    repository ("" for another source). ``there`` is its reading of the VM's Claude Code: ``readable`` while
    `claude plugin list` answered, the user-scope plugins installed and the marketplaces known there. A plugin there
    already is left; one whose marketplace is missing there and is no GitHub repository is left and named, since its
    source is this machine's; nothing is ever uninstalled.
    """
    here, there = here or {}, there or {}
    plan = {"install": [], "marketplaces": [], "left": [], "unread": False}
    if not here.get("enabled"):
        return plan
    if not there.get("readable"):
        plan["unread"] = True
        return plan
    installed, known = set(there.get("installed") or []), set(there.get("marketplaces") or [])
    sources = here.get("marketplaces") or {}
    for plugin in here.get("enabled") or []:
        plugin = str(plugin)
        if plugin in installed:
            continue
        if not _PLUGIN.fullmatch(plugin):
            plan["left"].append(plugin)
            continue
        market = plugin.split("@", 1)[1]
        if market not in known:
            repository = str(sources.get(market) or "")
            if not _REPOSITORY.fullmatch(repository):
                plan["left"].append(plugin)
                continue
            if repository not in plan["marketplaces"]:
                plan["marketplaces"].append(repository)
        plan["install"].append(plugin)
    return plan


def claude_history_plan(lines, projects):
    """The prompt-history lines to append on the VM: each of ``lines`` (claude_config_info's, this machine's lines of
    the sessions copied) whose session is in ``projects`` (session ID to the VM checkout it continues in), its
    ``project`` that checkout, as the copy's relocated entry names it, in time order."""
    found = []
    for line in lines or []:
        session = line.get("sessionId") if isinstance(line, dict) else None
        if session in (projects or {}):
            found.append(dict(line, project=projects[session]))
    return sorted(found, key=lambda line: (_when(line.get("timestamp")), str(line.get("sessionId"))))


def _when(value):
    """A history line's time, as a number to order by: Claude Code writes milliseconds since the epoch."""
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def claude_config_plan(here, here_checkouts, there, here_dir, dest, options=None):
    """Everything just code carries beside the sessions, as one plan: the parts ``options`` turns on, from ``here``,
    claude_config_info's reading of this machine's Claude Code directory.

    ``options``: ``memory`` (claude_memory_sync) and ``note``, the memory folders' note; ``instructions``
    (claude_instructions_sync), with ``instructions_owned`` where claude_code_global_instructions makes CLAUDE.md the
    role's; ``settings`` (claude_settings_sync), with ``managed`` and ``extra`` as claude_settings_plan takes them;
    ``plugins`` (claude_plugins_sync), with ``plugins_there``, the VM's reading, as claude_plugins_plan takes it;
    ``history`` (claude_history_sync), with ``history_projects``, the copied sessions' checkouts on the VM;
    ``extensions`` (claude_extensions_sync).
    Returns the writer's inputs - ``files``, ``generated``, ``appended``, ``scopes``, ``held`` and ``settings`` - and
    each part's own record, for what the run says: ``memory``, its folders, ``ambiguous`` and ``shared``;
    ``instructions``, the files and the imports CLAUDE.md names; ``settings``' ``left``; ``unread``, what was not read
    here.

    What claude_config_info could not read here (``unread``: a link, a file or folder it could not open) is no file
    gone here: each is ``held``, nothing at or below it removed on the VM. Where this machine's directory is not there
    at all (``missing``), no part has a scope and no setting is an item, so nothing there is removed.
    """
    options = options or {}
    here = here or {}
    missing = bool(here.get("missing"))
    unread = [str(path) for path in here.get("unread") or []]
    plan = {
        "files": [],
        "generated": [],
        "appended": {},
        "scopes": [],
        "held": [],
        "missing": missing,
        "unread": unread,
        "memory": {"folders": [], "ambiguous": [], "shared": []},
        "instructions": {"files": [], "imports": [], "owned": False, "over": False},
        "settings": {"values": {}, "items": [], "left": {}, "unreadable": False},
        "plugins": {"install": [], "marketplaces": [], "left": [], "unread": False},
        "history": [],
        "extensions": {"files": [], "over": False},
    }
    if options.get("memory"):
        memory = claude_memory_plan(
            (here or {}).get("memory") or {}, here_checkouts, there, here_dir, dest, options.get("note", True), unread
        )
        for key in ("files", "generated", "scopes", "held"):
            plan[key] += memory[key]
        plan["appended"].update(memory["appended"])
        plan["memory"] = {"folders": memory["folders"], "ambiguous": memory["ambiguous"], "shared": memory["shared"]}
    if options.get("instructions"):
        owned = bool(options.get("instructions_owned"))
        files = [
            str(item.get("path", ""))
            for item in here.get("instructions") or []
            if not (owned and item.get("path") == INSTRUCTIONS)
        ]
        files = [path for path in files if path == INSTRUCTIONS or (path.startswith(RULES) and _FILE.fullmatch(path))]
        plan["files"] += [{"source": path, "target": path} for path in files]
        # rules/ past its bound is listed with none: no scope then, or what an earlier run carried would read as gone.
        over = bool(here.get("rules_over"))
        plan["scopes"] += ([] if owned else [INSTRUCTIONS]) + ([] if over else [RULES])
        plan["held"] += [path for path in unread if path in (INSTRUCTIONS, RULES.rstrip("/")) or path.startswith(RULES)]
        plan["instructions"] = {
            "files": files,
            "imports": [str(name) for name in here.get("imports") or []][:20],
            "owned": owned,
            "over": over,
        }
    if options.get("settings"):
        plan["settings"] = claude_settings_plan(here.get("settings"), options.get("managed"), options.get("extra"))
    if options.get("plugins"):
        plan["plugins"] = claude_plugins_plan(here.get("plugins"), options.get("plugins_there"))
    if options.get("history"):
        plan["history"] = claude_history_plan(here.get("history"), options.get("history_projects"))
    if options.get("extensions"):
        paths = [str(item.get("path", "")) for item in here.get("extensions") or []]
        # The account's synced skills are downloaded at the VM's own sign-in: never this machine's to carry.
        files = [path for path in paths if _EXTENSION.fullmatch(path) and not path.startswith("skills/synced/")]
        plan["files"] += [{"source": path, "target": path} for path in files]
        # Past the bound the reader lists none: no scope then, or what an earlier run carried would read as gone here.
        if not here.get("extensions_over"):
            plan["scopes"] += list(EXTENSIONS)
        tops = tuple(scope.rstrip("/") for scope in EXTENSIONS)
        plan["held"] += [path for path in unread if path.split("/")[0] in tops]
        plan["extensions"] = {"files": files, "over": bool(here.get("extensions_over"))}
    if missing:
        plan["scopes"] = []
        plan["settings"] = dict(plan["settings"], items=[], values={}, unreadable=True)
    return plan


def claude_config_rsync(plan):
    """The paths, relative to this machine's Claude Code directory, rsync sends into the VM's mirror for ``plan``."""
    return sorted({item["source"] for item in plan.get("files", [])})
