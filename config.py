"""Configurações gerais, pontos de extremidade (endpoints), modelos e tabelas de preços."""

from pathlib import Path
import shutil

# Diretórios e Arquivos
BASE_DIR = Path(__file__).resolve().parent
LEDGER_DB_PATH = BASE_DIR / "prompt_ledger.db"

# Detecção de Harnesses no Sistema
ANTIGRAVITY_PATH = shutil.which("agy") or "/home/mattdolne/.local/bin/agy"

# Configurações do Ollama Local
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_TAGS_ENDPOINT = f"{OLLAMA_BASE_URL}/api/tags"
OLLAMA_GENERATE_ENDPOINT = f"{OLLAMA_BASE_URL}/api/generate"
OLLAMA_PULL_ENDPOINT = f"{OLLAMA_BASE_URL}/api/pull"

# Modelos recomendados padrão
DEFAULT_SLM_MODELS = [
    "qwen2.5-coder:1.5b",
    "llama3.2:1b",
    "deepseek-r1:1.5b",
]
DEFAULT_ACTIVE_SLM = "qwen2.5-coder:1.5b"

# Catálogo de modelos SLM disponíveis para instalação via Ollama
CATALOG_AVAILABLE_MODELS = [
    {
        "name": "qwen2.5-coder:1.5b",
        "size": "1.0 GB",
        "desc": "Otimizado para código, tradução técnica e instruções densas.",
        "recommended": True,
    },
    {
        "name": "llama3.2:1b",
        "size": "1.3 GB",
        "desc": "Modelo ultraleve da Meta, ideal para baixa latência.",
        "recommended": True,
    },
    {
        "name": "deepseek-r1:1.5b",
        "size": "1.1 GB",
        "desc": "Modelo de raciocínio lógico ultracompacto.",
        "recommended": True,
    },
    {
        "name": "qwen2.5:3b",
        "size": "1.9 GB",
        "desc": "Excelente vocabulário multilíngue e precisão conceitual.",
        "recommended": False,
    },
    {
        "name": "mistral:7b",
        "size": "4.1 GB",
        "desc": "Modelo geral robusto para tarefas mais complexas.",
        "recommended": False,
    },
]

# Modelos do Antigravity (agy)
DEFAULT_AGY_MODELS = [
    ("gemini-3.8-flash-medium", "Gemini 3.8 Flash (Médio - Rápido)"),
    ("gemini-4-argon-medium", "Gemini 4 Argon (Médio - Alta Capacidade)"),
    ("gemini-4-argon-high", "Gemini 4 Argon (Alto)"),
    ("claude-sonnet-5-5-medium", "Claude Sonnet 5.5 (Médio)"),
    ("claude-opus-5-5-medium", "Claude Opus 5.5 (Médio)"),
    ("gpt-oss-120b-medium", "GPT-OSS 120B (Médio)"),
]

# Harnesses Suportados para Dispatch
HARNESS_TARGETS = [
    ("agy", "Antigravity CLI (agy)"),
    ("ollama", "Ollama Local (SLM Ativo)"),
    ("gemini-api", "Google Gemini API Direta"),
    ("shell", "Comando de Shell Personalizado"),
]
DEFAULT_HARNESS_TARGET = "agy"

# Tabela de preços aproximados por 1 Milhão de tokens de entrada (USD)
CLOUD_MODEL_PRICING_PER_1M_TOKENS: dict[str, float] = {
    "gemini-3.8-flash-medium": 0.15,
    "gemini-4-argon-medium": 0.50,
    "gemini-2.5-flash": 0.15,
    "gemini-2.5-pro": 1.25,
    "claude-sonnet-5-5-medium": 3.00,
    "claude-opus-5-5-medium": 15.00,
    "gpt-oss-120b-medium": 0.30,
    "ollama-local": 0.00,
    "agy": 0.15,
}

# Limiares de Hardware e Telemetria
TELEMETRY_POLL_INTERVAL_SEC = 2.0
RAM_WARNING_THRESHOLD_PCT = 85.0
VRAM_WARNING_THRESHOLD_PCT = 85.0
TOKEN_WINDOW_MAX = 8192
TOKEN_WINDOW_WARNING_PCT = 85.0
SESSION_COST_WARNING_USD = 1.00

# Instrução de Sistema para o Modelo Sanitizador
SANITIZER_SYSTEM_INSTRUCTION = """You are an ultra-dense prompt optimizer and technical translator.
Task:
1. Translate the user prompt from Portuguese to concise, precise technical English.
2. Strip conversational fillers, polite phrasing, and introductory fluff.
3. Preserve 100% of the technical constraints, logic, flags, and code tokens.
4. Output ONLY the raw optimized English text without backticks or meta-announcements."""
