"""Interface TUI Retrô para Interceptação de Prompts, Otimização de Tokens e Middleware de Harnesses."""

from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess
from typing import Optional

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Input,
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


class RetroHarnessInterceptorApp(App):
    """Aplicativo TUI estilo Terminal Retrô para Interceptação e Otimização de Prompts."""

    TITLE = "INTERCEPTADOR DE PROMPTS & HARNESS MIDDLEWARE"
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

    def compose(self) -> ComposeResult:
        # Barra Superior de Status do Terminal Retrô
        yield Static(
            "[ INICIALIZANDO TERMINAL... ] Verificando Ollama, Antigravity e Hardware...",
            id="top-status-bar",
        )

        with TabbedContent(initial="aba-prompt"):
            # ─────────────────────────────────────────────────────────────────
            # ABA 1: INTERCEPTADOR & FLUXO DE PROMPTS
            # ─────────────────────────────────────────────────────────────────
            with TabPane("1. Interceptador & Prompt", id="aba-prompt"):
                with Horizontal(classes="linha-dupla"):
                    # Coluna Esquerda: Entrada, Revisão e Saída
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
                                yield Button("Aprovar & Enviar [y]", id="btn-aprovar", variant="success", disabled=True)
                                yield Button("Editar [e]", id="btn-editar", disabled=True)
                                yield Button("Descartar [n]", id="btn-descartar", variant="error", disabled=True)

                        with Vertical(classes="painel-retro", id="painel-saida"):
                            yield Label("└── RESPOSTA DO HARNESS / MODELO ──────────────┘", classes="titulo-painel")
                            yield TextArea(
                                "Pronto. Ao aprovar um prompt sanitizado, a resposta do harness aparecerá aqui.",
                                id="output-harness",
                                read_only=True,
                            )
                            with Horizontal(classes="linha-botoes"):
                                yield Button("Copiar Resposta", id="btn-copiar")

                    # Coluna Direita: Seleção de Destino, Infra e Telemetria
                    with Vertical(classes="coluna-dir"):
                        with Vertical(classes="painel-retro"):
                            yield Label("┌── DESTINO & MODELOS ─────────────────────────┐", classes="titulo-painel")
                            yield Label("Harness Alvo para Execução:")
                            yield Select(
                                [(rotulo, id_h) for id_h, rotulo in config.HARNESS_TARGETS],
                                value=config.DEFAULT_HARNESS_TARGET,
                                id="select-harness-alvo",
                                allow_blank=False,
                            )
                            yield Label("Modelo do Harness Selecionado:")
                            yield Select(
                                [(desc, m_id) for m_id, desc in config.DEFAULT_AGY_MODELS],
                                value="gemini-3.8-flash-medium",
                                id="select-modelo-harness",
                                allow_blank=False,
                            )
                            yield Label("Modelo Local Sanitizador (SLM):")
                            yield Select(
                                [(m, m) for m in config.DEFAULT_SLM_MODELS],
                                value=config.DEFAULT_ACTIVE_SLM,
                                id="select-slm-sanitizador",
                                allow_blank=False,
                            )

                        with Vertical(classes="painel-retro"):
                            yield Label("├── INFRAESTRUTURA LOCAL (F9) ─────────────────┤", classes="titulo-painel")
                            yield Static("Docker: Verificando...", id="caixa-docker")
                            yield Static("Kubernetes: Verificando...", id="caixa-k8s")

                        with Vertical(classes="painel-retro"):
                            yield Label("└── TELEMETRIA DE TOKENS & ALERTA ─────────────┘", classes="titulo-painel")
                            yield Static("Sessão: $0.0000 | Tokens: 0", id="resumo-tokens-rapido")
                            with Horizontal(id="linha-gauge"):
                                yield Label("Janela de Contexto: ")
                                yield ProgressBar(total=config.TOKEN_WINDOW_MAX, show_eta=False, id="barra-tokens")
                            yield Static("Status do sistema: Normal.", id="banner-alerta")

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
                            yield Label("┌── MODELOS INSTALADOS NO COMPUTADOR ──────────┐", classes="titulo-painel")
                            yield Static("Consultando Ollama local...", id="caixa-modelos-instalados")
                            with Horizontal(classes="linha-botoes"):
                                yield Button("Descarregar VRAM (F2)", id="btn-descarregar-vram-tab2")
                                yield Button("Limpar Buffer de Contexto (F5)", id="btn-limpar-ctx-tab2")

                    with Vertical(classes="coluna-dir"):
                        with Vertical(classes="painel-retro"):
                            yield Label("┌── INSTALAR NOVOS MODELOS SLM ───────────────┐", classes="titulo-painel")
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
            # ABA 4: TERMINAL DO COMPUTADOR (PTY LINUX PERSISTENTE EM C)
            # ─────────────────────────────────────────────────────────────────
            with TabPane("4. Terminal do PC", id="aba-terminal"):
                with Vertical(classes="painel-retro"):
                    yield Label(
                        "┌── TERMINAL LINUX PERSISTENTE (PTY NATIVO EM C) ──────────┐",
                        classes="titulo-painel",
                    )
                    yield Static(
                        f"Sessão PTY ativa (PID: {self.sessao_terminal.child_pid}). "
                        f"Diretório e variáveis persistem entre comandos (cd, export, alias, clear).\n"
                        f"{self.sessao_terminal.get_prompt_prefix()} ",
                        id="console-terminal",
                    )
                    with Horizontal(classes="linha-botoes"):
                        yield Button("Enviar SIGINT / Ctrl+C", id="btn-int-terminal", variant="error")
                        yield Button("Limpar Tela (clear)", id="btn-clear-terminal")
                    yield Input(
                        placeholder="Digite qualquer comando Linux (ex: cd .., ls -la, export, agy models) e Enter...",
                        id="input-terminal",
                    )

        yield Footer()

    def on_mount(self) -> None:
        """Inicializa leituras em segundo plano e configura a tabela."""
        # Configurar colunas da tabela de histórico
        tabela = self.query_one("#tabela-historico-ledger", DataTable)
        tabela.add_columns("ID", "Data/Hora", "Chars PT", "Tokens EN", "Harness/Modelo", "Custo Est. ($)")

        self.sincronizar_tudo()
        self.set_interval(config.TELEMETRY_POLL_INTERVAL_SEC, self.atualizar_telemetria_timer)

    def sincronizar_tudo(self) -> None:
        """Dispara atualização de modelos, harnesses, infra e telemetria."""
        self.trabalhador_atualizar_harnesses()
        self.trabalhador_atualizar_modelos()
        self.trabalhador_atualizar_infra()
        self.atualizar_telemetria_ui()

    def _atualizar_texto_cabecalho(self) -> None:
        cabecalho = self.query_one("#top-status-bar", Static)
        status_ollama = "Ativo" if self.ollama_online else "Offline"

        hw = self._ultimas_metricas_hw
        if hw:
            vram_txt = f"{hw.vram_used_gb:.1f}/{hw.vram_total_gb:.1f} GB" if hw.gpu_available else "N/A (CPU)"
            ram_txt = f"{hw.ram_percent:.0f}% ({hw.ram_used_gb:.1f}/{hw.ram_total_gb:.1f} GB)"
            cpu_txt = f"{hw.cpu_percent:.0f}%"
        else:
            vram_txt = "0.0/0.0 GB"
            ram_txt = "0%"
            cpu_txt = "0%"

        stats = self._ultimas_estatisticas_ledger
        custo_sessao = stats.session_cost_usd if stats else 0.0

        cabecalho.update(
            f"[ SISTEMA ] Ollama: [{status_ollama}] | SLM: {self.slm_ativo} | "
            f"Alvo: {self.harness_alvo.upper()} ({self.modelo_harness_ativo}) | "
            f"CPU: {cpu_txt} | RAM: {ram_txt} | VRAM: {vram_txt} | Sessão: ${custo_sessao:.4f}"
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

        # Resumo rápido da Aba 1
        resumo_rapido = self.query_one("#resumo-tokens-rapido", Static)
        resumo_rapido.update(
            f"Sessão: {stats.session_tokens} tokens (${stats.session_cost_usd:.4f}) | "
            f"Dia: ${stats.daily_cost_usd:.4f} | Mês: ${stats.monthly_cost_usd:.4f}"
        )

        # Barra de Contexto
        barra = self.query_one("#barra-tokens", ProgressBar)
        ctx_tokens = min(self.sanitizador.context_tokens_used, config.TOKEN_WINDOW_MAX)
        barra.update(progress=ctx_tokens)

        ctx_pct = (self.sanitizador.context_tokens_used / config.TOKEN_WINDOW_MAX) * 100.0
        avisos = []
        if hw.ram_warning:
            avisos.append(f"RAM ELEVADA ({hw.ram_percent:.0f}%)")
        if hw.vram_warning:
            avisos.append(f"VRAM ELEVADA ({hw.vram_percent:.0f}%)")
        if ctx_pct >= config.TOKEN_WINDOW_WARNING_PCT:
            avisos.append(f"BUFFER DE TOKENS >85% ({ctx_pct:.0f}%) - Pressione F5")

        alerta_widget = self.query_one("#banner-alerta", Static)
        if avisos:
            alerta_widget.update("AVISO: " + " | ".join(avisos))
        else:
            alerta_widget.update(f"Contexto: {ctx_pct:.1f}% ({ctx_tokens}/{config.TOKEN_WINDOW_MAX} tokens).")

        # Aba 3: Detalhes
        try:
            fin_widget = self.query_one("#resumo-financeiro-detalhado", Static)
            fin_widget.update(
                f"Consumo da Sessão: {stats.session_tokens} tokens (${stats.session_cost_usd:.4f} em {stats.session_prompts} prompts)\n"
                f"Custo Estimado Hoje: ${stats.daily_cost_usd:.4f} | Custo Este Mês: ${stats.monthly_cost_usd:.4f}"
            )
            hw_widget = self.query_one("#hardware-detalhado", Static)
            vram_detalhe = (
                f"{hw.vram_used_gb:.2f} GB / {hw.vram_total_gb:.2f} GB ({hw.vram_percent}%)"
                if hw.gpu_available
                else "N/A (GPU não detectada)"
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
                f"• {status.name}: [ INSTALADO ]\n"
                f"  Caminho: {status.binary_path}\n"
                f"  Versão: {status.version_info}\n"
                f"  Modelos disponíveis no agy: {len(status.models_available)} modelos identificados."
            )
        else:
            txt = (
                f"• {status.name}: [ NÃO DETECTADO NO PATH ]\n"
                f"  Instale o Antigravity ou adicione ao PATH para habilitar o dispatch direto."
            )
        widget_info.update(txt)

        # Atualizar opções do dropdown de modelos se o harness ativo for agy
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
            caixa_inst.update("Ollama Local: [ OFFLINE ] (serviço não encontrado em localhost:11434)")
        elif not instalados:
            caixa_inst.update("Ollama Local: [ ONLINE ]\nNenhum modelo baixado ainda. Use a coluna ao lado para instalar.")
        else:
            linhas = [f"Ollama Local: [ ONLINE ] ({len(instalados)} modelos instalados):"]
            for m in instalados:
                linhas.append(f"  • {m.name} — {m.size_gb:.1f} GB ({m.parameter_size} {m.quantization})")
            caixa_inst.update("\n".join(linhas))

        # Atualiza opções do dropdown de sanitizador SLM
        opcoes_slm = (
            [(m.display_label, m.name) for m in instalados]
            if instalados
            else [(m, m) for m in config.DEFAULT_SLM_MODELS]
        )
        sel_slm = self.query_one("#select-slm-sanitizador", Select)
        sel_slm.set_options(opcoes_slm)

        # Atualiza catálogo
        caixa_cat = self.query_one("#caixa-catalogo", Static)
        linhas_cat = ["Catálogo de Modelos Recomendados:"]
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
            docker_box.update("Docker: [ Offline ] (daemon não acessível)")
        elif not containers:
            docker_box.update(f"Docker: [{docker_status}] - 0 contêineres ativos.")
        else:
            linhas = [f"Docker: [{docker_status}]"]
            for c in containers[:4]:
                linhas.append(f"  • {c.short_id[:8]} | {c.name} [{c.status}]")
            docker_box.update("\n".join(linhas))

        k8s_box = self.query_one("#caixa-k8s", Static)
        if not k8s_ok:
            k8s_box.update("Kubernetes: [ Offline ] (cluster não acessível)")
        elif not pods:
            k8s_box.update(f"Kubernetes: [{k8s_status}] - Nenhum pod ativo.")
        else:
            linhas = [f"Kubernetes: [{k8s_status}]"]
            for p in pods[:4]:
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
            self._atualizar_texto_cabecalho()

    @on(Select.Changed, "#select-modelo-harness")
    def on_modelo_harness_changed(self, event: Select.Changed) -> None:
        if event.value and event.value != Select.BLANK:
            self.modelo_harness_ativo = str(event.value)
            self._atualizar_texto_cabecalho()

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
        self.call_from_thread(self._atualizar_log_instalacao, f"Download/instalação de '{model_name}' concluído!")
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
            f"Otimizando e traduzindo via {self.slm_ativo}..."
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

        origem = "Fallback Local" if res.used_fallback else f"Ollama ({self.slm_ativo})"
        self.query_one("#banner-economia", Static).update(
            f"[{origem}] PT: {res.pt_chars} chars (~{res.pt_tokens_est} tok) -> "
            f"EN: {res.en_chars} chars (~{res.en_tokens_est} tok) | "
            f"Economia Estimada: {res.token_savings_pct:+.1f}%"
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
            btn.label = "Travar Edição [e]"
            preview.focus()
            self.notify("Modo de edição rápida ativado.")
        else:
            btn.label = "Editar [e]"

    def action_descartar_prompt(self) -> None:
        if self.query_one("#btn-descartar", Button).disabled:
            return
        self._ultimo_resultado_sanitizacao = None
        preview = self.query_one("#preview-en", TextArea)
        preview.clear()
        preview.read_only = True
        self.modo_edicao_ativo = False
        self.query_one("#btn-editar", Button).label = "Editar [e]"
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
        en_tokens = self.sanitizador.estimate_tokens(texto_aprovado)

        # Gravar no livro-razão SQLite
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
        cabecalho = (
            f"Alvo: {resp.target} | Status: {'SUCESSO' if resp.success else 'ERRO'} | "
            f"Latência: {resp.latency_ms} ms\n"
            + ("═" * 68)
            + "\n"
        )
        self.query_one("#output-harness", TextArea).load_text(cabecalho + resp.output)

    # ── TERMINAL EMBUTIDO (PTY LINUX PERSISTENTE) ────────────────────────────

    @on(Button.Pressed, "#btn-int-terminal")
    def on_btn_int_terminal_pressed(self) -> None:
        ok = self.sessao_terminal.send_interrupt()
        self.notify(
            "Sinal SIGINT (Ctrl+C) enviado ao processo no PTY."
            if ok
            else "Nenhum processo ativo para interromper."
        )

    @on(Button.Pressed, "#btn-clear-terminal")
    def on_btn_clear_terminal_pressed(self) -> None:
        console = self.query_one("#console-terminal", Static)
        console.update(f"{self.sessao_terminal.get_prompt_prefix()} ")

    @on(Input.Submitted, "#input-terminal")
    def on_terminal_comando(self, event: Input.Submitted) -> None:
        comando = event.value.strip()
        if not comando:
            return
        self.query_one("#input-terminal", Input).value = ""
        console = self.query_one("#console-terminal", Static)
        if comando == "clear":
            console.update(f"{self.sessao_terminal.get_prompt_prefix()} ")
            return
        prompt_atual = self.sessao_terminal.get_prompt_prefix()
        console.update(f"{console.renderable}\n{prompt_atual} {comando}\nExecutando...")
        self.trabalhador_executar_comando_terminal(comando)

    @work(thread=True)
    def trabalhador_executar_comando_terminal(self, cmd: str) -> None:
        _, saida = self.sessao_terminal.execute_command(cmd)
        novo_prompt = self.sessao_terminal.get_prompt_prefix()
        self.call_from_thread(self._aplicar_saida_terminal, saida, novo_prompt)

    def _aplicar_saida_terminal(self, saida: str, novo_prompt: str) -> None:
        console = self.query_one("#console-terminal", Static)
        linhas_existentes = str(console.renderable).splitlines()
        if linhas_existentes and linhas_existentes[-1] == "Executando...":
            linhas_existentes.pop()
        if saida:
            linhas_existentes.append(saida)
        linhas_existentes.append(f"{novo_prompt} ")
        texto_final = "\n".join(linhas_existentes[-120:])
        console.update(texto_final)

    def on_unmount(self) -> None:
        """Encerra a sessão PTY nativa ao fechar o aplicativo."""
        self.sessao_terminal.close()

    # ── ATALHOS GLOBAIS ──────────────────────────────────────────────────────

    def action_descarregar_slm(self) -> None:
        """Descarrega o modelo ativo da VRAM (F2)."""
        ok, msg = self.gerenciador_modelos.unload_model(self.slm_ativo)
        self.notify(msg, severity="information" if ok else "warning")
        self.atualizar_telemetria_ui()

    def action_limpar_contexto(self) -> None:
        """Limpa o buffer de contexto do sanitizador (F5)."""
        self.sanitizador.clear_context()
        self.atualizar_telemetria_ui()
        self.notify("Buffer de contexto zerado (modo zero-context ativo).")

    def action_sincronizar_tudo(self) -> None:
        """Sincroniza modelos, infra e métricas (F9)."""
        self.sincronizar_tudo()
        self.notify("Sincronização de infraestrutura e modelos concluída.")


if __name__ == "__main__":
    app = RetroHarnessInterceptorApp()
    app.run()
