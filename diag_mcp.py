"""
Diagnóstico de tools MCP-only via JSON-RPC stdio.
Arranca mcp_server.py como subprocess y llama tools directamente.
"""
import subprocess, json, sys, io, time, threading, pathlib, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

PYTHON = str(pathlib.Path(__file__).parent / '.venv312/Scripts/python.exe')
MCP_SCRIPT = str(pathlib.Path(__file__).parent / 'run_mcp.py')
ENV = {**os.environ, 'ILUMINATY_API_KEY': 'ILUM-dev-local',
       'ILUMINATY_BASE_URL': 'http://127.0.0.1:8420',
       'ILUMINATY_VISION_MODE': 'medium_res'}

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


class MCPClient:
    def __init__(self):
        self.proc = subprocess.Popen(
            [PYTHON, MCP_SCRIPT],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=ENV, text=True, bufsize=1
        )
        self._id = 0
        self._lock = threading.Lock()
        # Initialize
        self._send({'jsonrpc':'2.0','id':0,'method':'initialize',
                    'params':{'protocolVersion':'2024-11-05',
                              'capabilities':{},'clientInfo':{'name':'diag','version':'1'}}})
        resp = self._read(timeout=10)
        if not resp:
            raise RuntimeError('MCP init timeout')
        self._send({'jsonrpc':'2.0','method':'notifications/initialized','params':{}})
        time.sleep(0.5)

    def _send(self, obj):
        line = json.dumps(obj) + '\n'
        self.proc.stdin.write(line)
        self.proc.stdin.flush()

    def _read(self, timeout=15):
        result = {}
        def _r():
            try:
                line = self.proc.stdout.readline()
                if line:
                    result['data'] = json.loads(line.strip())
            except Exception as e:
                result['error'] = str(e)
        t = threading.Thread(target=_r)
        t.start()
        t.join(timeout=timeout)
        return result.get('data')

    def call(self, tool, args=None, timeout=20):
        with self._lock:
            self._id += 1
            self._send({'jsonrpc':'2.0','id':self._id,'method':'tools/call',
                        'params':{'name': tool, 'arguments': args or {}}})
            resp = self._read(timeout=timeout)
            if not resp:
                return None, 'timeout'
            if 'error' in resp:
                return None, str(resp['error'])
            content = resp.get('result',{}).get('content',[])
            texts = [c.get('text','') for c in content if c.get('type')=='text']
            images = [c for c in content if c.get('type')=='image']
            return '\n'.join(texts), images

    def close(self):
        try:
            self.proc.terminate()
        except:
            pass


print('\n' + '='*55)
print('AUTODIAGNOSTICO MCP-ONLY TOOLS')
print('='*55)

client = MCPClient()
print('  MCP stdio conectado\n')

# 1. screen_status
text, imgs = client.call('screen_status')
if text and ('fps' in text.lower() or 'buffer' in text.lower() or 'running' in text.lower()):
    ok('screen_status', text[:60])
else:
    fail('screen_status', str(text)[:60])

# 2. see_now M1
text, imgs = client.call('see_now', {'monitor_id': 1})
path_line = text.split('\n')[0] if text else ''
has_path = 'Read(' in path_line or '.webp' in path_line
if has_path:
    ok('see_now', path_line[:70])
else:
    warn('see_now', str(text)[:60])

# 3. see_region zoom
text, imgs = client.call('see_region', {'monitor_id':1,'x':0,'y':0,'width':400,'height':200,'scale':2})
if text and ('Read(' in text or '.webp' in text or 'region' in text.lower()):
    ok('see_region', text[:70])
else:
    fail('see_region', str(text)[:60])

# 4. what_changed
text, imgs = client.call('what_changed', {'seconds': 5})
if text and len(text) > 20:
    ok('what_changed', text[:70])
else:
    warn('what_changed', str(text)[:50])

# 5. get_spatial_context
text, imgs = client.call('get_spatial_context')
if text and ('M1' in text or 'monitor' in text.lower()):
    ok('get_spatial_context', text[:80])
else:
    warn('get_spatial_context', str(text)[:60])

# 6. map_environment
text, imgs = client.call('map_environment', {'monitor_id': 1, 'grid': True})
if imgs or (text and ('Read(' in text or 'grid' in text.lower() or '.webp' in text)):
    ok('map_environment', f'imagen={len(imgs)} path={text[:50] if text else ""}')
else:
    fail('map_environment', str(text)[:60])

# 7. list_windows
text, imgs = client.call('list_windows')
if text and len(text) > 30:
    ok('list_windows', text[:80])
else:
    fail('list_windows', str(text)[:50])

# 8. watch_and_notify — abrir notepad y detectar
import subprocess as sp
def open_np():
    time.sleep(1.5)
    sp.Popen(['notepad.exe'])

threading.Thread(target=open_np, daemon=True).start()
t0 = time.time()
text, imgs = client.call('watch_and_notify', {'condition':'window_changed','timeout':8}, timeout=15)
elapsed = time.time()-t0
triggered = text and ('trigger' in text.lower() or 'window' in text.lower() or 'changed' in text.lower() or 'detectado' in text.lower())
if triggered:
    ok('watch_and_notify', f'{elapsed:.1f}s: {text[:60]}')
else:
    warn('watch_and_notify', f'{elapsed:.1f}s: {str(text)[:60]}')

time.sleep(1)

# 9. uia_find_all en Notepad
text, imgs = client.call('uia_find_all', {'window_title': 'Bloc de notas'})
if text and ('element' in text.lower() or 'editor' in text.lower() or 'interactivo' in text.lower()):
    ok('uia_find_all', text[:80])
else:
    warn('uia_find_all', str(text)[:60])

# 10. uia_focused
text, imgs = client.call('uia_focused')
if text and ('focused' in text.lower() or 'name:' in text.lower() or 'control' in text.lower()):
    ok('uia_focused', text[:80])
else:
    warn('uia_focused', str(text)[:60])

# 11. act_on click + type
text, imgs = client.call('act_on', {'target':'Editor de texto','action':'click','window_title':'Bloc de notas'})
if text and 'error' not in text.lower()[:20]:
    time.sleep(0.3)
    text2, _ = client.call('act_on', {'target':'Editor de texto','action':'type',
                                       'text':'ILUMINATY MCP DIAG 2026','window_title':'Bloc de notas'})
    time.sleep(0.5)
    if text2 and 'error' not in text2.lower()[:20]:
        ok('act_on', f'click+type OK: {text2[:50]}')
    else:
        warn('act_on', f'type: {str(text2)[:60]}')
else:
    fail('act_on', str(text)[:60])

# 12. verify_action
text, imgs = client.call('verify_action', {'action_description':'escribio texto en Notepad','wait_ms':500})
if text and 'Read(' in text:
    ok('verify_action', text[:80])
elif text and len(text) > 30:
    warn('verify_action', text[:80])
else:
    fail('verify_action', str(text)[:60])

# Cerrar Notepad
client.call('act', {'action':'key','keys':'alt+F4'})
time.sleep(1)
client.call('act', {'action':'key','keys':'n'})
time.sleep(0.5)

# 13. open_path
text, imgs = client.call('open_path', {'path':'https://httpbin.org/forms/post'})
if text and 'error' not in text.lower()[:20]:
    ok('open_path', text[:60])
else:
    warn('open_path', str(text)[:60])
time.sleep(3)

# 14. focus_window
text, imgs = client.call('focus_window', {'title':'Brave'})
if text and 'error' not in text.lower()[:20]:
    ok('focus_window', text[:60])
else:
    warn('focus_window', str(text)[:60])

# 15. find_on_screen
text, imgs = client.call('find_on_screen', {'query':'Customer name'})
if text and len(text) > 10:
    ok('find_on_screen', text[:80])
else:
    warn('find_on_screen', str(text)[:60])

# 16. run_command
text, imgs = client.call('run_command', {'command':'echo MCP_DIAG_OK'})
if text and 'MCP_DIAG_OK' in text:
    ok('run_command', 'echo OK')
else:
    fail('run_command', str(text)[:60])

# 17. write_file + read_file
TEST = 'C:/Users/jgodo/Desktop/diag_mcp_test.txt'
text, _ = client.call('write_file', {'path': TEST, 'content': 'MCP DIAG 2026'})
if text and 'error' not in text.lower()[:20]:
    text2, _ = client.call('read_file', {'path': TEST})
    if text2 and 'MCP DIAG' in text2:
        ok('write_file + read_file', 'OK')
    else:
        fail('read_file', str(text2)[:60])
else:
    fail('write_file', str(text)[:60])

# 18. get_clipboard
text, imgs = client.call('get_clipboard')
if text and len(text) > 5:
    ok('get_clipboard', text[:60])
else:
    warn('get_clipboard', str(text)[:40])

# 19. os_dialog_resolve
text, imgs = client.call('os_dialog_resolve', {'strategy':'dismiss_first'})
ok('os_dialog_resolve', str(text)[:60])

client.close()

# RESUMEN
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
        if r[0]=='WARN': print(f'  - {r[1]}: {r[2]}')
if failed_list:
    print('Fallos:')
    for r in failed_list:
        print(f'  - {r[1]}: {r[2]}')
