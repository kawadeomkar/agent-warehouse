# Comments

A comment in the new engine's code is one of a few kinds, each of which a check reads: a pointer to a section of
the design, to a test or to an upstream issue, or a tool's directive with its rule's code. Anything else is prose,
counted file by file and held to a count that only goes down. A docstring stays, and says the contract alone.
`just lint` runs the check, `tools/comments`, as its `comments` part, and the commit hook runs it on the files a commit
changes.

## Why

Lauren Tan's talk "All Code Is Slop (Except Mine)" (AI Engineer CODE, 2026-11-11) starts from what a team's agents
do: they copy what the codebase holds. A comment can be neither enforced nor kept true, so a wrong one is copied as
readily as a right one, and her team turned about 99% of its comments into lint rules whose errors say how to fix the
cause. The rule here is the same, written as the kinds a comment may be and never as a share of comments to remove: a
rule lives in the test that holds it, a reason of the design in its section of [the architecture](../architecture/engine.md),
and a comment points at one of them. That most prose goes is the rule's result, not its target.

## The kinds a comment may be

| Kind | Its form | Example |
|---|---|---|
| A section of the design | `§` and a section number a page of `docs/architecture/` has | `# §16.6` |
| A test | `test:`, a file of `tests/`, and a test function or class in it, by pytest's node ID without parameters | `# test: tests/unit/test_aw_comments.py::test_a_pointer_to_nothing_is_refused` |
| An upstream issue | `upstream:` and `owner/repo#N` | `# upstream: astral-sh/ruff#1234` |
| A tool's directive, with its rule's code | ruff's `noqa` and `ruff: noqa`, `type: ignore[...]`, `pragma: no cover`, `fmt: off`, `on` or `skip`; several in one comment, each after a `#` of its own | `# noqa: E501`, `# type: ignore[attr-defined]` |
| A shebang, or a coding line | on the first line, or on one of the first two | `#!/usr/bin/env python3` |

A comment is one of these whole, or one pointer with directives beside it, each after a `#` of its own, as a test
pointer past the line's limit takes ruff's `E501`:
`# test: tests/unit/test_aw_comments.py::test_each_allowed_kind_is_let_through  # noqa: E501`. One that starts as a
pointer and goes on in words is a pointer not of its form.

**A docstring is the contract**: what the function, class or module does, takes, returns and raises, in 5 lines at
most, its quotes' lines counted. The why goes where a comment's would.

## What the check finds

Each finding is a line `path:line:col: CMTnnn <what it is>. Fix: <where its content goes>.`, sorted by path and line.
The sentence is the kind's own, never the comment's text. Any finding makes the check exit 1.

| Code | What it is | Fix |
|---|---|---|
| `CMT001` | A workaround's comment: one of none of the kinds above that names, as a whole word in any case, workaround, hack, for now, temporary, until, flaky, race, retry or sleep | Fix the cause, or file a card and hold the case with a test |
| `CMT002` | A pointer to nothing: a comment that starts with `§`, `test:` or `upstream:`, and is not of its form or points at a section, a file or a test that does not exist | Point it at what exists, or delete it |
| `CMT003` | Prose in a comment of the new engine's | A rule goes into the test that holds it, its why in the test's docstring; a design reason into its § section, a `# §` pointer left here; a step the code shows, deleted |
| `CMT004` | A docstring of the new engine's past 5 lines | The contract kept within 5 lines; the rest goes as `CMT003`'s does |

## Where it applies

- **The new engine** (the ten subpackages of `lib/claude_vm/` and `secret.py` beside them, `tools/aw`, `tools/aw-mcp`
  and `tools/comments`): every kind.
- **Today's product's Python** (`playbooks/`, `roles/`, `filter_plugins/`, `callback_plugins/`, `inventory/`, the rest
  of `tools/`, and today's modules of `lib/claude_vm/`): `CMT001` alone, since the cutover retires it.
- **Not read**: `tests/` and the Molecule scenarios, where a rule and its why go; the files the wiki's reference pages
  are generated from (`*.example`, a role's `defaults/main.yml`, `vars/main.yml` and `argument_specs.yml`), whose
  comments are those pages' text; and what is not Python (the `justfile`, YAML, shell), which takes the rule in a later
  change, through a parser of each and never a pattern over lines.
- **Every Python file**, tests too, takes ruff's four rules of the same kind: no code left commented out (`ERA001`), a
  `type: ignore` or a `noqa` only with its code (`PGH003`, `PGH004`), and no `noqa` that suppresses nothing
  (`RUF100`).

## The count

`tests/unit/comments.json` holds, for each file of the new engine that has any, how many `CMT003` and `CMT004`
findings it has: the count made when the rule came in. A file within its count says nothing. A file above it fails,
naming both numbers, with each of its findings; a file below it fails too, until its count is lowered to match in the
same commit, so the count only goes down; a file it does not name is held to 0. Run with no file named, the check also
fails on a count whose file is gone. Raising a count is never the fix.

```bash
just lint comments                                  # the whole checkout
tools/env-run python -I tools/comments <file>...    # some files: what the commit hook runs
```
