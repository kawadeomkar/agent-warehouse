"""`just status` at a terminal: the live report, drawn and redrawn in place, then the finished one (lifecycle.md §4.6).

Each test runs tools/status against the fake Proxmox (harness.fake_fleet), at a pseudo-terminal of its own whose size
the test sets, and reads the bytes back through vt.VT, a small terminal: what is on the screen and in its scrollback
when the run ends, and whether a frame ever wrapped. The finished report is the run log's, which the engine writes and
the live frames never reach.
"""

from __future__ import annotations

import json
import os
import re
import signal
import sys
from pathlib import Path

import live_kit as kit
import pytest
import yaml
from ansible.parsing.vault import VaultLib, VaultSecret
from harness import PVE_SECRET, _env, _executable, fake_fleet
from test_status import STATUS, status_root
from vt import VT

FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
# UTF-8 whatever this machine's locale: the engine draws only for a terminal that takes the spinner.
LIVE = {"PYTHONUTF8": "1"}


@pytest.fixture(scope="module")
def fleet(tmp_path_factory):
    # One fake for the module: status only reads, which test_status holds.
    yield from fake_fleet(tmp_path_factory.mktemp("fleet"))


def _inventory(root: Path) -> Path:
    """localhost alone, as Ansible's implicit one: an empty inventory is a warning, which a real run never prints."""
    inventory = root / "hosts.yml"
    inventory.write_text(
        yaml.safe_dump(
            {
                "all": {
                    "hosts": {
                        "localhost": {"ansible_connection": "local", "ansible_python_interpreter": sys.executable}
                    }
                }
            }
        )
    )
    return inventory


def _command(root: Path) -> list[str]:
    return [sys.executable, str(STATUS), "-i", str(_inventory(root))]


def _at_a_terminal(
    tmp_path: Path,
    base: str,
    cols: int,
    rows: int,
    *,
    term="xterm-256color",
    resize=None,
    interrupt=False,
    colour=False,
):
    """Run tools/status at a pseudo-terminal of cols x rows: its rc, the VT the bytes went through, the raw bytes and
    the run log. resize, (cols, rows), changes the size once the first frame with the VMs is drawn; interrupt sends
    Ctrl-C's SIGINT to the run's process group once both running VMs (harness.fake_fleet) wait in ssh.

    Either holds every VM's ssh (test_status.SSH) until it is done: a Linux pty takes the rest of the run's output
    without blocking it, so the run could end before the test reads that frame. A resize lets the run go on once
    the region has pushed the screen up at the new size; an interrupt never does."""
    root = status_root(tmp_path, base)
    hold = root / "ssh-holds"
    if resize or interrupt:
        hold.touch()
    # The harness turns colour off for every run; with colour, this one is at a colour terminal, as a person's is.
    env = kit.environment(root, config="", TERM=term, ANSIBLE_NOCOLOR="0" if colour else "1", **LIVE)
    acted = []
    # The screen pushed up at the new size: a newline (the terminal's \\r\\n) for each of its rows, then up all but one.
    pushed = re.compile(rb"(\r?\n){%d}\x1b\[%dA" % (resize[1], resize[1] - 1)) if resize else None

    held = root / "ssh.log"

    def resize_or_interrupt(chunk, output, master, process, vt):
        if not acted and resize and b"VMID" in output:
            acted.append(len(output))
            kit.resize(master, vt, *resize)
            os.killpg(process.pid, signal.SIGWINCH)
        if not acted and interrupt and held.exists() and len(held.read_text().splitlines()) >= 2:
            acted.append(len(output))
            os.killpg(process.pid, signal.SIGINT)
        if pushed and acted and hold.exists() and pushed.search(output, acted[0]):
            hold.unlink()

    ran = kit.at_a_terminal(_command(root), root, env, cols=cols, rows=rows, act=resize_or_interrupt)
    return ran.rc, ran.screen, ran.raw, ran.log


def _report(log: str) -> list[str]:
    """The finished report, as the run log holds it: from its title to its exit line."""
    lines = log.splitlines()
    start = next(i for i, line in enumerate(lines) if re.search(r"\| agent-warehouse  \d{4}-", line))
    end = next(i for i in range(start, len(lines)) if re.match(r"exit \d", lines[i]))
    return [lines[start].split("| ", 1)[1]] + lines[start + 1 : end + 1]


def _wrapped(lines: list[str], cols: int) -> list[str]:
    """lines as a terminal of cols shows them: each one longer than cols on as many rows as it takes."""
    out = []
    for line in lines:
        line = line.rstrip()
        out += [line[i : i + cols].rstrip() for i in range(0, len(line), cols)] or [""]
    return out


def _shown(vt: VT) -> list[str]:
    return [line.rstrip() for line in vt.transcript()]


def test_a_password_script_that_asks_is_answered_before_anything_is_drawn_and_asked_once(tmp_path, fleet) -> None:
    root = status_root(tmp_path, fleet)
    secret = VaultSecret(b"the-vault-password")
    plain = json.dumps({"vault_proxmox_api_token_secret": PVE_SECRET}).encode()
    (root / "group_vars" / "all" / "vault.yml").write_bytes(
        bytes(VaultLib(secrets=[("default", secret)]).encrypt(plain, secret))
    )
    asked = root / "asked"
    script = _executable(
        root / "vault-password.sh",
        f'#!/bin/sh\necho asked >> "{asked}"\nprintf "Vault password: " >&2\nread answer\n'
        '[ "$answer" = open ] && echo the-vault-password\n',
    )
    env = kit.environment(root, config="", TERM="xterm-256color", ANSIBLE_VAULT_PASSWORD_FILE=str(script), **LIVE)
    prompted = []

    def answer(chunk, output, master, process, vt):
        if not prompted and b"Vault password: " in output:
            prompted.append(len(output))
            os.write(master, b"open\n")

    ran = kit.at_a_terminal(_command(root), root, env, cols=100, rows=60, act=answer)
    assert ran.rc == 2 and prompted, ran.raw.decode(errors="replace")[-2000:]
    assert b"VMS" not in ran.raw[: prompted[0]] and b"VMS" in ran.raw[prompted[0] :]
    assert asked.read_text() == "asked\n"


def test_a_report_that_fits_is_redrawn_in_place_in_colour_and_leaves_only_the_finished_one(tmp_path, fleet) -> None:
    # At a colour terminal, as a person's is: a live smoke at one found the run log full of the report's colour codes.
    rc, vt, output, log = _at_a_terminal(tmp_path, fleet, 100, 60, colour=True)
    assert rc == 2, output.decode(errors="replace")
    assert output.count(b"\x1b[?2026h") >= 3, "the report was not drawn live"
    assert b"\x1b[1;36mVMS" in output
    report = _report(log)
    # Everything the live frames drew was erased: the screen and its scrollback hold the finished report alone.
    assert _shown(vt) == [line.rstrip() for line in report]
    assert not vt.unknown and vt.frame_wraps == 0
    assert max(vt.ups) < 60
    # The colour reaches the screen and never the log.
    assert not any(glyph in log for glyph in FRAMES) and "\x1b" not in log


def test_a_short_narrow_window_shows_its_top_never_wraps_and_says_how_much_more(tmp_path, fleet) -> None:
    # Shorter than the report and narrower than its lines: the frame is cut to both, and the line that says how
    # much more is left fits 60 columns.
    rc, vt, output, log = _at_a_terminal(tmp_path, fleet, 60, 14)
    assert rc == 2, output.decode(errors="replace")
    assert b"more lines, shown when the run ends" in output
    # Cut to the height, a frame never scrolls, and cut to the width it never wraps: nothing of it is left in the
    # scrollback.
    assert max(vt.ups) <= 13 and vt.frame_wraps == 0 and not vt.unknown
    # The finished report is printed whole, as text: the terminal wraps it, and nothing else is left.
    assert _shown(vt) == _wrapped(_report(log), 60)


@pytest.mark.parametrize(("before", "after"), [((100, 40), (70, 20)), ((70, 20), (100, 40))])
def test_a_resize_keeps_every_frame_in_the_scrollback_and_the_finished_report_whole(
    tmp_path, fleet, before, after
) -> None:
    rc, vt, output, log = _at_a_terminal(tmp_path, fleet, *before, resize=after)
    assert rc == 2, output.decode(errors="replace")
    report = _wrapped(_report(log), after[0])
    shown = _shown(vt)
    assert shown[-len(report) :] == report
    # The screen was pushed up rather than drawn over: what went before is whole lines, never a frame overwritten
    # part way, and the report is printed once.
    assert sum(1 for line in shown if line.startswith("agent-warehouse  ") and "·" in line) >= 2
    assert sum(1 for line in shown if re.match(r"exit \d", line)) == 1
    # A frame worked out for the old size can still be on its way when the window shrinks, and wrap: that is what
    # pushing the screen up and drawing afresh is for, so no count of wraps holds across a resize.


def test_ctrl_c_erases_the_live_region_and_ends_the_run(tmp_path, fleet) -> None:
    rc, vt, output, log = _at_a_terminal(tmp_path, fleet, 100, 60, interrupt=True)
    # 3 is the run's own failing; 2 would be a report, which an interrupted run never gives.
    assert rc == 3, output.decode(errors="replace")
    shown = "\n".join(_shown(vt))
    assert not any(glyph in shown for glyph in FRAMES), shown
    assert "interrupted" in shown.lower(), shown


def test_a_dumb_terminal_is_given_the_finished_report_only(tmp_path, fleet) -> None:
    rc, vt, output, log = _at_a_terminal(tmp_path, fleet, 100, 60, term="dumb")
    assert rc == 2, output.decode(errors="replace")
    assert b"\x1b" not in output
    assert _shown(vt) == [line.rstrip() for line in _report(log)]


def test_piped_the_finished_report_is_all_there_is(tmp_path, fleet) -> None:
    root = status_root(tmp_path, fleet)
    # No terminal at all, stdout and stderr one pipe: what is printed is the finished report, and nothing else.
    env = _env(root, ANSIBLE_LOG_PATH=str(root / "run.log"), TERM="xterm-256color", **LIVE)
    ran = kit.piped(_command(root), root, env)
    assert ran.rc == 2, ran.text
    assert ran.text.splitlines() == _report(ran.log)
    assert "\x1b" not in ran.text


def test_what_ansible_warns_of_while_it_reads_is_shown_above_the_frame(tmp_path, fleet) -> None:
    # The first run after a settings change asks Ansible, in this process and with the outline up: a warning of its
    # own, written where it writes, would land inside the frame.
    root = status_root(tmp_path, fleet)
    (root / "group_vars" / "all" / "warns.yml").write_text("twice: 1\ntwice: 2\n")
    env = kit.environment(root, config="", TERM="xterm-256color", ANSIBLE_NOCOLOR="1", **LIVE)
    ran = kit.at_a_terminal(_command(root), root, env, cols=100, rows=60)
    assert ran.rc == 2, ran.text
    shown = _shown(ran.screen)
    assert any("twice" in line for line in shown), shown
    assert ran.screen.frame_wraps == 0
