"""#246 G7: every server script in infra/ec2 parses (``bash -n``), so a merge cannot ship a
deployer that fails on its next timer run. Skipped where there is no bash."""

import shutil
import subprocess
from pathlib import Path

import pytest

EC2 = Path(__file__).resolve().parents[2] / "infra" / "ec2"
SCRIPTS = sorted(EC2.glob("*.sh"))
BASH = shutil.which("bash")


def test_the_scripts_are_found() -> None:
    assert {path.name for path in SCRIPTS} >= {"deploy.sh", "idle-stop.sh", "pgdump.sh"}


@pytest.mark.skipif(BASH is None, reason="no bash on this machine")
@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.name)
def test_each_script_parses(script: Path) -> None:
    # The text goes in on stdin, so no path needs translating (Git Bash or WSL on Windows).
    checked = subprocess.run(  # noqa: S603 - bash -n only parses; it runs nothing
        [str(BASH), "-n"],
        input=script.read_bytes(),
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert checked.returncode == 0, checked.stderr.decode(errors="replace")


@pytest.mark.skipif(BASH is None, reason="no bash on this machine")
def test_a_broken_script_is_caught() -> None:
    checked = subprocess.run(  # noqa: S603 - bash -n only parses; it runs nothing
        [str(BASH), "-n"], input=b"if true; then\n  echo x\n", capture_output=True, check=False, timeout=60
    )
    assert checked.returncode != 0
