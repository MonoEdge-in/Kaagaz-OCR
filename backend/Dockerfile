# The backend image: the API (uvicorn), the worker and the migrations all run
# from it; docker-compose.yml picks the command. It includes the `ocr` extra
# (RapidOCR on onnxruntime), so the worker can read scans.
#
# Versions are pinned. PROVISIONAL: the backend has no
# lock file yet, so `pip install` takes the newest versions pyproject.toml
# allows at build time. A lock (and hash checking) is Vrushit's call.

FROM python:3.12.14-slim-trixie

# OpenCV, which RapidOCR uses, loads libGL and GLib from the system. Debian 13
# names GLib's package libglib2.0-0t64.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends libgl1 libglib2.0-0t64 \
    && rm -rf /var/lib/apt/lists/*

# Word, PowerPoint and old Excel files (#522) are converted by headless LibreOffice
# (kaagaz/reading/office.py): Writer, Calc and Impress without their windows, and no
# Java. The fonts their pages are drawn in: Noto (Noto Sans Devanagari, so Marathi and
# Hindi render), and the ones metric-compatible with Arial, Times New Roman and Courier
# New (Liberation) and with Calibri (Carlito), so a page breaks where it did in Word.
# The build stops unless soffice runs and a Devanagari font is there.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui \
        fonts-noto-core fonts-liberation fonts-crosextra-carlito \
    && rm -rf /var/lib/apt/lists/* \
    && soffice --version \
    && fc-list :lang=hi family | grep -q "Noto Sans Devanagari"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml constraints.txt ./
COPY kaagaz kaagaz
# constraints.txt pins what indic-transliteration brings in (PRD 2 L3), so the
# image gets the versions checked on 30 Sep 2026, not whatever is newest.
RUN pip install -c constraints.txt ".[ocr]"

# Page pictures are stored as WebP (PRD 1 S1). Pillow's wheel carries libwebp;
# stop the build here if a future Pillow or base image ever lacks it, rather
# than at the first scan.
RUN python -c "from PIL import features; assert features.check('webp'), 'Pillow has no WebP'"

# RapidOCR fetches its model files on first use, into its own package folder,
# which the non-root user below cannot write. Load it once here, as root, so
# the files are part of the image. Then the same for its Devanagari recogniser,
# which the worker uses on Marathi and Hindi scans and only ever loads from disk
# (kaagaz/reading/ocr.py): the build stops unless the file is there with the
# SHA-256 rapidocr 3.9.2 lists for it.
RUN python -c "from rapidocr import RapidOCR; RapidOCR()" \
    && python -c "from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR; RapidOCR(params={'Rec.lang_type': LangRec.DEVANAGARI, 'Rec.ocr_version': OCRVersion.PPOCRV5, 'Rec.model_type': ModelType.MOBILE})" \
    && python -c "import hashlib; from kaagaz.reading.ocr import devanagari_model_path; assert hashlib.sha256(devanagari_model_path().read_bytes()).hexdigest() == 'd6f0a906580e3fa6b324a318718f1f31f268b6ea8ef985f91c2012a37f52c91e', 'the Devanagari model is not the one expected'"

COPY alembic.ini ./
COPY migrations migrations

RUN useradd --create-home --uid 10001 app
USER app

# The commit this image was built from, for /api/health and the operator's Release card
# (#246 O1). docker-compose.yml passes the deployer's KAAGAZ_TAG; "local" otherwise. Last,
# so a new commit rebuilds no layer above.
ARG KAAGAZ_COMMIT=local
ENV KAAGAZ_COMMIT=$KAAGAZ_COMMIT

# uvicorn takes the client's address from X-Forwarded-For (it reads this variable; the compose
# command and CMD below both use it), so each visitor has their own rate bucket, not Caddy's one
# (production QA S2). Safe because only Caddy reaches this port on the server
# (docker-compose.prod.yml publishes no API port) and Caddy, with no trusted_proxies set, replaces
# any X-Forwarded-For a visitor sends with the address it saw.
ENV FORWARDED_ALLOW_IPS="*"

EXPOSE 8000
# No "server: uvicorn" on a reply, and an idle connection kept 75 s, longer than Caddy keeps one
# (keepalive 60s in infra/caddy/Caddyfile), so Caddy never reuses a connection uvicorn just closed
# (a 502). docker-compose.yml's api command repeats these flags.
CMD ["uvicorn", "kaagaz.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header", "--timeout-keep-alive", "75"]
