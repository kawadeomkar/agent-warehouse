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
    found, imports, over = reader.instructions(str(root))
    assert [item["path"] for item in found] == ["CLAUDE.md", "rules/python.md"] and not over
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


def test_a_program_s_setting_is_an_item_only_while_named_and_the_role_s_keys_go_to_the_writer():
    found = reader.settings_from(SETTINGS_HERE, core.claude_settings_asked())
    plan = core.claude_settings_plan(found, managed=["env"])
    assert not {"hooks", "statusLine"} & set(plan["items"]) and plan["managed"] == ["env"]
    # Named, it is carried; the role's own key never an item.
    plan = core.claude_settings_plan(found, managed=["hooks"], extra=["statusLine"])
    assert "hooks" not in plan["items"] and plan["items"].count("statusLine") == 1


def test_a_setting_taken_out_of_the_list_goes_from_the_vm_while_as_carried_whatever_it_is(tmp_path):
    dest = tmp_path / ".claude"
    dest.mkdir()
    _, copies, _ = writer.carry_settings(
        str(dest),
        {"values": {"spinnerTipsEnabled": False, "model": "opus"}, "items": ["spinnerTipsEnabled", "model"]},
        {},
        False,
    )
    # No longer named: not one of this run's items, so it goes, as carried; model, still one, stays.
    result, copies, changed = writer.carry_settings(
        str(dest), {"values": {"model": "opus"}, "items": ["model"], "managed": []}, copies, False
    )
    assert changed and result["removed"] == ["spinnerTipsEnabled"] and list(copies) == ["model"]
    assert json.loads((dest / "settings.json").read_text()) == {"model": "opus"}


def test_a_setting_the_role_now_writes_loses_its_record_and_keeps_its_value(tmp_path):
    dest = tmp_path / ".claude"
    dest.mkdir()
    _, copies, _ = _settings(dest, {"model": "opus", "permissions.allow": ["Bash(ls)"]})
    # claude_code_settings names model and permissions now: the role's, never removed by the carry.
    result, copies, _ = writer.carry_settings(
        str(dest), {"values": {}, "items": [], "managed": ["model", "permissions"]}, copies, False
    )
    assert result["removed"] == [] and copies == {}
    assert json.loads((dest / "settings.json").read_text()) == {"model": "opus", "permissions": {"allow": ["Bash(ls)"]}}


def test_nothing_carried_is_removed_while_this_machine_s_settings_could_not_be_read(tmp_path):
    dest = tmp_path / ".claude"
    dest.mkdir()
    _, copies, _ = _settings(dest, {"model": "opus"})
    plan = core.claude_settings_plan({"unreadable": True})
    result, kept, changed = writer.carry_settings(str(dest), plan, copies, False)
    assert not changed and result["removed"] == [] and kept == copies
    assert json.loads((dest / "settings.json").read_text()) == {"model": "opus"}


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


def test_a_lone_surrogate_in_a_value_or_a_prompt_is_carried_never_the_module_s_end(tmp_path):
    dest = tmp_path / ".claude"
    dest.mkdir()
    lone = json.loads('"split \\ud83d emoji"')
    result, _, changed = _settings(dest, {"model": lone})
    assert changed and result["written"] == ["model"]
    assert json.loads((dest / "settings.json").read_text())["model"] == lone
    line = dict(_line(SID, 1, lone), project=VM_CHECKOUT)
    assert writer.carry_history(str(dest), [line], False) == 1
    assert json.loads((dest / "history.jsonl").read_text())["display"] == lone


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


# ------------------------------------------------------------------------------------------- plugins ---
PLUGINS_HERE = {
    "enabled": ["lsp@octo-plugins", "pyright-lsp@claude-plugins-official", "own@local-market"],
    "marketplaces": {
        "octo-plugins": "octo/plugins",
        "claude-plugins-official": "anthropics/claude-plugins-official",
        "local-market": "",
    },
}


def test_the_plugins_missing_on_the_vm_are_installed_with_the_marketplaces_they_need():
    there = {
        "readable": True,
        "installed": ["pyright-lsp@claude-plugins-official"],
        "marketplaces": ["claude-plugins-official"],
    }
    plan = core.claude_plugins_plan(PLUGINS_HERE, there)
    assert plan == {
        "install": ["lsp@octo-plugins"],
        "marketplaces": ["octo/plugins"],
        "left": ["own@local-market"],
        "unread": False,
    }
    # A VM whose Claude Code did not answer installs nothing; one that has them all, nothing either.
    assert core.claude_plugins_plan(PLUGINS_HERE, {"readable": False})["unread"] is True
    assert core.claude_plugins_plan({"enabled": []}, {})["unread"] is False


@pytest.mark.parametrize("plugin", ["-x@m", "a@-m", "a", "a@b c", "a@b\n"])
def test_an_id_that_could_read_as_an_option_or_is_no_plugin_s_is_left(plugin):
    plan = core.claude_plugins_plan({"enabled": [plugin], "marketplaces": {}}, {"readable": True})
    assert plan["install"] == [] and plan["left"] == [plugin]


def test_the_plugins_enabled_here_for_the_user_are_read_from_claude_code_s_files(tmp_path):
    root = tmp_path / ".claude"
    (root / "plugins").mkdir(parents=True)
    installed = {
        "version": 2,
        "plugins": {
            "lsp@octo-plugins": [{"scope": "user", "installPath": "/x"}],
            "off@octo-plugins": [{"scope": "user"}],
            "project@octo-plugins": [{"scope": "project", "projectPath": "/Users/me/demo"}],
        },
    }
    (root / "plugins" / "installed_plugins.json").write_text(json.dumps(installed))
    (root / "plugins" / "known_marketplaces.json").write_text(
        json.dumps(
            {
                "octo-plugins": {"source": {"source": "github", "repo": "octo/plugins"}},
                "local": {"source": {"source": "directory", "path": "/Users/me/m"}},
            }
        )
    )
    (root / "settings.json").write_text(
        json.dumps(
            {"enabledPlugins": {"lsp@octo-plugins": True, "off@octo-plugins": False, "project@octo-plugins": True}}
        )
    )
    assert reader.plugins_here(str(root)) == {
        "enabled": ["lsp@octo-plugins"],
        "marketplaces": {"octo-plugins": "octo/plugins", "local": ""},
    }


def test_the_vm_s_claude_code_is_asked_what_it_has_and_one_that_does_not_answer_is_unread(tmp_path):
    claude = tmp_path / "claude"
    claude.write_text(
        "#!/bin/sh\n"
        'case "$2 $3" in\n'
        '  "list --json") echo \'[{"id":"lsp@octo-plugins","scope":"user"},{"id":"p@x","scope":"project"}]\' ;;\n'
        '  "marketplace list") echo \'[{"name":"octo-plugins","source":"github","repo":"octo/plugins"}]\' ;;\n'
        "esac\n"
    )
    claude.chmod(0o755)
    found = reader.plugins_there([str(tmp_path / "missing"), str(claude)])
    assert found == {
        "readable": True,
        "claude": str(claude),
        "installed": ["lsp@octo-plugins"],
        "marketplaces": ["octo-plugins"],
    }
    claude.write_text("#!/bin/sh\necho 'not json'\n")
    assert reader.plugins_there([str(claude)])["readable"] is False
    assert reader.plugins_there([str(tmp_path / "missing")]) == {
        "readable": False,
        "claude": "",
        "installed": [],
        "marketplaces": [],
    }


# --------------------------------------------------------------------------------------- the history ---
SID = "0a608ad8-1b2c-4d3e-8f90-123456789abc"
OTHER_SID = "bc0e637b-2c3d-4e4f-9a01-23456789abcd"


def _line(sid, when, text="run the tests"):
    return {"display": text, "pastedContents": {}, "timestamp": when, "project": "/Users/me/demo", "sessionId": sid}


def test_the_history_of_the_sessions_copied_is_appended_with_the_vm_s_checkout_in_time_order():
    lines = [_line(SID, 20), _line(OTHER_SID, 5), _line(SID, 3)]
    plan = core.claude_history_plan(lines, {SID: VM_CHECKOUT})
    assert plan == [dict(_line(SID, 3), project=VM_CHECKOUT), dict(_line(SID, 20), project=VM_CHECKOUT)]


def test_the_reader_takes_the_lines_of_the_sessions_asked_for_within_its_bound(tmp_path, monkeypatch):
    root = tmp_path / ".claude"
    root.mkdir()
    text = "".join(json.dumps(line) + "\n" for line in (_line(SID, 1), _line(OTHER_SID, 2), _line(SID, 3)))
    (root / "history.jsonl").write_text(text + '{"sessionId":"' + SID + '","timestamp":4')
    lines, over = reader.history(str(root), [SID])
    assert [line["timestamp"] for line in lines] == [1, 3] and not over
    monkeypatch.setattr(reader, "HISTORY_BYTES", len(json.dumps(_line(SID, 1))) + 1)
    lines, over = reader.history(str(root), [SID])
    assert [line["timestamp"] for line in lines] == [1] and over
    assert reader.history(str(root), []) == ([], False)


def test_the_writer_appends_each_line_once_and_never_through_a_link(tmp_path):
    dest = tmp_path / ".claude"
    lines = [dict(_line(SID, 1), project=VM_CHECKOUT), dict(_line(SID, 2), project=VM_CHECKOUT)]
    assert writer.carry_history(str(dest), lines, False) == 2
    assert stat.S_IMODE((dest / "history.jsonl").stat().st_mode) == 0o600
    with (dest / "history.jsonl").open("a") as handle:
        handle.write(json.dumps(_line(SID, 9, "typed on the VM")) + "\n")
    assert writer.carry_history(str(dest), lines + [dict(_line(SID, 3), project=VM_CHECKOUT)], False) == 1
    held = [json.loads(line) for line in (dest / "history.jsonl").read_text().splitlines()]
    assert [line["timestamp"] for line in held] == [1, 2, 9, 3]
    assert writer.carry_history(str(dest), lines, True) == 0
    (dest / "history.jsonl").unlink()
    (dest / "history.jsonl").symlink_to(tmp_path / "elsewhere")
    assert writer.carry_history(str(dest), lines, False) == 0 and not (tmp_path / "elsewhere").exists()


# ------------------------------------------------------------------------------------ the extensions ---
def test_the_extensions_are_listed_but_the_account_s_synced_skills_and_never_through_a_link(tmp_path):
    root = tmp_path / ".claude"
    _memory(
        root,
        {
            "skills/review/SKILL.md": b"a",
            "skills/review/scripts/run.sh": b"b",
            "skills/synced/x/guide/SKILL.md": b"c",
            "skills/.trash/old/SKILL.md": b"d",
            "agents/reviewer.md": b"e",
            "commands/ship.md": b"f",
            "keybindings.json": b"{}",
            "projects/-Users-me-demo/memory/MEMORY.md": b"g",
        },
    )
    (root / "agents" / "linked.md").symlink_to(tmp_path)
    found, over = reader.extensions(str(root))
    assert [item["path"] for item in found] == [
        "skills/review/SKILL.md",
        "skills/review/scripts/run.sh",
        "agents/reviewer.md",
        "commands/ship.md",
        "keybindings.json",
    ]
    assert not over
    plan = core.claude_config_plan({"extensions": found}, [], THERE, HERE, DEST, {"extensions": True})
    assert [item["target"] for item in plan["files"]] == [item["path"] for item in found]
    assert "skills/" in plan["scopes"] and "keybindings.json" in plan["scopes"]


def test_extensions_past_their_bound_are_left_whole(tmp_path, monkeypatch):
    root = tmp_path / ".claude"
    _memory(root, {"agents/a.md": b"a", "agents/b.md": b"b"})
    monkeypatch.setattr(reader, "EXTENSION_FILES", 1)
    assert reader.extensions(str(root)) == ([], True)


def test_extensions_past_their_bound_manage_no_scope_so_nothing_carried_before_is_removed():
    here = {"extensions": [], "extensions_over": True}
    plan = core.claude_config_plan(here, [], THERE, HERE, DEST, {"extensions": True})
    assert plan["scopes"] == [] and plan["extensions"]["over"]


def test_the_writer_never_writes_the_account_s_synced_skills(tmp_path):
    mirror, dest, manifest = _world(tmp_path, {"skills/synced/x/SKILL.md": b"x", "agents/a.md": b"a"})
    items = [{"source": path, "target": path} for path in ("skills/synced/x/SKILL.md", "agents/a.md")]
    result, _ = writer.carry(str(dest), str(mirror), str(manifest), ["skills/", "agents/"], items)
    assert result["written"] == ["agents/a.md"] and not (dest / "skills").exists()


# ------------------------------------------------------------------------- what could not be read here ---
def test_a_link_or_a_file_the_reader_may_not_open_is_unread_never_gone(tmp_path):
    root = tmp_path / ".claude"
    _memory(
        root,
        {
            "rules/a.md": b"a",
            "rules/locked.md": b"b",
            "skills/review/SKILL.md": b"c",
            "skills/synced/x/SKILL.md": b"d",
            "projects/-Users-me-demo/memory/MEMORY.md": b"e",
            "projects/-Users-me-demo/memory/locked.md": b"f",
        },
    )
    elsewhere = tmp_path / "dotfiles"
    _memory(elsewhere, {"CLAUDE.md": b"Be brief.\n", "skill/SKILL.md": b"g"})
    (root / "CLAUDE.md").symlink_to(elsewhere / "CLAUDE.md")
    (root / "skills" / "linked").symlink_to(elsewhere / "skill", target_is_directory=True)
    (root / "skills" / "synced" / "linked").symlink_to(elsewhere / "skill", target_is_directory=True)
    (root / "agents").symlink_to(elsewhere, target_is_directory=True)
    for path in ("rules/locked.md", "projects/-Users-me-demo/memory/locked.md"):
        (root / path).chmod(0)
    try:
        if os.access(root / "rules" / "locked.md", os.R_OK):
            pytest.skip("a file mode 0 is still read here (root)")
        unread = []
        found, _, over = reader.instructions(str(root), unread)
        assert [item["path"] for item in found] == ["rules/a.md"] and not over
        memory, _ = reader.memory_folders(str(root), unread)
        assert [item["path"] for item in memory["-Users-me-demo"]] == ["MEMORY.md"]
        extended, _ = reader.extensions(str(root), unread)
        assert [item["path"] for item in extended] == ["skills/review/SKILL.md"]
    finally:
        for path in ("rules/locked.md", "projects/-Users-me-demo/memory/locked.md"):
            (root / path).chmod(0o600)
    # The account's synced skills are never carried, a link there no more than a file.
    assert sorted(unread) == [
        "CLAUDE.md",
        "agents",
        "projects/-Users-me-demo/memory/locked.md",
        "rules/locked.md",
        "skills/linked",
    ]


def test_what_was_not_read_here_is_held_there_and_nothing_at_or_below_it_is_removed(tmp_path):
    here = {
        "memory": {"-Users-me-demo": [{"path": "MEMORY.md", "size": 1, "digest": "a"}]},
        "instructions": [{"path": "rules/a.md", "size": 1, "digest": "b"}],
        "extensions": [],
        "unread": ["CLAUDE.md", "rules/locked.md", "skills/linked", "projects/-Users-me-demo/memory/notes"],
    }
    options = {"memory": True, "instructions": True, "extensions": True}
    plan = core.claude_config_plan(here, HERE_DEMO, THERE, HERE, DEST, options)
    assert sorted(plan["held"]) == [
        "CLAUDE.md",
        f"projects/{VM_FOLDER}/memory/notes",
        "rules/locked.md",
        "skills/linked",
    ]
    # What earlier runs wrote there, each now gone from this run's files: removed, but where it is held.
    gone = {
        "CLAUDE.md": b"Be brief.\n",
        "rules/old.md": b"old\n",
        "rules/locked.md": b"locked\n",
        "skills/linked/SKILL.md": b"skill\n",
        "skills/other/SKILL.md": b"other\n",
        f"projects/{VM_FOLDER}/memory/notes/a.md": b"note\n",
    }
    mirror, dest, manifest = _world(tmp_path, {path: data for path, data in gone.items()})
    items = [{"source": path, "target": path} for path in gone]
    writer.carry(str(dest), str(mirror), str(manifest), plan["scopes"], items)
    result, _ = writer.carry(str(dest), str(mirror), str(manifest), plan["scopes"], [], held=plan["held"])
    assert sorted(result["removed"]) == ["rules/old.md", "skills/other/SKILL.md"]
    assert all((dest / path).exists() for path in gone if path not in result["removed"])


def test_a_claude_code_directory_that_is_not_there_carries_nothing_and_removes_nothing(tmp_path, patch_module):
    asked = core.claude_settings_asked()
    found = run_main(
        reader.main,
        {"path": str(tmp_path / "none"), "memory": True, "instructions": True, "extensions": True, "settings": asked},
    )
    assert found["missing"] and found["instructions"] == [] and found["unread"] == []
    assert found["settings"]["unreadable"]
    options = {"memory": True, "instructions": True, "extensions": True, "settings": True}
    plan = core.claude_config_plan(found, [], THERE, HERE, DEST, options)
    assert plan["scopes"] == [] and plan["settings"]["items"] == [] and plan["settings"]["unreadable"]
    assert plan["missing"]


def test_rules_past_their_bound_are_left_whole_and_manage_no_scope(tmp_path, monkeypatch):
    root = tmp_path / ".claude"
    _memory(root, {"CLAUDE.md": b"x", "rules/a.md": b"a", "rules/b.md": b"b"})
    monkeypatch.setattr(reader, "RULES_FILES", 1)
    found, _, over = reader.instructions(str(root))
    assert [item["path"] for item in found] == ["CLAUDE.md"] and over
    plan = core.claude_config_plan(
        {"instructions": found, "rules_over": over}, [], THERE, HERE, DEST, {"instructions": True}
    )
    assert plan["scopes"] == ["CLAUDE.md"] and plan["instructions"]["over"]


def test_a_memory_folder_two_checkouts_here_share_by_claude_code_s_naming_goes_nowhere():
    # /Users/me/my.app and /Users/me/my-app are both -Users-me-my-app to Claude Code: whose notes are they?
    here = [
        dict(HERE_DEMO[0], path="/Users/me/my.app", toplevel="/Users/me/my.app"),
        dict(
            HERE_DEMO[0], path="/Users/me/my-app", toplevel="/Users/me/my-app", origin="git@github.com:octo/other.git"
        ),
    ]
    memory = {"-Users-me-my-app": MEMORY["-Users-me-demo"]}
    plan = core.claude_memory_plan(memory, here, THERE, HERE, DEST)
    assert plan["folders"] == [] and plan["scopes"] == []
    assert plan["shared"] == [{"repository": "octo/demo", "folder": "-Users-me-my-app"}]


def test_a_path_the_note_cannot_write_as_text_gets_no_note():
    there = [dict(THERE[0], path="/home/dev/projects/d\udce9mo")]
    plan = core.claude_memory_plan(MEMORY, HERE_DEMO, there, HERE, DEST)
    assert plan["folders"] and plan["generated"] == [] and plan["appended"] == {}


def test_a_note_that_cannot_be_written_as_text_is_named_never_the_module_s_end(tmp_path):
    mirror, dest, manifest = _world(tmp_path, SOURCES)
    note = [{"target": f"{SCOPE}{core.NOTE}", "content": "\udce9"}]
    result, _ = _carry(mirror, dest, manifest, SOURCES, generated=note)
    assert result["not_written"] == [f"{SCOPE}{core.NOTE}"] and len(result["written"]) == 2


def test_a_file_that_can_run_here_can_run_there(tmp_path):
    files = {"skills/review/scripts/run.sh": b"#!/bin/sh\necho ok\n", "skills/review/SKILL.md": b"run it\n"}
    mirror, dest, manifest = _world(tmp_path, files)
    (mirror / "skills" / "review" / "scripts" / "run.sh").chmod(0o755)
    items = [{"source": path, "target": path} for path in files]
    writer.carry(str(dest), str(mirror), str(manifest), ["skills/"], items)
    script, skill = dest / "skills" / "review" / "scripts" / "run.sh", dest / "skills" / "review" / "SKILL.md"
    assert stat.S_IMODE(script.stat().st_mode) == 0o700 and stat.S_IMODE(skill.stat().st_mode) == 0o600
    # A copy an earlier version made 0600 is let run, once.
    script.chmod(0o600)
    _, changed = writer.carry(str(dest), str(mirror), str(manifest), ["skills/"], items)
    assert changed and stat.S_IMODE(script.stat().st_mode) == 0o700
    _, changed = writer.carry(str(dest), str(mirror), str(manifest), ["skills/"], items)
    assert not changed


def test_a_manifest_folder_that_is_a_file_is_refused_whole_and_a_mirror_that_is_one_brings_nothing(tmp_path):
    mirror, dest, manifest = _world(tmp_path, SOURCES)
    os.rename(tmp_path / "ws", tmp_path / "real-ws")
    (tmp_path / "ws").write_text("in the way\n")
    result, changed = _carry(mirror, dest, manifest, SOURCES)
    assert result["refused"] and not changed and not dest.exists()
    assert (tmp_path / "ws").read_text() == "in the way\n"
    # A file where the mirror goes holds none of the files: each is named as rsync not having brought it.
    (tmp_path / "ws").unlink()
    (tmp_path / "ws").mkdir()
    mirror.write_text("in the way\n")
    result, _ = _carry(mirror, dest, manifest, SOURCES)
    assert not result["refused"] and len(result["unreadable"]) == 2 and mirror.read_text() == "in the way\n"


def test_a_last_history_line_left_unfinished_is_ended_before_any_is_appended(tmp_path):
    dest = tmp_path / ".claude"
    dest.mkdir()
    (dest / "history.jsonl").write_bytes(b'{"display":"x","sessionId":"a","timestamp":1}\n{"display":"cut')
    lines = [{"display": "y", "sessionId": SID, "timestamp": 2}]
    assert writer.carry_history(str(dest), lines, False) == 1
    text = (dest / "history.jsonl").read_bytes().split(b"\n")
    assert text[1] == b'{"display":"cut' and json.loads(text[2]) == lines[0] and text[3] == b""
    assert writer.carry_history(str(dest), lines, False) == 0
