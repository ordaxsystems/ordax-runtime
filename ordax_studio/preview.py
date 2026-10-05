from __future__ import annotations

import base64
import io
import threading
import tkinter as tk
from tkinter import ttk
from typing import Callable

from PIL import Image, ImageTk


class PreviewPane(tk.Frame):
    """Lovable-style visual preview surface shared by web and engine projects."""

    def __init__(self, parent, agent, get_project: Callable[[], str | None]):
        super().__init__(parent, bg="#080F19")
        self.agent = agent
        self.get_project = get_project
        self.mode: str | None = None
        self.revision: int | None = None
        self.photo = None
        self.auto = tk.BooleanVar(value=True)
        self._busy = False
        self._build()
        self.after(1200, self._poll)

    def _build(self) -> None:
        bar = tk.Frame(self, bg="#080F19")
        bar.pack(fill="x", padx=8, pady=8)
        tk.Label(bar, text="PREVIEW", bg="#080F19", fg="#8E9DB4",
                 font=("Segoe UI Semibold", 9)).pack(side="left")
        ttk.Checkbutton(bar, text="Auto", variable=self.auto).pack(side="right")
        ttk.Button(bar, text="Parar", command=self.stop_runtime).pack(side="right", padx=(0, 6))
        ttk.Button(bar, text="Executar", command=self.start_runtime).pack(side="right", padx=(0, 6))
        ttk.Button(bar, text="Capturar", command=self.capture).pack(side="right", padx=(0, 6))
        ttk.Button(bar, text="Atualizar", command=self.refresh).pack(side="right", padx=(0, 6))

        self.image = tk.Label(
            self, text="Nenhuma imagem de preview ainda.", bg="#050A10", fg="#8E9DB4",
            anchor="center", justify="center", padx=8, pady=8,
        )
        self.image.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.meta = tk.Label(
            self, text="", bg="#080F19", fg="#8E9DB4", anchor="w",
            justify="left", wraplength=360, font=("Segoe UI", 9),
        )
        self.meta.pack(fill="x", padx=8, pady=(0, 8))

    def project_changed(self) -> None:
        self.mode = None
        self.revision = None
        self.photo = None
        self.image.configure(image="", text="Carregando preview…")
        self.refresh()

    def refresh(self) -> None:
        project = self.get_project()
        if not project or self._busy:
            return
        self._busy = True
        def worker() -> None:
            result = self.agent.execute("project.preview_status", {"project": project})
            self.after(0, lambda: self._apply_status(project, result))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_status(self, project: str, result) -> None:
        self._busy = False
        if project != self.get_project():
            return
        if not result.ok:
            self.meta.configure(text=result.summary)
            return
        data = result.data
        self.mode = str(data.get("mode") or "artifact")
        latest = data.get("latest_image")
        url = data.get("url")
        self.meta.configure(text=f"Modo: {self.mode}" + (f"\n{url}" if url else ""))
        if not latest:
            self.image.configure(image="", text="Nenhuma captura disponível.\nUse Capturar para gerar uma.")
            self.photo = None
            return
        revision = int(latest.get("modified_at_ns") or 0)
        if revision == self.revision and self.photo is not None:
            return
        payload = {
            "project": project,
            **latest["artifact_preview_payload"],
            "thumbnail": True,
            "max_width": 900,
            "max_height": 700,
        }
        def worker() -> None:
            preview = self.agent.execute("artifact.preview", payload)
            self.after(0, lambda: self._apply_image(project, revision, latest, preview))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_image(self, project: str, revision: int, latest: dict, result) -> None:
        if project != self.get_project() or not result.ok:
            if not result.ok:
                self.meta.configure(text=result.summary)
            return
        try:
            raw = base64.b64decode(result.data["base64"])
            image = Image.open(io.BytesIO(raw))
            image.thumbnail((900, 700), Image.Resampling.LANCZOS)
            self.photo = ImageTk.PhotoImage(image)
        except Exception as error:
            self.meta.configure(text=f"Falha ao abrir preview: {error}")
            return
        self.revision = revision
        self.image.configure(image=self.photo, text="")
        self.meta.configure(
            text=f"Modo: {self.mode}\n{latest.get('relative_path', '')} • {latest.get('size_bytes', 0) / 1024:.1f} KB"
        )

    def capture(self, *, silent: bool = False) -> None:
        project = self.get_project()
        if not project or self._busy:
            return
        self._busy = True
        if not silent:
            self.meta.configure(text="Gerando preview…")

        def worker() -> None:
            result = self.agent.execute("project.preview_capture", {"project": project})
            self.after(0, lambda: self._capture_done(project, result, silent))

        threading.Thread(target=worker, daemon=True).start()

    def _capture_done(self, project: str, result, silent: bool) -> None:
        self._busy = False
        if project != self.get_project():
            return
        if not result.ok:
            if not silent:
                self.meta.configure(text=result.summary)
            return
        self.revision = None
        self.refresh()

    def _poll(self) -> None:
        try:
            if self.auto.get():
                if self.mode == "web":
                    self.capture(silent=True)
                else:
                    self.refresh()
        finally:
            self.after(3000, self._poll)

    def start_runtime(self) -> None:
        project = self.get_project()
        if not project or self._busy:
            return
        self._busy = True
        self.meta.configure(text="Iniciando preview web…")

        def worker() -> None:
            result = self.agent.execute("project.preview_start", {"project": project})
            self.after(0, lambda: self._runtime_done(project, result))

        threading.Thread(target=worker, daemon=True).start()

    def stop_runtime(self) -> None:
        project = self.get_project()
        if not project or self._busy:
            return
        self._busy = True
        self.meta.configure(text="Parando preview web…")

        def worker() -> None:
            result = self.agent.execute("project.preview_stop", {"project": project})
            self.after(0, lambda: self._runtime_done(project, result))

        threading.Thread(target=worker, daemon=True).start()

    def _runtime_done(self, project: str, result) -> None:
        self._busy = False
        if project != self.get_project():
            return
        self.meta.configure(text=result.summary)
        self.mode = "web" if result.ok else self.mode
        self.after(300, self.refresh)
