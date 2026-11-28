#!/bin/sh
# Stand-in for virt-cat: the os-release of the image, Ubuntu's unless a test has
# put another in /var/lib/fake-virt-cat-os-release, or in the file
# FAKE_VIRT_CAT_OS_RELEASE names (tests/unit/test_aw_engine_template.py).
set -eu
RELEASE=${FAKE_VIRT_CAT_OS_RELEASE:-/var/lib/fake-virt-cat-os-release}
if [ -f "$RELEASE" ]; then
  cat "$RELEASE"
else
  printf 'ID=ubuntu\nID_LIKE=debian\n'
fi
