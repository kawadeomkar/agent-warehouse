"""Proxmox's API and qm read: guest agent output, a refused token, the managed SSH config's blocks, the built template
and the duplicated names in a listing, a VM's tags, the drift of a VM's and a template's settings. Stdlib only,
nothing read or written (docs/architecture/custom-code.md §7.1)."""

from __future__ import annotations

import ipaddress
import re
from collections import Counter
from urllib.parse import unquote

from .checks import _alias_refusal, _ipv4, guest_ipv4
from .vpn.model import MARK

MAC_RE = re.compile(r"([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}")

# proxmoxer words every API error "<status> <reason>: <detail>", and a
# community.proxmox module puts its own "...: " in front. 401 and 403 are only
# looked for in that status position: Proxmox names the VM in the detail, so
# "500 Internal Server Error: VM 4013 is not running" must not read as a 401.
DENIED_RE = re.compile(r"(?:^|: )40[13] |[Pp]ermission|[Aa]uthentication")


_RAISE = object()


def net_mac(net_config, default=_RAISE):
    """Return the MAC address from a Proxmox ``netN`` config string.

    Proxmox stores it as ``virtio=BC:24:11:0E:72:04,bridge=vmbr0``.

    Raises when no MAC can be read, because a caller that is about to act on
    one VM wants to know. Pass ``default`` to get a fallback instead: a caller
    sweeping the whole fleet should skip an odd VM (one whose NIC was removed
    in the UI, or that has ``net1`` but no ``net0``) rather than abort the run
    for every other VM as well.
    """
    if isinstance(net_config, str):
        match = MAC_RE.search(net_config)
        if match:
            return match.group(0).lower()
        problem = f"no MAC address found in {net_config!r}"
    else:
        problem = f"net_mac expects a string, got {type(net_config).__name__}"

    if default is _RAISE:
        raise ValueError(problem)
    return default


_TAILNET_V4 = ipaddress.IPv4Network("100.64.0.0/10")


def tailnet_ipv4(addresses):
    """Return a node's tailnet IPv4 address from `tailscale status --json`'s Self.TailscaleIPs, or "".

    Tailscale gives each node one IPv4 address in 100.64.0.0/10 (beside an IPv6
    one). The list is what tailscaled on the VM reports, so the VM chooses it, as
    with guest_ipv4: only a string ``ipaddress`` parses as one IPv4 address in
    that range is taken, and anything else is no address.
    """
    for text in addresses if isinstance(addresses, list) else []:
        ip = _ipv4(text)
        if ip is not None and ip in _TAILNET_V4:
            return str(ip)
    return ""


# A mark: a fact about a VM that the VM cannot change, kept as a tag beside the ownership tag, <tag>-<mark>
# (docs/architecture/custom-code.md §7.11): tailscale, or wg-<the VM's WireGuard address>, with -reached after it
# once the VM has reached its hub there.
MARK_RE = MARK  # the core's own pattern (lib/claude_vm/vpn/model.py), so the two never disagree


def _tag_list(tags):
    """A VM's tags as proxmox_tags reads them, from its string or a list of them, each lowercased, as the ownership
    tag is matched."""
    return [tag.lower() for tag in proxmox_tags(tags if isinstance(tags, str) else ";".join(map(str, tags or ())))]


def proxmox_marks(tags, tag):
    """The marks a VM's tags hold, the ownership tag's prefix taken off, in the order Proxmox gave them."""
    prefix = f"{str(tag).lower()}-"
    return [t[len(prefix) :] for t in _tag_list(tags) if t.startswith(prefix) and MARK_RE.fullmatch(t[len(prefix) :])]


def proxmox_wireguard_taken(resources, tag, but=()):
    """Every WireGuard address a VM of the project holds in Proxmox, from the cluster's listing, but the VMs whose VM
    IDs are given: the addresses no other VM may be given."""
    taken, but = [], {int(vmid) for vmid in but}
    for vm in resources or ():
        if not isinstance(vm, dict) or vm.get("vmid") in but:
            continue
        if str(tag).lower() in _tag_list(vm.get("tags", "")):
            taken += [
                mark[3:].removesuffix("-reached")
                for mark in proxmox_marks(vm.get("tags", ""), tag)
                if mark.startswith("wg-")
            ]
    return taken


def guest_addresses(vms, configs, networks, nic="net0"):
    """Pair each VM of a cluster listing with the address its agent reports.

    ``vms`` is a list of ``/cluster/resources`` entries; ``configs`` and
    ``networks`` are the registered results of a ``uri`` loop over them, one
    per VM in the same order, reading ``/config`` and
    ``/agent/network-get-interfaces``. Returns the entries with an ``address``
    key added: the IPv4 the agent reports on the interface whose MAC is the
    config's ``nic``, so a bridge the guest grew later (docker0) is never
    returned. ``nic`` is the one setting that decides this, which is why the
    caller passes ``proxmox_nic``.

    Anything that leaves one VM without an address - its request failed or was
    skipped, the VM is stopped, the agent has not answered, the VM has no such
    NIC, an answer of another shape - gives that VM "", never an error: the
    caller sweeps a whole fleet and skips that VM rather than abort for every
    other one, and never guesses an address without the MAC.
    """
    if not isinstance(vms, list) or not isinstance(configs, list) or not isinstance(networks, list):
        raise ValueError("guest_addresses expects three lists")
    if not len(vms) == len(configs) == len(networks):
        raise ValueError(
            f"guest_addresses got {len(vms)} VMs, {len(configs)} configs and {len(networks)} network replies"
        )

    def body(result):
        data = result.get("json") if isinstance(result, dict) else None
        return data.get("data") if isinstance(data, dict) else None

    rows = []
    for vm, config, network in zip(vms, configs, networks, strict=True):
        settings, interfaces = body(config), body(network)
        mac = net_mac(settings.get(nic), "") if isinstance(settings, dict) else ""
        reported = interfaces.get("result") if isinstance(interfaces, dict) else None
        address = (guest_ipv4(reported, mac) or "") if mac and isinstance(reported, list) else ""
        rows.append({**vm, "address": address})
    return rows


def proxmox_access_denied(msg):
    """Whether a community.proxmox error means the API refused the token.

    That is worth failing on straight away: it applies to every VM, and waiting
    or skipping would only hide it. Anything else - a stopped VM, an agent that
    is not up yet, a node that is down - is about one VM and is not.
    """
    return isinstance(msg, str) and DENIED_RE.search(msg) is not None


# The managed SSH config on the controller (playbooks/tasks/ssh_config.yml):
# one blockinfile block per VM, keyed by the VM's name, marked agent-warehouse
# from 2.0 and claude-on-proxmox through 1.x, which a file not moved yet has.
BLOCK_RE = re.compile(
    r"^# BEGIN (?P<by>agent-warehouse|claude-on-proxmox): (?P<name>\S+)$\n(?P<body>.*?)^# END (?P=by): (?P=name)$",
    re.M | re.S,
)


def ssh_config_blocks(content):
    """The alias blocks in a managed ssh config, as ``{name}``, in file order."""
    if content is None:
        content = ""
    if not isinstance(content, str):
        raise ValueError(f"ssh_config_blocks expects the file's text, got {type(content).__name__}")
    return [{"name": match.group("name")} for match in BLOCK_RE.finditer(content)]


def ssh_config_prune(blocks, resources, tag_pattern, hosts=None):
    """Which alias blocks a fleet-wide refresh should remove, and which it must not.

    ``blocks`` is what ``ssh_config_blocks`` read; ``resources`` the cluster's
    ``/cluster/resources`` listing; ``tag_pattern`` the project's whole-tag
    regex; ``hosts`` the names this machine reaches the inventory's hosts by.
    Pruning only ever removes alias blocks, never VMs, and a block was
    only ever written for a tagged VM, so the question is just whether that VM
    is still there. Returns a dict:

    - ``refused``: names no alias may have, whatever VM carries them: one ssh
      reads as an address, or one of ``hosts`` (``ssh_alias_refusal``). An
      earlier version wrote them, and the refresh no longer does, so a block
      left would take this machine's connections to that host for good.
      Removed even while the listing is incomplete. A name with a dot is not
      among them: a named run writes one.

    - ``prune``: names with no VM of that name left anywhere in the cluster.
    - ``untagged``: names a VM still carries but without the tag. Left alone
      and reported: an untagged VM is not this project's to reason about, as
      provisioning and destroy already refuse it.
    - ``nameless``: VMIDs of tagged VMs the listing shows without a name,
      which is what a node that is down looks like. Any block could be one of
      those VMs, so when this is non-empty nothing is pruned: ``prune`` is
      empty and the names it would have held are in ``held`` instead.
    """
    if not isinstance(blocks, list) or not isinstance(resources, list):
        raise ValueError("ssh_config_prune expects the blocks and the cluster listing as lists")
    qemu = [vm for vm in resources if isinstance(vm, dict) and vm.get("type") == "qemu"]
    tagged = [vm for vm in qemu if re.search(tag_pattern, str(vm.get("tags", "")))]
    tagged_names = {vm["name"] for vm in tagged if vm.get("name")}
    all_names = {vm["name"] for vm in qemu if vm.get("name")}
    nameless = [str(vm.get("vmid", "?")) for vm in tagged if not vm.get("name")]

    result = {"prune": [], "held": [], "untagged": [], "nameless": nameless, "refused": []}
    for block in blocks:
        name = block.get("name", "")
        why, unnamed = _alias_refusal(name, hosts, None)
        if why and not unnamed:
            result["refused"].append(name)
            continue
        if name in tagged_names:
            continue
        if name in all_names:
            result["untagged"].append(name)
        elif nameless:
            result["held"].append(name)
        else:
            result["prune"].append(name)
    return result


def vms_not_named(vms, names):
    """The entries of ``vms`` (the cluster listing's) whose name is none of ``names``, in their order.

    An entry without a name - a VM on a node that is down - is kept: it can never
    be taken for a host by its name, and the listing still shows it.
    """
    if isinstance(vms, (str, dict)) or not hasattr(vms, "__iter__"):
        raise ValueError(f"vms_not_named expects a list, got {type(vms).__name__}")
    taken = set(names or [])
    return [vm for vm in vms if not (isinstance(vm, dict) and "name" in vm and vm["name"] in taken)]


def built_template(vms, name):
    """The built template named ``name`` among ``vms`` (the cluster listing's entries, or proxmox_vm_info's), or {}.

    A qemu VM of that name whose ``template`` is set, as Proxmox lists a template (1): a VM that merely shares the
    template's name (0, or no key at all) is no template, nor is a container's. An entry that names no type counts
    as qemu. Of two, the first.
    """
    if isinstance(vms, (str, dict)) or not hasattr(vms, "__iter__"):
        raise ValueError(f"built_template expects a list, got {type(vms).__name__}")
    for vm in vms:
        if isinstance(vm, dict) and vm.get("type", "qemu") == "qemu" and vm.get("name") == name and vm.get("template"):
            return vm
    return {}


def proxmox_tags(tags):
    """A VM's tags, as a list: split on ";" or ",", each trimmed, an empty one left out, the case as given.

    Proxmox joins tags with ";" on the wire, lowercased; proxmox_kvm sends them joined with ",", and some versions
    echo that back. Whole tags, so that a caller compares a tag whole: claude-on-proxmox is never
    claude-on-proxmox-staging.
    """
    return [tag.strip() for tag in re.split(r"[;,]", str(tags or "")) if tag.strip()]


def duplicate_names(vms):
    """The names more than one entry of ``vms`` (the cluster listing's) carries, sorted as Jinja's sort sorts them,
    regardless of case; an entry without a name is none of them.

    Proxmox lets two VMs share a name, and what finds a VM by its name - add_host, an alias, destroy's release -
    would act on one of them only, unpredictably.
    """
    if isinstance(vms, (str, dict)) or not hasattr(vms, "__iter__"):
        raise ValueError(f"duplicate_names expects a list, got {type(vms).__name__}")
    counts = Counter(vm["name"] for vm in vms if isinstance(vm, dict) and vm.get("name"))
    return sorted((name for name, count in counts.items() if count > 1), key=lambda name: str(name).lower())


_SIZE_RE = re.compile(r"^\s*([0-9.]+)\s*([KMGT])?B?\s*$", re.I)


def _size_bytes(text):
    """A Proxmox size ("32G", "1T", "3584M") in bytes; None when it is none."""
    match = _SIZE_RE.match(str(text or ""))
    if not match:
        return None
    unit = (match.group(2) or "G").upper()
    return int(float(match.group(1)) * 1024 ** ("KMGT".index(unit) + 1))


def scsi0_storage(scsi0):
    """The storage a disk's property string names (``local-lvm:vm-9001-disk-0,size=32G``): its volume, the first item,
    is "<storage>:<name>". "" for a disk that names none."""
    volume = str(scsi0 or "").split(",", 1)[0]
    return volume.split(":", 1)[0] if ":" in volume else ""


def _disk_size(scsi0):
    match = re.search(r"(?:^|,)size=([0-9.]+[KMGT])", str(scsi0 or ""))
    return _size_bytes(match.group(1)) if match else None


def proxmox_vm_drift(config, wanted):
    """How a VM's settings differ from what roles/proxmox_vm would send it: ``{hardware: [...], cloud_init: [...]}``.

    ``config`` is the VM's config as /nodes/<node>/qemu/<vmid>/config gives it; ``wanted`` what the role sends, from
    its own variables (tasks/settings_drift.yml). Deploy applies these only to a new VM or under
    proxmox_vm_force_update, so an edited vm_cores never reaches an existing VM by itself: this is how `just status`
    sees it. Hardware differences are ``{setting, wanted, has}``, the disk's size in bytes and only when it is
    smaller than asked, since the role grows a disk and never shrinks one; the cloud-init settings that differ are
    named. A config not read gives no difference at all.
    """
    if not isinstance(config, dict) or not config or not isinstance(wanted, dict):
        return {"hardware": [], "cloud_init": []}
    hardware = []

    def number(value):
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    for setting in ("cores", "memory", "balloon"):
        has = number(config.get(setting))
        if setting == "balloon" and has is None:
            has = number(config.get("memory"))
        want = number(wanted.get(setting))
        if want is not None and has is not None and has != want:
            hardware.append({"setting": setting, "wanted": want, "has": has})
    onboot = str(config.get("onboot", "0")) in ("1", "true", "True")
    if "onboot" in wanted and bool(wanted["onboot"]) != onboot:
        hardware.append({"setting": "onboot", "wanted": bool(wanted["onboot"]), "has": onboot})
    has_disk, want_disk = _disk_size(config.get("scsi0")), _size_bytes(wanted.get("disk"))
    if has_disk and want_disk and has_disk < want_disk:
        hardware.append({"setting": "disk", "wanted": want_disk, "has": has_disk})
    cloud_init = []
    if "ciuser" in wanted and str(config.get("ciuser", "")) != str(wanted.get("ciuser") or ""):
        cloud_init.append("user")
    has_keys = {k.strip() for k in unquote(str(config.get("sshkeys", ""))).splitlines() if k.strip()}
    want_keys = {str(k).strip() for k in wanted.get("sshkeys") or [] if str(k).strip()}
    if "sshkeys" in wanted and has_keys != want_keys:
        cloud_init.append("SSH keys")
    if "ipconfig0" in wanted and str(config.get("ipconfig0", "")) != str(wanted.get("ipconfig0") or ""):
        cloud_init.append("network")
    has_dns = (str(config.get("nameserver", "")).split(), str(config.get("searchdomain", "")))
    want_dns = (list(wanted.get("nameserver") or []), str(wanted.get("searchdomain") or ""))
    if ("nameserver" in wanted or "searchdomain" in wanted) and has_dns != want_dns:
        cloud_init.append("DNS")
    return {"hardware": hardware, "cloud_init": cloud_init}


# What roles/proxmox_template records in a template's notes when it builds one: the version of its build steps and
# what went into the disk, none of which the template's config shows. One line, so that `qm config` prints it as one,
# found by this marker wherever it is among the notes a person may have added. A full clone copies its template's
# notes, so each VM keeps the line too, which reads true of it as well: what it was built from.
TEMPLATE_RECORD = "Built by claude-on-proxmox:"
_TEMPLATE_RECORD_RE = re.compile(re.escape(TEMPLATE_RECORD) + r"\s*(.*)$")
# What the record holds, in its order: the build steps' version, the image's URL, the packages put into it (empty
# when none were), and the image's SHA-256, kept to say which image it was but never compared.
TEMPLATE_RECORD_KEYS = ("build", "image", "packages", "sha256")
# What a template is compared on, in the order its differences are said: the settings every clone inherits from it,
# then the record. Not cores or memory, which each VM sets for itself.
TEMPLATE_FIELDS = (
    "cpu",
    "ostype",
    "bridge",
    "storage",
    "scsihw",
    "agent",
    "serial0",
    "vga",
    "build",
    "image",
    "packages",
)


def qm_config(text):
    """`qm config`'s output as the dict /nodes/<node>/qemu/<vmid>/config gives: each "key: value" line, and a line
    that is none added to the key before it. qm prints the description encoded, on one line, as the VM's config file
    keeps it - a colon, a newline and any byte outside printable ASCII as %XX (PVE::Tools::encode_text; "Built by
    claude-on-proxmox%3A build=1" on Proxmox VE 9.2) - which the API gives decoded, and so does this."""
    config, key = {}, None
    for line in str(text or "").splitlines():
        match = re.match(r"^([a-z][a-z0-9_-]*): ?(.*)$", line)
        if match:
            key = match.group(1)
            config[key] = match.group(2)
        elif key is not None:
            config[key] = f"{config[key]}\n{line}"
    if "description" in config:
        config["description"] = unquote(config["description"])
    return config


def _option(value, key):
    """One option of a Proxmox property string (``virtio=BC:..,bridge=vmbr0``): ``key``'s value, or the first item
    when it names none, which is the default key's (``cpu: host`` is ``cputype=host``, ``agent: 1`` ``enabled=1``)."""
    items = [item for item in str(value or "").split(",") if item]
    for item in items:
        name, _, rest = item.partition("=")
        if name == key and rest:
            return rest
    return items[0] if items and "=" not in items[0] else ""


def proxmox_template_record(values):
    """The one line a template's notes keep: ``values`` has TEMPLATE_RECORD_KEYS."""
    return TEMPLATE_RECORD + " " + "; ".join(f"{key}={values.get(key, '')}" for key in TEMPLATE_RECORD_KEYS)


def _template_record(config):
    for line in str(config.get("description") or "").splitlines():
        match = _TEMPLATE_RECORD_RE.search(line)
        if match:
            return dict(part.split("=", 1) for part in match.group(1).split("; ") if "=" in part)
    return None


def proxmox_template_drift(config, wanted):
    """How a template differs from what roles/proxmox_template would build: a line for each difference, "<field>
    <has> → <wanted>" in TEMPLATE_FIELDS' order; one line alone when its notes keep no record of how it was built;
    [] when it is what the role would build.

    ``config`` is the template's config, as /nodes/<node>/qemu/<vmid>/config gives it or as ``qm config`` prints it
    (through qm_config); ``wanted`` has TEMPLATE_FIELDS, as the role's own variables make them (vars/main.yml). The
    qm settings are read from the config, so a change made in Proxmox's UI is seen; what went into the disk is read
    from the record the build left in the notes, since Proxmox cannot show it.
    """
    if not isinstance(config, dict) or not isinstance(wanted, dict):
        raise ValueError("proxmox_template_drift expects the template's config and what is wanted, as dicts")
    record = _template_record(config)
    if record is None:
        return ["no record of how it was built, so it was built by an earlier version of this project"]
    has = {
        "cpu": _option(config.get("cpu"), "cputype"),
        "ostype": str(config.get("ostype") or ""),
        "bridge": _option(config.get("net0"), "bridge"),
        "storage": scsi0_storage(config.get("scsi0")),
        "scsihw": str(config.get("scsihw") or ""),
        "agent": _option(config.get("agent"), "enabled"),
        "serial0": str(config.get("serial0") or ""),
        "vga": _option(config.get("vga"), "type"),
        **{key: record.get(key, "") for key in ("build", "image", "packages")},
    }
    return [
        f"{field} {has[field] or 'unset'} → {wanted.get(field) or 'unset'}"
        for field in TEMPLATE_FIELDS
        if str(has[field]) != str(wanted.get(field) or "")
    ]
