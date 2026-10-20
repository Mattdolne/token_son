"""Gerenciamento de modelos locais (Ollama), descoberta de Harnesses (Antigravity/agy) e ciclo de vida de VRAM."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
from typing import Generator, List, Tuple
import requests

import config


@dataclass
class OllamaModelInfo:
    """Metadados de um modelo instalado localmente no Ollama."""

    name: str
    size_bytes: int = 0
    parameter_size: str = ""
    quantization: str = ""

    @property
    def size_gb(self) -> float:
        return round(self.size_bytes / (1024**3), 2) if self.size_bytes else 0.0

    @property
    def display_label(self) -> str:
        if self.size_bytes > 0:
            return f"{self.name} ({self.size_gb:.1f} GB)"
        return self.name


@dataclass
class CatalogModelInfo:
    """Modelo disponível no catálogo geral para instalação."""

    name: str
    size: str
    description: str
    recommended: bool
    is_installed: bool = False


@dataclass
class HarnessStatus:
    """Status de disponibilidade de um Harness no computador."""

    id: str
    name: str
    installed: bool
    binary_path: str
    version_info: str
    models_available: List[Tuple[str, str]]


class LocalModelManager:
    """Gerencia modelos Ollama, descoberta do Antigravity CLI e download/instalação de SLMs."""

    def __init__(self, base_url: str = config.OLLAMA_BASE_URL, timeout: float = 3.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.active_model: str = config.DEFAULT_ACTIVE_SLM

    def check_ollama_health(self) -> bool:
        """Verifica se o serviço local do Ollama está respondendo."""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=self.timeout)
            return resp.status_code == 200
        except requests.RequestException:
            return False

    def list_installed_models(self) -> Tuple[bool, List[OllamaModelInfo]]:
        """Descobre os modelos instalados no Ollama local via GET /api/tags."""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=self.timeout)
            resp.raise_for_status()
            payload = resp.json()
            raw_models = payload.get("models", [])
            models: List[OllamaModelInfo] = []
            for item in raw_models:
                details = item.get("details", {}) or {}
                models.append(
                    OllamaModelInfo(
                        name=item.get("name", "unknown"),
                        size_bytes=int(item.get("size", 0) or 0),
                        parameter_size=str(details.get("parameter_size", "")),
                        quantization=str(details.get("quantization_level", "")),
                    )
                )
            if models:
                if not any(m.name == self.active_model for m in models):
                    self.active_model = models[0].name
                return True, models
            # Ollama online, mas nenhum modelo baixado ainda
            return True, []
        except requests.RequestException:
            return False, []

    def get_catalog_with_status(self) -> List[CatalogModelInfo]:
        """Compara o catálogo de modelos recomendados com os que já estão instalados no PC."""
        _, installed = self.list_installed_models()
        installed_names = {m.name.split(":")[0] for m in installed} | {m.name for m in installed}

        catalog_result: List[CatalogModelInfo] = []
        for cat in config.CATALOG_AVAILABLE_MODELS:
            name = cat["name"]
            is_inst = (name in installed_names) or (name.split(":")[0] in installed_names)
            catalog_result.append(
                CatalogModelInfo(
                    name=name,
                    size=cat["size"],
                    description=cat["desc"],
                    recommended=cat["recommended"],
                    is_installed=is_inst,
                )
            )
        return catalog_result

    def pull_model_stream(self, model_name: str) -> Generator[str, None, bool]:
        """Baixa e instala um modelo via Ollama API streaming ou CLI com feedback de progresso."""
        # Tenta via API streaming do Ollama
        try:
            resp = requests.post(
                f"{self.base_url}/api/pull",
                json={"name": model_name, "stream": True},
                stream=True,
                timeout=1200.0,
            )
            if resp.status_code == 200:
                for line in resp.iter_lines():
                    if line:
                        try:
                            import json

                            data = json.loads(line.decode("utf-8"))
                            status = data.get("status", "")
                            completed = data.get("completed", 0)
                            total = data.get("total", 0)
                            if total > 0:
                                pct = round((completed / total) * 100, 1)
                                yield f"{status}: {pct}% ({round(completed/(1024**2))}MB / {round(total/(1024**2))}MB)"
                            else:
                                yield status
                        except Exception:
                            yield "Processando download..."
                return True
        except Exception:
            pass

        # Fallback via CLI se o comando `ollama` estiver disponível
        ollama_bin = shutil.which("ollama")
        if ollama_bin:
            try:
                proc = subprocess.Popen(
                    [ollama_bin, "pull", model_name],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                if proc.stdout:
                    for line in proc.stdout:
                        yield line.strip()
                proc.wait()
                return proc.returncode == 0
            except Exception as e:
                yield f"Erro ao executar comando ollama pull: {e}"
                return False

        yield (
            f"Ollama local não está ativo na porta 11434. "
            f"Inicie o Ollama com `ollama serve` ou execute `ollama pull {model_name}` no terminal."
        )
        return False

    def unload_model(self, model_name: str | None = None) -> Tuple[bool, str]:
        """Libera a memória VRAM enviando keep_alive: 0 ao Ollama."""
        target = model_name or self.active_model
        if not target:
            return False, "Nenhum modelo selecionado para descarregar."
        try:
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json={"model": target, "prompt": "", "keep_alive": 0},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return True, f"Modelo '{target}' descarregado da VRAM com sucesso (keep_alive=0)."
        except requests.RequestException as exc:
            return False, f"Falha ao descarregar '{target}': {exc}"

    def inspect_antigravity_harness(self) -> HarnessStatus:
        """Verifica se o Antigravity (agy) está instalado e lista os modelos disponíveis."""
        agy_bin = shutil.which("agy")
        if not agy_bin:
            possible_path = Path("/home/mattdolne/.local/bin/agy")
            if possible_path.exists() and os.access(possible_path, os.X_OK):
                agy_bin = str(possible_path)

        if not agy_bin:
            return HarnessStatus(
                id="agy",
                name="Google Antigravity (agy)",
                installed=False,
                binary_path="Não encontrado no PATH",
                version_info="Não instalado",
                models_available=config.DEFAULT_AGY_MODELS,
            )

        # Obter versão
        version_str = "Instalado"
        try:
            v_proc = subprocess.run([agy_bin, "--version"], capture_output=True, text=True, timeout=2.0)
            if v_proc.returncode == 0 and v_proc.stdout.strip():
                version_str = v_proc.stdout.strip()
        except Exception:
            pass

        # Obter modelos disponíveis do agy
        models_list: List[Tuple[str, str]] = []
        try:
            m_proc = subprocess.run([agy_bin, "models"], capture_output=True, text=True, timeout=4.0)
            if m_proc.returncode == 0:
                lines = m_proc.stdout.strip().splitlines()
                for line in lines:
                    line_clean = line.strip()
                    if not line_clean or "Fetching" in line_clean or "⠋" in line_clean:
                        continue
                    parts = line_clean.split(maxsplit=1)
                    if len(parts) >= 2:
                        models_list.append((parts[0].strip(), parts[1].strip()))
                    elif len(parts) == 1:
                        models_list.append((parts[0].strip(), parts[0].strip()))
        except Exception:
            pass

        if not models_list:
            models_list = config.DEFAULT_AGY_MODELS

        return HarnessStatus(
            id="agy",
            name="Google Antigravity (agy)",
            installed=True,
            binary_path=agy_bin,
            version_info=version_str,
            models_available=models_list,
        )
