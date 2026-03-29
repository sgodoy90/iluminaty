"""
ILUMINATY - Plugin System
===========================
Sistema de plugins extensible.
Los plugins se registran y reciben eventos del sistema.

Events:
  on_frame(slot)           - nuevo frame capturado
  on_change(diff)          - algo cambio en pantalla
  on_app_switch(old, new)  - usuario cambio de app
  on_speech(chunks)        - voz detectada
  on_text_detected(text)   - OCR detecto texto nuevo
  on_workflow_change(old, new) - cambio de workflow

Plugin lifecycle:
  1. Plugin se carga desde iluminaty/plugins/
  2. Plugin.setup() se llama al arrancar
  3. Eventos se disparan automaticamente
  4. Plugin.teardown() se llama al parar
"""

import os
import time
import importlib
import importlib.util
from typing import Optional, Callable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PluginEvent:
    """Un evento del sistema."""
    type: str
    timestamp: float
    data: dict = field(default_factory=dict)


class IluminatyPlugin:
    """
    Base class para plugins.
    Extender esta clase e implementar los hooks que necesites.
    """
    name: str = "unnamed"
    version: str = "0.1.0"
    description: str = ""

    def setup(self, api_base: str = "http://127.0.0.1:8420"):
        """Llamado al cargar el plugin. Inicializar recursos aqui."""
        pass

    def teardown(self):
        """Llamado al descargar. Limpiar recursos aqui."""
        pass

    def on_frame(self, frame_data: dict):
        """Nuevo frame capturado. frame_data tiene timestamp, width, height, size."""
        pass

    def on_change(self, diff_data: dict):
        """Algo cambio en la pantalla. diff_data tiene regions, percentage, heatmap."""
        pass

    def on_app_switch(self, old_app: str, new_app: str, old_title: str, new_title: str):
        """Usuario cambio de app."""
        pass

    def on_speech(self, audio_data: dict):
        """Voz detectada. audio_data tiene duration, rms_level."""
        pass

    def on_text_detected(self, text: str, blocks: list):
        """OCR detecto texto nuevo."""
        pass

    def on_workflow_change(self, old_workflow: str, new_workflow: str):
        """Cambio de workflow (coding -> browsing, etc.)."""
        pass


class PluginManager:
    """
    Gestiona plugins: carga, descarga, dispatch de eventos.
    """

    def __init__(self, plugin_dir: Optional[str] = None):
        self.plugin_dir = plugin_dir or os.path.join(
            os.path.dirname(__file__), "plugins"
        )
        self._plugins: dict[str, IluminatyPlugin] = {}
        self._event_log: list[PluginEvent] = []
        self._max_log = 200

    @property
    def loaded(self) -> list[str]:
        return list(self._plugins.keys())

    def register(self, plugin: IluminatyPlugin):
        """Registra un plugin manualmente."""
        self._plugins[plugin.name] = plugin
        try:
            plugin.setup()
        except Exception as e:
            print(f"[iluminaty] plugin '{plugin.name}' setup error: {e}")

    def unregister(self, name: str):
        """Descarga un plugin."""
        plugin = self._plugins.pop(name, None)
        if plugin:
            try:
                plugin.teardown()
            except Exception:
                pass

    def load_from_directory(self):
        """Carga todos los plugins .py del directorio de plugins."""
        plugin_path = Path(self.plugin_dir)
        if not plugin_path.exists():
            plugin_path.mkdir(parents=True, exist_ok=True)
            return

        for py_file in plugin_path.glob("*.py"):
            if py_file.name.startswith("_"):
                continue
            try:
                spec = importlib.util.spec_from_file_location(
                    f"iluminaty_plugin_{py_file.stem}", py_file
                )
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)

                    # Buscar clases que hereden de IluminatyPlugin
                    for attr_name in dir(module):
                        attr = getattr(module, attr_name)
                        if (isinstance(attr, type)
                            and issubclass(attr, IluminatyPlugin)
                            and attr is not IluminatyPlugin):
                            instance = attr()
                            self.register(instance)
                            print(f"[iluminaty] loaded plugin: {instance.name} v{instance.version}")

            except Exception as e:
                print(f"[iluminaty] error loading plugin {py_file.name}: {e}")

    def emit(self, event_type: str, **kwargs):
        """Emite un evento a todos los plugins."""
        event = PluginEvent(type=event_type, timestamp=time.time(), data=kwargs)
        self._event_log.append(event)
        if len(self._event_log) > self._max_log:
            self._event_log = self._event_log[-self._max_log:]

        for name, plugin in self._plugins.items():
            try:
                handler = getattr(plugin, f"on_{event_type}", None)
                if handler and callable(handler):
                    handler(**kwargs)
            except Exception as e:
                print(f"[iluminaty] plugin '{name}' error on {event_type}: {e}")

    def get_info(self) -> list[dict]:
        """Info de todos los plugins cargados."""
        return [
            {
                "name": p.name,
                "version": p.version,
                "description": p.description,
            }
            for p in self._plugins.values()
        ]

    def get_event_log(self, count: int = 50) -> list[dict]:
        """Ultimos N eventos emitidos."""
        return [
            {
                "type": e.type,
                "time": time.strftime("%H:%M:%S", time.localtime(e.timestamp)),
                "data_keys": list(e.data.keys()),
            }
            for e in self._event_log[-count:]
        ]

    def teardown_all(self):
        """Descarga todos los plugins."""
        for name in list(self._plugins.keys()):
            self.unregister(name)


# ─── Example Built-in Plugin ───

class LoggerPlugin(IluminatyPlugin):
    """Plugin ejemplo: loguea cambios de app y workflow."""
    name = "logger"
    version = "0.1.0"
    description = "Logs app switches and workflow changes to console"

    def on_app_switch(self, old_app: str, new_app: str, old_title: str = "", new_title: str = ""):
        ts = time.strftime("%H:%M:%S")
        print(f"  [{ts}] APP: {old_app} -> {new_app}")

    def on_workflow_change(self, old_workflow: str, new_workflow: str):
        ts = time.strftime("%H:%M:%S")
        print(f"  [{ts}] WORKFLOW: {old_workflow} -> {new_workflow}")
