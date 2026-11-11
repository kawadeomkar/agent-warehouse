#!/bin/sh
# Stand-in for virt-cat: the os-release of the image, Ubuntu's unless a test has
# put another in /var/lib/fake-virt-cat-os-release.
set -eu
if [ -f /var/lib/fake-virt-cat-os-release ]; then
  cat /var/lib/fake-virt-cat-os-release
else
  printf 'ID=ubuntu\nID_LIKE=debian\n'
fi
