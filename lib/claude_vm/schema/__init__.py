"""The new engine's command line as data: the patterns a typed value is held to, and the one table of verbs and options
that the parser, help, the reference page, the printer of a command and the agents' schemas all read
(docs/architecture/engine.md §16.2). Literals written by hand: nothing is generated from them into another file.

It imports the standard library and its own modules, nothing else: every command reads it, a refusal and help among
them. Any part of the new engine may import it; nothing of today's product does (tests/unit/test_aw_layout.py).
"""
