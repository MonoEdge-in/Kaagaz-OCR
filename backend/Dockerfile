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

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml ./
COPY kaagaz kaagaz
RUN pip install ".[ocr]"

# Page pictures are stored as WebP (PRD 1 S1). Pillow's wheel carries libwebp;
# stop the build here if a future Pillow or base image ever lacks it, rather
# than at the first scan.
RUN python -c "from PIL import features; assert features.check('webp'), 'Pillow has no WebP'"

# RapidOCR fetches its model files on first use, into its own package folder,
# which the non-root user below cannot write. Load it once here, as root, so
# the files are part of the image.
RUN python -c "from rapidocr import RapidOCR; RapidOCR()"

COPY alembic.ini ./
COPY migrations migrations

RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8000
CMD ["uvicorn", "kaagaz.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
