# ILUMINATY - Real-time visual perception + action for AI
# Zero-disk, RAM-only ring buffer architecture
# v1.0: 7 capas, 42+ acciones, percepcion + manos

__version__ = "1.0.0"

# Lazy imports - only load when accessed
def __getattr__(name):
    _map = {
        # Core (existing)
        "RingBuffer": (".ring_buffer", "RingBuffer"),
        "ScreenCapture": (".capture", "ScreenCapture"),
        "CaptureConfig": (".capture", "CaptureConfig"),
        "VisionIntelligence": (".vision", "VisionIntelligence"),
        "app": (".server", "app"),
        # Capa 1: OS Control
        "ActionBridge": (".actions", "ActionBridge"),
        "WindowManager": (".windows", "WindowManager"),
        "ClipboardManager": (".clipboard", "ClipboardManager"),
        "ProcessManager": (".process_mgr", "ProcessManager"),
        # Capa 2: UI Intelligence
        "UITree": (".ui_tree", "UITree"),
        # Capa 3: App Control
        "VSCodeBridge": (".vscode", "VSCodeBridge"),
        "TerminalManager": (".terminal", "TerminalManager"),
        "GitOps": (".git_ops", "GitOps"),
        # Capa 4: Web
        "BrowserBridge": (".browser", "BrowserBridge"),
        # Capa 5: File System
        "FileSystemSandbox": (".filesystem", "FileSystemSandbox"),
        # Capa 6: Brain
        "ActionResolver": (".resolver", "ActionResolver"),
        "IntentClassifier": (".intent", "IntentClassifier"),
        "TaskPlanner": (".planner", "TaskPlanner"),
        "ActionVerifier": (".verifier", "ActionVerifier"),
        "ErrorRecovery": (".recovery", "ErrorRecovery"),
        # Capa 7: Safety
        "SafetySystem": (".safety", "SafetySystem"),
        "AutonomyManager": (".autonomy", "AutonomyManager"),
        "AuditLog": (".audit", "AuditLog"),
    }
    if name in _map:
        module_path, attr = _map[name]
        import importlib
        mod = importlib.import_module(module_path, __package__)
        return getattr(mod, attr)
    raise AttributeError(f"module 'iluminaty' has no attribute {name!r}")

__all__ = [
    # Core
    "RingBuffer", "ScreenCapture", "CaptureConfig", "VisionIntelligence", "app",
    # Capa 1
    "ActionBridge", "WindowManager", "ClipboardManager", "ProcessManager",
    # Capa 2
    "UITree",
    # Capa 3
    "VSCodeBridge", "TerminalManager", "GitOps",
    # Capa 4
    "BrowserBridge",
    # Capa 5
    "FileSystemSandbox",
    # Capa 6
    "ActionResolver", "IntentClassifier", "TaskPlanner", "ActionVerifier", "ErrorRecovery",
    # Capa 7
    "SafetySystem", "AutonomyManager", "AuditLog",
    "__version__",
]
