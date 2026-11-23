"""A run of the new engine drawn as today's runs are: runs/model.py's Run with tui/'s lines, palette and cut
(docs/architecture/engine.md §16.8).

`View(run, started_at=...)` gives what a person reads of a run at a terminal and off one, from the model alone: the
live frame (`frame`: the stages, the running stage's steps, each machine's latest line of work under it, and a
footer; a run of one stage drawn as its steps), a line as each step and stage ends for output that is no terminal
(`ended_lines`), what is said above the region (`above`: each report whole, wrapped and never cut, each failure with
where and why), the summary at the end (`summary`), and the terminal's title (`title_text`). Lines are built with
tui/screen.py and tui/run.py's module-level functions (MARKS, first_sentence, report_lines, failure_lines) and its
two helpers for a summary's lines (Run._flow, Run._cut_head), as they stand, so they cut, pad, colour and wrap as
every live output does; no line of a frame or a summary is wider than the screen.

The model has no tasks, so there is no count, bar, percentage or window of tasks: a machine's latest line of work
(`latest`) stands where today's window of tasks stood, and a stage's or a step's row says each machine's mark where
the run has more than one. A stage or a step of work that is each machine's is said off a terminal once every machine
of the run is through it (or the run has ended), so a fleet whose machines reach a stage one after another says it
once, at its end. Nothing here prints, reads a clock, a file or the terminal: every time is given, and the caller
prints what it gets (the live view, runs/live.py). It imports runs/model.py and tui/, never the reverse
(tests/unit/test_aw_run_view.py).
"""

from __future__ import annotations

import time
from collections.abc import Iterable

from claude_vm.runs import model
from claude_vm.runs.stages import Stage
from claude_vm.text import one_line
from claude_vm.tui import run as drawn
from claude_vm.tui import screen

# How many lines of work a running stage or step shows, the newest at the bottom; one once a frame is folded tight.
WINDOW = 5
# The run's outcome, as the title and the summary say it.
ENDED = {"succeeded": "done", "failed": "failed", "cancelled": "stopped"}
# The states in which a stage or a step has ended.
OVER = ("done", "skipped", "failed", "stopped")
# The said column of a line off a terminal, as today's: each machine's mark there, or their count when they do not fit.
SAID = 36

Segments = list[tuple[str, str]]


def _mark(state: str, glyph: str) -> tuple[str, str]:
    mark, style = drawn.MARKS[state]
    return mark or glyph, style


class View:
    """A run as drawn: its frame, a line as each step ends, what is said above, the summary and the title, from the
    model alone. title is the run's as a person typed it (the verb and the machines given, by default); started_at,
    seconds since the epoch, is when it started, said as a clock; log is the run's text log; host, said beside the
    title, the run's host where the fleet has more than one; check, a dry run."""

    def __init__(
        self,
        run: model.Run,
        *,
        started_at: float,
        title: str | None = None,
        log: str = "",
        host: str = "",
        check: bool = False,
    ) -> None:
        if not isinstance(run, model.Run):
            raise TypeError("a view draws a runs.model.Run")
        self.run = run
        self.title = title if title is not None else " ".join([run.declared.title, *run.machines])
        self.started_at = started_at
        self.log = log
        self.host = host
        self.check = check
        # The stages and steps a line off a terminal was printed for, as (stage, step); step None for the stage.
        self._said: set[tuple[str, str | None]] = set()
        # How many of the model's reports and failures above() has given.
        self._reports = 0
        self._failures = 0

    # --- where it is ------------------------------------------------------------------------------------------
    def _failed_in(self, stage: Stage) -> bool:
        progress = self.run.progress(stage.key)
        parts = [*progress.parts.values(), *(part for step in progress.steps.values() for part in step.parts.values())]
        return any(part.outcome == "failed" for part in parts)

    def _shown(self) -> list[Stage]:
        """The stages drawn: every one but a quiet one where nothing failed."""
        return [stage for stage in self.run.declared.stages if not stage.quiet or self._failed_in(stage)]

    def _flat(self) -> bool:
        """A run of one stage that has steps is drawn as its steps, with no row of its own."""
        shown = self._shown()
        return len(shown) == 1 and bool(shown[0].steps)

    def _at_stage(self) -> Stage | None:
        """The stage the run is at: the last one drawn that runs; while only a stage not drawn runs, the last drawn
        that has begun; None between stages, before the first and after the last."""
        shown = self._shown()
        running = set(self.run.running())
        drawn_running = [stage for stage in shown if stage.key in running]
        if drawn_running:
            return drawn_running[-1]
        if running:
            begun = [stage for stage in shown if self.run.state(stage.key) != "to come"]
            return begun[-1] if begun else None
        return None

    def _at_step(self, stage: Stage) -> str | None:
        """The last step of stage under way, by its key."""
        running = [step.key for step in stage.steps if self.run.state(stage.key, step.key) in ("running", "waiting")]
        return running[-1] if running else None

    def _label(self, stage: str | None, step: str | None = None) -> str:
        found = self.run.declared.stage(stage) if stage is not None else None
        if found is None:
            return ""
        if step is not None:
            each = found.step(step)
            return each.label if each is not None else ""
        return found.label

    # --- the live frame ---------------------------------------------------------------------------------------
    def frame(self, now: float, cols: int, rows: int, glyph: str, color: bool = True) -> list[str]:
        """The region's lines: each cut to cols - 1, and few enough for rows - 1 where folding can make them so."""
        limit = cols - 1
        head = self._header(limit)
        body = self._stages(now, limit, glyph, fold=0)
        for fold in (1, 2, 3):
            if len(head) + len(body) + 2 <= rows - 1:
                break
            body = self._stages(now, limit, glyph, fold=fold)
        lines = [*head, *body, [], self._footer(now, limit, glyph)]
        return [screen.render(screen.cut(line, limit), color) for line in lines]

    def _on(self) -> str:
        """The run's host, as the header and the summary say it: nothing with one host."""
        return f" · {self.host}" if self.host else ""

    def _header(self, limit: int) -> list[Segments]:
        started = time.strftime("%H:%M", time.localtime(self.started_at))
        first = [("agent-warehouse", "b"), (f"  {self.title}{self._on()} · started {started}", "")]
        if self.check:
            first.append((" · a dry run: nothing is changed", "y"))
        return [first, []]

    def _row(self, indent: int, glyph: str, stage: Stage, step: str | None, width: int, now: float, limit: int):
        """A stage's or a step's line: its mark, its label, each machine's mark in it, and its time at its right."""
        state = self.run.state(stage.key, step)
        label = self._label(stage.key, step)
        mark, style = _mark(state, glyph)
        if state == "to come":
            return screen.cut([(" " * indent, ""), (f"{mark} ", style), (label, style)], limit)
        bold = "b" if state in ("running", "waiting") else ""
        left = [(" " * indent, ""), (mark + " ", style), (label.ljust(width), "d" if state == "skipped" else bold)]
        seconds = self.run.seconds(now, stage.key, step)
        clock = screen.clock(seconds) if state in ("running", "waiting") else screen.duration(seconds)
        tail = [(clock, "")]
        marks = self._marks(stage, step, glyph, limit - screen.width(left) - 1 - screen.width(tail) - 1)
        if marks:
            left += [(" ", ""), *marks]
        return screen.right(left, tail, limit)

    def _marks(self, stage: Stage, step: str | None, glyph: str, room: int) -> Segments:
        """Each machine's mark in a stage or a step - alpha ✓ · beta ⠴ · gamma ○ - or their count where they do not fit
        room; none for a run of fewer than two machines, or work that is not each machine's."""
        states = self.run.marks(stage.key, step) if len(self.run.machines) >= 2 else {}
        if not states:
            return []
        parts: Segments = []
        for machine, state in states.items():
            parts += [(f"{machine} ", ""), _mark(state, glyph), (" · ", "d")]
        parts = parts[:-1]
        if screen.width(parts) <= room:
            return parts
        done = sum(state in ("done", "skipped") for state in states.values())
        failed = sum(state == "failed" for state in states.values())
        said = [(f"{done}/{screen.plural(len(states), 'machine')}", "")]
        if failed:
            said.append((f" {failed} failed", "r"))
        return said

    def _waiting_line(self, indent: int, stage: Stage, step: str | None, now: float, limit: int, labelled: bool):
        """The wait for you at a stage (or at one of its steps): what the run waits for, whose, and since when."""
        waits = [
            wait
            for wait in self.run.waiting()
            if wait.yours and wait.stage == stage.key and (step is None or wait.step == step)
        ]
        first = waits[0]
        said = f"{self._label(stage.key, first.step)}: " if first.step is not None else ""
        said += first.text
        machines = list(dict.fromkeys(wait.machine for wait in waits if wait.machine is not None))
        if machines:
            said += f" · {', '.join(machines)}"
        said += f" · {screen.clock(now - first.since)}"
        head = [(" " * indent, ""), ("⏸ ", "y")]
        if labelled:
            head.append((stage.label.ljust(13), "b"))
        return screen.cut([*head, (said, "y")], limit)

    def _stages(self, now: float, limit: int, glyph: str, fold: int) -> list[Segments]:
        lines: list[Segments] = []
        pending = []
        flat = self._flat()
        for stage in self._shown():
            state = self.run.state(stage.key)
            if not flat:
                if state == "to come" and fold >= 2:
                    pending.append(stage.label)
                    continue
                if state == "waiting":
                    lines.append(self._waiting_line(1, stage, None, now, limit, labelled=True))
                else:
                    lines.append(self._row(1, glyph, stage, None, 13, now, limit))
                if state not in ("running", "waiting"):
                    continue
            lines += self._steps(stage, now, limit, glyph, fold, flat)
        if pending:
            lines.append(screen.cut([(" ", ""), ("○ ", "d"), (f"then {', '.join(pending)}", "d")], limit))
        return lines

    def _steps(self, stage: Stage, now: float, limit: int, glyph: str, fold: int, flat: bool) -> list[Segments]:
        """A stage's work and steps: under its row, or as the rows of a run of one stage."""
        lines: list[Segments] = []
        indent, window = (1, 5) if flat else (5, 9)
        if self.run.state(stage.key) in ("running", "waiting"):
            lines += self._work(stage.key, None, now, glyph, limit, window, fold)
        later = []
        for step in stage.steps:
            state = self.run.state(stage.key, step.key)
            if state == "to come" and fold >= 1:
                later.append(step.label)
                continue
            if flat and state == "waiting":
                lines.append(self._waiting_line(1, stage, step.key, now, limit, labelled=False))
            else:
                lines.append(self._row(indent, glyph, stage, step.key, 18, now, limit))
            if state in ("running", "waiting"):
                lines += self._work(stage.key, step.key, now, glyph, limit, window, fold)
        if later:
            lines.append(screen.cut([(" " * indent, ""), ("○ ", "d"), (f"then {', '.join(later)}", "d")], limit))
        return lines

    def _work(self, stage: str, step: str | None, now: float, glyph: str, limit: int, indent: int, fold: int):
        """Under a running stage or step: each machine's latest line of work there while it is still at it, and each
        wait for another run (a short section's), the newest at the bottom, each with its machine where the run has
        several and the time since it was said."""
        progress = self.run.progress(stage, step)
        found = []
        for number, (machine, line) in enumerate(self.run.latest.items()):
            if (line.stage, line.step) == (stage, step) and progress.open_for(machine):
                found.append((line.at, number, machine, (glyph, "c"), line.text, "b"))
        for number, wait in enumerate(self.run.waiting()):
            if not wait.yours and (wait.stage, wait.step) == (stage, step):
                found.append((wait.since, len(self.run.latest) + number, wait.machine, ("⏸", "y"), wait.text, "d"))
        found.sort(key=lambda item: item[:2])
        width = min(limit, 88) - indent
        fleet = len(self.run.machines) >= 2
        lines = []
        for at, _, machine, (mark, style), text, weight in found[-(1 if fold >= 3 else WINDOW) :]:
            whose = [(f"{machine}  ", "d")] if fleet and machine is not None else []
            tail = [*whose, (screen.clock(now - at), style)]
            room = width - 2 - screen.width(tail) - 2
            line = [(" " * indent, ""), (mark + " ", style), *screen.cut([(text, weight)], room)]
            lines.append(screen.right(line, tail, indent + width))
        return lines

    def _footer(self, now: float, limit: int, glyph: str) -> Segments:
        run = self.run
        waiting = run.waiting_for_you()
        where = ""
        at = self._at_stage()
        if self._flat():
            stage = self._shown()[0]
            step = self._at_step(stage)
            keys = [each.key for each in stage.steps]
            if step is not None:
                where = f"step {keys.index(step) + 1} of {len(keys)}"
        elif at is not None:
            keys = [stage.key for stage in self._shown()]
            where = f"stage {keys.index(at.key) + 1} of {len(keys)}"
        runs = [("⏸ " if waiting else glyph + " ", "y" if waiting else "c"), (screen.clock(run.seconds(now)), "b")]
        if where:
            runs.append((f" · {where}", "d"))
        if waiting:
            runs.append((" · waiting for you", "y"))
        elif run.stopping is not None and run.ended is None:
            runs.append((" · stopping", "y"))
        if self.log:
            runs.append((f" · {self.log}", "d"))
        return screen.cut(runs, limit)

    def title_text(self, now: float) -> str:
        """The terminal's title: the run, and where it is - waiting for you, or once it has ended, how."""
        run = self.run
        if run.ended is not None:
            return f"{self.title} · {ENDED[str(run.outcome)]}"
        if run.waiting_for_you():
            return f"{self.title} · waiting for you"
        stage = self._at_stage()
        if stage is None:
            return self.title
        step = self._at_step(stage) if self._flat() else None
        return f"{self.title} · {self._label(stage.key, step) if step is not None else stage.label}"

    # --- output that is no terminal -----------------------------------------------------------------------------
    def _over(self, stage: Stage, step: str | None) -> bool:
        """Whether a stage or a step has ended for good: ended, and where its work is each machine's, every machine of
        the run through it or ended, or the run over."""
        if self.run.state(stage.key, step) not in OVER:
            return False
        if self.run.ended is not None or not stage.each_machine:
            return True
        return all(
            self.run.mark(machine, stage.key, step) in OVER or machine in self.run.outcomes
            for machine in self.run.machines
        )

    def ended_lines(self, now: float) -> list[str]:
        """A line for each step and stage that has ended since the last call, for output that is no terminal: a stage
        of one step said once, by its step's name; a run of one stage, by its steps alone."""
        out = []
        flat = self._flat()
        for stage in self._shown():
            only = len(stage.steps) == 1 and not flat
            for step in stage.steps:
                if only or (stage.key, step.key) in self._said or not self._over(stage, step.key):
                    continue
                self._said.add((stage.key, step.key))
                out.append(self._ended(f"{stage.label} / {step.label}", stage, step.key, now))
            if flat or (stage.key, None) in self._said or not self._over(stage, None):
                continue
            self._said.add((stage.key, None))
            label = f"{stage.label} / {stage.steps[0].label}" if only else stage.label
            out.append(self._ended(label, stage, None, now))
        return out

    def _ended(self, label: str, stage: Stage, step: str | None, now: float) -> str:
        state = self.run.state(stage.key, step)
        mark, _ = _mark(state, "…")
        said = screen.plain(self._marks(stage, step, "…", SAID)) or ("" if state == "done" else state)
        return f"{mark} {label.ljust(28)} {said:<{SAID}} {screen.duration(self.run.seconds(now, stage.key, step))}"

    # --- what is said above the region ------------------------------------------------------------------------
    def above(self, cols: int, color: bool = True) -> list[tuple[str, str]]:
        """What is said above the region since the last call, in the order said: each report whole, under its machine,
        wrapped and never cut; each failure with where it happened, whose and why, and where the log has the rest. As
        (coloured, plain) pairs, which the caller prints."""
        reports = self.run.reports[self._reports :]
        failures = self.run.failures[self._failures :]
        self._reports += len(reports)
        self._failures += len(failures)
        said = sorted(
            [(report.at, 0, number, report) for number, report in enumerate(reports)]
            + [(failure.at, 1, number, failure) for number, failure in enumerate(failures)],
            key=lambda item: item[:3],
        )
        out = []
        for _, _, _, item in said:
            if isinstance(item, model.Report):
                out += self._report(item, cols, color)
            else:
                out += self._failure(item, cols, color)
        return out

    @staticmethod
    def _report(report: model.Report, cols: int, color: bool) -> list[tuple[str, str]]:
        """A report, whole: under its machine, which leads unless the report names it itself."""
        machine = report.machine
        lines = report.text.split("\n")
        if machine is not None and lines[0].startswith(machine + ": "):
            lines[0] = lines[0][len(machine) + 2 :]
        named = machine is not None and not lines[0].startswith(machine + " ")
        said = {"host": machine or "", "lines": lines, "styles": [""] * len(lines), "named": named}
        return drawn.report_lines(said, cols, color)

    def _who(self, machine: str | None) -> str:
        """Whom a failure is about: its machine; with none, the run's one machine, else this machine."""
        if machine is not None:
            return machine
        return self.run.machines[0] if len(self.run.machines) == 1 else "this machine"

    def _failure(self, failure: model.Failure, cols: int, color: bool) -> list[tuple[str, str]]:
        """A failure, for good above the region: the step or the stage, whose, why and for how long, and the log."""
        where = self._label(failure.stage, failure.step) or self._label(failure.stage) or self.run.declared.title
        why = failure.why.split("\n")
        record = {
            "where": where,
            "who": self._who(failure.machine),
            "task": why[0] or "failed",
            "seconds": failure.seconds,
            "how": "failed",
            "said": why[1:],
            "log": self.log,
        }
        return drawn.failure_lines(record, cols, color)

    # --- the summary ------------------------------------------------------------------------------------------
    def _stopped_at(self) -> str:
        """Where a stopped run was: the last stage drawn that stopped, by its last step that stopped where it has
        one."""
        stopped = [stage for stage in self._shown() if self.run.state(stage.key) == "stopped"]
        if not stopped:
            return ""
        stage = stopped[-1]
        steps = [step.label for step in stage.steps if self.run.state(stage.key, step.key) == "stopped"]
        return f" at {steps[-1] if steps else stage.label}"

    def _head(self, now: float) -> Segments:
        """The summary's first line: done; done, with the machines that failed; failed, and where; or stopped."""
        run = self.run
        total = run.seconds(now)
        minutes, seconds = divmod(round(total), 60)
        took = f"{minutes} min {seconds} s" if minutes else f"{seconds} s"
        # The clock it ended at, last: what a head too long for the width loses first (Run._cut_head).
        tail = (f" · {time.strftime('%H:%M', time.localtime(self.started_at + total))}", "")
        failed = [machine for machine, end in run.outcomes.items() if end.outcome == "failed"]
        succeeded = any(end.outcome == "succeeded" for end in run.outcomes.values())
        own = any(failure.machine is None for failure in run.failures)
        if run.outcome == "cancelled":
            said, style = f"stopped after {took}{self._stopped_at()}", "y"
        elif not run.failures and run.outcome != "failed":
            said, style = f"done in {took}", ""
        elif failed and succeeded and not own and len(run.machines) >= 2:
            said, style = f"done in {took}", ""
            tail = (f" · {screen.plural(len(failed), 'machine')} failed: {', '.join(failed)}", "r")
        else:
            where = ""
            if run.failures:
                first = run.failures[0]
                where = self._label(first.stage, first.step) or self._label(first.stage)
            said, style = f"failed after {took}{f' at {where}' if where else ''}", "r"
        return [("agent-warehouse", "b"), (f"  {self.title}{self._on()} · {said}", style), tail]

    def summary(self, now: float, cols: int, color: bool = True, next_steps: Iterable[str] | None = None) -> list[str]:
        """The summary at the run's end: how it ended; each stage's mark and time, with each machine's mark where there
        are several, and its steps' times with your time in them; what it never reached, where it failed or stopped;
        the last word of each report on each machine, but what NEXT STEPS will say whole (next_steps, by default the
        run's); and the totals. Each line cut to cols - 1, the head losing its clock first."""
        run = self.run
        limit = cols - 1
        left_to_do = {one_line(line, None) for line in (run.next_steps() if next_steps is None else next_steps)}
        lines: list[Segments] = [self._head(now), []]
        flat = self._flat()
        several = len(run.machines) >= 2
        if flat:
            stage = self._shown()[0]
            begun = [step for step in stage.steps if run.state(stage.key, step.key) != "to come"]
            rows = [(step.label, stage, step.key) for step in begun]
        else:
            rows = [(stage.label, stage, None) for stage in self._shown() if run.state(stage.key) != "to come"]
        label = min(18, max([11, *(len(name) for name, _, _ in rows)]))
        heading = f"{'STEPS' if flat else 'STAGES':<{label + 3}}{'TIME':>6}"
        if several:
            heading += "    MACHINES"
        lines.append([(heading, "h")])
        for name, stage, step in rows:
            mark, style = _mark(run.state(stage.key, step), "…")
            head = [(" ", ""), (mark + " ", style), (name[:label].ljust(label), "b")]
            head.append((f"{screen.duration(run.seconds(now, stage.key, step)):>6}    ", ""))
            parts = []
            if several:
                states = run.marks(stage.key, step)
                if states:
                    parts.append(" · ".join(f"{machine} {_mark(state, '…')[0]}" for machine, state in states.items()))
            for each in stage.steps if not flat else ():
                if run.state(stage.key, each.key) == "to come":
                    continue
                piece = f"{each.label} {screen.duration(run.seconds(now, stage.key, each.key))}"
                waited = run.waited(now, stage.key, each.key)
                parts.append(f"{piece} ({screen.duration(waited)} yours)" if waited else piece)
            if flat or not stage.steps:
                waited = run.waited(now, stage.key, step)
                if waited:
                    parts.append(f"({screen.duration(waited)} yours)")
            lines += drawn.Run._flow(head, parts, limit)
        # A run that failed or stopped part way says what it never reached: the steps, or the stages, still to come.
        if run.outcome in ("failed", "cancelled") or run.failures:
            if flat:
                stage = self._shown()[0]
                never = [step.label for step in stage.steps if run.state(stage.key, step.key) == "to come"]
            else:
                never = [stage.label for stage in self._shown() if run.state(stage.key) == "to come"]
            if never:
                lines += drawn.Run._flow([(" not run  ", "d")], never, limit, style="d")
        lines += self._reports_said(limit, left_to_do)
        lines.append([])
        totals = [screen.plural(len(run.machines), "machine")] if run.machines else []
        totals.append(f"{len(run.failures)} failed")
        yours = run.waited(now)
        if yours:
            totals.append(f"{screen.duration(yours)} of it yours")
        if self.log:
            totals.append(f"log {self.log}")
        lines += drawn.Run._flow([], totals, limit, style="d")
        return [
            screen.render(drawn.Run._cut_head(line, limit) if index == 0 else screen.cut(line, limit), color)
            for index, line in enumerate(lines)
        ]

    def _reports_said(self, limit: int, left_to_do: set[str]) -> list[Segments]:
        """Each machine's reports, a line each, under its name (the run's own under THIS MACHINE): a report of several
        lines, or longer than a line, by its first sentence, since it is whole above; the last ten; and nothing NEXT
        STEPS will say whole."""
        said: dict[str | None, list[str]] = {}
        for report in self.run.reports:
            machine = report.machine
            text = one_line(report.text, None)
            unnamed = text[len(machine) + 2 :] if machine is not None and text.startswith(machine + ": ") else text
            if not unnamed or {text, unnamed, f"{machine}: {unnamed}"} & left_to_do:
                continue
            if "\n" in report.text.strip() or len(unnamed) > limit - 2:
                unnamed = drawn.first_sentence(unnamed)
            kept = said.setdefault(machine, [])
            if unnamed not in kept:
                kept.append(unnamed)
        machines = self.run.machines
        order = [machine for machine in machines if machine in said]
        order += [machine for machine in said if machine is not None and machine not in machines]
        order += [None] if None in said else []
        lines: list[Segments] = []
        for machine in order:
            lines += [[], [("THIS MACHINE" if machine is None else machine.upper(), "h")]]
            lines += [screen.cut([(" ", ""), (text, "")], limit) for text in said[machine][-10:]]
        return lines
