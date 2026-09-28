"""Answering: a question in, a cited answer out (docs/system-design/03-answering.md).

``service.AnswerService`` is the entry point. Numbers come from fixed queries
(``fixed_queries.py``); everything else is drafted from search evidence and
checked against it (``verify.py``) before it can reach the customer.
"""
