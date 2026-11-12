"""infra/ec2/pgdump.sh and restore-drill.sh (#476) and disk-alert.sh (#480) run for real, with
stand-ins for docker, aws, df, flock and sleep first on their PATH and the real openssl: what
goes into the bucket, that a part that fails leaves no ``done``, that the drill restores what
pgdump wrote, checked against ``done``, and when the disk alert records its day.

The stand-ins are small bash scripts. pgdump's fake docker answers pg_dump, pg_dumpall and
backup-files, and its backup-upload writes stdin into a folder for the bucket and prints the
size and sha256, as kaagaz.cli backup-upload does; the drill's fake aws reads that folder. The
scripts' fixed paths are pointed into a temporary folder.
"""

import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

EC2 = Path(__file__).resolve().parents[2] / "infra" / "ec2"
BASH = shutil.which("bash")
OPENSSL = shutil.which("openssl")
pytestmark = pytest.mark.skipif(BASH is None, reason="no bash on this machine")
# Git Bash starts each process in seconds, so one run takes minutes there, and its pipes do not
# reach a Windows openssl; CI runs these on Linux.
whole_runs = pytest.mark.skipif(
    sys.platform == "win32" or OPENSSL is None, reason="needs Linux or macOS, with openssl"
)

ROLES = "CREATE ROLE kaagaz;\nALTER ROLE kaagaz WITH SUPERUSER INHERIT LOGIN;\nCREATE ROLE kaagaz_app;\n"
DOCKER = r"""#!/usr/bin/env bash
# docker, as pgdump.sh calls it.
state="${0%/bin/docker}"
case "$*" in
  *pg_isready*) ;;
  *pg_dumpall*) printf 'ROLES' ;;
  *"pg_dump "*) printf 'the dump' ;;
  *backup-files*)
    if [[ -f "$state/fail-files" ]]; then
      echo "backup-files: the storage did not answer" >&2
      exit 1
    fi
    tar -C "$state/files" -cf - customers
    echo "backup-files: 2 files" >&2
    ;;
  *backup-upload*)
    key="${*: -1}" # the last argument
    if [[ -f "$state/fail-${key##*/}" ]]; then
      cat >/dev/null
      exit 1
    fi
    mkdir -p "$state/bucket/${key%/*}"
    cat >"$state/bucket/$key"
    printf '%s %s\n' "$(wc -c <"$state/bucket/$key")" "$(sha256sum <"$state/bucket/$key" | cut -d' ' -f1)"
    ;;
  *) echo "unexpected: docker $*" >&2; exit 1 ;;
esac
""".replace("ROLES", ROLES.replace("\n", "\\n"))
DRILL_DOCKER = r"""#!/usr/bin/env bash
# docker, as restore-drill.sh calls it.
state="${0%/bin/docker}"
case "$*" in
  *pg_isready*) [[ ! -f "$state/never-ready" ]] ;;
  *"psql -q -v ON_ERROR_STOP=1"*) cat >"$state/roles-run.sql" ;;
  *"pg_restore -l"*) echo "; dbname: kaagaz" ;;
  *"psql -q -tA"*)
    cat >/dev/null
    if [[ ! -f "$state/no-row" ]]; then echo "1 1"; fi
    ;;
  *restore-files*) cat >/dev/null ;;
  *) ;; # network, run -d, cp, rm, the restore and wget: done
esac
"""
AWS = r"""#!/usr/bin/env bash
# aws s3 cp --quiet s3://<bucket>/<key> <file or ->, from the fake upload's folder.
state="${0%/bin/aws}"
object="$state/bucket/${4#s3://*/}"
if [[ "$5" == - ]]; then cat "$object"; else cp "$object" "$5"; fi
"""
DOCUMENT = "customers/c1/documents/d1"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(0o755)


def copy_with_stand_ins(root: Path, name: str, moves: dict[str, str]) -> Path:
    """infra/ec2/<name> under ``root``, its fixed paths moved and ``root``/bin first on its PATH."""
    script = (EC2 / name).read_text(encoding="utf-8")
    moves = {**moves, "\nset -euo pipefail\n": f'\nset -euo pipefail\nPATH="{root}/bin:$PATH"\n'}
    for fixed, moved in moves.items():
        assert script.count(fixed) == 1, fixed
        script = script.replace(fixed, moved)
    write(root / "infra" / "ec2" / name, script)
    return root / "infra" / "ec2" / name


def pgdump(root: Path, *failing: str) -> subprocess.CompletedProcess[str]:
    """pgdump.sh with its fixed paths under ``root``, backing up to the folder ``root``/bucket."""
    assert BASH is not None and OPENSSL is not None
    write(root / "bin" / "docker", DOCKER)
    write(root / "bin" / "flock", "#!/usr/bin/env bash\n")
    for name in failing:
        (root / f"fail-{name}").touch()
    for path in ("original", "pages/1.webp"):
        (root / "files" / DOCUMENT / path).parent.mkdir(parents=True, exist_ok=True)
        (root / "files" / DOCUMENT / path).write_bytes(b"made-up " + path.encode())
    subprocess.run(  # noqa: S603 - a throwaway key pair, no passphrase
        [
            OPENSSL,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-days",
            "1",
            "-subj",
            "/CN=drill",
            "-noenc",
            "-keyout",
            str(root / "key.pem"),
            "-out",
            str(root / "cert.pem"),
        ],
        capture_output=True,
        check=True,
        timeout=60,
    )
    (root / "kaagaz").mkdir()
    (root / "kaagaz" / ".env").write_text(
        f"BACKUP_BUCKET=kaagaz-backups\nBACKUP_CERT={root / 'cert.pem'}\n", encoding="utf-8"
    )
    script = copy_with_stand_ins(
        root,
        "pgdump.sh",
        {
            "\nKAAGAZ_DIR=/home/ubuntu/kaagaz\n": f"\nKAAGAZ_DIR={root}/kaagaz\n",
            "\nBACKUP_DIR=/home/ubuntu/backups\n": f"\nBACKUP_DIR={root}/backups\n",
            "\nLOCK_FILE=/tmp/kaagaz-deploy.lock\n": f"\nLOCK_FILE={root}/lock\n",
        },
    )
    return subprocess.run(  # noqa: S603 - the copied script, with stand-ins
        [BASH, str(script)], cwd=root, capture_output=True, text=True, check=False, timeout=120
    )


def drill(root: Path, run: str) -> subprocess.CompletedProcess[str]:
    """restore-drill.sh on the folder pgdump wrote, with the throwaway containers faked."""
    assert BASH is not None
    write(root / "bin" / "docker", DRILL_DOCKER)
    write(root / "bin" / "aws", AWS)
    write(root / "bin" / "sleep", "#!/usr/bin/env bash\n")
    script = copy_with_stand_ins(root, "restore-drill.sh", {})
    return subprocess.run(  # noqa: S603 - the copied script, with stand-ins
        [BASH, str(script), "kaagaz-backups", run, str(root / "key.pem")],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def backed_up(root: Path) -> str:
    ran = pgdump(root)
    assert ran.returncode == 0, ran.stderr
    [run_folder] = (root / "bucket").iterdir()
    return run_folder.name


@whole_runs
def test_a_run_uploads_each_part_encrypted_then_done_with_sizes_hashes_and_the_file_count(
    tmp_path: Path,
) -> None:
    run = backed_up(tmp_path)
    assert re.fullmatch(r"\d{8}T\d{6}Z", run)  # its own folder: a second run writes beside it
    folder = tmp_path / "bucket" / run
    parts = ("kaagaz.dump", "roles.sql", "files.tar")
    names = {f"{part}.{end}" for part in parts for end in ("enc", "key.cms")}
    assert {path.name for path in folder.iterdir()} == {*names, "done"}
    assert b"the dump" not in (folder / "kaagaz.dump.enc").read_bytes()
    for part in parts:
        assert (folder / f"{part}.key.cms").read_bytes()[:1] == b"\x30"  # DER: only the key is in cms

    def line(part: str) -> str:
        data = (folder / f"{part}.enc").read_bytes()
        return f"{run}/{part}.enc {len(data)} {hashlib.sha256(data).hexdigest()}\n"

    assert (folder / "done").read_text(encoding="utf-8") == "".join(map(line, parts)) + "files 2\n"
    ran_out = (tmp_path / "backups").iterdir()
    assert [path.name for path in ran_out] == [f"kaagaz-{run[:8]}.dump"]  # and no count file left


@whole_runs
@pytest.mark.parametrize("failing", ["files", "kaagaz.dump.key.cms", "roles.sql.enc"])
def test_a_part_that_fails_leaves_no_done_and_fails_the_run(tmp_path: Path, failing: str) -> None:
    ran = pgdump(tmp_path, failing)
    assert ran.returncode != 0
    assert not list((tmp_path / "bucket").glob("*/done"))
    assert [path.name for path in (tmp_path / "backups").iterdir()] == [
        path.name for path in (tmp_path / "backups").glob("kaagaz-*.dump")
    ]  # the local dump stays, and no count file


@whole_runs
def test_the_drill_restores_what_pgdump_wrote(tmp_path: Path) -> None:
    run = backed_up(tmp_path)
    ran = drill(tmp_path, run)
    assert ran.returncode == 0, ran.stderr
    assert f"downloaded, decrypted and checked backup {run}\n" in ran.stdout
    assert ran.stdout.endswith(f"DRILL PASS {run}\n")
    # The superuser's own CREATE ROLE is left out, so ON_ERROR_STOP stops on any other error.
    assert (tmp_path / "roles-run.sql").read_text(encoding="utf-8") == ROLES.replace(
        "CREATE ROLE kaagaz;\n", ""
    )


def flip_a_byte(root: Path, run: str) -> None:
    part = root / "bucket" / run / "files.tar.enc"
    data = bytearray(part.read_bytes())
    data[100] ^= 1
    part.write_bytes(bytes(data))


@whole_runs
@pytest.mark.parametrize(
    ("change", "says"),
    [
        (flip_a_byte, "files.tar.enc is not the size and sha256 done lists"),
        (lambda root, run: (root / "never-ready").touch(), "did not start in 2 minutes"),
        (lambda root, run: (root / "no-row").touch(), "document d1 has no row"),
    ],
    ids=["changed part", "never ready", "no row"],
)
def test_the_drill_fails_loudly(tmp_path: Path, change: object, says: str) -> None:
    run = backed_up(tmp_path)
    assert callable(change)
    change(tmp_path, run)
    ran = drill(tmp_path, run)
    assert ran.returncode == 1
    assert "DRILL FAIL: " in ran.stderr and says in ran.stderr
    assert "DRILL PASS" not in ran.stdout


def test_env_value_reads_env_as_docker_compose_does(tmp_path: Path) -> None:
    assert BASH is not None
    script = (EC2 / "pgdump.sh").read_text(encoding="utf-8")
    start = script.index("env_value() {\n")
    function = script[start : script.index("\n}\n", start) + 3]
    lines = {
        "PLAIN": "PLAIN=kaagaz",
        "QUOTED": 'QUOTED="a b # not a comment"',
        "SINGLE": "SINGLE='one'  # a comment",
        "COMMENTED": "COMMENTED=value # a comment",
        "HASH": "HASH=value#kept",
        "EXPORTED": "export EXPORTED=yes",
        "SPACED": "  SPACED = trimmed  ",
        "EMPTY": "EMPTY=",
        "WINDOWS": "WINDOWS=crlf\r",
        "TWICE": "TWICE=first\nTWICE=second",
    }
    (tmp_path / ".env").write_text(
        "# a comment line\n" + "\n".join(lines.values()) + "\n", "utf-8", newline=""
    )
    calls = "".join(f'printf "[%s]\\n" "$(env_value {key})"\n' for key in [*lines, "MISSING"])
    got = subprocess.run(  # noqa: S603 - the function, read from the script
        [BASH, "-c", function + calls], cwd=tmp_path, capture_output=True, text=True, check=True, timeout=60
    ).stdout.splitlines()
    assert got == [
        "[kaagaz]",
        "[a b # not a comment]",
        "[one]",
        "[value]",
        "[value#kept]",
        "[yes]",
        "[trimmed]",
        "[]",
        "[crlf]",
        "[second]",
        "[]",
    ]


DF = "#!/usr/bin/env bash\nprintf 'Use%%\\n 91%%\\n'\n"


@whole_runs
@pytest.mark.parametrize("sends", [True, False], ids=["sent", "not sent"])
def test_the_disk_alert_records_the_day_only_once_sent_and_says_loudly_when_not(
    tmp_path: Path, sends: bool
) -> None:
    """#480: run in the backend image on the host network (hop limit 1), with .env as its settings."""
    assert BASH is not None
    write(tmp_path / "bin" / "df", DF)
    docker = f'#!/usr/bin/env bash\necho "$*" >"{tmp_path}/ran"\nexit {int(not sends)}\n'
    write(tmp_path / "bin" / "docker", docker)
    (tmp_path / "kaagaz").mkdir()
    script = copy_with_stand_ins(
        tmp_path,
        "disk-alert.sh",
        {
            "\nKAAGAZ_DIR=/home/ubuntu/kaagaz\n": f"\nKAAGAZ_DIR={tmp_path}/kaagaz\n",
            "\nSENT_FILE=/home/ubuntu/.kaagaz-disk-alert\n": f"\nSENT_FILE={tmp_path}/sent\n",
        },
    )
    ran = subprocess.run(  # noqa: S603 - the copied script, with stand-ins
        [BASH, str(script)], cwd=tmp_path, capture_output=True, text=True, check=False, timeout=120
    )
    assert (tmp_path / "ran").read_text(encoding="utf-8") == (
        "run --rm --network host --env-file .env -e POSTGRES_PASSWORD= -e STORAGE_ADMIN_SECRET_KEY="
        " -e DATABASE_URL=none -e APP_DATABASE_URL= -e OPS_DATABASE_URL= -e RDS_DATABASE_URL= kaagaz-backend:local"
        " python -m kaagaz.cli disk-alert --used 91\n"
    )
    if sends:
        assert (ran.returncode, ran.stdout) == (0, "disk 91% used; emailed\n")
        assert (tmp_path / "sent").exists()
    else:
        assert ran.returncode == 1 and not (tmp_path / "sent").exists()  # tried again next hour
        assert "DISK 91% USED and the alert email was NOT sent" in ran.stderr


@whole_runs
@pytest.mark.parametrize("sends", [True, False], ids=["sent", "not sent"])
def test_an_alert_records_its_day_only_once_sent_and_the_same_one_goes_once_a_day(
    tmp_path: Path, sends: bool
) -> None:
    """A22: ops-alert.sh in the backend image on the host network, as the disk alert."""
    assert BASH is not None
    # The run, the subject and the text (ops-alert --subject S --text T).
    echo = 'echo "$1 $2 $3 ${@: -3:1} ${@: -1}"'
    docker = f'#!/usr/bin/env bash\n{echo} >>"{tmp_path}/ran"\nexit {int(not sends)}\n'
    write(tmp_path / "bin" / "docker", docker)
    (tmp_path / "kaagaz").mkdir()
    script = copy_with_stand_ins(
        tmp_path,
        "ops-alert.sh",
        {
            "\nKAAGAZ_DIR=/home/ubuntu/kaagaz\n": f"\nKAAGAZ_DIR={tmp_path}/kaagaz\n",
            "\nSENT_DIR=/home/ubuntu\n": f"\nSENT_DIR={tmp_path}\n",
        },
    )

    def alert(subject: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - the copied script, with stand-ins
            [BASH, str(script), "kaagaz-deploy.service", subject, "the text"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )

    first, again, other = alert("deploy failed"), alert("deploy failed"), alert("dump failed")
    ran = (tmp_path / "ran").read_text(encoding="utf-8").splitlines()
    if sends:
        assert [first.returncode, again.returncode, other.returncode] == [0, 0, 0]
        assert ran == ["run --rm --network deploy failed the text", "run --rm --network dump failed the text"]
        assert "already emailed today: deploy failed" in again.stdout
    else:
        assert [first.returncode, again.returncode] == [1, 1]  # tried again next time
        assert len(ran) == 3 and not (tmp_path / ".kaagaz-alert-kaagaz-deploy.service").exists()
        assert "ALERT NOT SENT: deploy failed" in first.stderr


@whole_runs
@pytest.mark.parametrize("bad", [True, False], ids=["one unhealthy", "all healthy"])
def test_the_container_check_alerts_only_when_one_is_unhealthy_or_restarting(
    tmp_path: Path, bad: bool
) -> None:
    assert BASH is not None
    names = "kaagaz-ocr-worker-2" if bad else ""
    docker = f'#!/usr/bin/env bash\ncase "$*" in *health=unhealthy*) echo "{names}" ;; esac\n'
    write(tmp_path / "bin" / "docker", docker)
    write(
        tmp_path / "infra" / "ec2" / "ops-alert.sh", f'#!/usr/bin/env bash\necho "$@" >"{tmp_path}/alert"\n'
    )
    script = copy_with_stand_ins(tmp_path, "containers-alert.sh", {})
    ran = subprocess.run(  # noqa: S603 - the copied script, with stand-ins
        [BASH, str(script)], cwd=tmp_path, capture_output=True, text=True, check=False, timeout=120
    )
    assert ran.returncode == 0
    if bad:
        sent = (tmp_path / "alert").read_text(encoding="utf-8")
        assert sent.startswith("containers Kaagaz server: not healthy: kaagaz-ocr-worker-2 ")
    else:
        assert not (tmp_path / "alert").exists() and ran.stdout == "every container healthy\n"
