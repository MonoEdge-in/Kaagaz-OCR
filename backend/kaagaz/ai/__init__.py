"""The one door to the local AI model: embeddings, JSON answers and image reads."""

from kaagaz.ai.client import (
    ModelClient,
    ModelOutputError,
    ModelUnavailable,
    OllamaClient,
    build_model_client,
)

__all__ = ["ModelClient", "ModelOutputError", "ModelUnavailable", "OllamaClient", "build_model_client"]
