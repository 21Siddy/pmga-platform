import pytest
from app.services.llm_client import LLMClient, RateLimitThrottler
import time

def test_throttler_pacing():
    throttler = RateLimitThrottler(min_interval_sec=0.2)
    start = time.time()
    throttler.wait_for_slot()
    throttler.wait_for_slot()
    elapsed = time.time() - start
    assert elapsed >= 0.18

def test_llm_status_reporting():
    status = LLMClient.get_provider_status()
    assert "default_provider" in status
    assert "gemini" in status
    assert "kiconnect" in status
    assert status["gemini"]["model"] == "gemini-3.8-flash"
