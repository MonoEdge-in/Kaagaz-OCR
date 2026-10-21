"""The upgrade sweep's mark_for_upgrade gives _REPROCESS every value it binds (16 Oct 2026:
"model" was passed where the statement binds :m, :mode and :also, so the sweep failed)."""

import inspect
import re

from kaagaz.db import documents


def test_mark_for_upgrade_passes_every_bind_of_the_reprocess_statement() -> None:
    binds = set(re.findall(r"(?<!:):([a-z_]+)", documents._REPROCESS))
    line = next(
        row for row in inspect.getsource(documents.mark_for_upgrade).splitlines() if "params = {" in row
    )
    given = set(re.findall(r'"([a-z_]+)":', line))
    assert binds <= given, sorted(binds - given)
