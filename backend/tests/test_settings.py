"""Start-up settings for the scan reader (PRD 2 P3, P5, R5) and the lease check (AR1)."""

from pathlib import Path
from typing import Any

import pytest

from kaagaz import settings as settings_module
from kaagaz.reading import ocr_dpi
from kaagaz.settings import Settings, SettingsError
from tests.settings_factory import make_settings

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"
OCR_SETTINGS = ["OCR_ENGINE", "OCR_TIMEOUT_SECONDS", "OCR_DPI"]
KEY = "sk-or-v1-" + "k" * 64
# PRD 5 section 7: the settings this backend no longer reads, with values an older .env
# may still hold. Each name is split so the removed-names guard finds no copy of it here.
REMOVED_SETTINGS = {
    "OCR_" + "URL": "http://ocr:8080",
    "MODEL_" + "BASE_URL": "http://localhost:11434",
    "DOCKER_MODEL_" + "BASE_URL": "http://host.docker.internal:11434",
    "MODEL_" + "VISION": "a-local-vision-model:3b",
    "MODEL_" + "NUM_CTX": "8192",
    "MODEL_" + "WINDOW_MAX_TOKENS": "2048",
}


def shipped(**changes: str | None) -> dict[str, str]:
    """.env.example as a mapping, with usable secrets (both placeholders are too
    short on purpose), then ``changes`` (None removes a setting)."""
    env = {"SESSION_SECRET": "x" * 32, "TOTP_ENCRYPTION_KEY": "y" * 32}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            name, _, value = line.partition("=")
            env.setdefault(name.strip(), value.strip())
    for changed, new_value in changes.items():
        if new_value is None:
            env.pop(changed)
        else:
            env[changed] = new_value
    return env


def test_the_shipped_env_example_parses_with_the_scan_reader_defaults() -> None:
    settings = Settings.from_env(shipped())
    assert (settings.ocr_engine, settings.ocr_timeout_seconds, settings.ocr_dpi) == (
        "rapidocr",
        60.0,  # one Gemini page (PRD 5 Q8; was 300)
        300,
    )
    assert (settings.queue_lease_seconds, settings.page_image_dpi) == (1200, 150)


@pytest.mark.parametrize("name", OCR_SETTINGS)
def test_a_missing_scan_reader_setting_stops_start_up_by_name(name: str) -> None:
    with pytest.raises(SettingsError, match=name):
        Settings.from_env(shipped(**{name: None}))


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("OCR_ENGINE", "tesseract"),
        ("OCR_TIMEOUT_SECONDS", "0"),
        ("OCR_TIMEOUT_SECONDS", "-5"),
        ("OCR_TIMEOUT_SECONDS", "nan"),
        ("OCR_TIMEOUT_SECONDS", "inf"),
        ("OCR_TIMEOUT_SECONDS", "ten"),
        ("OCR_DPI", "0"),
    ],
)
def test_a_bad_scan_reader_setting_stops_start_up_by_name(name: str, value: str) -> None:
    with pytest.raises(SettingsError, match=name):
        Settings.from_env(shipped(**{name: value}))


@pytest.mark.parametrize("engine", ["gemini", "rapidocr", "none"])
def test_each_engine_name_is_accepted(engine: str) -> None:
    # gemini needs a key (tests/test_gemini_settings.py)
    assert Settings.from_env(shipped(OCR_ENGINE=engine, OPENROUTER_API_KEY=KEY)).ocr_engine == engine


def test_a_removed_setting_still_in_the_env_is_ignored() -> None:
    """PRD 5 section 7: the deploy runs unattended, so an older .env with every removed
    setting still starts, exactly as without them."""
    assert Settings.from_env(shipped(**REMOVED_SETTINGS)) == Settings.from_env(shipped())


def test_the_old_layout_reader_stops_start_up_by_name() -> None:
    """PRD 5 section 7: OCR_ENGINE may no longer name the removed reader."""
    with pytest.raises(SettingsError, match="OCR_ENGINE"):
        Settings.from_env(shipped(OCR_ENGINE="padd" + "leocr_vl"))


@pytest.mark.parametrize(("name", "other"), [("MODEL_CHAT", "MODEL_EMBED"), ("MODEL_EMBED", "MODEL_CHAT")])
def test_a_local_model_with_the_model_on_stops_start_up_by_name(name: str, other: str) -> None:
    """PRD 5 M1, M5: with MODEL_ENABLED, MODEL_CHAT and MODEL_EMBED are OpenRouter model IDs;
    a local model name (it used to fall back to a local server) stops start-up by name."""
    gemini = {"MODEL_CHAT": "google/gemini-3.1-pro-preview", "MODEL_EMBED": "google/gemini-embedding-2"}
    on = {**gemini, "MODEL_ENABLED": "true", "OPENROUTER_API_KEY": KEY}
    assert Settings.from_env(shipped(**on)).model_enabled
    with pytest.raises(SettingsError, match=name) as caught:
        Settings.from_env(shipped(**{**on, name: "a-local-model:4b"}))
    assert other not in str(caught.value)
    off = Settings.from_env(shipped(**{**on, "MODEL_ENABLED": "false", name: "a-local-model:4b"}))
    assert not off.model_enabled  # with the model off nothing is called, so nothing is refused


def test_one_ocr_request_must_end_well_inside_the_lease() -> None:
    """The longest step without a heartbeat is one OCR request: with less than two
    minutes to spare, plus the 15 s the worker may wait to renew a lease, a slow page
    outlives the lease and a second worker starts."""
    fits = Settings.from_env(shipped(OCR_TIMEOUT_SECONDS="1065", QUEUE_LEASE_SECONDS="1200"))
    assert fits.ocr_timeout_seconds == 1065.0
    for timeout, lease in (("1066", "1200"), ("900", "900")):
        with pytest.raises(SettingsError, match="OCR_TIMEOUT_SECONDS") as caught:
            Settings.from_env(shipped(OCR_TIMEOUT_SECONDS=timeout, QUEUE_LEASE_SECONDS=lease))
        assert "QUEUE_LEASE_SECONDS" in str(caught.value)


def test_a_dpi_rule_needs_two_ocr_requests_inside_the_lease() -> None:
    """A page read again at the ceiling (OCR_DPI_RULE) makes two requests between heartbeats."""
    twice = {"OCR_DPI_RULE": "text_height", "QUEUE_LEASE_SECONDS": "1200"}
    assert Settings.from_env(shipped(OCR_TIMEOUT_SECONDS="532", **twice)).ocr_timeout_seconds == 532.0
    with pytest.raises(SettingsError, match="2 x OCR_TIMEOUT_SECONDS"):
        Settings.from_env(shipped(OCR_TIMEOUT_SECONDS="533", **twice))
    once = Settings.from_env(shipped(OCR_TIMEOUT_SECONDS="533", QUEUE_LEASE_SECONDS="1200"))
    assert once.ocr_dpi_rule == "fixed"


def test_the_ocr_dpi_rule_is_fixed_unless_set_to_one_the_readers_know() -> None:
    """Council 5 Oct 2026: OCR_DPI_RULE is optional, so an older .env keeps today's DPI."""
    unset = shipped()
    unset.pop("OCR_DPI_RULE", None)
    assert Settings.from_env(unset).ocr_dpi_rule == "fixed"
    assert Settings.from_env(shipped(OCR_DPI_RULE=" ")).ocr_dpi_rule == "fixed"  # empty is unset
    for rule in sorted(ocr_dpi.DPI_RULES):
        assert Settings.from_env(shipped(OCR_DPI_RULE=rule)).ocr_dpi_rule == rule
    for bad in ("auto", "Text_Height", "400"):
        with pytest.raises(SettingsError, match="OCR_DPI_RULE"):
            Settings.from_env(shipped(OCR_DPI_RULE=bad))


ROUTE_SETTINGS = [
    "TEXT_LAYER_MIN_CHARS",
    "TEXT_LAYER_MIN_GOOD_RATIO",
    "SCAN_IMAGE_COVERAGE",
    "BLANK_PAGE_STDDEV",
    "LOW_RES_DPI",
    "REPROCESS_PER_HOUR",
]


def test_the_shipped_env_example_has_the_page_route_starting_values() -> None:
    settings = Settings.from_env(shipped())
    assert (
        settings.text_layer_min_chars,
        settings.text_layer_min_good_ratio,
        settings.scan_image_coverage,
        settings.blank_page_stddev,
        settings.low_res_dpi,
        settings.reprocess_per_hour,
    ) == (40, 0.9, 0.5, 3.0, 150, 20)


@pytest.mark.parametrize("name", ROUTE_SETTINGS)
def test_a_missing_page_route_setting_stops_start_up_by_name(name: str) -> None:
    with pytest.raises(SettingsError, match=name):
        Settings.from_env(shipped(**{name: None}))


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TEXT_LAYER_MIN_CHARS", "0"),
        ("TEXT_LAYER_MIN_GOOD_RATIO", "0"),
        ("TEXT_LAYER_MIN_GOOD_RATIO", "1.5"),
        ("TEXT_LAYER_MIN_GOOD_RATIO", "nan"),
        ("SCAN_IMAGE_COVERAGE", "-0.1"),
        ("SCAN_IMAGE_COVERAGE", "inf"),
        ("BLANK_PAGE_STDDEV", "-1"),
        ("BLANK_PAGE_STDDEV", "nan"),
        ("LOW_RES_DPI", "0"),
        ("REPROCESS_PER_HOUR", "0"),
    ],
)
def test_a_bad_page_route_setting_stops_start_up_by_name(name: str, value: str) -> None:
    with pytest.raises(SettingsError, match=name):
        Settings.from_env(shipped(**{name: value}))


# The settings that are secrets (council security note 7): repr never shows them.
SECRET_FIELDS = [
    "database_url",
    "ops_database_url",  # #479
    "storage_access_key",
    "storage_secret_key",
    "session_secret",
    "totp_encryption_key",
    "openrouter_api_key",  # PRD 5: never in the repo, logs or errors
]


def test_repr_shows_none_of_the_secrets() -> None:
    canaries = {name: f"canary-{name}-{'z' * 32}" for name in SECRET_FIELDS}
    text = repr(make_settings(**canaries))
    assert [name for name, value in canaries.items() if value in text] == []
    assert "db_app_role='kaagaz_app'" in text  # the other settings still show


def test_the_shipped_embedding_dim_is_the_vector_column_size() -> None:
    assert Settings.from_env(shipped()).embedding_dim == 1024


@pytest.mark.parametrize("value", ["0", "768", "1023", "1536"])
def test_an_embedding_dim_other_than_1024_stops_start_up(value: str) -> None:
    """The pieces' vector(1024) column (migration 0001) holds no other size."""
    with pytest.raises(SettingsError, match="EMBEDDING_DIM must be 1024"):
        Settings.from_env(shipped(EMBEDDING_DIM=value))


def test_search_ef_ships_at_100_and_must_be_1_to_1000() -> None:
    """PRD 3 V2: hnsw.ef_search takes 1 to 1000; a value outside stops start-up by name."""
    assert Settings.from_env(shipped()).search_ef == 100
    assert Settings.from_env(shipped(SEARCH_EF="1000")).search_ef == 1000
    for bad in ("0", "1001", "-5", "ten", None):
        with pytest.raises(SettingsError, match="SEARCH_EF"):
            Settings.from_env(shipped(SEARCH_EF=bad))


def test_search_max_documents_ships_at_20_and_must_be_1_to_100() -> None:
    """PRD 3 Q2: at most SEARCH_MAX_DOCUMENTS documents per reply; a value outside stops start-up by name."""
    assert Settings.from_env(shipped()).search_max_documents == 20
    assert Settings.from_env(shipped(SEARCH_MAX_DOCUMENTS="100")).search_max_documents == 100
    for bad in ("0", "101", "twenty", None):
        with pytest.raises(SettingsError, match="SEARCH_MAX_DOCUMENTS"):
            Settings.from_env(shipped(SEARCH_MAX_DOCUMENTS=bad))


def test_answer_neighbours_ships_at_1_and_must_be_0_to_3() -> None:
    """PRD 3 Q4: the pieces on each side of an evidence piece; a value outside stops start-up by name."""
    assert Settings.from_env(shipped()).answer_neighbours == 1
    for bad in ("4", "-1", "one", None):
        with pytest.raises(SettingsError, match="ANSWER_NEIGHBOURS"):
            Settings.from_env(shipped(ANSWER_NEIGHBOURS=bad))


def test_chat_history_turns_ships_at_3_and_must_be_0_to_10() -> None:
    """PRD 3 H1: the pairs a follow-up is rewritten with; a value outside stops start-up by name."""
    assert Settings.from_env(shipped()).chat_history_turns == 3
    for bad in ("11", "-1", "three", None):
        with pytest.raises(SettingsError, match="CHAT_HISTORY_TURNS"):
            Settings.from_env(shipped(CHAT_HISTORY_TURNS=bad))


def test_the_doc_gives_the_prd3_defaults_that_ship() -> None:
    """docs/running-locally.md states the four PRD 3 defaults above (moved here from the Render
    blueprint test when render.yaml went, 24 Oct 2026)."""
    doc = (ENV_EXAMPLE.parent / "docs" / "running-locally.md").read_text(encoding="utf-8")
    for name, value in (
        ("SEARCH_EF", "100"),
        ("SEARCH_MAX_DOCUMENTS", "20"),
        ("ANSWER_NEIGHBOURS", "1"),
        ("CHAT_HISTORY_TURNS", "3"),
    ):
        assert f"`{name}={value}`" in doc


# PRD 5 E and U9: the queue and bulk upload settings, with the values they take when unset.
QUEUE_DEFAULTS = {
    "WORKER_REPLICAS": ("worker_replicas", 3),
    "MAX_ACTIVE_PER_CUSTOMER": ("max_active_per_customer", 2),
    "QUEUE_EXPRESS_URL": ("queue_express_url", None),
    "EXPRESS_MAX_PAGES": ("express_max_pages", 3),
    "MAX_FILES_PER_BATCH": ("max_files_per_batch", 100),
}


def test_the_queue_settings_take_the_prd_values_when_unset_or_empty() -> None:
    unset = {name: value for name, value in shipped().items() if name not in QUEUE_DEFAULTS}
    empty = {**unset, **dict.fromkeys(QUEUE_DEFAULTS, " ")}
    for env in (unset, empty):
        settings = Settings.from_env(env)
        assert {name: getattr(settings, field) for name, (field, _) in QUEUE_DEFAULTS.items()} == {
            name: default for name, (_, default) in QUEUE_DEFAULTS.items()
        }


def test_the_queue_settings_are_read_when_set() -> None:
    express = "http://localhost:9324/000000000000/kaagaz-jobs-express"
    settings = Settings.from_env(
        shipped(
            WORKER_REPLICAS="2",
            MAX_ACTIVE_PER_CUSTOMER="4",
            QUEUE_EXPRESS_URL=express,
            EXPRESS_MAX_PAGES="5",
            MAX_FILES_PER_BATCH="50",
        )
    )
    assert (
        settings.worker_replicas,
        settings.max_active_per_customer,
        settings.queue_express_url,
        settings.express_max_pages,
        settings.max_files_per_batch,
    ) == (2, 4, express, 5, 50)


def test_the_removed_model_prices_are_ignored_so_an_older_env_still_starts() -> None:
    """Model choice (council 11 Oct 2026): the ledger replaces MODEL_PRICE_*; a value that
    would have stopped start-up before is now not read at all."""
    settings = Settings.from_env(
        shipped(MODEL_PRICE_INPUT_USD_PER_M="-0.5", MODEL_PRICE_OUTPUT_USD_PER_M="nan")
    )
    assert not hasattr(settings, "model_price_input_usd_per_m")


# PRD 5 sections CX and M (council 9 Oct 2026): optional, the PRD's value when unset.
CONTEXT_DEFAULTS = {
    "MODEL_CONTEXT_TOKENS": 200_000,
    "ANSWER_WHOLE_DOC_TOKENS": 120_000,
    "TOPIC_MAX_PER_DOC": 40,
    "TOPIC_EXPAND_MAX_PIECES": 12,
    "QUERY_ASPECTS_MAX": 6,
    "CHAT_SUMMARY_EVERY": 6,
}


def _context_settings(settings: Settings) -> dict[str, int]:
    return {name: getattr(settings, name.lower()) for name in CONTEXT_DEFAULTS}


def test_the_context_and_memory_settings_are_the_prds_values_when_unset() -> None:
    """An older .env without them keeps working; empty counts as unset."""
    env = shipped()
    for name in CONTEXT_DEFAULTS:
        env.pop(name, None)
    assert _context_settings(Settings.from_env(env)) == CONTEXT_DEFAULTS
    blank = dict.fromkeys(CONTEXT_DEFAULTS, " ")
    assert _context_settings(Settings.from_env(shipped(**blank))) == CONTEXT_DEFAULTS


def test_the_context_and_memory_settings_take_a_set_value() -> None:
    chosen = {
        "MODEL_CONTEXT_TOKENS": 50_000,
        "ANSWER_WHOLE_DOC_TOKENS": 0,  # whole-document mode off (CX5)
        "TOPIC_MAX_PER_DOC": 1,
        "TOPIC_EXPAND_MAX_PIECES": 0,
        "QUERY_ASPECTS_MAX": 0,
        "CHAT_SUMMARY_EVERY": 1,
    }
    settings = Settings.from_env(shipped(**{name: str(value) for name, value in chosen.items()}))
    assert _context_settings(settings) == chosen


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("WORKER_REPLICAS", "0"),
        ("MAX_ACTIVE_PER_CUSTOMER", "-1"),
        ("EXPRESS_MAX_PAGES", "three"),
        ("MAX_FILES_PER_BATCH", "0"),
        ("QUEUE_EXPRESS_URL", "kaagaz-jobs-express"),
    ],
)
def test_a_bad_queue_setting_stops_start_up_by_name(name: str, value: str) -> None:
    with pytest.raises(SettingsError, match=name):
        Settings.from_env(shipped(**{name: value}))


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MODEL_CONTEXT_TOKENS", "0"),
        ("MODEL_CONTEXT_TOKENS", "lots"),
        ("ANSWER_WHOLE_DOC_TOKENS", "-1"),
        ("ANSWER_WHOLE_DOC_TOKENS", "1.5"),
        ("TOPIC_MAX_PER_DOC", "0"),
        ("TOPIC_EXPAND_MAX_PIECES", "-1"),
        ("QUERY_ASPECTS_MAX", "six"),
        ("CHAT_SUMMARY_EVERY", "0"),
    ],
)
def test_a_bad_context_or_memory_setting_stops_start_up_by_name(name: str, value: str) -> None:
    with pytest.raises(SettingsError, match=name):
        Settings.from_env(shipped(**{name: value}))


def test_the_express_lane_must_be_another_queue() -> None:
    with pytest.raises(SettingsError, match="QUEUE_EXPRESS_URL"):
        Settings.from_env(shipped(QUEUE_EXPRESS_URL=shipped()["QUEUE_URL"]))


def test_q8_the_shipped_reader_timeout_fits_the_lease_with_room_for_a_heartbeat() -> None:
    """PRD 5 Q8: OCR_TIMEOUT_SECONDS ships at 60 (one page at most, 16 Oct 2026) and QUEUE_LEASE_SECONDS at
    1200, and the existing rule OCR_TIMEOUT_SECONDS + 135 <= QUEUE_LEASE_SECONDS holds; a timeout
    one second past the rule is refused by name."""
    settings = Settings.from_env(shipped())
    assert (settings.ocr_timeout_seconds, settings.queue_lease_seconds) == (
        60.0,
        1200,
    )  # 60 s a page (Tushar, 16 Oct)
    assert settings.ocr_timeout_seconds + 135 <= settings.queue_lease_seconds
    with pytest.raises(SettingsError, match="OCR_TIMEOUT_SECONDS"):
        Settings.from_env(shipped(OCR_TIMEOUT_SECONDS="1066"))


def test_the_whole_documents_must_fit_the_context_window() -> None:
    """CX5: the whole documents, the answer's reply, its repair retry and the system prompt
    must fit MODEL_CONTEXT_TOKENS, or the call would be cut off."""
    prompt = settings_module.MAX_SYSTEM_PROMPT_TOKENS + settings_module.CHAT_OVERHEAD_TOKENS
    room = 2 * Settings.from_env(shipped()).model_answer_max_tokens + prompt
    context = str(120_000 + room)
    fits = Settings.from_env(shipped(MODEL_CONTEXT_TOKENS=context, ANSWER_WHOLE_DOC_TOKENS="120000"))
    assert fits.model_context_tokens == int(context)
    with pytest.raises(SettingsError, match="MODEL_CONTEXT_TOKENS"):
        Settings.from_env(shipped(MODEL_CONTEXT_TOKENS=context, ANSWER_WHOLE_DOC_TOKENS="120001"))
    off = Settings.from_env(shipped(MODEL_CONTEXT_TOKENS="8192", ANSWER_WHOLE_DOC_TOKENS="0"))
    assert off.answer_whole_doc_tokens == 0


def test_chat_questions_per_minute_is_20_when_unset_and_at_least_1() -> None:
    """Security checklist (15 Oct 2026): the chat limit a user, beside RATE_LIMIT_PER_MINUTE."""
    env = shipped()
    env.pop("CHAT_QUESTIONS_PER_MINUTE", None)
    assert Settings.from_env(env).chat_questions_per_minute == 20
    assert Settings.from_env(shipped(CHAT_QUESTIONS_PER_MINUTE="5")).chat_questions_per_minute == 5
    with pytest.raises(SettingsError, match="CHAT_QUESTIONS_PER_MINUTE"):
        Settings.from_env(shipped(CHAT_QUESTIONS_PER_MINUTE="0"))


# --- Google sign-in (#457 phase 1) ---------------------------------------------------------

GOOGLE_CALLBACK = "https://kaagaz.example.com/api/auth/oidc/google/callback"
GOOGLE_ON = {
    "GOOGLE_SIGNIN_ENABLED": "true",
    "GOOGLE_CLIENT_ID": "made-up-client.apps.googleusercontent.com",
    "GOOGLE_CLIENT_SECRET": "made-up-google-secret",
    "GOOGLE_REDIRECT_URI": GOOGLE_CALLBACK,
}


def test_google_signin_is_off_by_default() -> None:
    settings = Settings.from_env(shipped())
    assert settings.google_signin_enabled is False
    assert (settings.google_client_id, settings.google_client_secret, settings.google_redirect_uri) == (
        None,
        None,
        None,
    )


@pytest.mark.parametrize("missing", ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI"])
def test_google_signin_needs_its_three_values(missing: str) -> None:
    with pytest.raises(SettingsError, match=missing):
        Settings.from_env(shipped(**{**GOOGLE_ON, missing: None}))


@pytest.mark.parametrize(
    ("uri", "ok"),
    [
        ("http://kaagaz.example.com/api/auth/oidc/google/callback", False),
        ("https://kaagaz.example.com/other", False),
        ("https://kaagaz.example.com/api/auth/oidc/google/callback?x=1", False),
        (GOOGLE_CALLBACK, True),
        ("http://localhost:3000/api/auth/oidc/google/callback", True),
    ],
)
def test_google_redirect_must_be_https_and_the_callback_path(uri: str, ok: bool) -> None:
    env = shipped(**{**GOOGLE_ON, "GOOGLE_REDIRECT_URI": uri})
    if ok:
        settings = Settings.from_env(env)
        assert settings.google_signin_enabled is True
        assert settings.google_redirect_uri == uri
    else:
        with pytest.raises(SettingsError, match="GOOGLE_REDIRECT_URI"):
            Settings.from_env(env)


@pytest.mark.parametrize(
    ("check", "value", "message"),
    [
        (
            lambda value: settings_module._openrouter_base_url(value),
            "https://user@openrouter.example.com",
            "OPENROUTER_BASE_URL must have no user, password, query or fragment",
        ),
        (
            lambda value: settings_module._openrouter_base_url(value),
            "http://openrouter.example.com",
            "OPENROUTER_BASE_URL must be an https URL (http only for localhost, 127.0.0.1 or ::1)",
        ),
        (
            lambda value: settings_module._app_url(value),
            "https://kaagaz.example.com/app",
            "APP_URL must be an https origin such as https://kaagaz.example.com"
            " (http only for localhost, 127.0.0.1 or ::1)",
        ),
        (
            lambda value: settings_module._redirect_uri(value, "google"),
            "https://kaagaz.example.com/api/auth/oidc/google/callback/",
            "GOOGLE_REDIRECT_URI must be an https URL ending /api/auth/oidc/google/callback"
            " (http only for localhost, 127.0.0.1 or ::1)",
        ),
    ],
)
def test_each_https_setting_keeps_its_own_message(check: Any, value: str, message: str) -> None:
    with pytest.raises(SettingsError) as refused:
        check(value)
    assert str(refused.value) == message


def test_the_three_https_settings_share_one_check() -> None:
    assert (
        settings_module._https_url("https://kaagaz.example.com/", "APP_URL", "")
        == "https://kaagaz.example.com"
    )
    assert (
        settings_module._https_url("http://localhost:8080/v1/", "OPENROUTER_BASE_URL")
        == "http://localhost:8080/v1"
    )
    callback = "https://kaagaz.example.com/api/auth/oidc/google/callback"
    assert (
        settings_module._https_url(callback, "GOOGLE_REDIRECT_URI", "/api/auth/oidc/google/callback")
        == callback
    )


def test_google_client_secret_is_never_in_repr() -> None:
    settings = Settings.from_env(shipped(**GOOGLE_ON))
    assert settings.google_client_secret == "made-up-google-secret"
    assert "made-up-google-secret" not in repr(settings)
