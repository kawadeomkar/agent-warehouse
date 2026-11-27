"""Layer 2: what the engine brings a machine to after the system layer, in plain code, each piece a step of one shape
(docs/architecture/engine.md §16.10).

step.py is that shape: a step's probe, its apply and its difference, the context every step is handed (the machine,
its settings, the runner's calls on it, the run's events, the cancel flag), and the runner of a machine's steps, which
stops that machine at its first failed step. claude_install.py is the first step: Claude Code brought to the release
the settings name, held to Anthropic's signature by default, or by npm. claude_settings.py writes Claude Code's
user settings (settings.json, the API key in it or removed from it, CLAUDE.md), and remote_control.py brings Remote
Control to what the login and the key allow: running, waiting for a login through its path unit, stopped with what is
in the way said, or removed; whether it is wanted is decided there alone, which aw status reads too
(cli/status_inputs.py). Imported by engine/ and that one reader; nothing of today's product imports it.
"""
