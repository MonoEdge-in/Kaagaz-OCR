"""The migrations form one line, without a database (24 Oct 2026). Two files both said
``revision = "0043"`` (#212's model rate limits and #220's organization words), so
``alembic upgrade head`` found two heads and stopped, and with it the migrate container and
every deploy of production. The rate limits are now 0046, after 0045."""

import re
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND = Path(__file__).resolve().parents[1]
VERSIONS = BACKEND / "migrations" / "versions"


def _revision(path: Path) -> str:
    # Changed on purpose (30 Oct 2026): a letter may follow the 4 digits ("0061b"), for a
    # revision made while another branch holds the next number; the line still has one head.
    match = re.search(r'^revision = "(\d{4}[a-z]?)"$', path.read_text(encoding="utf-8"), re.MULTILINE)
    assert match is not None, path.name
    return match.group(1)


def test_each_migration_has_its_own_revision_id_and_its_file_starts_with_it() -> None:
    revisions = {path.name: _revision(path) for path in VERSIONS.glob("*.py")}
    assert len(set(revisions.values())) == len(revisions)
    assert [name for name, revision in revisions.items() if not name.startswith(f"{revision}_")] == []


def _scripts() -> ScriptDirectory:
    config = Config()
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    return ScriptDirectory.from_config(config)


def test_alembic_finds_one_head() -> None:
    assert _scripts().get_heads() == [max(_revision(path) for path in VERSIONS.glob("*.py"))]


def test_productions_0061c_is_a_branch_off_0061b_that_0071_merges() -> None:
    """Production ran production-with-folder-upload's 0061c (#484, 4 Nov 2026), so Alembic must
    find it; 0071 merges it with 0070, so the line still has one head."""
    scripts = _scripts()
    assert scripts.get_revision("0061c").down_revision == "0061b"
    assert scripts.get_revision("0071").down_revision == ("0070", "0061c")


def test_0071_is_the_merge_alone_and_0072_adds_after_it() -> None:
    """#484, 5 Nov 2026: the test server ran 0071 as #509 merged it, so 0071 is the merge alone
    (it never runs there again) and "Add new files only" is 0072's, after it."""
    scripts = _scripts()
    assert scripts.get_revision("0072").down_revision == "0071"
    merge = (VERSIONS / "0071_folder_upload.py").read_text(encoding="utf-8")
    assert "op.execute" not in merge and "items_counted" not in merge.split('"""')[-1]


def test_googles_0074_comes_after_0073() -> None:
    """#507's Google sign-in was 0072 on its own branch; with folder upload's 0072 and Ultra low
    cost's 0073 first (5 Nov 2026), it is 0074. Changed on purpose (#516): the head is now 0075,
    the merge of 0074 with production's 0061d."""
    scripts = _scripts()
    assert scripts.get_revision("0073").down_revision == "0072"
    assert scripts.get_revision("0074").down_revision == "0073"


def test_productions_0061d_is_a_branch_off_0061c_that_0075_merges() -> None:
    """Production ran the storage limit's 0061d (#516, 5 Nov 2026), so Alembic must find it; 0075
    merges it with 0074 and adds nothing (as 0071), so the line still has one head."""
    scripts = _scripts()
    assert scripts.get_revision("0061d").down_revision == "0061c"
    assert scripts.get_revision("0075").down_revision == ("0074", "0061d")
    merge = (VERSIONS / "0075_org_storage_limit_merge.py").read_text(encoding="utf-8")
    assert "op.execute" not in merge
    # Changed on purpose (#522): the versioning PR's 0077 follows 0075, then 0078 (the file kinds).
    assert scripts.get_revision("0077").down_revision == "0075"
    assert scripts.get_revision("0078").down_revision == "0077"
    assert scripts.get_heads() == ["0078"]
