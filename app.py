"""Interface TUI Retrô Azul para Interceptação de Prompts, Otimização de Tokens e Terminal Linux."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, List, Optional

from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Label,
    ProgressBar,
    Select,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)

import config
from modules.harness import HarnessDispatcher
from modules.infra import InfraObserver
from modules.models import CatalogModelInfo, HarnessStatus, LocalModelManager
from modules.sanitizer import PromptSanitizer, SanitizeResult
from modules.telemetry import HardwareMetrics, LedgerStats, TelemetryCollector
from modules.terminal import LinuxTerminalSession


class TerminalLinuxDireto(TextArea):
    """Emulador de Terminal Linux interativo manipulável dentro da própria janela.

    Permite digitar diretamente após o prompt `usuario@host:~/caminho$ `, executar com Enter,
    navegar no histórico com Seta Cima/Baixo, autocompletar arquivos com Tab, limpar com Ctrl+L
    e enviar SIGINT com Ctrl+C.
    """

    def __init__(
        self,
        sessao_pty: LinuxTerminalSession,
        on_cmd_submit: Callable[[str], None],
        on_sigint: Callable[[], None],
        **kwargs,
    ) -> None:
        super().__init__("", **kwargs)
        self.sessao_pty = sessao_pty
        self._on_cmd_submit = on_cmd_submit
        self._on_sigint = on_sigint
        self._historico: List[str] = []
        self._indice_historico: int = -1
        self._ocupado: bool = False

    def on_mount(self) -> None:
        prompt = self.sessao_pty.get_prompt_prefix()
        banner = (
            f"=== TERMINAL LINUX INTERATIVO (PTY NATIVO EM C - PID {self.sessao_pty.child_pid}) ===\n"
            "Digite diretamente nesta janela. [Enter] Executar | [Cima/Baixo] Histórico | [Tab] Autocompletar | [Ctrl+L] Limpar | [Ctrl+C] SIGINT\n\n"
            f"{prompt} "
        )
        self.load_text(banner)
        self.move_cursor_to_end()

    def move_cursor_to_end(self) -> None:
        linhas = self.text.split("\n")
        ultima_linha_idx = max(0, len(linhas) - 1)
        ultima_col_idx = len(linhas[ultima_linha_idx])
        self.cursor_location = (ultima_linha_idx, ultima_col_idx)

    def _obter_prefixo_atual(self) -> str:
        return f"{self.sessao_pty.get_prompt_prefix()} "

    def _substituir_comando_atual(self, novo_cmd: str) -> None:
        linhas = self.text.split("\n")
        prefixo = self._obter_prefixo_atual()
        linhas[-1] = f"{prefixo}{novo_cmd}"
        self.load_text("\n".join(linhas))
        self.move_cursor_to_end()

    def _extrair_comando_atual(self) -> str:
        linhas = self.text.split("\n")
        ultima = linhas[-1] if linhas else ""
        prefixo = self._obter_prefixo_atual()
        if ultima.startswith(prefixo):
            return ultima[len(prefixo) :]
        if "$ " in ultima:
            return ultima.split("$ ", 1)[1]
        return ultima

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "ctrl+c":
            event.prevent_default()
            event.stop()
            self._on_sigint()
            return

        if event.key == "ctrl+l":
            event.prevent_default()
            event.stop()
            self.load_text(self._obter_prefixo_atual())
            self.move_cursor_to_end()
            return

        if self._ocupado:
            event.prevent_default()
            event.stop()
            return

        linhas = self.text.split("\n")
        ultima_linha_idx = max(0, len(linhas) - 1)
        prefixo = self._obter_prefixo_atual()
        col_minima = len(prefixo)
        row, col = self.cursor_location

        # Garante que a digitação sempre ocorra na linha ativa do prompt
        if row < ultima_linha_idx and event.key not in ("up", "down", "pageup", "pagedown"):
            self.move_cursor_to_end()
            row, col = self.cursor_location

        if event.key == "enter":
            event.prevent_default()
            event.stop()
            cmd = self._extrair_comando_atual().strip()
            if cmd == "clear":
                self.load_text(self._obter_prefixo_atual())
                self.move_cursor_to_end()
                return
            if cmd:
                if not self._historico or self._historico[-1] != cmd:
                    self._historico.append(cmd)
                self._indice_historico = len(self._historico)
                self._ocupado = True
                self.load_text(f"{self.text}\n")
                self.move_cursor_to_end()
                self._on_cmd_submit(cmd)
            else:
                self.load_text(f"{self.text}\n{self._obter_prefixo_atual()}")
                self.move_cursor_to_end()
            return

        if event.key in ("backspace", "left") and row == ultima_linha_idx and col <= col_minima:
            event.prevent_default()
            event.stop()
            return

        if event.key == "home":
            event.prevent_default()
            event.stop()
            self.cursor_location = (ultima_linha_idx, col_minima)
            return

        if event.key == "up":
            event.prevent_default()
            event.stop()
            if self._historico and self._indice_historico > 0:
                self._indice_historico -= 1
                self._substituir_comando_atual(self._historico[self._indice_historico])
            return

        if event.key == "down":
            event.prevent_default()
            event.stop()
            if self._historico:
                if self._indice_historico < len(self._historico) - 1:
                    self._indice_historico += 1
                    self._substituir_comando_atual(self._historico[self._indice_historico])
                else:
                    self._indice_historico = len(self._historico)
                    self._substituir_comando_atual("")
            return

        if event.key == "tab":
            event.prevent_default()
            event.stop()
            cmd_atual = self._extrair_comando_atual()
            partes = cmd_atual.split()
            parcial = partes[-1] if partes else ""
            if parcial:
                cwd = Path(self.sessao_pty.get_cwd())
                try:
                    candidatos = [
                        p.name + ("/" if p.is_dir() else "")
                        for p in cwd.iterdir()
                        if p.name.startswith(parcial)
                    ]
                    if len(candidatos) == 1:
                        partes[-1] = candidatos[0]
                        self._substituir_comando_atual(" ".join(partes))
                except Exception:
                    pass
            return

        await super()._on_key(event)

    def anexar_resposta(self, saida: str) -> None:
        self._ocupado = False
        texto_base = self.text
        if saida:
            novo_texto = f"{texto_base}{saida}\n{self._obter_prefixo_atual()}"
        else:
            novo_texto = f"{texto_base}{self._obter_prefixo_atual()}"
        linhas = novo_texto.splitlines()
        if len(linhas) > 200:
            linhas = linhas[-200:]
        self.load_text("\n".join(linhas))
        self.move_cursor_to_end()


class RetroHarnessInterceptorApp(App):
    """Aplicativo TUI estilo Terminal Retrô Azul para Interceptação de Prompts e Harnesses."""

    TITLE = "TOKEN SON — INTERCEPTADOR DE PROMPTS & TERMINAL LINUX"
    CSS_PATH = str(Path(__file__).resolve().parent / "styles" / "app.tcss")

    BINDINGS = [
        Binding("f2", "descarregar_slm", "Descarregar VRAM (F2)", show=True),
        Binding("f5", "limpar_contexto", "Limpar Contexto (F5)", show=True),
        Binding("f9", "sincronizar_tudo", "Sincronizar (F9)", show=True),
        Binding("ctrl+r", "otimizar_prompt", "Otimizar (Ctrl+R)", show=True),
        Binding("ctrl+q", "quit", "Sair (Ctrl+Q)", show=True),
    ]

    ollama_online: reactive[bool] = reactive(False)
    slm_ativo: reactive[str] = reactive(config.DEFAULT_ACTIVE_SLM)
    harness_alvo: reactive[str] = reactive(config.DEFAULT_HARNESS_TARGET)
    modelo_harness_ativo: reactive[str] = reactive("gemini-3.8-flash-medium")
    modo_edicao_ativo: reactive[bool] = reactive(False)

    def __init__(self, db_path: Path | str = config.LEDGER_DB_PATH) -> None:
        super().__init__()
        self.gerenciador_modelos = LocalModelManager()
        self.observador_infra = InfraObserver()
        self.sanitizador = PromptSanitizer()
        self.despachador = HarnessDispatcher()
        self.telemetria = TelemetryCollector(db_path=db_path)
        self.sessao_terminal = LinuxTerminalSession(initial_cwd=config.BASE_DIR)

        self._ultimo_resultado_sanitizacao: Optional[SanitizeResult] = None
        self._ultimas_metricas_hw: Optional[HardwareMetrics] = None
        self._ultimas_estatisticas_ledger: Optional[LedgerStats] = None
        self._status_antigravity: Optional[HarnessStatus] = None

        # Contadores estilo AGY / VS Code para o Harness/Modelo em uso
        self._ultimo_tokens_prompt_en: int = 0
        self._ultimo_tokens_resposta: int = 0
        self._ultimo_tokens_economizados: int = 0
        self._total_tokens_economizados_sessao: int = 0
        self._total_tokens_modelo_atual: int = 0

    def compose(self) -> ComposeResult:
        yield Static(
            "[ INICIALIZANDO TERMINAL AZUL... ] Verificando Antigravity, Ollama e Hardware...",
            id="top-status-bar",
        )

        with TabbedContent(initial="aba-prompt"):
            # ─────────────────────────────────────────────────────────────────
            # ABA 1: INTERCEPTADOR & FLUXO DE PROMPTS (2 COLUNAS VISÍVEIS)
            # ─────────────────────────────────────────────────────────────────
            with TabPane("1. Interceptador & Prompt", id="aba-prompt"):
                with Horizontal(classes="linha-dupla"):
                    # Coluna Esquerda: Entrada PT, Revisão EN e Saída do Harness
                    with Vertical(classes="coluna-esq"):
                        with Vertical(classes="painel-retro", id="painel-entrada"):
                            yield Label("┌── ENTRADA EM PORTUGUÊS (PT-BR) ──────────────┐", classes="titulo-painel")
                            yield TextArea("", id="input-pt", show_line_numbers=True)
                            with Horizontal(classes="linha-botoes"):
                                yield Button("Otimizar Prompt (Ctrl+R)", id="btn-otimizar", variant="primary")
                                yield Button("Limpar Texto", id="btn-limpar-entrada")

                        with Vertical(classes="painel-retro", id="painel-preview"):
                            yield Label("├── REVISÃO SANITIZADA EM INGLÊS (EN) ─────────┤", classes="titulo-painel")
                            yield Static("Aguardando inserção de prompt em português...", id="banner-economia")
                            yield TextArea("", id="preview-en", read_only=True, show_line_numbers=True)
                            with Horizontal(classes="linha-botoes"):
                                yield Button("Aprovar & Enviar", id="btn-aprovar", variant="success", disabled=True)
                                yield Button("Editar", id="btn-editar", disabled=True)
                                yield Button("Descartar", id="btn-descartar", variant="error", disabled=True)

                        with Vertical(classes="painel-retro", id="painel-saida"):
                            yield Label("└── RESPOSTA DO HARNESS / MODELO ──────────────┘", classes="titulo-painel")
                            yield TextArea(
                                "Pronto. Ao aprovar um prompt sanitizado, a resposta do harness aparecerá aqui.",
                                id="output-harness",
                                read_only=True,
                            )
                            with Horizontal(classes="linha-botoes"):
                                yield Button("Copiar Resposta", id="btn-copiar")

                    # Coluna Direita: Seleção de Harness/Modelo, Medidor de Tokens estilo VS Code e Infra
                    with Vertical(classes="coluna-dir"):
                        with Vertical(classes="painel-retro", id="painel-destino"):
                            yield Label("┌── HARNESS & MODELOS EM USO ──────────────────┐", classes="titulo-painel")
                            yield Label("Harness Alvo:")
                            yield Select(
                                [(rotulo, id_h) for id_h, rotulo in config.HARNESS_TARGETS],
                                value=config.DEFAULT_HARNESS_TARGET,
                                id="select-harness-alvo",
                                allow_blank=False,
                            )
                            yield Label("Modelo do Harness (ex: Antigravity):")
                            yield Select(
                                [(desc, m_id) for m_id, desc in config.DEFAULT_AGY_MODELS],
                                value="gemini-3.8-flash-medium",
                                id="select-modelo-harness",
                                allow_blank=False,
                            )
                            yield Label("Motor Sanitizador PT->EN:")
                            yield Select(
                                [(m, m) for m in config.DEFAULT_SLM_MODELS],
                                value=config.DEFAULT_ACTIVE_SLM,
                                id="select-slm-sanitizador",
                                allow_blank=False,
                            )

                        with Vertical(classes="painel-retro", id="painel-tokens-modelo"):
                            yield Label("├── USO DE TOKENS NO MODELO (ESTILO AGY) ──────┤", classes="titulo-painel")
                            yield Static(
                                "Modelo Ativo: gemini-3.8-flash-medium\n"
                                "• Entrada (EN): 0 tok | Saída: 0 tok\n"
                                "• Poupança na Sessão: 0 tok (~$0.0000)\n"
                                "• Total Processado no Modelo: 0 tok",
                                id="resumo-tokens-rapido",
                            )
                            with Horizontal(id="linha-gauge"):
                                yield Label("Janela Contexto: ")
                                yield ProgressBar(total=config.TOKEN_WINDOW_MAX, show_eta=False, id="barra-tokens")
                            yield Static("Status: Pronto.", id="banner-alerta")

                        with Vertical(classes="painel-retro", id="painel-infra"):
                            yield Label("└── INFRAESTRUTURA LOCAL (F9) ─────────────────┘", classes="titulo-painel")
                            yield Static("Docker: Verificando...", id="caixa-docker")
                            yield Static("Kubernetes: Verificando...", id="caixa-k8s")

            # ─────────────────────────────────────────────────────────────────
            # ABA 2: GERENCIAMENTO DE HARNESSES & MODELOS
            # ─────────────────────────────────────────────────────────────────
            with TabPane("2. Harnesses & Modelos", id="aba-harnesses"):
                with Vertical(classes="painel-retro"):
                    yield Label("┌── HARNESSES DETECTADOS NO SISTEMA ───────────┐", classes="titulo-painel")
                    yield Static("Analisando ferramentas instaladas...", id="status-harness-info")

                with Horizontal(classes="linha-dupla"):
                    with Vertical(classes="coluna-esq"):
                        with Vertical(classes="painel-retro"):
                            yield Label("┌── MODELOS LOCAIS INSTALADOS (OLLAMA) ────────┐", classes="titulo-painel")
                            yield Static("Consultando Ollama local...", id="caixa-modelos-instalados")
                            with Horizontal(classes="linha-botoes"):
                                yield Button("Descarregar VRAM (F2)", id="btn-descarregar-vram-tab2")
                                yield Button("Limpar Buffer Contexto (F5)", id="btn-limpar-ctx-tab2")

                    with Vertical(classes="coluna-dir"):
                        with Vertical(classes="painel-retro"):
                            yield Label("┌── CATÁLOGO & INSTALAÇÃO DE MODELOS SLM ──────┐", classes="titulo-painel")
                            yield Static("Catálogo de modelos recomendados:", id="caixa-catalogo")
                            yield Select(
                                [(f"{c['name']} ({c['size']})", c["name"]) for c in config.CATALOG_AVAILABLE_MODELS],
                                value=config.CATALOG_AVAILABLE_MODELS[0]["name"],
                                id="select-modelo-para-instalar",
                                allow_blank=False,
                            )
                            with Horizontal(classes="linha-botoes"):
                                yield Button("Instalar Modelo Selecionado", id="btn-instalar-modelo", variant="primary")
                            yield Static("Pronto para download.", id="log-instalacao-modelo")

            # ─────────────────────────────────────────────────────────────────
            # ABA 3: CONSUMO DE TOKENS & TELEMETRIA DETALHADA
            # ─────────────────────────────────────────────────────────────────
            with TabPane("3. Consumo de Tokens", id="aba-telemetria"):
                with Vertical(classes="painel-retro"):
                    yield Label("┌── RESUMO FINANCEIRO E DE ECONOMIA ───────────┐", classes="titulo-painel")
                    yield Static(
                        "Consumo da Sessão: 0 tokens ($0.0000) | Hoje: $0.0000 | Este Mês: $0.0000",
                        id="resumo-financeiro-detalhado",
                    )
                    yield Static("Métricas de Hardware: CPU 0% | RAM: 0% | VRAM: 0.0 GB", id="hardware-detalhado")

                with Vertical(classes="painel-retro"):
                    yield Label("└── HISTÓRICO DE PROMPTS PROCESSADOS ─────────┘", classes="titulo-painel")
                    yield DataTable(id="tabela-historico-ledger")

            # ─────────────────────────────────────────────────────────────────
            # ABA 4: TERMINAL LINUX MANIPULÁVEL DIRETO NA PRÓPRIA JANELA
            # ─────────────────────────────────────────────────────────────────
            with TabPane("4. Terminal do PC", id="aba-terminal"):
                with Vertical(classes="painel-retro", id="painel-terminal-full"):
                    yield Label(
                        "┌── TERMINAL LINUX INTERATIVO (DIGITE DIRETO NA JANELA ABAIXO) ──┐",
                        classes="titulo-painel",
                    )
                    yield TerminalLinuxDireto(
                        sessao_pty=self.sessao_terminal,
                        on_cmd_submit=self._submeter_comando_terminal_direto,
                        on_sigint=self._interromper_terminal_direto,
                        id="janela-terminal-direta",
                    )

        yield Footer()

    def on_mount(self) -> None:
        tabela = self.query_one("#tabela-historico-ledger", DataTable)
        tabela.add_columns("ID", "Data/Hora", "Chars PT", "Tokens EN", "Harness/Modelo", "Custo Est. ($)")

        self.sincronizar_tudo()
        self.set_interval(config.TELEMETRY_POLL_INTERVAL_SEC, self.atualizar_telemetria_timer)

    def sincronizar_tudo(self) -> None:
        self.trabalhador_atualizar_harnesses()
        self.trabalhador_atualizar_modelos()
        self.trabalhador_atualizar_infra()
        self.atualizar_telemetria_ui()

    def _atualizar_texto_cabecalho(self) -> None:
        cabecalho = self.query_one("#top-status-bar", Static)
        status_ollama = "Online" if self.ollama_online else "Compressor Híbrido Ativo"

        hw = self._ultimas_metricas_hw
        if hw:
            vram_txt = f"{hw.vram_used_gb:.1f}/{hw.vram_total_gb:.1f}GB" if hw.gpu_available else "CPU"
            ram_txt = f"{hw.ram_percent:.0f}%"
            cpu_txt = f"{hw.cpu_percent:.0f}%"
        else:
            vram_txt = "0GB"
            ram_txt = "0%"
            cpu_txt = "0%"

        stats = self._ultimas_estatisticas_ledger
        custo_sessao = stats.session_cost_usd if stats else 0.0
        tok_sessao = self._total_tokens_modelo_atual or (stats.session_tokens if stats else 0)

        cabecalho.update(
            f"[ TOKEN SON ] Alvo: {self.harness_alvo.upper()}:{self.modelo_harness_ativo} | "
            f"Tokens Modelo: {tok_sessao} tok (Poupados: {self._total_tokens_economizados_sessao} tok) | "
            f"Sanitizador: {status_ollama} | CPU:{cpu_txt} RAM:{ram_txt} VRAM:{vram_txt} | ${custo_sessao:.4f}"
        )

        alerta = False
        if hw and (hw.ram_warning or hw.vram_warning):
            alerta = True
        if custo_sessao >= config.SESSION_COST_WARNING_USD:
            alerta = True
        cabecalho.set_class(alerta, "alerta-memoria")

    def atualizar_telemetria_timer(self) -> None:
        self.atualizar_telemetria_ui()

    def atualizar_telemetria_ui(self) -> None:
        hw = self.telemetria.poll_hardware()
        stats = self.telemetria.get_ledger_stats()
        self._ultimas_metricas_hw = hw
        self._ultimas_estatisticas_ledger = stats

        self._atualizar_texto_cabecalho()

        # Painel de Uso de Tokens estilo AGY / VS Code na Aba 1
        resumo_rapido = self.query_one("#resumo-tokens-rapido", Static)
        resumo_rapido.update(
            f"Harness/Modelo: {self.harness_alvo.upper()} ({self.modelo_harness_ativo})\n"
            f"• Último Turno -> Prompt EN: {self._ultimo_tokens_prompt_en} tok | Resposta: {self._ultimo_tokens_resposta} tok\n"
            f"• Tokens Poupados: +{self._ultimo_tokens_economizados} tok (Sessão: +{self._total_tokens_economizados_sessao} tok)\n"
            f"• Acumulado no Modelo: {self._total_tokens_modelo_atual} tok (${stats.session_cost_usd:.4f})"
        )

        barra = self.query_one("#barra-tokens", ProgressBar)
        tokens_barra = min(
            self._total_tokens_modelo_atual + self.sanitizador.context_tokens_used,
            config.TOKEN_WINDOW_MAX,
        )
        barra.update(progress=tokens_barra)

        ctx_pct = (tokens_barra / config.TOKEN_WINDOW_MAX) * 100.0
        avisos = []
        if hw.ram_warning:
            avisos.append(f"RAM ELEVADA ({hw.ram_percent:.0f}%)")
        if hw.vram_warning:
            avisos.append(f"VRAM ELEVADA ({hw.vram_percent:.0f}%)")
        if ctx_pct >= config.TOKEN_WINDOW_WARNING_PCT:
            avisos.append(f"JANELA >85% ({ctx_pct:.0f}%) - Pressione F5")

        alerta_widget = self.query_one("#banner-alerta", Static)
        if avisos:
            alerta_widget.update("AVISO: " + " | ".join(avisos))
        else:
            alerta_widget.update(
                f"Janela de Contexto: {ctx_pct:.1f}% ({tokens_barra}/{config.TOKEN_WINDOW_MAX} tok) | Dia: ${stats.daily_cost_usd:.4f}"
            )

        # Aba 3: Detalhes
        try:
            fin_widget = self.query_one("#resumo-financeiro-detalhado", Static)
            fin_widget.update(
                f"Consumo da Sessão: {stats.session_tokens} tokens enviados | {self._total_tokens_economizados_sessao} tokens poupados "
                f"(${stats.session_cost_usd:.4f} em {stats.session_prompts} prompts)\n"
                f"Custo Estimado Hoje: ${stats.daily_cost_usd:.4f} | Custo Este Mês: ${stats.monthly_cost_usd:.4f}"
            )
            hw_widget = self.query_one("#hardware-detalhado", Static)
            vram_detalhe = (
                f"{hw.vram_used_gb:.2f} GB / {hw.vram_total_gb:.2f} GB ({hw.vram_percent}%)"
                if hw.gpu_available
                else "N/A (GPU dedicada não detectada)"
            )
            hw_widget.update(
                f"CPU: {hw.cpu_percent}% | RAM: {hw.ram_used_gb:.2f} GB / {hw.ram_total_gb:.2f} GB ({hw.ram_percent}%)\n"
                f"GPU VRAM: {vram_detalhe}"
            )
        except Exception:
            pass

    @work(thread=True)
    def trabalhador_atualizar_harnesses(self) -> None:
        status_agy = self.gerenciador_modelos.inspect_antigravity_harness()
        self.call_from_thread(self._aplicar_status_harness, status_agy)

    def _aplicar_status_harness(self, status: HarnessStatus) -> None:
        self._status_antigravity = status
        widget_info = self.query_one("#status-harness-info", Static)
        if status.installed:
            txt = (
                f"• {status.name}: [ INSTALADO E PRONTO ]\n"
                f"  Binário: {status.binary_path}\n"
                f"  Modelos disponíveis no agy: {len(status.models_available)} modelos identificados."
            )
        else:
            txt = (
                f"• {status.name}: [ NÃO DETECTADO NO PATH ]\n"
                f"  Instale o Antigravity ou adicione ao PATH para habilitar o dispatch direto."
            )
        widget_info.update(txt)

        if self.harness_alvo == "agy":
            opcoes = [(desc, m_id) for m_id, desc in status.models_available]
            if opcoes:
                sel = self.query_one("#select-modelo-harness", Select)
                sel.set_options(opcoes)

    @work(thread=True)
    def trabalhador_atualizar_modelos(self) -> None:
        online, instalados = self.gerenciador_modelos.list_installed_models()
        catalogo = self.gerenciador_modelos.get_catalog_with_status()
        self.call_from_thread(self._aplicar_lista_modelos, online, instalados, catalogo)

    def _aplicar_lista_modelos(self, online: bool, instalados: list, catalogo: list[CatalogModelInfo]) -> None:
        self.ollama_online = online
        caixa_inst = self.query_one("#caixa-modelos-instalados", Static)
        if not online:
            caixa_inst.update(
                "Ollama Local: [ OFFLINE ] (porta 11434 inativa).\n"
                "-> O Sanitizador está usando automaticamente o Compressor Híbrido PT->EN embutido!\n"
                "-> Para usar SLMs neurais locais (ex: qwen2.5-coder:1.5b), instale/inicie o Ollama (`ollama serve`)."
            )
        elif not instalados:
            caixa_inst.update("Ollama Local: [ ONLINE ]\nNenhum modelo baixado ainda. Use o catálogo ao lado para instalar.")
        else:
            linhas = [f"Ollama Local: [ ONLINE ] ({len(instalados)} modelos instalados):"]
            for m in instalados:
                linhas.append(f"  • {m.name} — {m.size_gb:.1f} GB ({m.parameter_size} {m.quantization})")
            caixa_inst.update("\n".join(linhas))

        opcoes_slm = (
            [(m.display_label, m.name) for m in instalados]
            if instalados
            else [(m, m) for m in config.DEFAULT_SLM_MODELS]
        )
        sel_slm = self.query_one("#select-slm-sanitizador", Select)
        sel_slm.set_options(opcoes_slm)

        caixa_cat = self.query_one("#caixa-catalogo", Static)
        linhas_cat = ["Catálogo de Modelos SLM Recomendados:"]
        for c in catalogo:
            status_tag = "[ INSTALADO ]" if c.is_installed else "[ DISPONÍVEL ]"
            linhas_cat.append(f"  • {c.name} ({c.size}) {status_tag}\n    {c.description}")
        caixa_cat.update("\n".join(linhas_cat))

        self._atualizar_texto_cabecalho()

    @work(thread=True)
    def trabalhador_atualizar_infra(self) -> None:
        docker_ok, docker_status, containers = self.observador_infra.get_docker_containers()
        k8s_ok, k8s_status, pods = self.observador_infra.get_k8s_pods()
        self.call_from_thread(self._aplicar_status_infra, docker_ok, docker_status, containers, k8s_ok, k8s_status, pods)

    def _aplicar_status_infra(
        self, docker_ok: bool, docker_status: str, containers: list, k8s_ok: bool, k8s_status: str, pods: list
    ) -> None:
        docker_box = self.query_one("#caixa-docker", Static)
        if not docker_ok:
            docker_box.update("Docker: [ Offline ]")
        elif not containers:
            docker_box.update(f"Docker: [{docker_status}] - 0 contêineres.")
        else:
            linhas = [f"Docker: [{docker_status}]"]
            for c in containers[:3]:
                linhas.append(f"  • {c.short_id[:8]} | {c.name} [{c.status}]")
            docker_box.update("\n".join(linhas))

        k8s_box = self.query_one("#caixa-k8s", Static)
        if not k8s_ok:
            k8s_box.update("Kubernetes: [ Offline ]")
        elif not pods:
            k8s_box.update(f"Kubernetes: [{k8s_status}] - 0 pods.")
        else:
            linhas = [f"Kubernetes: [{k8s_status}]"]
            for p in pods[:3]:
                linhas.append(f"  • {p.name} -> {p.phase}")
            k8s_box.update("\n".join(linhas))

    # ── EVENTOS E AÇÕES DA INTERFACE ─────────────────────────────────────────

    @on(Select.Changed, "#select-harness-alvo")
    def on_harness_alvo_changed(self, event: Select.Changed) -> None:
        if event.value and event.value != Select.BLANK:
            self.harness_alvo = str(event.value)
            sel_mod = self.query_one("#select-modelo-harness", Select)
            if self.harness_alvo == "agy" and self._status_antigravity:
                opcoes = [(desc, m_id) for m_id, desc in self._status_antigravity.models_available]
                sel_mod.set_options(opcoes)
            elif self.harness_alvo == "ollama":
                _, inst = self.gerenciador_modelos.list_installed_models()
                opcoes = (
                    [(m.name, m.name) for m in inst]
                    if inst
                    else [(m, m) for m in config.DEFAULT_SLM_MODELS]
                )
                sel_mod.set_options(opcoes)
            elif self.harness_alvo == "gemini-api":
                sel_mod.set_options([("Gemini 2.5 Flash", "gemini-2.5-flash"), ("Gemini 2.5 Pro", "gemini-2.5-pro")])
            self.atualizar_telemetria_ui()

    @on(Select.Changed, "#select-modelo-harness")
    def on_modelo_harness_changed(self, event: Select.Changed) -> None:
        if event.value and event.value != Select.BLANK:
            self.modelo_harness_ativo = str(event.value)
            self.atualizar_telemetria_ui()

    @on(Select.Changed, "#select-slm-sanitizador")
    def on_slm_sanitizador_changed(self, event: Select.Changed) -> None:
        if event.value and event.value != Select.BLANK:
            self.slm_ativo = str(event.value)
            self.gerenciador_modelos.active_model = self.slm_ativo
            self._atualizar_texto_cabecalho()

    @on(Button.Pressed, "#btn-otimizar")
    def on_btn_otimizar_pressed(self) -> None:
        self.action_otimizar_prompt()

    @on(Button.Pressed, "#btn-limpar-entrada")
    def on_btn_limpar_entrada_pressed(self) -> None:
        self.query_one("#input-pt", TextArea).clear()

    @on(Button.Pressed, "#btn-aprovar")
    def on_btn_aprovar_pressed(self) -> None:
        self.action_aprovar_prompt()

    @on(Button.Pressed, "#btn-editar")
    def on_btn_editar_pressed(self) -> None:
        self.action_alternar_edicao_preview()

    @on(Button.Pressed, "#btn-descartar")
    def on_btn_descartar_pressed(self) -> None:
        self.action_descartar_prompt()

    @on(Button.Pressed, "#btn-copiar")
    def on_btn_copiar_pressed(self) -> None:
        txt = self.query_one("#output-harness", TextArea).text
        self.copy_to_clipboard(txt)
        self.notify("Resposta copiada para a área de transferência.", title="Copiado")

    @on(Button.Pressed, "#btn-descarregar-vram-tab2")
    def on_btn_descarregar_tab2_pressed(self) -> None:
        self.action_descarregar_slm()

    @on(Button.Pressed, "#btn-limpar-ctx-tab2")
    def on_btn_limpar_ctx_tab2_pressed(self) -> None:
        self.action_limpar_contexto()

    @on(Button.Pressed, "#btn-instalar-modelo")
    def on_btn_instalar_modelo_pressed(self) -> None:
        modelo_escolhido = str(self.query_one("#select-modelo-para-instalar", Select).value)
        self.trabalhador_instalar_modelo(modelo_escolhido)

    @work(thread=True)
    def trabalhador_instalar_modelo(self, model_name: str) -> None:
        self.call_from_thread(
            self._atualizar_log_instalacao, f"Iniciando download de '{model_name}' via Ollama..."
        )
        for msg in self.gerenciador_modelos.pull_model_stream(model_name):
            self.call_from_thread(self._atualizar_log_instalacao, msg)
        self.call_from_thread(self._atualizar_log_instalacao, f"Processo finalizado para '{model_name}'.")
        self.trabalhador_atualizar_modelos()

    def _atualizar_log_instalacao(self, mensagem: str) -> None:
        self.query_one("#log-instalacao-modelo", Static).update(f"> {mensagem}")

    # ── LÓGICA DO INTERCEPTADOR ──────────────────────────────────────────────

    def action_otimizar_prompt(self) -> None:
        texto_pt = self.query_one("#input-pt", TextArea).text.strip()
        if not texto_pt:
            self.notify("Digite um prompt em português primeiro.", severity="warning")
            return
        self.query_one("#banner-economia", Static).update(
            f"Traduzindo e comprimindo prompt para inglês técnico..."
        )
        self.trabalhador_sanitizacao(texto_pt, self.slm_ativo)

    @work(thread=True)
    def trabalhador_sanitizacao(self, texto_pt: str, modelo: str) -> None:
        resultado = self.sanitizador.sanitize(texto_pt, model=modelo)
        self.call_from_thread(self._aplicar_resultado_sanitizacao, resultado)

    def _aplicar_resultado_sanitizacao(self, res: SanitizeResult) -> None:
        self._ultimo_resultado_sanitizacao = res
        preview = self.query_one("#preview-en", TextArea)
        preview.load_text(res.sanitized_en)
        preview.read_only = True
        self.modo_edicao_ativo = False

        poupados = max(0, res.pt_tokens_est - res.en_tokens_est)
        self._ultimo_tokens_prompt_en = res.en_tokens_est
        self._ultimo_tokens_economizados = poupados

        self.query_one("#banner-economia", Static).update(
            f"[{res.model_used}] PT: {res.pt_chars} chars (~{res.pt_tokens_est} tok) -> "
            f"EN: {res.en_chars} chars (~{res.en_tokens_est} tok) | "
            f"Economia: {res.token_savings_pct:+.1f}% ({poupados} tok poupados)"
        )

        self.query_one("#btn-aprovar", Button).disabled = False
        self.query_one("#btn-editar", Button).disabled = False
        self.query_one("#btn-descartar", Button).disabled = False
        self.atualizar_telemetria_ui()

    def action_alternar_edicao_preview(self) -> None:
        if self.query_one("#btn-editar", Button).disabled:
            return
        preview = self.query_one("#preview-en", TextArea)
        self.modo_edicao_ativo = not self.modo_edicao_ativo
        preview.read_only = not self.modo_edicao_ativo
        btn = self.query_one("#btn-editar", Button)
        if self.modo_edicao_ativo:
            btn.label = "Travar Edição"
            preview.focus()
            self.notify("Modo de edição rápida ativado.")
        else:
            btn.label = "Editar"

    def action_descartar_prompt(self) -> None:
        if self.query_one("#btn-descartar", Button).disabled:
            return
        self._ultimo_resultado_sanitizacao = None
        preview = self.query_one("#preview-en", TextArea)
        preview.clear()
        preview.read_only = True
        self.modo_edicao_ativo = False
        self.query_one("#btn-editar", Button).label = "Editar"
        self.query_one("#banner-economia", Static).update("Prompt descartado. Pronto para novo input.")
        self.query_one("#btn-aprovar", Button).disabled = True
        self.query_one("#btn-editar", Button).disabled = True
        self.query_one("#btn-descartar", Button).disabled = True

    def action_aprovar_prompt(self) -> None:
        if self.query_one("#btn-aprovar", Button).disabled:
            return
        texto_aprovado = self.query_one("#preview-en", TextArea).text.strip()
        if not texto_aprovado:
            self.notify("O prompt sanitizado está vazio.", severity="warning")
            return

        pt_chars = (
            self._ultimo_resultado_sanitizacao.pt_chars
            if self._ultimo_resultado_sanitizacao
            else len(self.query_one("#input-pt", TextArea).text)
        )
        pt_tokens = (
            self._ultimo_resultado_sanitizacao.pt_tokens_est
            if self._ultimo_resultado_sanitizacao
            else self.sanitizador.estimate_tokens(self.query_one("#input-pt", TextArea).text)
        )
        en_tokens = self.sanitizador.estimate_tokens(texto_aprovado)
        poupados = max(0, pt_tokens - en_tokens)

        self._ultimo_tokens_prompt_en = en_tokens
        self._ultimo_tokens_economizados = poupados
        self._total_tokens_economizados_sessao += poupados
        self._total_tokens_modelo_atual += en_tokens

        custo = self.telemetria.record_prompt(
            pt_chars=pt_chars,
            en_tokens_est=en_tokens,
            cloud_model=f"{self.harness_alvo}:{self.modelo_harness_ativo}",
        )
        self._atualizar_tabela_historico()
        self.atualizar_telemetria_ui()

        self.query_one("#output-harness", TextArea).load_text(
            f"Enviando {en_tokens} tokens para [{self.harness_alvo.upper()}] "
            f"modelo '{self.modelo_harness_ativo}' (custo est. ${custo:.6f})...\nAguarde a resposta do harness."
        )

        self.trabalhador_despacho(texto_aprovado, self.harness_alvo, self.modelo_harness_ativo)

    def _atualizar_tabela_historico(self) -> None:
        try:
            tabela = self.query_one("#tabela-historico-ledger", DataTable)
            tabela.clear()
            registros = self.telemetria.get_recent_records(limit=10)
            for r in registros:
                tabela.add_row(
                    str(r.id),
                    r.timestamp,
                    str(r.pt_chars),
                    str(r.en_tokens_est),
                    r.cloud_model,
                    f"${r.cost_estimate:.6f}",
                )
        except Exception:
            pass

    @work(thread=True)
    def trabalhador_despacho(self, prompt: str, harness: str, modelo: str) -> None:
        resp = self.despachador.dispatch(prompt, harness_type=harness, model_name=modelo)
        self.call_from_thread(self._aplicar_resposta_harness, resp)

    def _aplicar_resposta_harness(self, resp) -> None:
        tokens_resp = self.sanitizador.estimate_tokens(resp.output)
        self._ultimo_tokens_resposta = tokens_resp
        self._total_tokens_modelo_atual += tokens_resp
        self.atualizar_telemetria_ui()

        cabecalho = (
            f"Alvo: {resp.target} | Status: {'SUCESSO' if resp.success else 'ERRO'} | "
            f"Tokens Resp: ~{tokens_resp} tok | Latência: {resp.latency_ms} ms\n"
            + ("═" * 68)
            + "\n"
        )
        self.query_one("#output-harness", TextArea).load_text(cabecalho + resp.output)

    # ── TERMINAL INTERATIVO DIRETO NA JANELA ─────────────────────────────────

    def _submeter_comando_terminal_direto(self, cmd: str) -> None:
        self.trabalhador_executar_comando_terminal(cmd)

    def _interromper_terminal_direto(self) -> None:
        ok = self.sessao_terminal.send_interrupt()
        term = self.query_one("#janela-terminal-direta", TerminalLinuxDireto)
        term.anexar_resposta("^C")
        self.notify(
            "Sinal SIGINT (Ctrl+C) enviado ao processo no PTY."
            if ok
            else "Prompt reiniciado (^C)."
        )

    @work(thread=True)
    def trabalhador_executar_comando_terminal(self, cmd: str) -> None:
        _, saida = self.sessao_terminal.execute_command(cmd)
        self.call_from_thread(self._aplicar_saida_terminal, saida)

    def _aplicar_saida_terminal(self, saida: str) -> None:
        term = self.query_one("#janela-terminal-direta", TerminalLinuxDireto)
        term.anexar_resposta(saida)

    def on_unmount(self) -> None:
        """Encerra a sessão PTY nativa ao fechar o aplicativo."""
        self.sessao_terminal.close()

    # ── ATALHOS GLOBAIS ──────────────────────────────────────────────────────

    def action_descarregar_slm(self) -> None:
        ok, msg = self.gerenciador_modelos.unload_model(self.slm_ativo)
        self.notify(msg, severity="information" if ok else "warning")
        self.atualizar_telemetria_ui()

    def action_limpar_contexto(self) -> None:
        self.sanitizador.clear_context()
        self._total_tokens_modelo_atual = 0
        self._ultimo_tokens_prompt_en = 0
        self._ultimo_tokens_resposta = 0
        self.atualizar_telemetria_ui()
        self.notify("Buffer de contexto e contador de tokens zerados (F5).")

    def action_sincronizar_tudo(self) -> None:
        self.sincronizar_tudo()
        self.notify("Sincronização de infraestrutura e modelos concluída.")


if __name__ == "__main__":
    app = RetroHarnessInterceptorApp()
    app.run()
