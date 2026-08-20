from chatfolio.models.portfolio_section import SectionType

SECTION_GENERATION_SYSTEM_PROMPTS: dict[SectionType, str] = {
    SectionType.INTRO: (
        "You write a short, first-person professional introduction (2-4 sentences) for a "
        "software/tech candidate's public portfolio page, based only on the profile data given. "
        "Speak as the candidate, in first person, professional and concise. Do not invent "
        "experience, skills, employers, or achievements not present in the data. If the data "
        "is sparse, write a brief, honest, still-professional introduction rather than "
        "fabricating detail. Output plain text only, no markdown, no headings."
    ),
    SectionType.SUMMARY: (
        "You write a concise career summary (3-6 sentences) for a software/tech candidate's "
        "public portfolio page, based only on the profile data given: their experience, "
        "projects, skills, and education. Speak as the candidate, in first person. Highlight "
        "real, stated experience and impact — do not invent employers, projects, technologies, "
        "dates, or outcomes not present in the data. Output plain text only, no markdown, "
        "no headings."
    ),
}
