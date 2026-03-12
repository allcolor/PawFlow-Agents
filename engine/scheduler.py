# Flow Scheduler

"""
Scheduler pour execution de flux basee sur CRON.
Gere les jobs planifies avec persistance JSON.
"""

import json
import os
import threading
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, Any

from engine.parser import FlowParser
from engine.continuous_executor import ContinuousFlowExecutor

logger = logging.getLogger(__name__)


class SimpleCronParser:
    """
    Parser simple pour expressions CRON.

    Format: "minute hour day month weekday"
    Supporte: exact (5), wildcard (*), interval (*/N), range (1-3)
    """

    def parse_field(self, field: str, max_value: int) -> set:
        if field.startswith("*/"):
            step = int(field[2:])
            return set(range(0, max_value + 1, step))

        if field == "*":
            return set(range(max_value + 1))

        if "-" in field and not field.startswith("-"):
            start, end = map(int, field.split("-"))
            return set(range(start, end + 1))

        return {int(field)}

    def parse(self, cron_expression: str) -> Dict[str, set]:
        fields = cron_expression.strip().split()
        if len(fields) != 5:
            raise ValueError(f"Expression CRON invalide: {cron_expression}")

        names = ["minute", "hour", "day", "month", "weekday"]
        limits = [59, 23, 31, 12, 6]

        return {
            name: self.parse_field(field, limit)
            for name, field, limit in zip(names, fields, limits)
        }

    def matches(self, cron_expression: str, dt: datetime) -> bool:
        try:
            parsed = self.parse(cron_expression)

            if dt.minute not in parsed["minute"]:
                return False
            if dt.hour not in parsed["hour"]:
                return False
            if dt.day not in parsed["day"]:
                return False
            if dt.month not in parsed["month"]:
                return False

            # Python weekday: mon=0..sun=6 -> CRON: sun=0..sat=6
            cron_weekday = (dt.weekday() + 1) % 7
            if cron_weekday not in parsed["weekday"]:
                return False

            return True
        except (ValueError, IndexError):
            return False


class FlowScheduler:
    """Scheduler pour execution de flux basee sur CRON."""

    def __init__(self):
        self.jobs: Dict[str, Dict[str, Any]] = {}
        self.running = False
        self.scheduler_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._cron = SimpleCronParser()

    def add_job(self, job_id: str, flow_path: str, cron_expression: str,
                enabled: bool = True,
                parameters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Ajouter un job planifie."""
        # Validate cron expression
        self._cron.parse(cron_expression)

        with self._lock:
            self.jobs[job_id] = {
                "flow_path": flow_path,
                "cron_expression": cron_expression,
                "enabled": enabled,
                "last_run": None,
                "next_run": self._calculate_next_run(cron_expression),
                "status": "idle",
                "parameters": parameters or {},
            }
            return self.jobs[job_id].copy()

    def remove_job(self, job_id: str):
        with self._lock:
            self.jobs.pop(job_id, None)

    def get_jobs(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {jid: j.copy() for jid, j in self.jobs.items()}

    def get_job(self, job_id: str) -> Dict[str, Any]:
        with self._lock:
            if job_id not in self.jobs:
                raise KeyError(f"Job '{job_id}' non trouve")
            return self.jobs[job_id].copy()

    def enable_job(self, job_id: str):
        with self._lock:
            if job_id not in self.jobs:
                raise KeyError(f"Job '{job_id}' non trouve")
            self.jobs[job_id]["enabled"] = True

    def disable_job(self, job_id: str):
        with self._lock:
            if job_id not in self.jobs:
                raise KeyError(f"Job '{job_id}' non trouve")
            self.jobs[job_id]["enabled"] = False

    def start(self):
        """Demarrer le scheduler en arriere-plan."""
        with self._lock:
            if self.running:
                return
            self.running = True

        self.scheduler_thread = threading.Thread(
            target=self._scheduler_loop,
            daemon=True,
            name="FlowScheduler",
        )
        self.scheduler_thread.start()
        logger.info("Scheduler demarre")

    def stop(self):
        """Arreter le scheduler."""
        with self._lock:
            if not self.running:
                return
            self.running = False

        if self.scheduler_thread:
            self.scheduler_thread.join(timeout=5.0)
            self.scheduler_thread = None
        logger.info("Scheduler arrete")

    def _scheduler_loop(self):
        """Boucle principale, verifie toutes les 30s."""
        while self.running:
            try:
                now = datetime.now()

                with self._lock:
                    jobs_snapshot = {jid: dict(j) for jid, j in self.jobs.items()}

                for job_id, job in jobs_snapshot.items():
                    if not job.get("enabled"):
                        continue
                    if job.get("status") == "running":
                        continue

                    if self._should_run(job, now):
                        threading.Thread(
                            target=self._execute_job,
                            args=(job_id,),
                            daemon=True,
                        ).start()

            except Exception as e:
                logger.error(f"Erreur scheduler loop: {e}")

            # Sleep 30s with responsive stopping
            for _ in range(300):
                if not self.running:
                    break
                threading.Event().wait(0.1)

    def _should_run(self, job: Dict, now: datetime) -> bool:
        """Verifier si un job doit s'executer maintenant."""
        last_run_str = job.get("last_run")

        # Never ran before — run if cron matches now
        if last_run_str is None:
            return self._cron.matches(job["cron_expression"], now)

        last_run = datetime.fromisoformat(last_run_str)

        # Don't re-run within the same minute
        if (now - last_run).total_seconds() < 60:
            return False

        return self._cron.matches(job["cron_expression"], now)

    def _calculate_next_run(self, cron_expression: str) -> str:
        """Calculer la prochaine execution (max 48h de recherche)."""
        test_time = datetime.now().replace(second=0, microsecond=0) + timedelta(minutes=1)

        for _ in range(48 * 60):  # 48 hours of minutes
            if self._cron.matches(cron_expression, test_time):
                return test_time.isoformat()
            test_time += timedelta(minutes=1)

        return test_time.isoformat()

    def _execute_job(self, job_id: str):
        """Executer un job."""
        with self._lock:
            if job_id not in self.jobs:
                return
            self.jobs[job_id]["status"] = "running"
            flow_path = self.jobs[job_id]["flow_path"]
            cron_expr = self.jobs[job_id]["cron_expression"]
            parameters = self.jobs[job_id].get("parameters") or None

        try:
            flow = FlowParser.parse_from_file(flow_path)
            result = ContinuousFlowExecutor.run_batch(
                flow, parameters=parameters, max_workers=4,
            )

            with self._lock:
                if job_id in self.jobs:
                    self.jobs[job_id]["last_run"] = datetime.now().isoformat()
                    self.jobs[job_id]["next_run"] = self._calculate_next_run(cron_expr)
                    self.jobs[job_id]["status"] = "success" if result.success else "error"

            logger.info(f"Job {job_id}: {'success' if result.success else 'error'}")

        except Exception as e:
            logger.error(f"Job {job_id} failed: {e}")
            with self._lock:
                if job_id in self.jobs:
                    self.jobs[job_id]["status"] = "error"
                    self.jobs[job_id]["last_run"] = datetime.now().isoformat()

    def save_jobs(self, filepath: str = "config/scheduler.json"):
        """Sauvegarder les jobs en JSON."""
        with self._lock:
            jobs_data = {jid: dict(j) for jid, j in self.jobs.items()}

        dir_path = os.path.dirname(filepath)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(jobs_data, f, indent=2, ensure_ascii=False)

    def load_jobs(self, filepath: str = "config/scheduler.json"):
        """Charger les jobs depuis un JSON."""
        if not os.path.exists(filepath):
            return 0

        with open(filepath, 'r', encoding='utf-8') as f:
            jobs_data = json.load(f)

        with self._lock:
            self.jobs = {jid: dict(j) for jid, j in jobs_data.items()}

        return len(self.jobs)
