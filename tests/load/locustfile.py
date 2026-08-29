"""
Locust load test for Inferra inference gateway.

Usage:
    locust -f tests/load/locustfile.py \\
        --host http://localhost:9100 \\
        --api-key inf_your_inference_key_here

    # Headless run (CI / automated):
    locust -f tests/load/locustfile.py \\
        --host http://localhost:9100 \\
        --api-key inf_... \\
        --users 10 \\
        --spawn-rate 2 \\
        --run-time 60s \\
        --headless \\
        --html reports/load-test.html

Then open http://localhost:8089 for the Locust web UI (when not headless).

Task mix (weighted):
  - short_chat (weight 3): fast, low-token interactive query
  - medium_chat (weight 2): typical assistant workload
  - streaming_chat (weight 2): streaming SSE response
  - long_prompt (weight 1): large prompt, KV-cache stress
"""

from __future__ import annotations

import json

from locust import HttpUser, between, events, task

SHORT_PROMPT = "What is 2+2? Answer in one word."
MEDIUM_PROMPT = (
    "Explain how a transformer's attention mechanism works in simple terms. "
    "Cover self-attention, keys, queries, values, and why it's better than RNNs."
)
LONG_PROMPT = (
    "The following document describes transformer architecture in detail. "
    "Attention is all you need. Self-attention operates on query, key, value matrices. "
    "Multi-head attention runs multiple attention heads in parallel. "
    "Positional encoding adds sequence order information. "
    "Feed-forward layers process each token independently. "
    "Layer normalisation is applied before or after sublayers. "
    "Residual connections allow gradient flow through deep networks. "
) * 8 + "\n\nSummarise the three most important architectural choices described above."

_MODEL = "test-assistant"


class InferenceUser(HttpUser):
    """Simulates a mix of interactive and batch inference users."""

    wait_time = between(0.5, 2.0)

    def on_start(self) -> None:
        key = self.environment.parsed_options.api_key
        self._headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

    @task(3)
    def short_chat(self) -> None:
        """Fast, short response — simulates interactive chat."""
        self.client.post(
            "/v1/chat/completions",
            headers=self._headers,
            json={
                "model": _MODEL,
                "messages": [{"role": "user", "content": SHORT_PROMPT}],
                "max_tokens": 32,
                "stream": False,
            },
            name="/v1/chat/completions [short]",
        )

    @task(2)
    def medium_chat(self) -> None:
        """Medium-length response — typical assistant workload."""
        self.client.post(
            "/v1/chat/completions",
            headers=self._headers,
            json={
                "model": _MODEL,
                "messages": [
                    {"role": "system", "content": "You are a helpful technical assistant."},
                    {"role": "user", "content": MEDIUM_PROMPT},
                ],
                "max_tokens": 256,
                "stream": False,
            },
            name="/v1/chat/completions [medium]",
        )

    @task(2)
    def streaming_chat(self) -> None:
        """Streaming SSE response — validates streaming path under load."""
        with self.client.post(
            "/v1/chat/completions",
            headers=self._headers,
            json={
                "model": _MODEL,
                "messages": [{"role": "user", "content": MEDIUM_PROMPT}],
                "max_tokens": 128,
                "stream": True,
            },
            name="/v1/chat/completions [stream]",
            stream=True,
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"HTTP {resp.status_code}")
                return
            done_seen = False
            try:
                for line in resp.iter_lines():
                    if line == b"data: [DONE]":
                        done_seen = True
                        break
                if done_seen:
                    resp.success()
                else:
                    resp.failure("Stream ended without [DONE]")
            except Exception as exc:
                resp.failure(str(exc))

    @task(1)
    def long_prompt_chat(self) -> None:
        """Large prompt — stresses KV-cache and prefill throughput."""
        self.client.post(
            "/v1/chat/completions",
            headers=self._headers,
            json={
                "model": _MODEL,
                "messages": [{"role": "user", "content": LONG_PROMPT}],
                "max_tokens": 256,
                "stream": False,
            },
            name="/v1/chat/completions [long]",
        )

    @task(1)
    def health_check(self) -> None:
        """Periodic health probe — verifies gateway responsiveness."""
        self.client.get("/health", name="/health")


@events.init_command_line_parser.add_listener
def add_custom_arguments(parser) -> None:
    parser.add_argument(
        "--api-key",
        type=str,
        default="",
        env_var="INFERRA_INFERENCE_KEY",
        help="Inferra inference API key (or set INFERRA_INFERENCE_KEY env var)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="test-assistant",
        help="Model alias to use in requests",
    )
