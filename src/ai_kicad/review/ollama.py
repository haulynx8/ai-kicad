"""Ollama chat provider using local HTTP and schema-constrained output."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ai_kicad.review.provider import ReviewProviderError
from ai_kicad.review.schema import ReviewReport, ReviewSchemaError, review_json_schema, validate_review_report


SYSTEM_PROMPT = """You are an engineering design reviewer. Review only the supplied verified KiCad context.
Do not claim a design defect as a KiCad fact. Every finding must have evidence_level 'inferred' and cite one or more exact IDs from the top-level evidence registry. The context section contains references to that registry. Do not invent evidence IDs, pin semantics, component behavior, specifications, or missing source material. If the context is insufficient, state the uncertainty in the reasoning and use low confidence, or return no finding. Do not propose or apply edits; suggested_action is advice for the human reviewer. Return only the requested JSON review report."""


@dataclass
class OllamaProvider:
    model: str
    base_url: str = "http://localhost:11434"
    timeout_seconds: float = 900.0
    context_length: int = 32768

    def review(self, review_input: dict[str, Any]) -> ReviewReport:
        if not self.model.strip():
            raise ReviewProviderError("an Ollama model name is required")
        base_url = self.base_url.rstrip("/")
        if "://" not in base_url:
            base_url = "http://" + base_url
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Review this verified design context. Cite only IDs from the top-level evidence registry and include all "
                        "required fields in the review schema. Return at most five findings with concise reasoning.\n\n"
                        + json.dumps(review_input, ensure_ascii=False, separators=(",", ":"))
                    ),
                },
            ],
            "format": review_json_schema(),
            "stream": False,
            "think": False,
            "options": {
                "temperature": 0,
                "num_ctx": self.context_length,
                "num_predict": 1200,
            },
        }
        request = Request(
            base_url + "/api/chat",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                response_data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ReviewProviderError(f"Ollama returned HTTP {exc.code}: {detail}") from exc
        except TimeoutError as exc:
            raise ReviewProviderError(
                f"Ollama review request timed out after {self.timeout_seconds:g}s; no report was received"
            ) from exc
        except URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise ReviewProviderError(
                    f"Ollama review request timed out after {self.timeout_seconds:g}s; no report was received"
                ) from exc
            raise ReviewProviderError(f"could not reach Ollama at {base_url}: {exc}") from exc
        except OSError as exc:
            raise ReviewProviderError(f"could not reach Ollama at {base_url}: {exc}") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReviewProviderError(f"Ollama returned invalid JSON: {exc}") from exc

        try:
            content = response_data["message"]["content"]
            candidate = json.loads(content)
            return validate_review_report(candidate, review_input)
        except (KeyError, TypeError, json.JSONDecodeError, ReviewSchemaError) as exc:
            raise ReviewProviderError(f"Ollama response did not satisfy the review contract: {exc}") from exc
