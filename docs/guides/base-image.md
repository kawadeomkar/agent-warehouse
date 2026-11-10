# Your own base image

By default every VM starts from Ubuntu's minimal cloud image. You can start from your own instead, one that already
holds your toolchain, however you built it: Packer, `virt-customize`, an image made with Nix, or by hand.

There are two ways, set in `local.yml`:

| You have | Set | What the project does |
|---|---|---|
| An image at a URL | `proxmox_template_image_url` and `proxmox_template_image_checksum` | Downloads it, adds the guest agent, and builds its template from it, rebuilding when the URL changes (a new image at the same URL needs the template removed first) |
| A Proxmox template you built | `proxmox_template_custom: <its name>` | Checks it and clones it. It never changes, rebuilds or deletes it, and builds no template of its own |

A custom image URL needs its checksum: `sha256:<digest>`, or `sha256:<URL of a checksum list>`.

Do not point `proxmox_template_vmid` and `proxmox_template_name` at a template of your own: those name the template this
project builds. A template there with no record of this project's build is refused and left as it is.

## What a base must have

**Ubuntu or Debian.** Configuring a VM installs with apt and runs modules with `/usr/bin/python3`, so a base of another
family is refused by name, before it is built on or configured. Ubuntu 24.04 is what every test runs on; configure and
a golden template have also been run on Debian 12. `dev_tools_install_rust` needs a `rustup` package, which Debian 12
and Ubuntu 22.04 do not have, so leave it off on those.

Inside it:

- cloud-init, which Proxmox's cloud-init drive feeds: it creates the VM's user with your keys, and sets its hostname and
  network.
- `qemu-guest-agent`, enabled. For an image the build adds it; a template of yours must have it, since a VM's address
  comes from it.
- An OpenSSH server, systemd and apt.
- `/usr/bin/python3`, and `sudo` at `/usr/bin/sudo`.

Everything else, from git to Claude Code, is installed for you.

A template of yours also needs, on Proxmox:

- A cloud-init drive, the guest agent enabled, and `net0` (or set `proxmox_nic`).
- Its boot disk on `scsi0`, or `proxmox_vm_disk` set to the one it has, such as `virtio0`.
- A CPU type with AVX2, such as `host`: without it, Claude Code hangs, and Proxmox's defaults lack it.
- No cloud-init user set on the template, and no tag of this project's. A clone would inherit them and pass for a
  finished VM.

## Each thing is checked first

- **A template of yours** is read through the API before anything is created. Each thing it lacks is said, with what
  fixes it.
- **An image** has its `os-release` read before it is built on.
- **Every VM**, whatever it was made from, is checked first in configure: Python, sudo, apt, systemd, cloud-init and the
  OS family. Only that VM stops.

## Your toolchain in the image

Configure still runs every task on a VM made from your base, so a package your image already has reports `ok` and
costs nothing. For a tool your image provides some other way, turn the project's off, so that it does not install a
second one:

```yaml
dev_tools_install_node: false     # Node from nvm or fnm: the role's would go into /usr/bin beside it
dev_tools_install_docker: false   # Ubuntu's docker.io conflicts with the docker-ce the role installs
dev_tools_install_gh: false
dev_tools_install_uv: false
```

Claude Code stays the project's: `just code` and Remote Control look for it only where its installers put it.

## An example: an image with `virt-customize`

On any Linux machine with `libguestfs-tools`:

```bash
curl -LO https://cloud-images.ubuntu.com/minimal/releases/noble/release/ubuntu-24.04-minimal-cloudimg-amd64.img
virt-customize -a ubuntu-24.04-minimal-cloudimg-amd64.img --install ripgrep,tmux,postgresql-client
sha256sum ubuntu-24.04-minimal-cloudimg-amd64.img
```

Serve the file from somewhere your Proxmox host can reach, and set:

```yaml
proxmox_template_image_url: https://files.example.com/my-image.img
proxmox_template_image_checksum: sha256:<the digest sha256sum printed>
```

The next `just deploy` rebuilds the template from it.

## With golden templates

With `proxmox_template_golden` on, a golden template is built on top of your base. Changing your base makes a new one
on the next deploy of a new VM.
