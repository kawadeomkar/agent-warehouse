# WireGuard

With `vpn: wireguard`, each VM is a peer of a WireGuard server you already run - a `wg-quick` interface on a Linux
box or a VPS, your router's WireGuard, a NAS's - so you reach it from anywhere your other devices are, as you would
over a tailnet. The project uses that server and never changes it: it makes each VM's key on the VM, gives the VM
an address from a range of the server's tunnel network you keep for the VMs, sets the VM's link up, and says, run
after run, the peer to add on your server. `just destroy` says the one to remove.

A commercial VPN service is no such server: its peers cannot reach each other.

## What your server must already do

The project sets up nothing on the server. Before the first VM joins:

- **A tunnel network, with a range kept for the VMs.** Say your server's interface is `198.51.100.1/24`: keep
  `198.51.100.128/25` for the VMs, and give your devices addresses below it. VMs of a second Proxmox joining the
  same server need a range of their own, `198.51.100.64/26` say: each Proxmox gives out its range's addresses
  knowing only its own VMs, so two sharing one would give two VMs the same address.
- **Forwarding between its peers**, so a device reaches a VM through it: on Linux, `net.ipv4.ip_forward=1` and the
  two rules below.
- **Each device's config routes the VMs' range into the tunnel**: its `[Peer]`'s `AllowedIPs` holds
  `198.51.100.128/25`, or the whole tunnel network.
- **A port the VMs reach.** On the VMs' own LAN, its LAN address; away from it, its public address or a name.

On a Linux server whose interface is `wg0`, these pass your devices to the VMs' range, and keep an agent VM off your
devices - a VM answers, and never opens a connection to one:

```
iptables -A FORWARD -i wg0 -o wg0 -s 198.51.100.128/25 -m conntrack --ctstate NEW -j DROP
iptables -A FORWARD -i wg0 -o wg0 -s 198.51.100.0/25 -d 198.51.100.128/25 -j ACCEPT
```

## Joining a VM

In `local.yml`:

```yaml
vpn: wireguard
wireguard_hub_network: 198.51.100.0/24     # the server's tunnel network
wireguard_vm_prefix: 198.51.100.128/25     # the part of it kept for the VMs
wireguard_hub_public_key: <the server's public key>   # wg show wg0 public-key, on the server
wireguard_hub_endpoint: 192.0.2.1:51820    # what the VMs dial: address or name, and port
```

Then `just deploy <vm>` (or `just deploy --only configure` for VMs that exist). For each VM, the run:

1. installs `wireguard-tools` and makes the VM's key pair, on the VM: root and `systemd-network` may read the private
   key, nobody else, and it never leaves the VM;
2. gives the VM an address in the range - the lowest one free of your other VMs' (and, on OPNsense, of every peer
   your instance has), or the one it had, which Proxmox keeps for it as a tag beside the ownership tag,
   `claude-on-proxmox-wg-198.51.100.129`, so it keeps it for its life;
3. sets up the link `claude-wg` with systemd-networkd, your server its one peer;
4. says the peer to add on your server, and keeps the line in NEXT STEPS until the VM has reached it.

The peer, for a VM named `alpha`:

```
[Peer]
# claude-on-proxmox-alpha
PublicKey = <the VM's public key>
AllowedIPs = 198.51.100.129/32
```

On a `wg-quick` server, add it to `/etc/wireguard/wg0.conf` and run `wg syncconf wg0 <(wg-quick strip wg0)`, or
`wg set wg0 peer <key> allowed-ips 198.51.100.129/32` for the running interface alone.

`just status <vm>` shows the VM's address and when it last reached the server. From a device on the tunnel,
`ssh <vm user>@198.51.100.129` reaches it; the SSH alias keeps the VM's LAN address, unless you ask for one address
everywhere (below).

## One address at home and away

`vpn_reach: tunnel` puts each VM's tunnel address in its SSH alias, so `ssh alpha` and your editor reach the VM the
same way at home and away: away through your device's tunnel, at home through your router's route to the tunnel
network, when your server is the router or the router routes that network to it.

At home, the request then comes from your laptop's LAN address, and the VM would answer it from its tunnel address
straight over the LAN, which your router's firewall drops as half a connection. So under `tunnel` the VM answers
whatever reached it at its tunnel address back through the tunnel: a rule of its own (`From=<its tunnel
address>/32`), a table whose one route is the tunnel, and the server its peer for every address. Its own traffic,
from its LAN address, still leaves on its LAN.

A VM's alias moves to its tunnel address only once the VM has reached your server there: until then the address
leads nowhere, and `ssh alpha` and your editor would fail. Proxmox records it beside the address
(`claude-on-proxmox-wg-198.51.100.129-reached`), so `just deploy`, `just deploy --only ssh-config` and `just code`
agree. The run that first sees the handshake records it: after you add a VM's peer, `just deploy <vm> --only
configure` moves its alias, as its NEXT STEPS line says.

Your server's side, for this: pass your LAN to the VMs' range on port 22 (and any port you work on), and keep
dropping what the VMs' range opens itself. Tailscale offers no `tunnel` reach yet; the setting is refused with it.


## On OPNsense

With `wireguard_hub: opnsense`, on OPNsense 26.7.2 or later, the run writes each VM's peer on your instance through
the router's API instead of saying it, and `just destroy` removes it there. It writes only peers with every mark of
the project's - named `claude-on-proxmox-<vm>`, an address in `wireguard_vm_prefix`, on your instance - and never
the instance, your devices, a rule, NAT or DNS. A peer with the project's name but not every mark is said and left
alone. On a run without names and a complete listing of the VMs, a peer whose VM is gone is removed. A router that
lists its peers only in part gets no write that run: a peer it did not list may be the VM's own.

A VM renamed in Proxmox keeps its key, which its old peer holds: it is said and not joined beside that peer until a
fleet-wide `just deploy --only configure` has removed the old one. A VM cloned from another has that VM's key too:
delete `/etc/systemd/network/claude-wg.key` on the clone and run again, and it makes its own.

Before the first run:

1. **An API user** with the privilege "VPN: WireGuard", and a key for it (System, Access, Users). The run reads
   the router's version first (`/api/core/firmware/info`), which may need a firmware page's read privilege too: a
   key refused there is said by name. Put the key and
   secret in the vault as `vault_wireguard_hub_api_key` and `vault_wireguard_hub_api_secret` (`just vault`). That
   privilege covers all of WireGuard, the instance and its private key included: what keeps the project to its own
   peers is its code, which reads the instance and refuses any change to a peer that is not its own.
2. **The router in `hosts.yml`**, where the API answers, as below.
3. **The certificate's SHA-256**, since the router's certificate is self-signed: `wireguard_hub_cert_sha256` in
   `local.yml`, from `openssl s_client -connect 192.0.2.1:443 < /dev/null | openssl x509 -noout -fingerprint -sha256`.
   The run refuses any other certificate before it sends the key.
4. **`wireguard_hub_instance`**, your instance's name. Its tunnel network and public key are read from the router;
   `wireguard_vm_prefix` must be a part of that network no device of yours uses.

In `hosts.yml`, beside the Proxmox host:

```yaml
    wireguard_hub:
      hosts:
        router:
          ansible_host: 192.0.2.1
          ansible_connection: local
```

The run reads the router's version first and refuses one below 26.7.2, before any write and in check mode too.
Every answer the API gives is stripped of the instance's private key, which the API returns in plain text, before
anything is kept or said.

## What the role touches

Only what it made, by names of its own: the marker `/var/lib/claude-on-proxmox/wireguard-installed`, written before
its first change; the key `/etc/systemd/network/claude-wg.key`; the files `50-claude-wg.netdev` and
`50-claude-wg.network` beside it; the link `claude-wg`; and `wireguard-tools`, when it installed it. A WireGuard of
your own on the VM - `wg0`, `/etc/wireguard` - is never touched.

The link's peer is your server, and its `AllowedIPs` the tunnel network: the VM routes your devices' addresses into
the tunnel, and its own traffic stays on its LAN. The keepalive (`wireguard_keepalive`, 25 seconds) keeps a server
on the internet able to reach a VM behind NAT.

An endpoint given as a name is looked up once, when the link is made: a server whose address changes - a home
router's dynamic DNS - is reached again after `just deploy <vm> --only configure --tags wireguard`. On the VMs' own
LAN, give its LAN address instead.

## Turning it off

`vpn: none` (or another VPN), then `just deploy --only configure`: on each VM where the role set WireGuard up, the
run says it is taking it off, deletes the link, its files and the key, and removes `wireguard-tools` if the role
installed it. The peer stays on your server until you remove it, and the address stays recorded in Proxmox, so the
VM gets it again if WireGuard comes back.

## Destroying a VM

`just destroy <vm>` says, for each VM it deleted that had an address, the peer to remove from your server, and keeps
it in NEXT STEPS. Its key went with the VM, so the peer can never connect again; removing it frees the address.

## See also

- [Tailscale](tailscale.md), the other VPN.
- [Roles §5.9](../architecture/roles.md#59-wireguard--each-vm-a-peer-of-your-wireguard-server), what the role does.
- [Custom code §7.11](../architecture/custom-code.md#711-the-choices-the-editor-the-vpn-and-later-the-hosts-os), the
  plan of who is on the network.
