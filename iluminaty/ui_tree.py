"""
ILUMINATY - Capa 2: UI Tree (Accessibility Tree)
==================================================
Accede al arbol de elementos UI del SO real.
En vez de OCR sobre pixeles, lee botones, campos, menus DIRECTAMENTE.

Windows: UIAutomation via comtypes
macOS: AXUIElement via subprocess (AppleScript)
Linux: AT-SPI via subprocess (gdbus)

"Encuentra el boton Save" → busca en el arbol UI, no en pixeles.
"""

import sys
import time
import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass
class UIElement:
    """Un elemento del arbol de accesibilidad."""
    name: str
    role: str  # button, textfield, checkbox, combobox, menu, menuitem, etc.
    value: str
    x: int
    y: int
    width: int
    height: int
    is_enabled: bool
    is_focused: bool
    children_count: int
    automation_id: str = ""
    class_name: str = ""
    pid: int = 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "role": self.role,
            "value": self.value,
            "x": self.x, "y": self.y,
            "width": self.width, "height": self.height,
            "is_enabled": self.is_enabled,
            "is_focused": self.is_focused,
            "children_count": self.children_count,
            "automation_id": self.automation_id,
            "class_name": self.class_name,
            "pid": self.pid,
        }

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)


class UITree:
    """
    Acceso al Accessibility Tree del SO.
    Permite encontrar elementos UI reales sin OCR.

    Cross-platform con graceful degradation:
    - Windows: UIAutomation via comtypes (mejor)
    - macOS: AppleScript System Events
    - Linux: AT-SPI via gdbus
    """

    def __init__(self):
        self._platform = sys.platform
        self._uia = None  # Windows UIAutomation COM
        self._available = False
        self._init_platform()

    def _init_platform(self):
        if self._platform == "win32":
            self._init_windows()
        else:
            # macOS/Linux usan subprocess, siempre "disponible"
            self._available = True

    def _init_windows(self):
        """Inicializa UIAutomation en Windows via comtypes."""
        try:
            import comtypes
            import comtypes.client
            self._uia = comtypes.client.CreateObject(
                "{ff48dba4-60ef-4201-aa87-54103eef594e}",
                interface=None
            )
            self._available = True
        except Exception:
            # Fallback: usar PowerShell con UIAutomation
            try:
                result = subprocess.run(
                    ["powershell", "-command",
                     "Add-Type -AssemblyName UIAutomationClient; echo 'ok'"],
                    capture_output=True, text=True, timeout=5
                )
                if "ok" in result.stdout:
                    self._available = True
                    self._uia = "powershell"  # marker for PS fallback
            except Exception:
                self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def get_elements(self, pid: Optional[int] = None, max_depth: int = 5) -> list[dict]:
        """Lista elementos UI visibles. Opcionalmente filtra por PID."""
        if not self._available:
            return []
        if self._platform == "win32":
            return self._get_elements_win(pid, max_depth)
        elif self._platform == "darwin":
            return self._get_elements_mac(pid)
        return self._get_elements_linux(pid)

    def find_element(self, name: Optional[str] = None, role: Optional[str] = None,
                     automation_id: Optional[str] = None, pid: Optional[int] = None) -> Optional[dict]:
        """Busca un elemento por nombre, rol, o automation ID."""
        elements = self.get_elements(pid=pid)
        name_lower = name.lower() if name else None
        role_lower = role.lower() if role else None

        for el in elements:
            if name_lower and name_lower not in el.get("name", "").lower():
                continue
            if role_lower and role_lower not in el.get("role", "").lower():
                continue
            if automation_id and automation_id != el.get("automation_id", ""):
                continue
            return el
        return None

    def find_all(self, name: Optional[str] = None, role: Optional[str] = None,
                 pid: Optional[int] = None) -> list[dict]:
        """Busca todos los elementos que matchean."""
        elements = self.get_elements(pid=pid)
        name_lower = name.lower() if name else None
        role_lower = role.lower() if role else None

        results = []
        for el in elements:
            if name_lower and name_lower not in el.get("name", "").lower():
                continue
            if role_lower and role_lower not in el.get("role", "").lower():
                continue
            results.append(el)
        return results

    # ─── Windows Implementation ───

    def _get_elements_win(self, pid: Optional[int], max_depth: int) -> list[dict]:
        if self._uia == "powershell":
            return self._get_elements_win_ps(pid, max_depth)
        if self._uia is None:
            return []

        try:
            return self._get_elements_win_com(pid, max_depth)
        except Exception:
            return self._get_elements_win_ps(pid, max_depth)

    def _get_elements_win_com(self, pid: Optional[int], max_depth: int) -> list[dict]:
        """UIAutomation via comtypes COM."""
        try:
            import comtypes
            from comtypes import GUID

            uia = self._uia
            root = uia.GetRootElement()

            elements = []
            self._walk_win_com(uia, root, elements, 0, max_depth, pid)
            return elements
        except Exception:
            return []

    def _walk_win_com(self, uia, element, results, depth, max_depth, target_pid):
        """Recorre el arbol UIAutomation recursivamente."""
        if depth > max_depth or len(results) > 200:
            return
        try:
            name = element.CurrentName or ""
            control_type = element.CurrentControlType
            role = self._control_type_to_role(control_type)

            # Filtrar por PID si especificado
            el_pid = element.CurrentProcessId
            if target_pid and el_pid != target_pid:
                if depth > 0:
                    return

            # Obtener bounds
            rect = element.CurrentBoundingRectangle
            x, y = int(rect.left), int(rect.top)
            w, h = int(rect.right - rect.left), int(rect.bottom - rect.top)

            if w > 0 and h > 0 and (name or depth < 2):
                results.append(UIElement(
                    name=name,
                    role=role,
                    value=self._safe_get_value(element),
                    x=x, y=y, width=w, height=h,
                    is_enabled=bool(element.CurrentIsEnabled),
                    is_focused=bool(element.CurrentHasKeyboardFocus),
                    children_count=0,
                    automation_id=element.CurrentAutomationId or "",
                    class_name=element.CurrentClassName or "",
                    pid=el_pid,
                ).to_dict())

            # Recursion en hijos
            walker = self._uia.ControlViewWalker
            child = walker.GetFirstChildElement(element)
            while child:
                self._walk_win_com(self._uia, child, results, depth + 1, max_depth, target_pid)
                child = walker.GetNextSiblingElement(child)
        except Exception:
            pass

    def _safe_get_value(self, element) -> str:
        try:
            return element.CurrentValue or ""
        except Exception:
            return ""

    def _control_type_to_role(self, ct: int) -> str:
        """Mapea UIAutomation ControlType a role string."""
        roles = {
            50000: "button", 50001: "calendar", 50002: "checkbox",
            50003: "combobox", 50004: "edit", 50005: "hyperlink",
            50006: "image", 50007: "listitem", 50008: "list",
            50009: "menu", 50010: "menubar", 50011: "menuitem",
            50012: "progressbar", 50013: "radiobutton", 50014: "scrollbar",
            50015: "slider", 50016: "spinner", 50017: "statusbar",
            50018: "tab", 50019: "tabitem", 50020: "text",
            50021: "toolbar", 50022: "tooltip", 50023: "tree",
            50024: "treeitem", 50025: "custom", 50026: "group",
            50027: "thumb", 50028: "datagrid", 50029: "dataitem",
            50030: "document", 50031: "splitbutton", 50032: "window",
            50033: "pane", 50034: "header", 50035: "headeritem",
            50036: "table", 50037: "titlebar", 50038: "separator",
        }
        return roles.get(ct, f"control_{ct}")

    def _get_elements_win_ps(self, pid: Optional[int], max_depth: int) -> list[dict]:
        """Fallback: UIAutomation via PowerShell."""
        try:
            pid_filter = f"| Where-Object {{ $_.Current.ProcessId -eq {pid} }}" if pid else ""
            script = f"""
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$auto = [System.Windows.Automation.AutomationElement]::RootElement
$condition = [System.Windows.Automation.Condition]::TrueCondition
$elements = $auto.FindAll([System.Windows.Automation.TreeScope]::Descendants, $condition) {pid_filter}
$count = 0
foreach ($el in $elements) {{
    if ($count -ge 150) {{ break }}
    $rect = $el.Current.BoundingRectangle
    if ($rect.Width -gt 0 -and $rect.Height -gt 0 -and $el.Current.Name) {{
        $name = $el.Current.Name -replace '[\\r\\n]', ' '
        $role = $el.Current.ControlType.ProgrammaticName -replace 'ControlType\\.', ''
        Write-Output "$name|$role|$([int]$rect.X)|$([int]$rect.Y)|$([int]$rect.Width)|$([int]$rect.Height)|$($el.Current.IsEnabled)|$($el.Current.ProcessId)|$($el.Current.AutomationId)"
        $count++
    }}
}}
"""
            result = subprocess.run(
                ["powershell", "-command", script],
                capture_output=True, text=True, timeout=10
            )
            elements = []
            for line in result.stdout.strip().split("\n"):
                parts = line.split("|")
                if len(parts) >= 9:
                    elements.append(UIElement(
                        name=parts[0],
                        role=parts[1].lower(),
                        value="",
                        x=int(parts[2]), y=int(parts[3]),
                        width=int(parts[4]), height=int(parts[5]),
                        is_enabled=parts[6] == "True",
                        is_focused=False,
                        children_count=0,
                        pid=int(parts[7]) if parts[7].isdigit() else 0,
                        automation_id=parts[8] if len(parts) > 8 else "",
                    ).to_dict())
            return elements
        except Exception:
            return []

    # ─── macOS Implementation ───

    def _get_elements_mac(self, pid: Optional[int]) -> list[dict]:
        try:
            proc_filter = f'whose unix id is {pid}' if pid else 'whose visible is true'
            script = f'''
tell application "System Events"
    set output to ""
    repeat with proc in (every process {proc_filter})
        set procName to name of proc
        set procPID to unix id of proc
        try
            repeat with win in (every window of proc)
                set winName to name of win
                set winPos to position of win
                set winSize to size of win
                set output to output & procName & "|window|" & winName & "|" & (item 1 of winPos) & "|" & (item 2 of winPos) & "|" & (item 1 of winSize) & "|" & (item 2 of winSize) & "|" & procPID & linefeed
                try
                    repeat with btn in (every button of win)
                        set btnName to name of btn
                        set btnPos to position of btn
                        set btnSize to size of btn
                        set output to output & btnName & "|button||" & (item 1 of btnPos) & "|" & (item 2 of btnPos) & "|" & (item 1 of btnSize) & "|" & (item 2 of btnSize) & "|" & procPID & linefeed
                    end repeat
                end try
                try
                    repeat with tf in (every text field of win)
                        set tfName to name of tf
                        set tfVal to value of tf
                        set tfPos to position of tf
                        set tfSize to size of tf
                        set output to output & tfName & "|textfield|" & tfVal & "|" & (item 1 of tfPos) & "|" & (item 2 of tfPos) & "|" & (item 1 of tfSize) & "|" & (item 2 of tfSize) & "|" & procPID & linefeed
                    end repeat
                end try
            end repeat
        end try
    end repeat
    return output
end tell
'''
            result = subprocess.run(["osascript", "-e", script],
                                    capture_output=True, text=True, timeout=10)
            elements = []
            for line in result.stdout.strip().split("\n"):
                parts = line.split("|")
                if len(parts) >= 8:
                    elements.append(UIElement(
                        name=parts[0],
                        role=parts[1],
                        value=parts[2],
                        x=int(parts[3]) if parts[3].isdigit() else 0,
                        y=int(parts[4]) if parts[4].isdigit() else 0,
                        width=int(parts[5]) if parts[5].isdigit() else 0,
                        height=int(parts[6]) if parts[6].isdigit() else 0,
                        is_enabled=True,
                        is_focused=False,
                        children_count=0,
                        pid=int(parts[7]) if parts[7].isdigit() else 0,
                    ).to_dict())
            return elements
        except Exception:
            return []

    # ─── Linux Implementation ───

    def _get_elements_linux(self, pid: Optional[int]) -> list[dict]:
        """AT-SPI via gdbus (accesibilidad en Linux)."""
        try:
            # Listar aplicaciones AT-SPI accesibles
            result = subprocess.run(
                ["gdbus", "call", "--session",
                 "--dest", "org.a11y.atspi.Registry",
                 "--object-path", "/org/a11y/atspi/accessible/root",
                 "--method", "org.a11y.atspi.Accessible.GetChildren"],
                capture_output=True, text=True, timeout=5
            )
            # Parsing basico de gdbus output
            # AT-SPI es complejo, esta implementacion es un MVP
            elements = []
            # Fallback: usar xdotool para al menos obtener ventanas
            result = subprocess.run(
                ["xdotool", "search", "--onlyvisible", "--name", ""],
                capture_output=True, text=True, timeout=5
            )
            for wid in result.stdout.strip().split("\n")[:50]:
                if not wid.strip():
                    continue
                try:
                    name_result = subprocess.run(
                        ["xdotool", "getwindowname", wid],
                        capture_output=True, text=True, timeout=2
                    )
                    geo_result = subprocess.run(
                        ["xdotool", "getwindowgeometry", "--shell", wid],
                        capture_output=True, text=True, timeout=2
                    )
                    name = name_result.stdout.strip()
                    geo = {}
                    for line in geo_result.stdout.strip().split("\n"):
                        if "=" in line:
                            k, v = line.split("=", 1)
                            geo[k] = int(v) if v.isdigit() else v

                    elements.append(UIElement(
                        name=name,
                        role="window",
                        value="",
                        x=geo.get("X", 0),
                        y=geo.get("Y", 0),
                        width=geo.get("WIDTH", 0),
                        height=geo.get("HEIGHT", 0),
                        is_enabled=True,
                        is_focused=False,
                        children_count=0,
                    ).to_dict())
                except Exception:
                    continue
            return elements
        except Exception:
            return []

    @property
    def stats(self) -> dict:
        return {
            "platform": self._platform,
            "available": self._available,
            "backend": "comtypes" if (self._platform == "win32" and self._uia and self._uia != "powershell")
                       else "powershell" if (self._platform == "win32" and self._uia == "powershell")
                       else "applescript" if self._platform == "darwin"
                       else "atspi",
        }
