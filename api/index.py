import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from server import APIError, Application, parse_multipart
import json
import mimetypes

app_inst = Application()

def app(environ, start_response):
    path = environ.get('PATH_INFO', '')
    method = environ.get('REQUEST_METHOD', 'GET')
    
    headers = [
        ('Access-Control-Allow-Headers', 'Content-Type, Authorization'),
        ('Access-Control-Allow-Methods', 'GET, POST, PUT, PATCH, DELETE, OPTIONS')
    ]
    
    if method == 'OPTIONS':
        start_response('204 No Content', headers)
        return [b'']
        
    if path.startswith('/api'):
        authorization = environ.get('HTTP_AUTHORIZATION', '')
        key = authorization[7:] if authorization.startswith('Bearer ') else environ.get('HTTP_X_USER_KEY')
        body_bytes = b''
        try:
            length = int(environ.get('CONTENT_LENGTH', '0') or '0')
            if length > 0:
                body_bytes = environ['wsgi.input'].read(length)
        except Exception:
            pass

        ct = environ.get('CONTENT_TYPE', '')
        body, files = {}, {}
        if ct.startswith('multipart/form-data'):
            boundary_param = [p for p in ct.split(';') if 'boundary=' in p]
            if boundary_param:
                boundary = boundary_param[0].split('boundary=')[1].strip('"\'').encode()
                body, files = parse_multipart(body_bytes, boundary)
        elif body_bytes:
            try:
                body = json.loads(body_bytes.decode('utf-8'))
            except Exception:
                pass

        try:
            res = app_inst.request(method, path.rstrip('/'), key, body, files=files)
            res_bytes = json.dumps(res).encode('utf-8')
            headers.append(('Content-Type', 'application/json; charset=utf-8'))
            start_response('200 OK', headers)
            return [res_bytes]
        except APIError as err:
            headers.append(('Content-Type', 'application/json; charset=utf-8'))
            start_response(f'{err.status} Error', headers)
            return [json.dumps(err.payload).encode('utf-8')]
        except Exception:
            headers.append(('Content-Type', 'application/json; charset=utf-8'))
            start_response('500 Internal Server Error', headers)
            return [b'{"error": "An unexpected server error occurred."}']

    root = Path(__file__).parent.parent / 'web'
    filename = 'index.html' if path in ('/', '') else path.lstrip('/')
    target = (root / filename).resolve()
    if target.is_file() and target.is_relative_to(root.resolve()):
        mime = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        headers.append(('Content-Type', f"{mime}; charset=utf-8"))
        start_response('200 OK', headers)
        return [target.read_bytes()]

    start_response('404 Not Found', headers)
    return [b'{"error": "Not found"}']
