# Reaching your VMs from away

The VMs are on the LAN your Proxmox host is on, at the addresses its DHCP gives them, and every `just`
command reaches Proxmox's API and the VMs there. Away from that LAN you need a way back to it: a VPN of yours that
routes the LAN, which needs nothing from this project; the Proxmox host on your tailnet alone, which the project then
goes through to reach the VMs; or Tailscale on each VM.

## A VPN that routes your LAN

If a VPN you already run routes your LAN to your devices - WireGuard on your router, a Tailscale subnet
router, any other - set `vpn: none`, the default, and that is all. Your laptop reaches the VMs at their LAN
addresses wherever the VPN reaches it, so the SSH aliases this project writes, `just code` and your editor
work the same at home and away.

What this project reaches, so what your VPN's rules must let through:

| From your machine to | Port | For |
|---|---|---|
| the Proxmox host | 8006 | Proxmox's API: every `just` command |
| the Proxmox host | 22 | root SSH: `just init`'s checks, and building the template; with the route through the host, every connection to a VM ([below](#your-proxmox-host-on-your-tailnet-and-no-subnet-router)) |
| each VM | 22 | SSH: configuration, `just code`, your editor |

And whatever else you work on there, such as a dev server's port.

### Giving someone else access

The same route: a peer on your WireGuard server, or an invitation to your tailnet behind its subnet router -
not a shared machine, which carries no subnet routes - with rules that let them reach only the ports above.

- The VMs' addresses come from your DHCP pool, among your own devices', so no rule can name the VMs alone; a
  bridge or VLAN of their own (`proxmox_bridge`) puts them on one subnet to route and to firewall.
- A LAN of theirs on the same subnet as yours cannot reach it: their machine routes those addresses at home.

## Your Proxmox host on your tailnet, and no subnet router

Where your machine reaches the Proxmox host - over Tailscale, say - but not the network its VMs are on, every SSH
connection to a VM goes through the host's own sshd, as a forward: configuration, the sign-ins, `just code`,
`just destroy`'s save, `just status`, and the alias your editor and `ssh` use. `vm_ssh_route` in `local.yml` decides:

| `vm_ssh_route` | Reaches each VM |
|---|---|
| `auto` (the default) | through the Proxmox host where `ssh` reaches the host at a tailnet address (`100.64.0.0/10`, `fd7a:115c:a1e0::/48`, a MagicDNS name or your own name for one), straight to its address otherwise |
| `host` | always through the Proxmox host |
| `direct` | always straight to its address, as before 2.7 |

There is no fallback either way: a host `auto` goes through that cannot be used - no SSH to it, forwarding off -
stops the run, saying what the host said: no SSH to it at the run's start, forwarding off at the first step that
reaches a VM (for a new VM, once the template is built and the VM created). A VM's address is never dialled from
wherever your laptop is. Where a VPN of yours routes the VMs' network, or you reach them by a jump of your own, set
`vm_ssh_route: direct`. A VPN that reaches only the host, such as WireGuard to it alone, is not recognised: set
`host`.

- **Who the hop is.** The Proxmox host as the inventory reaches it - its address, `ansible_user`, port and key - or
  another account on it, named on the host's own entry in `hosts.yml`: `vm_ssh_jump: forwarder`. An account only;
  the hop is always the hypervisor.
- **A forwarding-only account is recommended**, to an owner too: through the host, a connection kept for two minutes
  after a run runs commands as that account for any process of yours while it lives. One that may only forward is
  worth nothing else; the lines for the host's sshd are below. The project never makes the account.
- **The aliases** gain one line, `ProxyJump root@pve.example.ts.net`, so `ssh alpha` and your editor go the same
  way. A `ProxyJump` carries no key: where the inventory names one for the host
  (`ansible_ssh_private_key_file`), a run says the lines to add to `~/.ssh/config` if your ssh would not offer it
  there. `claude_ssh_options` with a `ProxyJump` of your own writes none.
- **A jump of your own** - a `ProxyJump`, `-J` or `ProxyCommand` in `ansible_ssh_common_args` or
  `ansible_ssh_extra_args` for a VM - is that VM's route, and the run says so; the project adds no hop to it.
- **Names.** Through the host a VM is reached at its address: `claude_ssh_host_domain` is not used, since the host's
  sshd would resolve the name.
- **What it needs on the host.** Forwarding allowed to the VMs (`AllowTcpForwarding`, `PermitOpen`), and the host
  able to reach them: a VM on a bridge the host has no address on, or behind a firewall rule that admits your laptop
  and not the host, needs `direct`. A host running Tailscale's own SSH server answers port 22 itself, and its policy
  decides forwarding.
- **A VM that does not answer** through the host shows as a closed connection or a time-out; `just status <vm>` says
  what the host reports for it. A run started within 60 seconds of your laptop waking can find the VMs not answering
  once, on a connection that has not noticed it is gone. `just destroy` asks the host about such a VM before it
  deletes it: one that answers the host but not the run, or that the host will not forward to, is kept, neither
  released nor deleted.
- **`tailscale_accept_routes`** on, where the host advertises the VMs' own network to the tailnet: each VM then
  routes that network through Tailscale, its replies to the host's address on it among them, so a connection through
  the host breaks. Leave it off for such VMs.

The forwarding-only account, `forwarder` here, made on the host with no shell and your key in its `authorized_keys`,
and these lines at the end of the host's `/etc/ssh/sshd_config` (`PermitOpen` may list the VMs' network instead):

```
Match User forwarder
    AllowTcpForwarding local
    PermitOpen *:22
    PermitTTY no
    X11Forwarding no
    ForceCommand /bin/false
```

## Tailscale on each VM

`vpn: tailscale` joins each VM to your tailnet itself ([Tailscale](tailscale.md)), which adds what a route to
the LAN does not:

- a name for each VM (`alpha.tail1234.ts.net`), reached from any of your devices with no route, port or VPN
  server of yours - also where nothing routes the Proxmox host's network;
- each VM a device of its own, so your tailnet's policy can keep the VMs off your other devices, and you can
  share one VM with someone;
- with an auth key in the vault, the join with nothing to click, and with an API credential, the VM's device
  deleted when `just destroy` deletes the VM.

The `just` commands still reach Proxmox and the VMs at their LAN addresses: away from the LAN, Tailscale on the
VMs lets you `ssh` to them and open your editor, while `just deploy` and `just code` need a route to the LAN as
well - or the Proxmox host on your tailnet, which they then go through ([above](#your-proxmox-host-on-your-tailnet-and-no-subnet-router)).

## See also

- [Tailscale](tailscale.md), and [Settings](../reference/settings.md#vpn) for `vpn`, and
  [Editor and SSH](../reference/settings.md#editor-and-ssh) for `vm_ssh_route`.
- [Upgrading](upgrading.md#a-vpn-only-when-chosen), for an install from before 2.1, when Tailscale was the
  default.
