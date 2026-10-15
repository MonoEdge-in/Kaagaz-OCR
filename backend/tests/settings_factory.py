"""Settings for tests: every field filled with a safe laptop value.

Tests never read the environment for settings, except DATABASE_URL for the
database tests (tests/db_support.py). Pass overrides by field name.
"""

import dataclasses
import os
from typing import Any

from kaagaz.settings import Settings

TEST_DATABASE_URL = os.environ.get("DATABASE_URL", "")

_BASE: dict[str, Any] = {
    "database_url": TEST_DATABASE_URL or "postgresql://unused@localhost/unused",
    "db_app_role": "kaagaz_app",
    "storage_endpoint_url": "http://localhost:8333",
    "storage_region": "us-east-1",
    "storage_bucket": "kaagaz-test",
    "storage_access_key": "test-access",
    "storage_secret_key": "test-secret",
    "queue_endpoint_url": "http://localhost:9324",
    "queue_url": "http://localhost:9324/000000000000/kaagaz-jobs",
    "queue_region": "us-east-1",
    "queue_lease_seconds": 60,
    "queue_wait_seconds": 1,
    "job_max_attempts": 5,
    "scanner": "eicar_dev",
    "clamav_host": None,
    "clamav_port": None,
    "scanner_timeout_seconds": 5.0,
    "max_upload_bytes": 5 * 1024 * 1024,
    "max_image_bytes": 2 * 1024 * 1024,
    "max_sheet_bytes": 1 * 1024 * 1024,
    "allowed_types": frozenset({"pdf", "png", "jpeg", "tiff", "zip", "text"}),
    "csv_max_cells": 100_000,
    "xlsx_max_entries": 200,
    "xlsx_max_member_bytes": 5_000_000,
    "xlsx_max_total_bytes": 20_000_000,
    "xlsx_max_cells": 100_000,
    "xlsx_max_text_chars": 10_000_000,
    "max_pages": 50,
    "page_image_dpi": 100,
    "max_image_pixels": 40_000_000,
    "ocr_engine": "none",
    "ocr_timeout_seconds": 5.0,
    "ocr_dpi": 100,
    "ocr_dpi_rule": "fixed",
    "ocr_model": "google/gemini-3.1-pro-preview",
    "openrouter_api_key": None,  # laptops and CI call no model through OpenRouter
    "openrouter_base_url": "https://openrouter.ai/api/v1",
    "openrouter_providers": frozenset({"google-vertex"}),
    "openrouter_model_vendors": frozenset({"google", "anthropic"}),  # DEFAULT_MODEL_VENDORS
    "ocr_media_resolution": "high",
    "ocr_thinking_level": "low",
    "ocr_retry_media_resolution": "high",  # the "detail" shape sends low or high only
    "ocr_retry_thinking_level": "high",
    "ocr_page_concurrency": 6,
    "cpu_workers": 2,
    "max_check_crops": 200,
    "model_rpm": 60,
    "reader_outage_fallback_minutes": 15,
    "worker_replicas": 3,
    "max_active_per_customer": 2,
    "queue_express_url": None,  # one lane unless a test gives the express queue
    "express_max_pages": 3,
    "max_files_per_batch": 100,
    "text_layer_min_chars": 40,
    "text_layer_min_good_ratio": 0.9,
    "scan_image_coverage": 0.5,
    "blank_page_stddev": 3.0,
    "low_res_dpi": 150,
    "model_enabled": False,
    "model_chat": "google/gemini-3.1-pro-preview",
    "model_embed": "google/gemini-embedding-2",
    "embedding_dim": 1024,
    "model_timeout_seconds": 5.0,
    "model_card_max_tokens": 1024,
    "model_answer_max_tokens": 512,
    "model_extract_timeout_seconds": 5.0,
    "card_max_chars": 12000,
    "extract_window_chars": 1700,
    "extract_window_overlap": 400,
    "extract_max_windows": 10,
    "session_secret": "test-session-secret-that-is-long-enough-000",
    "session_hours": 12,
    "cookie_secure": False,
    "totp_encryption_key": "test-totp-encryption-key-not-the-session-secret-000",
    "login_max_failures": 5,
    "login_lock_minutes": 15,
    "admin_session_minutes": 60,
    "trash_days": 30,
    "reprocess_per_hour": 20,
    "search_ef": 100,
    "search_max_documents": 20,
    "answer_max_evidence": 8,
    "answer_min_evidence_score": 0.2,
    "answer_neighbours": 1,
    "chat_history_turns": 3,
    "answer_translate_question": False,
    "model_translate_timeout_seconds": 20.0,
    "model_context_tokens": 200_000,
    "answer_whole_doc_tokens": 120_000,
    "topic_max_per_doc": 40,
    "topic_expand_max_pieces": 12,
    "query_aspects_max": 6,
    "chat_summary_every": 6,
    "timezone": "Asia/Kolkata",
    "rate_limit_per_minute": 1000,
    "chat_questions_per_minute": 1000,
    "log_level": "WARNING",
}


def make_settings(**overrides: Any) -> Settings:
    unknown = set(overrides) - {field.name for field in dataclasses.fields(Settings)}
    if unknown:
        raise TypeError(f"unknown settings: {sorted(unknown)}")
    return Settings(**{**_BASE, **overrides})
