"""gh's sign-in on a machine: layer 2's step `github-auth`, today's roles/github_projects/tasks/auth.yml and
auth_status.yml in plain code (docs/architecture/engine.md §16.10).

Its settings: the rows `github_auth`, `github_auth_method` and `github_login_scopes`; the store's `github_token`, a
`Secret` or none; and what the run sets for itself (`RUN_SET`): the machine's name, the host its lines name, and whether
the run's device flow follows this configure. A method other than auto, web or token is refused before the machine is
read, a GitHub App's hourly token named: nothing here renews one once the run has ended.

**The probe** reads, as the machine's account and changing nothing: whether gh is there; `gh auth status --hostname
github.com --json hosts`, its exit, what it said and its active account (the login, its state, its token's source, its
scopes); where that account is signed in, the token gh holds, piped on the machine into its own `python3`, which prints
the token's digest and the kind its prefix tells (`pat`, `oauth`, `other`), never the token, so that it never leaves
the machine; the marker `~/.config/agent-warehouse/gh-login` (guest/states.py's GITHUB_MARKER), the digest of the token
of a login this product made; and, signed in, git's credential helpers for github.com.

**The decisions** are `decide(found, wanted)`, one function over that state and the settings, as auth.yml and
vars/main.yml decide them, by digests alone: never by a token's prefix, which tells a token the person pasted into gh
from none (the kind is the report's alone).

- It fails where gh could not report its state (a gh older than 2.81.0, which has no `--json`) and the run manages gh,
  and where the method is token and no token is set.
- **Sign in** with the token where the run manages gh (`github_auth` and gh there), the method takes the token (token,
  or auto with one), the token gh holds is not it, and the login is not the person's own: gh signed out, signed in by
  this product (the marker's digest is the stored token's), or the method token, which asks for the token whatever gh
  holds. A login made by hand stays, and the token only lists there, said with the switch that hands gh to the token.
- **Sign out**, with the method taking no token, only the login the marker says is this product's, by account
  (`--user`), so that a second account stays; then, once gh is signed out, the marker goes. A marker whose digest is
  not the stored token's (the person's own login replaced this product's) is left where it is, and so is that login.
- **The marker** is written, mode 0600 in a folder of 0700, wherever the login is the token's, not only straight after
  a sign-in, so that one gone missing comes back; only where its content or mode differs.
- **git's helper**: `gh auth setup-git --hostname github.com` where the run manages gh, gh is signed in and git has no
  helper of gh's for github.com, by gh's own test for its entry (`!<path>/gh ...`), so that a second run changes
  nothing.

**The acts** are plain calls of the SSH runner, each a fixed command: `gh auth login --hostname github.com
--git-protocol https --with-token --insecure-storage` with the token on its standard input and nowhere else (a refusal
fails the step quoting gh's own words, but one for a missing scope under auto, which keeps listing and leaves gh signed
out with the scopes named); the logout; the marker written on its writing command's standard input or removed; the
helper. gh's state is read again after a sign-in or a sign-out. Nothing is ever revoked.

**The report** is `lib/claude_vm/status/lines.py`'s line (`github_login_report`, what `aw status` and login say of gh
too), and its NEXT STEPS record under `github` while something is left; but for the three cases that file has no
sentence for, each auth.yml's own: a token that only lists for want of a scope (left to do), a token beside a login of
the person's own (nothing left), and a sign-in that follows in this run (nothing left).

The token is on the run's redaction list before anything is read, and no message, event, argument or difference
carries it: a message asks whether one is set. Standard library; nothing of the helper runs here.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from collections.abc import Mapping

from claude_vm import text
from claude_vm.guest import states
from claude_vm.guest.layer2 import claude_settings
from claude_vm.guest.layer2.step import Context, Difference, Failed
from claude_vm.schema import printer
from claude_vm.secret import Secret
from claude_vm.ssh import runner
from claude_vm.status.lines import github_login_report, github_too_old_said

# The settings this step reads, by name: the rows, the store's secret, and what the run sets for itself, each with its
# value where it sets none (the machine's name is required).
ROWS = ("github_auth", "github_auth_method", "github_login_scopes")
SECRET = "github_token"
RUN_SET = {"vm_name": None, "host_flag": "", "github_projects_sign_in_follows": False}
METHODS = ("auto", "web", "token")
# The marker of a login this product made, under the account's home, and its mode.
MARKER = states.GITHUB_MARKER.removeprefix("~/")
MARKER_MODE = "0600"
HOST = "github.com"
# The acts, each a fixed command.
LOGIN = ("gh", "auth", "login", "--hostname", HOST, "--git-protocol", "https", "--with-token", "--insecure-storage")
LOGOUT = ("gh", "auth", "logout", "--hostname", HOST)
SETUP_GIT = ("gh", "auth", "setup-git", "--hostname", HOST)
STATUS = ("gh", "auth", "status", "--hostname", HOST, "--json", "hosts")
HELPERS = ("git", "config", "--global", "--get-all", f"credential.https://{HOST}.helper")
QUICK = runner.Limits(seconds=30.0, silent=None)
# A call that reaches GitHub: gh checks a token there before it stores it.
GITHUB = runner.Limits(seconds=90.0, silent=None)
# A GitHub account's name, the only one a logout names and a line says (schema/patterns.py's github_user, one or more).
ACCOUNT = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")
# gh as git's helper, as gh writes its entry: its path, then its words.
GH_HELPER = re.compile(r"!(?:[^ ]*/)?gh(?: |\Z)")
# A token's source gh names where it came from the environment (GH_TOKEN, GITHUB_TOKEN).
FROM_ENV = re.compile(r".*_TOKEN\Z")
# A placeholder as the printer writes one: a word gh said that looks like one is said without its brackets, since a
# NEXT STEPS line holds none (engine/report.py).
PLACEHOLDER = re.compile(r"(?<!\S)<([a-z][a-z0-9_-]*)>")

# gh found, or not, as the account's shell finds it.
FIND = "if command -v gh >/dev/null 2>&1; then echo found; else echo absent; fi"
# The token gh holds, piped into the machine's own python3, which prints its kind and its digest, never the token: the
# token never leaves the machine. Stripped first, as auth_status.yml's trim strips it.
STORED = """gh auth token --hostname github.com 2>/dev/null | python3 -I -c '
import hashlib, sys
token = sys.stdin.read().strip()
kind = "pat" if token.startswith(("ghp_", "github_pat_")) else "oauth" if token.startswith("gho_") else "other"
print("kind", kind if token else "none")
print("digest", hashlib.sha256(token.encode()).hexdigest() if token else "none")
'"""
FORGET = 'rm -f -- "$HOME/$1"'
DIGEST = re.compile(r"[0-9a-f]{64}")


def _sh(script: str, *words: str) -> list[str]:
    return ["sh", "-c", script, "sh", *words]


def _said(said: str) -> str:
    """What gh wrote, as a line of this run says it: plain, one line, a placeholder's brackets dropped."""
    return PLACEHOLDER.sub(r"\1", text.one_line(said or "", 300))


def _failure(result: runner.Result) -> str:
    if isinstance(result, runner.Done):
        said = _said(result.said)
        return f"exit {result.status}" + (f": {said}" if said else "")
    if isinstance(result, runner.Unreachable):
        return "the machine was not reached (ssh's exit 255)"
    if isinstance(result, runner.TimedOut):
        return "it took longer than its time"
    if isinstance(result, runner.Silent):
        return "it said nothing in its time"
    return _said(getattr(result, "why", ""))


def _digest(token: Secret) -> str:
    """The digest the marker holds for token, Ansible's hash('sha256') of it: never the token."""
    return hashlib.sha256(token.reveal().encode()).hexdigest()


# ------------------------------------------------------------------ the settings ----


@dataclasses.dataclass(frozen=True)
class Wanted:
    """The settings as the step reads them: whether the run manages gh, the method, the token (None where none is set),
    its digest, the scopes the device flow asks for, whether a sign-in follows in this run, and the lines' commands."""

    auth: bool
    method: str
    token: Secret | None = dataclasses.field(repr=False)
    digest: str = dataclasses.field(repr=False)
    scopes: tuple[str, ...]
    follows: bool
    name: str
    user: str
    login_command: str
    configure_command: str

    @property
    def token_wanted(self) -> bool:
        """Whether the method takes the token: token, or auto with one."""
        return self.method == "token" or (self.method == "auto" and self.token is not None)


def token(settings: Mapping[str, object]) -> Secret | None:
    """The store's GitHub token in the run's settings, or None where none is set."""
    held = settings.get(SECRET)
    if held is None:
        return None
    if not isinstance(held, Secret):
        raise Failed(f"{SECRET} reaches a step as the store's Secret, never as plain text")
    return held if held.reveal() else None


def _setting(ctx: Context, name: str) -> object:
    if name in RUN_SET and RUN_SET[name] is not None:
        return ctx.settings.get(name, RUN_SET[name])
    if name not in ctx.settings:
        raise Failed(f"the run's settings hold no {name}, which gh's sign-in reads")
    return ctx.settings[name]


def wanted(ctx: Context) -> Wanted:
    """The settings, held: a method of none of METHODS refused by name, before anything is read."""
    method = _setting(ctx, "github_auth_method")
    if method not in METHODS:
        raise Failed(
            f"github_auth_method is {text.one_line(str(method), 40)!r}, not one of {', '.join(METHODS)}. A GitHub"
            " App's hourly token is not taken without a control VM, which is what renews one: set github_token to a"
            " fine-grained token limited to the repositories it needs, with an expiry, and github_auth_method to auto"
            " or token."
        )
    auth = _setting(ctx, "github_auth")
    if not isinstance(auth, bool):
        raise Failed("github_auth is true or false")
    scopes = _setting(ctx, "github_login_scopes")
    scopes = () if scopes is None else scopes
    if not isinstance(scopes, (list, tuple)) or not all(isinstance(scope, str) for scope in scopes):
        raise Failed("github_login_scopes is a list of scopes")
    name = _setting(ctx, "vm_name")
    if not isinstance(name, str) or not name or name.splitlines() != [name]:
        raise Failed("vm_name is the machine's name, one line")
    host = _setting(ctx, "host_flag")
    host = "" if host is None else str(host)
    held = token(ctx.settings)
    if held is not None:
        # Before anything is read or said: a machine's words may echo it.
        ctx.job.redact(held)
    login = printer.line("login", {"names": [name], "only": "github"}, refused=False, host_flag=host)
    configure = printer.line("deploy", {"names": [name], "tags": "github_projects"}, refused=False, host_flag=host)
    return Wanted(
        auth,
        str(method),
        held,
        "" if held is None else _digest(held),
        tuple(scopes),
        bool(_setting(ctx, "github_projects_sign_in_follows")),
        name,
        ctx.target.account,
        login,
        configure,
    )


# ------------------------------------------------------------------ the state ----


@dataclasses.dataclass(frozen=True)
class Found:
    """gh's state on the machine as read: whether gh is there; its status's exit and what it said; the active
    account's login, state, token source and scopes; the stored token's digest ("" where none was read) and the kind
    its prefix tells; the marker's digest ("" for none) and whether its file is the one the run writes; and git's
    helpers for github.com (read only where gh is signed in)."""

    gh: bool
    status: int = 0
    status_said: str = ""
    account: str = ""
    state: str = ""
    source: str = ""
    scopes: str = ""
    stored: str = dataclasses.field(default="", repr=False)
    kind: str = "none"
    marker: str = dataclasses.field(default="", repr=False)
    marker_file: str | None = dataclasses.field(default=None, repr=False)
    marker_mode_held: bool = False
    helpers: tuple[str, ...] = ()

    @property
    def signed_in(self) -> bool:
        return self.state == "success"

    @property
    def ours(self) -> bool:
        """Whether the login gh holds is the one this product made: the stored token's digest is the marker's."""
        return self.signed_in and bool(self.stored) and bool(self.marker) and self.marker == self.stored

    @property
    def gh_helper(self) -> bool:
        return any(GH_HELPER.match(line) for line in self.helpers)


def _active(lines: tuple[str, ...]) -> Mapping[str, object]:
    """The active github.com account of gh's status JSON, or {}: anything else reads as signed out."""
    said = "\n".join(lines).strip()
    if not said.startswith("{"):
        return {}
    try:
        hosts = json.loads(said).get("hosts") or {}
    except (AttributeError, ValueError):
        return {}
    accounts = hosts.get(HOST) if isinstance(hosts, Mapping) else None
    for one in accounts if isinstance(accounts, list) else ():
        if isinstance(one, Mapping) and one.get("active"):
            return one
    return {}


def _word(value: object) -> str:
    return text.one_line(value, 200) if isinstance(value, str) else ""


async def read(ctx: Context) -> Found:
    """gh's state on the machine, read as its account: see the module's docstring. Nothing of the token is read here
    but its digest, made on the machine."""
    found = await ctx.run(_sh(FIND), limits=QUICK)
    if not (isinstance(found, runner.Done) and found.status == 0 and list(found.lines) in (["found"], ["absent"])):
        raise Failed(f"gh could not be looked for on the machine: {_failure(found)}")
    if found.lines == ("absent",):
        return Found(False)
    status = await ctx.run(list(STATUS), limits=QUICK)
    if not isinstance(status, runner.Done):
        raise Failed(f"gh auth status could not be read on the machine: {_failure(status)}")
    account = _active(status.lines) if status.status == 0 else {}
    read_state = {
        "status": status.status,
        "status_said": text.plain_text(status.said).strip(),
        "account": _word(account.get("login")),
        "state": _word(account.get("state")),
        "source": _word(account.get("tokenSource")),
        "scopes": _word(account.get("scopes")),
    }
    stored, kind, helpers = "", "none", ()
    if read_state["state"] == "success":
        held = await ctx.run(_sh(STORED), limits=QUICK)
        said = dict(line.split(" ", 1) for line in held.lines if " " in line) if isinstance(held, runner.Done) else {}
        if not (isinstance(held, runner.Done) and held.status == 0 and set(said) == {"kind", "digest"}):
            raise Failed(f"the token gh holds could not be told on the machine: {_failure(held)}")
        if said["kind"] not in ("pat", "oauth", "other", "none") or not (
            said["digest"] == "none" or DIGEST.fullmatch(said["digest"])
        ):
            raise Failed("the token gh holds could not be told on the machine: its digest is not one")
        stored, kind = ("" if said["digest"] == "none" else said["digest"]), said["kind"]
        listed = await ctx.run(list(HELPERS), limits=QUICK)
        # 1 is "no such key", which includes no ~/.gitconfig at all.
        if not (isinstance(listed, runner.Done) and listed.status in (0, 1)):
            raise Failed(f"git's credential helpers for {HOST} could not be read on the machine: {_failure(listed)}")
        helpers = tuple(listed.lines) if listed.status == 0 else ()
    marker = await claude_settings.read_file(ctx, MARKER, MARKER_MODE)
    return Found(
        True,
        stored=stored,
        kind=kind,
        marker=marker.content.strip() if marker.there else "",
        marker_file=marker.content if marker.there else None,
        marker_mode_held=marker.mode_held,
        helpers=helpers,
        **read_state,  # type: ignore[arg-type]
    )


# ------------------------------------------------------------------ the decisions ----


@dataclasses.dataclass(frozen=True)
class Acts:
    """What the run does to gh, decided from its state and the settings: sign in with the token; sign out (the account
    a logout names, "" for none, or None for no logout); remove the marker; write it; make gh git's helper; and the
    case the report says where status/lines.py has no sentence for it ("beside", "follows" or "")."""

    sign_in: bool = False
    logout: str | None = None
    forget: bool = False
    mark: bool = False
    setup_git: bool = False
    case: str = ""

    @property
    def any(self) -> bool:
        return self.sign_in or self.logout is not None or self.forget or self.mark or self.setup_git


def decide(found: Found, want: Wanted) -> Acts:
    """What auth.yml does in this state, by digests alone: see the module's docstring. Failed where it fails."""
    if want.auth and found.gh and found.status != 0:
        raise Failed(
            f"gh on {want.name} could not report its login state: {_said(found.status_said) or 'no output'}."
            f" {github_too_old_said(True)}"
        )
    if want.auth and want.method == "token" and want.token is None:
        raise Failed(
            f"github_auth_method is token, but no github_token is set for {want.name}: set one, or choose auto or web"
            " to sign in by the device flow."
        )
    managed = want.auth and found.gh
    holds_token = found.signed_in and bool(want.digest) and found.stored == want.digest
    sign_in = (
        managed
        and want.token_wanted
        and not holds_token
        and (not found.signed_in or found.ours or want.method == "token")
    )
    beside = managed and want.token_wanted and found.signed_in and not holds_token and not found.ours
    beside = beside and want.method != "token"
    logout = None
    if managed and not want.token_wanted and found.ours:
        if found.account and not ACCOUNT.fullmatch(found.account):
            raise Failed(
                f"gh on {want.name} is signed in by this product's token under a name that is no GitHub account's, so"
                " it is not signed out: sign it out there with gh auth logout"
            )
        logout = found.account
    forget = managed and not want.token_wanted and not found.signed_in and bool(found.marker)
    wanted_marker = f"{want.digest}\n"
    mark = managed and want.token_wanted and holds_token
    mark = mark and not (found.marker_file == wanted_marker and found.marker_mode_held)
    setup_git = managed and found.signed_in and not found.gh_helper and not sign_in and logout is None
    case = "beside" if beside else ""
    if managed and not found.signed_in and not sign_in and want.follows:
        case = "follows"
    return Acts(sign_in, logout, forget, mark, setup_git, case)


# ------------------------------------------------------------------ the report ----


def _account(found: Found) -> str:
    return found.account if ACCOUNT.fullmatch(found.account) else "?"


def _kind(found: Found) -> str:
    """The kind of login as the report says it: from the environment by its source, else the stored token's."""
    if FROM_ENV.fullmatch(found.source):
        return "env"
    return found.kind if found.kind in ("pat", "oauth") else "other"


def reported(found: Found, want: Wanted, case: str, *, login_done: bool = False, short: str = "") -> tuple[str, str]:
    """The report's text and its NEXT STEPS line ("" once nothing is left): see the module's docstring. short, what gh
    said refusing the token for want of a scope, where it did."""
    name = want.name
    if short:
        said = (
            f"{name}: gh did not sign in with github_token ({short}), so the token only lists the repositories, and"
            " git push on the machine will fail. Add read:org to the token (and workflow only for pushes that change"
            f" .github/workflows/) and run {want.configure_command}; or set github_auth_method: web and sign gh in by"
            f" the device flow with {want.login_command}"
        )
        return said, said
    if case == "beside":
        said = (
            f"{name}: gh is signed in to GitHub as {_account(found)} by a login of your own, which this run leaves as"
            " it is, so github_token only lists the repositories there. To sign gh in with the token instead, set"
            f" github_auth_method: token and run {want.configure_command}"
        )
        return said, ""
    if case == "follows":
        said = (
            f"{name}: gh is not signed in to GitHub, so only public repositories are listed and cloned, and git push"
            " on the machine will fail. The deploy signs it in next, in this run, and the private repositories follow."
        )
        return said, ""
    kind = _kind(found) if found.signed_in else "other"
    facts = {
        "gh_found": found.gh,
        "status_rc": found.status,
        "status_said": _said(found.status_said),
        "signed_in": found.signed_in,
        "login_done": login_done,
        "account": _account(found) if found.account else "",
        "kind": kind,
        "is_ours": found.ours,
        "lacks_workflow": "workflow" in want.scopes and kind == "oauth" and "workflow" not in found.scopes,
        "user": want.user,
        "host": name,
        "via": "",
        "auth": want.auth,
        "auth_method": want.method,
        "token_wanted": want.token_wanted,
        "token_set": want.token is not None,
        "role_follows": False,
        "configure_command": want.configure_command,
        "login_command": want.login_command,
    }
    made = github_login_report(facts)
    said = f"{name}: {made['line']}"
    return said, said if made["left"] else ""


# ------------------------------------------------------------------ the step ----


@dataclasses.dataclass(frozen=True)
class State:
    """What the probe found, the settings as held, and what the run would do."""

    found: Found
    want: Wanted
    acts: Acts


class GitHubAuth:
    """The step: see the module's docstring."""

    name = "github-auth"
    part = "github_projects"

    async def probe(self, ctx: Context) -> State:
        """gh's state and what the run would do: nothing changed."""
        want = wanted(ctx)
        found = await read(ctx)
        return State(found, want, decide(found, want))

    async def apply(self, ctx: Context, state: State) -> bool:
        found, want, acts = state.found, state.want, state.acts
        changed, login_done, short = False, False, ""
        if acts.sign_in and want.token is not None:
            result = await ctx.run(list(LOGIN), limits=GITHUB, stdin=[want.token, "\n"])
            if not (isinstance(result, runner.Done) and result.status == 0):
                # Struck of the token, though gh never says it: what gh says is quoted.
                said = want.token.struck(_said(result.said) if isinstance(result, runner.Done) else _failure(result))
                if not (want.method == "auto" and "missing required scope" in said):
                    raise Failed(
                        f"gh auth login on {want.name} with github_token failed: {said or 'no output'}. GitHub refused"
                        " the token or the machine cannot reach it. Check the token and its permissions"
                        ' (docs/guides/github.md, "Private repositories, and pushing from the VM"), or clear it to sign'
                        f" in by the device flow with {want.login_command}"
                    )
                short = said
            else:
                changed = login_done = True
                found = await read(ctx)
                acts = decide(found, want)
        elif acts.logout is not None:
            words = [*LOGOUT, *(["--user", acts.logout] if acts.logout else [])]
            result = await ctx.run(words, limits=QUICK)
            if not (isinstance(result, runner.Done) and result.status == 0):
                raise Failed(f"gh auth logout on {want.name} failed: {_failure(result)}")
            changed = True
            found = await read(ctx)
            acts = decide(found, want)
        if not short:
            changed = await self._rest(ctx, want, acts) or changed
            found = _after(found, want, acts)
        self._say(ctx, *reported(found, want, acts.case, login_done=login_done, short=short))
        return changed

    @staticmethod
    async def _rest(ctx: Context, want: Wanted, acts: Acts) -> bool:
        """The marker forgotten or written, and gh made git's helper, as acts says."""
        if acts.forget:
            result = await ctx.run(_sh(FORGET, MARKER), limits=QUICK)
            if not (isinstance(result, runner.Done) and result.status == 0):
                raise Failed(f"~/{MARKER} could not be removed on the machine: {_failure(result)}")
        if acts.mark:
            await claude_settings.write_file(ctx, MARKER, MARKER_MODE, f"{want.digest}\n")
        if acts.setup_git:
            result = await ctx.run(list(SETUP_GIT), limits=QUICK)
            if not (isinstance(result, runner.Done) and result.status == 0):
                raise Failed(f"gh auth setup-git on {want.name} failed: {_failure(result)}")
        return acts.forget or acts.mark or acts.setup_git

    @staticmethod
    def _say(ctx: Context, said: str, left: str) -> None:
        # Here alone, as remote_control.py does: guest/ is imported where the engine is not.
        from claude_vm.engine import report

        report.say(ctx.job, ctx.machine, "github", said, left)

    def difference(self, state: State) -> Difference | None:
        """What the run would do to gh, in its order, and gh's login as it is now: never a token."""
        found, acts = state.found, state.acts
        if not acts.any:
            return None
        said = []
        if acts.sign_in:
            said.append("gh signed in with github_token, and this product's marker of the login written")
            if not found.gh_helper:
                said.append("gh made git's credential helper where it is not")
        if acts.logout is not None:
            said.append(f"gh signed out of {_account(found)}'s login, which this product made, and its marker removed")
        if acts.forget:
            said.append("this product's marker of a login gh no longer holds removed")
        if acts.mark:
            said.append("this product's marker of the login written")
        if acts.setup_git:
            said.append("gh made git's credential helper")
        return Difference("gh's login", _as_now(found), "; ".join(said))


def _after(found: Found, want: Wanted, acts: Acts) -> Found:
    """found as the marker and git's helper are once _rest has done acts: what the report says."""
    if acts.forget:
        found = dataclasses.replace(found, marker="", marker_file=None, marker_mode_held=False)
    if acts.mark:
        found = dataclasses.replace(found, marker=want.digest, marker_file=f"{want.digest}\n", marker_mode_held=True)
    if acts.setup_git:
        found = dataclasses.replace(found, helpers=("", "!gh auth git-credential"))
    return found


def _as_now(found: Found) -> str:
    if not found.gh:
        return "gh not installed"
    if not found.signed_in:
        return "signed out"
    whose = "this product's token" if found.ours else "a login of your own"
    return f"signed in as {_account(found)} by {whose}"
