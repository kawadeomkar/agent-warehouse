#!/bin/sh
# Stand-in for virt-customize: records its arguments so the Molecule verify
# stage can assert what would have been installed into the image. The new
# engine's build tests (tests/unit/test_aw_engine_template.py) give the log a
# path of their own in FAKE_VIRT_CUSTOMIZE_LOG.
set -eu
LOG=${FAKE_VIRT_CUSTOMIZE_LOG:-/var/lib/fake-virt-customize.log}
mkdir -p "$(dirname "$LOG")"
printf '%s\n' "$*" >>"$LOG"
exit 0
