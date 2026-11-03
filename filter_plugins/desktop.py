"""Filters for the steps the sign-ins take on this machine's desktop."""

from __future__ import annotations

import os
import shlex

# The openers tried when BROWSER names none, in this order: macOS's, then the
# freedesktop one.
OPENERS = ("open", "xdg-open")
# Commands that read the URL again as a command line of their own - a shell's
# -c, Windows' cmd.exe /c start or PowerShell's Start-Process - where a page's
# query string (a & or a ;, in a page the VM chose) would be a command run here.
# By name, without a directory or Windows' .exe.
INTERPRETERS = frozenset(
    {"sh", "bash", "zsh", "dash", "ksh", "mksh", "ash", "fish", "csh", "tcsh", "busybox", "cmd", "powershell", "pwsh"}
)


def _through_interpreter(argv):
    """Whether argv starts an interpreter before it reaches the page: a word ahead of the one that holds %s (any word,
    when none does) named as one of INTERPRETERS. Every word counts, not only the first, so a launcher before one -
    env and its options, nohup, sudo -u - cannot hide it, and each of a word's own words, which env -S splits."""
    ahead = []
    for word in argv:
        if "%s" in word:
            break
        ahead += str(word).split()
    return any(_name(word) in INTERPRETERS for word in ahead)


def _name(word):
    name = os.path.basename(str(word).replace("\\", "/")).lower()
    return name[: -len(".exe")] if name.endswith(".exe") else name


def page_opener(url, browser, found):
    """Return the argv that opens ``url`` in a browser on this machine, or ``[]`` when nothing can.

    ``browser`` is the ``BROWSER`` environment variable, which is a list of
    commands separated by ``:``, each of which may hold ``%s`` where the URL
    goes; a command without one gets the URL as its last argument. Each command
    is split the way a shell would split it, so a quoted path with a space stays
    one argument. Python's ``webbrowser`` module tries the commands in turn
    until one starts; an argv holds only one, so only the first command (the
    first entry that is not empty) is taken, and the others are never tried.

    ``found`` is what ``which`` printed for the openers asked about, as paths or
    names: without a usable ``BROWSER``, ``open`` is used if it was found, then
    ``xdg-open``.

    A ``BROWSER`` command that is a shell or a Windows command interpreter
    (INTERPRETERS) opens nothing: it would read the URL as a command line of
    its own. The caller then prints the page for the person to open, as it does
    when this machine has no opener at all.
    """
    url = str(url)
    for entry in str(browser or "").split(":"):
        if not entry.strip():
            continue
        try:
            argv = shlex.split(entry)
        except ValueError:
            # Unbalanced quotes: no command can be read out of it.
            argv = []
        if argv:
            if _through_interpreter(argv):
                return []
            if any("%s" in arg for arg in argv):
                return [arg.replace("%s", url) for arg in argv]
            return [*argv, url]
        break
    names = {os.path.basename(str(path).strip()) for path in found or []}
    for opener in OPENERS:
        if opener in names:
            return [opener, url]
    return []


class FilterModule:
    def filters(self):
        return {"page_opener": page_opener}
