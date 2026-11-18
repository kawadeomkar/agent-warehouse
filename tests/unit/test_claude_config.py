"""What just code carries of this machine's Claude Code beside the session copy: lib/claude_vm/claude_config.py, and
the modules claude_config_info (the reads) and claude_config_carry (the writes on the VM).

Every ~/.claude, checkout and VM here is built for the test in its own directory, never the developer's. The window
step that runs these (playbooks/tasks/claude_config_read.yml and claude_config_write.yml) is run in
test_code_session.py.
"""

from __future__ import annotations

import json
import os
import stat

import pytest
from conftest import run_main
from harness import load_module

from claude_vm import claude_config as core

reader = load_module("playbooks/library/claude_config_info.py")
writer = load_module("playbooks/library/claude_config_carry.py")

HERE = "/Users/me/.claude"
DEST = "/home/dev/.claude"
VM_CHECKOUT = "/home/dev/projects/demo"
VM_FOLDER = "-home-dev-projects-demo"
THERE = [{"path": VM_CHECKOUT, "name": "demo", "origin": "https://github.com/octo/demo.git"}]
HERE_DEMO = [
    {
        "path": "/Users/me/demo",
        "exists": True,
        "git": True,
        "toplevel": "/Users/me/demo",
        "origin": "git@github.com:octo/demo.git",
    }
]
MEMORY = {
    "-Users-me-demo": [
        {"path": "MEMORY.md", "size": 10, "digest": "a"},
        {"path": "notes/feedback.md", "size": 5, "digest": "b"},
    ]
}


# ------------------------------------------------------------------------------------------ the core ---
def test_a_repository_s_notes_go_to_the_memory_folder_of_the_vm_s_checkout_of_it():
    plan = core.claude_memory_plan(MEMORY, HERE_DEMO, THERE, HERE, DEST)
    scope = f"projects/{VM_FOLDER}/memory/"
    assert plan["folders"] == [
        {
            "repository": "octo/demo",
            "here": "-Users-me-demo",
            "there": VM_FOLDER,
            "source": "/Users/me/demo",
            "checkout": VM_CHECKOUT,
        }
    ]
    assert plan["scopes"] == [scope]
    assert plan["files"] == [
        {"source": "projects/-Users-me-demo/memory/MEMORY.md", "target": f"{scope}MEMORY.md"},
        {"source": "projects/-Users-me-demo/memory/notes/feedback.md", "target": f"{scope}notes/feedback.md"},
    ]
    assert core.claude_config_rsync(plan) == [
        "projects/-Users-me-demo/memory/MEMORY.md",
        "projects/-Users-me-demo/memory/notes/feedback.md",
    ]
    assert plan["ambiguous"] == []


def test_the_note_says_where_the_other_machine_s_paths_are_and_is_indexed_once():
    plan = core.claude_memory_plan(MEMORY, HERE_DEMO, THERE, HERE, DEST)
    scope = f"projects/{VM_FOLDER}/memory/"
    (note,) = plan["generated"]
    assert note["target"] == f"{scope}{core.NOTE}"
    assert "`/Users/me/demo` is `/home/dev/projects/demo` here." in note["content"]
    assert f"`{HERE}/projects/-Users-me-demo/memory/` is this folder, `{DEST}/{scope}`." in note["content"]
    assert plan["appended"] == {f"{scope}MEMORY.md": core.NOTE_LINE}
    # Without it, or for a path the note could not set in a code span, none.
    assert core.claude_memory_plan(MEMORY, HERE_DEMO, THERE, HERE, DEST, note=False)["generated"] == []
    odd = [dict(HERE_DEMO[0], path="/Users/me/a`b", toplevel="/Users/me/a`b")]
    plan = core.claude_memory_plan({"-Users-me-a-b": MEMORY["-Users-me-demo"]}, odd, THERE, HERE, DEST)
    assert plan["files"] and plan["generated"] == [] and plan["appended"] == {}


def test_without_a_memory_md_the_note_is_written_and_nothing_is_indexed():
    memory = {"-Users-me-demo": [{"path": "only.md", "size": 1, "digest": "c"}]}
    plan = core.claude_memory_plan(memory, HERE_DEMO, THERE, HERE, DEST)
    assert len(plan["generated"]) == 1 and plan["appended"] == {}


def test_notes_in_two_folders_here_of_one_repository_go_nowhere_and_are_named():
    other = dict(HERE_DEMO[0], path="/Users/me/demo-copy", toplevel="/Users/me/demo-copy")
    memory = dict(MEMORY, **{"-Users-me-demo-copy": [{"path": "MEMORY.md", "size": 1, "digest": "d"}]})
    plan = core.claude_memory_plan(memory, [HERE_DEMO[0], other], THERE, HERE, DEST)
    assert plan["folders"] == [] and plan["files"] == []
    assert plan["ambiguous"] == [{"repository": "octo/demo", "folders": ["-Users-me-demo", "-Users-me-demo-copy"]}]
    # A second checkout here without notes is no rival.
    plan = core.claude_memory_plan(MEMORY, [HERE_DEMO[0], other], THERE, HERE, DEST)
    assert [item["here"] for item in plan["folders"]] == ["-Users-me-demo"]


@pytest.mark.parametrize(
    "origin",
    [
        "https://github.com/someone-else/demo.git",  # another owner's repository of the same name
        "https://github.com/octo/demo-api.git",
        "",
    ],
)
def test_the_notes_of_a_repository_the_vm_has_no_checkout_of_stay_here(origin):
    here = [dict(HERE_DEMO[0], origin=origin)]
    assert core.claude_memory_plan(MEMORY, here, THERE, HERE, DEST)["folders"] == []


def test_a_vm_checkout_names_its_repository_only_by_a_github_origin():
    there = [dict(THERE[0], origin="/srv/git/octo/demo.git")]
    assert core.claude_memory_plan(MEMORY, HERE_DEMO, there, HERE, DEST)["folders"] == []


def test_a_checkout_here_that_is_gone_or_no_git_checkout_is_passed_over():
    for change in ({"exists": False}, {"git": False}, {"toplevel": ""}):
        assert core.claude_memory_plan(MEMORY, [dict(HERE_DEMO[0], **change)], THERE, HERE, DEST)["folders"] == []


def test_a_file_name_the_writer_would_refuse_is_never_planned():
    memory = {"-Users-me-demo": [{"path": "../escape.md"}, {"path": ".hidden"}, {"path": "a/../b"}, {"path": "ok.md"}]}
    plan = core.claude_memory_plan(memory, HERE_DEMO, THERE, HERE, DEST)
    assert [item["target"].rsplit("/", 1)[1] for item in plan["files"]] == ["ok.md"]


def test_the_checkouts_asked_about_are_those_of_the_vm_s_repositories_and_the_sessions():
    repo_paths = {"octo/Demo": ["/Users/me/demo", "/Users/me/old-demo"], "octo/other": ["/Users/me/other"]}
    assert core.claude_config_paths(repo_paths, THERE, ["/Users/me/demo", "/Users/me/demo/.claude/worktrees/x"]) == [
        "/Users/me/demo",
        "/Users/me/old-demo",
        "/Users/me/demo/.claude/worktrees/x",
    ]


# ------------------------------------------------------------------------------------------ the reader ---
def _memory(root, files):
    for path, data in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def test_memory_folders_are_listed_by_name_size_and_digest_and_no_link_is_followed(tmp_path):
    root = tmp_path / ".claude"
    _memory(
        root,
        {
            "projects/-Users-me-demo/memory/MEMORY.md": b"index\n",
            "projects/-Users-me-demo/memory/deep/note.md": b"x" * 7,
            "projects/-Users-me-demo/memory/.DS_Store": b"\0",
            "projects/-Users-me-empty/memory/.keep": b"",
            "projects/-Users-me-other/notes.txt": b"no memory folder",
        },
    )
    outside = tmp_path / "outside.md"
    outside.write_text("not a note\n")
    (root / "projects" / "-Users-me-demo" / "memory" / "link.md").symlink_to(outside)
    (root / "projects" / "-Users-me-demo" / "memory" / "folder").symlink_to(tmp_path, target_is_directory=True)
    (root / "projects" / "-Users-me-linked").mkdir()
    (root / "projects" / "-Users-me-linked" / "memory").symlink_to(root / "projects" / "-Users-me-demo" / "memory")
    found, over = reader.memory_folders(str(root))
    # A memory folder that holds nothing Claude Code writes is listed with none, so its copies go too.
    assert over == [] and sorted(found) == ["-Users-me-demo", "-Users-me-empty"] and found["-Users-me-empty"] == []
    assert [(item["path"], item["size"]) for item in found["-Users-me-demo"]] == [("MEMORY.md", 6), ("deep/note.md", 7)]
    assert found["-Users-me-demo"][0]["digest"] == reader.file_digest(
        str(root / "projects" / "-Users-me-demo" / "memory" / "MEMORY.md")
    )


def test_a_name_with_a_control_character_or_too_deep_is_passed_over(tmp_path):
    root = tmp_path / "memory"
    _memory(root, {"a\nb.md": b"1", "1/2/3/4/5/deep.md": b"2", "1/2/3/4/ok.md": b"3"})
    assert [item["path"] for item in reader.plain_files(str(root))] == ["1/2/3/4/ok.md"]


def test_a_memory_folder_past_its_bound_is_left_whole_and_named(tmp_path, monkeypatch):
    root = tmp_path / ".claude"
    _memory(root, {f"projects/-Users-me-big/memory/{n}.md": b"x" for n in range(3)})
    _memory(root, {"projects/-Users-me-demo/memory/MEMORY.md": b"x"})
    monkeypatch.setattr(reader, "MEMORY_FILES", 2)
    found, over = reader.memory_folders(str(root))
    assert sorted(found) == ["-Users-me-demo"] and over == ["-Users-me-big"]


def test_the_reader_returns_names_and_digests_and_nothing_a_note_says(tmp_path, patch_module):
    root = tmp_path / ".claude"
    _memory(root, {"projects/-Users-me-demo/memory/MEMORY.md": b"SECRET-NOTE-TEXT\n"})
    (tmp_path / ".claude.json").write_text(json.dumps({"githubRepoPaths": {"octo/demo": ["/Users/me/demo"]}}))
    result = run_main(reader.main, {"path": str(root), "memory": True, "repo_paths": True})
    assert not result["changed"] and result["repo_paths"] == {"octo/demo": ["/Users/me/demo"]}
    assert "SECRET-NOTE-TEXT" not in json.dumps(result)
    assert list(result["memory"]) == ["-Users-me-demo"]
    assert run_main(reader.main, {"path": str(root)})["memory"] == {}


# ------------------------------------------------------------------------------------------ the writer ---
SCOPE = f"projects/{VM_FOLDER}/memory/"


def _world(tmp_path, files):
    """A mirror holding ``files`` ({path under this machine's ~/.claude: bytes}), and the paths the writer takes."""
    mirror = tmp_path / "ws" / "mirror"
    _memory(mirror, files)
    return mirror, tmp_path / "vm" / ".claude", tmp_path / "ws" / "manifest.json"


def _carry(mirror, dest, manifest, files, scopes=(SCOPE,), check=False, **kwargs):
    items = [{"source": source, "target": f"{SCOPE}{source.rsplit('/memory/', 1)[1]}"} for source in files]
    return writer.carry(str(dest), str(mirror), str(manifest), list(scopes), items, check=check, **kwargs)


SOURCES = {"projects/-Users-me-demo/memory/MEMORY.md": b"- [x](x.md)\n", "projects/-Users-me-demo/memory/x.md": b"x\n"}


def test_files_are_written_with_their_modes_and_recorded_and_a_second_run_changes_nothing(tmp_path):
    mirror, dest, manifest = _world(tmp_path, SOURCES)
    result, changed = _carry(mirror, dest, manifest, SOURCES)
    assert changed and result["written"] == [f"{SCOPE}MEMORY.md", f"{SCOPE}x.md"] and result["left"] == []
    note = dest / SCOPE / "x.md"
    assert note.read_bytes() == b"x\n" and stat.S_IMODE(note.stat().st_mode) == 0o600
    assert stat.S_IMODE((dest / SCOPE).stat().st_mode) == 0o700
    recorded = json.loads(manifest.read_text())
    assert recorded["format"] == 1 and sorted(recorded["files"]) == [f"{SCOPE}MEMORY.md", f"{SCOPE}x.md"]
    again, changed = _carry(mirror, dest, manifest, SOURCES)
    assert not changed and again["written"] == [] and again["unchanged"] == 2


def test_a_file_changed_on_the_vm_is_left_and_one_still_as_written_is_replaced(tmp_path):
    mirror, dest, manifest = _world(tmp_path, SOURCES)
    _carry(mirror, dest, manifest, SOURCES)
    (dest / SCOPE / "MEMORY.md").write_text("- [x](x.md)\n- [vm](vm.md)\n")
    _memory(
        mirror,
        {"projects/-Users-me-demo/memory/MEMORY.md": b"new index\n", "projects/-Users-me-demo/memory/x.md": b"y\n"},
    )
    result, _ = _carry(mirror, dest, manifest, SOURCES)
    assert result["left"] == [f"{SCOPE}MEMORY.md"] and result["written"] == [f"{SCOPE}x.md"]
    assert (dest / SCOPE / "MEMORY.md").read_text() == "- [x](x.md)\n- [vm](vm.md)\n"
    assert (dest / SCOPE / "x.md").read_text() == "y\n"


def test_a_file_the_vm_made_itself_is_never_overwritten(tmp_path):
    mirror, dest, manifest = _world(tmp_path, SOURCES)
    _memory(dest, {f"{SCOPE}x.md": b"the VM's own\n"})
    result, _ = _carry(mirror, dest, manifest, SOURCES)
    assert result["left"] == [f"{SCOPE}x.md"] and (dest / SCOPE / "x.md").read_text() == "the VM's own\n"


def test_a_file_gone_here_is_removed_there_only_while_it_is_as_written(tmp_path):
    mirror, dest, manifest = _world(tmp_path, SOURCES)
    _carry(mirror, dest, manifest, SOURCES)
    (dest / SCOPE / "MEMORY.md").write_text("changed on the VM\n")
    result, changed = _carry(mirror, dest, manifest, {})
    assert changed and result["removed"] == [f"{SCOPE}x.md"] and not (dest / SCOPE / "x.md").exists()
    assert (dest / SCOPE / "MEMORY.md").read_text() == "changed on the VM\n"
    assert json.loads(manifest.read_text())["files"] == {}


def test_a_scope_not_managed_this_run_keeps_its_files_and_records(tmp_path):
    mirror, dest, manifest = _world(tmp_path, SOURCES)
    _carry(mirror, dest, manifest, SOURCES)
    result, changed = _carry(mirror, dest, manifest, {}, scopes=(), prune=False)
    assert not changed and result["removed"] == [] and (dest / SCOPE / "x.md").exists()
    assert len(json.loads(manifest.read_text())["files"]) == 2


def test_the_note_is_written_and_memory_md_gets_its_line_once(tmp_path):
    mirror, dest, manifest = _world(tmp_path, SOURCES)
    generated = [{"target": f"{SCOPE}{core.NOTE}", "content": "the note\n"}]
    appended = {f"{SCOPE}MEMORY.md": core.NOTE_LINE}
    _carry(mirror, dest, manifest, SOURCES, generated=generated, appended=appended)
    assert (dest / SCOPE / core.NOTE).read_text() == "the note\n"
    assert (dest / SCOPE / "MEMORY.md").read_text() == f"- [x](x.md)\n{core.NOTE_LINE}\n"
    again, changed = _carry(mirror, dest, manifest, SOURCES, generated=generated, appended=appended)
    assert not changed and again["unchanged"] == 3
    # An index that ends without a newline, or holds the line already, gets it once.
    assert writer._with_line(b"a", "b") == b"a\nb\n" and writer._with_line(b"a\nb\n", "b") == b"a\nb\n"


@pytest.mark.parametrize(
    "target",
    [
        "projects/-home-dev-projects-demo/memory/../../../settings.json",
        "projects/-home-dev-projects-demo/memory/.hidden",
        "/etc/passwd",
        "projects/-home-dev-projects-demo/memory/",
        "projects/-home-dev-projects-demo/memory/a\nb",
        "CLAUDE.md",
    ],
)
def test_a_target_outside_its_scope_or_not_a_plain_path_is_never_written(tmp_path, target):
    mirror, dest, manifest = _world(tmp_path, SOURCES)
    items = [{"source": "projects/-Users-me-demo/memory/x.md", "target": target}]
    result, _ = writer.carry(str(dest), str(mirror), str(manifest), [SCOPE], items)
    assert result["written"] == [] and not dest.exists()


@pytest.mark.parametrize("scope", ["projects/../memory/", "projects/-x/notes/", "", "/", "settings.json"])
def test_a_scope_of_another_form_manages_nothing(tmp_path, scope):
    mirror, dest, manifest = _world(tmp_path, SOURCES)
    items = [{"source": "projects/-Users-me-demo/memory/x.md", "target": f"{scope}x.md"}]
    result, _ = writer.carry(str(dest), str(mirror), str(manifest), [scope], items)
    assert result["written"] == []


def test_a_link_on_the_way_or_in_a_file_s_place_is_never_followed(tmp_path):
    mirror, dest, manifest = _world(tmp_path, SOURCES)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (dest / "projects").mkdir(parents=True)
    (dest / "projects" / VM_FOLDER).symlink_to(elsewhere, target_is_directory=True)
    result, _ = _carry(mirror, dest, manifest, SOURCES)
    assert sorted(result["left"]) == [f"{SCOPE}MEMORY.md", f"{SCOPE}x.md"] and list(elsewhere.iterdir()) == []
    (dest / "projects" / VM_FOLDER).unlink()
    (dest / SCOPE).mkdir(parents=True)
    (dest / SCOPE / "x.md").symlink_to(elsewhere / "target.md")
    result, _ = _carry(mirror, dest, manifest, SOURCES)
    assert result["left"] == [f"{SCOPE}x.md"] and not (elsewhere / "target.md").exists()


def test_a_source_rsync_did_not_bring_is_named_and_written_nowhere(tmp_path):
    mirror, dest, manifest = _world(tmp_path, {"projects/-Users-me-demo/memory/x.md": b"x\n"})
    result, _ = _carry(mirror, dest, manifest, SOURCES)
    assert result["unreadable"] == [f"{SCOPE}MEMORY.md"] and result["written"] == [f"{SCOPE}x.md"]


def test_check_mode_writes_nothing(tmp_path):
    mirror, dest, manifest = _world(tmp_path, SOURCES)
    result, changed = _carry(mirror, dest, manifest, SOURCES, check=True)
    assert changed and len(result["written"]) == 2 and not dest.exists() and not manifest.exists()
    assert sorted(path.name for path in (mirror / "projects" / "-Users-me-demo" / "memory").iterdir()) == [
        "MEMORY.md",
        "x.md",
    ]


def test_what_left_the_plan_leaves_the_mirror(tmp_path):
    mirror, dest, manifest = _world(tmp_path, SOURCES)
    _carry(mirror, dest, manifest, {"projects/-Users-me-demo/memory/x.md": b""})
    assert [path.name for path in (mirror / "projects" / "-Users-me-demo" / "memory").iterdir()] == ["x.md"]


def test_the_writer_returns_paths_and_nothing_a_file_holds(tmp_path, patch_module):
    mirror, dest, manifest = _world(tmp_path, {"projects/-Users-me-demo/memory/x.md": b"SECRET-NOTE-TEXT\n"})
    args = {
        "dest": str(dest),
        "mirror": str(mirror),
        "manifest": str(manifest),
        "scopes": [SCOPE],
        "files": [{"source": "projects/-Users-me-demo/memory/x.md", "target": f"{SCOPE}x.md"}],
    }
    result = run_main(writer.main, args)
    assert result["changed"] and result["written"] == [f"{SCOPE}x.md"]
    assert "SECRET-NOTE-TEXT" not in json.dumps(result)
    assert os.path.exists(dest / SCOPE / "x.md")


# ------------------------------------------------------------------------------------- what review found ---
def test_a_write_that_fails_is_named_left_unrecorded_and_the_rest_goes_on(tmp_path):
    mirror, dest, manifest = _world(tmp_path, SOURCES)
    (dest / "projects").mkdir(parents=True)
    # A file where one memory folder's parent goes: every write under it fails; the other folder's and the run do not.
    (dest / "projects" / VM_FOLDER).write_text("in the way\n")
    other = "projects/-home-dev-projects-other/memory/"
    items = [{"source": source, "target": f"{SCOPE}{source.rsplit('/memory/', 1)[1]}"} for source in SOURCES]
    items.append({"source": "projects/-Users-me-demo/memory/x.md", "target": f"{other}x.md"})
    result, changed = writer.carry(str(dest), str(mirror), str(manifest), [SCOPE, other], items)
    assert sorted(result["not_written"]) == [f"{SCOPE}MEMORY.md", f"{SCOPE}x.md"]
    assert result["written"] == [f"{other}x.md"]
    assert list(json.loads(manifest.read_text())["files"]) == [f"{other}x.md"]


def test_a_file_planned_that_rsync_did_not_bring_is_never_removed_there(tmp_path):
    mirror, dest, manifest = _world(tmp_path, SOURCES)
    _carry(mirror, dest, manifest, SOURCES)
    for path in (mirror / "projects" / "-Users-me-demo" / "memory").iterdir():
        path.unlink()
    result, _ = _carry(mirror, dest, manifest, SOURCES, prune=False)
    assert sorted(result["unreadable"]) == [f"{SCOPE}MEMORY.md", f"{SCOPE}x.md"] and result["removed"] == []
    assert (dest / SCOPE / "x.md").exists() and len(json.loads(manifest.read_text())["files"]) == 2


def test_a_mirror_or_manifest_folder_that_is_a_link_is_refused_whole(tmp_path):
    mirror, dest, manifest = _world(tmp_path, SOURCES)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_text("not the mirror's\n")
    os.rename(mirror, tmp_path / "real-mirror")
    mirror.symlink_to(elsewhere, target_is_directory=True)
    result, changed = _carry(mirror, dest, manifest, SOURCES)
    assert result["refused"] and not changed and (elsewhere / "keep.txt").exists() and not dest.exists()


def test_an_emptied_memory_folder_removes_its_copies_and_writes_no_note(tmp_path):
    root = tmp_path / ".claude"
    (root / "projects" / "-Users-me-demo" / "memory").mkdir(parents=True)
    found, _ = reader.memory_folders(str(root))
    assert found == {"-Users-me-demo": []}
    plan = core.claude_memory_plan(found, HERE_DEMO, THERE, HERE, DEST)
    assert plan["scopes"] == [SCOPE] and plan["files"] == [] and plan["generated"] == []
    # Beside a second checkout here whose folder holds notes, the emptied one is no rival.
    other = dict(HERE_DEMO[0], path="/Users/me/demo-copy", toplevel="/Users/me/demo-copy")
    plan = core.claude_memory_plan(
        dict(found, **{"-Users-me-demo-copy": MEMORY["-Users-me-demo"]}), [HERE_DEMO[0], other], THERE, HERE, DEST
    )
    assert [item["here"] for item in plan["folders"]] == ["-Users-me-demo-copy"]


# ------------------------------------------------------------------------------ instructions and settings ---
SETTINGS_HERE = {
    "model": "claude-opus-5-5",
    "effortLevel": "xhigh",
    "theme": "dark",
    "permissions": {"allow": ["Bash(just test:*)"], "defaultMode": "auto", "additionalDirectories": ["/Users/me/x"]},
    "autoMode": {"allow": ["$defaults"], "environment": ["- trusted repo: octo/demo"]},
    "env": {"ANTHROPIC_API_KEY": "SECRET-KEY-VALUE"},
    "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "~/.claude/tools/x.sh"}]}]},
    "apiKeyHelper": "~/bin/key",
    "statusLine": {"type": "command", "command": "jq ."},
    "somethingNew": 1,
}


def test_a_setting_never_carried_is_refused_by_name_and_one_that_runs_a_program_is_not():
    assert core.claude_settings_refused(["env", "permissions", "statusLine", "hooks", "a b", "apiKeyHelper"]) == [
        "a b",
        "apiKeyHelper",
        "env",
        "permissions",
    ]
    asked = core.claude_settings_asked(["statusLine", "env"])
    assert asked["keys"][-1] == "statusLine" and "env" not in asked["keys"]
    assert asked["parts"] == {
        "permissions": ["allow", "ask", "deny", "defaultMode"],
        "autoMode": ["allow", "soft_deny"],
    }


def test_the_settings_carried_are_the_list_s_and_the_rest_is_said_by_why():
    found = reader.settings_from(SETTINGS_HERE, core.claude_settings_asked())
    plan = core.claude_settings_plan(found, managed=["autoUpdatesChannel", "env", "theme"])
    assert plan["values"] == {
        "model": "claude-opus-5-5",
        "effortLevel": "xhigh",
        "permissions.allow": ["Bash(just test:*)"],
        "permissions.defaultMode": "auto",
        "autoMode.allow": ["$defaults"],
    }
    assert "theme" not in plan["items"] and "permissions.deny" in plan["items"]
    assert plan["left"] == {
        "role": ["theme"],
        "program": ["hooks", "statusLine"],
        "never": ["apiKeyHelper", "autoMode.environment", "env", "permissions.additionalDirectories"],
        "other": ["somethingNew"],
    }
    # claude_settings_sync_keys carries a program's setting; the role's own key is still the role's.
    plan = core.claude_settings_plan(
        reader.settings_from(SETTINGS_HERE, core.claude_settings_asked(["statusLine"])),
        managed=["permissions"],
        extra=["statusLine"],
    )
    assert plan["values"]["statusLine"] == {"type": "command", "command": "jq ."}
    assert not any(item.startswith("permissions.") for item in plan["values"])
    assert "permissions" in plan["left"]["role"]


def test_the_reader_returns_a_value_only_where_it_was_asked_for(tmp_path, patch_module):
    root = tmp_path / ".claude"
    root.mkdir()
    (root / "settings.json").write_text(json.dumps(SETTINGS_HERE))
    result = run_main(reader.main, {"path": str(root), "settings": core.claude_settings_asked()})
    assert "SECRET-KEY-VALUE" not in json.dumps(result) and "~/bin/key" not in json.dumps(result)
    assert result["settings"]["values"]["model"] == "claude-opus-5-5"
    assert "env" in result["settings"]["present"]
    # A file that is no JSON object, or a link, is said so, and nothing of it is read.
    (root / "settings.json").write_text("[1, 2]")
    assert run_main(reader.main, {"path": str(root), "settings": core.claude_settings_asked()})["settings"] == {
        "present": [],
        "values": {},
        "parts_left": [],
        "unreadable": True,
    }
    (root / "settings.json").unlink()
    (root / "settings.json").symlink_to(tmp_path / "elsewhere.json")
    assert reader.settings(str(root), core.claude_settings_asked())["unreadable"] is True


def test_the_global_instructions_and_rules_are_listed_with_the_imports_named(tmp_path):
    root = tmp_path / ".claude"
    _memory(
        root,
        {
            "CLAUDE.md": b"Be brief. See @~/notes/style.md and @notes/x.md, not `@code`.\n",
            "rules/python.md": b"x",
            "rules/.hidden": b"y",
        },
    )
    found, imports = reader.instructions(str(root))
    assert [item["path"] for item in found] == ["CLAUDE.md", "rules/python.md"]
    assert imports == ["~/notes/style.md", "notes/x.md"]
    plan = core.claude_config_plan(
        {"instructions": found, "imports": imports}, [], THERE, HERE, DEST, {"instructions": True}
    )
    assert plan["files"] == [
        {"source": "CLAUDE.md", "target": "CLAUDE.md"},
        {"source": "rules/python.md", "target": "rules/python.md"},
    ]
    assert plan["scopes"] == ["CLAUDE.md", "rules/"]
    # claude_code_global_instructions makes CLAUDE.md the role's: never carried, never removed.
    owned = core.claude_config_plan(
        {"instructions": found}, [], THERE, HERE, DEST, {"instructions": True, "instructions_owned": True}
    )
    assert owned["files"] == [{"source": "rules/python.md", "target": "rules/python.md"}]
    assert owned["scopes"] == ["rules/"] and owned["instructions"]["owned"]


def test_the_writer_writes_claude_md_and_rules_and_no_other_file_at_the_top(tmp_path):
    files = {"CLAUDE.md": b"Be brief.\n", "rules/a.md": b"a\n", "settings.json": b"{}"}
    mirror, dest, manifest = _world(tmp_path, files)
    items = [{"source": path, "target": path} for path in ("CLAUDE.md", "rules/a.md", "settings.json")]
    result, _ = writer.carry(str(dest), str(mirror), str(manifest), ["CLAUDE.md", "rules/", "settings.json"], items)
    assert result["written"] == ["CLAUDE.md", "rules/a.md"] and not (dest / "settings.json").exists()


def test_a_settings_file_here_that_cannot_be_read_changes_nothing_there():
    plan = core.claude_settings_plan({"unreadable": True}, extra=["hooks"])
    assert plan["items"] == [] and plan["values"] == {} and plan["unreadable"]


def test_a_program_s_setting_no_longer_named_is_an_item_with_no_value_so_it_goes_there():
    found = reader.settings_from(SETTINGS_HERE, core.claude_settings_asked())
    plan = core.claude_settings_plan(found)
    assert {"hooks", "statusLine"} <= set(plan["items"])
    assert "hooks" not in plan["values"] and "statusLine" not in plan["values"]
    # Named, it is carried; the role's own key never an item.
    plan = core.claude_settings_plan(found, managed=["hooks"], extra=["statusLine"])
    assert "hooks" not in plan["items"] and plan["items"].count("statusLine") == 1


def _settings(dest, values, items=None, recorded=None, check=False):
    items = list(values) if items is None else items
    return writer.carry_settings(str(dest), {"values": values, "items": items}, recorded or {}, check)


def test_settings_are_set_key_by_key_and_every_other_key_of_the_file_is_kept(tmp_path):
    dest = tmp_path / ".claude"
    dest.mkdir()
    (dest / "settings.json").write_text(json.dumps({"autoUpdatesChannel": "stable", "permissions": {"ask": ["x"]}}))
    result, copies, changed = _settings(dest, {"model": "opus", "permissions.allow": ["Bash(ls)"]})
    assert changed and result["written"] == ["model", "permissions.allow"]
    text = (dest / "settings.json").read_text()
    assert json.loads(text) == {
        "autoUpdatesChannel": "stable",
        "permissions": {"ask": ["x"], "allow": ["Bash(ls)"]},
        "model": "opus",
    }
    assert text.startswith('{\n  "autoUpdatesChannel"') and text.endswith("}\n")
    assert stat.S_IMODE((dest / "settings.json").stat().st_mode) == 0o600
    values = {"model": "opus", "permissions.allow": ["Bash(ls)"]}
    again, copies_again, changed = _settings(dest, values, recorded=copies)
    assert not changed and again["written"] == [] and copies_again == copies


def test_a_setting_changed_on_the_vm_is_left_and_one_gone_here_is_removed_while_as_carried(tmp_path):
    dest = tmp_path / ".claude"
    dest.mkdir()
    _, copies, _ = _settings(dest, {"model": "opus", "theme": "dark", "permissions.allow": ["a"]})
    data = json.loads((dest / "settings.json").read_text())
    data["model"] = "sonnet"  # /model on the VM
    (dest / "settings.json").write_text(json.dumps(data))
    items = ["model", "theme", "permissions.allow"]
    result, copies, _ = _settings(dest, {"model": "fable"}, items=items, recorded=copies)
    assert result["left"] == ["model"] and sorted(result["removed"]) == ["permissions.allow", "theme"]
    assert json.loads((dest / "settings.json").read_text()) == {"model": "sonnet"}
    # The one left keeps its record: were the VM's value to come back to what was carried, it is carried again.
    assert list(copies) == ["model"]


def test_a_program_s_setting_taken_out_of_the_list_is_removed_while_as_carried_and_left_once_changed(tmp_path):
    dest = tmp_path / ".claude"
    dest.mkdir()
    hook = {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]}
    _, copies, _ = _settings(dest, {"hooks": hook, "statusLine": {"type": "command", "command": "jq ."}})
    data = json.loads((dest / "settings.json").read_text())
    data["statusLine"] = {"type": "command", "command": "made on the VM"}
    (dest / "settings.json").write_text(json.dumps(data))
    result, copies, _ = _settings(dest, {}, items=["hooks", "statusLine"], recorded=copies)
    assert result["removed"] == ["hooks"] and result["left"] == []
    assert json.loads((dest / "settings.json").read_text()) == {"statusLine": data["statusLine"]}


def test_the_settings_are_written_as_the_role_writes_them_so_its_next_configure_changes_nothing(tmp_path):
    dest = tmp_path / ".claude"
    dest.mkdir()
    (dest / "settings.json").write_text(json.dumps({"theme": "dark", "attribution": {"pr": "made with \u2728"}}))
    _settings(dest, {"model": "opus", "modelSettings": {"z": 1, "a": "\u00e9"}})
    text = (dest / "settings.json").read_text()
    data = json.loads(text)
    # to_nice_json(indent=2): sorted keys, ASCII escapes, ", " and ": ", then the role's newline.
    assert text == json.dumps(data, indent=2, sort_keys=True, separators=(",", ": "), ensure_ascii=True) + "\n"
    assert text.isascii() and data["attribution"]["pr"] == "made with \u2728"


def test_a_lone_surrogate_in_a_value_is_carried_never_the_module_s_end(tmp_path):
    dest = tmp_path / ".claude"
    dest.mkdir()
    lone = json.loads('"split \\ud83d emoji"')
    result, _, changed = _settings(dest, {"model": lone})
    assert changed and result["written"] == ["model"]
    assert json.loads((dest / "settings.json").read_text())["model"] == lone


def test_a_settings_file_that_is_no_object_or_a_link_is_left_whole(tmp_path):
    dest = tmp_path / ".claude"
    dest.mkdir()
    (dest / "settings.json").write_text("[]")
    result, _, changed = _settings(dest, {"model": "opus"})
    assert result["unreadable"] and not changed and (dest / "settings.json").read_text() == "[]"
    (dest / "settings.json").unlink()
    (dest / "settings.json").symlink_to(tmp_path / "target.json")
    result, _, _ = _settings(dest, {"model": "opus"})
    assert result["unreadable"] and not (tmp_path / "target.json").exists()


def test_the_settings_record_is_kept_beside_the_files_in_the_manifest(tmp_path):
    mirror, dest, manifest = _world(tmp_path, SOURCES)
    settings = {"values": {"model": "opus"}, "items": ["model"]}
    result, changed = _carry(mirror, dest, manifest, SOURCES, settings=settings)
    assert changed and result["settings"]["written"] == ["model"]
    recorded = json.loads(manifest.read_text())
    assert list(recorded["settings"]) == ["model"] and len(recorded["files"]) == 2
    again, changed = _carry(mirror, dest, manifest, SOURCES, settings=settings)
    assert not changed and again["settings"]["written"] == []


def test_check_mode_sets_no_setting(tmp_path):
    dest = tmp_path / ".claude"
    result, _, changed = _settings(dest, {"model": "opus"}, check=True)
    assert changed and result["written"] == ["model"] and not dest.exists()
