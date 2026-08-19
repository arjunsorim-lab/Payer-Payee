"""Small, explicit client for the local Ollama HTTP API."""

from __future__ import annotations

import os
from typing import Any

import requests


class OllamaError(RuntimeError):
    """Raised when the configured local Ollama service cannot satisfy a request."""


class OllamaClient:
    def __init__(
        self,
        base_url: str | None = None,
        chat_model: str | None = None,
        embed_model: str | None = None,
        timeout_seconds: float | None = None,
    ):
        self.base_url = (
            base_url or os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
        ).rstrip("/")
        self.chat_model = chat_model or os.getenv("OLLAMA_CHAT_MODEL", "gemma3")
        self.embed_model = embed_model or os.getenv(
            "OLLAMA_EMBED_MODEL", "embeddinggemma"
        )
        self.timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else os.getenv("OLLAMA_TIMEOUT_SECONDS", "60")
        )

    def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        try:
            response = requests.request(
                method,
                f"{self.base_url}{path}",
                timeout=self.timeout_seconds,
                **kwargs,
            )
            response.raise_for_status()
        except requests.Timeout as error:
            raise OllamaError(
                f"Local Ollama request timed out after {self.timeout_seconds:g} seconds."
            ) from error
        except requests.ConnectionError as error:
            raise OllamaError(
                "Local model service is temporarily unavailable."
            ) from error
        except requests.HTTPError as error:
            detail = ""
            try:
                detail = str(response.json().get("error") or "").strip()
            except (ValueError, AttributeError):
                detail = response.text[:300].strip()
            raise OllamaError(
                f"Ollama returned HTTP {response.status_code}"
                + (f": {detail}" if detail else ".")
            ) from error
        try:
            payload = response.json()
        except ValueError as error:
            raise OllamaError("Ollama returned invalid JSON.") from error
        if not isinstance(payload, dict):
            raise OllamaError("Ollama returned an unexpected response shape.")
        if payload.get("error"):
            raise OllamaError(f"Ollama error: {payload['error']}")
        return payload

    def health(self) -> dict[str, Any]:
        try:
            models = self.list_models()
            return {
                "available": True,
                "base_url": self.base_url,
                "chat_model": self.chat_model,
                "embedding_model": self.embed_model,
                "chat_model_available": self._model_exists(
                    self.chat_model, models
                ),
                "embedding_model_available": self._model_exists(
                    self.embed_model, models
                ),
                "models": models,
            }
        except OllamaError as error:
            return {
                "available": False,
                "base_url": self.base_url,
                "chat_model": self.chat_model,
                "embedding_model": self.embed_model,
                "chat_model_available": False,
                "embedding_model_available": False,
                "error": str(error),
            }

    def list_models(self) -> list[str]:
        payload = self._request("GET", "/api/tags")
        models = payload.get("models")
        if not isinstance(models, list):
            raise OllamaError("Ollama model-list response did not contain models.")
        return [
            str(model.get("name") or model.get("model") or "").strip()
            for model in models
            if isinstance(model, dict)
            and str(model.get("name") or model.get("model") or "").strip()
        ]

    @staticmethod
    def _model_exists(model: str, installed: list[str]) -> bool:
        def canonical(value: str) -> str:
            normalized = value.strip()
            return normalized if ":" in normalized else f"{normalized}:latest"

        requested = canonical(model)
        return any(canonical(candidate) == requested for candidate in installed)

    def require_model(self, model: str) -> None:
        installed = self.list_models()
        if not self._model_exists(model, installed):
            raise OllamaError(
                f"Ollama model '{model}' is not installed. Run: ollama pull {model}"
            )

    def chat(
        self,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.require_model(self.chat_model)
        body: dict[str, Any] = {
            "model": self.chat_model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": 0,
                "num_predict": max(
                    int(os.getenv("OLLAMA_MAX_PREDICT_TOKENS", "160")),
                    64,
                ),
            },
        }
        if json_schema is not None:
            body["format"] = json_schema
        payload = self._request("POST", "/api/chat", json=body)
        message = payload.get("message")
        if not isinstance(message, dict):
            raise OllamaError("Ollama chat response did not contain a message.")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise OllamaError("Ollama chat response was empty.")
        return {
            "content": content.strip(),
            "model": str(payload.get("model") or self.chat_model),
            "done": bool(payload.get("done", True)),
        }

    def embed(self, texts: list[str] | str) -> list[list[float]]:
        items = [texts] if isinstance(texts, str) else list(texts)
        if not items:
            return []
        if not all(isinstance(text, str) and text.strip() for text in items):
            raise OllamaError("Ollama embedding input must contain non-empty text.")
        self.require_model(self.embed_model)
        payload = self._request(
            "POST",
            "/api/embed",
            json={
                "model": self.embed_model,
                "input": items,
                "truncate": True,
            },
        )
        embeddings = payload.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(items):
            raise OllamaError(
                "Ollama embedding response count did not match the input count."
            )
        dimension = None
        normalized: list[list[float]] = []
        for vector in embeddings:
            if not isinstance(vector, list) or not vector:
                raise OllamaError("Ollama returned an empty embedding vector.")
            try:
                numeric = [float(value) for value in vector]
            except (TypeError, ValueError) as error:
                raise OllamaError(
                    "Ollama returned a non-numeric embedding vector."
                ) from error
            dimension = dimension or len(numeric)
            if len(numeric) != dimension:
                raise OllamaError(
                    "Ollama returned inconsistent embedding dimensions."
                )
            normalized.append(numeric)
        return normalized


def configured_ollama_client() -> OllamaClient:
    return OllamaClient()
