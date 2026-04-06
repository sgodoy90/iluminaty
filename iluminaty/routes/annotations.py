"""Route module — annotations."""
from __future__ import annotations
from typing import Optional
import asyncio
import base64
import io as _io
import json
import logging
import os
import time

from fastapi import APIRouter, Query, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel

# _state and helpers are resolved at import time via server module globals
import iluminaty.server as _srv

router = APIRouter()

def _get_state():
    return _srv._state

def _auth(k):
    return _srv._check_auth(k)

# ─── Annotations ───

@router.post("/annotations/add")
async def add_annotation(
    type: str = Query(..., description="circle, rect, arrow, text, freehand"),
    x: int = Query(...),
    y: int = Query(...),
    width: int = Query(50),
    height: int = Query(50),
    color: str = Query("#FF0000"),
    thickness: int = Query(3),
    text: str = Query(""),
    x_api_key: Optional[str] = Header(None),
):
    """
    Agrega una anotacion visual (lapiz/marcador).
    La IA vera el overlay dibujado + la descripcion textual.
    Tipos: circle, rect, arrow, text, freehand
    """
    _srv._check_auth(x_api_key)
    if not _srv._state.vision:
        raise HTTPException(503, "Not initialized")

    import uuid
    ann = Annotation(
        id=str(uuid.uuid4())[:8],
        type=type,
        x=x, y=y,
        width=width, height=height,
        color=color,
        thickness=thickness,
        text=text,
    )
    _srv._state.vision.annotations.add(ann)
    return {"id": ann.id, "type": type, "position": f"({x},{y})", "status": "added"}


@router.get("/annotations/list")
async def list_annotations(x_api_key: Optional[str] = Header(None)):
    """Lista todas las anotaciones activas."""
    _srv._check_auth(x_api_key)
    if not _srv._state.vision:
        raise HTTPException(503, "Not initialized")
    return {
        "count": len(_srv._state.vision.annotations.annotations),
        "annotations": _srv._state.vision.annotations.to_description(),
    }


@router.delete("/annotations/{annotation_id}")
async def remove_annotation(annotation_id: str, x_api_key: Optional[str] = Header(None)):
    """Elimina una anotacion por ID."""
    _srv._check_auth(x_api_key)
    if not _srv._state.vision:
        raise HTTPException(503, "Not initialized")
    removed = _srv._state.vision.annotations.remove(annotation_id)
    return {"removed": removed, "id": annotation_id}


@router.post("/annotations/clear")
async def clear_annotations(x_api_key: Optional[str] = Header(None)):
    """Borra todas las anotaciones."""
    _srv._check_auth(x_api_key)
    if not _srv._state.vision:
        raise HTTPException(503, "Not initialized")
    _srv._state.vision.annotations.clear()
    return {"status": "cleared"}


@router.get("/frame/annotated")
async def frame_annotated(x_api_key: Optional[str] = Header(None)):
    """Frame actual con las anotaciones dibujadas encima. Devuelve JPEG."""
    _srv._check_auth(x_api_key)
    if not _srv._state.buffer or not _srv._state.vision:
        raise HTTPException(503, "Not initialized")

    slot = _srv._state.buffer.get_latest()
    if not slot:
        raise HTTPException(404, "No frames in buffer")

    rendered = _srv._state.vision.annotations.render_overlay(slot.frame_bytes)
    return Response(content=rendered, media_type="image/jpeg")


