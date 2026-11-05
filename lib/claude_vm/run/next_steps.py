"""NEXT STEPS: what is left to do, from the records each report task made with set_stats, by VM and matter.

Each report records {vm: {matter: line}} under its owner's stat right after it says what it says, '' once nothing is
left (docs/architecture/custom-code.md §7.5): the stdout callback says them at the end of a run, and the parts'
engine at the end of a run in parts, the same way. Pure: nothing read or written.
"""

# The set_stats names the report tasks record under, each owner's own prefix,
# and the order a VM's matters are listed in: just init's two first, as it comes first.
NEXT_STATS = ("claude_vm_next", "claude_code_next", "github_projects_next", "tailscale_next", "code_next")
MATTERS = (
    "init_settings",
    "init_check",
    "vm",
    "claude",
    "remote_control",
    "github",
    "github_identity",
    "tailscale",
    "tailscale_record",
    "editor",
    "destroy",
)
# Bright, so the blocks stand out from ok's green and changed's yellow.
COLOR = "bright yellow"


def next_steps(run_stats):
    """The lines left to do, from the records the report tasks made: by VM name, then matter, each text once."""
    records = {}
    for stat in NEXT_STATS:
        for vm, matters in (run_stats.get(stat) or {}).items():
            if isinstance(matters, dict):
                records.setdefault(str(vm), {}).update(matters)
    rank = {matter: index for index, matter in enumerate(MATTERS)}
    lines = []
    for vm in sorted(records):
        for matter in sorted(records[vm], key=lambda m: (rank.get(m, len(MATTERS)), m)):
            line = records[vm][matter]
            if isinstance(line, str) and line.strip() and line not in lines:
                lines.append(line)
    return lines
