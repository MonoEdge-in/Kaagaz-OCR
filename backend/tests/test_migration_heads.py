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
