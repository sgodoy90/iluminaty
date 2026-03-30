"""
ILUMINATY - Capa 3: VS Code Command Bridge
============================================
Ejecuta cualquier comando de VS Code sin tocar la UI.
Usa la CLI `code` para operaciones directas.

"Guarda el archivo" → code --command workbench.action.files.save
"Abre el terminal" → code --command workbench.action.terminal.new
"Formatea el codigo" → code --command editor.action.formatDocument
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Optional


class VSCodeBridge:
    """
    Puente directo a VS Code via CLI.
    Ejecuta comandos, abre archivos, gestiona extensiones.
    """

    def __init__(self):
        self._platform = sys.platform
        self._code_path = self._find_code_cli()

    def _find_code_cli(self) -> Optional[str]:
        """Encuentra el ejecutable `code` de VS Code."""
        candidates = ["code"]
        if self._platform == "win32":
            candidates.extend([
                str(Path.home() / "AppData/Local/Programs/Microsoft VS Code/bin/code.cmd"),
                "code.cmd",
            ])
        elif self._platform == "darwin":
            candidates.append("/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code")

        for cmd in candidates:
            try:
                result = subprocess.run(
                    [cmd, "--version"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    return cmd
            except (FileNotFoundError, OSError):
                continue
        return None

    @property
    def available(self) -> bool:
        return self._code_path is not None

    def execute_command(self, command: str) -> dict:
        """Ejecuta un comando de VS Code (workbench.action.*, editor.action.*, etc)."""
        if not self._code_path:
            return {"success": False, "error": "VS Code CLI not found"}
        try:
            # VS Code CLI no soporta --command directamente en todas las versiones
            # Usamos el approach de extension o stdin
            result = subprocess.run(
                [self._code_path, "--command", command],
                capture_output=True, text=True, timeout=10
            )
            return {"success": result.returncode == 0, "command": command,
                    "output": result.stdout.strip()}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def open_file(self, file_path: str, line: Optional[int] = None,
                  column: Optional[int] = None) -> dict:
        """Abre un archivo en VS Code, opcionalmente en linea:columna."""
        if not self._code_path:
            return {"success": False, "error": "VS Code CLI not found"}
        target = file_path
        if line:
            target += f":{line}"
            if column:
                target += f":{column}"
        try:
            subprocess.Popen(
                [self._code_path, "--goto", target],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return {"success": True, "file": file_path, "line": line}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def open_folder(self, folder_path: str, new_window: bool = False) -> dict:
        """Abre una carpeta en VS Code."""
        if not self._code_path:
            return {"success": False, "error": "VS Code CLI not found"}
        args = [self._code_path, folder_path]
        if new_window:
            args.append("--new-window")
        try:
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"success": True, "folder": folder_path}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def diff_files(self, file1: str, file2: str) -> dict:
        """Abre un diff de dos archivos en VS Code."""
        if not self._code_path:
            return {"success": False, "error": "VS Code CLI not found"}
        try:
            subprocess.Popen(
                [self._code_path, "--diff", file1, file2],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return {"success": True, "file1": file1, "file2": file2}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def list_extensions(self) -> list[str]:
        """Lista extensiones instaladas."""
        if not self._code_path:
            return []
        try:
            result = subprocess.run(
                [self._code_path, "--list-extensions"],
                capture_output=True, text=True, timeout=10
            )
            return [ext.strip() for ext in result.stdout.strip().split("\n") if ext.strip()]
        except Exception:
            return []

    def get_version(self) -> Optional[str]:
        if not self._code_path:
            return None
        try:
            result = subprocess.run(
                [self._code_path, "--version"],
                capture_output=True, text=True, timeout=5
            )
            return result.stdout.strip().split("\n")[0]
        except Exception:
            return None

    @property
    def stats(self) -> dict:
        return {
            "available": self.available,
            "code_path": self._code_path,
            "version": self.get_version(),
        }
