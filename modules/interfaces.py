"""Contratos e interfaces (Protocols) seguindo os princípios SOLID (ISP e DIP)."""

from __future__ import annotations

from typing import List, Protocol, Tuple, runtime_checkable


@runtime_checkable
class ISanitizer(Protocol):
    """Contrato para serviços de sanitização e compressão de prompts."""

    def sanitize(self, prompt_pt: str, model: str) -> object:
        ...

    def clear_context(self) -> None:
        ...

    @staticmethod
    def estimate_tokens(text: str) -> int:
        ...


@runtime_checkable
class IHarnessStrategy(Protocol):
    """Contrato Strategy (OCP) para envio de payloads a um tipo específico de Harness."""

    def execute(self, prompt: str, model_name: str | None = None, **kwargs: object) -> object:
        ...


@runtime_checkable
class IHarnessDispatcher(Protocol):
    """Contrato para o despachador central de Harnesses."""

    def register_strategy(self, harness_type: str, strategy: IHarnessStrategy) -> None:
        ...

    def dispatch(
        self,
        approved_prompt: str,
        harness_type: str = "agy",
        model_name: str | None = None,
        custom_shell_cmd: str | None = None,
    ) -> object:
        ...


@runtime_checkable
class ITerminalSession(Protocol):
    """Contrato para uma sessão real de terminal Linux (PTY)."""

    def get_cwd(self) -> str:
        ...

    def get_prompt_prefix(self) -> str:
        ...

    def execute_command(self, command: str, timeout_sec: float = 15.0) -> Tuple[str, str]:
        ...

    def send_interrupt(self) -> bool:
        ...

    def close(self) -> None:
        ...
