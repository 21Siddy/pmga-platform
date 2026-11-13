import json
import logging
import time
from threading import Lock
from typing import Any, Dict, Optional, Type, TypeVar

import httpx
from google import genai
from google.genai import types
from openai import OpenAI
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

#: Every provider the app can talk to. `style` decides which call path is
#: used; `needs_base_url` is true where the customer endpoint is unique
#: (Azure deployments and the university gateway), so the user must supply it.
PROVIDER_SPECS: Dict[str, Dict[str, Any]] = {
    "gemini": {
        "label": "Google Gemini",
        "style": "gemini",
        "base_url": None,
        "default_model": settings.DEFAULT_GEMINI_MODEL,
        "needs_base_url": False,
    },
    "openai": {
        "label": "OpenAI",
        "style": "openai",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4.1-mini",
        "needs_base_url": False,
    },
    "anthropic": {
        "label": "Anthropic",
        "style": "anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "default_model": "claude-sonnet-4-6",
        "needs_base_url": False,
    },
    "mistral": {
        "label": "Mistral",
        "style": "openai",
        "base_url": "https://api.mistral.ai/v1",
        "default_model": "mistral-large-latest",
        "needs_base_url": False,
    },
    "azure": {
        "label": "Azure OpenAI",
        "style": "openai",
        "base_url": None,
        "default_model": "",
        "needs_base_url": True,
    },
    "kiconnect": {
        "label": "University gateway (OpenAI-compatible)",
        "style": "openai",
        "base_url": settings.API_BASE or None,
        "default_model": settings.API_CHAT_MODEL,
        "needs_base_url": True,
    },
}

PROVIDER_ORDER = ("gemini", "openai", "anthropic", "mistral", "azure", "kiconnect")


class RateLimitThrottler:
    """
    Enforces inter-request spacing and backoff for university or tier-limited keys.
    Principle: 'Slow is fine - Reliability is top priority'.
    """

    def __init__(self, min_interval_sec: float = 4.0):
        self.min_interval = min_interval_sec
        self.next_slot_time = 0.0
        self.lock = Lock()

    def wait_for_slot(self):
        with self.lock:
            now = time.time()
            scheduled = max(now, self.next_slot_time)
            self.next_slot_time = scheduled + self.min_interval
            sleep_needed = scheduled - now
        if sleep_needed > 0:
            time.sleep(sleep_needed)


throttler = RateLimitThrottler(min_interval_sec=settings.LLM_MIN_INTERVAL_SEC)


class ProviderError(RuntimeError):
    """A provider failure translated into a kind the UI can explain."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


def classify_provider_error(error: Exception) -> ProviderError:
    """Translate a provider exception into a plain-English kind and message.

    The original text is inspected, never re-raised verbatim, because it can
    contain request detail. The API key never appears in a message.
    """
    text = str(error)
    low = text.lower()

    if any(token in low for token in ("invalid api key", "incorrect api key", "unauthorized",
                                      "authentication", "api key not valid", "invalid_api_key")):
        return ProviderError("auth", "The provider refused the key. Check that it is complete and still active.")
    if "401" in text:
        return ProviderError("auth", "The provider refused the key (401). Check that it is complete and still active.")
    if any(token in low for token in ("insufficient_quota", "quota", "billing", "credit", "payment",
                                      "exceeded your current")):
        return ProviderError("quota", "The key is valid but has no credit or has hit its quota. Top up the account or use another key.")
    if "429" in text:
        return ProviderError("quota", "The provider is rate limiting this key (429). Wait a moment and try again.")
    if any(token in low for token in ("model_not_found", "does not exist", "no such model", "unknown model",
                                      "invalid model", "deployment not found")):
        return ProviderError("model_not_found", "The provider does not know that model name. Check the exact model or deployment name.")
    if "404" in text:
        return ProviderError("model_not_found", "The provider could not find that model or endpoint (404). Check the model and the base URL.")
    if any(token in low for token in ("connect", "timeout", "timed out", "dns", "name or service",
                                      "network", "unreachable", "ssl", "certificate")):
        return ProviderError("network", "Could not reach the provider. Check the network, proxy or base URL and try again.")
    return ProviderError("unknown", f"The provider rejected the request: {text[:180]}")


class LLMClient:
    """Unified multi-provider client with per-run keys, pacing and typed errors."""

    # ------------------------------------------------------------------
    # Status and registry
    # ------------------------------------------------------------------

    @staticmethod
    def list_providers() -> list:
        """Provider registry for the UI, no secrets included."""
        return [
            {
                "id": provider_id,
                "label": spec["label"],
                "default_model": spec["default_model"],
                "needs_base_url": spec["needs_base_url"],
                "base_url": spec["base_url"] or "",
            }
            for provider_id, spec in ((pid, PROVIDER_SPECS[pid]) for pid in PROVIDER_ORDER)
        ]

    @staticmethod
    def get_provider_status() -> Dict[str, Any]:
        """Reports active configuration and key status without exposing secrets."""
        has_gemini = bool(settings.GEMINI_API_KEY)
        has_kiconnect = bool(settings.API_KEY and settings.API_BASE)

        return {
            "default_provider": settings.DEFAULT_LLM_PROVIDER,
            "gemini": {
                "configured": has_gemini,
                "model": settings.DEFAULT_GEMINI_MODEL
            },
            "kiconnect": {
                "configured": has_kiconnect,
                "base_url": settings.API_BASE or "Not set",
                "model": settings.API_CHAT_MODEL
            },
            "min_interval_sec": settings.LLM_MIN_INTERVAL_SEC,
            "providers": LLMClient.list_providers(),
        }

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    @staticmethod
    def resolve(provider: Optional[str], api_key: Optional[str], model_name: Optional[str],
                base_url: Optional[str] = None) -> Dict[str, Any]:
        """Turn a loose provider request into a concrete call configuration."""
        provider_id = (provider or settings.DEFAULT_LLM_PROVIDER or "gemini").strip().lower()

        if provider_id not in PROVIDER_SPECS:
            raise ProviderError(
                "config",
                f"Unknown provider '{provider_id}'. Choose one of: {', '.join(PROVIDER_ORDER)}.",
            )

        spec = PROVIDER_SPECS[provider_id]
        key = (api_key or "").strip()
        if not key:
            # Fall back to the locally configured key for that provider only.
            if provider_id == "gemini":
                key = settings.GEMINI_API_KEY
            elif provider_id == "kiconnect":
                key = settings.API_KEY
        if not key:
            raise ProviderError(
                "config",
                "No key provided. Paste your key for this run; it is not stored by the app.",
            )

        resolved_base = (base_url or spec["base_url"] or "").strip().rstrip("/")
        if spec["needs_base_url"] and not resolved_base:
            raise ProviderError(
                "config",
                f"{spec['label']} needs its own endpoint URL. Paste it next to the key.",
            )

        resolved_model = (model_name or spec["default_model"] or "").strip()
        if not resolved_model:
            raise ProviderError("config", "No model name given. Type the model or deployment name you want to use.")

        return {
            "provider": provider_id,
            "style": spec["style"],
            "api_key": key,
            "base_url": resolved_base,
            "model": resolved_model,
        }

    # ------------------------------------------------------------------
    # Connection test
    # ------------------------------------------------------------------

    @staticmethod
    def test_connection(provider: Optional[str], api_key: Optional[str],
                        model_name: Optional[str] = None,
                        base_url: Optional[str] = None) -> Dict[str, Any]:
        """One real, cheap call. Returns a plain result, never raises."""
        started = time.time()
        try:
            config = LLMClient.resolve(provider, api_key, model_name, base_url)
        except ProviderError as error:
            return {
                "ok": False,
                "provider": provider,
                "model": model_name,
                "error_kind": error.kind,
                "message": error.message,
                "latency_ms": 0,
            }

        try:
            reply = LLMClient._probe(config)
        except ProviderError as error:
            return {
                "ok": False,
                "provider": config["provider"],
                "model": config["model"],
                "error_kind": error.kind,
                "message": error.message,
                "latency_ms": int((time.time() - started) * 1000),
            }
        except Exception as error:  # provider SDKs raise their own shapes
            translated = classify_provider_error(error)
            return {
                "ok": False,
                "provider": config["provider"],
                "model": config["model"],
                "error_kind": translated.kind,
                "message": translated.message,
                "latency_ms": int((time.time() - started) * 1000),
            }

        return {
            "ok": True,
            "provider": config["provider"],
            "model": config["model"],
            "error_kind": None,
            "message": f"Connected. The model replied: {reply[:60]}",
            "latency_ms": int((time.time() - started) * 1000),
        }

    @staticmethod
    def _probe(config: Dict[str, Any]) -> str:
        """A single minimal round trip used by the test button."""
        if config["style"] == "gemini":
            client = genai.Client(api_key=config["api_key"])
            response = client.models.generate_content(
                model=config["model"],
                contents="Reply with the single word ready.",
                config=types.GenerateContentConfig(temperature=0, max_output_tokens=16),
            )
            return (response.text or "").strip()

        if config["style"] == "anthropic":
            payload = {
                "model": config["model"],
                "max_tokens": 16,
                "temperature": 0,
                "messages": [{"role": "user", "content": "Reply with the single word ready."}],
            }
            response = httpx.post(
                f"{config['base_url']}/messages",
                headers={
                    "x-api-key": config["api_key"],
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
                timeout=30.0,
            )
            if response.status_code >= 400:
                raise ProviderError(
                    *_status_error(response.status_code, response.text)
                )
            body = response.json()
            blocks = body.get("content") or []
            return (blocks[0].get("text", "") if blocks else "").strip()

        client = OpenAI(api_key=config["api_key"], base_url=config["base_url"] or None)
        completion = client.chat.completions.create(
            model=config["model"],
            messages=[{"role": "user", "content": "Reply with the single word ready."}],
            max_tokens=16,
            temperature=0,
        )
        return (completion.choices[0].message.content or "").strip()

    # ------------------------------------------------------------------
    # Structured generation
    # ------------------------------------------------------------------

    @staticmethod
    def generate_structured(
        prompt: str,
        response_model: Type[T],
        system_instruction: str = "You are an expert systems and business process modeling architect.",
        api_key: Optional[str] = None,
        provider: Optional[str] = None,
        model_name: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> T:
        """Structured generation against whichever provider the user chose."""
        config = LLMClient.resolve(provider, api_key, model_name, base_url)
        throttler.wait_for_slot()

        if config["style"] == "gemini":
            return LLMClient._call_gemini(
                prompt=prompt,
                response_model=response_model,
                system_instruction=system_instruction,
                api_key=config["api_key"],
                model_name=config["model"],
            )

        if config["style"] == "anthropic":
            return LLMClient._call_anthropic(
                prompt=prompt,
                response_model=response_model,
                system_instruction=system_instruction,
                config=config,
            )

        return LLMClient._call_openai_compatible(
            prompt=prompt,
            response_model=response_model,
            system_instruction=system_instruction,
            model_name=config["model"],
            api_key=config["api_key"],
            base_url=config["base_url"],
            label=PROVIDER_SPECS[config["provider"]]["label"],
        )

    # ------------------------------------------------------------------
    # Call paths
    # ------------------------------------------------------------------

    @staticmethod
    def _call_gemini(
        prompt: str,
        response_model: Type[T],
        system_instruction: str,
        api_key: str,
        model_name: str
    ) -> T:
        client = genai.Client(api_key=api_key)
        retries = settings.LLM_MAX_RETRIES

        for attempt in range(retries):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        response_mime_type="application/json",
                        response_schema=response_model,
                        temperature=0.2
                    )
                )
                return response_model.model_validate_json(response.text)
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    wait_time = (attempt + 1) * 6.0
                    logger.warning(f"Gemini 429 rate limit hit. Backing off for {wait_time}s (attempt {attempt+1}/{retries})")
                    time.sleep(wait_time)
                    continue
                if "404" in err_str and "gemini-2.5-flash" in err_str:
                    logger.info("gemini-2.5-flash deprecated; falling back to gemini-3.8-flash")
                    return LLMClient._call_gemini(prompt, response_model, system_instruction, api_key, "gemini-3.8-flash")
                raise classify_provider_error(e) from e

        raise ProviderError("quota", "Gemini stayed rate limited after several retries. Wait and try again.")

    @staticmethod
    def _call_anthropic(
        prompt: str,
        response_model: Type[T],
        system_instruction: str,
        config: Dict[str, Any],
    ) -> T:
        schema_json = json.dumps(response_model.model_json_schema(), indent=2)
        payload = {
            "model": config["model"],
            "max_tokens": 4096,
            "temperature": 0.2,
            "system": (
                f"{system_instruction}\n\nRespond with raw JSON only, matching this schema exactly:\n{schema_json}"
            ),
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {
            "x-api-key": config["api_key"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        retries = settings.LLM_MAX_RETRIES
        for attempt in range(retries):
            response = httpx.post(
                f"{config['base_url']}/messages",
                headers=headers,
                json=payload,
                timeout=120.0,
            )
            if response.status_code == 429:
                wait_time = (attempt + 1) * 8.0
                logger.warning(f"Anthropic 429 rate limit hit. Backing off for {wait_time}s (attempt {attempt+1}/{retries})")
                time.sleep(wait_time)
                continue
            if response.status_code >= 400:
                kind, message = _status_error(response.status_code, response.text)
                raise ProviderError(kind, message)

            body = response.json()
            blocks = body.get("content") or []
            raw_json = blocks[0].get("text", "{}") if blocks else "{}"
            return response_model.model_validate_json(_strip_code_fence(raw_json))

        raise ProviderError("quota", "Anthropic stayed rate limited after several retries. Wait and try again.")

    @staticmethod
    def _call_openai_compatible(
        prompt: str,
        response_model: Type[T],
        system_instruction: str,
        model_name: str,
        api_key: str,
        base_url: str,
        label: str = "Provider",
    ) -> T:
        client = OpenAI(api_key=api_key, base_url=base_url or None)
        schema_json = json.dumps(response_model.model_json_schema(), indent=2)
        augmented_prompt = f"{prompt}\n\nYou MUST respond with raw JSON matching this JSON Schema exactly:\n{schema_json}"

        retries = settings.LLM_MAX_RETRIES
        for attempt in range(retries):
            try:
                completion = client.chat.completions.create(
                    model=model_name,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": system_instruction},
                        {"role": "user", "content": augmented_prompt}
                    ],
                    temperature=0.2
                )
                raw_json = completion.choices[0].message.content or "{}"
                return response_model.model_validate_json(_strip_code_fence(raw_json))
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "rate limit" in err_str.lower():
                    wait_time = (attempt + 1) * 8.0
                    logger.warning(f"{label} 429 rate limit hit. Backing off for {wait_time}s (attempt {attempt+1}/{retries})")
                    time.sleep(wait_time)
                    continue
                raise classify_provider_error(e) from e

        raise ProviderError("quota", f"{label} stayed rate limited after several retries. Wait and try again.")


def _strip_code_fence(text: str) -> str:
    """Remove a ```json fence that some providers add despite instructions."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        if cleaned.endswith("```"):
            cleaned = cleaned[: -3]
    return cleaned.strip()


def _status_error(status_code: int, body: str) -> tuple:
    """Map a raw HTTP status to (kind, plain message)."""
    low = (body or "").lower()
    if status_code in (401, 403):
        return "auth", "The provider refused the key. Check that it is complete and still active."
    if status_code == 404:
        return "model_not_found", "The provider could not find that model or endpoint. Check the model and base URL."
    if status_code == 429:
        if "quota" in low or "credit" in low or "billing" in low:
            return "quota", "The key is valid but has no credit left. Top up the account or use another key."
        return "quota", "The provider is rate limiting this key. Wait a moment and try again."
    if status_code >= 500:
        return "network", f"The provider had a server problem ({status_code}). Try again shortly."
    return "unknown", f"The provider rejected the request ({status_code})."
