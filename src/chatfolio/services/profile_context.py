import re
from dataclasses import dataclass
from datetime import date

from chatfolio.models.profile import Education, Experience, Project
from chatfolio.services.embedding_service import (
    education_chunk_text,
    experience_chunk_text,
    project_chunk_text,
)

# Dense retrieval is poor at exact names: a long experience chunk gets truncated by the embedder
# and its vector ends up describing the tech, not the employer, so "what did you do at <Company>?"
# can score below the similarity threshold against the very chunk that answers it. The overview
# and the name matching here come straight from the database instead, so a company, project or
# school the recruiter names is always found whatever the vectors say.
MAX_OVERVIEW_PROJECTS = 8
_ORG_SUFFIXES = frozenset(
    {"plc", "ltd", "limited", "inc", "llc", "pvt", "co", "corp", "corporation", "company"}
)
_MIN_NAME_CHARS = 3


@dataclass(frozen=True)
class ProfileContext:
    overview: str
    pinned: list[str]


def _tokens(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).split()


def _name_keys(name: str) -> list[str]:
    """Normalised spellings of an organisation/project name a recruiter might type: with
    legal suffixes ("PLC", "Ltd") dropped, and also without a trailing number ("Brain Station 23"
    -> "brain station"), since people often shorten it."""
    tokens = _tokens(name)
    while len(tokens) > 1 and tokens[-1] in _ORG_SUFFIXES:
        tokens.pop()
    keys = [" ".join(tokens)]
    if len(tokens) > 2 and tokens[-1].isdigit():
        keys.append(" ".join(tokens[:-1]))
    return [key for key in keys if len(key) >= _MIN_NAME_CHARS]


def mentions(message: str, name: str) -> bool:
    padded = f" {' '.join(_tokens(message))} "
    return any(f" {key} " in padded for key in _name_keys(name))


def _month(value: date | None) -> str:
    return f"{value:%b %Y}" if value else "?"


def _period(experience: Experience) -> str:
    end = "present" if experience.is_current else _month(experience.end_date)
    return f"{_month(experience.start_date)}-{end}"


def build_profile_context(
    message: str,
    experiences: list[Experience],
    projects: list[Project],
    education: list[Education],
) -> ProfileContext:
    lines: list[str] = []
    if experiences:
        lines.append("Work history:")
        lines += [f"- {e.role} at {e.company} ({_period(e)})" for e in experiences]
    if projects:
        titles = "; ".join(p.title for p in projects[:MAX_OVERVIEW_PROJECTS])
        lines.append(f"Projects: {titles}")
    if education:
        entries = "; ".join(
            f"{' '.join(part for part in (e.degree, e.field) if part) or 'Studies'}, "
            f"{e.institution}"
            for e in education
        )
        lines.append(f"Education: {entries}")

    pinned = [experience_chunk_text(e) for e in experiences if mentions(message, e.company)]
    pinned += [project_chunk_text(p) for p in projects if mentions(message, p.title)]
    pinned += [education_chunk_text(e) for e in education if mentions(message, e.institution)]
    return ProfileContext(overview="\n".join(lines), pinned=pinned)
