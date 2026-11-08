# -*- mode: ruby -*-
# End-to-end test of playbooks/configure.yml on a real Ubuntu VM (VirtualBox).
# Docker-based Molecule tests cover the roles; this additionally exercises
# the docker service and a full cloud image. qemu-guest-agent stays stopped:
# VirtualBox gives the VM no guest-agent port.
#
#   just vagrant        # ~10 minutes on first run
#   vagrant ssh            # then: sudo -iu dev claude
#   just vagrant destroy
#
# Vagrant generates its own inventory, so inventory/group_vars is not loaded;
# role variables are passed directly. No real credentials are needed. Set
# GITHUB_USERNAME (and optionally GITHUB_TOKEN, ANTHROPIC_API_KEY) to use yours.
# Secrets are read from the environment inside the play (lookup), never placed
# on the ansible-playbook command line.
SSH_PUBLIC_KEY = ENV.fetch("SSH_PUBLIC_KEY") do
  path = File.expand_path("~/.ssh/id_ed25519.pub")
  # Vagrant injects its own key; this one only needs to be well-formed.
  File.exist?(path) ? File.read(path).strip : "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPlaceholderKeyForVagrantTestsOnly0000000 vagrant@test"
end

Vagrant.configure("2") do |config|
  config.vm.box = "bento/ubuntu-24.04"
  # A box version moves only when this line does, as every other download is
  # pinned. bento publishes no checksum for its boxes, so the version is all
  # that holds it: Vagrant verifies nothing past the download's TLS.
  config.vm.box_version = "202510.26.0"
  config.vm.hostname = "claude-dev"
  # Vagrant shares the project folder with the VM, read-write at /vagrant,
  # unless told not to - in a checkout that holds .vault_pass and the vault.
  # The VM runs an agent with sudo: it would read every secret, and could
  # change the files this machine runs next (tools/env-run, .git/hooks). The
  # Ansible provisioner runs here and needs no share.
  config.vm.synced_folder ".", "/vagrant", disabled: true
  config.vm.provider "virtualbox" do |vb|
    vb.memory = 4096
    vb.cpus = 2
  end

  config.vm.provision "ansible" do |ansible|
    ansible.compatibility_mode = "2.0"
    # The project's own, which just vagrant passes; run without it, the one
    # tools/env-run finds - never one merely first on PATH - with this
    # checkout's own collections. With no environment, a path that says what is
    # missing, so a provision fails on it rather than on another Ansible.
    ansible.playbook_command = ENV.fetch("ANSIBLE_PLAYBOOK") do
      bin = `tools/env-run --bin 2>/dev/null`.strip
      File.join(bin.empty? ? "/no-environment-for-agent-warehouse-run-just-install" : bin, "ansible-playbook")
    end
    ENV["ANSIBLE_COLLECTIONS_PATH"] ||= `tools/env-run --collections`.strip
    ansible.playbook = "playbooks/configure.yml"
    ansible.groups = { "claude_vms" => ["default"] }
    ansible.extra_vars = {
      # This VM is already in Vagrant's own inventory and there is no Proxmox
      # API here, so skip discovery; otherwise configure.yml fails resolving
      # proxmox_api_host, which lives in inventory/group_vars Vagrant does not load.
      claude_vm_discovery: false,
      # Vagrant reaches this VM on a forwarded port that an alias would not
      # carry, and a test must never write into the developer's ~/.ssh.
      claude_ssh_config: false,
      configure_wait_for_cloud_init: false,
      common_manage_hostname: false,
      common_user_ssh_public_keys: [SSH_PUBLIC_KEY],
      github_projects_username: ENV.fetch("GITHUB_USERNAME", "octocat"),
      github_projects_token: "{{ lookup('ansible.builtin.env', 'GITHUB_TOKEN') }}",
      github_projects_include: ENV.key?("GITHUB_USERNAME") ? [] : ["Hello-World"],
      claude_code_anthropic_api_key: "{{ lookup('ansible.builtin.env', 'ANTHROPIC_API_KEY') }}",
      # The real tailscaled is installed here, and a test never reaches
      # Tailscale: logs off from its first start, and no key, so the VM is
      # never joined - configure reports it not signed in.
      tailscale_upload_logs: false,
      tailscale_auth_key: "",
    }
  end
end
