"""Despachador para Harnesses e Modelos estruturado com o Padrão Strategy (SOLID OCP/SRP/DIP)."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Dict
import requests

import config
from modules.interfaces import IHarnessStrategy


@dataclass
class DispatchResponse:
    """Resultado imutável do envio do prompt sanitizado para o Harness ou modelo de destino."""

    success: bool
    target: str
    output: str
    latency_ms: float


class AntigravityStrategy(IHarnessStrategy):
    """Estratégia de execução direta via Google Antigravity CLI (agy --print)."""

    def execute(self, prompt: str, model_name: str | None = None, **kwargs: object) -> DispatchResponse:
        start = time.perf_counter()
        agy_bin = shutil.which("agy") or config.ANTIGRAVITY_PATH
        if not (Path(agy_bin).exists() and os.access(agy_bin, os.X_OK)):
            latency = round((time.perf_counter() - start) * 1000, 1)
            return DispatchResponse(
                success=False,
                target="Antigravity (agy)",
                output="Executável 'agy' não foi encontrado no sistema ou em ~/.local/bin/agy.",
                latency_ms=latency,
            )

        # Passagem por lista de argumentos sem shell=True para prevenir command injection
        # Inclui --dangerously-skip-permissions para evitar bloqueio de permissão em modo headless (--print)
        cmd = [agy_bin, "--dangerously-skip-permissions", "--print", prompt]
        if model_name:
            cmd.extend(["--model", model_name])

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60.0,
                check=False,
            )
            latency = round((time.perf_counter() - start) * 1000, 1)
            output = proc.stdout.strip() or proc.stderr.strip() or "(Comando executado sem saída de texto)"
            return DispatchResponse(
                success=proc.returncode == 0,
                target=f"Antigravity ({model_name or 'padrão'})",
                output=output,
                latency_ms=latency,
            )
        except subprocess.TimeoutExpired:
            latency = round((time.perf_counter() - start) * 1000, 1)
            return DispatchResponse(
                success=False,
                target=f"Antigravity ({model_name or 'padrão'})",
                output="Tempo limite de execução excedido (timeout de 60s).",
                latency_ms=latency,
            )
        except Exception as exc:
            latency = round((time.perf_counter() - start) * 1000, 1)
            return DispatchResponse(
                success=False,
                target=f"Antigravity ({model_name or 'padrão'})",
                output=f"Erro ao disparar agy: {exc}",
                latency_ms=latency,
            )


class OllamaStrategy(IHarnessStrategy):
    """Estratégia de inferência local via API HTTP do Ollama."""

    def execute(self, prompt: str, model_name: str | None = None, **kwargs: object) -> DispatchResponse:
        start = time.perf_counter()
        model = model_name or config.DEFAULT_ACTIVE_SLM
        try:
            resp = requests.post(
                f"{config.OLLAMA_BASE_URL}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False},
                timeout=45.0,
            )
            latency = round((time.perf_counter() - start) * 1000, 1)
            resp.raise_for_status()
            data = resp.json()
            return DispatchResponse(
                success=True,
                target=f"Ollama ({model})",
                output=str(data.get("response", "")).strip(),
                latency_ms=latency,
            )
        except Exception as exc:
            latency = round((time.perf_counter() - start) * 1000, 1)
            return DispatchResponse(
                success=False,
                target=f"Ollama ({model})",
                output=f"Falha de comunicação com Ollama: {exc}",
                latency_ms=latency,
            )


class GeminiApiStrategy(IHarnessStrategy):
    """Estratégia de envio para a API em nuvem Google Gemini via SDK google-genai."""

    def execute(self, prompt: str, model_name: str | None = None, **kwargs: object) -> DispatchResponse:
        start = time.perf_counter()
        model = model_name or "gemini-2.5-flash"
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            latency = round((time.perf_counter() - start) * 1000, 1)
            return DispatchResponse(
                success=True,
                target=f"Gemini API ({model}) [Simulação Local]",
                output=(
                    f"[Modo de Simulação — Defina GEMINI_API_KEY no ambiente para chamadas de nuvem ativas]\n"
                    f"Prompt aprovado enviado ({len(prompt)} caracteres):\n"
                    f"--------------------------------------------------\n"
                    f"{prompt}"
                ),
                latency_ms=latency,
            )
        try:
            from google import genai

            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=model,
                contents=prompt,
            )
            latency = round((time.perf_counter() - start) * 1000, 1)
            text = getattr(response, "text", None) or str(response)
            return DispatchResponse(
                success=True,
                target=f"Gemini Cloud ({model})",
                output=text.strip(),
                latency_ms=latency,
            )
        except Exception as exc:
            latency = round((time.perf_counter() - start) * 1000, 1)
            return DispatchResponse(
                success=False,
                target=f"Gemini Cloud ({model})",
                output=f"Erro na API Gemini: {exc}",
                latency_ms=latency,
            )


class ShellPipeStrategy(IHarnessStrategy):
    """Estratégia para enviar o prompt via stdin a um comando de teste local."""

    def execute(self, prompt: str, model_name: str | None = None, **kwargs: object) -> DispatchResponse:
        start = time.perf_counter()
        shell_cmd = str(kwargs.get("custom_shell_cmd") or model_name or "cat")
        try:
            proc = subprocess.run(
                shell_cmd,
                input=prompt,
                capture_output=True,
                text=True,
                shell=True,
                timeout=30.0,
                check=False,
            )
            latency = round((time.perf_counter() - start) * 1000, 1)
            output = proc.stdout.strip() or proc.stderr.strip() or "(Comando executado sem saída)"
            return DispatchResponse(
                success=proc.returncode == 0,
                target=f"Terminal: {shell_cmd}",
                output=output,
                latency_ms=latency,
            )
        except Exception as exc:
            latency = round((time.perf_counter() - start) * 1000, 1)
            return DispatchResponse(
                success=False,
                target=f"Terminal: {shell_cmd}",
                output=f"Erro ao executar comando: {exc}",
                latency_ms=latency,
            )


class HarnessDispatcher:
    """Coordenador central de envio que delega para estratégias plugáveis (OCP/DIP)."""

    def __init__(self) -> None:
        self._strategies: Dict[str, IHarnessStrategy] = {
            "agy": AntigravityStrategy(),
            "ollama": OllamaStrategy(),
            "gemini-api": GeminiApiStrategy(),
            "shell": ShellPipeStrategy(),
        }

    def register_strategy(self, harness_type: str, strategy: IHarnessStrategy) -> None:
        """Permite registrar novos Harnesses em tempo de execução sem alterar a classe."""
        self._strategies[harness_type] = strategy

    def dispatch(
        self,
        approved_prompt: str,
        harness_type: str = "agy",
        model_name: str | None = None,
        custom_shell_cmd: str | None = None,
    ) -> DispatchResponse:
        """Envia o prompt aprovado usando a estratégia correspondente."""
        strategy = self._strategies.get(harness_type)
        if strategy is not None:
            return strategy.execute(
                approved_prompt,
                model_name=model_name,
                custom_shell_cmd=custom_shell_cmd,
            )

        start = time.perf_counter()
        latency = round((time.perf_counter() - start) * 1000, 1)
        return DispatchResponse(
            success=True,
            target=f"{harness_type}:{model_name or 'padrão'}",
            output=(
                f"[Simulação de Harness | Alvo: {harness_type}]\n"
                f"Prompt recebido ({len(approved_prompt)} caracteres):\n\n"
                f"{approved_prompt}"
            ),
            latency_ms=latency,
        )
