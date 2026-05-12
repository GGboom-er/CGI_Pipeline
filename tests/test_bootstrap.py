import socket, time, base64
from pathlib import Path
server_script = Path(r'y:\GGbommer\scripts\CGI_Pipeline\dccs\maya\rpyc_server.py').read_text(encoding='utf-8')
server_script += '\n_start_rpyc_server(port=61690)\n'
b64 = base64.b64encode(server_script.encode('utf-8')).decode('ascii')
inject_code = f"exec(__import__('base64').b64decode('{b64}').decode('utf-8'), globals())\n"
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(5)
s.connect(('127.0.0.1', 51690))
s.sendall(inject_code.encode('utf-8'))
try:
    resp = s.recv(4096).decode('utf-8', errors='replace')
    print('Response:', resp)
except socket.timeout:
    print('Response: timeout')
s.close()

time.sleep(2)
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(2)
try:
    s.connect(('127.0.0.1', 61690))
    print('RPyC successfully started on port 61690')
except Exception as e:
    print('Failed to connect to RPyC:', e)
