"""Testes unitários para o Interceptador de Prompts e Middleware de Harnesses."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from unittest.mock import MagicMock, patch
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from app import RetroHarnessInterceptorApp
from modules.harness import HarnessDispatcher
from modules.infra import InfraObserver
from modules.models import LocalModelManager
from modules.quota import QuotaInspector
from modules.sanitizer import PromptSanitizer
from modules.telemetry import TelemetryCollector
from modules.terminal import LinuxTerminalSession, NativePtyBridge


def test_model_manager_discovery_and_unload() -> None:
    mgr = LocalModelManager(base_url="http://localhost:11434")

    with patch("modules.models.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "models": [
                {
                    "name": "qwen2.5-coder:1.5b",
                    "size": 1073741824,
                    "details": {"parameter_size": "1.5B", "quantization_level": "Q4_K_M"},
                }
            ]
        }
        mock_get.return_value = mock_resp

        online, models = mgr.list_installed_models()
        assert online is True
        assert len(models) == 1
        assert models[0].name == "qwen2.5-coder:1.5b"
        assert models[0].size_gb == 1.0

    with patch("modules.models.requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        ok, msg = mgr.unload_model("qwen2.5-coder:1.5b")
        assert ok is True
        assert "keep_alive=0" in msg
        mock_post.assert_called_once_with(
            "http://localhost:11434/api/generate",
            json={"model": "qwen2.5-coder:1.5b", "prompt": "", "keep_alive": 0},
            timeout=mgr.timeout,
        )


def test_model_manager_antigravity_detection() -> None:
    mgr = LocalModelManager()
    status = mgr.inspect_antigravity_harness()
    # Verifica que o objeto foi retornado com campos consistentes
    assert status.id == "agy"
    assert status.name == "Google Antigravity (agy)"
    assert len(status.models_available) > 0


def test_sanitizer_preserves_code_and_strips_fillers_offline() -> None:
    sanitizer = PromptSanitizer(base_url="http://localhost:11434")
    pt_prompt = (
        "Olá, bom dia! Por favor, você poderia me ajudar a refatorar esta função "
        "mantendo a flag `--strict-mode` e o código abaixo?\n"
        "```python\ndef calc(x: int) -> int:\n    return x * 2\n```\n"
        "Muito obrigado desde já!"
    )

    with patch("modules.sanitizer.requests.post", side_effect=requests.ConnectionError("Offline")):
        result = sanitizer.sanitize(pt_prompt, model="qwen2.5-coder:1.5b")

    assert result.used_fallback is True
    assert "--strict-mode" in result.sanitized_en
    assert "def calc(x: int) -> int:" in result.sanitized_en
    assert "Olá" not in result.sanitized_en
    assert "Muito obrigado" not in result.sanitized_en
    assert result.en_chars < result.pt_chars


def test_sanitizer_with_mocked_ollama_and_context_flush() -> None:
    sanitizer = PromptSanitizer(base_url="http://localhost:11434")

    with patch("modules.sanitizer.requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "response": "Refactor `calc(x)` preserving `--strict-mode`.",
            "context": [101, 102, 103],
            "eval_count": 12,
            "prompt_eval_count": 30,
        }
        mock_post.return_value = mock_resp

        res = sanitizer.sanitize("Por favor refatore calc(x) mantendo --strict-mode")
        assert res.used_fallback is False
        assert res.sanitized_en == "Refactor `calc(x)` preserving `--strict-mode`."
        assert sanitizer.context_buffer == [101, 102, 103]
        assert sanitizer.context_tokens_used == 42

        sanitizer.clear_context()
        assert sanitizer.context_buffer == []
        assert sanitizer.context_tokens_used == 0


def test_telemetry_sqlite_ledger_and_hardware(tmp_path: Path) -> None:
    db_file = tmp_path / "test_ledger.db"
    telemetry = TelemetryCollector(db_path=db_file)

    cost = telemetry.record_prompt(
        pt_chars=500,
        en_tokens_est=100_000,
        cloud_model="gemini-3.8-flash-medium",
    )
    # gemini-3.8-flash-medium is $0.15 per 1M tokens -> 100k tokens = $0.015
    assert cost == 0.015

    stats = telemetry.get_ledger_stats()
    assert stats.session_prompts == 1
    assert stats.session_tokens == 100_000
    assert stats.session_cost_usd == 0.015
    assert stats.daily_cost_usd == 0.015
    assert stats.monthly_cost_usd == 0.015

    records = telemetry.get_recent_records(limit=5)
    assert len(records) == 1
    assert records[0].en_tokens_est == 100_000

    hw = telemetry.poll_hardware()
    assert 0.0 <= hw.ram_percent <= 100.0
    assert hw.ram_total_gb > 0.0


def test_infra_observer_graceful_offline() -> None:
    observer = InfraObserver()
    with patch("modules.infra.subprocess.run", side_effect=OSError("Daemon down")):
        docker_ok, docker_status, containers = observer._docker_cli_fallback()
        assert docker_ok is False
        assert docker_status == "Offline"
        assert containers == []

        k8s_ok, k8s_status, pods = observer._kubectl_fallback()
        assert k8s_ok is False
        assert k8s_status == "Offline"
        assert pods == []


def test_harness_dispatcher_shell() -> None:
    dispatcher = HarnessDispatcher()
    resp = dispatcher.dispatch(
        "Optimize query",
        harness_type="shell",
        custom_shell_cmd="cat",
    )
    assert resp.success is True
    assert resp.output == "Optimize query"


def test_native_c_pty_terminal_persistence_and_ansi(tmp_path: Path) -> None:
    bridge = NativePtyBridge.get()
    assert bridge.available is True

    # Testa limpeza ANSI nativa em C
    dirty = b"\x1b[32mHello\x1b[0m \r\nWorld"
    assert bridge.strip_ansi(dirty) == "Hello \nWorld"

    # Testa estimativa rápida de tokens em C
    tokens = bridge.estimate_tokens("SELECT * FROM users WHERE id = 42;")
    assert tokens is not None and tokens > 0

    # Testa persistência de diretório (cd) e variável de ambiente (export) no PTY real
    session = LinuxTerminalSession(initial_cwd=tmp_path)
    try:
        sub = tmp_path / "subdir"
        sub.mkdir()
        session.execute_command(f"cd {sub}")
        assert Path(session.get_cwd()) == sub.resolve()

        session.execute_command("export MEU_TOKEN_TESTE=valor_123")
        _, out = session.execute_command("echo $MEU_TOKEN_TESTE")
        assert "valor_123" in out
    finally:
        session.close()


def test_quota_inspector_returns_structured_groups() -> None:
    inspector = QuotaInspector()
    quota = inspector.fetch_user_quota()
    assert quota is not None
    assert quota.account_email != ""
    assert len(quota.groups) >= 3
    # Verifica grupos clássicos do Antigravity
    nomes_grupos = [g.display_name for g in quota.groups]
    assert any("Gemini" in n for n in nomes_grupos)
    assert any("Claude" in n or "GPT" in n for n in nomes_grupos)
    for g in quota.groups:
        for b in g.buckets:
            assert 0 <= b.remaining_pct <= 100
            assert "Renova" in b.reset_desc or "Disponível" in b.reset_desc


def test_tui_app_mounts_and_handles_actions(tmp_path: Path) -> None:
    async def _run_ui() -> None:
        db_file = tmp_path / "ui_ledger.db"
        app = RetroHarnessInterceptorApp(db_path=db_file)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            # Verifica componentes principais
            pt_input = app.query_one("#input-pt")
            en_preview = app.query_one("#preview-en")
            assert pt_input is not None
            assert en_preview.read_only is True

            # Testa ação de limpar contexto
            app.action_limpar_contexto()
            await pilot.pause()
            assert app.sanitizador.context_tokens_used == 0

    asyncio.run(_run_ui())
