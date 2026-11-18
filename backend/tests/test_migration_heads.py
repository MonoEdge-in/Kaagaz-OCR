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
    # Changed on purpose (#521, 6 Nov 2026): 0077 (the exact overlap) follows it;
    # the line has no 0076 (Tushar dropped confirmed copies).
    assert scripts.get_revision("0077").down_revision == "0075"


def test_guard_rails_0077a_and_0077b_follow_0077() -> None:
    """#502, #520 (Tushar, 6 Nov 2026): logout's ended sessions (0077a) and Ensemble held by the
    reading pause (0077b) follow versioning's 0077, on one line. Changed on purpose (#522): the
    file kinds' 0078 follows 0077b, so 0077b is no longer the head."""
    scripts = _scripts()
    assert scripts.get_revision("0077a").down_revision == "0077"
    assert scripts.get_revision("0077b").down_revision == "0077a"
    # Changed on purpose (#524, 6 Nov 2026): the amount reasons' 0077c follows 0077b.
    assert scripts.get_revision("0077c").down_revision == "0077b"


def test_the_file_kinds_0078_the_zip_unpack_s_0080_and_trashed_folders_0081_follow() -> None:
    """#522 (6 Nov 2026): 0078 (the file kinds) after the amount reasons' 0077c, then 0080 (a zip
    unpacked into a folder; no 0079 when it was written). Changed on purpose (#542): a folder in
    Trash's 0081 follows 0080."""
    scripts = _scripts()
    assert scripts.get_revision("0078").down_revision == "0077c"
    assert scripts.get_revision("0080").down_revision == "0078"
    assert scripts.get_revision("0081").down_revision == "0080"


def test_the_trashed_folder_parent_0082_the_worker_role_0083_and_the_access_tables_0084_follow() -> None:
    """A13 (stability pass, 7 Nov 2026). Changed on purpose (#587, 14 Nov 2026): the workers'
    role and login (0083) follow 0082. Changed on purpose (#590, 14 Nov 2026): who-can-see-what's
    tables and columns (0084) follow 0083. Changed on purpose (#593, 14 Nov 2026): their triggers
    (0085) follow 0084. Changed on purpose (#595, 14 Nov 2026): the access views (0086) follow 0085.
    Changed on purpose (#599, 14 Nov 2026): the trashed folders' view (0087) follows 0086.
    Changed on purpose (#600, 14 Nov 2026): page turns saved for everyone (0088) follow 0087.
    Changed on purpose (#594, 14 Nov 2026): nothing opens by accident (0089) follows 0088.
    Changed on purpose (#601, 14 Nov 2026): the access API (0090) follows 0089.
    Changed on purpose (#601, 15 Nov 2026): the wall rules (0090b) follow 0090.
    Changed on purpose (#606, 15 Nov 2026): the database rule (0091) follows 0090b.
    Changed on purpose (#606, PR 9b, 15 Nov 2026): its writes over rows the person can't see (0092) follow
    0091.
    Changed on purpose (#605, PR 12, 15 Nov 2026): locked files (0093) follow 0092.
    Changed on purpose (#617 part 1, 16 Nov 2026): the bounded word search (0094) follows 0093.
    Changed on purpose (#647, 16 Nov 2026): each model's supported parameters (0095) follow 0094.
    Changed on purpose (#605, PR 13, 16 Nov 2026): the locked-file pop-ups' names (0096, 0094 on
    their branch) follow 0095.
    Changed on purpose (#603, 16 Nov 2026): Kaagaz on WhatsApp's tables (0097) follow 0096, and
    its voice notes' spend (0098) follows 0097.
    Changed on purpose (#648, 16 Nov 2026): the version finder (0099, 0098 on its branch) follows
    0098, and linking files as versions (0100) follows 0099.
    Changed on purpose (#664, 18 Nov 2026): the backup reader (0101) follows 0100 and is the head."""
    scripts = _scripts()
    assert scripts.get_revision("0082").down_revision == "0081"
    assert scripts.get_revision("0083").down_revision == "0082"
    assert scripts.get_revision("0084").down_revision == "0083"
    assert scripts.get_revision("0085").down_revision == "0084"
    assert scripts.get_revision("0086").down_revision == "0085"
    assert scripts.get_revision("0087").down_revision == "0086"
    assert scripts.get_revision("0088").down_revision == "0087"
    assert scripts.get_revision("0089").down_revision == "0088"
    assert scripts.get_revision("0090").down_revision == "0089"
    assert scripts.get_revision("0090b").down_revision == "0090"
    assert scripts.get_revision("0091").down_revision == "0090b"
    assert scripts.get_revision("0092").down_revision == "0091"
    assert scripts.get_revision("0093").down_revision == "0092"
    assert scripts.get_revision("0094").down_revision == "0093"
    assert scripts.get_revision("0095").down_revision == "0094"
    assert scripts.get_revision("0096").down_revision == "0095"
    assert scripts.get_revision("0097").down_revision == "0096"
    assert scripts.get_revision("0098").down_revision == "0097"
    assert scripts.get_revision("0099").down_revision == "0098"
    assert scripts.get_revision("0100").down_revision == "0099"
    assert scripts.get_revision("0101").down_revision == "0100"
    assert scripts.get_heads() == ["0101"]
