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
"""

from __future__ import annotations

import getpass
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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from .. import proxmox as proxmox_reads
from ..text import one_line
from ..tui import live_region as live
from ..tui import status as status_view
from . import found as vm_found
from . import model, reach, settings

GET_WORKERS = 8
# What is read before anything else, the listing first: the version, and the storages' and the nodes' figures.
FIRST_WAVE = ("cluster/resources?type=vm", "version", "cluster/resources?type=storage", "cluster/resources?type=node")


class Display:
    """What the live region asks of Ansible's Display: a lock, and display(), with the run's log as Ansible keeps it."""

    def __init__(self, log_path: str):
        self._lock = threading.RLock()
        self._log = None
        if log_path:
            self._log = logging.getLogger("status")
            handler = logging.FileHandler(log_path, encoding="utf-8")
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
        text = status_view.status_text(report, self.color, glyph=glyph, cols=cols - 1)
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

    def land(self, host, part):
        now = time.monotonic() - self.started
        with self.display._lock:
            self.found[host] = part
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
        plain = status_view.status_text(report, False, reading=reading)
        if self.quiet:
            # Under --json the screen gets the JSON alone; the run log still gets the report.
            self.display.display(plain, log_only=True)
        else:
            coloured = status_view.status_text(report, True, reading=reading) if self.color else plain
            self.out.print([(coloured, plain)])


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
            one["status"], one["msg"], body = error.code, f"HTTP Error {error.code}: {error.reason}", error.read()
        except ValueError:
            # http.client's words for a header it refuses quote the header, the token in it.
            one["msg"] = "the request could not be made"
        except OSError as error:
            one["msg"] = f"Request failed: {error}"
        try:
            one["json"] = json.loads(body) if body else None
        except (RecursionError, ValueError):
            one["json"] = None
        return one

    def later(self, item):
        return self.pool.submit(self.get, item)


def _data(result):
    body = result.get("json") if isinstance(result, dict) else None
    return body.get("data") if isinstance(body, dict) else None


def _second_wave(requested, template, storages):
    """Every GET that follows the listing: each VM's config, address, state and history, the template's config and each
    storage's disks. A VM's reads each say which VM's they are, and which read."""
    items = []
    for vm in requested:
        status = vm.get("status", "unknown")
        path = f"nodes/{vm.get('node')}/qemu/{vm.get('vmid')}"
        vmid = vm.get("vmid")
        items.append({"kind": "config", "vmid": vmid, "path": f"{path}/config", "skip": status == "unknown"})
        items.append(
            {
                "kind": "network",
                "vmid": vmid,
                "path": f"{path}/agent/network-get-interfaces",
                "skip": status != "running",
            }
        )
        reads = [("current", "status/current"), ("rrddata", "rrddata?timeframe=day&cf=AVERAGE"), ("pending", "pending")]
        if status == "running":
            reads += [("osinfo", "agent/get-osinfo"), ("fsinfo", "agent/get-fsinfo")]
        items += [{"kind": "api", "vmid": vmid, "name": name, "path": f"{path}/{tail}"} for name, tail in reads]
    if template:
        path = f"nodes/{template.get('node')}/qemu/{template.get('vmid')}/config"
        items.append({"kind": "template", "path": path})
    for storage in storages:
        path = f"nodes/{storage['node']}/storage/{storage['storage']}/content?content=images"
        items.append({"kind": "content", "path": path, "node": storage["node"], "storage": storage["storage"]})
    return items


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

    def __init__(self, opts, screen, config):
        self.opts = opts
        self.screen = screen
        self.config = config
        self.fleet = config["fleet"]
        self.names = config["run"]["names"]
        self.sections = list(screen.sections)
        self.settings = {**self.fleet["settings"], "names": self.names, "sections": self.sections}
        self.over_ssh = model.status_needs_ssh(self.sections)
        self.cache = Path(opts.cache) if opts.cache else None
        self.known = (settings.read_json(self.cache / "upgrades.json") or {}) if self.cache else {}
        self.kept: dict = {}
        self.lock = threading.Lock()
        self.password_lock = threading.Lock()
        self.vault_password = None

    # --- a read of one VM -----------------------------------------------------------------------------------------
    def password(self):
        """The VM user's password, for the sudo that asks for it: read once in a run, and only if one does."""
        with self.password_lock:
            if self.vault_password is None:
                self.vault_password = self.config["password"]()
            return self.vault_password

    def asked(self, vm_settings, key):
        wanted = vm_found.parts_wanted(self.sections)
        asked = {k: v for k, v in vm_settings["asked"].items() if k in wanted or k in ("user", "limit")}
        if "system" in asked:
            asked["system"] = {**asked["system"], "known": self.known.get(key) or {}}
        return asked

    def probe(self, name, how, vm_settings, joined_before, key, base):
        """A VM's reads: what is drawn as soon as only apt's simulation is left, then the whole of them."""
        host = how["host"]
        sections = self.sections

        def land(read):
            part = vm_found.ssh_found(read, vm_settings, name, host, sections, joined_before)
            self.screen.land(name, model.status_clean({**base(), "ssh": part}))

        try:
            read = reach.read(how, self.asked(vm_settings, key), land, self.password)
        except Exception as error:  # noqa: BLE001 - one VM's failure is that VM's, said as not read
            read = {"failed": True, "msg": str(error) or type(error).__name__}
        land(read)
        system = (read.get("user") or {}).get("system") if isinstance(read.get("user"), dict) else None
        if isinstance(system, dict) and isinstance(system.get("upgrades"), int) and system.get("upgrades_key"):
            with self.lock:
                self.kept[key] = {
                    "key": system["upgrades_key"],
                    "upgrades": system["upgrades"],
                    "security": system.get("security"),
                }

    # --- the fleet ------------------------------------------------------------------------------------------------
    def unread(self, unread, hint):
        fleet = model.status_fleet(
            None, settings={**self.settings, "proxmox_hint": hint}, unread=unread, hosts=self.fleet["hosts"]
        )
        self.screen.land("localhost", {"fleet": model.status_clean(fleet)})

    def from_listing(self):
        api = Proxmox(self.fleet)
        first = [api.later(path) for path in FIRST_WAVE]
        listing = first[0].result()
        if listing["status"] != 200 or not isinstance(_data(listing), list):
            status = listing["status"]
            if status in (401, 403):
                self.unread(
                    f"Proxmox refused the token: {listing['msg']}",
                    "check proxmox_api_user, proxmox_api_token_id and vault_proxmox_api_token_secret"
                    " (docs/getting-started/proxmox-access.md)",
                )
            else:
                self.unread(
                    f"Proxmox did not answer: {listing['msg'] or 'no response'}",
                    "check proxmox_api_host in inventory/hosts.yml, and that the Proxmox host is up",
                )
            return
        data = _data(listing)
        pattern, joined = self.fleet["tag_pattern"], self.fleet["tailscale_tag_pattern"]
        tagged = [
            one
            for one in data
            if isinstance(one, dict) and one.get("type") == "qemu" and "tags" in one and re.search(pattern, one["tags"])
        ]
        requested = [one for one in tagged if "name" in one and one["name"] in self.names] if self.names else tagged
        early = model.status_clean(
            model.status_fleet(
                data,
                tagged=tagged,
                requested=requested,
                aliases=[] if self.fleet["ssh_config"] else None,
                settings=self.settings,
                hosts=self.fleet["hosts"],
                pending=True,
            )
        )
        self.screen.land("localhost", {"fleet": early})
        lists = [one.result() for one in first[1:]]
        template = proxmox_reads.built_template(data, self.fleet["template_name"]) or None
        storages = model.status_storages(lists, self.fleet["storage"])
        items = _second_wave(requested, template, storages)
        futures = [api.later(item) for item in items]
        by_vm: dict = {}
        for item, future in zip(items, futures, strict=True):
            if item["kind"] in ("config", "network"):
                by_vm.setdefault(item["vmid"], {})[item["kind"]] = future
        fleet_ready = threading.Event()
        full: dict = {}
        parts = {vm["name"]: {"ready": threading.Event()} for vm in early["vms"] if vm.get("read")}
        pool = ThreadPoolExecutor(max_workers=max(1, 2 * len(parts)))

        def reach_vm(vm):
            """The VM's probe, started the moment its own config and agent answer give its address."""
            pending = by_vm.get(vm["vmid"], {})
            if not self.over_ssh or vm.get("status") != "running" or len(pending) < 2:
                return
            entry = next((one for one in requested if one.get("vmid") == vm["vmid"]), vm)
            address = proxmox_reads.guest_addresses(
                [entry], [pending["config"].result()], [pending["network"].result()], self.fleet["nic"]
            )[0]["address"]
            if not address:
                return
            name = vm["name"]
            vm_settings = settings.for_vm(self.config, name)
            how = {**self.config["reach"], "host": address}
            tags = str(entry.get("tags") or "")
            before = bool(re.search(joined, tags))

            def base():
                parts[name]["ready"].wait()
                return parts[name]["found"]

            self.probe(name, how, vm_settings, before, f"{name}/{vm['vmid']}", base)

        def proxmox_part(vm):
            """The VM's Proxmox part, once the rest of the fleet has been worked out."""
            name = vm["name"]
            fleet_ready.wait()
            if "fleet" not in full:
                parts[name]["found"] = {}
                parts[name]["ready"].set()
                return
            whole = next((one for one in full["fleet"]["vms"] if one["name"] == name), vm)
            vmid = whole["vmid"]
            results = [
                one for one in full["results"] if one["item"].get("kind") == "api" and one["item"]["vmid"] == vmid
            ]
            part = model.status_clean(
                {
                    "api": model.status_vm_api(results, time.time(), whole.get("created")),
                    "drift": proxmox_reads.proxmox_vm_drift(
                        whole.get("config") or {}, _wanted(settings.for_vm(self.config, name), whole.get("config"))
                    ),
                }
            )
            parts[name]["found"] = part
            parts[name]["ready"].set()
            self.screen.land(name, {**self.screen.found.get(name, {}), **part})

        vms = [vm for vm in early["vms"] if vm.get("read")]
        probes = [pool.submit(reach_vm, vm) for vm in vms]
        fills = [pool.submit(proxmox_part, vm) for vm in vms]
        results = [future.result() for future in futures]
        denied = _denied(results)
        if denied:
            self.unread(denied, "check proxmox_api_host in inventory/hosts.yml, and that the Proxmox host is up")
            fleet_ready.set()
            reach.end_all()
            return
        full["results"] = results
        configs, networks = _picked(results, "config"), _picked(results, "network")
        for one in configs:
            one["claude_vm_item"] = {"vmid": one["item"]["vmid"]}
        template_config = (_picked(results, "template") or [{}])[0]
        contents = [
            {**one, "item": {"node": one["item"]["node"], "storage": one["item"]["storage"]}}
            for one in _picked(results, "content")
        ]
        drift = []
        if template_config.get("status") == 200:
            drift = proxmox_reads.proxmox_template_drift(_data(template_config), self.fleet["template_wanted"])
        whole = model.status_clean(
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
            )
        )
        full["fleet"] = whole
        self.screen.land("localhost", {"fleet": whole})
        fleet_ready.set()
        for job in [*fills, *probes]:
            job.result()

    def from_inventory(self):
        """Discovery off: no Proxmox, the inventory's claude_vms each read as it says it is reached."""
        fleet = model.status_fleet([], settings=self.settings, aliases=_aliases(self.fleet))
        self.screen.land("localhost", {"fleet": model.status_clean(fleet)})
        if not self.over_ssh:
            return
        hosts = self.config["inventory"]
        with ThreadPoolExecutor(max_workers=max(1, len(hosts))) as pool:
            jobs = [
                pool.submit(self.probe, name, one["reach"], one["vm"], False, f"{name}/inventory", lambda: {})
                for name, one in hosts.items()
            ]
            for job in jobs:
                job.result()

    def go(self):
        if self.fleet["settings"]["discovery"]:
            self.from_listing()
        else:
            self.from_inventory()
        if self.kept and self.cache:
            settings.write_json(self.cache / "upgrades.json", {**self.known, **self.kept})


def _end_for_an_interrupt(screen):
    reach.end_all()
    screen.out.finish()
    print("just status: interrupted", file=sys.stderr)
    sys.stdout.flush()
    os._exit(3)


def run(opts) -> int:
    """One just status: the report on screen and in the log, printed as JSON with ``--json``; its exit code."""
    try:
        sections = settings.sections(opts.only)
    except ValueError as error:
        print(f"just status: {error}", file=sys.stderr)
        return 3
    screen = Screen(sections, opts.json, os.environ.get("ANSIBLE_LOG_PATH", ""))
    try:
        config = settings.load(opts, screen.display.display)
        if config["run"].get("only") and not opts.only:
            screen.sections = sections = settings.sections(config["run"]["only"])
        Run(opts, screen, config).go()
        report = model.status_report(dict(screen.found), screen.now_said(), time.time())
    except KeyboardInterrupt:
        _end_for_an_interrupt(screen)
    except Exception as error:  # noqa: BLE001 - said once, whatever failed, and the run's exit is the one for that
        screen.out.finish()
        print(f"just status: the run failed: {one_line(str(error), 2000)}", file=sys.stderr)
        reach.end_all()
        return 3
    screen.finish(report)
    if opts.json:
        sys.stdout.write(json.dumps(report, indent=4, sort_keys=True) + "\n")
        sys.stdout.flush()
    return report["exit"] if report["exit"] in (0, 1, 2) else 3
