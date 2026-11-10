# Tailscale

You can reach a VM over [Tailscale](https://tailscale.com): it puts each VM on your tailnet - the private
network your other devices are on - so `ssh`, your editor (VS Code's Remote - SSH, or Zed) and a dev
server on any port reach the VM from anywhere, not only from the LAN Proxmox sits on. A VPN you already
run that routes your LAN does that too, with nothing here ([Reaching your VMs from away](remote-access.md)).
Tailscale is optional: `just init` asks whether to use it and writes your answer into `local.yml` as `vpn:
tailscale` or `vpn: none`, which every VM deployed from then on follows, and which you can change there any
time; after a `y` it asks for an auth key for the vault (below). Until it is answered, no VM gets
Tailscale: `none` is the default, and every command but `just init` warns of it. `just init --tailscale on`
or `off` answers it without a terminal. Off, no VM gets anything of Tailscale's, and a VM it is already on
keeps it, unmanaged ([Turning it off](#turning-it-off)).

## Joining a VM to your tailnet

There are two ways in. `tailscale_auth_method` picks: `auto` (the default) is the key when the vault holds
one and the web login otherwise, `key` is the key only (an empty one fails by name, and no VM is ever sent
to a browser), and `web` is the web login only, with a key in the vault left unused.

- **An auth key** (`vault_tailscale_auth_key`, which `just init` asks for after `tailscale`). Configuration - `just deploy`, or `just deploy --only configure` - joins each new
  VM by itself, with nothing to click. Make the key in Tailscale's admin console (Settings, Keys):
  **reusable** for a fleet, or one-off for one VM; **pre-approved** if your tailnet has device approval
  on; and **tagged** (`tag:claude-vm`, below). Never ephemeral: Tailscale removes an ephemeral node within
  the hour of it going offline, and a VM left stopped would be. The key is only used for a VM's first
  join - a VM that has joined keeps working after the key expires - and it reaches the VM on standard
  input, never on a command line or in a file. Once a VM's Tailscale has had a login - by the key, the web
  login or by hand - or a sign-in of the project's has started on it, its tag in Proxmox
  (`agent-warehouse-tailscale`) records it, written by the first `just deploy` or `just login` to see it,
  and the key is never sent to that VM again, even if it is logged out: only something on the VM logs it
  out, and the key would then let it join devices of its own. Sign such a VM in by the web login, or remove
  the tag in Proxmox to let the key join it once more. A login no run has seen - made by hand on the VM and
  logged out before the next run - leaves no record. An OAuth client secret (`tskey-client-...`) works too, with
  `tailscale_advertise_tags` set and `?ephemeral=false` on the secret.
- **The web login**, with no secret at all. `just login alpha --only tailscale` - and `just deploy`'s
  step, which starts by itself (`tailscale_login_auto_start`; `false` asks first, and a run with no
  terminal then leaves the VM, saying so) - opens Tailscale's
  sign-in page here and waits up to five minutes (`tailscale_login_timeout`) for you to sign in and
  approve the VM. A VM you approve later joins by itself: the page stays valid for 7 days. `just deploy`
  leaves out a VM whose Claude sign-in nobody answered, and says so, with
  `just login alpha --only tailscale` to sign it in later.

Each run says each VM's name and address on the tailnet, and the tailnet itself - so a VM that someone
else's approval put in their tailnet shows:

```
alpha: on Tailscale in the tailnet you@example.com as alpha.tail1234.ts.net (100.101.102.103).
```

## Connecting

From any device on the tailnet: `ssh dev@alpha.tail1234.ts.net` (or its `100.x` address),
your editor to the same host, `http://alpha.tail1234.ts.net:3000` for a dev server. It is the
same sshd and the same keys as on the LAN (`vm_ssh_public_keys`). With `claude_ssh_host_domain:
tail1234.ts.net`, the aliases this project writes and `just code` use `<VM name>.tail1234.ts.net` in place
of the LAN address; the machine running Ansible must be on the tailnet then, and Ansible itself keeps
using the LAN. That is the VM's tailnet name only when the VM joined under its own name: a VM name that is
not one DNS label joins under a cleaned-up one (`alpha.lab` as `alpha-lab`), and a name already taken gets
a suffix (`alpha-1`). The report shows the name each VM has; `tailscale_hostname`
([Other settings](#other-settings)) asks for another.

## Keeping an agent VM off your other devices

A new tailnet's policy lets every device reach every other - its one rule has `"src": ["*"]`, and `*`
matches tagged devices too - and these VMs run an AI agent with passwordless sudo. With a tagged key, one
edit to the policy (Access controls in the admin console) keeps your devices reaching the VMs while a VM
can open no connection at all. **Merge** it into your policy; never paste it over the whole file, and
never as `"acls": []`, which cuts off every other device you have:

```jsonc
{
  // The VMs' tag. An admin owns it, and so can also apply it at a web login.
  "tagOwners": { "tag:claude-vm": ["autogroup:admin"] },
  // The default rule, its source narrowed from "*" to your own devices: they still reach
  // everything, the VMs and every port on them included; a tagged VM is no source, so it reaches nothing.
  "acls": [ { "action": "accept", "src": ["autogroup:member"], "dst": ["*:*"] } ],
  // Run on every save: an edit that lets a VM reach your devices, or another VM, is refused.
  "tests": [ { "src": "tag:claude-vm", "deny": ["you@example.com:22", "tag:claude-vm:22"] } ]
}
```

Put your own address in the test. Any other rule whose source is `*`, or names the tag, undoes the edit,
and the test makes the console refuse such a change when you save it. A policy that already narrowed the
default rule needs only the tag owner and the test. A VM joined by the web login is your own device and
reaches what your other devices reach; to isolate one, set `tailscale_advertise_tags: [tag:claude-vm]`
before signing it in - as an admin, you can apply the tag at the login. On the VM itself, sudo can always
log it out or read its node keys: only the tag and the policy bound what it can reach.

**Tailscale SSH** (`tailscale_ssh: true`) answers port 22 on the tailnet address instead of sshd,
authorised by your policy, which then needs an `ssh` rule:
`{"action": "accept", "src": ["autogroup:member"], "dst": ["tag:claude-vm"], "users": ["dev"]}`. Every
configure holds each VM to the value you set, both ways.

## DNS

The VM keeps the resolver DHCP gave it (`tailscale_accept_dns: false`), and your devices still
reach it by its tailnet name. With `true` it takes your tailnet's DNS settings - and a tailnet that
overrides local DNS then answers every lookup on the VM, from nameservers that, if they are on the tailnet,
a VM the policy above isolates cannot reach.

## Other settings

`tailscale_login_server` points the VMs at another control server, such as Headscale; empty, the
default, is Tailscale's own. The web login then opens a page only when it is under that server.
`tailscale_accept_routes: true` has the VMs use the subnet routes other devices on the tailnet
advertise; it is off by default. `tailscale_hostname` is the machine name each VM asks for, by default
the VM's name made one DNS label (`alpha.lab` and `my_vm` become `alpha-lab` and `my-vm`). Every
Tailscale setting, with its default, is in [Settings](../reference/settings.md#tailscale).

## What the role touches

Only the Tailscale it installed. A Tailscale you installed on a VM yourself -
the package, the snap or Tailscale's static binaries - is left exactly as it is,
and reported while Tailscale is on; off, `just deploy` says nothing of
it. To hand one to this project, run `sudo tailscale logout` on the VM
and remove it the way you installed it; for the package that is `sudo apt purge tailscale` - purge, so the
next install does not start under the old login.

A login you made by hand on a Tailscale this project installed keeps its own settings, and when its key
expires `just login --only tailscale` and `just deploy` leave it for you to sign in again the way you made it,
rather than put this project's settings over it. A sign-in this project started is its own, however late
it ends - a page you approve after the run, or a join with the key that ran out of time - and so is a
login made by hand while that page still waits, which cannot be told from it; a join with the key that
Tailscale refused is over, and leaves nothing waiting. How the role tells its own login from yours is
in [§5.8](../architecture/roles.md#58-tailscale--join-the-vm-to-a-tailnet).

Log upload to Tailscale is on, as Tailscale ships it; `tailscale_upload_logs: false` turns it off from
the daemon's first start, which also ends Tailscale's technical support for the VMs.

If installing Tailscale fails - its package site out of reach, say - the VM is configured otherwise and
keeps its alias, `just deploy` still signs it in and opens it, and the run fails at its end with `just
deploy alpha --tags tailscale`; Tailscale's repository is taken back out, so `apt` on the VM
works as before.

## Turning it off

`vpn: none` (or `tailscale_enabled: false`, its form before the `vpn` setting, still read while `vpn` is not
set) puts Tailscale on no VM deployed from then on, and takes it off none: choosing a VPN never removes one.
On each VM this project installed Tailscale on, `just deploy --only configure` says it is left as it is,
unmanaged - still running and joined, with nothing of this project's keeping its settings - and `just status`
shows the VM on the tailnet, unmanaged. `vpn: tailscale` manages it again. A Tailscale you installed yourself
is left alone, as ever.

To take it off a VM, by hand, on the VM:

```
sudo tailscale logout
sudo apt-get remove -y tailscale
sudo rm -f /etc/apt/sources.list.d/tailscale.sources \
  /etc/systemd/system/tailscaled.service.d/10-agent-warehouse.conf \
  /var/lib/agent-warehouse/tailscale-installed /var/lib/agent-warehouse/tailscale-joined
sudo systemctl daemon-reload
```

Then delete its device in the admin console (Machines): a logged-out device stays there, and holds its name.
`just destroy` does all of it for a VM it deletes ([Removing devices for you](#removing-devices-for-you)).

The VM's `agent-warehouse-tailscale` tag in Proxmox stays: the project never takes it off, since only
you can say the logout was yours. Turned back on, the VM is signed in by the web login under `auto`, and
under `key` the run stops and names the tag. For one more join with the key, remove that tag from the VM in
Proxmox - its tags, in the VM's summary in the web UI - and keep the ownership tag beside it.

## Removing devices for you

A logged-out device stays in the admin console until it is deleted, and
holds its name: a VM built again as `alpha` joins as `alpha-1`, for good. With an API credential the
project deletes the device itself when `just destroy` deletes the VM. Put either credential below in the vault and that is all: it is used as soon as it
is there, and every call is made from this machine, so it never reaches a VM.

| Credential | In the vault | Made in the admin console |
|---|---|---|
| An API access token | `vault_tailscale_api_token` | Settings, Keys, *Generate access token*: a `tskey-api-` token, which lasts at most 90 days and can delete any device you can |
| An OAuth client | `vault_tailscale_oauth_client_id`, `vault_tailscale_oauth_client_secret` | Settings, OAuth clients, with the `devices:core` scope: it never expires, but Tailscale scopes it to the tags you give it, which may keep it from deleting a device without them, such as one joined by the web login |

With neither, the device is only logged out, and the run names it to delete by hand. With both, the API
access token is used, since it can delete every device this project makes. `tailscale_api_auth_method` in
`local.yml` overrides that choice: `token` or `oauth` uses that credential only, and `none` leaves the
credentials in the vault unused. Its default, `auto`, is what this section describes.

A deletion Tailscale refuses is said, with the device to delete by hand, and the rest of the run goes on.

## Rebuilding a VM

Delete the old device first - `just destroy` does with a credential above, and says
so otherwise - or the new VM joins as `alpha-1`; a configure that finds the name taken says so.

## See also

- Settings: [Tailscale](../reference/settings.md#tailscale) and
  [Secrets: Tailscale](../reference/settings.md#secrets-tailscale)
- Role: [`tailscale`](../reference/roles/tailscale.md)
- How it works: [§5.8](../architecture/roles.md#58-tailscale--join-the-vm-to-a-tailnet), and the sign-in
  playbook in [§4.10](../architecture/lifecycle.md#410-playbookstailscale_loginyml)
