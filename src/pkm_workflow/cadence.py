"""Reading cadence, not a second scheduler."""
from datetime import date

PAIRS = (("research", "ai_practice"), ("builder", "vc"), ("cognition", "github"))


def edition_for(day: date) -> str:
    return "weekly" if day.weekday() == 6 else "daily"


def sections_for(day: date, edition: str) -> tuple[str, ...]:
    if edition == "weekly":
        return tuple(key for pair in PAIRS for key in pair)
    if edition != "daily":
        raise ValueError("EDITION_INVALID")
    return PAIRS[day.weekday() % 3]
