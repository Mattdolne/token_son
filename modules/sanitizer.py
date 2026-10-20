"""Prompt Interceptor & Sanitizer Pipeline powered by local Ollama SLM."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import List, Tuple
import requests

import config


@dataclass
class SanitizeResult:
    """Contains the original and sanitized prompt along with compression and token metrics."""

    original_pt: str
    sanitized_en: str
    pt_chars: int
    en_chars: int
    pt_tokens_est: int
    en_tokens_est: int
    token_savings_pct: float
    used_fallback: bool = False
    model_used: str = ""


class PromptSanitizer:
    """Translates and compresses PT-BR technical prompts into dense English via Ollama."""

    # Common PT-BR conversational fillers and polite preambles for local deterministic fallback
    _PT_FILLERS = [
        r"(?i)\b(ol[áa]|oi|bom dia|boa tarde|boa noite)\b[,!\.]?\s*",
        r"(?i)\b(por favor|por gentileza|se poss[íi]vel|voc[êe] poderia|poderia me ajudar a|gostaria que voc[êe])\b[,!\.]?\s*",
        r"(?i)\b(muito obrigado|obrigado desde j[áa]|agrade[çc]o desde j[áa]|valeu)\b[,!\.]?\s*",
        r"(?i)\b(eu preciso que|preciso de ajuda para|estou tentando)\b\s*",
    ]

    _PT_EN_GLOSSARY = {
        "analise": "analyze",
        "analisar": "analyze",
        "corrija": "fix",
        "corrigir": "fix",
        "refatore": "refactor",
        "refatorar": "refactor",
        "otimize": "optimize",
        "otimizar": "optimize",
        "crie": "create",
        "criar": "create",
        "implemente": "implement",
        "implementar": "implement",
        "explique": "explain",
        "explicar": "explain",
        "função": "function",
        "funcao": "function",
        "classe": "class",
        "código": "code",
        "codigo": "code",
        "erro": "error",
        "exceção": "exception",
        "banco de dados": "database",
        "consulta": "query",
        "testes unitários": "unit tests",
        "teste unitário": "unit test",
        "sem alterar": "without modifying",
        "mantendo": "preserving",
        "retorne apenas": "return only",
        "em seguida": "then",
        "para que": "so that",
    }

    def __init__(self, base_url: str = config.OLLAMA_BASE_URL, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.context_buffer: List[int] = []
        self.context_tokens_used: int = 0

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Estimate LLM token count via native C O(N) routine (with Python fallback)."""
        if not text or not text.strip():
            return 0
        try:
            from modules.terminal import NativePtyBridge

            native_est = NativePtyBridge.get().estimate_tokens(text)
            if native_est is not None and native_est > 0:
                return native_est
        except Exception:
            pass
        char_est = max(1, round(len(text) / 4))
        token_chunks = re.findall(r"\w+|[^\w\s]", text, re.UNICODE)
        word_est = max(1, int(len(token_chunks) * 1.15))
        return max(1, round((char_est + word_est) / 2))

    def clear_context(self) -> None:
        """Flush local conversation context buffer to force a zero-context run (F5)."""
        self.context_buffer.clear()
        self.context_tokens_used = 0

    def sanitize(self, prompt_pt: str, model: str = config.DEFAULT_ACTIVE_SLM) -> SanitizeResult:
        """Run the PT-BR prompt through local Ollama SLM or deterministic fallback if offline."""
        cleaned_input = prompt_pt.strip()
        if not cleaned_input:
            return SanitizeResult(
                original_pt="",
                sanitized_en="",
                pt_chars=0,
                en_chars=0,
                pt_tokens_est=0,
                en_tokens_est=0,
                token_savings_pct=0.0,
                used_fallback=False,
                model_used=model,
            )

        pt_chars = len(cleaned_input)
        pt_tokens = self.estimate_tokens(cleaned_input)

        payload: dict = {
            "model": model,
            "system": config.SANITIZER_SYSTEM_INSTRUCTION,
            "prompt": cleaned_input,
            "stream": False,
            "options": {
                "temperature": 0.1,
            },
        }
        if self.context_buffer:
            payload["context"] = self.context_buffer

        used_fallback = False
        try:
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            sanitized_en = str(data.get("response", "")).strip()
            sanitized_en = self._strip_wrapping_backticks(sanitized_en)
            if "context" in data and isinstance(data["context"], list):
                self.context_buffer = data["context"]
            eval_count = int(data.get("eval_count", 0) or 0)
            prompt_eval_count = int(data.get("prompt_eval_count", 0) or 0)
            self.context_tokens_used += (eval_count + prompt_eval_count) or self.estimate_tokens(sanitized_en)
            if not sanitized_en:
                sanitized_en = self._local_rule_fallback(cleaned_input)
                used_fallback = True
        except requests.RequestException:
            sanitized_en = self._local_rule_fallback(cleaned_input)
            used_fallback = True
            self.context_tokens_used += self.estimate_tokens(sanitized_en)

        en_chars = len(sanitized_en)
        en_tokens = self.estimate_tokens(sanitized_en)
        savings_pct = round(((pt_tokens - en_tokens) / pt_tokens) * 100.0, 1) if pt_tokens > 0 else 0.0

        return SanitizeResult(
            original_pt=cleaned_input,
            sanitized_en=sanitized_en,
            pt_chars=pt_chars,
            en_chars=en_chars,
            pt_tokens_est=pt_tokens,
            en_tokens_est=en_tokens,
            token_savings_pct=savings_pct,
            used_fallback=used_fallback,
            model_used=model if not used_fallback else f"{model} (offline-fallback)",
        )

    @staticmethod
    def _strip_wrapping_backticks(text: str) -> str:
        """Strip outer markdown code fences if the model wrapped the entire response."""
        stripped = text.strip()
        if stripped.startswith("```") and stripped.endswith("```") and stripped.count("```") == 2:
            lines = stripped.splitlines()
            if len(lines) >= 2:
                return "\n".join(lines[1:-1]).strip()
        return stripped

    def _local_rule_fallback(self, text: str) -> str:
        """Rule-based filler stripper and glossary translator when Ollama daemon is unreachable.

        Preserves 100% of fenced code blocks, inline code, CLI flags, and variables.
        """
        preserved_blocks: List[str] = []

        def _stash(match: re.Match) -> str:
            idx = len(preserved_blocks)
            preserved_blocks.append(match.group(0))
            return f"__CODE_BLOCK_{idx}__"

        # Preserve fenced code blocks and inline code
        working = re.sub(r"```[\s\S]*?```|`[^`\n]+`", _stash, text)

        for pattern in self._PT_FILLERS:
            working = re.sub(pattern, "", working)

        for pt_term, en_term in self._PT_EN_GLOSSARY.items():
            working = re.sub(rf"(?i)\b{re.escape(pt_term)}\b", en_term, working)

        # Normalize whitespace outside code blocks
        working = re.sub(r"[ \t]+", " ", working)
        working = re.sub(r"\n{3,}", "\n\n", working).strip()

        for idx, block in enumerate(preserved_blocks):
            working = working.replace(f"__CODE_BLOCK_{idx}__", block)

        return working

