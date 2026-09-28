"""Kaagaz backend: upload, virus gate, readers, pieces, values, search and cited answers.

The API is ``kaagaz.api`` (``uvicorn kaagaz.api.main:app``), the worker is
``kaagaz.worker_main``, and the operator commands are ``kaagaz.cli``. Anything
waiting for a Vrushit decision is marked PROVISIONAL in its docstring.
"""
