"""Scrollable Tkinter frontend for the shared PawFlow installer engine."""

from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk

from pawflow_installer.engine import InstallerEngine
from pawflow_installer.events import InstallEvent
from pawflow_installer.models import InstallRequest
from pawflow_installer.state import InstallerStateStore


class InstallerGui:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Installation universelle PawFlow")
        self.root.geometry("900x760")
        self.values: dict[str, tk.Variable] = {}
        self.events: queue.Queue[str] = queue.Queue()
        self.engine: InstallerEngine | None = None
        self._build()
        self.root.after(100, self._drain_events)

    def _build(self) -> None:
        canvas = tk.Canvas(self.root, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.root, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        frame = ttk.Frame(canvas, padding=18)
        window = canvas.create_window((0, 0), window=frame, anchor="nw")
        frame.bind(
            "<Configure>",
            lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(window, width=event.width),
        )

        ttk.Label(
            frame,
            text="Installer PawFlow",
            font=("TkDefaultFont", 18, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            frame,
            text=(
                "Installation locale ou SSH, reprise durable, Tailscale recommandé "
                "et Relay Desktop optionnel."
            ),
            wraplength=820,
        ).pack(anchor="w", pady=(2, 16))

        form = ttk.Frame(frame)
        form.pack(fill="x")
        fields = [
            ("target", "Cible", "local"),
            ("host", "Hôte SSH", ""),
            ("ssh_port", "Port SSH", "22"),
            ("user", "Utilisateur SSH", ""),
            ("host_key_policy", "Politique de clé hôte", "strict"),
            ("pawflow_home", "Dossier PawFlow cible", str(Path.home() / "pawflow")),
            ("port", "Port PawFlow", "9443"),
            ("install_source", "Source", "published"),
            ("version", "Version (vide = publiée la plus récente)", ""),
            ("reachability", "Accès", "local"),
            ("reachability_host", "URL HTTPS ou nom Tailscale", ""),
            ("certificate_sha256", "Empreinte TLS SHA-256 (optionnelle)", ""),
            ("relay_server_url", "URL serveur Relay Desktop", ""),
            ("relay_server_name", "Nom du serveur Relay", ""),
            ("relay_workspace_name", "Nom du workspace Relay", ""),
            ("shares", "Chemins partagés (un par ligne)", ""),
            (
                "capabilities",
                "Capacités (une par ligne)",
                "filesystem.read\nfilesystem.write",
            ),
            ("relay_artifact", "Artifact Relay Desktop", ""),
        ]
        choices = {
            "target": ("local", "ssh"),
            "host_key_policy": ("strict", "accept-new"),
            "install_source": ("published", "source"),
            "reachability": (
                "local", "tailscale", "existing_https", "public_manual"
            ),
        }
        for row, (name, label, initial) in enumerate(fields):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="nw", padx=(0, 12), pady=4)
            variable = tk.StringVar(value=initial)
            self.values[name] = variable
            if name in {"shares", "capabilities"}:
                widget = tk.Text(form, height=4, wrap="word")
                widget.insert("1.0", initial)
                widget.grid(row=row, column=1, sticky="ew", pady=4)
                self.values[name] = widget
            elif name in choices:
                widget = ttk.Combobox(
                    form, textvariable=variable, values=choices[name], state="readonly"
                )
                widget.grid(row=row, column=1, sticky="ew", pady=4)
            else:
                ttk.Entry(form, textvariable=variable).grid(
                    row=row, column=1, sticky="ew", pady=4
                )
        form.columnconfigure(1, weight=1)

        checks = ttk.Frame(frame)
        checks.pack(fill="x", pady=8)
        for name, label in (
            ("native", "Serveur natif"),
            ("keep_old_images", "Conserver les anciennes images"),
            ("skip_apparmor", "Ignorer AppArmor"),
            ("relay_desktop", "Installer/configurer Relay Desktop"),
            ("relay_autostart", "Démarrage automatique Relay (opt-in)"),
        ):
            variable = tk.BooleanVar(value=False)
            self.values[name] = variable
            ttk.Checkbutton(checks, text=label, variable=variable).pack(anchor="w")

        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=10)
        ttk.Button(actions, text="Vérifier le plan", command=self.plan).pack(side="left")
        ttk.Button(actions, text="Exécuter", command=self.run).pack(side="left", padx=8)
        ttk.Button(actions, text="Annuler", command=self.cancel).pack(side="left")
        self.output = tk.Text(frame, height=18, wrap="word", state="disabled")
        self.output.pack(fill="both", expand=True, pady=(8, 0))

    def _text_value(self, name: str) -> str:
        value = self.values[name]
        if isinstance(value, tk.Text):
            return value.get("1.0", "end").strip()
        return str(value.get()).strip()

    def build_request(self) -> InstallRequest:
        target_kind = self._text_value("target")
        target = {"kind": target_kind}
        if target_kind == "ssh":
            target.update({
                "host": self._text_value("host"),
                "port": int(self._text_value("ssh_port")),
                "user": self._text_value("user"),
                "identity_file": None,
                "host_key_policy": self._text_value("host_key_policy"),
            })
        relay_enabled = bool(self.values["relay_desktop"].get())
        shares = [line.strip() for line in self._text_value("shares").splitlines() if line.strip()]
        capabilities = [
            line.strip() for line in self._text_value("capabilities").splitlines()
            if line.strip()
        ]
        version = self._text_value("version") or None
        certificate = self._text_value("certificate_sha256") or None
        reachability_host = self._text_value("reachability_host") or None
        return InstallRequest.model_validate({
            "version": 1,
            "target": target,
            "install": {
                "pawflow_home": self._text_value("pawflow_home"),
                "port": int(self._text_value("port")),
                "version": version,
                "source": self._text_value("install_source"),
                "native": bool(self.values["native"].get()),
                "keep_old_images": bool(self.values["keep_old_images"].get()),
                "skip_apparmor": bool(self.values["skip_apparmor"].get()),
            },
            "reachability": {
                "mode": self._text_value("reachability"),
                "hostname": reachability_host,
                "certificate_sha256": certificate,
            },
            "relay_desktop": {
                "install": relay_enabled,
                "server_url": self._text_value("relay_server_url") or None if relay_enabled else None,
                "server_name": self._text_value("relay_server_name") or None if relay_enabled else None,
                "workspace_name": self._text_value("relay_workspace_name") or None if relay_enabled else None,
                "capabilities": capabilities if relay_enabled else [],
                "paths": shares if relay_enabled else [],
                "autostart": bool(self.values["relay_autostart"].get()) if relay_enabled else False,
                "artifact_path": self._text_value("relay_artifact") or None if relay_enabled else None,
                "artifact_sha256": None,
            },
        })

    def _append(self, text: str) -> None:
        self.output.configure(state="normal")
        self.output.insert("end", text.rstrip() + "\n")
        self.output.see("end")
        self.output.configure(state="disabled")

    def _event(self, event: InstallEvent) -> None:
        data = event.as_dict()
        self.events.put(f"{data['step_id']} · {data['kind']} · {data['message']}")

    def _secret(self, label: str, value: str) -> None:
        self.events.put(f"{label}: {value}")

    def _confirm_certificate(self, fingerprint: str) -> bool:
        result: list[bool] = []
        completed = threading.Event()

        def ask() -> None:
            result.append(messagebox.askyesno(
                "Certificat HTTPS",
                "Le certificat n'est pas reconnu. Vérifiez puis confirmez cette "
                f"empreinte SHA-256 :\n\n{fingerprint}",
            ))
            completed.set()

        self.root.after(0, ask)
        completed.wait()
        return result[0]

    def _confirm_broad_paths(self, paths: list[str]) -> bool:
        result: list[bool] = []
        completed = threading.Event()

        def ask() -> None:
            result.append(messagebox.askyesno(
                "Partages très larges",
                "Ces chemins exposent une racine ou votre dossier personnel :\n\n"
                + "\n".join(paths)
                + "\n\nConfirmer exactement ces partages ?",
            ))
            completed.set()

        self.root.after(0, ask)
        completed.wait()
        return result[0]

    def _drain_events(self) -> None:
        while True:
            try:
                self._append(self.events.get_nowait())
            except queue.Empty:
                break
        self.root.after(100, self._drain_events)

    def plan(self) -> None:
        try:
            request = self.build_request()
            self._append(json.dumps({
                "request_digest": request.digest(),
                "steps": InstallerEngine.plan(request),
            }, indent=2, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001 - GUI validation boundary
            messagebox.showerror("Plan invalide", str(exc))

    def run(self) -> None:
        try:
            request = self.build_request()
        except Exception as exc:  # noqa: BLE001 - GUI validation boundary
            messagebox.showerror("Configuration invalide", str(exc))
            return
        if not messagebox.askyesno(
            "Confirmation",
            "Exécuter les étapes de mutation affichées dans le plan ?",
        ):
            return
        store = InstallerStateStore()
        self.engine = InstallerEngine(
            state_store=store,
            scripts_root=Path(__file__).resolve().parents[2] / "scripts",
            event_sink=self._event,
            secret_sink=self._secret,
            certificate_confirmation=self._confirm_certificate,
            broad_path_confirmation=self._confirm_broad_paths,
        )

        def work() -> None:
            try:
                state = self.engine.run(request, confirmed=True)
                url = state.step_results["reachability_plan"].evidence["wizard_url"]
                self.events.put(f"Installation prête : {url}")
                webbrowser.open(url)
            except Exception as exc:  # noqa: BLE001 - keep worker failures inside the UI
                self.events.put(f"Échec : {exc}")

        threading.Thread(target=work, daemon=True).start()

    def cancel(self) -> None:
        if self.engine is not None:
            self.engine.cancel()
            self._append("Annulation immédiate demandée.")


def main() -> None:
    root = tk.Tk()
    InstallerGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
