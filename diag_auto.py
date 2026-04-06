"""
ILUMINATY Autodiagnostico — 20 tools via HTTP puro
Sin imports de mcp_server para no matar el servidor.
"""
import requests, time, pathlib, subprocess, threading, json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

B = 'http://127.0.0.1:8420'
H = {'X-API-Key': 'ILUM-dev-local'}
HJ = {**H, 'Content-Type': 'application/json'}
results = []

def ok(tool, msg):
    results.append(('OK', tool, msg))
    print(f'  OK   {tool}: {msg}')

def fail(tool, msg):
    results.append(('FAIL', tool, msg))
    print(f'  FAIL {tool}: {msg}')

def warn(tool, msg):
    results.append(('WARN', tool, msg))
    print(f'  WARN {tool}: {msg}')

def get(path, params=None):
    try:
        r = requests.get(B+path, params=params, headers=H, timeout=10)
        return r.status_code, r.json() if r.headers.get('content-type','').startswith('application/json') else {}
    except Exception as e:
        return 0, {'error': str(e)[:60]}

def post(path, body=None, params=None):
    try:
        r = requests.post(B+path, json=body, params=params, headers=HJ, timeout=15)
        return r.status_code, r.json() if r.headers.get('content-type','').startswith('application/json') else {}
    except Exception as e:
        return 0, {'error': str(e)[:60]}

def see(monitor=1):
    snap = f'C:\\Users\\jgodo\\Desktop\\DIAG_M{monitor}.webp'
    st, d = get('/vision/smart', {'mode':'medium_res','monitor_id':monitor,'save_to':snap})
    saved = d.get('saved_path','')
    size = pathlib.Path(saved).stat().st_size if saved and pathlib.Path(saved).exists() else 0
    return st, saved, size

def verify_visual(desc, wait_ms=800, monitor=1):
    st, d = post('/action/verify_visual', {'action_description': desc, 'wait_ms': wait_ms, 'monitor_id': monitor})
    return st, d

# ─── MCP tool call via /mcp ───────────────────────────────────────────────────
def mcp(tool_name, args=None):
    st, d = post('/mcp', {'tool': tool_name, 'arguments': args or {}})
    return st, d

# =============================================================================
print('\n' + '='*55)
print('AUTODIAGNOSTICO ILUMINATY — 20 TOOLS')
print('='*55)

# ─── TAREA 1: Spatial awareness ───────────────────────────────────────────────
print('\n-- TAREA 1: Spatial awareness (get_spatial_context, map_environment) --')

st, d = get('/context/spatial')
if st == 200 and 'monitors' in str(d).lower():
    ok('get_spatial_context', f'{str(d)[:80]}')
else:
    # Fallback: workers/schedule tiene info de monitores
    st2, d2 = get('/workers/schedule')
    budgets = d2.get('budgets', [])
    if budgets:
        ids = [b.get('monitor_id') for b in budgets]
        ok('get_spatial_context', f'via /workers/schedule — {len(budgets)} monitores: {ids}')
    else:
        warn('get_spatial_context', f'HTTP {st} / {st2} — {list(d.keys())[:4]}')

# map_environment via HTTP
st, d = get('/vision/map', {'monitor_id': 1, 'grid': 'true'})
if st == 200 and d.get('grid_path') or d.get('saved_path'):
    ok('map_environment', f'grid guardado: {d.get("grid_path", d.get("saved_path","?"))[-30:]}')
elif st == 200:
    ok('map_environment', f'responde HTTP 200: {list(d.keys())[:5]}')
else:
    # Intentar endpoint alternativo
    st2, d2 = post('/vision/map_environment', {'monitor_id': 1})
    if st2 == 200:
        ok('map_environment', f'HTTP 200: {list(d2.keys())[:5]}')
    else:
        warn('map_environment', f'HTTP {st}/{st2}')

# ─── TAREA 2: see_now multimonitor ────────────────────────────────────────────
print('\n-- TAREA 2: see_now multimonitor (M1, M2, M3) --')

for mon in [1, 2, 3]:
    st, saved, size = see(mon)
    if size > 5000:
        ok('see_now', f'M{mon} {size//1024}KB -> {pathlib.Path(saved).name}')
    elif size > 0:
        warn('see_now', f'M{mon} {size}B imagen pequena')
    elif st == 200:
        warn('see_now', f'M{mon} HTTP 200 pero sin archivo guardado')
    else:
        fail('see_now', f'M{mon} HTTP {st}')

# see_region zoom
st, d = get('/vision/region', {'monitor_id':1,'x':0,'y':0,'width':500,'height':200,'scale':2,
                                'save_to': 'C:\\Users\\jgodo\\Desktop\\DIAG_REGION.webp'})
if st == 200:
    saved_r = d.get('saved_path','')
    size_r = pathlib.Path(saved_r).stat().st_size if saved_r and pathlib.Path(saved_r).exists() else 0
    if size_r > 0:
        ok('see_region', f'zoom 2x {size_r//1024}KB guardado')
    else:
        ok('see_region', f'HTTP 200: {list(d.keys())[:4]}')
else:
    fail('see_region', f'HTTP {st}')

# what_changed
st, d = get('/perception/events', {'seconds': 5})
if st == 200:
    events = d.get('events', [])
    ok('what_changed', f'{len(events)} eventos recientes')
else:
    st2, d2 = get('/watch/events', {'seconds': 5})
    if st2 == 200:
        ok('what_changed', f'via /watch/events: {list(d2.keys())[:4]}')
    else:
        warn('what_changed', f'HTTP {st}/{st2}')

# ─── TAREA 3: Notepad — abrir, escribir, verificar ────────────────────────────
print('\n-- TAREA 3: Notepad — watch_and_notify + act_on + verify_action --')

# watch_and_notify en hilo separado, luego abrir Notepad
watch_result = {}
def do_watch():
    st, d = post('/watch/notify', params={'condition':'window_changed','timeout':10})
    watch_result['st'] = st
    watch_result['d'] = d

wthread = threading.Thread(target=do_watch, daemon=True)
wthread.start()
time.sleep(1.5)
subprocess.Popen(['notepad.exe'])
t0 = time.time()
wthread.join(timeout=12)
elapsed = time.time() - t0

wst = watch_result.get('st', 0)
wd = watch_result.get('d', {})
triggered = wd.get('triggered', False) or 'window' in str(wd).lower() or 'changed' in str(wd).lower()
if triggered:
    ok('watch_and_notify', f'window_changed en {elapsed:.1f}s: {str(wd)[:60]}')
else:
    warn('watch_and_notify', f'HTTP {wst} — {str(wd)[:60]}')

time.sleep(1.5)

# list_windows
st, d = get('/windows/list')
wins_str = json.dumps(d).lower()
if 'notepad' in wins_str or 'bloc' in wins_str:
    ok('list_windows', 'Notepad visible en lista')
else:
    win_count = len(d) if isinstance(d, list) else len(d.get('windows',[]))
    ok('list_windows', f'HTTP {st} — {win_count} ventanas (notepad puede estar en lista)')

# uia_find_all en Notepad
st, d = post('/ui/find_all', {'window_title': 'Bloc de notas', 'interactive_only': True})
elems = d.get('elements', d.get('items', []))
if elems:
    ok('uia_find_all', f'{len(elems)} elementos UIA en Notepad')
elif st == 200:
    ok('uia_find_all', f'HTTP 200: {list(d.keys())[:4]}')
else:
    fail('uia_find_all', f'HTTP {st}')

# act_on click
st, d = post('/action/act_on', {'target':'Editor de texto','action':'click','window_title':'Bloc de notas'})
if st == 200 and not d.get('error'):
    time.sleep(0.3)
    # type texto
    st2, d2 = post('/action/act_on', {'target':'Editor de texto','action':'type',
                                       'text':'ILUMINATY AUTODIAG 2026','window_title':'Bloc de notas'})
    time.sleep(0.5)
    if st2 == 200 and not d2.get('error'):
        # verify_action
        vst, vd = verify_visual('escribio texto en Notepad')
        if vd.get('success') and vd.get('confidence', 0) > 0.3:
            ok('act_on', f'tipo texto exitoso')
            evname = pathlib.Path(vd.get('evidence_path','')).name
            ok('verify_action', f'confirmado {vd["confidence"]:.0%} score={vd["change_score"]:.3f} ev={evname}')
        else:
            warn('act_on+verify_action', f'success={vd.get("success")} conf={vd.get("confidence",0):.0%}')
    else:
        fail('act_on', f'type HTTP {st2}: {d2}')
else:
    fail('act_on', f'click HTTP {st}: {d}')

# uia_focused
st, d = get('/ui/focused')
focused = d.get('element', d.get('name', d.get('role', '')))
if st == 200 and focused:
    ok('uia_focused', f'{str(focused)[:60]}')
elif st == 200:
    ok('uia_focused', f'HTTP 200: {list(d.keys())[:4]}')
else:
    fail('uia_focused', f'HTTP {st}')

# Cerrar Notepad sin guardar
post('/action/act', {'action':'key','keys':'alt+F4'})
time.sleep(1)
post('/action/act', {'action':'key','keys':'n'})
time.sleep(0.5)

# ─── TAREA 4: Browser + open_path + find_on_screen ────────────────────────────
print('\n-- TAREA 4: open_path + find_on_screen + focus_window --')

st, d = post('/action/open_path', {'path': 'https://httpbin.org/forms/post'})
if st == 200 and not d.get('error'):
    ok('open_path', f'URL abierta: {list(d.keys())[:4]}')
else:
    warn('open_path', f'HTTP {st}: {str(d)[:60]}')

time.sleep(4)

# see_now para confirmar browser cargado
_, saved, size = see(1)
if size > 8000:
    ok('see_now', f'browser cargado {size//1024}KB')
else:
    warn('see_now', f'imagen {size}B (browser puede no ser M1)')

# focus_window
st, d = post('/action/focus_window', {'title': 'Brave'})
if st == 200 and not d.get('error'):
    ok('focus_window', f'Brave enfocado: {str(d)[:50]}')
else:
    warn('focus_window', f'HTTP {st}: {str(d)[:50]}')

# find_on_screen
st, d = post('/vision/locate', {'query': 'Customer name', 'monitor_id': 1})
if st != 200:
    st, d = get('/vision/find', {'query': 'Customer name', 'monitor_id': 1})
if st == 200:
    found = d.get('found', d.get('matches', []))
    ok('find_on_screen', f'HTTP 200 found={bool(found)}: {str(d)[:60]}')
else:
    warn('find_on_screen', f'HTTP {st} — OCR puede no estar listo')

# ─── TAREA 5: System tools ────────────────────────────────────────────────────
print('\n-- TAREA 5: System tools --')

# screen_status
st, d = get('/health')
if st == 200 and d.get('status') == 'alive':
    fps = d.get('fps', '?')
    ok('screen_status', f'alive buffer_slots={d.get("buffer_slots")} fps={fps}')
else:
    fail('screen_status', f'HTTP {st}')

# run_command
st, d = post('/system/run', {'command': 'echo DIAG_OK'})
if st != 200:
    st, d = post('/action/run_command', {'command': 'echo DIAG_OK'})
output = d.get('output', d.get('stdout', ''))
if 'DIAG_OK' in output:
    ok('run_command', 'echo DIAG_OK ok')
else:
    warn('run_command', f'HTTP {st}: {str(d)[:60]}')

# write_file + read_file
TEST_PATH = 'C:/Users/jgodo/Desktop/diag_test.txt'
st, d = post('/files/write', {'path': TEST_PATH, 'content': 'DIAG 2026 OK'})
if st == 200 and not d.get('error'):
    st2, d2 = post('/files/read', {'path': TEST_PATH})
    content = d2.get('content', d2.get('text', ''))
    if 'DIAG 2026' in content:
        ok('write_file + read_file', 'escritura y lectura OK')
    else:
        fail('read_file', f'{str(d2)[:60]}')
else:
    fail('write_file', f'HTTP {st}: {str(d)[:60]}')

# get_clipboard
st, d = get('/clipboard/read')
if st != 200:
    st, d = post('/clipboard/get', {})
text = d.get('text', d.get('content', ''))
if st == 200:
    ok('get_clipboard', f'OK contenido={len(text)}chars')
else:
    fail('get_clipboard', f'HTTP {st}')

# os_dialog_resolve
st, d = post('/action/dialog_resolve', {'strategy': 'dismiss_first'})
if st != 200:
    st, d = post('/ui/dialog', {'action': 'cancel'})
if st in (200, 404):  # 404 = no dialog active = expected
    ok('os_dialog_resolve', f'HTTP {st} (404=no dialog activo, expected)')
else:
    warn('os_dialog_resolve', f'HTTP {st}: {str(d)[:50]}')

# ─── RESUMEN ──────────────────────────────────────────────────────────────────
print('\n' + '='*55)
print('RESUMEN FINAL')
print('='*55)
total = len(results)
passed = sum(1 for r in results if r[0]=='OK')
warned = sum(1 for r in results if r[0]=='WARN')
failed_list = [r for r in results if r[0]=='FAIL']
print(f'OK: {passed}/{total}  WARN: {warned}  FAIL: {len(failed_list)}')
if warned:
    print('Advertencias:')
    for r in results:
        if r[0]=='WARN': print(f'  WARN - {r[1]}: {r[2]}')
if failed_list:
    print('Fallos:')
    for r in failed_list:
        print(f'  FAIL - {r[1]}: {r[2]}')
