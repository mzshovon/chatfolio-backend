from chatfolio.models.portfolio_section import SectionType

SECTION_GENERATION_SYSTEM_PROMPTS: dict[SectionType, str] = {
    SectionType.INTRO: (
        "Write a first-person portfolio intro for the candidate. "
        "Max 80 characters, one sentence: role + strongest stated focus. "
        "Use only the given data; never invent. Plain text, no markdown."
    ),
    SectionType.SUMMARY: (
        "Write a first-person career summary for the candidate. "
        "Max 120 characters, one sentence: key experience, skills, impact. "
        "Use only the given data; never invent. Plain text, no markdown."
    ),
}
