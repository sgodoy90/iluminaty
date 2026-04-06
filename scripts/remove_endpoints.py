"""
Elimina bloques de endpoints @app.X de server.py para módulos eliminados en S04.
Estrategia: leer todas las líneas, encontrar bloques @app.X de rutas muertas,
marcar el bloque completo (decorador + función) y eliminar.
"""
import re, sys, pathlib, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

SERVER = pathlib.Path('iluminaty/server.py')
lines = SERVER.read_text(encoding='utf-8').splitlines(keepends=True)

# Rutas a eliminar — todos sus endpoints
DEAD_PREFIXES = [
    '/context/',
    '/agents', '/agents/',
    '/plugins',
    '/memory/',
    '/ai/router',
    '/memory/save', '/memory/load', '/memory/prompt', '/memory/stats',
    '/operating/mode',
    '/autonomy/',
    '/vscode/',
    '/terminal/',
    '/git/',
    '/browser/tabs', '/browser/navigate', '/browser/new_tab',
    '/session/',
    '/planner/',
]

# También estos exactos
DEAD_EXACT = {
    '/operating/mode',
    '/autonomy/level',
    '/plugins',
    '/ai/router/stats',
}

def is_dead_route(route: str) -> bool:
    if route in DEAD_EXACT:
        return True
    for p in DEAD_PREFIXES:
        if route.startswith(p):
            return True
    return False

# Encuentra inicio de cada bloque @app.X
app_decorator_re = re.compile(r'^@app\.(get|post|put|delete|websocket|patch)\(')
async_def_re = re.compile(r'^async def |^def ')

# Construir lista de bloques a eliminar
# Un bloque = [línea @app.X, ..., hasta siguiente @app.X o fin de sección]
def find_blocks_to_remove(lines):
    remove_ranges = []  # (start, end) inclusive
    i = 0
    while i < len(lines):
        line = lines[i]
        m = app_decorator_re.match(line)
        if m:
            # Extraer la ruta
            route_m = re.search(r'\("([^"]+)"', line)
            if not route_m:
                i += 1
                continue
            route = route_m.group(1)
            if not is_dead_route(route):
                i += 1
                continue
            
            # Marcar inicio del bloque (puede haber docstring o comentarios antes)
            # Retroceder para incluir comentarios de sección justo antes
            block_start = i
            # No retrocedemos — solo el decorador y la función
            
            # Avanzar hasta encontrar el fin de la función
            # Primero encontrar la línea `async def` o `def`
            j = i + 1
            while j < len(lines) and not async_def_re.match(lines[j]):
                j += 1
            # Ahora j apunta al def. Avanzar hasta el próximo bloque a nivel 0
            j += 1
            while j < len(lines):
                l = lines[j]
                # Nueva función/clase/decorador/separador a nivel 0 = fin
                if (app_decorator_re.match(l) or
                    async_def_re.match(l) or
                    re.match(r'^class ', l) or
                    re.match(r'^# [─═]', l)):
                    break
                j += 1
            
            # j apunta al primer línea del siguiente bloque
            remove_ranges.append((block_start, j - 1))
            i = j
        else:
            i += 1
    return remove_ranges

blocks = find_blocks_to_remove(lines)
print(f'Bloques a eliminar: {len(blocks)}')

total_lines = 0
for start, end in sorted(blocks, reverse=True):
    route_line = lines[start].strip()[:60]
    count = end - start + 1
    total_lines += count
    print(f'  [{start+1}-{end+1}] ({count}L) {route_line}')

print(f'\nTotal líneas a eliminar: {total_lines}')

# Aplicar eliminaciones (en reversa para no desplazar índices)
new_lines = list(lines)
for start, end in sorted(blocks, reverse=True):
    del new_lines[start:end+1]

result = ''.join(new_lines)
print(f'\nAntes: {len(lines)} líneas')
print(f'Después: {len(new_lines)} líneas')
print(f'Eliminadas: {len(lines) - len(new_lines)} líneas')

# Verificar sintaxis
import ast
try:
    ast.parse(result)
    print('Sintaxis: OK')
except SyntaxError as e:
    print(f'SyntaxError: {e}')
    sys.exit(1)

# Guardar
SERVER.write_text(result, encoding='utf-8')
print('Guardado.')
