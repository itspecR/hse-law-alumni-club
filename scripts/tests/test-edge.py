#!/usr/bin/env python3

import argparse
import gzip
import http.client
import json
from pathlib import Path
import re
import ssl
import subprocess
import tempfile
import time
import uuid

REPO = Path(__file__).resolve().parents[2]
MIB = 1024 * 1024
PROBE = """import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class Probe(BaseHTTPRequestHandler):
    def do_GET(self):
        size = int(self.headers.get('Content-Length', '0'))
        received = 0
        while received < size:
            chunk = self.rfile.read(min(65536, size - received))
            if not chunk:
                return
            received += len(chunk)
        path = self.path.split('?')[0]
        if path == '/pages/home':
            payload = {'blocks': {}}
        elif path in ('/news', '/programs', '/events', '/timeline'):
            payload = []
        else:
            payload = {
                'path': self.path, 'bytes': received,
                'forwardedFor': self.headers.get('X-Forwarded-For'),
                'forwardedProto': self.headers.get('X-Forwarded-Proto')
            }
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    do_POST = do_GET

    def log_message(self, *args):
        pass

ThreadingHTTPServer(('0.0.0.0', 3000), Probe).serve_forever()
"""


def docker(*args, check=True):
    return subprocess.run(['docker', *args], text=True, capture_output=True, check=check)


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def run(args):
    name = 'club-edge-qa-' + uuid.uuid4().hex[:12]
    networks, containers = [], []
    cleanup_errors = []
    with tempfile.TemporaryDirectory(prefix='club-edge-qa-') as directory:
        try:
            for suffix in ['internal', 'browser']:
                options = ['--internal'] if suffix == 'internal' else []
                network = docker('network', 'create', *options, '--label', 'club.qa=edge', name + '-' + suffix).stdout.strip()
                networks.append(network)
            for suffix, options in [
                ('api', ['--entrypoint', 'python', args.api_image, '-c', PROBE]),
                ('web', [args.web_image]),
            ]:
                container = docker('run', '--detach', '--network', networks[0], '--network-alias', suffix,
                                   '--read-only', '--tmpfs', '/data', '--tmpfs', '/config', '--tmpfs', '/tmp',
                                   '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                                   '--name', name + '-' + suffix, *options).stdout.strip()
                containers.append(container)
            edge = docker('create', '--network', networks[1], '--read-only',
                          '--tmpfs', '/data', '--tmpfs', '/config', '--memory', '128m',
                          '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                          '--publish', '127.0.0.1::443', '--publish', '127.0.0.1::80',
                          '--publish', '127.0.0.1::8081', '--name', name + '-edge',
                          '--env', 'WEB_DOMAIN=localhost', '--env', 'ADMIN_DOMAIN=:8081',
                          '--env', 'PUBLIC_URL=https://localhost', '--env', 'ACME_EMAIL=edge-qa@example.invalid',
                          '--mount', f'type=bind,source={REPO / "deploy/Caddyfile"},target=/etc/caddy/Caddyfile,readonly',
                          args.edge_image).stdout.strip()
            containers.append(edge)
            docker('network', 'connect', networks[0], edge)
            docker('start', edge)
            ca = Path(directory) / 'root.crt'
            deadline = time.monotonic() + 60
            while True:
                certificate = docker('exec', edge, 'cat', '/data/caddy/pki/authorities/local/root.crt', check=False)
                if certificate.returncode == 0:
                    ca.write_text(certificate.stdout)
                    break
                check(time.monotonic() < deadline, 'Caddy не создал локальную CA за 60 секунд')
                time.sleep(0.25)
            ports = json.loads(docker('inspect', edge).stdout)[0]['NetworkSettings']['Ports']
            context = ssl.create_default_context(cafile=str(ca))

            def request(path, body=None, headers=None, port='443/tcp'):
                number = int(ports[port][0]['HostPort'])
                connection = (http.client.HTTPSConnection('localhost', number, context=context, timeout=30)
                              if port == '443/tcp' else http.client.HTTPConnection('127.0.0.1', number, timeout=30))
                try:
                    connection.request('GET' if body is None else 'POST', path, body=body, headers=headers or {})
                    response = connection.getresponse()
                    return response.status, dict((key.lower(), value) for key, value in response.getheaders()), response.read()
                finally:
                    connection.close()

            while True:
                try:
                    status, headers, html = request('/')
                    if status == 200:
                        break
                except (OSError, http.client.HTTPException):
                    if time.monotonic() >= deadline:
                        raise
                check(time.monotonic() < deadline, 'HTTPS не стал готов за 60 секунд')
                time.sleep(0.25)
            check(status == 200 and b'<html' in html, 'HTTPS не вернул HTML')
            check('https://telegram.org' in headers.get('content-security-policy', ''), 'Потеряна CSP Telegram')
            check(headers.get('x-content-type-options') == 'nosniff' and 'server' not in headers, 'Потеряны защитные заголовки')
            check('max-age=' in headers.get('strict-transport-security', ''), 'Потерян HSTS')
            check(headers.get('cache-control') == 'no-cache' and headers.get('x-club-offline') == 'public',
                  'Публичная главная не разрешена для офлайн-чтения')
            check('x-frame-options' not in headers, 'Telegram embedding заблокирован')
            while request('/api/ready')[0] != 200:
                check(time.monotonic() < deadline, 'HTTP-фикстура не стала готова за 60 секунд')
                time.sleep(0.25)
            for path in ['/admin', '/lk', '/cart', '/search']:
                status, headers, body = request(path)
                check(status == 200 and b'<main' in body and headers.get('cache-control') == 'no-store', 'Серверная страница: ' + path)
                check('x-club-offline' not in headers, 'Приватная страница разрешена для офлайн-чтения: ' + path)
            status, headers, _ = request('/', headers={'Authorization': 'Bearer edge-test'})
            check(status == 200 and headers.get('cache-control') == 'no-store' and 'x-club-offline' not in headers,
                  'Страница с авторизацией разрешена для офлайн-чтения')
            status, redirect_headers, _ = request('/v2/cart')
            check(status == 308 and redirect_headers.get('location') == '/cart', 'Потерян переход с прежнего маршрута')
            check(request('/not-a-club-route')[0] == 404, 'Потерян 404 неизвестной страницы')
            for path in ['/.env', '/.git/config', '/wp-login.php']:
                check(request(path)[0] == 404, 'Запрос сканера прошёл: ' + path)
            for path, expected in [('/api/ready', '/ready'), ('/sitemap.xml', '/sitemap.xml'), ('/robots.txt', '/robots.txt'),
                                   ('/assets/00000000-0000-4000-8000-000000000000/file', '/media/00000000-0000-4000-8000-000000000000/file')]:
                status, _, body = request(path)
                check(status == 200 and json.loads(body)['path'] == expected, 'Прокси изменил маршрут: ' + path)
            status, _, body = request('/api/ready', headers={'X-Forwarded-For': '198.51.100.72', 'X-Forwarded-Proto': 'http'})
            forwarded = json.loads(body)
            check(status == 200 and '198.51.100.72' not in forwarded['forwardedFor'] and forwarded['forwardedProto'] == 'https', 'Доверие к поддельным proxy headers')
            for path, limit in [('/api/body', 512_000), ('/api/me/avatar', 4_000_000), ('/api/admin/media', 129_000_000)]:
                status, _, body = request(path, b'x' * limit)
                check(status == 200 and json.loads(body)['bytes'] == limit, f'Отклонён допустимый размер: {path}, HTTP {status}')
                check(request(path, b'x' * (limit + 1))[0] == 413, 'Не соблюдён лимит тела: ' + path)
            check(request('/api/ready', headers={'X-Large': 'x' * (20 * 1024)})[0] == 431, 'Не соблюдён лимит заголовков')
            status, headers, body = request('/', headers={'Accept-Encoding': 'gzip'})
            check(status == 200 and headers.get('content-encoding') == 'gzip' and b'<html' in gzip.decompress(body), 'gzip не работает')
            asset = re.search(rb'<script[^>]+src="([^"?]+\.js)\?v=([0-9a-f]+)"', html)
            check(asset is not None, 'Не найден JS-артефакт с версией')
            status, headers, _ = request(asset[1].decode() + '?v=' + asset[2].decode())
            check(status == 200 and headers.get('cache-control') == 'public, max-age=31536000, immutable', 'JavaScript с версией не кэшируется надолго')
            status, headers, _ = request(asset[1].decode())
            check(status == 200 and headers.get('cache-control') == 'no-cache', 'Браузер может сохранить прежний JavaScript')
            check('no-cache' in request('/sw.js')[1].get('cache-control', ''), 'Service worker кэшируется')
            status, headers, _ = request('/old-office', port='8081/tcp')
            check(status == 308 and headers.get('location') == 'https://localhost/admin', 'Потерян legacy redirect')
            status, headers, _ = request('/lk', port='80/tcp')
            check(status == 308 and headers.get('location', '').startswith('https://'), 'Потерян HTTP → HTTPS redirect')
            docker('exec', edge, 'caddy', 'validate', '--config', '/etc/caddy/Caddyfile')
            docker('exec', edge, 'caddy', 'reload', '--config', '/etc/caddy/Caddyfile')
            check(request('/api/ready')[0] == 200 and request('/')[0] == 200, 'Reload нарушил маршруты или TLS')
            output = docker('logs', edge)
            logs = output.stdout + output.stderr
            memory = [json.loads(line)['GOMEMLIMIT'] for line in logs.splitlines()
                      if line.startswith('{') and '"GOMEMLIMIT":' in line]
            check(any(0 < value <= 128 * MIB for value in memory), 'CLI не применил cgroup memory limit')
            print('Edge: доверенный TLS/CA, reload, маршруты, статика, CSP, gzip, client IP, 3 body limits, header limit и cgroup memory – успешно')
        except Exception:
            for container in containers:
                output = docker('logs', container, check=False)
                print((output.stdout + output.stderr)[-6000:])
            raise
        finally:
            for container in reversed(containers):
                result = docker('rm', '--force', '--volumes', container, check=False)
                if result.returncode:
                    cleanup_errors.append(result.stderr.strip())
            for network in reversed(networks):
                result = docker('network', 'rm', network, check=False)
                if result.returncode:
                    cleanup_errors.append(result.stderr.strip())
            check(not cleanup_errors, 'Не завершена очистка QA: ' + '; '.join(cleanup_errors))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Проверка штатных Caddyfile и TLS в собственных контейнерах без рабочей БД.')
    parser.add_argument('--edge-image', required=True)
    parser.add_argument('--web-image', required=True)
    parser.add_argument('--api-image', required=True)
    run(parser.parse_args())
