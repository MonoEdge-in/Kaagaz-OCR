"""What uvicorn runs: ``uvicorn kaagaz.api.main:app``.

Why at import: uvicorn imports this module and takes ``app``. Settings come
from the environment (``.env.example`` lists them); a missing one stops the
process here with its name. Log lines are the JSON the middleware writes,
one per line, at ``LOG_LEVEL``.
"""

import logging

from kaagaz.api.app import create_app
from kaagaz.settings import Settings

settings = Settings.from_env()
logging.basicConfig(level=settings.log_level.upper(), format="%(message)s")
app = create_app(settings)
