Angelo's PDF reader: pypdf
==========================

Angelo reads the text of a PDF a person attaches to the agent chat with pypdf 6.21.2 (the PyPI package "pypdf",
github.com/py-pdf/pypdf), a pure-Python PDF library under the BSD 3-Clause license. Angelo bundles PyPI's wheel
pypdf-6.21.2-py3-none-any.whl unmodified, renamed pypdf-6.21.2-py3-none-any.pyz (the Angelo repository keeps .whl
files in Git LFS; Python imports from either name):

  macOS:    Angelo.app/Contents/Resources/5.2/scripts/addons_core/angelo_agent/vendor/
  Windows:  5.2\scripts\addons_core\angelo_agent\vendor\ in the install folder

LICENSE.txt here is the license file the wheel carries (pypdf-6.21.2.dist-info/licenses/LICENSE), byte for byte.
MANIFEST lists the bundled file's SHA-256, which is the SHA-256 PyPI publishes for that wheel. The wheel needs
nothing outside Python's standard library to extract text; its optional packages (cryptography or PyCryptodome for
AES, Pillow for images, and others) are not bundled. Without them a PDF encrypted with AES can't be read, and the
attachment says so on its chip; RC4-encrypted PDFs that open without a password are read.

What it reads: untrusted input
------------------------------

The reader runs on anything a person attaches, so Angelo treats every PDF as untrusted. pypdf never runs inside
Angelo itself: a separate process of Angelo's bundled Python runs it (angelo_agent/pdf_text_worker.py), isolated
from installed packages (python -I -S: pypdf is imported only from the bundled file, never from site-packages), with
none of Angelo's environment variables but a short list (PATH, TEMP and the like, never provider keys). It never
runs on Angelo's interface thread, and it is never asked to do anything but extract text: no scripts, links, forms,
images or OCR, and no external program (pypdf's JBIG2 decoder program is switched off).

Limits (angelo_agent/pdf_text.py and pdf_text_worker.py)
---------------------------------------------------------

  Process memory   Linux: fixed hard 1 GiB address-space ceiling; Windows: fixed hard 1 GiB committed
                   process/job ceiling, established before parser import or PDF access. A tighter inherited
                   limit or unavailable/unverified protection refuses extraction; neither budget auto-expands.
                   macOS: sampled physical-footprint termination threshold of 1 GiB, NOT a hard
                   allocation ceiling. The parent binds the owned child PID and start time, checks accounting
                   before releasing a pre-parser pipe barrier, then samples in the bounded output/deadline loop
                   (50 ms maximum requested pipe wait, not a scheduling guarantee). Missing live accounting,
                   changed identity or a sampled threshold crossing refuses extraction and kills/reaps the child.
                   Overshoot and a peak wholly between samples remain possible: this is not the Linux/Windows
                   hard-ceiling guarantee. macOS allocator reservations can exceed the bundled runtime's useful
                   physical footprint, making the same small virtual-address ceiling unusable. Actual OS probes
                   and complete parser/cleanup acceptance are required for each supported runtime.
  Output           at most six bytes per permitted character plus 4 KiB JSON metadata; stderr is discarded.
  File size        20 MiB, checked before parsing (attaching refuses a larger file too).
  Pages            the first 64; the agent is told when pages were left unread.
  Characters       1,000,000.
  Time             pages stop being read after 20 s and the text read so far is kept; the process is killed at
                   30 s whatever it is doing, and the file counts as unreadable.
  Decoded data     no stream, image or array may decode to more than 32 MB, and a stream's decoding work is capped
                   at 100 MB (pypdf's limits, tightened from 75 MB and 300 MB), which stops decompression bombs.
  Parsing work     pypdf's own caps on content-stream operators (2,000,000 per stream), array and dictionary sizes,
                   page-tree and outline depth, and XObject forms per page; nesting deeper than Python's recursion
                   limit ends as a parse failure.

A parse error, a limit reached, a crash or a time-out ends the read of that file only: the attachment's chip says its
text couldn't be read (or that it is locked, or has no text), the agent is told why, and the failure is remembered
for the session rather than retried every turn. tests/python/angelo_agent/test_native_pdf_reader.py checks these
cases with PDFs the bundled pypdf writes at test time, and test_native_pdf_reader_notices.py checks this folder.
