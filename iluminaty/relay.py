"""
ILUMINATY - F15: Cloud Relay
===============================
Ver tu PC desde cualquier lugar. E2E encrypted.

Arquitectura:
  PC (ILUMINATY) ──encrypted──→ Relay Server ──encrypted──→ Client (phone/browser)

El relay server NUNCA ve los frames. Solo reenvía bytes cifrados.
La key de encriptación solo la tienen PC y Client.

Componentes:
  1. RelayPublisher  - corre en tu PC, envía frames al relay
  2. RelayServer     - corre en la nube, reenvía bytes
  3. RelaySubscriber - corre en tu phone/browser, recibe frames

Para desarrollo/testing, el relay server corre en localhost.
Para producción, se despliega en Cloudflare Workers / Fly.io / Railway.
"""

import json
import time
import base64
import hashlib
import secrets
import asyncio
from typing import Optional, Callable
from dataclasses import dataclass


@dataclass
class RelayConfig:
    """Configuración del relay."""
    relay_url: str = "ws://127.0.0.1:8421"
    room_id: str = ""
    encryption_key: str = ""
    max_fps: float = 2.0

    def generate_room(self) -> str:
        """Genera un room ID único."""
        self.room_id = secrets.token_urlsafe(16)
        return self.room_id

    def generate_key(self) -> str:
        """Genera una key de encriptación."""
        self.encryption_key = secrets.token_urlsafe(32)
        return self.encryption_key

    def share_link(self) -> str:
        """Genera link para compartir (room + key encoded)."""
        return f"{self.relay_url}?room={self.room_id}&key={self.encryption_key}"


class SimpleEncryption:
    """
    Encriptación simple con XOR + key derivation.
    Para producción usar AES-256-GCM via cryptography library.
    Para el MVP, XOR con key derivada es suficiente.
    """

    def __init__(self, key: str):
        # Derive a key from the passphrase
        self._key = hashlib.sha256(key.encode()).digest()

    def encrypt(self, data: bytes) -> bytes:
        """Encrypt data with XOR (MVP). Replace with AES for production."""
        key_repeated = (self._key * (len(data) // len(self._key) + 1))[:len(data)]
        return bytes(a ^ b for a, b in zip(data, key_repeated))

    def decrypt(self, data: bytes) -> bytes:
        """Decrypt (XOR is symmetric)."""
        return self.encrypt(data)  # XOR is its own inverse


class RelayPublisher:
    """
    Publica frames al relay server desde tu PC.
    Los frames se encriptan ANTES de salir de tu máquina.
    """

    def __init__(self, config: RelayConfig, api_base: str = "http://127.0.0.1:8420"):
        self.config = config
        self.api_base = api_base
        self._crypto = SimpleEncryption(config.encryption_key) if config.encryption_key else None
        self._running = False
        self._frames_sent = 0

    async def start(self):
        """Conecta al relay y empieza a publicar frames."""
        try:
            import websockets
        except ImportError:
            print("[iluminaty] relay requires: pip install websockets")
            return

        self._running = True
        url = f"{self.config.relay_url}/publish?room={self.config.room_id}"

        try:
            async with websockets.connect(url) as ws:
                print(f"[iluminaty] relay connected: {self.config.room_id}")
                while self._running:
                    try:
                        # Get latest frame from ILUMINATY API
                        import urllib.request
                        req = urllib.request.Request(f"{self.api_base}/frame/latest")
                        with urllib.request.urlopen(req, timeout=5) as resp:
                            frame_bytes = resp.read()

                        # Encrypt
                        if self._crypto:
                            frame_bytes = self._crypto.encrypt(frame_bytes)

                        # Send
                        await ws.send(frame_bytes)
                        self._frames_sent += 1

                    except Exception as e:
                        pass  # Frame capture failed, skip

                    await asyncio.sleep(1.0 / self.config.max_fps)

        except Exception as e:
            print(f"[iluminaty] relay error: {e}")

    def stop(self):
        self._running = False

    @property
    def stats(self) -> dict:
        return {
            "room": self.config.room_id,
            "frames_sent": self._frames_sent,
            "running": self._running,
            "encrypted": self._crypto is not None,
        }


class RelaySubscriber:
    """
    Recibe frames del relay server (desde tu phone/browser).
    Desencripta los frames recibidos.
    """

    def __init__(self, config: RelayConfig):
        self.config = config
        self._crypto = SimpleEncryption(config.encryption_key) if config.encryption_key else None
        self._running = False
        self._frames_received = 0
        self._on_frame: Optional[Callable] = None

    def on_frame(self, callback: Callable):
        """Registra callback para frames recibidos."""
        self._on_frame = callback

    async def start(self):
        """Conecta al relay y empieza a recibir frames."""
        try:
            import websockets
        except ImportError:
            print("[iluminaty] relay requires: pip install websockets")
            return

        self._running = True
        url = f"{self.config.relay_url}/subscribe?room={self.config.room_id}"

        try:
            async with websockets.connect(url) as ws:
                print(f"[iluminaty] subscribed to room: {self.config.room_id}")
                while self._running:
                    try:
                        data = await ws.recv()
                        if isinstance(data, bytes):
                            # Decrypt
                            if self._crypto:
                                data = self._crypto.decrypt(data)

                            self._frames_received += 1

                            if self._on_frame:
                                self._on_frame(data)

                    except Exception:
                        break

        except Exception as e:
            print(f"[iluminaty] subscribe error: {e}")

    def stop(self):
        self._running = False


class RelayServer:
    """
    Relay server minimo. Reenvía bytes entre publisher y subscribers.
    NO puede ver el contenido (está encriptado).
    
    Para producción, usar Cloudflare Workers + Durable Objects
    o un server en Fly.io/Railway.
    
    Este server es para desarrollo/testing local.
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 8421):
        self.host = host
        self.port = port
        self._rooms: dict[str, dict] = {}  # room_id -> {"publishers": set, "subscribers": set}

    async def handle_connection(self, websocket, path: str):
        """Handle WebSocket connection."""
        import urllib.parse
        parsed = urllib.parse.urlparse(path)
        params = urllib.parse.parse_qs(parsed.query)
        room_id = params.get("room", [""])[0]
        role = "publisher" if "/publish" in path else "subscriber"

        if not room_id:
            await websocket.close(1008, "room parameter required")
            return

        # Init room
        if room_id not in self._rooms:
            self._rooms[room_id] = {"publishers": set(), "subscribers": set()}

        room = self._rooms[room_id]
        room[f"{role}s"].add(websocket)

        try:
            if role == "publisher":
                # Receive frames and broadcast to subscribers
                async for message in websocket:
                    # Forward to all subscribers (we can't read it, it's encrypted)
                    dead = set()
                    for sub in room["subscribers"]:
                        try:
                            await sub.send(message)
                        except Exception:
                            dead.add(sub)
                    room["subscribers"] -= dead

            else:
                # Subscriber just waits
                async for _ in websocket:
                    pass

        except Exception:
            pass
        finally:
            room[f"{role}s"].discard(websocket)
            # Cleanup empty rooms
            if not room["publishers"] and not room["subscribers"]:
                self._rooms.pop(room_id, None)

    async def start(self):
        """Start the relay server."""
        try:
            import websockets
            server = await websockets.serve(
                self.handle_connection, self.host, self.port
            )
            print(f"[iluminaty] relay server running on ws://{self.host}:{self.port}")
            await server.wait_closed()
        except ImportError:
            print("[iluminaty] relay requires: pip install websockets")

    @property
    def stats(self) -> dict:
        total_pubs = sum(len(r["publishers"]) for r in self._rooms.values())
        total_subs = sum(len(r["subscribers"]) for r in self._rooms.values())
        return {
            "rooms": len(self._rooms),
            "publishers": total_pubs,
            "subscribers": total_subs,
        }
