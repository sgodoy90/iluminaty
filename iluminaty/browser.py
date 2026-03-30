"""
ILUMINATY - Capa 4: Browser Control (Chrome DevTools Protocol)
===============================================================
Control total del browser sin screenshots.
DOM, formularios, network, JavaScript, tabs — todo via CDP.

Chrome/Edge deben estar corriendo con --remote-debugging-port.

"Navega a google.com" → navigate("https://google.com")
"Haz click en #submit" → click_selector("#submit")
"Llena el formulario" → fill_form({"email": "test@test.com"})
"""

import json
import subprocess
import sys
import time
from typing import Optional


class BrowserBridge:
    """
    Control de Chrome/Edge via Chrome DevTools Protocol (CDP).

    Requiere que el browser corra con:
        chrome --remote-debugging-port=9222

    Usa HTTP endpoints de CDP para comunicarse (no websocket para simplicidad).
    """

    def __init__(self, debug_port: int = 9222, host: str = "127.0.0.1"):
        self._host = host
        self._port = debug_port
        self._base_url = f"http://{host}:{debug_port}"
        self._ws_url: Optional[str] = None
        self._ws = None  # websocket connection

    @property
    def available(self) -> bool:
        """Verifica si Chrome esta corriendo con debug port."""
        try:
            return self._http_get("/json/version") is not None
        except Exception:
            return False

    def _http_get(self, path: str) -> Optional[dict]:
        """HTTP GET a CDP endpoint."""
        try:
            import urllib.request
            url = f"{self._base_url}{path}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=3) as resp:
                return json.loads(resp.read().decode())
        except Exception:
            return None

    def _http_get_list(self, path: str) -> list:
        """HTTP GET que retorna lista."""
        try:
            import urllib.request
            url = f"{self._base_url}{path}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=3) as resp:
                return json.loads(resp.read().decode())
        except Exception:
            return []

    def _send_command(self, method: str, params: Optional[dict] = None) -> dict:
        """Envia un comando CDP via websocket."""
        if not self._ws:
            if not self._connect_ws():
                return {"error": "Not connected to browser"}
        try:
            cmd_id = int(time.time() * 1000) % 1000000
            msg = {"id": cmd_id, "method": method}
            if params:
                msg["params"] = params
            self._ws.send(json.dumps(msg))
            # Leer respuesta (con timeout simple)
            response = self._ws.recv()
            return json.loads(response)
        except Exception as e:
            self._ws = None
            return {"error": str(e)}

    def _connect_ws(self) -> bool:
        """Conecta al websocket de la pagina activa."""
        try:
            import websockets.sync.client as ws_client
            tabs = self._http_get_list("/json")
            if not tabs:
                return False
            # Primer tab de tipo page
            for tab in tabs:
                if tab.get("type") == "page":
                    self._ws_url = tab.get("webSocketDebuggerUrl")
                    break
            if not self._ws_url:
                self._ws_url = tabs[0].get("webSocketDebuggerUrl")
            if not self._ws_url:
                return False
            self._ws = ws_client.connect(self._ws_url)
            return True
        except Exception:
            return False

    # ─── Tab Management ───

    def list_tabs(self) -> list[dict]:
        """Lista todas las tabs abiertas."""
        tabs = self._http_get_list("/json")
        return [
            {
                "id": tab.get("id"),
                "title": tab.get("title", ""),
                "url": tab.get("url", ""),
                "type": tab.get("type", ""),
            }
            for tab in tabs
        ]

    def new_tab(self, url: str = "about:blank") -> dict:
        """Abre una nueva tab."""
        result = self._http_get(f"/json/new?{url}")
        if result:
            return {"success": True, "tab_id": result.get("id"), "url": url}
        return {"success": False, "error": "Failed to create tab"}

    def close_tab(self, tab_id: str) -> dict:
        """Cierra una tab. DESTRUCTIVE."""
        result = self._http_get(f"/json/close/{tab_id}")
        return {"success": True, "tab_id": tab_id}

    def activate_tab(self, tab_id: str) -> dict:
        """Trae una tab al frente."""
        self._http_get(f"/json/activate/{tab_id}")
        return {"success": True, "tab_id": tab_id}

    # ─── Navigation ───

    def navigate(self, url: str) -> dict:
        """Navega a una URL."""
        result = self._send_command("Page.navigate", {"url": url})
        if "error" in result:
            return {"success": False, "error": result["error"]}
        return {"success": True, "url": url}

    def get_url(self) -> str:
        """URL actual."""
        result = self._send_command("Runtime.evaluate",
                                     {"expression": "window.location.href"})
        try:
            return result.get("result", {}).get("result", {}).get("value", "")
        except Exception:
            return ""

    def go_back(self) -> dict:
        """Navega atras."""
        return self._send_command("Page.navigateToHistoryEntry",
                                   {"entryId": -1})

    def reload(self) -> dict:
        """Recarga la pagina."""
        return self._send_command("Page.reload")

    # ─── DOM Interaction ───

    def get_dom(self, depth: int = 3) -> dict:
        """Obtiene el DOM tree (simplificado)."""
        result = self._send_command("DOM.getDocument", {"depth": depth})
        return result.get("result", {})

    def query_selector(self, selector: str) -> dict:
        """Busca un elemento por CSS selector."""
        # Primero obtener el document root
        doc = self._send_command("DOM.getDocument")
        root_id = doc.get("result", {}).get("root", {}).get("nodeId")
        if not root_id:
            return {"error": "No document root"}
        result = self._send_command("DOM.querySelector",
                                     {"nodeId": root_id, "selector": selector})
        return result.get("result", {})

    def query_selector_all(self, selector: str) -> list:
        """Busca todos los elementos que matchean un CSS selector."""
        doc = self._send_command("DOM.getDocument")
        root_id = doc.get("result", {}).get("root", {}).get("nodeId")
        if not root_id:
            return []
        result = self._send_command("DOM.querySelectorAll",
                                     {"nodeId": root_id, "selector": selector})
        return result.get("result", {}).get("nodeIds", [])

    def click_selector(self, selector: str) -> dict:
        """Click en un elemento DOM via JavaScript."""
        safe_selector = json.dumps(selector)
        js = f'document.querySelector({safe_selector})?.click(); "clicked"'
        result = self._send_command("Runtime.evaluate", {"expression": js})
        value = result.get("result", {}).get("result", {}).get("value")
        if value == "clicked":
            return {"success": True, "selector": selector}
        return {"success": False, "error": "Element not found or click failed"}

    def fill_input(self, selector: str, value: str) -> dict:
        """Llena un input field via DOM."""
        safe_selector = json.dumps(selector)
        js = f'''
        (function() {{
            var el = document.querySelector({safe_selector});
            if (!el) return "not_found";
            el.focus();
            el.value = {json.dumps(value)};
            el.dispatchEvent(new Event("input", {{bubbles: true}}));
            el.dispatchEvent(new Event("change", {{bubbles: true}}));
            return "filled";
        }})()
        '''
        result = self._send_command("Runtime.evaluate", {"expression": js})
        val = result.get("result", {}).get("result", {}).get("value")
        if val == "filled":
            return {"success": True, "selector": selector}
        return {"success": False, "error": "Element not found"}

    def fill_form(self, fields: dict[str, str]) -> dict:
        """Llena multiples campos de un formulario."""
        results = {}
        for selector, value in fields.items():
            results[selector] = self.fill_input(selector, value)
        success = all(r.get("success") for r in results.values())
        return {"success": success, "fields": results}

    def submit_form(self, form_selector: str = "form") -> dict:
        """Submit de un formulario."""
        safe_selector = json.dumps(form_selector)
        js = f'document.querySelector({safe_selector})?.submit(); "submitted"'
        result = self._send_command("Runtime.evaluate", {"expression": js})
        return {"success": True, "form": form_selector}

    # ─── JavaScript Execution ───

    def evaluate(self, expression: str) -> dict:
        """Ejecuta JavaScript en la pagina."""
        result = self._send_command("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
        })
        return result.get("result", {}).get("result", {})

    def get_page_text(self) -> str:
        """Obtiene todo el texto visible de la pagina."""
        result = self.evaluate("document.body?.innerText || ''")
        return result.get("value", "")

    def get_page_title(self) -> str:
        """Titulo de la pagina."""
        result = self.evaluate("document.title")
        return result.get("value", "")

    # ─── Utility ───

    def get_browser_info(self) -> dict:
        """Informacion del browser."""
        return self._http_get("/json/version") or {}

    def launch_chrome(self, url: str = "about:blank") -> dict:
        """Intenta lanzar Chrome con debug port habilitado."""
        platform = sys.platform
        chrome_paths = []
        if platform == "win32":
            chrome_paths = [
                "C:/Program Files/Google/Chrome/Application/chrome.exe",
                "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
                str(__import__('pathlib').Path.home() / "AppData/Local/Google/Chrome/Application/chrome.exe"),
            ]
        elif platform == "darwin":
            chrome_paths = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
        else:
            chrome_paths = ["google-chrome", "chromium-browser", "chromium"]

        for path in chrome_paths:
            try:
                subprocess.Popen(
                    [path, f"--remote-debugging-port={self._port}", url],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                time.sleep(1)
                if self.available:
                    return {"success": True, "port": self._port}
            except (FileNotFoundError, OSError):
                continue
        return {"success": False, "error": "Chrome not found"}

    @property
    def stats(self) -> dict:
        tabs = self.list_tabs() if self.available else []
        return {
            "available": self.available,
            "debug_port": self._port,
            "tabs_count": len(tabs),
            "connected": self._ws is not None,
        }
