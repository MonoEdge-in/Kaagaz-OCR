"""A fix to pdfminer.six (20260107), imported for its effect by both the content check
(kaagaz/scanning/content.py, in its own child process) and the PDF reader
(kaagaz/reading/pdf_reader.py), so a file the check passes is one the reader can open.

#551 (UAT GR-08): a PDF locked with an owner password only and every permission off
(``/P 0``) was refused as one that "could not be checked". pdfminer reads P with
``uint_value``, which returns ``x + 2**32`` for any ``x <= 0``, so 0 became 2**32 and
``struct.pack("<L", p)`` failed while the file was opened (the reader then said
``corrupt_file``). Here P is kept to 32 bits, two's complement, as viewers read it: 0 stays
0, a negative P (the usual -3904 or -44) is what it always was. P only feeds the hash that
makes the decryption key, which pdfminer then checks against the file's /U, so a wrong P can
never give a key that is accepted: a file that needs a password is still refused.

``uint_value`` is pdfdocument's own name for it (imported there, used only for P). If a pdfminer
upgrade stops importing it there, this import fails loudly rather than silently patching nothing.
"""

from pdfminer import pdfdocument
from pdfminer.pdftypes import int_value


def _uint_value(x: object, n_bits: int) -> int:
    return int_value(x) & ((1 << n_bits) - 1)


if not hasattr(pdfdocument, "uint_value"):
    raise ImportError("pdfminer_fix: pdfminer.pdfdocument no longer has uint_value; check the /P 0 fix")
pdfdocument.uint_value = _uint_value  # pdfdocument's own name, see above
