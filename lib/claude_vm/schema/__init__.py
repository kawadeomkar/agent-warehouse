"""The new engine's command line as data: the patterns a typed value is held to, the one table of verbs and options
that the parser, help, the reference page, the printer of a command and the agents' schemas all read, the rules between
options, the one check every entry runs, and the printer of a command, the one way a line a person types is written
and a sentence names a command (docs/architecture/engine.md §16.2). The patterns, the table and the rules are literals
and plain functions written by hand: no code is generated from them into another file.

It imports the standard library and its own modules, nothing else: every command reads it, a refusal and help among
them. patterns.py and words.py import no other module of it. The check, the rules and the printer call one another,
since the printer holds a line to the check and every sentence of the check and the rules names its commands through
the printer: each of the three imports the others as a module (from claude_vm.schema import check), never a name from
one, so that any of them may be imported first (tests/unit/test_aw_cli_printer.py imports each alone). Any part of the
new engine may import it, and does so at the top of a file; nothing of today's product does
(tests/unit/test_aw_layout.py).
"""
