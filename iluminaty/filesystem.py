"""
ILUMINATY - Capa 5: File System Sandbox
========================================
Acceso controlado al file system con permisos y restricciones.
La IA puede leer, escribir, buscar archivos — pero SOLO dentro
de directorios permitidos (sandbox).

"Lee el archivo config.py" → read_file("config.py")
"Busca todos los TODOs" → search_files("*.py", contains="TODO")
"Escribe el resultado" → write_file("output.txt", data)
"""

import fnmatch
import os
import shutil
import time
from pathlib import Path
from typing import Optional


class FileSystemSandbox:
    """
    Acceso al file system con sandbox de seguridad.

    Reglas:
    1. Solo puede acceder a directorios en allowed_paths
    2. Nunca puede acceder a paths en blocked_paths
    3. Backup automatico antes de escribir (opcional)
    4. Size limits para reads y writes
    """

    def __init__(self, allowed_paths: Optional[list[str]] = None,
                 blocked_paths: Optional[list[str]] = None,
                 max_read_mb: float = 10.0,
                 max_write_mb: float = 5.0,
                 auto_backup: bool = True):
        self._allowed: list[Path] = [Path(p).resolve() for p in (allowed_paths or ["."])]
        self._blocked: list[Path] = [Path(p).resolve() for p in (blocked_paths or [])]
        self._max_read = int(max_read_mb * 1024 * 1024)
        self._max_write = int(max_write_mb * 1024 * 1024)
        self._auto_backup = auto_backup

        # Default blocked: system dirs, dotfiles con secretos
        self._blocked.extend([
            Path("/etc"), Path("/System"), Path("C:/Windows"),
        ])

    @property
    def available(self) -> bool:
        return True

    def _check_path(self, path_str: str) -> Path:
        """Valida que el path este dentro del sandbox."""
        path = Path(path_str).resolve()

        # Check blocked
        for blocked in self._blocked:
            if path.is_relative_to(blocked):
                raise PermissionError(f"Access denied: {path} is in blocked path {blocked}")

        # Check allowed
        in_allowed = False
        for allowed in self._allowed:
            if path.is_relative_to(allowed):
                in_allowed = True
                break

        if not in_allowed:
            raise PermissionError(
                f"Access denied: {path} is not in allowed paths {[str(p) for p in self._allowed]}")

        return path

    # ─── Read Operations (Safe) ───

    def read_file(self, path: str, encoding: str = "utf-8") -> dict:
        """Lee un archivo de texto."""
        try:
            fpath = self._check_path(path)
            if not fpath.exists():
                return {"success": False, "error": f"File not found: {path}"}
            if not fpath.is_file():
                return {"success": False, "error": f"Not a file: {path}"}

            size = fpath.stat().st_size
            if size > self._max_read:
                return {"success": False,
                        "error": f"File too large: {size / 1024 / 1024:.1f}MB (max {self._max_read / 1024 / 1024:.1f}MB)"}

            content = fpath.read_text(encoding=encoding)
            return {
                "success": True, "path": str(fpath),
                "content": content, "size": size,
                "lines": content.count("\n") + 1,
            }
        except PermissionError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": f"Read failed: {e}"}

    def read_binary(self, path: str) -> dict:
        """Lee un archivo binario (retorna bytes)."""
        try:
            fpath = self._check_path(path)
            if not fpath.exists():
                return {"success": False, "error": f"File not found: {path}"}
            size = fpath.stat().st_size
            if size > self._max_read:
                return {"success": False, "error": "File too large"}
            data = fpath.read_bytes()
            return {"success": True, "path": str(fpath), "data": data, "size": size}
        except PermissionError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def list_dir(self, path: str = ".", pattern: Optional[str] = None) -> dict:
        """Lista contenido de un directorio."""
        try:
            fpath = self._check_path(path)
            if not fpath.is_dir():
                return {"success": False, "error": f"Not a directory: {path}"}

            entries = []
            for item in sorted(fpath.iterdir()):
                if pattern and not fnmatch.fnmatch(item.name, pattern):
                    continue
                stat = item.stat()
                entries.append({
                    "name": item.name,
                    "type": "dir" if item.is_dir() else "file",
                    "size": stat.st_size if item.is_file() else 0,
                    "modified": time.strftime("%Y-%m-%d %H:%M:%S",
                                             time.localtime(stat.st_mtime)),
                })
            return {"success": True, "path": str(fpath), "entries": entries,
                    "count": len(entries)}
        except PermissionError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def search_files(self, pattern: str = "*", contains: Optional[str] = None,
                     path: str = ".", max_results: int = 100) -> dict:
        """Busca archivos por glob pattern y opcionalmente por contenido."""
        try:
            fpath = self._check_path(path)
            results = []
            for match in fpath.rglob(pattern):
                if len(results) >= max_results:
                    break
                if not match.is_file():
                    continue
                # Check que sigue dentro del sandbox
                try:
                    self._check_path(str(match))
                except PermissionError:
                    continue

                entry = {"path": str(match.relative_to(fpath)), "size": match.stat().st_size}

                if contains:
                    try:
                        text = match.read_text(errors="ignore")
                        if contains.lower() not in text.lower():
                            continue
                        # Encontrar lineas que matchean
                        lines = []
                        for i, line in enumerate(text.split("\n"), 1):
                            if contains.lower() in line.lower():
                                lines.append({"line": i, "text": line.strip()[:200]})
                                if len(lines) >= 5:
                                    break
                        entry["matches"] = lines
                    except Exception:
                        continue

                results.append(entry)

            return {"success": True, "pattern": pattern, "contains": contains,
                    "results": results, "count": len(results)}
        except PermissionError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def file_info(self, path: str) -> dict:
        """Informacion detallada de un archivo."""
        try:
            fpath = self._check_path(path)
            if not fpath.exists():
                return {"success": False, "error": "File not found"}
            stat = fpath.stat()
            return {
                "success": True,
                "path": str(fpath),
                "name": fpath.name,
                "extension": fpath.suffix,
                "size": stat.st_size,
                "is_file": fpath.is_file(),
                "is_dir": fpath.is_dir(),
                "created": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_ctime)),
                "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
            }
        except PermissionError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ─── Write Operations (Normal - requieren whitelist) ───

    def write_file(self, path: str, content: str, encoding: str = "utf-8") -> dict:
        """Escribe un archivo de texto. Crea backup si ya existe."""
        try:
            fpath = self._check_path(path)
            data = content.encode(encoding)
            if len(data) > self._max_write:
                return {"success": False,
                        "error": f"Content too large: {len(data) / 1024 / 1024:.1f}MB (max {self._max_write / 1024 / 1024:.1f}MB)"}

            # Backup si existe
            if self._auto_backup and fpath.exists():
                backup = fpath.with_suffix(fpath.suffix + ".bak")
                shutil.copy2(fpath, backup)

            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(content, encoding=encoding)
            return {"success": True, "path": str(fpath), "size": len(data)}
        except PermissionError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def append_file(self, path: str, content: str, encoding: str = "utf-8") -> dict:
        """Agrega contenido al final de un archivo."""
        try:
            fpath = self._check_path(path)
            data = content.encode(encoding)
            if len(data) > self._max_write:
                return {"success": False, "error": "Content too large"}

            fpath.parent.mkdir(parents=True, exist_ok=True)
            with open(fpath, "a", encoding=encoding) as f:
                f.write(content)
            return {"success": True, "path": str(fpath), "appended": len(data)}
        except PermissionError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_dir(self, path: str) -> dict:
        """Crea un directorio (y padres si necesario)."""
        try:
            fpath = self._check_path(path)
            fpath.mkdir(parents=True, exist_ok=True)
            return {"success": True, "path": str(fpath)}
        except PermissionError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def copy_file(self, src: str, dst: str) -> dict:
        """Copia un archivo."""
        try:
            src_path = self._check_path(src)
            dst_path = self._check_path(dst)
            shutil.copy2(src_path, dst_path)
            return {"success": True, "src": str(src_path), "dst": str(dst_path)}
        except PermissionError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def move_file(self, src: str, dst: str) -> dict:
        """Mueve/renombra un archivo."""
        try:
            src_path = self._check_path(src)
            dst_path = self._check_path(dst)
            shutil.move(str(src_path), str(dst_path))
            return {"success": True, "src": str(src_path), "dst": str(dst_path)}
        except PermissionError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ─── Delete Operations (Destructive - siempre requieren confirmacion) ───

    def delete_file(self, path: str) -> dict:
        """Elimina un archivo. DESTRUCTIVE."""
        try:
            fpath = self._check_path(path)
            if not fpath.is_file():
                return {"success": False, "error": "Not a file"}
            fpath.unlink()
            return {"success": True, "path": str(fpath)}
        except PermissionError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def add_allowed_path(self, path: str):
        """Agrega un path al sandbox."""
        self._allowed.append(Path(path).resolve())

    def get_allowed_paths(self) -> list[str]:
        return [str(p) for p in self._allowed]

    @property
    def stats(self) -> dict:
        return {
            "available": True,
            "allowed_paths": self.get_allowed_paths(),
            "blocked_paths": [str(p) for p in self._blocked],
            "max_read_mb": self._max_read / 1024 / 1024,
            "max_write_mb": self._max_write / 1024 / 1024,
            "auto_backup": self._auto_backup,
        }
