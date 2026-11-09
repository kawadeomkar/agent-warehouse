"""`just init` drawn live: its two playbooks as one run in parts, as the recipe runs them after just install.

A stand-in recipe with the justfile's trap runs the real init_settings.yml as test_init_settings.py runs it at a
terminal - its four files copies in the test's own directory, a refusing ssh first on PATH - typing its questions'
answers, then the real init_check.yml as test_init_check.py runs it, against the fake Proxmox with its own inventory
and the same refusing ssh. What a person sees: the questions with no frame flashed between them, the settings stage's
steps, the checks drawn as they are recorded and said whole in the summary, one NEXT STEPS at the end; and a check
that fails drawn as the failure, the last task naming nothing of its own (docs/architecture/custom-code.md §7.8).

A choice is asked as a menu (lib/claude_vm/tui/choose.py), as the live engine asks one at a terminal that can
draw: the VPN question, none or tailscale, answered by its first letter and Enter, never wrapped, erased once
answered and said on one line; and Ctrl-C at it, then C to go on - the question left for later - or A to abort the
run, as pause has it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import harness
import live_kit as kit
import pytest
import test_init_check as tic
import test_init_settings as tis

from claude_vm.tui import choose

# The questions, each typed once it has the terminal: a GitHub user, no VPN - a menu, its hint the sign it has the
# terminal: n moves to none, Enter takes it - the host, the token ID as it is, the secret.
ANSWERS = {
    tis.HOST_PROMPT.decode(): b"pve.lan\r",
    tis.TOKEN_PROMPT.decode(): b"\r",
    tis.GITHUB_PROMPT.decode(): b"octocat\r",
    choose.HINT: b"n\r",
    tis.SECRET_PROMPT.decode(): tis.SECRET.encode() + b"\r",
}
# pause's question after a Ctrl-C, which the menu raises as prompt_until does.
GO_ON = "Press 'C' to continue the play or 'A' to abort"


@pytest.fixture(scope="module")
def pve(tmp_path_factory):
    yield from harness._fake_pve(tmp_path_factory.mktemp("init_pve"))


def _init(root, pve, check_settings=None, answers=ANSWERS):
    (root / "settings").mkdir()
    files = tis._files(root / "settings")
    paths, env = tic._inventory_of(root / "checks", pve, "ssh", check_settings, tic.VAULT_HEADER, "127.0.0.1")
    # The recipe's two playbooks, each as it is: they join the run through the exported title and state.
    settings, checks = json.dumps(files | {"init_ask": True}), json.dumps(paths)
    parts = [
        f'"{kit.ANSIBLE_PLAYBOOK}" -i "{tis.INVENTORY}" -e \'{settings}\' "{tis.PLAYBOOK}"',
        f'"{kit.ANSIBLE_PLAYBOOK}" -i "{root / "checks" / "proxmox.yml"}" -e \'{checks}\' "{tic.PLAYBOOK}"',
    ]
    argv, environment, state = kit.recipe_in_parts(root, parts, "init", **env)
    ran = kit.at_a_terminal(argv, root, environment, prompts=answers, timeout=240)
    ran.state = state
    ran.local = Path(files["init_local_file"])
    return ran


@pytest.fixture(scope="module")
def passed(tmp_path_factory, pve):
    return _init(tmp_path_factory.mktemp("init_passed"), pve)


@pytest.fixture(scope="module")
def refused(tmp_path_factory, pve):
    return _init(tmp_path_factory.mktemp("init_refused"), pve, {"vault_proxmox_api_token_secret": "not-the-secret"})


@pytest.fixture(scope="module", params=["c", "a"], ids=["go-on", "abort"])
def interrupted(request, tmp_path_factory, pve):
    answers = {**ANSWERS, choose.HINT: b"\x03", GO_ON: request.param.encode()}
    ran = _init(tmp_path_factory.mktemp(f"init_interrupted_{request.param}"), pve, answers=answers)
    ran.key = request.param
    return ran


def test_init_is_one_run_its_questions_steps_and_its_checks_drawn_as_they_are_made(passed):
    assert passed.rc == 0, passed.text
    passed.assert_drawn_and_gone()
    frames = "\n".join(passed.frames)
    for step in ("Proxmox host", "API token", "GitHub", "secret", "vault"):
        assert re.search(rf"^     \S {step} ", frames, re.M), step
    assert re.search(r"^ ✓ settings ", frames, re.M), "the checks drew the settings, done"
    assert re.search(r"^     ✓ Proxmox VE \S+ at ", frames, re.M), "a check, as it was recorded"
    screen = passed.transcript
    assert screen.count("agent-warehouse  init · done in") == 1 and screen.count("##  NEXT STEPS") == 1, screen
    assert screen.index("agent-warehouse  init · done in") < screen.index("##  NEXT STEPS")
    assert "just deploy alpha" in screen.split("##  NEXT STEPS", 1)[1]
    checks = passed.table("\nCHECKS\n").splitlines()
    assert checks and all(line.startswith(" ✓ ") for line in checks), checks
    assert passed.screen.titles[-1] == "init · done"
    assert not passed.state.exists()


def test_a_check_that_fails_is_the_failure_and_the_last_task_names_nothing_of_its_own(refused):
    assert refused.rc == 2, refused.text
    refused.assert_drawn_and_gone()
    screen = refused.transcript
    assert re.search(r"^✗ checks · this machine: \S", screen, re.M), screen
    assert "Fail when a check found a problem" not in refused.above_summary("init")
    assert re.search(r"^agent-warehouse  init · failed after .* at checks", screen, re.M), screen
    checks = refused.table("\nCHECKS\n")
    assert re.search(r"^ ✗ ", checks, re.M), checks
    # NEXT STEPS keeps the record's lines: each problem under the line that counts them, with its indent.
    steps = screen.split("##  NEXT STEPS", 1)[1]
    assert re.search(r"^just init found 2 problems", steps, re.M) and re.search(r"^  !!   Proxmox at ", steps, re.M)


def test_a_choice_is_a_menu_answered_by_its_letter_and_said_on_one_line(passed):
    assert passed.rc == 0, passed.text
    # Drawn, and never as the typed prompt: its last words and their colon are the menu's place.
    assert choose.HINT.encode() in passed.raw and tis.TAILSCALE_PROMPT not in passed.raw
    assert passed.screen.frame_wraps == 0 and b"\x1b[?25l" not in passed.raw
    screen = passed.transcript
    assert "VPN: none" in screen, screen
    assert choose.HINT not in screen, "the menu is erased once answered"
    assert 'vpn: "none"' in passed.local.read_text()


def test_ctrl_c_at_the_menu_is_pause_s_c_goes_on_with_the_question_left_and_a_aborts(interrupted):
    assert GO_ON in interrupted.text
    if interrupted.key == "c":
        assert interrupted.rc == 0, interrupted.text
        assert "VPN: left for later" not in interrupted.transcript
        assert not [line for line in interrupted.local.read_text().splitlines() if line.startswith("vpn:")]
    else:
        assert interrupted.rc != 0, interrupted.text
        assert "user requested abort!" in interrupted.text
    assert choose.HINT not in interrupted.transcript
