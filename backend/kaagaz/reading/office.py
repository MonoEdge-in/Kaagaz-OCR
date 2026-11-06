"""Word, PowerPoint and old Excel files, converted by headless LibreOffice (#522, PR 2).

A Word file (``docx``, ``dotx``, ``doc``) or a PowerPoint (``pptx``, ``ppt``) is converted
to a PDF, which the PDF pipeline then reads like any other: the text layer with no AI cost,
pictures inside by OCR, and the page limit on the PDF's pages. The PDF is the document's
rendition, stored beside the original for the viewer (``put_rendition``). An old Excel file
(``xls``) is converted to an XLSX, which the XLSX reader reads; it has no rendition, since
its cells are shown, not a page.

Marathi and Hindi survive into the PDF (the image has Noto Sans Devanagari), but LibreOffice
gives some Devanagari glyphs their text only by /ActualText, which pdfplumber skips: such a
page reads with "(cid:N)" gaps, and its route check would send it to OCR, at a page's cost.
So the pipeline reads a rendition through its own key, ``RENDITION``, whose reader takes
PDFium's reading of such a page (it reads /ActualText) with no OCR or AI cost
(``kaagaz/reading/pdf_reader.py``, ``pdfium_text``); a PDF upload is read as before.

The rendition is derived from the original, like the page pictures, so it never counts
towards the organisation's storage limit (``kaagaz/db/storage_limits.py``: the documents'
``size_bytes``, the originals' sizes), and it is deleted with its document.

Each conversion runs ``soffice --headless --norestore --nolockcheck --convert-to`` once:

* in a new temporary folder (``lo-<random>``), removed after, whatever happened: the file
  LibreOffice opens is ``document.<kind>``, never the user's file name, its output goes to
  ``out/``, and its profile (``-env:UserInstallation``) is new each time, so no setting,
  macro or recent file of one conversion reaches the next;
* with that profile written first (``PROFILE``): macros never run (security level "very
  high" with no trusted place, and macro execution off altogether), links to other files
  or addresses are never updated (Writer's and Calc's link update "never"), and links from
  a document to anything outside it are blocked (``BlockUntrustedRefererLinks``);
* with none of the worker's environment (no key, no proxy), only PATH, a HOME and TMPDIR in
  the folder, and the language;
* in a process group of its own, killed whole (SIGKILL) after ``TIMEOUT_SECONDS``, and
  with resource limits (Linux): CPU seconds, the largest file it may write
  (``OUTPUT_MAX_BYTES``), its address space, and no core file.

Macros: a headless conversion never runs a document's macros, even with a profile that
allows them, and this profile stops them when a document is opened too (the backend image's
tests show both, with a control whose macro does run: tests/test_office_real.py). An
OpenDocument file with macros dressed as a .docx is not opened at all: LibreOffice opens a
.docx only as one.

What this can't enforce: the network. The worker's network is what it is; LibreOffice is
only told never to fetch (the link settings above). The backend image's tests prove a
linked picture and an INCLUDEPICTURE field fetch nothing, while the same file, converted
with link updates on and nothing blocked, does fetch. Other ways a document could reach
out (an OLE object's link, a Calc external reference in an .xls) are covered by the same
settings but not each tested.

A file LibreOffice can't open (a password, damage), a conversion that runs out of time,
and an output that is missing, empty, of the wrong kind or too large all raise
``ReadError(UNREADABLE)``, outside any ``except`` block, so nothing that can quote the file
is chained on. The pipeline fails the document with it, and the file list says "This Word
file could not be opened. If it has a password, remove it and upload again."
"""

import contextlib
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from kaagaz.ingestion import resolve, sniff
from kaagaz.ingestion.ports import RenditionStorage
from kaagaz.reading.spans import ReadError

_log = logging.getLogger(__name__)

PDF_KINDS = frozenset({resolve.DOCX, resolve.DOTX, resolve.DOC, resolve.PPTX, resolve.PPT})
KINDS = PDF_KINDS | {resolve.XLS}
# The reader socket's key for a Word or PowerPoint file's PDF rendition (kaagaz/reading/wiring.py):
# read as a PDF is, with PDFium's text where pdfplumber can't read it (the module notes).
RENDITION = "rendition"
UNREADABLE = "office_unreadable"  # the failure code (kaagaz/api/views.py says it in words)
TIMEOUT_SECONDS = 60
OUTPUT_MAX_BYTES = 512 * 1024 * 1024  # more than any PDF the page limit lets through
_CPU_SECONDS = 2 * TIMEOUT_SECONDS  # a backstop: the timeout kills it first
_ADDRESS_SPACE_BYTES = 3 * 1024 * 1024 * 1024  # a worker has 4 GiB (docker-compose.prod.yml)
_SOURCE = "document"

# What ``--convert-to`` asks for, and the first bytes the output must start with.
_PDF_TARGET = ("pdf", b"%PDF-")
_TARGETS = {
    **dict.fromkeys(PDF_KINDS, _PDF_TARGET),
    resolve.XLS: ("xlsx:Calc MS Excel 2007 XML", b"PK\x03\x04"),
}

# The profile each conversion starts with (registrymodifications.xcu). Macro security level 3
# is "very high": only macros from trusted places run, and there are none. Writer's link
# update 0 and Calc's 1 are "never" (sw linkenum.hxx NEVER, sc ScLkUpdMode LM_NEVER).
_SETTINGS = (
    ("/org.openoffice.Office.Common/Security/Scripting", "MacroSecurityLevel", "3"),
    ("/org.openoffice.Office.Common/Security/Scripting", "DisableMacrosExecution", "true"),
    ("/org.openoffice.Office.Common/Security/Scripting", "BlockUntrustedRefererLinks", "true"),
    ("/org.openoffice.Office.Writer/Content/Update", "Link", "0"),
    ("/org.openoffice.Office.Calc/Content/Update", "Link", "1"),
    ("/org.openoffice.Office.Common/Misc", "UseLocking", "false"),
)
PROFILE = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<oor:items xmlns:oor="http://openoffice.org/2001/registry" '
    'xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
    + "".join(
        f'<item oor:path="{path}"><prop oor:name="{name}" oor:op="fuse">'
        f"<value>{value}</value></prop></item>\n"
        for path, name, value in _SETTINGS
    )
    + "</oor:items>\n"
)

# How a conversion's command is run: (command, environment, timeout in seconds). Raises
# subprocess.TimeoutExpired, after the process group is killed, or OSError.
Run = Callable[[Sequence[str], dict[str, str], float], None]


def read_as(kind: str) -> str:
    """What a converted file of ``kind`` is read as: ``pdf``, or ``xlsx`` for an old Excel file."""
    if kind not in KINDS:
        raise ValueError(f"not a kind LibreOffice converts: {kind!r}")
    return sniff.PDF if kind in PDF_KINDS else resolve.XLSX


def drawn_from(
    storage: RenditionStorage, customer_id: str, document_id: str, detected_type: str, data: bytes
) -> tuple[str, bytes]:
    """The type and bytes a stored document's pages are drawn from again (Ensemble, land facts):
    a Word or PowerPoint file's PDF rendition, since its pages were read from it; any other
    file, its own ``data``. Raises BlobNotFound when the rendition is missing."""
    if detected_type in PDF_KINDS:
        return sniff.PDF, storage.get_rendition(customer_id, document_id)
    return detected_type, data


def run_limited(
    command: Sequence[str], env: Mapping[str, str], timeout: float, *, max_file_bytes: int = OUTPUT_MAX_BYTES
) -> None:
    """Run ``command`` with only ``env``, in a process group of its own with resource limits,
    and wait at most ``timeout`` seconds: then the whole group is killed and
    subprocess.TimeoutExpired raised. Any process of the group still there when it ends is
    killed too (LibreOffice starts soffice.bin under oosplash). OSError on Windows, where
    the worker never runs (laptops only)."""
    if sys.platform != "win32":

        def limits() -> None:  # in the child, before soffice starts: setrlimit only
            import resource

            resource.setrlimit(resource.RLIMIT_CPU, (_CPU_SECONDS, _CPU_SECONDS))
            resource.setrlimit(resource.RLIMIT_FSIZE, (max_file_bytes, max_file_bytes))
            resource.setrlimit(resource.RLIMIT_AS, (_ADDRESS_SPACE_BYTES, _ADDRESS_SPACE_BYTES))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

        process = subprocess.Popen(  # noqa: S603 (a fixed command, never a shell)
            list(command),
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,  # never buffered: a file could make it log gigabytes
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            preexec_fn=limits,
        )
        try:
            process.wait(timeout=timeout)
        finally:
            with contextlib.suppress(ProcessLookupError):  # the group is gone already: fine
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        return
    raise OSError("LibreOffice conversions run on Linux only")


class OfficeConverter:
    """``convert(kind, data)``: the file's PDF (or, for ``xls``, XLSX) bytes, by ``soffice``
    at the path given. ``run`` is ``run_limited`` but in tests."""

    def __init__(
        self,
        soffice: str,
        *,
        timeout: float = TIMEOUT_SECONDS,
        max_output_bytes: int = OUTPUT_MAX_BYTES,
        run: Run | None = None,
    ) -> None:
        self._soffice = soffice
        self._timeout = timeout
        self._max_output_bytes = max_output_bytes
        self._run = run or (lambda command, env, seconds: run_limited(command, env, seconds))

    def convert(self, kind: str, data: bytes) -> bytes:
        target, magic = _TARGETS.get(kind, (None, b""))
        if target is None:
            raise ValueError(f"not a kind LibreOffice converts: {kind!r}")
        folder = Path(tempfile.mkdtemp(prefix="lo-"))
        try:
            output, code = self._converted(folder, kind, target, data)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        if output is None or not output.startswith(magic):
            _log.warning("office conversion failed", extra={"kind": kind, "code": code})
            raise ReadError(UNREADABLE)
        return output

    def _converted(self, folder: Path, kind: str, target: str, data: bytes) -> tuple[bytes | None, str]:
        """The output's bytes, or None with why (a short code, never the file's words)."""
        profile, out_dir = folder / "profile", folder / "out"
        (profile / "user").mkdir(parents=True)
        (profile / "user" / "registrymodifications.xcu").write_text(PROFILE, encoding="utf-8")
        out_dir.mkdir()
        source = folder / f"{_SOURCE}.{kind}"
        source.write_bytes(data)
        command = [
            self._soffice,
            f"-env:UserInstallation={profile.as_uri()}",
            "--headless",
            "--invisible",
            "--nologo",
            "--nodefault",
            "--norestore",
            "--nolockcheck",
            "--convert-to",
            target,
            "--outdir",
            str(out_dir),
            str(source),
        ]
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(folder),
            "TMPDIR": str(folder),
            "LANG": "C.UTF-8",
            "SAL_USE_VCLPLUGIN": "svp",  # draws with no display
        }
        try:
            self._run(command, env, self._timeout)
        except subprocess.TimeoutExpired:
            return None, "office_timeout"
        except OSError:
            return None, "office_not_started"
        output = out_dir / f"{_SOURCE}.{target.split(':')[0]}"
        if not output.is_file():
            return None, "office_no_output"  # a password, damage, or a file it can't open
        size = output.stat().st_size
        if size == 0 or size > self._max_output_bytes:
            return None, "office_output_size"
        return output.read_bytes(), "converted"
