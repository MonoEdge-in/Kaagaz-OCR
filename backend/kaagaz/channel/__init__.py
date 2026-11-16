"""Kaagaz on WhatsApp's channel (PRD 7 section 15, #603): what the webhook stores and the
channel process (``kaagaz.channel_main``) handles.

The channel works as the person: it builds its Database with ``db_app_role`` and opens only
``db.user`` transactions, so everything it reads goes through PRD 7's views and rule, as in the
browser; ``db.system`` only calls the owner-rights functions (look a number up, claim work,
sweep). It never uses the workers' role (tests/test_whatsapp_guard.py).
"""
