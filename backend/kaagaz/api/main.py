"""What uvicorn runs: ``uvicorn kaagaz.api.main:app``.

Why at import: uvicorn imports this module and takes ``app``. Settings come
from the environment (``.env.example`` lists them); a missing one stops the
process here with its name. Log lines are JSON, one per line, at
``LOG_LEVEL``, in the worker's format (``configure_logging``): an error is
logged by its type, never its message or traceback.
"""

from kaagaz.api.app import configure_logging, create_app
from kaagaz.settings import Settings

settings = Settings.from_env()
configure_logging(settings.log_level)
app = create_app(settings)
