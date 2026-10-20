"""Gerenciador de sessão de Terminal Linux persistente (PTY) acoplado ao núcleo nativo em C."""

from __future__ import annotations

import ctypes
import getpass
import os
from pathlib import Path
import socket
import subprocess
import time
from typing import Optional, Tuple
import uuid

import config


class NativePtyBridge:
    """Ponte ctypes segura para a biblioteca compartilhada C `native/libpty_core.so`."""

    _instance: Optional["NativePtyBridge"] = None

    def __init__(self) -> None:
        self.lib: Optional[ctypes.CDLL] = None
        self._load_or_compile()

    @classmethod
    def get(cls) -> "NativePtyBridge":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _load_or_compile(self) -> None:
        so_path = config.BASE_DIR / "native" / "libpty_core.so"
        c_path = config.BASE_DIR / "native" / "pty_core.c"

        if not so_path.exists() and c_path.exists():
            try:
                subprocess.run(
                    [
                        "gcc",
                        "-O3",
                        "-shared",
                        "-fPIC",
                        "-Wall",
                        "-Wextra",
                        str(c_path),
                        "-o",
                        str(so_path),
                        "-lutil",
                    ],
                    check=True,
                    capture_output=True,
                    timeout=10.0,
                )
            except Exception:
                pass

        if so_path.exists():
            try:
                lib = ctypes.CDLL(str(so_path))

                # int pty_spawn_shell(const char *, const char *, int, int, int *, int *)
                lib.pty_spawn_shell.argtypes = [
                    ctypes.c_char_p,
                    ctypes.c_char_p,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.POINTER(ctypes.c_int),
                    ctypes.POINTER(ctypes.c_int),
                ]
                lib.pty_spawn_shell.restype = ctypes.c_int

                # ssize_t pty_write_data(int, const char *, size_t)
                lib.pty_write_data.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_size_t]
                lib.pty_write_data.restype = ctypes.c_ssize_t

                # ssize_t pty_read_nonblocking(int, char *, size_t, int)
                lib.pty_read_nonblocking.argtypes = [
                    ctypes.c_int,
                    ctypes.c_char_p,
                    ctypes.c_size_t,
                    ctypes.c_int,
                ]
                lib.pty_read_nonblocking.restype = ctypes.c_ssize_t

                # size_t pty_strip_ansi(const char *, size_t, char *, size_t)
                lib.pty_strip_ansi.argtypes = [
                    ctypes.c_char_p,
                    ctypes.c_size_t,
                    ctypes.c_char_p,
                    ctypes.c_size_t,
                ]
                lib.pty_strip_ansi.restype = ctypes.c_size_t

                # int pty_get_cwd(int, char *, size_t)
                lib.pty_get_cwd.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_size_t]
                lib.pty_get_cwd.restype = ctypes.c_int

                # int pty_send_signal(int, int)
                lib.pty_send_signal.argtypes = [ctypes.c_int, ctypes.c_int]
                lib.pty_send_signal.restype = ctypes.c_int

                # int pty_close_session(int, int)
                lib.pty_close_session.argtypes = [ctypes.c_int, ctypes.c_int]
                lib.pty_close_session.restype = ctypes.c_int

                # int fast_estimate_tokens(const char *, size_t)
                lib.fast_estimate_tokens.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
                lib.fast_estimate_tokens.restype = ctypes.c_int

                self.lib = lib
            except Exception:
                self.lib = None

    @property
    def available(self) -> bool:
        return self.lib is not None

    def estimate_tokens(self, text: str) -> Optional[int]:
        if not self.lib:
            return None
        encoded = text.encode("utf-8", errors="ignore")
        if not encoded:
            return 0
        return int(self.lib.fast_estimate_tokens(encoded, len(encoded)))

    def strip_ansi(self, raw_bytes: bytes) -> str:
        if not self.lib or not raw_bytes:
            return raw_bytes.decode("utf-8", errors="replace")
        cap = len(raw_bytes) + 16
        out_buf = ctypes.create_string_buffer(cap)
        self.lib.pty_strip_ansi(raw_bytes, len(raw_bytes), out_buf, cap)
        return out_buf.value.decode("utf-8", errors="replace")


class LinuxTerminalSession:
    """Sessão de terminal Linux real e persistente baseada em PTY nativo em C.

    Mantém estado de diretório atual (`cd`), variáveis de ambiente (`export`),
    histórico de sessão e permite envio de sinais POSIX (`SIGINT` / Ctrl+C).
    """

    def __init__(self, initial_cwd: Path | str = config.BASE_DIR) -> None:
        self.bridge = NativePtyBridge.get()
        self.initial_cwd = str(Path(initial_cwd).resolve())
        self.master_fd: int = -1
        self.child_pid: int = -1
        self._fallback_cwd: str = self.initial_cwd
        self._user = getpass.getuser()
        self._host = socket.gethostname()
        self._start_pty()

    def _start_pty(self) -> None:
        if not self.bridge.available or not self.bridge.lib:
            return
        fd = ctypes.c_int(-1)
        pid = ctypes.c_int(-1)
        shell_bin = b"/bin/bash"
        cwd_bytes = self.initial_cwd.encode("utf-8")
        res = self.bridge.lib.pty_spawn_shell(
            shell_bin, cwd_bytes, 30, 120, ctypes.byref(fd), ctypes.byref(pid)
        )
        if res == 0:
            self.master_fd = fd.value
            self.child_pid = pid.value
            # Drena qualquer banner inicial do bash
            self._drain_available(timeout_ms=40)

    def _drain_available(self, timeout_ms: int = 30) -> str:
        if self.master_fd < 0 or not self.bridge.lib:
            return ""
        chunks: list[bytes] = []
        buf_size = 8192
        buf = ctypes.create_string_buffer(buf_size)
        while True:
            n = self.bridge.lib.pty_read_nonblocking(self.master_fd, buf, buf_size, timeout_ms)
            if n <= 0:
                break
            chunks.append(buf.raw[:n])
            timeout_ms = 10
        if not chunks:
            return ""
        return self.bridge.strip_ansi(b"".join(chunks))

    def get_cwd(self) -> str:
        """Retorna o diretório de trabalho atual real do processo shell via /proc/<pid>/cwd."""
        if self.child_pid > 0 and self.bridge.lib:
            buf = ctypes.create_string_buffer(4096)
            if self.bridge.lib.pty_get_cwd(self.child_pid, buf, 4096) == 0:
                path_str = buf.value.decode("utf-8", errors="replace")
                if path_str:
                    return path_str
        return self._fallback_cwd

    def get_prompt_prefix(self) -> str:
        """Formata o prompt clássico estilo Linux: usuario@host:~/caminho$"""
        cwd = self.get_cwd()
        home = str(Path.home())
        if cwd.startswith(home):
            cwd_display = "~" + cwd[len(home) :]
        else:
            cwd_display = cwd
        return f"{self._user}@{self._host}:{cwd_display}$"

    def execute_command(self, command: str, timeout_sec: float = 20.0) -> Tuple[str, str]:
        """Executa um comando no PTY persistente e retorna (prompt_usado, saida_limpa)."""
        cmd_clean = command.strip()
        prompt_before = self.get_prompt_prefix()
        if not cmd_clean:
            return prompt_before, ""

        if self.master_fd < 0 or not self.bridge.lib:
            return prompt_before, self._execute_fallback(cmd_clean, timeout_sec)

        # Usa marcador único para detectar término exato do comando no PTY persistente
        marker = f"__END_CMD_{uuid.uuid4().hex[:10]}__"
        payload = f"{cmd_clean}\necho {marker}\n".encode("utf-8")
        written = self.bridge.lib.pty_write_data(self.master_fd, payload, len(payload))
        if written < 0:
            # Reinicia PTY caso tenha sido fechado (ex: usuário digitou exit)
            self._start_pty()
            return prompt_before, "Sessão de shell reiniciada."

        collected: list[bytes] = []
        buf_size = 8192
        buf = ctypes.create_string_buffer(buf_size)
        deadline = time.monotonic() + timeout_sec

        while time.monotonic() < deadline:
            n = self.bridge.lib.pty_read_nonblocking(self.master_fd, buf, buf_size, 50)
            if n > 0:
                collected.append(buf.raw[:n])
                joined = b"".join(collected)
                if marker.encode("utf-8") in joined:
                    break
            elif n < 0:
                # Shell encerrado (ex: comando exit)
                self._start_pty()
                break

        raw_output = b"".join(collected)
        clean_text = self.bridge.strip_ansi(raw_output)

        # Remove o marcador final e linhas de eco da estrutura interna
        output_lines: list[str] = []
        for line in clean_text.splitlines():
            if marker in line:
                continue
            if line.strip().startswith("bash-") or line.strip().startswith("sh-"):
                continue
            output_lines.append(line)

        return prompt_before, "\n".join(output_lines).strip()

    def _execute_fallback(self, cmd_clean: str, timeout_sec: float) -> str:
        """Fallback seguro caso a biblioteca C não esteja carregada."""
        if cmd_clean.startswith("cd "):
            target = os.path.expanduser(cmd_clean[3:].strip())
            new_path = (Path(self._fallback_cwd) / target).resolve()
            if new_path.is_dir():
                self._fallback_cwd = str(new_path)
                return ""
            return f"cd: diretório não encontrado: {target}"
        try:
            proc = subprocess.run(
                cmd_clean,
                cwd=self._fallback_cwd,
                capture_output=True,
                text=True,
                shell=True,
                timeout=timeout_sec,
                check=False,
            )
            return (proc.stdout or proc.stderr or "").strip()
        except Exception as exc:
            return f"Erro ao executar comando: {exc}"

    def send_interrupt(self) -> bool:
        """Envia SIGINT (Ctrl+C) ao processo em execução no PTY."""
        if self.child_pid > 0 and self.bridge.lib:
            return self.bridge.lib.pty_send_signal(self.child_pid, 2) == 0
        return False

    def close(self) -> None:
        """Encerra limpa e definitivamente o processo de shell e o descritor PTY."""
        if self.master_fd >= 0 and self.bridge.lib:
            self.bridge.lib.pty_close_session(self.master_fd, self.child_pid)
            self.master_fd = -1
            self.child_pid = -1
