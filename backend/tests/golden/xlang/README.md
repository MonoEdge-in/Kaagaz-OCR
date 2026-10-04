# Cross-language eval documents

Six synthetic scans for `../questions_xlang.json` (council 4 Oct 2026). Every name,
number and address in them is made up: no real person, customer or record.

| File | Language | What it is | Pages |
|---|---|---|---|
| hi-rent-agreement.pdf | Hindi | rent agreement | 2 |
| mr-land-record.pdf | Marathi (Devanagari digits) | 7/12 land record extract | 2 |
| gu-invoice.pdf | Gujarati | tax invoice | 1 |
| bn-loan-letter.pdf | Bengali (Bengali digits) | loan sanction letter | 1 |
| ta-bill.pdf | Tamil | grocery bill | 1 |
| ur-salary-certificate.pdf | Urdu | salary certificate | 1 |

How they were made: HTML pages rendered by headless Chromium with the Windows fonts
Nirmala UI and Segoe UI (real shaping), each page screenshotted at 150 to 200 dpi, then
made to look scanned with Pillow (grey, tilted 0.5 to 2 degrees, slight blur, seeded
noise, JPEG quality 40) and saved as image-only PDFs, with no text layer. The generator
is kept outside the repository. `documents.sha256` freezes the bytes: a changed file
means a new baseline.
