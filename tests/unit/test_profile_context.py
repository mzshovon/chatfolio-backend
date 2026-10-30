from datetime import date

from chatfolio.models.profile import Education, Experience, Project
from chatfolio.services.profile_context import build_profile_context, mentions


def _experience(company: str, role: str = "Engineer", **kwargs: object) -> Experience:
    return Experience(
        company=company,
        role=role,
        start_date=kwargs.pop("start_date", date(2022, 8, 1)),
        is_current=kwargs.pop("is_current", False),
        end_date=kwargs.pop("end_date", None),
        description=kwargs.pop("description", "Built things."),
    )


def test_mentions_ignores_case_punctuation_and_legal_suffix() -> None:
    assert mentions("What did you do at Brain Station 23 PLC?", "Brain Station 23 PLC")
    assert mentions("tell me about brain station 23", "Brain Station 23 PLC.")
    assert mentions("what was your role at Guardian Life Insurance?", "Guardian Life Insurance LTD")


def test_mentions_accepts_name_without_trailing_number() -> None:
    assert mentions("did you enjoy working at Brain Station?", "Brain Station 23 PLC")


def test_mentions_requires_whole_words_and_a_real_match() -> None:
    assert not mentions("Do you know Rust?", "Brain Station 23 PLC")
    assert not mentions("tell me about your iota-like skills", "IOTA Infotech LTD")
    assert not mentions("anything", "")


def test_overview_lists_every_employer_project_and_school() -> None:
    context = build_profile_context(
        "hi",
        [
            _experience("Brain Station 23 PLC", "Team Lead", is_current=True),
            _experience("Guardian Life Insurance LTD", end_date=date(2022, 9, 1)),
        ],
        [Project(title="CFL", tech_stack=[])],
        [Education(institution="NSTU", degree="BSc", field="CSE")],
    )

    assert "Team Lead at Brain Station 23 PLC (Aug 2022-present)" in context.overview
    assert "Engineer at Guardian Life Insurance LTD (Aug 2022-Sep 2022)" in context.overview
    assert "Projects: CFL" in context.overview
    assert "Education: BSc CSE, NSTU" in context.overview
    assert context.pinned == []


def test_named_company_is_pinned_with_its_full_record() -> None:
    context = build_profile_context(
        "What did you do at Brain Station 23 PLC?",
        [
            _experience("Brain Station 23 PLC", description="Led the CFL team."),
            _experience("Guardian Life Insurance LTD"),
        ],
        [],
        [],
    )

    assert len(context.pinned) == 1
    assert "Brain Station 23 PLC" in context.pinned[0]
    assert "Led the CFL team." in context.pinned[0]


def test_empty_profile_has_empty_overview() -> None:
    context = build_profile_context("hello", [], [], [])
    assert context.overview == ""
    assert context.pinned == []
