"""just status without ansible-playbook: what every VM this project created is doing, in the time it takes to ask.

The outline is drawn first, before anything is read. The settings come from a snapshot, or from Ansible in this
process (claude_vm.status.settings). The Proxmox API is read in two waves of GETs at once, each VM drawn the moment
the listing lands, and each VM's probe (claude_vm.status.reach) starts as soon as its own address is read. One
connection per VM, one program there reading everything at once: docs/architecture/lifecycle.md §4.6.

Read-only information, and it must never change anything: GETs to the Proxmox API, and over SSH only reads. It writes
only into its own folder (.cache/status/) and the run's log.

Drawn through the one live region every live output shares (claude_vm.tui.live_region), from the same model and the
same functions for the frames and the finished report. Piped, under --json, or at a terminal that cannot draw it,
nothing is drawn.

Which of the listing's VMs are the fleet's is the reader's to say (`run`'s reader): today's, `Tagged`, the VMs whose
tags hold the project's, the one tools/status runs with; aw status hands it another, its reader of the pool (`Pool`),
which reads the pool's members and each one's record (docs/architecture/engine.md §16.3). Each of Tagged's methods is
a place where the two part, and each does what this engine did before there was a second. `program` is the name the
run's own messages give the program: just for tools/status, aw for aw status; the commands a report names are today's
lines unless the settings give them printed whole under "lines" (aw status's, model.py's printed_line), which the
drawing is given too.
"""

from __future__ import annotations

import getpass
import http.client
import json
import logging
import os
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path

from .. import proxmox as proxmox_reads
from .. import route as routes
from ..checks import vm_control_name, vm_ssh_args
from ..text import one_line
from ..tui import live_region as live
from ..tui import status as status_view
from . import found as vm_found
from . import model, reach, settings

GET_WORKERS = 8
# How long the main thread waits at a time for what it asked off itself (_off_main): the most a Ctrl-C waits to land.
OFF_MAIN_SLICE = 0.1
# What is read before anything else, the listing first: the version, the storages' and the nodes' figures, and the
# pools the token may read the members of (Pool.Audit), without which the listing names no guest's pool.
FIRST_WAVE = (
    "cluster/resources?type=vm",
    "version",
    "cluster/resources?type=storage",
    "cluster/resources?type=node",
    "cluster/resources?type=pool",
)


class Display:
    """What the live region asks of Ansible's Display: a lock, and display(), with the run's log as Ansible keeps it."""

    def __init__(self, log_path: str):
        self._lock = threading.RLock()
        self._log = None
        if log_path:
            try:
                handler = logging.FileHandler(log_path, encoding="utf-8")
            except OSError as error:
                # As ansible-core does with a log it cannot write: said, and the run goes on.
                print(f"[WARNING]: just status cannot write its log: {error}", file=sys.stderr)
                return
            self._log = logging.getLogger("status")
            handler.setFormatter(
                logging.Formatter(
                    f"%(asctime)s p=%(process)d u={getpass.getuser()} n=ansible %(levelname)s| %(message)s"
                )
            )
            self._log.addHandler(handler)
            self._log.setLevel(logging.INFO)
            self._log.propagate = False

    def display(self, msg, color=None, stderr=False, screen_only=False, log_only=False, newline=True, caplevel=None):
        if not log_only:
            stream = sys.stderr if stderr else sys.stdout
            stream.write(msg + ("\n" if newline else ""))
            stream.flush()
        if self._log and not screen_only:
            self._log.info(msg)

    def prompt_until(self, *args, **kwargs):
        raise RuntimeError("just status never asks")

    do_var_prompt = prompt_until


def _color_on() -> bool:
    return not (
        os.environ.get("NO_COLOR") or os.environ.get("ANSIBLE_NOCOLOR", "").lower() in ("1", "true", "yes", "on")
    )


class Screen:
    """The report as it fills: the outline until the fleet lands, then each VM as what is read of it does."""

    def __init__(self, sections, quiet, log_path):
        self.found: dict = {}
        # The commands the report names printed whole, by key (model.py's _line), once the settings are loaded.
        self.lines = None
        self.sections = sections
        self.quiet = quiet
        self.started = time.monotonic()
        self.display = Display(log_path)
        self.fleet_at = None
        self.done_at: dict = {}
        drawing = live.can_draw() and not quiet
        more = "⋮ {} more lines, shown when the run ends"
        self.out = live.Output(self.display, frame=self.frame, live=drawing, color=_color_on(), more=more)
        self.color = self.out.color
        if drawing:
            self.out.start()
            self.out.spin()
            self.out.redraw()

    @staticmethod
    def now_said() -> str:
        return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z").strip()

    def frame(self, cols, rows, glyph):
        elapsed = f"{time.monotonic() - self.started:.1f} s"
        if "localhost" not in self.found:
            title = f"agent-warehouse  {self.now_said()} · reading Proxmox"
            text = status_view.skeleton_text(title, self.sections, self.color, glyph=glyph, cols=cols - 1)
            return text.split("\n") + [model.one_line(f"{glyph} listing the VMs · {elapsed}", cols - 1)]
        report = model.status_report(dict(self.found), self.now_said(), time.time(), live=True)
        text = status_view.status_text(report, self.color, glyph=glyph, cols=cols - 1, lines=self.lines)
        api = [vm["name"] for vm in report["vms"] if vm.get("pending_api")]
        ssh = [vm["name"] for vm in report["vms"] if vm.get("pending_ssh")]
        if api:
            doing = f"reading {', '.join(api)}"
        elif ssh:
            doing = f"reading {', '.join(ssh)} over SSH"
        else:
            doing = "ranking what needs attention"
        reading = len(api) + len(ssh)
        self.out.title(f"status · reading {reading} VM{'' if reading == 1 else 's'}" if reading else "status")
        return text.split("\n") + [model.one_line(f"{glyph} {doing} · {elapsed}", cols - 1)]

    def land(self, host, part, merge=False):
        """What was read of a host drawn. ``merge`` adds its keys to what the host has, under the lock: a VM's Proxmox
        part and its SSH part land in either order, from two threads, and neither replaces the other."""
        now = time.monotonic() - self.started
        with self.display._lock:
            self.found[host] = {**self.found.get(host, {}), **part} if merge else part
            if host == "localhost":
                self.fleet_at = self.fleet_at if self.fleet_at is not None else now
            else:
                self.done_at[host] = now
        self.out.redraw()

    def reading(self, report):
        """The line on what was read, and how long it took: proxmox, then the VMs."""
        parts = [f"read in {time.monotonic() - self.started:.1f} s"]
        proxmox = report["proxmox"]
        if proxmox.get("off"):
            parts.append("proxmox not asked (claude_vm_discovery is off)")
        elif proxmox.get("unread"):
            parts.append("proxmox did not answer")
        elif self.fleet_at is not None:
            parts.append(f"proxmox {self.fleet_at:.1f} s")
        read = [vm for vm in report["vms"] if vm.get("read")]
        if read:
            done = [vm for vm in read if vm["name"] in self.done_at]
            last = max((self.done_at[vm["name"]] for vm in done), default=0.0)
            parts.append(f"vms {len(done)} of {len(read)} {last:.1f} s")
        return " · ".join(parts)

    def finish(self, report):
        self.out.title("status · done")
        self.out.finish()
        reading = self.reading(report)
        plain = status_view.status_text(report, False, reading=reading, lines=self.lines)
        if self.quiet:
            # Under --json the screen gets the JSON alone; the run log still gets the report.
            self.display.display(plain, log_only=True)
        else:
            coloured = status_view.status_text(report, True, reading=reading, lines=self.lines) if self.color else plain
            self.out.print([(coloured, plain)])


class Emitter(Screen):
    """The screen of a host's read for just status across hosts (claude_vm.status.hosts): nothing drawn, and what
    lands written as one JSON line each on standard output, for the run that draws every host's."""

    def __init__(self, sections):
        super().__init__(sections, True, "")

    def emit(self, message: dict) -> None:
        with self.display._lock:
            sys.stdout.write(json.dumps(message, sort_keys=True) + "\n")
            sys.stdout.flush()

    def land(self, host, part, merge=False):
        super().land(host, part, merge)
        self.emit({"land": [host, part, merge]})

    def finish(self, report):
        self.out.finish()
        self.emit({"end": {"exit": report["exit"], "fleet_at": self.fleet_at, "done_at": self.done_at}})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """The token is never carried to another address: a redirect is an answer, not a place to go."""

    def redirect_request(self, *args, **kwargs):
        return None


class Proxmox:
    """GETs, a few at a time, each result in the shape of a registered uri task: item, status, json, msg."""

    def __init__(self, fleet):
        self.base = fleet["url"].rstrip("/")
        self.headers = fleet["headers"]
        self.timeout = fleet["timeout"]
        context = ssl.create_default_context(cafile=fleet["ca_path"] or None)
        if not fleet["validate"]:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        self.opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=context))
        self.pool = ThreadPoolExecutor(max_workers=GET_WORKERS)

    def get(self, item):
        if isinstance(item, dict) and item.get("skip"):
            return {"item": item, "skipped": True}
        path = item["path"] if isinstance(item, dict) else item
        one = {"item": item, "status": -1, "msg": "", "json": None}
        body = b""
        try:
            request = urllib.request.Request(f"{self.base}/{path.lstrip('/')}", headers=self.headers, method="GET")
            with self.opener.open(request, timeout=self.timeout) as response:
                one["status"], body = response.status, response.read()
        except urllib.error.HTTPError as error:
            one["status"], one["msg"] = error.code, f"HTTP Error {error.code}: {error.reason}"
            try:
                body = error.read()
            except (OSError, http.client.HTTPException):
                body = b""  # the answer's status is what is known of it
        except ValueError:
            # http.client's words for a header it refuses quote the header, the token in it.
            one["msg"] = "the request could not be made"
        except (OSError, http.client.HTTPException) as error:
            # A connection cut short (IncompleteRead) or an answer that is no HTTP (BadStatusLine): this read failed.
            one["msg"] = f"Request failed: {str(error) or type(error).__name__}"
        try:
            one["json"] = reach.loads(body) if body else None
        except (RecursionError, ValueError):
            one["json"] = None
        return one

    def later(self, item):
        return self.pool.submit(self.get, item)


def _off_main(pool, call, *args, **kwargs):
    """``call``'s answer, run on a thread of ``pool`` while this one waits for it: how the main thread asks anything
    that starts an ssh of route.py's. Python raises a Ctrl-C's KeyboardInterrupt in the main thread alone, so it lands
    in this wait, never between an ssh's start and its record (route._started), and end_all ends that ssh with the
    rest."""
    return _waited(pool.submit(call, *args, **kwargs))


def _waited(future):
    """``future``'s answer, waited for on the main thread a slice at a time: where Python's locks are a mutex and a
    condition (macOS), a signal wakes no wait without a limit, and a Ctrl-C would land only once the future had ended
    (a probe's 45 s at most)."""
    while not future.done():
        try:
            future.exception(timeout=OFF_MAIN_SLICE)
        except TimeoutError:
            pass
    return future.result()


def _as_done(futures):
    """Each of ``futures`` as it ends, as ``as_completed`` gives them, each wait a slice long (``_waited``)."""
    pending = set(futures)
    while pending:
        done, pending = wait(pending, timeout=OFF_MAIN_SLICE, return_when=FIRST_COMPLETED)
        yield from done


def _data(result):
    body = result.get("json") if isinstance(result, dict) else None
    return body.get("data") if isinstance(body, dict) else None


def _second_wave(requested, template, storages, reading):
    """Every GET that follows the listing: each VM's config, address, state and history, the template's config and each
    storage's disks. A VM's reads each say which VM's they are, and which read. Every VM's config and address come
    first, so that no VM's probe waits behind another VM's history. Only a VM read further (``reading``, by name) has
    reads of its own: one on a node that is down would wait out the timeout of each, holding up the fleet."""
    addresses, items = [], []
    for vm in requested:
        status = vm.get("status", "unknown")
        path = f"nodes/{vm.get('node')}/qemu/{vm.get('vmid')}"
        vmid = vm.get("vmid")
        addresses.append({"kind": "config", "vmid": vmid, "path": f"{path}/config", "skip": status == "unknown"})
        addresses.append(
            {
                "kind": "network",
                "vmid": vmid,
                "path": f"{path}/agent/network-get-interfaces",
                "skip": status != "running",
            }
        )
        if vm.get("name") not in reading:
            continue
        reads = [("current", "status/current"), ("rrddata", "rrddata?timeframe=day&cf=AVERAGE"), ("pending", "pending")]
        if status == "running":
            reads += [("osinfo", "agent/get-osinfo"), ("fsinfo", "agent/get-fsinfo")]
        items += [{"kind": "api", "vmid": vmid, "name": name, "path": f"{path}/{tail}"} for name, tail in reads]
    items = addresses + items
    if template:
        path = f"nodes/{template.get('node')}/qemu/{template.get('vmid')}/config"
        items.append({"kind": "template", "path": path})
    for storage in storages:
        path = f"nodes/{storage['node']}/storage/{storage['storage']}/content?content=images"
        items.append({"kind": "content", "path": path, "node": storage["node"], "storage": storage["storage"]})
    return items


def _hop_words(name, hop, address, verdict):
    """What the hop said of one VM's address, from one forward of its own: (its line, where to look); a VM that got no
    forward of its own said as not having answered through it."""
    label = hop["label"]
    console = f"check {name} in Proxmox's console"
    if verdict is None:
        return f"it did not answer through {label}", console
    why, said = verdict.get("why"), routes.ssh_said(verdict.get("said"), 80)
    # ssh's own reason, as the host had it: "connect failed: Connection refused", "No route to host".
    reason = re.search(r"connect failed: ([^\n]*)", verdict.get("said") or "")
    if why == "prohibited":
        return (
            f"{label} refused to forward to {address}",
            "allow forwarding to the VMs in the host's sshd (AllowTcpForwarding, PermitOpen), or name an account that"
            " may (vm_ssh_jump)",
        )
    if why == "refused":
        why_not = one_line(reason.group(1), 60) if reason else said
        return f"through {label}, {address} did not take the connection: {why_not}", console
    if why == "silent":
        return f"no answer from {address} through {label} within {routes.LIMITS['forward']} s", console
    if why == "failed":
        return f"ssh could not use {label} as a hop to {address}: {said}", ""
    if why == "stalled":
        # The login's, never the VM's: the far end was never asked.
        return (
            f"the login to {label} for a forward of its own to {address} did not finish within"
            f" {routes.LIMITS['forward']} s",
            f"{routes.hop_login_line(hop)} here shows why",
        )
    if not verdict.get("banner"):
        return f"through {label}, {address} took the connection and closed it without a word", console
    return f"it answered a forward through {label}, but not the read", ""


def _login_hint(hop, said):
    """Where to look when the hop's login failed, by what ssh said: the host's key, which status never takes, or the
    login itself, tried by hand with the hop's own port and key."""
    tried = routes.hop_login_line(hop)
    if model.status_refusal(said) in ("changed", "unknown"):
        return f"{tried} by hand, or any run that configures, takes the host's key: status takes none"
    return f"{tried} here shows why"


def _picked(results, kind):
    return [one for one in results if one["item"].get("kind") == kind]


def _denied(results):
    """Proxmox's refusal of the token for a VM, as vm_addresses.yml says it, or ""."""
    for one in results:
        if one["item"].get("kind") in ("config", "network") and one.get("status") in (401, 403):
            return (
                f'Proxmox refused the API token for VM "{one["item"].get("vmid")}":'
                f" {one.get('msg') or 'HTTP ' + str(one.get('status'))}. Reading a VM needs VM.Audit, and its address"
                " needs the guest-agent privilege: VM.Monitor on Proxmox VE 8, VM.GuestAgent.Audit on 9"
                ' (docs/getting-started/proxmox-access.md, "The API token").'
            )
    return ""


def _wanted(vm_settings, config):
    """What the settings ask of a VM, as the role would send it. One sized when it was created, which its own tag says,
    has a size of its own: read from its config at each run, so no snapshot holds it."""
    tags = proxmox_reads.proxmox_tags((config or {}).get("tags"))
    return {**vm_settings["drift"], "sized": vm_settings["sized_tag"] in tags}


class Tagged:
    """Today's reader of the fleet, the default of run's: the listing's VMs whose tags hold the project's, each read as
    today's engine reads one. Every method takes the run and changes nothing of what it was given."""

    def members(self, run, api, data, waited):
        """The listing's entries that are the fleet's VMs: those its tags name, never a template, though it carry the
        ownership tag, as claude_vm_tagged has it: a VM made one by hand. A reader that reads more asks api for it and
        waits by waited (the main thread's wait, a slice at a time); one that cannot say which are the fleet's says why
        by run.unread and gives None."""
        pattern = run.fleet["tag_pattern"]
        return proxmox_reads.not_templates(
            [
                one
                for one in data
                if isinstance(one, dict)
                and one.get("type") == "qemu"
                and "tags" in one
                and re.search(pattern, one["tags"])
            ]
        )

    def ready(self, item):
        """A read the reader has already made, as a finished future, or None: today's has made none."""
        return None

    def later(self, run, requested):
        """GETs of the reader's own, sent after every read of the fleet's: none."""
        return []

    def joined(self, run, entry):
        """Whether the VM had joined the tailnet before: its tag that says so."""
        joined = run.fleet["tailscale_tag_pattern"]
        tags = str(entry.get("tags") or "")
        before = bool(re.search(joined, tags))
        return before

    def unkeyed(self, run, name, how, key_alias):
        """Why the VM's SSH sections are not read before its probe is sent, with where to go on: ("", "")."""
        return "", ""

    def wanted(self, run, vm_settings, entry, config):
        """What the settings ask of the VM: _wanted's."""
        return _wanted(vm_settings, config)

    def contents(self, run, contents, results):
        """Each storage's disks, as the model counts them: as Proxmox listed them."""
        return contents

    def fleet(self, run, fleet, results):
        """The fleet as the model made it, from the listing (results empty) or from every read: as it is."""
        return fleet

    def report(self, run, report):
        """The finished report: as the model made it."""
        return report


def _aliases(fleet):
    text = ""
    if fleet["ssh_config"]:
        for path in (fleet["ssh_config_file"], fleet["ssh_config_legacy"]):
            if path and os.path.isfile(os.path.expanduser(path)):
                try:
                    text = Path(os.path.expanduser(path)).read_text(encoding="utf-8")
                except OSError:
                    text = ""
                break
    return model.status_aliases(text, fleet["ssh_config"])


class Run:
    """One run: the fleet read, each VM read beside it, each piece drawn as it lands."""

    def __init__(self, opts, screen, config, reader=None, program="just"):
        self.opts = opts
        self.reader = reader or Tagged()
        self.screen = screen
        self.config = config
        self.fleet = config["fleet"]
        self.names = config["run"]["names"]
        self.sections = list(screen.sections)
        self.settings = {**self.fleet["settings"], "names": self.names, "sections": self.sections}
        # The program Tailscale's line names where it names one of its own (lines.py's tailscale_report).
        if program != "just":
            self.settings["program"] = program
        self.over_ssh = model.status_needs_ssh(self.sections)
        self.cache = Path(opts.cache) if opts.cache else None
        self.known = (settings.read_json(self.cache / "upgrades.json") or {}) if self.cache else {}
        self.kept: dict = {}
        self.lock = threading.Lock()
        self.password_lock = threading.Lock()
        self.passwords: dict = {}
        # Through the Proxmox host (lib/claude_vm/route.py): the route, worked out at each run; the hop's login, made
        # once, by the first VM that takes the hop (None until then); and each VM that did not answer through the hop,
        # for the hop's own words after the probes.
        self.route: dict | None = None
        self.hop_lock = threading.Lock()
        self.hop_ok: bool | None = None
        self.unanswered: dict = {}

    # --- a read of one VM -----------------------------------------------------------------------------------------
    def password(self, name):
        """The VM user's password on the VM called ``name``, for the sudo that asks for it: read once in a run, and only
        if one does."""
        with self.password_lock:
            if name not in self.passwords:
                self.passwords[name] = self.config["password"](name)
            return self.passwords[name]

    def asked(self, vm_settings, key):
        wanted = vm_found.parts_wanted(self.sections)
        asked = {k: v for k, v in vm_settings["asked"].items() if k in wanted or k in ("user", "limit")}
        if "system" in asked:
            asked["system"] = {**asked["system"], "known": self.known.get(key) or {}}
        return asked

    def probe(self, name, how, vm_settings, joined_before, key):
        """A VM's reads: what is drawn as soon as only apt's simulation is left, then the whole of them. Merged into
        what the VM has, beside its Proxmox part, whichever lands first."""
        host = how["host"]
        sections = self.sections
        via = how.get("via") or ""
        jump = how.get("jump") or ""
        # The words a person's own ssh needs for it through the hop, in every line that says ssh to it.
        vm_settings = {**vm_settings, "github": {**vm_settings["github"], "via": via}} if via else vm_settings
        route = {**({"via": via} if via else {}), **({"jump": jump} if jump else {})}

        def land(read):
            part = vm_found.ssh_found(read, vm_settings, name, host, sections, joined_before)
            self.screen.land(name, model.status_clean({"ssh": {**part, **route}}), merge=True)

        def early(read):
            try:
                land(read)
            except Exception:  # noqa: BLE001 - what its root sent: the whole answer, next, says so of this VM
                return

        try:
            read = reach.read(how, self.asked(vm_settings, key), early, lambda: self.password(name))
            land(read)
        except Exception as error:  # noqa: BLE001 - a failure, or what its root sent, is that VM's, said as not read
            read = {"failed": True, "msg": str(error) or type(error).__name__}
            land(read)
        # Through the hop, a connection that ended before the VM's sshd spoke is all ssh says of a VM that does not
        # answer: the hop's own words for it are read after the probes (recheck).
        if via and read.get("unreachable") and routes.hop_unanswered(read.get("msg")):
            with self.lock:
                self.unanswered[name] = (how, vm_settings, joined_before, key)
        system = (read.get("user") or {}).get("system") if isinstance(read.get("user"), dict) else None
        if isinstance(system, dict) and isinstance(system.get("upgrades"), int) and system.get("upgrades_key"):
            with self.lock:
                self.kept[key] = {
                    "key": system["upgrades_key"],
                    "upgrades": system["upgrades"],
                    "security": system.get("security"),
                }

    def unread_through(self, name, how, vm_settings, joined_before, said, hint=""):
        """A VM not read for the hop's sake, said as the hop's, never with a VM's own words."""
        part = vm_found.ssh_found(
            {"unreachable": True, "msg": said}, vm_settings, name, how["host"], self.sections, joined_before
        )
        part.update(refusal="hop", hop_hint=hint)
        self.screen.land(name, model.status_clean({"ssh": part}), merge=True)

    # --- the route through the Proxmox host ------------------------------------------------------------------------
    def settle_route(self):
        """The route to the VMs, worked out at each run before any probe starts: None where the VMs are reached
        directly. Status takes no host key: the hop's ssh holds it to what this machine knows
        (StrictHostKeyChecking=yes). No login here: the first VM that takes the hop makes it (hop_login), and a run
        with none makes none."""
        given = self.config.get("hop") or {}
        setting = str(self.fleet.get("route") or "auto")
        if not self.over_ssh or not given or setting == "direct":
            return None
        try:
            hop = routes.hop_settings(
                ssh=given.get("ssh_executable") or "ssh",
                user=routes.account(given.get("jump")) or given.get("user") or "",
                host=given.get("host"),
                port=given.get("port") or None,
                key=given.get("key") or "",
                control_dir=given.get("control_dir") or "~/.ansible/cp",
                name=given.get("name") or "",
            )
        except routes.HopRefused as refused:
            if setting == "auto" and routes.route_rule("auto", routes.addresses_of(given.get("host"))) == "direct":
                return None
            label = given.get("name") or "the Proxmox host"
            return {
                "route": "host",
                "setting": setting,
                "hop": None,
                "policy": "yes",
                "label": label,
                "refused": refused,
            }
        if routes.route_settle(setting, hop)["route"] != "host":
            return None
        return {"route": "host", "setting": setting, "hop": hop, "policy": "yes", "label": hop["label"]}

    def hop_login(self):
        """Whether the hop can be used: its shared connection made sure of once, by the first VM that takes the hop,
        the others waiting on it. One that cannot be used is one line of the report."""
        with self.hop_lock:
            if self.hop_ok is None:
                self.hop_ok = self._hop_login()
            return self.hop_ok

    def _hop_login(self):
        route = self.route
        refused = route.get("refused")
        if refused is not None:
            # The reason first, cut where it must be; the way out whole, as the hint.
            self.settings.update(
                hop_said=f"the VMs' hop through {route['label']} cannot be used: {refused.reason}",
                hop_hint=f"{refused.way_out} (vm_ssh_route: {route['setting']})" if refused.way_out else "",
            )
            return False
        hop = route["hop"]
        held = routes.hop_held(hop, "yes")
        if held["ok"]:
            return True
        self.settings.update(
            hop_said=f"ssh could not log in to {hop['label']}, the VMs' hop: {routes.ssh_said(held['said'], 80)}",
            hop_hint=_login_hint(hop, held["said"]),
        )
        return False

    def recheck(self, pool):
        """After the probes, the VMs that did not answer through the hop: one forward of its own to each of the first
        three by name. Where any answered there as an SSH server does, its banner first, the shared connection had gone
        stale: told to take no new sessions (-O stop, never -O exit, which would cut another run's tasks), made sure of
        again, and every such VM read once more, through the pool - one that again does not answer said in the hop's
        words. A far end that takes the connection and closes it without a word is no stale connection, and stops none.
        Otherwise what the hop said is each one's line, and the rest are said as not having answered through it."""
        route, unanswered = self.route, dict(self.unanswered)
        self.unanswered.clear()
        if not route or not route.get("hop") or not unanswered:
            return
        hop, names = route["hop"], sorted(unanswered)
        verdicts = {}
        for name in names[:3]:
            how = unanswered[name][0]
            # Each ssh of the hop's asked off the main thread, which takes a Ctrl-C (_off_main).
            verdicts[name] = _off_main(pool, routes.hop_forward, hop, how["host"], "yes", port=how.get("port") or 22)
            if verdicts[name]["why"] == "answered" and verdicts[name].get("banner"):
                _off_main(pool, routes.hop_stop, hop, "yes")
                _off_main(pool, routes.hop_held, hop, "yes")
                for job in [pool.submit(self.probe, one, *unanswered[one]) for one in names]:
                    _waited(job)
                # Each that still did not answer: the hop's words for it, as the others get them.
                unanswered = dict(self.unanswered)
                self.unanswered.clear()
                names = sorted(unanswered)
                break
        for name in names:
            how, vm_settings, joined_before, _ = unanswered[name]
            said, hint = _hop_words(name, hop, how["host"], verdicts.get(name))
            self.unread_through(name, how, vm_settings, joined_before, said, hint)

    def probes(self, count):
        """The pool the probes run in: no more connections at once than Ansible's forks would open."""
        return ThreadPoolExecutor(max_workers=max(1, min(count, int(self.fleet["forks"]))))

    # --- the fleet ------------------------------------------------------------------------------------------------
    def unread(self, unread, hint):
        fleet = model.status_fleet(
            None, settings={**self.settings, "proxmox_hint": hint}, unread=unread, hosts=self.fleet["hosts"]
        )
        self.screen.land("localhost", {"fleet": model.status_clean(fleet)})

    def from_listing(self):
        api = Proxmox(self.fleet)
        try:
            self.read_fleet(api)
        finally:
            # However the read ended, no GET is left to be sent.
            api.pool.shutdown(wait=False, cancel_futures=True)

    def read_fleet(self, api):
        first = [api.later(path) for path in FIRST_WAVE]
        listing = _waited(first[0])
        if listing["status"] != 200 or not isinstance(_data(listing), list):
            status = listing["status"]
            if status in (401, 403):
                self.unread(
                    f"Proxmox refused the token: {listing['msg']}",
                    "check proxmox_api_user, proxmox_api_token_id and vault_proxmox_api_token_secret"
                    " (docs/getting-started/proxmox-access.md)",
                )
            elif untrusted := proxmox_reads.proxmox_untrusted_hint(listing["msg"], self.fleet.get("trust_file", "")):
                self.unread(f"Proxmox's certificate did not verify: {listing['msg']}", untrusted)
            else:
                self.unread(
                    f"Proxmox did not answer: {listing['msg'] or 'no response'}",
                    "check proxmox_api_host in inventory/hosts.yml, and that the Proxmox host is up",
                )
            return
        data = _data(listing)
        tagged = self.reader.members(self, api, data, _waited)
        if tagged is None:
            return
        requested = [one for one in tagged if "name" in one and one["name"] in self.names] if self.names else tagged
        early = model.status_clean(
            self.reader.fleet(
                self,
                model.status_fleet(
                    data,
                    tagged=tagged,
                    requested=requested,
                    aliases=[] if self.fleet["ssh_config"] else None,
                    settings=self.settings,
                    hosts=self.fleet["hosts"],
                    pending=True,
                ),
                [],
            )
        )
        self.screen.land("localhost", {"fleet": early})
        lists = [_waited(one) for one in first[1:]]
        template = proxmox_reads.built_template(data, self.fleet["template_name"]) or None
        storages = model.status_storages(lists, self.fleet["storage"])
        vms = [vm for vm in early["vms"] if vm.get("read")]
        items = _second_wave(requested, template, storages, {vm["name"] for vm in vms})
        # The reader's own reads after every one of the fleet's, so that no VM's probe waits behind them.
        items += self.reader.later(self, requested)
        futures = [self.reader.ready(item) or api.later(item) for item in items]
        pool = self.probes(len(vms))
        try:
            denied, jobs = self.reach_each(vms, requested, items, futures, pool)
            results = [] if denied else [_waited(future) for future in futures]
            denied = denied or _denied(results)
            if denied:
                reach.end_all()
                self.unread(denied, "check the privileges of the token's role (docs/getting-started/proxmox-access.md)")
                return
            whole = self.fill(data, tagged, requested, lists, results, vms)
            for job in jobs:
                _waited(job)
            # The hop's login is made by the first VM that takes the hop, on a thread of the pool, and may fail after
            # fill landed the fleet: landed again here, once the probes are joined, so the hop's line is there for
            # each VM that points at it - never from the login's own thread, which could land before fill's copy.
            if self.settings.get("hop_said") and not whole.get("hop_landed"):
                self.screen.land("localhost", {"fleet": self.fleet_whole(*whole["args"])})
            self.recheck(pool)
        except BaseException:
            # A failure, or a Ctrl-C: no probe is left running, nor started after, nor any ssh of the hop's.
            reach.end_all()
            routes.end_all()
            raise
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    def reach_each(self, vms, requested, items, futures, pool):
        """Each running VM's probe, started the moment its own config and agent answer give its address: (the token's
        refusal for a VM, said, with no probe started after it, or ""; the probes)."""
        if not self.over_ssh:
            return "", []
        # The route, before the first probe starts, off the main thread, since under auto it asks ssh -G (_off_main);
        # the API's reads go on meanwhile. The hop's login waits for a VM that takes the hop.
        self.route = _off_main(pool, self.settle_route)
        # What a VM's line says of the route: the hop it went through, or that it was dialled straight with a
        # Proxmox host to go through. A mark, never the words, which status_clean would cut as a setting's.
        if self.route:
            self.settings["hop_label"] = self.route["label"]
        elif self.config.get("hop"):
            self.settings["direct_route"] = True
        running = [vm for vm in vms if vm.get("status") == "running"]
        # Each running VM's config and agent answer, by the VM's place in running.
        place = {vm["vmid"]: at for at, vm in enumerate(running)}
        pairs: list[dict] = [{} for _ in running]
        for item, future in zip(items, futures, strict=True):
            at = place.get(item["vmid"]) if item["kind"] in ("config", "network") else None
            if at is not None:
                pairs[at][item["kind"]] = future
        waiting = {future: at for at, pair in enumerate(pairs) for future in pair.values()}
        left = [len(pair) for pair in pairs]
        jobs = []
        for future in _as_done(waiting):
            at = waiting[future]
            left[at] -= 1
            if left[at] or len(pairs[at]) < 2:
                continue
            config, network = pairs[at]["config"].result(), pairs[at]["network"].result()
            denied = _denied([config, network])
            if denied:
                return denied, jobs
            jobs.append(pool.submit(self.reach_vm, running[at], requested, config, network))
        return "", jobs

    def reach_vm(self, vm, requested, config, network):
        """One VM's probe, at the address its config and agent answer give."""
        entry = next((one for one in requested if one.get("vmid") == vm["vmid"]), vm)
        row = proxmox_reads.guest_addresses([entry], [config], [network], self.fleet["nic"])[0]
        address = row["address"]
        if not address:
            return
        name = vm["name"]
        how = {**settings.reach_for(self.config, name), "host": address}
        # Its host key held where every run holds it: the project's own file, under the VM's identity from Proxmox,
        # never the address it reports (status_ssh_args, first, still takes no new key).
        how["ssh_args"] = f"{how['ssh_args']} {vm_ssh_args(row['key_alias'], how['known_hosts'])}"
        before = self.reader.joined(self, entry)
        vm_settings = settings.for_vm(self.config, name)
        # A VM this computer has filed no host key for is not dialled: said, with the way on, as the hop's are.
        unkeyed, way = self.reader.unkeyed(self, name, how, row["key_alias"])
        if unkeyed:
            self.unread_through(name, how, vm_settings, before, unkeyed, way)
            return
        hop = {"name": "", "option": "", "via": ""}
        if self.route:
            hop = self.through(name, how, vm_settings, before, address, row)
            if hop is None:
                return
        how["via"] = hop["via"]
        # A jump it is given - the inventory's, ansible.cfg's or the environment's - is its route either way: said in
        # its line, which would otherwise read as the hop's or its own address's.
        how["jump"] = routes.ssh_jump(how.get("given") or "")
        # A shared connection of its identity's own, and its hop's: one by address alone would let a VM that reports
        # another's address ride that one's, on which no host key is checked again.
        how["control_name"] = vm_control_name(row["key_alias"], address, hop["name"])
        if self.route:
            refused, hint = self.held_to_route(how, address, bool(hop["via"]))
            if refused:
                self.unread_through(name, how, vm_settings, before, refused, hint)
                return
        self.probe(name, how, vm_settings, before, f"{name}/{vm['vmid']}")

    def through(self, name, how, vm_settings, before, address, row):
        """A VM's hop, through the Proxmox host: ``{name, option, via}`` - none for a VM given a jump of its own, read
        by it whatever the hop's state - its ssh args made as a run's are, the hop right after status's own options
        and before what the VM is given; None where the VM is not read, said why."""
        none = {"name": "", "option": "", "via": ""}
        if routes.ssh_jump(how.get("given") or ""):
            return none
        if not self.hop_login():
            said = (
                f"it is reached through {self.route['label']}, which could not be used as the hop (said under PROXMOX)"
            )
            self.unread_through(name, how, vm_settings, before, said)
            return None
        # Before the hop, only status's own options and the project's: another proxy there would be the one ssh takes.
        other = routes.ssh_proxy_any(how.get("own") or "")
        if other:
            said = f"status_ssh_args holds {other}, which ssh would take before the hop through {self.route['label']}"
            self.unread_through(
                name, how, vm_settings, before, said, "take it out of ssh_args, or vm_ssh_route: direct"
            )
            return None
        hop = routes.vm_hop(self.route, address)
        # As a run has it: the project's own options, the hop, then what the VM is given.
        known = vm_ssh_args(row["key_alias"], how["known_hosts"])
        how["ssh_args"] = f"{how.get('own') or ''} {known} {hop['option']} {how.get('given') or ''}"
        return hop

    def held_to_route(self, how, address, takes_hop):
        """Why the VM's ssh, given the whole line its probe will be, would not go where the route says, asked of ssh
        itself (ssh -G, no config read), and where to look: ("", "") where it would. Options that cannot even be split
        into words are that VM's line alone, as under direct, never the run's end."""
        try:
            argv = reach.command(how, as_root=False)
        except ValueError as error:
            return (
                f"its ssh options could not be read: {one_line(str(error), 120)}",
                "fix the ssh options the VM is given (ansible_ssh_common_args, ansible_ssh_extra_args)",
            )
        shown = routes.ssh_shown(argv[0], argv[1:-2], argv[-2])
        why = routes.vm_route_refusal(self.route, address, takes_hop, shown)
        if not why:
            return "", ""
        return (
            f"its ssh would not go where the route says: {why}",
            "set a jump of your own where the VM is given it, or vm_ssh_route: direct",
        )

    def fill(self, data, tagged, requested, lists, results, vms):
        """The whole fleet from every read, then each VM's Proxmox part: ``{args, hop_landed}``, what the fleet was
        made from, to land it again, and whether the hop's line was in the settings it was made with."""
        args = (data, tagged, requested, lists, results)
        landed = bool(self.settings.get("hop_said"))
        whole = self.fleet_whole(*args)
        for vm in vms:
            name = vm["name"]
            listed = next((one for one in whole["vms"] if one["name"] == name), vm)
            vmid = listed["vmid"]
            mine = [one for one in results if one["item"].get("kind") == "api" and one["item"]["vmid"] == vmid]
            part = {
                "api": model.status_vm_api(mine, time.time(), listed.get("created")),
                "drift": proxmox_reads.proxmox_vm_drift(
                    listed.get("config") or {},
                    self.reader.wanted(self, settings.for_vm(self.config, name), listed, listed.get("config")),
                ),
            }
            self.screen.land(name, model.status_clean(part), merge=True)
        return {"args": args, "hop_landed": landed}

    def fleet_whole(self, data, tagged, requested, lists, results):
        """The whole fleet from every read, landed: what it landed."""
        # Copies: a probe's thread may still be reading the same answers for its address.
        configs = [{**one, "claude_vm_item": {"vmid": one["item"]["vmid"]}} for one in _picked(results, "config")]
        networks = _picked(results, "network")
        template_config = (_picked(results, "template") or [{}])[0]
        contents = [
            {**one, "item": {"node": one["item"]["node"], "storage": one["item"]["storage"]}}
            for one in _picked(results, "content")
        ]
        contents = self.reader.contents(self, contents, results)
        drift = []
        # A template the user built is compared with nothing: the project never rebuilds it.
        if template_config.get("status") == 200 and not self.settings.get("template_custom"):
            drift = proxmox_reads.proxmox_template_drift(_data(template_config), self.fleet["template_wanted"])
        whole = model.status_clean(
            self.reader.fleet(
                self,
                model.status_fleet(
                    data,
                    tagged=tagged,
                    requested=requested,
                    addressed=proxmox_reads.guest_addresses(requested, configs, networks, self.fleet["nic"]),
                    configs=configs,
                    lists=lists,
                    contents=contents,
                    template_config=template_config,
                    template_drift=drift,
                    aliases=_aliases(self.fleet),
                    settings={**self.settings, "proxmox_hint": ""},
                    unread="",
                    hosts=self.fleet["hosts"],
                ),
                results,
            )
        )
        self.screen.land("localhost", {"fleet": whole})
        return whole

    def from_inventory(self):
        """Discovery off: no Proxmox, the inventory's claude_vms each read as it says it is reached."""
        fleet = model.status_fleet([], settings=self.settings, aliases=_aliases(self.fleet))
        self.screen.land("localhost", {"fleet": model.status_clean(fleet)})
        if not self.over_ssh:
            return
        hosts = self.config["inventory"]
        pool = self.probes(len(hosts))
        try:
            jobs = [
                pool.submit(self.probe, name, one["reach"], one["vm"], False, f"{name}/inventory")
                for name, one in hosts.items()
            ]
            for job in jobs:
                _waited(job)
        except BaseException:
            reach.end_all()
            raise
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    def go(self):
        if self.fleet["settings"]["discovery"]:
            self.from_listing()
        else:
            self.from_inventory()
        if self.kept and self.cache:
            settings.write_json(self.cache / "upgrades.json", {**self.known, **self.kept})


# The program the run's last line names, set by run before it ends for a Ctrl-C (the engine is not safe twice in one
# process): just, or aw for aw status.
_SAID_AS = ["just"]


def _end_for_an_interrupt(screen):
    # Every ssh first, the hop's login on a thread of the pool among them: os._exit runs no thread's finally.
    reach.end_all()
    routes.end_all()
    if screen:
        screen.out.finish()
    print(f"{_SAID_AS[0]} status: interrupted", file=sys.stderr)
    sys.stdout.flush()
    os._exit(3)


def run(opts, loader=settings.load, reader=None, program="just") -> int:
    """One just status: the report on screen and in the log, printed as JSON with ``--json``; its exit code. loader
    gives the run's settings, called as settings.load is, with opts and where Ansible's warnings are said: today's own,
    or aw status's, which hands it the settings it worked out before the run. reader says which VMs are the fleet's
    (Tagged, today's, where none is given), and program is the name the run's own messages give the program."""
    try:
        sections = settings.sections(opts.only)
    except ValueError as error:
        print(f"{program} status: {error}", file=sys.stderr)
        return 3

    def drawn():
        if opts.emit:
            return Emitter(sections)
        return Screen(sections, opts.json, os.environ.get("ANSIBLE_LOG_PATH", ""))

    # A password script may ask at the terminal: the outline waits for its answer rather than being drawn over it.
    screen = None if settings.may_ask(opts) else drawn()
    try:
        config = loader(opts, screen.display.display if screen else None)
        screen = screen or drawn()
        if isinstance(screen, Screen):
            screen.lines = config["fleet"]["settings"].get("lines")
        if opts.emit:
            fleet = config["fleet"]
            keys = ("ssh_config", "ssh_config_file", "ssh_config_legacy")
            screen.emit({"settings": {key: fleet.get(key) for key in keys}})
        if config["run"].get("only") and not opts.only:
            screen.sections = sections = settings.sections(config["run"]["only"])
        reading = Run(opts, screen, config, reader, program)
        reading.go()
        report = reading.reader.report(reading, model.status_report(dict(screen.found), screen.now_said(), time.time()))
        screen.finish(report)
        if opts.json and not opts.emit:
            sys.stdout.write(json.dumps(report, indent=4, sort_keys=True) + "\n")
            sys.stdout.flush()
    except KeyboardInterrupt:
        _SAID_AS[0] = program
        _end_for_an_interrupt(screen)
    except Exception as error:  # noqa: BLE001 - said once, whatever failed, and the run's exit is the one for that
        if screen:
            screen.out.finish()
        print(f"{program} status: the run failed: {one_line(str(error), 2000)}", file=sys.stderr)
        reach.end_all()
        routes.end_all()
        return 3
    return report["exit"] if report["exit"] in (0, 1, 2) else 3
