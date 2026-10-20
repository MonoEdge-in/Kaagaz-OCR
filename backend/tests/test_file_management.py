"""File management (Phase 2 PRD 1) stage 1, without a database: the department template's
property folder (F3, section 10's edge cases), the type rule's list (O2), the new feature
switch (V8), the folder view (F1) and migration 0041's text. The SQL itself:
test_file_management_db.py.
"""

import importlib.util
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType

import pytest

from kaagaz.api.views import folder
from kaagaz.db import ai_settings
from kaagaz.db import autofile, versions
from kaagaz.db.autofile import FOLDERS, filing_year, property_folder, survey_digits, survey_number, village
from kaagaz.db.documents import PgDocuments
from kaagaz.db.folders import FolderRow
from kaagaz.db.pipeline_store import CONFIDENTIAL_TYPES

MIGRATION = Path(__file__).parents[1] / "migrations" / "versions" / "0041_file_management.py"


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0041_test", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _card(survey: str | None = None, place: str | None = None) -> dict[str, object]:
    identifiers = [] if survey is None else [{"kind": "survey_number", "value": survey}]
    facts = [] if place is None else [{"field": "village", "value": place}]
    return {"identifiers": identifiers, "key_facts": facts}


def test_f3_a_survey_number_is_digits_and_slashes_with_devanagari_digits_as_ascii() -> None:
    assert survey_digits("स.नं. ४४/२") == "44/2"
    assert survey_digits("Survey No. 44") == "44"
    assert survey_digits("गट नं. १२३ / ४") == "123/4"
    assert survey_digits("स.नं.") is None
    assert survey_number(_card("४४")) == "44"
    assert survey_number(_card()) is None
    assert survey_number(None) is None


def test_f3_the_village_is_a_key_fact_in_latin_letters() -> None:
    assert village(_card(place="Wakad")) == "Wakad"
    assert village(_card(place="वाकड")) == "Vakad"  # the Latin spelling the search uses (L3)
    assert village({"key_facts": [{"field": "village_name", "value": "Hinjewadi"}]}) == "Hinjewadi"
    assert village({"key_facts": [{"field": "area", "value": "2 acres"}]}) is None
    assert village(None) is None


def test_f3_section_10_44_slash_2_is_its_own_property_and_never_merges_with_44() -> None:
    assert property_folder("44", "Wakad") == ("property:vkd:44", "Survey 44, Wakad")
    assert property_folder("44/2", "Wakad") == ("property:vkd:44/2", "Survey 44/2, Wakad")
    assert property_folder("44", None) == ("property::44", "Survey 44")  # no village: "Survey 44"
    assert property_folder("7", "Pimpri Chinchwad")[0] == "property:pimpri-cincvd:7"


def test_f3_a_misread_year_files_by_the_upload_year() -> None:
    uploaded = datetime(2026, 10, 20, tzinfo=UTC)
    assert filing_year(date(24, 1, 1), uploaded) == "2026"  # "24" or "0024" is not a year
    assert filing_year(date(2025, 6, 12), uploaded) == "2025"
    assert filing_year(None, uploaded) == "2026"


def test_a_failed_filing_still_runs_version_spotting(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Db:
        @contextmanager
        def customer(self, _customer_id: str) -> Iterator[None]:
            yield None

    def broken(*_args: object) -> str:
        raise ValueError("year 24 is out of range")

    spotted: list[str] = []
    monkeypatch.setattr(autofile, "file_document", broken)
    monkeypatch.setattr(versions, "spot", lambda _conn, _c, d: spotted.append(d) or "none")
    PgDocuments(_Db()).file_document("c1", "d1")  # type: ignore[arg-type]
    assert spotted == ["d1"]


def test_f3_a_devanagari_village_and_a_latin_wakad_deed_share_one_property_key() -> None:
    devanagari = village(_card(place="वाकड"))
    assert property_folder("44", devanagari)[0] == property_folder("44", "Wakad")[0]
    assert property_folder("44/2", "Wakad")[0] != property_folder("44", devanagari)[0]


def test_f3_every_key_the_template_makes_fits_0041s_check() -> None:
    """The CHECK's POSIX patterns read the same in Python's re for these."""
    patterns = re.findall(r"autofile_key ~ '([^']+)'", _migration().NEW_KEYS)
    department = "0b8f6f0e-5b9e-4c55-9a39-5f0e0e0e0e0e"
    keys = [
        "type:invoice",
        f"type:land_record:{department}",
        "party:27AAPFU0939F1ZV",
        property_folder("44/2", "Wakad")[0],
        property_folder("44", None)[0],
        f"year:2026:{department}",
    ]
    for key in keys:
        assert any(re.fullmatch(pattern.strip("^$"), key) for pattern in patterns), key
    assert not any(re.fullmatch(pattern.strip("^$"), "property:Wakad:44") for pattern in patterns)


def test_o2_the_type_rule_starts_land_contracts_and_bank_statements_confidential() -> None:
    assert set(CONFIDENTIAL_TYPES) == {"land_record", "contract", "bank_statement"}
    assert set(CONFIDENTIAL_TYPES) <= set(FOLDERS)


def test_v8_version_spotting_is_a_platform_and_an_organisation_switch() -> None:
    assert "version_spotting" in ai_settings.FEATURES
    migration = _migration()
    upgrade = "\n".join(migration.UPGRADE)
    assert "'version_spotting'" in upgrade
    assert upgrade.count("CREATE OR REPLACE FUNCTION public.kaagaz_op_") == 4
    assert "'version_spotting'" not in "\n".join(migration.DOWNGRADE[2:])  # the old lists come back


def test_f1_a_folder_says_its_kind_and_order() -> None:
    row = FolderRow("f", None, "Land & Legal", datetime(2026, 10, 19, tzinfo=UTC), "department", 2)
    assert folder(row) | {"created_at": None} == {
        "id": "f",
        "name": "Land & Legal",
        "parent_id": None,
        "created_at": None,
        "kind": "department",
        "sort_order": 2,
    }


def test_0041_indexes_the_suggestion_lookups_and_the_owner_filter() -> None:
    upgrade = " ".join(_migration().UPGRADE)
    assert "ON version_suggestions (customer_id, candidate_family_id) WHERE status = 'pending'" in upgrade
    assert "ON version_suggestions (customer_id, document_id)" in upgrade
    assert "ON documents (customer_id, owner_id)" in upgrade
