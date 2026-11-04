"""Migration 0073 (#488, Tushar, 5 Nov 2026): Ultra low cost's ceiling up to 55 paise. Both
tables' CHECKs and the operator's two saves take 20 to 55 (19 and 56 refused); the default stays
40 paise. Up, down (a ceiling above 40 lowered to 40) and up again, with rows: the schema, every
function body, comment and grant as before each time."""

import pytest

from kaagaz.db import operator_models as ops
from kaagaz.db.engine import Database, OperatorDatabase
from kaagaz.db.models import ULC_CEILING_PAISE, ULC_DEFAULT_PAISE
from tests import test_migration_0002_db as base
from tests.db_support import migrate, new_customer
from tests.test_isolation_ai_settings import platform, set_defaults, set_org
from tests.test_migration_0023_db import as_owner
from tests.test_migration_0037 import _load
from tests.test_migration_0070_db import _DEFINITION, _refused
from tests.test_migration_0071_db import _state

pytestmark = pytest.mark.db
__all__ = ["platform"]  # the fixture, used by name
fresh = base.fresh
M0073 = _load("0073_ulc_ceiling_55.py")
_INSERT = "INSERT INTO customer_ai_settings (customer_id, scan_reader, ulc_ceiling_paise) VALUES (:c, :r, :p)"
_PLATFORM = "UPDATE platform_ai_settings SET ulc_ceiling_paise = :p"
_CEILINGS = (
    "SELECT (SELECT ulc_ceiling_paise FROM customer_ai_settings WHERE customer_id = :c),"
    " (SELECT ulc_ceiling_paise FROM platform_ai_settings)"
)


def _saves(db: Database) -> dict[str, tuple[str, ...]]:
    """The two saves: (definition, owner, comment, grants)."""
    with db.system() as conn:
        return {each: tuple(conn.execute(_DEFINITION, {"f": f"public.{each}"}).one()) for each in M0073.SAVES}


def test_the_revision_follows_0072_and_the_code_takes_20_to_55_with_40_by_default() -> None:
    assert (M0073.revision, M0073.down_revision) == ("0073", "0072")
    assert ULC_CEILING_PAISE == (M0073.CEILING_LOW, M0073.CEILING_HIGH) == (20, 55)
    assert ULC_DEFAULT_PAISE == M0073.DEFAULT_PAISE == 40


def test_up_down_lowers_a_ceiling_above_40_to_40_and_up_again(fresh: tuple[str, Database]) -> None:
    url, db = fresh
    migrate(url, "0072")
    at_0072, saves_0072 = _state(db), _saves(db)
    customer, _ = new_customer(db)
    assert _refused(db, _INSERT, {"c": customer, "r": "ultra_low_cost", "p": 55}) == "23514"
    assert _refused(db, _PLATFORM, {"p": 41}) == "23514"

    migrate(url, "0073")
    at_0073, saves_0073 = _state(db), _saves(db)
    for signature, (body, *rest) in saves_0073.items():  # only the range moved
        old, *old_rest = saves_0072[signature]
        assert rest == old_rest and body == old.replace("BETWEEN 20 AND 40", "BETWEEN 20 AND 55"), signature
    as_owner(db, _INSERT, {"c": customer, "r": "ultra_low_cost", "p": 55})
    as_owner(db, _PLATFORM, {"p": 41})
    for paise in (19, 56):
        assert _refused(db, "UPDATE customer_ai_settings SET ulc_ceiling_paise = :p", {"p": paise}) == "23514"
        assert _refused(db, _PLATFORM, {"p": paise}) == "23514"

    base._downgrade(url, "0072")
    assert _state(db) == at_0072
    assert _saves(db) == saves_0072
    assert as_owner(db, _CEILINGS, {"c": customer}) == [(40, 40)]  # lowered to 0070's most

    as_owner(
        db, "UPDATE customer_ai_settings SET ulc_ceiling_paise = 30 WHERE customer_id = :c", {"c": customer}
    )
    as_owner(db, _PLATFORM, {"p": None})
    migrate(url, "0073")  # Changed on purpose (#507): head is 0074 (Google sign-in) now
    assert _state(db) == at_0073
    assert _saves(db) == saves_0073
    assert as_owner(db, _CEILINGS, {"c": customer}) == [(30, None)]  # a ceiling within 40 is kept


def test_the_checks_take_20_to_55_paise(db: Database) -> None:
    customer, _ = new_customer(db)
    for paise in (19, 56):
        assert _refused(db, _INSERT, {"c": customer, "r": "ultra_low_cost", "p": paise}) == "23514"
        assert _refused(db, _PLATFORM, {"p": paise}) == "23514"
    for paise in (20, 55):
        as_owner(db, _INSERT, {"c": customer, "r": "ultra_low_cost", "p": paise})
        as_owner(db, "DELETE FROM customer_ai_settings WHERE customer_id = :c", {"c": customer})


def test_the_saves_take_55_and_refuse_19_and_56(db: Database, platform: str) -> None:
    customer, _ = new_customer(db)
    for paise in (19, 56):
        refused = ops.AiChoice(scan_reader="ultra_low_cost", ulc_ceiling_paise=paise)
        assert set_org(db, platform, customer, refused).code == "ceiling_out_of_range"
        defaults = ops.AiChoice(
            chat_levels=["low"], chat_default_level="low", dynamic=False, ulc_ceiling_paise=paise
        )
        assert set_defaults(db, platform, defaults) == "ceiling_out_of_range"
    chosen = ops.AiChoice(scan_reader="ultra_low_cost", ulc_ceiling_paise=55)
    assert set_org(db, platform, customer, chosen).code == "done"
    defaults = ops.AiChoice(
        chat_levels=["low"], chat_default_level="low", dynamic=False, ulc_ceiling_paise=55
    )
    assert set_defaults(db, platform, defaults) == "done"
    with OperatorDatabase(db.engine).operator() as conn:
        own, platform_defaults = ops.org_ai(conn, platform, customer), ops.ai_defaults(conn, platform)
    assert own is not None and own["ulc_ceiling_paise"] == 55
    assert platform_defaults is not None and platform_defaults["ulc_ceiling_paise"] == 55
