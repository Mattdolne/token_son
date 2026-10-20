# Token Son — Interceptador de Prompts, Harness Middleware & Terminal Linux TUI

Um Terminal Interativo de Usuário (**TUI - Terminal User Interface**) híbrido (**Python + C**) projetado como **middleware interceptador e otimizador de prompts** entre o desenvolvedor e ferramentas de execução/harnesses (como o **Google Antigravity `agy`**, modelos locais via **Ollama**, scripts de teste ou APIs em nuvem), além de fornecer um **Terminal Linux real e persistente (PTY)** integrado.

Construído com um visual **retrô clássico de terminal de fósforo âmbar** (estilo VT220 / mainframe), seguindo rigorosamente os princípios **SOLID** e **Clean Architecture** para garantir segurança, modularidade e máxima economia de tokens.

---

## 🎯 Objetivos Principais

1. **Interceptação e Compressão de Prompts (PT-BR -> EN):** Recebe prompts em português, remove saudações/floreios e traduz para inglês técnico denso via SLM local (Ollama) ou motor determinístico offline, preservando 100% de blocos de código, variáveis e flags.
2. **Revisão Humana (Human-in-the-Loop):** Exibe estimativa de tokens economizados (calculada em $O(N)$ pelo núcleo em C) e permite **Aprovar (`y`)**, **Editar (`e`)** ou **Descartar (`n`)** antes do envio.
3. **Despacho Multi-Harness (Padrão Strategy):** Envia o prompt aprovado para o **Antigravity (`agy`)**, **Ollama Local**, **Google Gemini API** ou **Shell Pipe**.
4. **Gerenciamento de Modelos e VRAM:** Detecta o `agy` e seus modelos, lista modelos instalados no Ollama, oferece catálogo guiado para baixar novos modelos (`ollama pull`) e libera VRAM imediatamente (`F2`).
5. **Terminal Linux Real Persistente (PTY Nativo em C):** Emula um terminal Linux verdadeiro via `forkpty()` e `/proc/<pid>/cwd`, preservando mudanças de diretório (`cd`), variáveis de ambiente (`export`), `clear` e envio de sinais POSIX (`SIGINT` / `Ctrl+C`).

---

## 🏛️ Arquitetura do Projeto (SOLID & Híbrida Python + C)

```text
token_son/
├── app.py                  # Ponto de entrada Textual, interface em 4 abas e workers assíncronos
├── config.py               # Endpoints, catálogo de modelos, tabela de custos e limiares
├── Makefile                # Automação de compilação do núcleo C (libpty_core.so), testes e execução
├── native/
│   └── pty_core.c          # Núcleo em C (POSIX PTY forkpty, poll, filtro ANSI O(N), contador de tokens)
├── modules/
│   ├── __init__.py
│   ├── interfaces.py       # Contratos e Protocols SOLID (ISP / DIP)
│   ├── terminal.py         # Ponte ctypes e sessão de Terminal Linux persistente (PTY)
│   ├── sanitizer.py        # Pipeline de tradução técnica, limpeza de floreios e fallback
│   ├── harness.py          # Despachador baseado no Padrão Strategy (OCP / SRP)
│   ├── models.py           # Descoberta de harnesses (agy), modelos Ollama, download e VRAM
│   ├── infra.py            # Inspeção não-bloqueante de Docker e Kubernetes
│   └── telemetry.py        # Coletor de hardware (psutil/nvidia-smi) e livro-razão SQLite
├── styles/
│   └── app.tcss            # Estilização CSS retrô monocromática âmbar (fósforo VT220)
├── tests/
│   └── test_interceptor.py # Suíte de testes unitários, testes do núcleo C PTY e TUI headless
├── requirements.txt        # Dependências do projeto Python
├── README.md               # Documentação principal do repositório
└── gemini.md               # Diretrizes técnicas e regras de manutenção para agentes Gemini
```

---

## 🖥️ As 4 Abas do Aplicativo

1. **`1. Interceptador & Prompt`**: Entrada em português, revisão sanitizada em inglês com economia de tokens, seleção de Harness/Modelo alvo e resposta com cópia rápida para a área de transferência.
2. **`2. Harnesses & Modelos`**: Status do Antigravity (`agy`), lista de modelos instalados no computador, catálogo de modelos recomendados com instalação direta e botão para descarregar VRAM (`F2`).
3. **`3. Consumo de Tokens`**: Resumo financeiro (Sessão, Dia, Mês), tabela histórica de auditoria (`prompt_ledger.db`) e telemetria de CPU, RAM e GPU VRAM.
4. **`4. Terminal do PC`**: Sessão de terminal Linux persistente gerenciada em C (`native/pty_core.c`), exibindo o diretório real (`usuario@host:~/caminho$`), mantendo estado entre comandos (`cd`, `export`) e permitindo interrupção com `SIGINT` (`Ctrl+C`).

---

## ⌨️ Atalhos de Teclado Globais

| Tecla | Ação |
| :--- | :--- |
| **Ctrl + R** | Interceptar e Otimizar o prompt em português atual |
| **y** ou **Enter** | Aprovar prompt sanitizado e despachar para o harness |
| **e** | Alternar modo de edição rápida da versão em inglês |
| **n** | Descartar o prompt sanitizado atual |
| **F2** | Descarregar modelo ativo da memória VRAM da GPU |
| **F5** | Limpar buffer de contexto local do SLM (modo zero-context) |
| **F9** | Forçar sincronização imediata de modelos e infraestrutura |
| **Ctrl + Q** | Sair do aplicativo |

---

## 🚀 Como Compilar, Executar e Testar

```bash
# 1. Compilar a biblioteca nativa em C (também compilada automaticamente no início)
make

# 2. Criar ambiente virtual e instalar dependências (se necessário)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. Iniciar a aplicação TUI
.venv/bin/python app.py

# 4. Executar todos os testes automatizados (Python + C PTY + TUI)
.venv/bin/pytest -v
```
