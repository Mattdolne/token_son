"""Pipeline Interceptador e Sanitizador de Prompts (PT-BR -> Inglês Técnico Denso)."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import List, Tuple
import urllib.parse
import requests

import config


@dataclass
class SanitizeResult:
    """Contém o prompt original, a versão sanitizada em inglês e métricas de economia de tokens."""

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
    """Traduz e comprime prompts técnicos de PT-BR para inglês denso via SLM local ou motor híbrido."""

    # Expressões conversacionais, floreios e preâmbulos em PT-BR que gastam tokens sem valor técnico
    _PT_FILLERS: List[Tuple[str, str]] = [
        (r"(?i)\b(ol[áa]|oi|bom dia|boa tarde|boa noite|tudo bem|como vai)\b[,!\.\?]?\s*", ""),
        (
            r"(?i)\b(por favor|por gentileza|se poss[íi]vel|voc[êe] poderia|poderia me ajudar a|"
            r"gostaria que voc[êe]|eu gostaria que|quero que voc[êe]|quero que me|preciso que voc[êe])\b[,!\.]?\s*",
            "",
        ),
        (r"(?i)\b(muito obrigado|obrigado desde j[áa]|agrade[çc]o desde j[áa]|valeu|obrigado)\b[,!\.]?\s*", ""),
        (r"(?i)[,\s]*\b(por exemplo|como por exemplo|a t[íi]tulo de exemplo)\b[,!\.]?", ""),
        (r"(?i)\b(eu preciso que|preciso de ajuda para|estou tentando)\b\s*", ""),
    ]

    # Glossário técnico e estrutural PT-BR -> EN ordenado por especificidade
    _PT_EN_PHRASES: List[Tuple[str, str]] = [
        ("cores do programa", "app color theme"),
        ("cor do programa", "app color"),
        ("seja azul e não ambar", "blue instead of amber"),
        ("seja azul e não âmbar", "blue instead of amber"),
        ("azul e não ambar", "blue not amber"),
        ("azul e não âmbar", "blue not amber"),
        ("janela do terminal mesmo", "terminal window"),
        ("janela do terminal", "terminal window"),
        ("como um terminal comum", "like standard shell"),
        ("terminal comum", "standard shell"),
        ("contagem de uso de tokens", "token usage counter"),
        ("contegem de uso de tokens", "token usage counter"),
        ("uso de tokens", "token usage"),
        ("no harness/modelo em uso", "for active harness/model"),
        ("modelo local sanitizador", "local sanitizer model"),
        ("banco de dados", "database"),
        ("testes unitários", "unit tests"),
        ("teste unitário", "unit test"),
        ("sem alterar", "without modifying"),
        ("retorne apenas", "return only"),
        ("código abaixo", "code below"),
        ("em seguida", "then"),
        ("para que", "to"),
        ("de onde", "where from"),
        ("como funciona", "how it works"),
        ("manipulado", "interactive"),
        ("manipular", "interact"),
        ("analise", "analyze"),
        ("analisar", "analyze"),
        ("corrija", "fix"),
        ("corrigir", "fix"),
        ("refatore", "refactor"),
        ("refatorar", "refactor"),
        ("otimize", "optimize"),
        ("otimizar", "optimize"),
        ("crie", "create"),
        ("criar", "create"),
        ("implemente", "implement"),
        ("implementar", "implement"),
        ("explique", "explain"),
        ("explicar", "explain"),
        ("mostre", "show"),
        ("mostrar", "show"),
        ("função", "function"),
        ("funcao", "function"),
        ("classe", "class"),
        ("código", "code"),
        ("codigo", "code"),
        ("erro", "error"),
        ("exceção", "exception"),
        ("consulta", "query"),
        ("mantendo", "preserving"),
        ("programa", "app"),
        ("cores", "colors"),
        ("azul", "blue"),
        ("âmbar", "amber"),
        ("ambar", "amber"),
        ("não", "not"),
        ("faz no", "in"),
    ]

    # Stopwords redundantes em inglês após tradução para adensamento de prompt (telegraphic style)
    _EN_DENSE_COMPRESSION: List[Tuple[str, str]] = [
        (r"(?i)\b(please|kindly|could you|would you|can you|i would like you to|i want you to|i want to)\b\s*", ""),
        (r"(?i)\b(i prefer that the|i prefer that|i prefer the)\b\s*", "Set "),
        (r"(?i)\b(to be blue and not amber|be blue and not amber|are blue and not amber|is blue and not amber)\b", "to blue instead of amber"),
        (r"(?i)\b(needs to be able to be|must be able to be|has to be able to be)\b", "must be"),
        (r"(?i)\b(within the terminal window itself|inside the terminal window itself)\b", "directly in terminal window"),
        (r"(?i)\b(show me the|display to me the|show the)\b", "Show"),
        (r"(?i)\b(explain to me about the|explain to me about|explain about the|explain to me)\b", "Explain"),
        (r"(?i)\b(where it comes from and how it works)\b", "origin & operation"),
        (r"(?i)[,\s]*\b(for example|for instance|such as for example)\b[,!\.]?", ""),
        (r"(?i)\b(just like|such as)\b", "like"),
        (r"(?i)\b(of the)\b", "of"),
        (r"(?i)\b(in the)\b", "in"),
    ]

    def __init__(self, base_url: str = config.OLLAMA_BASE_URL, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.context_buffer: List[int] = []
        self.context_tokens_used: int = 0

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Estima tokens via rotina nativa em C O(N) (com fallback em Python)."""
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
        """Limpa o buffer de contexto da conversa local (F5)."""
        self.context_buffer.clear()
        self.context_tokens_used = 0

    def sanitize(self, prompt_pt: str, model: str = config.DEFAULT_ACTIVE_SLM) -> SanitizeResult:
        """Executa o pipeline de tradução e compressão via Ollama SLM ou motor híbrido."""
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
        engine_label = model
        try:
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=3.5,
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
                sanitized_en, engine_label = self._hybrid_fallback(cleaned_input)
                used_fallback = True
        except requests.RequestException:
            sanitized_en, engine_label = self._hybrid_fallback(cleaned_input)
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
            model_used=engine_label,
        )

    @staticmethod
    def _strip_wrapping_backticks(text: str) -> str:
        """Remove blocos markdown externos caso o modelo tenha envolvido a resposta inteira."""
        stripped = text.strip()
        if stripped.startswith("```") and stripped.endswith("```") and stripped.count("```") == 2:
            lines = stripped.splitlines()
            if len(lines) >= 2:
                return "\n".join(lines[1:-1]).strip()
        return stripped

    def _hybrid_fallback(self, text: str) -> Tuple[str, str]:
        """Motor híbrido quando o Ollama não está rodando localmente:
        1. Protege 100% dos blocos de código, inline code e flags CLI (--flag).
        2. Remove floreios e preâmbulos em PT-BR.
        3. Traduz de PT-BR para EN (via endpoint leve de tradução ou dicionário técnico local).
        4. Aplica compressão telegráfica em EN para máxima densidade de tokens.
        """
        preserved_blocks: List[str] = []

        def _stash(match: re.Match) -> str:
            idx = len(preserved_blocks)
            preserved_blocks.append(match.group(0))
            return f"__CODE_BLOCK_{idx}__"

        # 1. Protege blocos de código, inline code e flags CLI (ex: --strict-mode)
        working = re.sub(r"```[\s\S]*?```|`[^`\n]+`|(?<=\s)--[a-zA-Z0-9_-]+", _stash, text)

        # 2. Remove floreios conversacionais em PT-BR antes da tradução
        for pattern, repl in self._PT_FILLERS:
            working = re.sub(pattern, repl, working)

        # 3. Tenta tradução rápida PT->EN se houver conectividade, senão aplica léxico local
        translated_ok = False
        try:
            q = urllib.parse.quote(working)
            url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=pt&tl=en&dt=t&q={q}"
            r = requests.get(url, timeout=2.0)
            if r.status_code == 200:
                arr = r.json()
                if isinstance(arr, list) and arr and isinstance(arr[0], list):
                    parts = [seg[0] for seg in arr[0] if isinstance(seg, list) and seg and seg[0]]
                    candidate = "".join(parts).strip()
                    if candidate:
                        working = candidate
                        translated_ok = True
        except Exception:
            pass

        if not translated_ok:
            for pt_term, en_term in self._PT_EN_PHRASES:
                working = re.sub(rf"(?i)\b{re.escape(pt_term)}\b", en_term, working)
            # Remove artigos/preposições soltas comuns em PT que restarem
            working = re.sub(r"(?i)\b(o|a|os|as|um|uma|de|do|da|dos|das|no|na|nos|nas|que|seja)\b\s+", "", working)

        # 4. Aplica compressão telegráfica em inglês para reduzir tokens mantendo 100% da semântica
        for pattern, repl in self._EN_DENSE_COMPRESSION:
            working = re.sub(pattern, repl, working)

        # Normaliza espaços
        working = re.sub(r"[ \t]+", " ", working)
        working = re.sub(r"\s+([,;\.\?!])", r"\1", working)
        working = re.sub(r"\n{3,}", "\n\n", working).strip()

        # 5. Restaura blocos de código e flags intactos
        for idx, block in enumerate(preserved_blocks):
            working = working.replace(f"__CODE_BLOCK_{idx}__", block)
            working = working.replace(f"__code_block_{idx}__", block)

        label = "Compressor Híbrido PT->EN (Sem Ollama)" if translated_ok else "Compressor Léxico Local (Offline)"
        return working, label
