# Reaching your VMs from away

The VMs are on the LAN your Proxmox host is on, at the addresses its DHCP gives them, and every `just`
command reaches Proxmox's API and the VMs there. Away from that LAN you need a way back to it, and there are
two: a VPN of yours that routes the LAN, which needs nothing from this project, or Tailscale on each VM.

## A VPN that routes your LAN

If a VPN you already run routes your LAN to your devices - WireGuard on your router, a Tailscale subnet
router, any other - set `vpn: none`, the default, and that is all. Your laptop reaches the VMs at their LAN
addresses wherever the VPN reaches it, so the SSH aliases this project writes, `just code` and your editor
work the same at home and away.

What this project reaches, so what your VPN's rules must let through:

| From your machine to | Port | For |
|---|---|---|
| the Proxmox host | 8006 | Proxmox's API: every `just` command |
| the Proxmox host | 22 | root SSH: `just init`'s checks, and building the template |
| each VM | 22 | SSH: configuration, `just code`, your editor |

And whatever else you work on there, such as a dev server's port.

### Giving someone else access

The same route: a peer on your WireGuard server, or an invitation to your tailnet behind its subnet router -
not a shared machine, which carries no subnet routes - with rules that let them reach only the ports above.

- The VMs' addresses come from your DHCP pool, among your own devices', so no rule can name the VMs alone; a
  bridge or VLAN of their own (`proxmox_bridge`) puts them on one subnet to route and to firewall.
- A LAN of theirs on the same subnet as yours cannot reach it: their machine routes those addresses at home.

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
well.

## See also

- [Tailscale](tailscale.md), and [Settings](../reference/settings.md#vpn) for `vpn`.
- [Upgrading](upgrading.md#a-vpn-only-when-chosen), for an install from before 2.1, when Tailscale was the
  default.
