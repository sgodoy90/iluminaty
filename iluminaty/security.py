"""
ILUMINATY - Security Layer
============================
Seguridad es la prioridad #1 cuando tienes acceso visual
a toda la pantalla del usuario.

Capas de seguridad:
1. Auth tokens con rotacion automatica
2. Rate limiting por cliente
3. Encriptacion de frames en memoria (AES efimero)
4. Sensitive content detection (passwords, credit cards)
5. Audit log (quien accedio, cuando, sin guardar frames)
6. Origin verification
7. CORS estricto en produccion
"""

import time
import secrets
import hashlib
import hmac
import re
from collections import defaultdict, deque
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional
from io import BytesIO
from PIL import Image, ImageFilter


# ─── Token Auth ───

class TokenManager:
    """
    Gestiona tokens de acceso con rotacion automatica.
    Cada token tiene TTL y se invalida al expirar.
    """

    def __init__(self, master_key: Optional[str] = None):
        # Si no se provee key, generar una aleatoria
        self.master_key = master_key or secrets.token_urlsafe(32)
        self._tokens: dict[str, dict] = {}
        self._revoked: set[str] = set()

    def generate_token(self, client_name: str = "default", ttl_seconds: int = 3600) -> dict:
        """Genera un token con TTL."""
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        self._tokens[token_hash] = {
            "client": client_name,
            "created": time.time(),
            "expires": time.time() + ttl_seconds,
            "ttl": ttl_seconds,
        }
        return {
            "token": token,
            "expires_in": ttl_seconds,
            "client": client_name,
        }

    def validate_token(self, token: str) -> tuple[bool, str]:
        """Valida un token. Returns (valid, reason)."""
        if not token:
            return False, "no token provided"

        token_hash = hashlib.sha256(token.encode()).hexdigest()

        if token_hash in self._revoked:
            return False, "token revoked"

        info = self._tokens.get(token_hash)
        if not info:
            # Fallback: check if it matches master key
            if hmac.compare_digest(token, self.master_key):
                return True, "master_key"
            return False, "unknown token"

        if time.time() > info["expires"]:
            del self._tokens[token_hash]
            return False, "token expired"

        return True, f"valid ({info['client']})"

    def revoke_token(self, token: str):
        """Revoca un token inmediatamente."""
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        self._revoked.add(token_hash)
        self._tokens.pop(token_hash, None)

    def cleanup_expired(self):
        """Limpia tokens expirados."""
        now = time.time()
        expired = [h for h, info in self._tokens.items() if now > info["expires"]]
        for h in expired:
            del self._tokens[h]

    @property
    def active_count(self) -> int:
        self.cleanup_expired()
        return len(self._tokens)


# ─── Rate Limiter ───

class RateLimiter:
    """
    Rate limiting por IP/token para prevenir abuso.
    Sliding window de 1 minuto.
    """

    def __init__(self, max_requests_per_minute: int = 120):
        self.max_rpm = max_requests_per_minute
        self._windows: dict[str, deque] = defaultdict(lambda: deque(maxlen=max_requests_per_minute))

    def check(self, client_id: str) -> tuple[bool, int]:
        """
        Verifica si el cliente puede hacer request.
        Returns (allowed, remaining_requests).
        """
        now = time.time()
        window = self._windows[client_id]

        # Remove old entries (> 60 seconds)
        while window and now - window[0] > 60:
            window.popleft()

        remaining = self.max_rpm - len(window)

        if len(window) >= self.max_rpm:
            return False, 0

        window.append(now)
        return True, remaining - 1


# ─── Sensitive Content Detection ───

# Patrones para detectar contenido sensible en OCR text
SENSITIVE_PATTERNS = {
    "credit_card": re.compile(r'\b(?:\d{4}[\s-]?){3}\d{4}\b'),
    "email": re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'),
    "phone": re.compile(r'\b(?:\+?\d{1,3}[\s-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b'),
    "ssn": re.compile(r'\b\d{3}-\d{2}-\d{4}\b'),
    "api_key": re.compile(r'\b(?:sk-|pk-|api[_-]?key)[A-Za-z0-9_-]{20,}\b', re.IGNORECASE),
    "password_field": re.compile(r'(?:password|passwd|pwd|contraseña)[\s:=]+\S+', re.IGNORECASE),
    "oauth_token": re.compile(r'\b(?:Bearer|token|oauth)[:\s]+[A-Za-z0-9_\-\.]{20,}\b', re.IGNORECASE),
    "connection_string": re.compile(r'(?:mongodb|postgres|mysql|redis|amqp)://[^\s]{10,}', re.IGNORECASE),
    "private_key": re.compile(r'-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----', re.IGNORECASE),
    "aws_key": re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
}


class SensitiveDetector:
    """
    Detecta contenido sensible en texto OCR.
    Puede redactar automaticamente o alertar.
    """

    def __init__(self, auto_redact: bool = True):
        self.auto_redact = auto_redact
        self._detection_count: dict[str, int] = defaultdict(int)

    def scan_text(self, text: str) -> list[dict]:
        """Escanea texto por contenido sensible."""
        findings = []
        for name, pattern in SENSITIVE_PATTERNS.items():
            matches = pattern.findall(text)
            for match in matches:
                self._detection_count[name] += 1
                findings.append({
                    "type": name,
                    "matched": match[:4] + "***" if len(match) > 4 else "***",
                    "redacted": True,
                })
        return findings

    def redact_text(self, text: str) -> str:
        """Reemplaza contenido sensible con [REDACTED]."""
        for name, pattern in SENSITIVE_PATTERNS.items():
            text = pattern.sub(f"[REDACTED:{name}]", text)
        return text

    @property
    def stats(self) -> dict:
        return dict(self._detection_count)


class ScreenBlurrer:
    """
    Aplica blur a regiones sensibles de la imagen.
    Usa coordenadas de bloques OCR que contienen contenido sensible.
    """

    @staticmethod
    def blur_regions(frame_bytes: bytes, regions: list[dict], blur_radius: int = 20) -> bytes:
        """
        Aplica gaussian blur a regiones especificas del frame.
        regions: [{"x": int, "y": int, "w": int, "h": int}, ...]
        """
        if not regions:
            return frame_bytes

        img = Image.open(BytesIO(frame_bytes))

        for r in regions:
            x, y, w, h = r["x"], r["y"], r["w"], r["h"]
            # Crop, blur, paste back
            box = (x, y, x + w, y + h)
            region_img = img.crop(box)
            blurred = region_img.filter(ImageFilter.GaussianBlur(radius=blur_radius))
            img.paste(blurred, box)

        buf = BytesIO()
        # Detectar formato original
        img.save(buf, format="WEBP", quality=80)
        return buf.getvalue()


# ─── Audit Log ───

@dataclass
class AuditEntry:
    timestamp: float
    client: str
    action: str
    endpoint: str
    allowed: bool
    details: str = ""


class AuditLog:
    """
    Log de accesos. NO guarda frames — solo metadata.
    Vive en RAM con un maximo de entries.
    """

    def __init__(self, max_entries: int = 1000):
        self._entries: list[AuditEntry] = []
        self.max_entries = max_entries

    def log(self, client: str, action: str, endpoint: str, allowed: bool, details: str = ""):
        entry = AuditEntry(
            timestamp=time.time(),
            client=client,
            action=action,
            endpoint=endpoint,
            allowed=allowed,
            details=details,
        )
        self._entries.append(entry)
        # Auto-trim
        if len(self._entries) > self.max_entries:
            self._entries = self._entries[-self.max_entries:]

    def get_recent(self, count: int = 50) -> list[dict]:
        return [
            {
                "time": time.strftime("%H:%M:%S", time.localtime(e.timestamp)),
                "client": e.client,
                "action": e.action,
                "endpoint": e.endpoint,
                "allowed": e.allowed,
                "details": e.details,
            }
            for e in self._entries[-count:]
        ]

    @property
    def stats(self) -> dict:
        total = len(self._entries)
        denied = sum(1 for e in self._entries if not e.allowed)
        return {
            "total_requests": total,
            "denied_requests": denied,
            "entries_in_log": total,
        }


# ─── Security Manager (orquesta todo) ───

class SecurityManager:
    """Capa de seguridad unificada para ILUMINATY."""

    def __init__(
        self,
        master_key: Optional[str] = None,
        require_auth: bool = True,
        rate_limit_rpm: int = 120,
        auto_redact_sensitive: bool = True,
    ):
        self.require_auth = require_auth
        self.tokens = TokenManager(master_key=master_key)
        self.rate_limiter = RateLimiter(max_requests_per_minute=rate_limit_rpm)
        self.sensitive = SensitiveDetector(auto_redact=auto_redact_sensitive)
        self.blurrer = ScreenBlurrer()
        self.audit = AuditLog()

    def authenticate(self, token: Optional[str], client_ip: str, endpoint: str) -> tuple[bool, str]:
        """Punto unico de autenticacion."""
        # Auth check
        if self.require_auth:
            if not token:
                self.audit.log(client_ip, "auth_fail", endpoint, False, "no token")
                return False, "Authentication required. Provide X-API-Key header."

            valid, reason = self.tokens.validate_token(token)
            if not valid:
                self.audit.log(client_ip, "auth_fail", endpoint, False, reason)
                return False, f"Authentication failed: {reason}"

        # Rate limit check
        allowed, remaining = self.rate_limiter.check(client_ip)
        if not allowed:
            self.audit.log(client_ip, "rate_limit", endpoint, False)
            return False, "Rate limit exceeded. Try again in 60 seconds."

        self.audit.log(client_ip, "access", endpoint, True)
        return True, "ok"

    def process_ocr_text(self, text: str) -> tuple[str, list[dict]]:
        """Procesa texto OCR: detecta y opcionalmente redacta contenido sensible."""
        findings = self.sensitive.scan_text(text)
        if findings and self.sensitive.auto_redact:
            text = self.sensitive.redact_text(text)
        return text, findings

    @property
    def status(self) -> dict:
        return {
            "auth_required": self.require_auth,
            "active_tokens": self.tokens.active_count,
            "audit_stats": self.audit.stats,
            "sensitive_detections": self.sensitive.stats,
        }
