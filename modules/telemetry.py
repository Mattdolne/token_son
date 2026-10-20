"""Monitoramento de hardware do sistema (CPU, RAM, VRAM) e livro-razão de tokens (SQLite)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import subprocess
from typing import List, Tuple
import psutil

import config


@dataclass
class HardwareMetrics:
    """Métricas de CPU, memória RAM e GPU VRAM."""

    cpu_percent: float
    ram_percent: float
    ram_used_gb: float
    ram_total_gb: float
    vram_used_gb: float
    vram_total_gb: float
    gpu_available: bool

    @property
    def vram_percent(self) -> float:
        if self.vram_total_gb <= 0:
            return 0.0
        return round((self.vram_used_gb / self.vram_total_gb) * 100.0, 1)

    @property
    def ram_warning(self) -> bool:
        return self.ram_percent >= config.RAM_WARNING_THRESHOLD_PCT

    @property
    def vram_warning(self) -> bool:
        return self.gpu_available and self.vram_percent >= config.VRAM_WARNING_THRESHOLD_PCT


@dataclass
class LedgerStats:
    """Estatísticas consolidadas de consumo de tokens e custos estimados."""

    session_prompts: int
    session_tokens: int
    session_cost_usd: float
    daily_cost_usd: float
    monthly_cost_usd: float


@dataclass
class PromptRecord:
    """Registro individual de um prompt processado e arquivado no banco."""

    id: int
    timestamp: str
    pt_chars: int
    en_tokens_est: int
    cloud_model: str
    cost_estimate: float


class TelemetryCollector:
    """Coleta métricas de hardware e gerencia o banco de dados SQLite de auditoria de tokens."""

    def __init__(self, db_path: Path | str = config.LEDGER_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.session_start_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        self._init_db()

    def _init_db(self) -> None:
        """Cria a tabela append-only prompt_ledger caso não exista."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS prompt_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    pt_chars INTEGER NOT NULL,
                    en_tokens_est INTEGER NOT NULL,
                    cloud_model TEXT NOT NULL,
                    cost_estimate REAL NOT NULL
                )
                """
            )
            conn.commit()

    @staticmethod
    def calculate_cost(en_tokens_est: int, cloud_model: str) -> float:
        """Calcula o custo estimado em USD com base no modelo alvo."""
        rate_per_1m = config.CLOUD_MODEL_PRICING_PER_1M_TOKENS.get(
            cloud_model,
            config.CLOUD_MODEL_PRICING_PER_1M_TOKENS.get("gemini-3.8-flash-medium", 0.15),
        )
        return round((max(0, en_tokens_est) / 1_000_000.0) * rate_per_1m, 6)

    def record_prompt(
        self,
        pt_chars: int,
        en_tokens_est: int,
        cloud_model: str = "agy",
    ) -> float:
        """Registra a execução no banco SQLite e retorna o custo estimado."""
        cost = self.calculate_cost(en_tokens_est, cloud_model)
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO prompt_ledger (timestamp, pt_chars, en_tokens_est, cloud_model, cost_estimate)
                VALUES (?, ?, ?, ?, ?)
                """,
                (now_iso, int(pt_chars), int(en_tokens_est), str(cloud_model), float(cost)),
            )
            conn.commit()
        return cost

    def get_ledger_stats(self) -> LedgerStats:
        """Agrega o consumo de tokens e custos da sessão atual, do dia e do mês."""
        now = datetime.now(timezone.utc)
        day_prefix = now.strftime("%Y-%m-%d")
        month_prefix = now.strftime("%Y-%m")

        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(en_tokens_est), 0), COALESCE(SUM(cost_estimate), 0.0)
                FROM prompt_ledger
                WHERE timestamp >= ?
                """,
                (self.session_start_iso,),
            )
            sess_count, sess_tokens, sess_cost = cur.fetchone()

            cur.execute(
                """
                SELECT COALESCE(SUM(cost_estimate), 0.0)
                FROM prompt_ledger
                WHERE timestamp LIKE ?
                """,
                (f"{day_prefix}%",),
            )
            (daily_cost,) = cur.fetchone()

            cur.execute(
                """
                SELECT COALESCE(SUM(cost_estimate), 0.0)
                FROM prompt_ledger
                WHERE timestamp LIKE ?
                """,
                (f"{month_prefix}%",),
            )
            (monthly_cost,) = cur.fetchone()

        return LedgerStats(
            session_prompts=int(sess_count or 0),
            session_tokens=int(sess_tokens or 0),
            session_cost_usd=float(sess_cost or 0.0),
            daily_cost_usd=float(daily_cost or 0.0),
            monthly_cost_usd=float(monthly_cost or 0.0),
        )

    def get_recent_records(self, limit: int = 8) -> List[PromptRecord]:
        """Recupera os registros mais recentes salvos no banco para exibição."""
        records: List[PromptRecord] = []
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, timestamp, pt_chars, en_tokens_est, cloud_model, cost_estimate
                FROM prompt_ledger
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )
            for row in cur.fetchall():
                records.append(
                    PromptRecord(
                        id=row[0],
                        timestamp=row[1],
                        pt_chars=row[2],
                        en_tokens_est=row[3],
                        cloud_model=row[4],
                        cost_estimate=row[5],
                    )
                )
        return records

    def poll_hardware(self) -> HardwareMetrics:
        """Consulta métricas de CPU, RAM (psutil) e GPU VRAM (nvidia-smi)."""
        cpu_pct = psutil.cpu_percent(interval=None)
        vm = psutil.virtual_memory()
        ram_used_gb = round((vm.total - vm.available) / (1024**3), 2)
        ram_total_gb = round(vm.total / (1024**3), 2)

        vram_used, vram_total, gpu_ok = self._query_gpu_vram()
        return HardwareMetrics(
            cpu_percent=round(cpu_pct, 1),
            ram_percent=round(vm.percent, 1),
            ram_used_gb=ram_used_gb,
            ram_total_gb=ram_total_gb,
            vram_used_gb=vram_used,
            vram_total_gb=vram_total,
            gpu_available=gpu_ok,
        )

    @staticmethod
    def _query_gpu_vram() -> tuple[float, float, bool]:
        """Extrai VRAM alocada e total em GB via nvidia-smi."""
        try:
            proc = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=1.5,
                check=False,
            )
            if proc.returncode != 0 or not proc.stdout.strip():
                return 0.0, 0.0, False
            first_line = proc.stdout.strip().splitlines()[0]
            parts = [p.strip() for p in first_line.split(",")]
            if len(parts) >= 2:
                used_mib = float(parts[0])
                total_mib = float(parts[1])
                return round(used_mib / 1024.0, 2), round(total_mib / 1024.0, 2), True
        except Exception:
            pass
        return 0.0, 0.0, False
