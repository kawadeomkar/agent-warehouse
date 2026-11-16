#!/usr/bin/python
# Copyright (c) agent-warehouse contributors
# MIT License
"""Bring a VM's Claude Code sessions here as plain files and folders, whatever the VM sends."""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: claude_sessions_fetch
short_description: Bring entries of a VM's Claude Code projects folder here, as plain files and folders only
version_added: "1.0.0"
description:
  - Runs C(tar) on the VM, over SSH as O(ssh) says, for the entries O(names) of O(root), and unpacks what
    it sends into O(dest), a folder it creates here, 0700, with every folder in it 0700 and every file
    0600 and given the time the archive says. Nothing else of the archive is kept.
  - What the VM sends is not trusted. A file or folder is written only at a path inside O(dest) that is
    one of O(names) or inside one, named without C(..), an absolute path or an empty component, and
    never through a link: nothing here is a link, since a link, a hard link, a device or a pipe in the
    archive is left out and named. Nothing is ever written outside O(dest).
  - A name is held to what Claude Code writes there too - no part that starts with a dot or holds a control
    character, none longer than a file name can be, and no path deeper than 16 - and such an entry is left out.
  - What one save may hold is bounded, whatever the VM sends - O(max_bytes) of files, twice that and 64 MiB of
    archive in all, 200000 entries, and a header (a pax header, a GNU long name) of 1 MiB, which tarfile reads
    whole into memory - and so is a sparse file, which tar sends only when asked to. Past one of them the
    save fails, what arrived before kept in O(dest).
  - Fails, having written nothing outside O(dest), when SSH or tar fails, when the archive ends early, when an
    entry asked for did not arrive, or past a bound. just destroy then keeps the VM
    (playbooks/tasks/session_save.yml).
  - Nothing of what the files hold is returned, only how many and the names left out.
options:
  dest:
    description: The folder to create here and unpack into. It must not exist; the folders above it are made 0700.
    type: path
    required: true
  root:
    description: The VM's Claude Code projects folder, C(~/.claude/projects), as an absolute path there.
    type: str
    required: true
  names:
    description:
      - The entries of O(root) to bring, each C(<folder>/<id>.jsonl), C(<folder>/<id>) or C(<folder>/memory),
        a folder brought whole. A folder is named as Claude Code names one, ASCII letters, digits and "-"
        only, and an ID is a session's.
    type: list
    elements: str
    required: true
  max_bytes:
    description:
      - The most the files written may hold in all, in bytes: destroy_sessions_max_size.
    type: int
    default: 4294967296
  idle_seconds:
    description:
      - How long the VM may send nothing before the save is ended, failed: a VM that sends a block and stops would
        otherwise hold just destroy, and the run lock, until someone pressed Ctrl-C.
    type: int
    default: 300
  most_seconds:
    description:
      - How long the whole save may take before it is ended, failed.
    type: int
    default: 3600
  ssh:
    description:
      - How to reach the VM, as Ansible reaches it. Without it, the VM is this machine, as in the tests.
    type: dict
    suboptions:
      user:
        description: The user to log in as.
        type: str
        default: ""
      host:
        description: The address to connect to.
        type: str
        required: true
      port:
        description: The port, when the inventory gives one.
        type: str
        default: ""
      private_key_file:
        description: The key, when the inventory gives one.
        type: str
        default: ""
      args:
        description: Ansible's ssh_common_args and ssh_extra_args, split as Ansible splits them.
        type: str
        default: ""
author:
  - agent-warehouse contributors
"""

EXAMPLES = r"""
- name: Bring the VM's own sessions here
  claude_sessions_fetch:
    dest: /Users/me/agent-warehouse/.sessions/alpha-20261023-153000/projects
    root: /home/dev/.claude/projects
    names:
      - -home-dev-projects-demo/0a608ad8-1b2c-4d3e-8f90-123456789abc.jsonl
      - -home-dev-projects-demo/memory
    ssh:
      user: dev
      host: 192.0.2.10
  delegate_to: localhost
"""

RETURN = r"""
files:
  description: How many files were written.
  returned: always
  type: int
left_out:
  description: Each entry of the archive not written, with why, by its name in the archive; the first 50.
  returned: always
  type: list
  elements: dict
left_out_count:
  description: How many entries of the archive were not written.
  returned: always
  type: int
"""

import errno
import os
import re
import shlex
import stat
import subprocess
import tarfile
import tempfile
import threading
import time

from ansible.module_utils.agent_warehouse import SESSION_ID
from ansible.module_utils.basic import AnsibleModule

# What just destroy asks for: a transcript, the folder beside it, or a project's auto memory.
NAME = re.compile(rf"[A-Za-z0-9-]+/(?:{SESSION_ID.pattern}(?:\.jsonl)?|memory)")
# How many of the entries left out are named: a VM can send any number.
LISTED = 50
# What one save may hold, whatever the VM sends, beside the files' bytes (max_bytes, destroy_sessions_max_size): the
# archive's entries - a projects folder of months is a few thousand - and a header that is not an entry's own, a pax
# header or a GNU long name, which tarfile reads whole into memory before the entry it is for.
MOST_BYTES = 4 << 30
MOST_ENTRIES = 200_000
MOST_HEADER = 1 << 20
LONG_HEADERS = (
    tarfile.XHDTYPE,
    tarfile.XGLTYPE,
    tarfile.SOLARIS_XHDTYPE,
    tarfile.GNUTYPE_LONGNAME,
    tarfile.GNUTYPE_LONGLINK,
)
# A name as Claude Code writes them in its folders: no part that starts with a dot - a .git planted in a memory folder
# would be read by a git run inside it - or holds a control character, none longer than a file name may be, and no
# path deeper than a session's folder needs.
PART = re.compile(r"[^.\x00-\x1f\x7f][^\x00-\x1f\x7f]*")
PART_BYTES = 255
DEPTH = 16
# How much of a name left out the result says.
NAME_SAID = 200
# A host or user that ssh could read as an option, or that is not one word. fullmatch: an anchored match takes a word
# with a newline at its end.
WORD = re.compile(r"[^\s-]\S*")


class Refused(Exception):
    """An entry of the archive that is not written; the run goes on without it."""


class Stopped(RuntimeError):
    """What ends the save: more than one save may hold, or what tar sends only when asked. Never one of tarfile's own
    errors, some of which its reader takes for the archive's end."""


def _size(count):
    """``count`` bytes as a setting writes a size: 4G, 512M."""
    for unit, size in (("T", 1 << 40), ("G", 1 << 30), ("M", 1 << 20), ("K", 1 << 10)):
        if count >= size:
            return f"{count / size:g}{unit}"
    return f"{count} bytes"


class _Header(tarfile.TarInfo):
    """An entry's header, refused before tarfile reads more of it than a header needs. _proc_member is where tarfile
    reads each header, a pax header's or a long name's own included (its documented place for a subclass's work); a
    test fails if a Python moves it."""

    def _proc_member(self, tarfile_):
        # A global header's records go on every entry after it, each a copy: a few thousand entries after one of a
        # megabyte is gigabytes, inside every bound below. tar writes one only when asked.
        if self.type == tarfile.XGLTYPE:
            raise Stopped("the VM sent a global header, which its tar writes only when asked to")
        if self.type in LONG_HEADERS and self.size > MOST_HEADER:
            raise Stopped(f"the VM sent a header of {_size(self.size)}, more than the {_size(MOST_HEADER)} one needs")
        if self.type == tarfile.GNUTYPE_SPARSE:
            raise Stopped("the VM sent a sparse file, which its tar makes only when asked to")
        member = super()._proc_member(tarfile_)
        if member.sparse is not None:
            raise Stopped("the VM sent a sparse file, which its tar makes only when asked to")
        return member

    def _proc_gnusparse_10(self, next_, pax_headers, tarfile_):
        # Its map is read from the entry's data, as long as the VM makes it.
        raise Stopped("the VM sent a sparse file, which its tar makes only when asked to")


def ssh_argv(ssh, command):
    """The ssh that runs ``command`` on the VM, never asking for anything."""
    host, user = str(ssh.get("host") or ""), str(ssh.get("user") or "")
    if not WORD.fullmatch(host) or (user and not WORD.fullmatch(user)):
        raise ValueError(f"{user}@{host} is not a user and a host ssh can be given")
    argv = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=accept-new"]
    argv += ["-o", "Compression=yes"]
    if ssh.get("port"):
        argv += ["-p", str(int(ssh["port"]))]
    if ssh.get("private_key_file"):
        argv += ["-i", str(ssh["private_key_file"])]
    argv += shlex.split(str(ssh.get("args") or ""))
    return [*argv, f"{user}@{host}" if user else host, command]


def tar_command(root):
    """tar on the VM: the names come on stdin, one per line, each after "./" so none reads as an option."""
    return f"tar -cf - -C {shlex.quote(root)} -T -"


def _relative(name):
    """The archive name ``name`` as a path inside the projects folder, or Refused."""
    path = name
    while path.startswith("./"):
        path = path[2:]
    path = path.rstrip("/")
    if not path or path.startswith("/") or "\0" in path:
        raise Refused("not a path inside the folder")
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise Refused("not a path inside the folder")
    if len(parts) > DEPTH:
        raise Refused("deeper than a session's folder goes")
    for part in parts:
        if not PART.fullmatch(part) or len(part.encode("utf-8", "surrogateescape")) > PART_BYTES:
            raise Refused("not named as Claude Code names its own")
        # With a HEAD beside objects/ and refs/, a folder is a bare repository to git, whose config a git run inside
        # it - a shell prompt's, an editor's - reads; Claude Code names nothing HEAD. Any case: a folder on a file
        # system that ignores it takes head for HEAD.
        if part.upper() == "HEAD":
            raise Refused("named as git names a repository's own")
    return parts


def _folder(top, parts):
    """Make each folder of ``parts`` under ``top`` that is missing, 0700; one that is there must be a folder."""
    path = top
    for part in parts:
        path = os.path.join(path, part)
        try:
            os.mkdir(path, 0o700)
            os.chmod(path, 0o700)
        except FileExistsError:
            if not stat.S_ISDIR(os.lstat(path).st_mode):
                raise Refused("a file stands where a folder of it is") from None
    return path


def unpack(stream, dest, names, most=MOST_BYTES):
    """Unpack the tar ``stream`` into the folder ``dest`` (there already, empty): (files, left_out, left_out_count,
    arrived).

    Only plain files and folders, and only at ``names`` or inside one of the folders among them: a
    transcript is a file, and nothing is inside it. Everything else is left out. Past ``most`` bytes of
    files, or one of the bounds above, Stopped.
    """
    wanted = {tuple(name.split("/")): name.endswith(".jsonl") for name in names}
    files, written, entries, left_out, refused, arrived = 0, 0, 0, [], 0, set()
    with tarfile.open(fileobj=stream, mode="r|", tarinfo=_Header) as archive:
        for member in archive:
            # tarfile keeps every entry it read: one of each was a few hundred bytes, of a few hundred thousand.
            archive.members.clear()
            entries += 1
            if entries > MOST_ENTRIES:
                raise Stopped(f"the VM sent more than {MOST_ENTRIES} entries")
            try:
                parts = _relative(member.name)
                top = next((key for key in wanted if tuple(parts[: len(key)]) == key), None)
                if top is None or (wanted[top] and len(parts) > len(top)):
                    raise Refused("not asked for")
                exact = len(parts) == len(top)
                if member.isdir():
                    if exact and wanted[top]:
                        raise Refused("a folder where a transcript is asked for")
                    _folder(dest, parts)
                elif member.isreg():
                    if exact and not wanted[top]:
                        raise Refused("a file where a folder is asked for")
                    if written + member.size > most:
                        raise Stopped(
                            f"the VM sent more than the {_size(most)} of files destroy_sessions_max_size allows"
                        )
                    written += _write(
                        _folder(dest, parts[:-1]), parts[-1], archive.extractfile(member), member.mtime, most - written
                    )
                    files += 1
                elif member.issym() or member.islnk():
                    raise Refused("a link")
                else:
                    raise Refused("not a plain file or folder")
                if exact:
                    arrived.add("/".join(top))
            except (Refused, OSError) as why:
                # A path within every bound above can still be longer than this machine's file system takes: that
                # entry is left out, not the whole save failed.
                if isinstance(why, OSError):
                    if why.errno != errno.ENAMETOOLONG:
                        raise
                    why = Refused("a path longer than this machine takes")
                refused += 1
                if len(left_out) < LISTED:
                    # A name may be a megabyte: the result keeps its start, as plain text.
                    name = "".join(ch if ch.isprintable() else "?" for ch in member.name[:NAME_SAID])
                    left_out.append({"name": name, "why": str(why)})
    return files, left_out, refused, arrived


def _write(folder, name, data, mtime, room):
    """Write ``data`` to a new file ``name`` in ``folder``, 0600, with ``mtime``; never through a link, and never
    more than ``room`` bytes. The bytes written."""
    try:
        handle = os.open(os.path.join(folder, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        raise Refused("named twice, or a folder of that name came first") from None
    size = 0
    with os.fdopen(handle, "wb") as out:
        while block := data.read(1 << 20):
            size += len(block)
            if size > room:
                raise Stopped("the VM sent more of a file than its header said")
            out.write(block)
        out.flush()
        os.fchmod(out.fileno(), 0o600)
        try:
            os.utime(out.fileno(), (mtime, mtime))
        except (OverflowError, ValueError, OSError):
            pass
    return size


class _Counted:
    """A stream that counts what is read from it, a whole archive being a number of 512-byte blocks, and stops at
    ``most``: what the VM sends of entries left out, and of headers, is read too."""

    def __init__(self, stream, most):
        self.stream, self.count, self.most = stream, 0, most

    def read(self, size=-1):
        data = self.stream.read(size)
        self.count += len(data)
        if self.count > self.most:
            raise Stopped(f"the VM sent more than {_size(self.most)} in all")
        return data


def _feed(handle, text):
    try:
        handle.write(text)
    except (BrokenPipeError, OSError):
        pass
    finally:
        try:
            handle.close()
        except OSError:
            pass


def _said(errors):
    """What ssh and tar printed on stderr, as ": <text>", or ""."""
    errors.seek(0)
    text = errors.read().decode("utf-8", "replace").strip()
    return f": {text}" if text else ""


class _Watch:
    """Ends ``process`` when ``counted`` has read nothing for ``idle`` seconds, or ``whole`` have gone: a VM that sends
    one block and stops, or sends a byte a second, would hold the save, and the run, for as long as it liked."""

    def __init__(self, process, counted, idle, whole):
        self.process, self.counted, self.idle, self.whole = process, counted, idle, whole
        self.why, self._done = "", threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._done.set()
        self._thread.join()

    def _run(self):
        started = last_at = time.monotonic()
        last = self.counted.count
        while not self._done.wait(1):
            now = time.monotonic()
            if self.counted.count != last:
                last, last_at = self.counted.count, now
            if now - last_at > self.idle:
                self.why = f"the VM sent nothing for {self.idle} s"
            elif now - started > self.whole:
                self.why = f"the save took more than {self.whole} s"
            if self.why:
                self.process.kill()
                return


def fetch(dest, root, names, ssh=None, most=MOST_BYTES, idle=300, whole=3600):
    """Bring ``names`` of ``root`` into ``dest``, at most ``most`` bytes of files, within ``idle`` seconds of silence
    and ``whole`` in all: the result, or raise RuntimeError with what went wrong."""
    for name in names:
        if not NAME.fullmatch(name):
            raise RuntimeError(f"{name!r} is not a Claude Code transcript, a session's folder or a memory folder.")
    if not root.startswith("/"):
        raise RuntimeError(f"{root} is not an absolute path on the VM.")
    command = tar_command(root)
    argv = ssh_argv(ssh, command) if ssh else ["sh", "-c", command]
    parent = os.path.dirname(dest.rstrip("/"))
    if not os.path.isdir(parent):
        os.makedirs(parent, 0o700)
        os.chmod(parent, 0o700)
    os.mkdir(dest, 0o700)
    os.chmod(dest, 0o700)
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors)
        feeder = threading.Thread(
            target=_feed, args=(process.stdin, "".join(f"./{name}\n" for name in names).encode()), daemon=True
        )
        feeder.start()
        # Twice the files' bytes, and room for every entry's headers.
        stream = _Counted(process.stdout, 2 * most + (64 << 20))
        with _Watch(process, stream, idle, whole) as watch:
            try:
                files, left_out, refused, arrived = unpack(stream, dest, names, most)
                # What follows the archive's end is tar's padding: read to the end, so that ssh never
                # writes to a pipe already closed, but no further than a record's worth would need.
                extra = 0
                while extra <= 1 << 20:
                    block = stream.read(1 << 16)
                    if not block:
                        break
                    extra += len(block)
                if extra > 1 << 20:
                    process.kill()
                    process.wait()
                    raise RuntimeError("the VM sent more than the archive")
                # tarfile reads an archive cut short after its first header as one that ended.
                if stream.count % 512:
                    raise tarfile.ReadError("it stops part way through a block")
            except tarfile.TarError as error:
                process.kill()
                process.wait()
                # Ended by the watch, the stream stops where it was: said as what ended it.
                if watch.why:
                    raise RuntimeError(f"{watch.why}; what arrived before is in {dest}") from None
                raise RuntimeError(f"what came back is not a whole archive ({error}){_said(errors)}") from None
            except Stopped as error:
                process.kill()
                process.wait()
                raise RuntimeError(f"{error}; what arrived before is in {dest}") from None
            except BaseException:
                process.kill()
                process.wait()
                raise
            finally:
                process.stdout.close()
                feeder.join()
        if watch.why:
            process.wait()
            raise RuntimeError(f"{watch.why}; what arrived before is in {dest}")
        rc = process.wait()
        said = _said(errors)
    # 255 is ssh's own failure. GNU tar, the VM's, exits 2 for a name it cannot find and sends the
    # rest, so that is said as the name; tar's 1 is a file that changed as it was read, still sent.
    if rc == 255 and ssh:
        raise RuntimeError(f"ssh exited {rc}{said}")
    missing = sorted(set(names) - arrived)
    if missing:
        raise RuntimeError(f"{', '.join(missing)} did not arrive{said}")
    if rc not in (0, 1):
        raise RuntimeError(f"tar exited {rc}{said}")
    return {"files": files, "left_out": left_out, "left_out_count": refused}


def main():
    module = AnsibleModule(
        argument_spec={
            "dest": {"type": "path", "required": True},
            "root": {"type": "str", "required": True},
            "names": {"type": "list", "elements": "str", "required": True},
            "max_bytes": {"type": "int", "default": MOST_BYTES},
            "idle_seconds": {"type": "int", "default": 300},
            "most_seconds": {"type": "int", "default": 3600},
            "ssh": {
                "type": "dict",
                "options": {
                    "user": {"type": "str", "default": ""},
                    "host": {"type": "str", "required": True},
                    "port": {"type": "str", "default": ""},
                    "private_key_file": {"type": "str", "default": ""},
                    "args": {"type": "str", "default": ""},
                },
            },
        },
        supports_check_mode=True,
    )
    params = module.params
    if module.check_mode:
        module.exit_json(changed=True, files=0, left_out=[], left_out_count=0)
    try:
        if params["max_bytes"] < 1:
            raise ValueError(f"max_bytes must be a number of bytes above 0, not {params['max_bytes']}")
        if params["idle_seconds"] < 1 or params["most_seconds"] < 1:
            raise ValueError("idle_seconds and most_seconds must be a number of seconds above 0")
        result = fetch(
            params["dest"],
            params["root"],
            params["names"],
            params["ssh"],
            params["max_bytes"],
            params["idle_seconds"],
            params["most_seconds"],
        )
    except (RuntimeError, ValueError, OSError) as error:
        module.fail_json(msg=f"The sessions could not be brought here: {error}")
    module.exit_json(changed=True, **result)


if __name__ == "__main__":
    main()
