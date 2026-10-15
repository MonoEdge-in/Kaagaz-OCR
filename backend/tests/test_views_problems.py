"""A banner per problem page (PRD 5 U4), without a database: ``page_summary`` gives one
``PageProblem`` per issue in ``read_issues``, in the F1 table's words with its count,
and its fix; an issue with nothing left to count is left out. The review
queue's page has no problems (``page_fields``)."""

from typing import Any, cast

from sqlalchemy import RowMapping

from kaagaz.api.views import page_fields, page_problems, page_summary
from kaagaz.reading.page_quality import ISSUES


def _page(issues: list[str], **columns: Any) -> RowMapping:
    row = {
        "page_no": 1,
        "width": 595.0,
        "height": 842.0,
        "unit": "pt",
        "has_image": True,
        "route": "ocr",
        "effective_dpi": 150,
        "low_resolution": "low_resolution" in issues,
        "read_by": "google/gemini-3.1-pro-preview",
        "read_by_name": "Google: Gemini 3.1 Pro Preview",
        "read_quality": "uncertain",
        "read_issues": issues,
        "unsure_words": 0,
        "illegible_words": 0,
        "unpinned_lines": 0,
        "accepted_by": None,
        "accepted_at": None,
        "read_again_requested_at": None,
        "values_disputed": 0,
        **columns,
    }
    return cast(RowMapping, row)


def test_u4_each_issue_says_what_happened_with_its_count_and_the_fix() -> None:
    row = _page(
        ["low_resolution", "glare", "illegible_words", "readers_disagree", "unpinned"],
        illegible_words=3,
        values_disputed=1,
        unpinned_lines=2,
    )

    assert page_summary(row)["problems"] == [
        {
            "code": "low_resolution",
            # Changed on purpose (UI quality council, 15 Oct 2026): no DPI on user screens.
            "says": "This page was scanned at low quality, so small letters may be misread.",
            "fix": "Scan it again at a higher quality setting, or take the photo closer.",
        },
        {
            "code": "glare",
            "says": "Glare or a shadow covers part of the page.",
            "fix": "Take the photo without flash, away from a window, with the page flat.",
        },
        {
            "code": "illegible_words",
            "says": "3 words couldn't be read.",
            "fix": "Read this page again, or upload a better scan.",
        },
        {
            "code": "readers_disagree",
            "says": "Two readers read 1 value differently.",
            "fix": "Confirm the marked values.",
        },
        {
            "code": "unpinned",
            "says": "The exact spot of 2 lines couldn't be confirmed.",
            "fix": "Confirm the marked values.",
        },
    ]


def test_u4_every_issue_code_has_a_problem_and_a_good_page_has_none() -> None:
    every = list(ISSUES)
    row = _page(every, illegible_words=1, values_disputed=1, unpinned_lines=1)
    assert [problem["code"] for problem in page_problems(row)] == every
    assert page_problems(_page([])) == []


def test_u4_nothing_left_to_count_is_left_out_never_a_made_up_number() -> None:
    row = _page(["low_resolution", "readers_disagree", "faint"], effective_dpi=None)
    # Changed on purpose (UI quality council, 15 Oct 2026): the low quality sentence names
    # no DPI now, so it no longer needs one to be shown.
    assert [problem["code"] for problem in page_problems(row)] == ["low_resolution", "faint"]


def test_the_review_queues_page_has_the_fields_without_problems() -> None:
    row = _page(["faint"])
    assert "problems" not in page_fields(row)
    assert page_summary(row) == {
        **page_fields(row),
        "read_by_name": "Google: Gemini 3.1 Pro Preview",
        "problems": page_problems(row),
    }


def test_model_choice_a_page_says_the_catalogue_name_of_the_model_that_read_it() -> None:
    """The viewer's "Read by {name}" (model choice, council 11 Oct 2026): the catalogue's
    name, from listing's LEFT JOIN; null for a reader the catalogue doesn't list."""
    assert page_summary(_page([]))["read_by_name"] == "Google: Gemini 3.1 Pro Preview"
    local = page_summary(_page([], read_by="rapidocr", read_by_name=None))
    assert (local["read_by"], local["read_by_name"]) == ("rapidocr", None)
