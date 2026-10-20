# Contexto e Instruções Técnicas para o Gemini (`gemini.md`)

Este repositório contém o **Token Son (Harness Interceptor & Linux Terminal TUI)**, um middleware local de otimização de prompts e emulador de terminal Linux retrô construído em **Python + C** sob princípios **SOLID**.

---

## 📌 Arquitetura e Componentes

1. **Núcleo Nativo em C (`native/pty_core.c` -> `native/libpty_core.so`):**
   - Gerencia sessões reais de Pseudo-Terminal Linux (`forkpty`, `poll`, `readlink("/proc/<pid>/cwd")`, `kill`).
   - Executa limpeza de sequências de escape ANSI/VT100 em máquina de estados $O(N)$ (`pty_strip_ansi`).
   - Calcula estimativa léxica de tokens em passagem única $O(N)$ (`fast_estimate_tokens`).
2. **Contratos SOLID (`modules/interfaces.py`):**
   - Define `ISanitizer`, `IHarnessStrategy`, `IHarnessDispatcher` e `ITerminalSession` garantindo Segregação de Interfaces (ISP) e Inversão de Dependência (DIP).
3. **Sessão de Terminal Persistente (`modules/terminal.py`):**
   - `NativePtyBridge` carrega (e compila via `gcc` caso ausente) a biblioteca C compartilhada.
   - `LinuxTerminalSession` mantém o shell `/bin/bash` vivo em segundo plano, preservando `cd`, `export` e permitindo sinais `SIGINT` (`Ctrl+C`).
4. **Sanitizador (`modules/sanitizer.py`):**
   - Traduz e comprime prompts de PT-BR para EN técnico denso via Ollama (`localhost:11434`) ou **Compressor Híbrido PT->EN** (que traduz sentenças completas e aplica compressão telegráfica em inglês mesmo sem o Ollama instalado), preservando 100% dos blocos de código e flags.
5. **Despachador Strategy (`modules/harness.py`):**
   - Implementa `AntigravityStrategy` (invocando `agy --dangerously-skip-permissions --print` sem `shell=True` para máxima segurança e evitando bloqueio de permissão em modo headless), `OllamaStrategy`, `GeminiApiStrategy` e `ShellPipeStrategy`.
6. **Modelos, Cotas e Telemetria (`modules/models.py`, `modules/quota.py`, `modules/telemetry.py`, `modules/infra.py`):**
   - Inspeciona o binário `agy` e consulta via Connect-RPC (`/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary`) as cotas ativas da conta (`macedodolne@gmail.com`) por família de modelos e janelas temporais (semanal / 5h).
   - Gerencia modelos do Ollama (`pull` e `keep_alive: 0`), monitora Docker/K8s com fallback offline e grava auditoria no SQLite (`prompt_ledger.db`).
   - Garante que o terminal inicie na raiz (`/`) por padrão como um terminal padrão.

---

## 📋 Regras Obrigatórias de Manutenção e Git

1. **Idioma e Estética:** Toda a interface do usuário deve permanecer em **Português do Brasil**, mantendo o tema retrô monocromático de **fósforo azul** (`styles/app.tcss`).
2. **Padrões SOLID e Segurança:**
   - Novas integrações de Harnesses devem implementar `IHarnessStrategy` e ser registradas no `HarnessDispatcher`.
   - Nunca utilize `shell=True` ao passar prompts de usuário para binários externos como `agy`; passe sempre listas de argumentos.
3. **Atualização Documental e Sincronização com GitHub:**
   - **Sempre que houver alterações no código**, atualize todos os arquivos `.md` do projeto (`README.md`, `gemini.md` e o guia local ignorado `GUIA_ARQUITETURA_E_CODIGO.md`).
   - O arquivo `GUIA_ARQUITETURA_E_CODIGO.md` deve permanecer no `.gitignore` (uso exclusivo local do autor).
   - Após validar com `.venv/bin/pytest -v`, faça commit e envie (`git push`) para o repositório oficial:
     `https://github.com/Mattdolne/token_son.git`
