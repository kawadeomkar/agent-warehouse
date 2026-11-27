"""The account's repositories listed and cloned on a machine: layer 2's step `clones`, today's
roles/github_projects/tasks/clone.yml and the two modules it runs, github_repos.py and git_clone_many.py, in plain code
(docs/architecture/engine.md §16.10). The two modules stay in the role, which today's product runs, until the cutover.

Its settings: the rows `github_username`, `github_projects` (the names to clone, none for all),
`github_projects_exclude`, `github_projects_include_forks`, `github_projects_include_archived`,
`github_projects_clone_protocol`, `git_user_name`, `git_user_email` and `vm_projects_dir`, and over ssh `tls_ca_file`
and `network_proxy`, for this computer's one read; the role's options passed through by their own names (`OPTIONS`,
the role's own defaults where none is set); gh's sign-in's settings, read as github.py reads them, the store's
`github_token` among them; and what the run sets for itself (`RUN_SET`): the machine's own list of repositories, its
record's (None where it has none and follows the setting).

**The listing** runs on the machine, as today's module does, where the token is: `LISTING`, a program for the machine's
python3 of the standard library alone, is started there with the store's token on its standard input where one is
held, else with the token gh stored at a login there, read by `gh auth token` and piped into it on the machine, so that
gh's token never leaves the machine and this computer makes no call to GitHub with a token. It asks GitHub's API as
github_repos.py did: the token's own account first, and the account's every repository, private included, where the
token is that account's, else its public ones; each page's next link followed only to the API's own scheme and host;
a redirect never followed; a repository GitHub sizes at 0 asked for its branches, only where the selection would keep
it by kind; and the account a git identity is derived from, asked without a token only where one is to be derived. It
prints a line for each repository, then the count, the account and whether a token listed. A token goes over plain
http to loopback alone.

**The selection** is here, in the engine: `filter_repos` (github_repos.py's, copied whole) and `select`, the role's
vars/main.yml: forks and archived ones out but where the machine's own list names them, empty ones out, a name with a
leading dot never cloned and named, the exclude list out, and in, the machine's own list where it has one (a name only
compared with the listing, so it reaches no URL and no path; a list with no name left clones nothing), else
`github_projects` where it names any, else every one. A private repository is cloned only where git has a credential
for it - over ssh always, over https with gh signed in and git's helper gh's, made so by this run (github_auth, with gh
there) or by hand - and otherwise skipped and named, never cloned and failed. Never keyed off github_auth alone:
with it off, a token still lists.

**The git identity** is read before anything is written, each key on its own, as the account, and only where the run
manages gh (github_auth): a value of git_user_name or git_user_email is written always; otherwise the account's,
name and GitHub's noreply address, and only where the account's ~/.gitconfig has none, so that an identity set by hand
is never replaced. An organisation gives none, which is said, with the line that signs gh in to the person's own
account, and left to do.

**The checkouts** are the helper's verb `clones` (guest/helper/clones.py), sent by the bundler on standard input and
run as the machine's account: asked first to change nothing, which is the probe's and the check's read of what is
there, then to clone what is missing into the projects folder, each into a partial folder renamed into place once
whole, and, under github_projects_update_existing, to pull what was there by a fast-forward alone. Nothing removes a
checkout. Over ssh, GitHub's published SSH host keys are fetched here, on this computer, by the one transport (`/meta`
of the API, which takes no token), and the verb puts them in the account's known_hosts. The projects folder is made,
0755, as the account where it is under the home, else as root and given to the account.

**The report** is one, each run: how many repositories of how many were selected for the folder and what was skipped,
with why (empty, private for want of a credential, a leading dot), what of the machine's own list the account has none
of, and that private repositories are not listed while no token lists; the organisation's sentence under NEXT STEPS'
`github_identity`. A repository that could not be cloned, or a checkout that could not be pulled, fails the step,
naming each with git's last line; the rest are cloned.

The token is on the run's redaction list before anything is read (github.wanted), and no message, event, argument or
difference carries it. Standard library; asyncio is imported inside the coroutine that reads GitHub's keys.
"""

from __future__ import annotations

import dataclasses
import json
import re
import urllib.parse
from collections.abc import Mapping, Sequence

from claude_vm import sessions, text
from claude_vm.fleet import registry
from claude_vm.guest import bundle, remote
from claude_vm.guest.layer2 import github
from claude_vm.guest.layer2.step import Context, Difference, Failed
from claude_vm.ssh import runner
from claude_vm.vendors import proxy, transport, trust

# The settings this step reads, by name: the rows; the role's options passed through, each with the role's own default
# where none is set (roles/github_projects/defaults/main.yml, held equal by tests/unit/test_aw_clones.py); and what the
# run sets for itself, the machine's own list of repositories (None: it has none, and follows github_projects).
ROWS = (
    "github_username",
    "github_projects",
    "github_projects_exclude",
    "github_projects_include_forks",
    "github_projects_include_archived",
    "github_projects_clone_protocol",
    "git_user_name",
    "git_user_email",
    "vm_projects_dir",
    "tls_ca_file",
    "network_proxy",
)
OPTIONS = {
    "github_projects_api_url": "https://api.github.com",
    "github_projects_home": "",
    "github_projects_update_existing": False,
    "github_projects_depth": "",
    "github_projects_clone_jobs": 8,
}
RUN_SET = {"vm_repos": None}
# The clone protocols, as the setting's row gives them: https, where git's credential is gh's; else ssh, where git has
# a key of the person's own and GitHub's published host keys are trusted.
PROTOCOLS = registry.row("github_projects_clone_protocol").choices
HTTPS = "https"
QUICK = runner.Limits(seconds=30.0, silent=None)
# The listing: every page of the account's repositories, a request a repository GitHub sizes at 0, each request 30 s.
LISTED = runner.Limits(seconds=600.0, silent=None)
# The checkouts: every clone, each git command CLONE_SECONDS at most, and the whole call within its time.
CLONING = runner.Limits(seconds=3600.0, silent=None)
CLONE_SECONDS = 900
# GitHub's published keys, read here: the document's cap, larger than an error page, and the call's time.
META_MOST = 1024 * 1024 - 1
META_SECONDS = 60.0
USER_AGENT = "agent-warehouse"
# A key line as GitHub publishes one, and as the helper's verb takes it: its type, then its base64.
KEY = re.compile(r"[a-z0-9@.-]+ [A-Za-z0-9+/]+={0,3}")
HOME = 'printf "%s\\n" "$HOME"'
MAKE_FOLDER = 'mkdir -p -- "$1" && chmod -- 0755 "$1"'
MAKE_FOLDER_FOR = 'mkdir -p -- "$1" && chown -- "$2": "$1" && chmod -- 0755 "$1"'
# The store's token goes on the listing's standard input; else gh's, piped there on the machine.
WITH_GH = 'gh auth token --hostname github.com 2>/dev/null | "$1" -I -c "$2" "$3"'
# A leading dot would make a folder of settings in the projects folder: .claude for every Claude Code session started
# there, an editor's own, .agent-warehouse for code's own folder.
DOTTED = re.compile(r"[.]")
ORGANIZATION = "Organization"
UNLISTED = " Private repositories are not listed until gh is signed in."

# The listing, a program for the machine's own python3 (3.10 and later, the standard library alone): github_repos.py's
# requests in plain Python, run with the token on its standard input (an empty line: none) and what it is asked as JSON
# in its one argument. Never imported here: it is text a machine runs.
LISTING = r'''
import json
import re
import sys
import urllib.error
import urllib.request
from urllib.parse import quote, urlparse

FIELDS = ("name", "full_name", "clone_url", "ssh_url", "default_branch", "fork", "archived", "private")
KEPT = FIELDS + ("pushed_at", "size")
USER_FIELDS = ("login", "name", "id", "type")
NEXT_LINK = re.compile(r'<([^>]+)>;\s*rel="next"')
LOOPBACK = ("127.0.0.1", "localhost", "::1")
MOST = 32 * 1024 * 1024
PER_PAGE = 100


class Refused(Exception):
    """Why the listing could not be made, in a sentence."""


class Unfollowed(urllib.request.HTTPRedirectHandler):
    """A redirect is an answer, never followed: the token goes with every request."""

    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(Unfollowed)


def parse_next_link(link_header):
    """The URL with rel="next" from a Link header, or None."""
    if not link_header:
        return None
    match = NEXT_LINK.search(link_header)
    return match.group(1) if match else None


def build_headers(token):
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "agent-warehouse",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    return headers


def get(url, headers):
    """url asked: its status, its body, read to MOST bytes and one more, and its Link header."""
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with OPENER.open(request, timeout=30) as response:
            return response.status, response.read(MOST + 1), response.headers.get("Link")
    except urllib.error.HTTPError as error:
        return error.code, error.read(MOST + 1), None
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise Refused(f"GitHub API request to {url} failed: {getattr(error, 'reason', error)}") from None


def get_json(url, headers):
    """The JSON url answers with a 200, and its Link header; Refused, with GitHub's own words, for anything else."""
    status, body, link = get(url, headers)
    if len(body) > MOST:
        raise Refused(f"GitHub API request to {url} answered more than {MOST} bytes")
    if status != 200:
        try:
            detail = json.loads(body).get("message", "")
        except (ValueError, AttributeError, TypeError):
            detail = body.decode("utf-8", "replace")
        raise Refused(f"GitHub API request to {url} failed: HTTP {status} {detail}".rstrip())
    try:
        return json.loads(body), link
    except ValueError:
        raise Refused(f"GitHub API request to {url} returned a non-JSON body: {body[:200]!r}") from None


def resolve_listing(api_url, username, token, headers, per_page, account):
    """The listing's URL and the account a git identity is derived from, or {} where none was read."""
    login = quote(username, safe="")
    public = f"{api_url}/users/{login}/repos?type=owner&per_page={per_page}"
    if token:
        me, _link = get_json(f"{api_url}/user", headers)
        me = me if isinstance(me, dict) else {}
        if str(me.get("login", "")).lower() == username.lower():
            return f"{api_url}/user/repos?affiliation=owner&per_page={per_page}", me
        return public, me
    if account:
        user, _link = get_json(f"{api_url}/users/{login}", headers)
        return public, user if isinstance(user, dict) else {}
    return public, {}


def fetch_all_pages(url, headers, api_url):
    """Every page of a listing, following each answer's next link - only to api_url's own scheme and host."""
    api = urlparse(api_url)
    repos = []
    while url:
        page, link = get_json(url, headers)
        if not isinstance(page, list) or not all(isinstance(repo, dict) for repo in page):
            raise Refused(f"Unexpected response from {url}: expected a list")
        repos.extend(page)
        url = parse_next_link(link)
        following = urlparse(url) if url else None
        if following and (following.scheme, following.netloc) != (api.scheme, api.netloc):
            elsewhere = f"{following.scheme}://{following.netloc}"
            raise Refused(f"The listing's next page is at {elsewhere}, not {api_url}: not following it.")
    return repos


def branchless(api_url, repo, headers):
    """Whether GitHub lists no branch for a repository; an answer that is not a list of branches says nothing."""
    owner, _slash, name = str(repo.get("full_name") or "").partition("/")
    if not owner or not name:
        return False
    url = f"{api_url}/repos/{quote(owner, safe='')}/{quote(name, safe='')}/branches?per_page=1"
    try:
        status, body, _link = get(url, headers)
        return status == 200 and json.loads(body) == []
    except (Refused, ValueError):
        return False


def mark(repo, include_forks, include_archived, no_branch):
    """repo as the engine is handed it: the fields its selection reads, and no_branch, asked only of one the
    selection keeps by kind, that was pushed to and that GitHub sizes at 0."""
    kept = {field: repo.get(field) for field in KEPT}
    by_kind = (include_forks or not repo.get("fork")) and (include_archived or not repo.get("archived"))
    kept["no_branch"] = bool(
        by_kind and repo.get("pushed_at") is not None and repo.get("size") == 0 and no_branch(repo)
    )
    return kept


def check_api_url(api_url, token):
    """A token over plain http to loopback alone."""
    parsed = urlparse(api_url)
    if parsed.scheme not in ("http", "https"):
        raise Refused(f"api_url must be an http(s) URL, got {api_url!r}")
    if token and parsed.scheme == "http" and parsed.hostname not in LOOPBACK:
        raise Refused(f"Refusing to send a token over plain HTTP to {parsed.hostname}; use https.")


def main():
    asked = json.loads(sys.argv[1])
    token = sys.stdin.read().strip()
    api_url = str(asked["api_url"]).rstrip("/")
    try:
        check_api_url(api_url, token)
        headers = build_headers(token)
        url, account = resolve_listing(api_url, asked["username"], token, headers, PER_PAGE, asked["account"])
        repos = fetch_all_pages(url, headers, api_url)
        forks, archived = asked["include_forks"], asked["include_archived"]
        marked = [mark(repo, forks, archived, lambda one: branchless(api_url, one, headers)) for repo in repos]
    except Refused as refused:
        print(json.dumps({"error": str(refused)}))
        sys.exit(1)
    for one in marked:
        print(json.dumps({"repo": one}))
    user = {field: account.get(field) for field in USER_FIELDS} if account else {}
    print(json.dumps({"total": len(repos), "user": user, "token": bool(token)}))


if __name__ == "__main__":
    main()
'''

# github_repos.py's: the fields of a repository the selection keeps, and its account's.
FIELDS = ("name", "full_name", "clone_url", "ssh_url", "default_branch", "fork", "archived", "private")
USER_FIELDS = ("login", "name", "id", "type")


def filter_repos(repos, include_forks, include_archived, no_branch=lambda repo: False):
    """github_repos.py's filter_repos, copied whole: forks and archived ones out unless asked for, each kept with the
    listing's fields and whether it is empty, sorted by name in any case."""
    selected = []
    for repo in repos:
        if repo.get("fork") and not include_forks:
            continue
        if repo.get("archived") and not include_archived:
            continue
        entry = {field: repo.get(field) for field in FIELDS}
        entry["empty"] = repo.get("pushed_at") is None or (repo.get("size") == 0 and no_branch(repo))
        selected.append(entry)
    return sorted(selected, key=lambda r: str(r["name"]).lower())


def no_branch(repo: Mapping[str, object]) -> bool:
    """Whether the listing found no branch for repo, as the machine asked GitHub."""
    return repo.get("no_branch") is True


# ------------------------------------------------------------------ the settings ----


@dataclasses.dataclass(frozen=True)
class Wanted:
    """The settings as the step reads them: gh's sign-in's (github.wanted's), and the listing's, the selection's, the
    identity's and the checkouts'. own: the machine's own list, None where it follows github_projects."""

    gh: github.Wanted
    username: str
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    forks: bool
    archived: bool
    protocol: str
    name: str
    email: str
    folder: str
    home: str
    api_url: str
    pull: bool
    depth: int
    jobs: int
    own: tuple[str, ...] | None


def _setting(ctx: Context, name: str) -> object:
    if name in OPTIONS:
        return ctx.settings.get(name, OPTIONS[name])
    if name in RUN_SET:
        return ctx.settings.get(name, RUN_SET[name])
    if name not in ctx.settings:
        raise Failed(f"the run's settings hold no {name}, which the repositories' clones read")
    return ctx.settings[name]


def _names(ctx: Context, name: str) -> tuple[str, ...]:
    value = _setting(ctx, name)
    value = () if value is None else value
    if isinstance(value, str) or not isinstance(value, Sequence) or not all(isinstance(one, str) for one in value):
        raise Failed(f"{name} is a list of repository names")
    return tuple(value)


def _switch(ctx: Context, name: str) -> bool:
    value = _setting(ctx, name)
    if not isinstance(value, bool):
        raise Failed(f"{name} is true or false")
    return value


def _line(ctx: Context, name: str) -> str:
    value = _setting(ctx, name)
    value = "" if value is None else value
    if not isinstance(value, str) or value.splitlines() not in ([], [value]):
        raise Failed(f"{name} is one line of text")
    return value


def wanted(ctx: Context) -> Wanted:
    """The settings, held before anything is read: gh's (whose token joins the run's redaction list there) and the
    step's own."""
    gh = github.wanted(ctx)
    username = _line(ctx, "github_username")
    if not username:
        raise Failed(
            "github_username names no GitHub account, so no repository can be listed: set it to the account whose"
            " repositories the machines clone"
        )
    protocol = _setting(ctx, "github_projects_clone_protocol")
    if protocol not in PROTOCOLS:
        raise Failed(f"github_projects_clone_protocol is one of {', '.join(PROTOCOLS)}")
    # The role's spec types it text, and its play made it a number by `default(0, true) | int`.
    depth = _setting(ctx, "github_projects_depth")
    depth = "" if depth is None else str(depth) if isinstance(depth, int) and not isinstance(depth, bool) else depth
    if not isinstance(depth, str) or not (depth == "" or (depth.isascii() and depth.isdigit())):
        raise Failed("github_projects_depth is a shallow clone's depth, a whole number, or empty for the whole history")
    jobs = _setting(ctx, "github_projects_clone_jobs")
    if not isinstance(jobs, int) or isinstance(jobs, bool) or jobs < 1:
        raise Failed("github_projects_clone_jobs is how many clones run at once, 1 or more")
    own = _setting(ctx, "vm_repos")
    if own is not None and (
        isinstance(own, str) or not isinstance(own, Sequence) or not all(isinstance(one, str) for one in own)
    ):
        raise Failed("the machine's own list of repositories is a list of names")
    return Wanted(
        gh,
        username,
        _names(ctx, "github_projects"),
        _names(ctx, "github_projects_exclude"),
        _switch(ctx, "github_projects_include_forks"),
        _switch(ctx, "github_projects_include_archived"),
        str(protocol),
        _line(ctx, "git_user_name"),
        _line(ctx, "git_user_email"),
        _line(ctx, "vm_projects_dir"),
        _line(ctx, "github_projects_home"),
        _line(ctx, "github_projects_api_url").rstrip("/"),
        _switch(ctx, "github_projects_update_existing"),
        int(depth or 0),
        jobs,
        None if own is None else tuple(own),
    )


# ------------------------------------------------------------------ the listing ----


@dataclasses.dataclass(frozen=True)
class Listing:
    """What the machine's listing said: each repository with the fields the selection reads and whether it has no
    branch, the count GitHub gave before any filter, the account a git identity is derived from ({} for none), and
    whether a token listed (the store's, or gh's)."""

    repos: tuple[Mapping[str, object], ...]
    total: int
    user: Mapping[str, object]
    token: bool


def _failure(result: object) -> str:
    if isinstance(result, runner.Done):
        said = text.one_line(result.said or "", 300)
        return f"exit {result.status}" + (f": {said}" if said else "")
    if isinstance(result, runner.Unreachable):
        return "the machine was not reached (ssh's exit 255)"
    if isinstance(result, runner.TimedOut):
        return "it took longer than its time"
    if isinstance(result, runner.Silent):
        return "it said nothing in its time"
    if isinstance(result, remote.Answer):
        return f"exit {result.status}" + (f": {result.said}" if result.said else "")
    if isinstance(result, (remote.NotRun, remote.NotAnswered)):
        return text.one_line(getattr(result, "said", "") or getattr(result, "why", ""), 300)
    return text.one_line(str(getattr(result, "why", "")), 300)


def _said(want: Wanted, words: str) -> str:
    """words a machine or GitHub said, as this run says them: plain, one line, the token struck."""
    words = text.one_line(words, 600)
    return want.gh.token.struck(words) if want.gh.token is not None else words


async def listing(ctx: Context, want: Wanted, python: str, account: bool) -> Listing:
    """The account's repositories, listed on the machine with whatever token it holds: see the module's docstring."""
    asked = json.dumps(
        {
            "username": want.username,
            "api_url": want.api_url,
            # A machine's own list names what it wants: a fork or an archived repository on it is asked for.
            "include_forks": want.forks or want.own is not None,
            "include_archived": want.archived or want.own is not None,
            "account": account,
        }
    )
    if want.gh.token is not None:
        result = await ctx.run([python, "-I", "-c", LISTING, asked], limits=LISTED, stdin=[want.gh.token, "\n"])
    else:
        result = await ctx.run(["sh", "-c", WITH_GH, "sh", python, LISTING, asked], limits=LISTED)
    lines = []
    if isinstance(result, runner.Done):
        try:
            lines = [remote.loads(line) for line in result.lines]
        except ValueError:
            lines = []
    error = next((line.get("error") for line in lines if isinstance(line, dict) and "error" in line), None)
    if isinstance(error, str):
        raise Failed(f"The repositories of {want.username} could not be listed on {want.gh.name}: {_said(want, error)}")
    if not (isinstance(result, runner.Done) and result.status == 0 and lines and _whole(lines)):
        said = _said(want, _failure(result))
        raise Failed(f"The repositories of {want.username} could not be listed on {want.gh.name}: {said}")
    end = lines[-1]
    user = end["user"]
    return Listing(
        tuple(line["repo"] for line in lines[:-1]),
        end["total"],
        {field: user.get(field) for field in USER_FIELDS} if user else {},
        end["token"],
    )


def _whole(lines: list[object]) -> bool:
    """Whether the listing's lines are a repository a line, then its end: the count, the account and the token's
    word, each of its kind."""
    *repos, end = lines
    if not isinstance(end, dict) or set(end) != {"total", "user", "token"}:
        return False
    if not (isinstance(end["total"], int) and isinstance(end["user"], dict) and isinstance(end["token"], bool)):
        return False
    for line in repos:
        repo = line.get("repo") if isinstance(line, dict) and set(line) == {"repo"} else None
        if not isinstance(repo, dict) or not isinstance(repo.get("name"), str):
            return False
        if not all(isinstance(repo.get(key, False), bool) for key in ("fork", "archived", "private", "no_branch")):
            return False
    return True


# ------------------------------------------------------------------ the selection ----


@dataclasses.dataclass(frozen=True)
class Selection:
    """What of the listing is cloned, and what not, with why: the listing after filter_repos; the repositories
    selected; the empty, the private skipped for want of a credential (and the reason), the dotted, and the names of
    the machine's own list the account has none of."""

    listed: tuple[Mapping[str, object], ...]
    selected: tuple[Mapping[str, object], ...]
    empty: tuple[str, ...]
    private: tuple[str, ...]
    private_reason: str
    dotted: tuple[str, ...]
    own_missing: tuple[str, ...]


def cloneable(protocol: str, found: github.Found, want: github.Wanted) -> bool:
    """Whether git has a credential for a private repository: over ssh, a key of the person's own; over https, gh
    signed in and git asking gh, made so by this run (github_auth, gh there) or so already."""
    return protocol != HTTPS or (found.signed_in and ((want.auth and found.gh) or found.gh_helper))


def private_reason(found: github.Found) -> str:
    if not found.gh:
        return "private, skipped as git has no credential for them (gh is not installed)"
    if not found.signed_in:
        return "private, skipped until gh is signed in"
    return (
        "private, skipped as git has no credential for them (github_auth is false, and gh is not its credential helper)"
    )


def select(
    listed: Sequence[Mapping[str, object]],
    *,
    own: Sequence[str] | None,
    include: Sequence[str],
    exclude: Sequence[str],
    credential: bool,
    reason: str = "",
) -> Selection:
    """The role's selection (roles/github_projects/vars/main.yml): see the module's docstring."""
    names = [str(repo["name"]) for repo in listed]
    dotted = [name for name in names if DOTTED.match(name)]
    own_names = list(own or ())
    wanted = own_names if own is not None else list(include) if include else names
    candidates = [
        repo
        for repo in listed
        if not repo["empty"] and repo["name"] not in dotted and repo["name"] not in exclude and repo["name"] in wanted
    ]
    private = [] if credential else [str(repo["name"]) for repo in candidates if repo["private"]]
    return Selection(
        tuple(listed),
        tuple(repo for repo in candidates if repo["name"] not in private),
        tuple(str(repo["name"]) for repo in listed if repo["empty"]),
        tuple(private),
        reason,
        tuple(dotted),
        tuple(name for name in own_names if name not in names),
    )


# ------------------------------------------------------------------ the identity ----


IDENTITY = ("user.name", "user.email")


def identity_needed(auth: bool, set_by_hand: frozenset[str], name: str, email: str) -> bool:
    """Whether an identity is left to derive from the account: only where the run manages gh, and a key is set
    neither here nor by hand."""
    return auth and ((not name and "user.name" not in set_by_hand) or (not email and "user.email" not in set_by_hand))


def identity_wanted(set_by_hand: frozenset[str], name: str, email: str, user: Mapping[str, object]) -> dict[str, str]:
    """The keys of ~/.gitconfig to write: a value set here always; otherwise the account's, only where the key is not
    set by hand; never an organisation's, which nobody commits as."""
    person = bool(user.get("login")) and (user.get("type") or "User") != ORGANIZATION
    derived_name = str(user.get("name") or user.get("login")) if person else ""
    derived_email = f"{user.get('id')}+{user.get('login')}@users.noreply.github.com" if person else ""
    wanted = {
        "user.name": name or ("" if "user.name" in set_by_hand else derived_name),
        "user.email": email or ("" if "user.email" in set_by_hand else derived_email),
    }
    return {key: text.one_line(value, None) for key, value in wanted.items() if value}


# ------------------------------------------------------------------ the step ----


@dataclasses.dataclass(frozen=True)
class State:
    """What the probe found and what the run would do: the settings; gh's state; the projects folder; the identity as
    it is and the keys to write; the listing and its selection; GitHub's key lines (over ssh); and what the helper's
    verb, asked to change nothing, said of the checkouts."""

    want: Wanted
    found: github.Found
    folder: str
    under_home: bool
    identity: Mapping[str, str]
    writes: Mapping[str, str]
    listing: Listing
    selection: Selection
    keys: tuple[str, ...]
    plan: Mapping[str, object]

    @property
    def organisation(self) -> bool:
        """Whether an identity was to be derived from an organisation, which gives none."""
        needed = identity_needed(self.want.gh.auth, frozenset(self.identity), self.want.name, self.want.email)
        return needed and self.listing.user.get("type") == ORGANIZATION


class Clones:
    """The step: see the module's docstring. python: the machine's interpreter the listing and the helper run on;
    meta: where GitHub's published SSH host keys are read, by default the API's own /meta."""

    name = "clones"
    part = "github_projects"

    def __init__(self, *, python: str = bundle.PYTHON, meta: str | None = None) -> None:
        if not isinstance(python, str) or not python or python.split() != [python]:
            raise TypeError("the machine's interpreter is named by one word, its path or its name on the PATH")
        if meta is not None and not isinstance(meta, str):
            raise TypeError("GitHub's published keys are read at a URL, a str")
        self.python = python
        self.meta = meta

    async def probe(self, ctx: Context) -> State:
        """The settings held, gh's state, the identity, the listing, its selection and the checkouts as they are:
        nothing changed."""
        want = wanted(ctx)
        found = await github.read(ctx)
        home = want.home or await self._home(ctx)
        folder = sessions.projects_dir(want.folder, home)
        if not folder.startswith("/") or folder.splitlines() != [folder]:
            raise Failed(f"the projects folder is an absolute path on the machine, one line: not {folder!r}")
        identity = await self._identity(ctx) if want.gh.auth else {}
        needed = identity_needed(want.gh.auth, frozenset(identity), want.name, want.email)
        listed = await listing(ctx, want, self.python, needed)
        own = want.own is not None
        selection = select(
            filter_repos(listed.repos, want.forks or own, want.archived or own, no_branch),
            own=want.own,
            include=want.include,
            exclude=want.exclude,
            credential=cloneable(want.protocol, found, want.gh),
            reason=private_reason(found),
        )
        writes = (
            {
                key: value
                for key, value in identity_wanted(frozenset(identity), want.name, want.email, listed.user).items()
                if identity.get(key) != value
            }
            if want.gh.auth
            else {}
        )
        keys = await self._keys(ctx, want) if want.protocol != HTTPS else ()
        plan = await self._verb(ctx, want, folder, selection, keys, check=True)
        under = folder == home.rstrip("/") or folder.startswith(home.rstrip("/") + "/")
        return State(want, found, folder, under, identity, writes, listed, selection, keys, plan)

    async def apply(self, ctx: Context, state: State) -> bool:
        want, changed = state.want, False
        for key, value in state.writes.items():
            result = await ctx.run(["git", "config", "--global", key, value], limits=QUICK)
            if not (isinstance(result, runner.Done) and result.status == 0):
                raise Failed(f"git's {key} could not be set on {want.gh.name}: {_said(want, _failure(result))}")
            changed = True
        if not state.plan["folder"]:
            await self._folder(ctx, state)
            changed = True
        done = await self._verb(ctx, want, state.folder, state.selection, state.keys, check=False)
        changed = changed or bool(done["known_hosts"] or done["cloned"] or done["leftovers"] or done["pulled"])
        self._say(ctx, state)
        if done["not_cloned"]:
            failed = "; ".join(
                f"{one['name']} ({one['said'] or ('git exited ' + str(one['rc']))})" for one in done["not_cloned"]
            )
            raise Failed(
                f"{len(done['not_cloned'])} of {len(state.selection.selected)} repositories could not be cloned into"
                f" {state.folder} on {want.gh.name}: {_said(want, failed)}. The rest were cloned."
            )
        if done["not_pulled"]:
            failed = "; ".join(
                f"{one['name']} ({one['said'] or ('git exited ' + str(one['rc']))})" for one in done["not_pulled"]
            )
            raise Failed(
                f"{len(done['not_pulled'])} of the checkouts in {state.folder} on {want.gh.name} were not pulled, and"
                f" are left as they are: {_said(want, failed)}."
            )
        return changed

    def difference(self, state: State) -> Difference | None:
        """What the run would do to the checkouts and the identity: never a token."""
        plan, said = state.plan, []
        if state.writes:
            said.append(f"git's {' and '.join(state.writes)} set")
        if not plan["folder"]:
            said.append(f"{state.folder} made")
        if plan["known_hosts"]:
            said.append("GitHub's published SSH host keys trusted for github.com")
        if plan["leftovers"]:
            said.append(f"{len(plan['leftovers'])} partial folders an earlier run left removed")
        if plan["cloned"]:
            said.append(f"cloned: {', '.join(_shown(plan['cloned']))}")
        if state.want.pull and plan["skipped_existing"]:
            pulled = ", ".join(_shown(plan["skipped_existing"]))
            said.append(f"pulled by a fast-forward where GitHub has more: {pulled}")
        if not said:
            return None
        there = len(plan["skipped_existing"])
        return Difference(
            f"the repositories in {state.folder}",
            f"{there} of the {len(state.selection.selected)} selected checked out",
            "; ".join(said),
        )

    # ------------------------------------------------------------ the reads and the writes ----

    @staticmethod
    async def _home(ctx: Context) -> str:
        home = await ctx.run(["sh", "-c", HOME], limits=QUICK)
        if not (isinstance(home, runner.Done) and home.status == 0 and len(home.lines) == 1 and home.lines[0]):
            raise Failed(f"the account's home could not be read on the machine: {_failure(home)}")
        return home.lines[0].rstrip("/") or "/"

    @staticmethod
    async def _identity(ctx: Context) -> dict[str, str]:
        """Each key of the identity the account's ~/.gitconfig sets, with its value."""
        found = {}
        for key in IDENTITY:
            result = await ctx.run(["git", "config", "--global", "--get", key], limits=QUICK)
            # 1 is "not set", which includes no ~/.gitconfig at all.
            if not (isinstance(result, runner.Done) and result.status in (0, 1)):
                raise Failed(f"git's {key} could not be read on the machine: {_failure(result)}")
            if result.status == 0 and result.lines and result.lines[0]:
                found[key] = result.lines[0]
        return found

    async def _keys(self, ctx: Context, want: Wanted) -> tuple[str, ...]:
        """GitHub's published SSH host keys, read on this computer by the one transport: /meta takes no token."""
        import asyncio

        url = self.meta or f"{want.api_url}/meta"
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.query or parts.fragment:
            raise Failed(f"GitHub's published SSH host keys are read over https alone, at a path, never at {url}")
        path = parts.path or "/"
        setting, tls_ca_file = _line(ctx, "network_proxy"), _line(ctx, "tls_ca_file") or None
        try:
            chosen = proxy.choose(setting, ctx.env, url)
        except ValueError as error:
            raise Failed(f"GitHub's published SSH host keys could not be read at {url}: {error}") from None

        def fetched() -> transport.Answer:
            with transport.Transport(
                f"https://{parts.netloc}",
                context=trust.context(tls_ca_file),
                user_agent=USER_AGENT,
                most=META_MOST,
                proxy=chosen,
            ) as reading:
                return reading.request("GET", path, deadline=META_SECONDS)

        try:
            answer = await asyncio.to_thread(fetched)
        except (transport.TransportError, trust.Unusable, ValueError) as error:
            raise Failed(f"GitHub's published SSH host keys could not be read at {url}: {error}") from None
        keys = None
        if answer.status == 200:
            try:
                document = json.loads(answer.body)
            except ValueError:
                document = None
            listed = document.get("ssh_keys") if isinstance(document, dict) else None
            held = isinstance(listed, list) and all(isinstance(key, str) and KEY.fullmatch(key) for key in listed)
            if held and listed:
                keys = tuple(listed)
        if keys is None:
            raise Failed(
                f"GitHub's published SSH host keys could not be read at {url}: HTTP {answer.status}, and no list of"
                " key lines"
            )
        return keys

    async def _verb(
        self, ctx: Context, want: Wanted, folder: str, selection: Selection, keys: Sequence[str], *, check: bool
    ) -> Mapping[str, object]:
        """The helper's verb clones on the machine, as its account: see guest/helper/clones.py."""
        asked = {
            "dir": folder,
            "repos": [
                {field: repo.get(field) for field in ("name", "full_name", "clone_url", "ssh_url", "default_branch")}
                for repo in selection.selected
            ],
            "url": "clone_url" if want.protocol == HTTPS else "ssh_url",
            "depth": want.depth,
            "jobs": want.jobs,
            "timeout": CLONE_SECONDS,
            "pull": want.pull,
            "known_hosts": list(keys),
            "check": check,
        }
        answer = await remote.helper(
            ctx.job,
            ctx.target,
            "clones",
            asked=asked,
            as_root=False,
            limits=CLONING,
            env=dict(ctx.env),
            python=self.python,
        )
        document = answer.lines[0] if isinstance(answer, remote.Answer) and len(answer.lines) == 1 else None
        if isinstance(document, dict) and isinstance(document.get("error"), str):
            said = _said(want, document["error"])
            raise Failed(f"The repositories could not be cloned into {folder} on {want.gh.name}: {said}")
        if not (isinstance(answer, remote.Answer) and answer.status == 0 and _answered(document)):
            raise Failed(
                f"The checkouts in {folder} could not be read on {want.gh.name}: {_said(want, _failure(answer))}"
            )
        return document

    async def _folder(self, ctx: Context, state: State) -> None:
        """The projects folder made, 0755: as the account under its home, else as root and given to the account."""
        if state.under_home:
            result = await ctx.run(["sh", "-c", MAKE_FOLDER, "sh", state.folder], limits=QUICK)
        else:
            words = ["sh", "-c", MAKE_FOLDER_FOR, "sh", state.folder, ctx.target.account]
            result = await ctx.run(words, as_root=True, limits=QUICK)
        if not (isinstance(result, runner.Done) and result.status == 0):
            raise Failed(f"{state.folder} could not be made on the machine: {_failure(result)}")

    @staticmethod
    def _say(ctx: Context, state: State) -> None:
        # Here alone, as github.py does: guest/ is imported where the engine is not.
        from claude_vm.engine import report

        report.say(ctx.job, ctx.machine, "github_identity", *reported(state))


def _answered(document: object) -> bool:
    """Whether the verb's document is one of its kind."""
    if not isinstance(document, dict):
        return False
    if not (isinstance(document.get("folder"), bool) and isinstance(document.get("known_hosts"), bool)):
        return False
    for key in ("cloned", "skipped_existing", "leftovers", "pulled"):
        if not (isinstance(document.get(key), list) and all(isinstance(one, str) for one in document[key])):
            return False
    for key in ("not_cloned", "not_pulled"):
        failures = document.get(key)
        if not (isinstance(failures, list) and all(isinstance(one, dict) for one in failures)):
            return False
        if not all(isinstance(one.get("name"), str) and isinstance(one.get("said"), str) for one in failures):
            return False
    return True


def _shown(names: object) -> list[str]:
    return [text.one_line(str(name), 100) for name in names] if isinstance(names, list) else []


def reported(state: State) -> tuple[str, str]:
    """The step's report and its NEXT STEPS line under github_identity, as clone.yml's three messages say them."""
    want, selection, name = state.want, state.selection, state.want.gh.name
    skipped = ", ".join(_shown(list(selection.empty))) or "none"
    if selection.private:
        skipped += f"; {selection.private_reason}: {', '.join(_shown(list(selection.private)))}"
    if selection.dotted:
        skipped += (
            "; named with a leading dot, which would make a folder of settings there:"
            f" {', '.join(_shown(list(selection.dotted)))}"
        )
    unlisted = "" if state.listing.token else UNLISTED
    said = [
        f"{name}: {len(selection.selected)} of {len(selection.listed)} repositories selected for {state.folder}"
        f" (skipped as empty: {skipped}).{unlisted}"
    ]
    if selection.own_missing:
        said.append(
            f"{name}: not cloned from its own list of repositories, as {want.username} has none listed by that name:"
            f" {', '.join(_shown(list(selection.own_missing)))}.{unlisted}"
        )
    left = ""
    if state.organisation:
        login = text.one_line(str(state.listing.user.get("login") or want.username), 100)
        left = (
            f"{name}: {login} is an organisation, so no git identity is derived from it, and a commit on the machine"
            " fails until one is set. Set git_user_name and git_user_email, or sign gh in to your own account, whose"
            f" identity that run derives: {want.gh.login_command}"
        )
        said.append(left)
    return " ".join(said), left
