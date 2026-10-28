import time
import json
import logging
from typing import Optional, Dict, Any, Type, TypeVar
from threading import Lock
from pydantic import BaseModel
from google import genai
from google.genai import types
from openai import OpenAI
from app.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

class RateLimitThrottler:
    """
    Enforces inter-request spacing and backoff for university or tier-limited keys.
    Principle: 'Slow is fine - Reliability is top priority'.
    """
    def __init__(self, min_interval_sec: float = 4.0):
        self.min_interval = min_interval_sec
        self.last_call_time = 0.0
        self.lock = Lock()

    def wait_for_slot(self):
        with self.lock:
            now = time.time()
            elapsed = now - self.last_call_time
            if elapsed < self.min_interval:
                sleep_needed = self.min_interval - elapsed
                time.sleep(sleep_needed)
            self.last_call_time = time.time()


throttler = RateLimitThrottler(min_interval_sec=settings.LLM_MIN_INTERVAL_SEC)


class LLMClient:
    """
    Unified multi-provider client with rate limiting, error translation, and model routing.
    Supports:
    1. Google Gemini (e.g., gemini-3.8-flash)
    2. University KI-Connect / OpenAI-Compatible (e.g., kiconnect.mistralai-mistral-small-4-119b-2603)
    """

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
            "min_interval_sec": settings.LLM_MIN_INTERVAL_SEC
        }

    @staticmethod
    def generate_structured(
        prompt: str,
        response_model: Type[T],
        system_instruction: str = "You are an expert systems and business process modeling architect.",
        api_key: Optional[str] = None,
        provider: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> T:
        """
        Executes structured generation using either Gemini or KI-Connect with rate-limit pacing.
        """
        chosen_provider = provider or settings.DEFAULT_LLM_PROVIDER
        throttler.wait_for_slot()

        # 1. University KI-Connect / OpenAI-Compatible Provider
        if chosen_provider == "kiconnect" or (not api_key and settings.API_KEY and settings.API_BASE):
            return LLMClient._call_openai_compatible(
                prompt=prompt,
                response_model=response_model,
                system_instruction=system_instruction,
                model_name=model_name or settings.API_CHAT_MODEL
            )

        # 2. Google Gemini Provider
        active_gemini_key = api_key or settings.GEMINI_API_KEY
        if active_gemini_key:
            return LLMClient._call_gemini(
                prompt=prompt,
                response_model=response_model,
                system_instruction=system_instruction,
                api_key=active_gemini_key,
                model_name=model_name or settings.DEFAULT_GEMINI_MODEL
            )

        raise ValueError(
            "No active LLM configuration found. Please enter a Gemini API key or configure your University KI-Connect credentials in .env."
        )

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
                raise

        raise RuntimeError("Exceeded maximum retries for Gemini API due to rate limits.")

    @staticmethod
    def _call_openai_compatible(
        prompt: str,
        response_model: Type[T],
        system_instruction: str,
        model_name: str
    ) -> T:
        client = OpenAI(
            api_key=settings.API_KEY,
            base_url=settings.API_BASE
        )
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
                return response_model.model_validate_json(raw_json)
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "rate limit" in err_str.lower():
                    wait_time = (attempt + 1) * 8.0
                    logger.warning(f"KI-Connect 429 rate limit hit. Backing off for {wait_time}s (attempt {attempt+1}/{retries})")
                    time.sleep(wait_time)
                    continue
                raise

        raise RuntimeError("Exceeded maximum retries for KI-Connect API due to rate limits.")
