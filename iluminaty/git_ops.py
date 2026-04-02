"""
ILUMINATY - Capa 3: Git Operations
====================================
Operaciones Git sin abrir terminal.
Status, commit, push, pull, branch, diff, log — todo programatico.

"Commitea los cambios" → git_commit("fix: bug resolved")
"Que cambio?" → git_diff()
"Crea un branch" → git_branch("feature/new-ui")
"""

import subprocess
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class GitOps:
    """
    Wrapper de Git operations via subprocess.
    Todas las operaciones son sobre un repo especifico.
    """

    def __init__(self, repo_path: Optional[str] = None):
        self._repo_path = repo_path or "."
        self._git = self._find_git()

    def _find_git(self) -> Optional[str]:
        try:
            result = subprocess.run(
                ["git", "--version"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return "git"
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as e:
            logger.debug("git binary not available: %s", e)
        return None

    @property
    def available(self) -> bool:
        return self._git is not None

    def _run(self, *args, timeout: float = 15) -> dict:
        """Ejecuta un comando git y retorna resultado."""
        if not self._git:
            return {"success": False, "error": "Git not found"}
        try:
            result = subprocess.run(
                [self._git] + list(args),
                capture_output=True, text=True,
                cwd=self._repo_path, timeout=timeout,
            )
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip(),
                "return_code": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Git command timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ─── Read-only Operations (Safe) ───

    def status(self) -> dict:
        """git status --porcelain + branch info."""
        result = self._run("status", "--porcelain", "-b")
        if not result["success"]:
            return result
        lines = result["stdout"].split("\n")
        branch_line = lines[0] if lines else ""
        branch = branch_line.replace("## ", "").split("...")[0] if branch_line.startswith("##") else "unknown"
        changes = []
        for line in lines[1:]:
            if line.strip():
                status_code = line[:2].strip()
                file_path = line[3:].strip()
                changes.append({"status": status_code, "file": file_path})
        return {
            "success": True, "branch": branch,
            "changes": changes, "clean": len(changes) == 0,
        }

    def diff(self, staged: bool = False, file_path: Optional[str] = None) -> dict:
        """git diff (staged o unstaged)."""
        args = ["diff"]
        if staged:
            args.append("--staged")
        if file_path:
            args.extend(["--", file_path])
        return self._run(*args)

    def log(self, count: int = 10, oneline: bool = True) -> dict:
        """git log."""
        args = ["log", f"-{count}"]
        if oneline:
            args.append("--oneline")
        result = self._run(*args)
        if result["success"] and oneline:
            commits = []
            for line in result["stdout"].split("\n"):
                if line.strip():
                    parts = line.split(" ", 1)
                    commits.append({"hash": parts[0], "message": parts[1] if len(parts) > 1 else ""})
            result["commits"] = commits
        return result

    def show(self, commit: str = "HEAD") -> dict:
        """git show."""
        return self._run("show", commit, "--stat")

    def blame(self, file_path: str) -> dict:
        """git blame."""
        return self._run("blame", file_path)

    def stash_list(self) -> dict:
        """git stash list."""
        return self._run("stash", "list")

    # ─── Write Operations (Normal) ───

    def add(self, files: Optional[list[str]] = None) -> dict:
        """git add. Sin args = git add -A."""
        if files:
            return self._run("add", "--", *files)
        return self._run("add", "-A")

    def commit(self, message: str) -> dict:
        """git commit."""
        return self._run("commit", "-m", message)

    def branch(self, name: str, checkout: bool = True) -> dict:
        """Crea (y opcionalmente cambia a) un branch."""
        if checkout:
            return self._run("checkout", "-b", name)
        return self._run("branch", name)

    def checkout(self, ref: str) -> dict:
        """git checkout (branch, tag, commit)."""
        return self._run("checkout", ref)

    def pull(self, remote: str = "origin", branch: Optional[str] = None) -> dict:
        """git pull."""
        args = ["pull", remote]
        if branch:
            args.append(branch)
        return self._run(*args, timeout=30)

    def stash(self, message: Optional[str] = None) -> dict:
        """git stash."""
        args = ["stash"]
        if message:
            args.extend(["push", "-m", message])
        return self._run(*args)

    def stash_pop(self) -> dict:
        """git stash pop."""
        return self._run("stash", "pop")

    # ─── Destructive Operations (requieren confirmacion) ───

    def push(self, remote: str = "origin", branch: Optional[str] = None,
             set_upstream: bool = False) -> dict:
        """git push. DESTRUCTIVE - afecta remoto."""
        args = ["push", remote]
        if branch:
            args.append(branch)
        if set_upstream:
            args.insert(1, "-u")
        return self._run(*args, timeout=30)

    def reset(self, ref: str = "HEAD", hard: bool = False) -> dict:
        """git reset. DESTRUCTIVE si hard=True."""
        args = ["reset", ref]
        if hard:
            args.append("--hard")
        return self._run(*args)

    @property
    def stats(self) -> dict:
        status = self.status()
        return {
            "available": self.available,
            "repo_path": self._repo_path,
            "branch": status.get("branch", "unknown"),
            "clean": status.get("clean", False),
            "changes": len(status.get("changes", [])),
        }
