import json
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from govwatch import metrics
from govwatch.agent.prompt import Prompt

log = logging.getLogger(__name__)


class LLMError(Exception):
    pass


@dataclass(frozen=True)
class Generation:
    output: dict[str, Any]
    model: str
    seconds: float
    prompt_tokens: int
    completion_tokens: int


class OllamaClient:
    """Local Ollama over its HTTP API. Same shape as the source clients: bounded retries
    with jittered backoff on transport errors and 5xx (ollama returns 500 when a model
    fails to load, e.g. out of memory), and nothing retried on 4xx."""

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        timeout: float = 180.0,
        max_retries: int = 2,
        keep_alive: str = "10m",
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.model = model
        self.max_retries = max_retries
        self.keep_alive = keep_alive
        self._sleep = sleep
        self._http = httpx.Client(base_url=base_url, timeout=timeout)

    def generate(self, prompt: Prompt, schema: dict[str, Any]) -> Generation:
        body = {
            "model": self.model,
            "system": prompt.system,
            "prompt": prompt.user,
            # constrained decoding: the model can't emit anything outside the schema
            "format": schema,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"temperature": 0, "num_ctx": 4096},
        }
        for attempt in range(self.max_retries + 1):
            last_try = attempt == self.max_retries
            started = time.perf_counter()
            try:
                resp = self._http.post("/api/generate", json=body)
            except httpx.TransportError as exc:
                metrics.LLM_REQUESTS.labels(self.model, "error").inc()
                if last_try:
                    raise LLMError(f"ollama unreachable: {exc}") from exc
                self._backoff(attempt, str(exc))
                continue

            elapsed = time.perf_counter() - started
            if resp.status_code >= 500 and not last_try:
                metrics.LLM_REQUESTS.labels(self.model, "error").inc()
                self._backoff(attempt, f"HTTP {resp.status_code}")
                continue
            if resp.is_error:
                metrics.LLM_REQUESTS.labels(self.model, "error").inc()
                raise LLMError(f"ollama returned HTTP {resp.status_code}: {resp.text[:200]}")

            data = resp.json()
            try:
                output = json.loads(data["response"])
            except (KeyError, json.JSONDecodeError) as exc:
                metrics.LLM_REQUESTS.labels(self.model, "bad_json").inc()
                raise LLMError(f"model output wasn't JSON: {exc}") from exc

            prompt_tokens = int(data.get("prompt_eval_count") or 0)
            completion_tokens = int(data.get("eval_count") or 0)
            metrics.LLM_REQUESTS.labels(self.model, "ok").inc()
            metrics.LLM_LATENCY.labels(self.model).observe(elapsed)
            metrics.LLM_TOKENS.labels(self.model, "prompt").inc(prompt_tokens)
            metrics.LLM_TOKENS.labels(self.model, "completion").inc(completion_tokens)
            return Generation(output, self.model, elapsed, prompt_tokens, completion_tokens)

        raise AssertionError("unreachable")

    def close(self) -> None:
        self._http.close()

    def _backoff(self, attempt: int, reason: str) -> None:
        delay = random.uniform(0, min(30.0, 2.0 * 2**attempt))
        log.warning("ollama: %s, retrying in %.1fs", reason, delay)
        self._sleep(delay)
