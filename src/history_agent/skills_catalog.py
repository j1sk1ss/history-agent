"""Small, explicit model skills. Individual skill text is loaded on demand."""

from importlib.resources import files

SKILLS = {
    "history-current": "Select default project, branch and concrete snapshot; "
    "explicit URLs override defaults.",
    "history-review": "Investigate one warning against checker documentation, "
    "source and trace; propose or save TP/FP/WF/UC/UD.",
    "history-batch": "Run isolated, resumable reviews of all warnings with "
    "existing-review protection.",
    "history-navigation": (
        "Read source windows and follow DXR symbol relations without "
        "changing the snapshot."
    ),
}


def catalog():
    return [
        {"name": name, "description": description}
        for name, description in SKILLS.items()
    ]


def read_skill(name):
    if name not in SKILLS:
        raise ValueError("Unknown history skill: " + str(name))
    return (
        files("history_agent")
        .joinpath("skills", name, "SKILL.md")
        .read_text(encoding="utf-8")
    )
