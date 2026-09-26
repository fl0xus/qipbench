"""The QLP prompt in several arrangements, so placement can be compared controlled.

Every rule was verified against the solver one deviation at a time — violating any of them
aborts Yasol's parser rather than producing a wrong answer. Rules that sounded plausible
but turned out to be false are deliberately absent: `3 * x` with an explicit multiplication
sign parses fine, and constraint labels are optional.

What the arrangements are for
-----------------------------
Two things can be varied independently: *what* is said and *where* it is said. Measured so
far, all on Qwen3.6-27B over the same 60 instances:

    role + rules in the user message          0.283   (baseline, without the e/E rule)
    role + rules in the system message        0.133
    role + prohibitions in the system message 0.000

The last one failed because a single rule flipped phrasing: "Do not put the objective
expression on the MINIMIZE line" was ignored in 60 of 60 answers, while the positive
"MINIMIZE stands alone on its line; the objective expression goes on the next line" is
followed everywhere. A syntax rule apparently needs to say what the right layout *is*, not
only what it is not.

One rule is also absent for a measured reason: an earlier version added "auxiliary
variables included" to the variable-coverage rule, and answers using a single-term
objective with a linking constraint rose from 7 to 25 while accuracy fell. A rule meant to
fix syntax changed how the model modelled, so it was withdrawn.
"""

ROLE = (
    "You are an expert in quantified integer programming. "
    "Given a problem description, write a QLP instance for the Yasol solver.\n"
    "Declare the variable blocks explicitly: EXISTS for decisions you control, ALL for "
    "decisions made by an adversary, and ORDER for the sequence in which they are fixed.\n"
)

RULES = (
    "Format rules:\n"
    "- MINIMIZE stands alone on its line; the objective expression goes on the next line.\n"
    "- Always include a BOUNDS section giving lower and upper bounds for every variable.\n"
    "- Write EXISTS once and ALL once. Multiple decision stages are expressed purely "
    "through ORDER, not by repeating a block.\n"
    "- No parentheses anywhere in the objective or the constraints.\n"
    "- Every variable belongs on the left-hand side of a constraint; only a constant may "
    "stand on the right.\n"
    "- Never name a variable e or E; those names are reserved for scientific notation.\n"
)

PROHIBITIONS = (
    "Do not do any of the following:\n"
    "- Do not put the objective expression on the MINIMIZE line.\n"
    "- Do not omit the BOUNDS section, and do not leave a variable without bounds.\n"
    "- Do not write EXISTS or ALL more than once.\n"
    "- Do not use parentheses.\n"
    "- Do not place a variable on the right-hand side of a constraint.\n"
    "- Do not name a variable e or E.\n"
)

OUTPUT = "Output exactly one fenced code block tagged qlp, and nothing else.\n\n"


def parts(name: str) -> tuple[str | None, str | None]:
    """(system message, user-message prefix) for an arrangement.

    lighteval puts `Doc.instruction` at the front of the user message, so the second
    element ends in a blank line to keep it apart from the problem text.
    """
    arrangements = {
        # Everything in the user message. The original baseline.
        "user": (None, ROLE + RULES + OUTPUT),
        # Role in the system message, rules stay with the task in the user message.
        "split": (ROLE, RULES + OUTPUT),
        # Everything in the system message.
        "system": (ROLE + RULES + OUTPUT, None),
        # Same, with the rules phrased purely as prohibitions.
        "prohibitions": (ROLE + PROHIBITIONS + OUTPUT, None),
    }
    if name not in arrangements:
        raise SystemExit(f"Unknown ruleset {name!r}. Available: {sorted(arrangements)}")
    return arrangements[name]
