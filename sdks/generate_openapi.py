"""
Generate OpenAPI spec from ILUMINATY FastAPI app.
Run: python generate_openapi.py > openapi.json
"""
import json
import sys
sys.path.insert(0, ".")

# Minimal init to get the app without starting capture
from iluminaty.server import app

spec = app.openapi()
spec["info"]["title"] = "ILUMINATY API"
spec["info"]["description"] = "Real-time visual perception for AI. Zero-disk, RAM-only."
spec["info"]["version"] = "0.5.0"
spec["info"]["contact"] = {"name": "Godo", "url": "https://github.com/sgodoy90/iluminaty"}

print(json.dumps(spec, indent=2))
