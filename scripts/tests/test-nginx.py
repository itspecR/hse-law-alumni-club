import argparse
import http.client
import json
import os
import ssl
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4

UPSTREAM = """import json
from http.server import BaseHTTPRequestHandler,HTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.do_POST()
    def do_POST(self):
        size=int(self.headers.get('Content-Length','0'))
        self.rfile.read(size)
        body=json.dumps({'path':self.path,'ip':self.headers.get('X-Forwarded-For'),'forwarded':self.headers.get('Forwarded'),'pad':'x'*4096}).encode()
        kind='text/css' if self.path.split('?')[0].endswith('.css') else 'text/html' if self.path.startswith('/news') else 'application/json'
        self.send_response(200)
        self.send_header('Content-Type',kind)
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self,*args):
        pass
HTTPServer(('0.0.0.0',3000),Handler).serve_forever()
"""


def command(*arguments, input=None):
    return subprocess.run(arguments, input=input, check=True, capture_output=True, text=True).stdout.strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--application-image", required=True)
    parser.add_argument("--edge-image", default="club-nginx:local")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    name = "club-nginx-test-" + uuid4().hex[:12]
    upstream, edge = name + "-upstream", name + "-edge"
    network_created = False
    owned = []
    try:
        with tempfile.TemporaryDirectory(prefix=name) as directory:
            work = Path(directory)
            (work / "upstream.py").write_text(UPSTREAM)
            (work / "upstream.py").chmod(0o644)
            command(
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-subj",
                "/CN=localhost",
                "-addext",
                "subjectAltName=DNS:localhost",
                "-keyout",
                str(work / "privkey.pem"),
                "-out",
                str(work / "fullchain.pem"),
            )
            (work / "privkey.pem").chmod(0o640)
            (work / "fullchain.pem").chmod(0o644)
            configuration = (
                (root / "deploy/nginx.conf.template").read_text().replace("${PUBLIC_URL}", "https://localhost")
            )
            configuration = configuration.replace("http://web:80", "http://api:3000")
            (work / "nginx.conf").write_text(configuration)
            (work / "nginx.conf").chmod(0o644)
            command("docker", "network", "create", name)
            network_created = True
            command(
                "docker",
                "run",
                "-d",
                "--name",
                upstream,
                "--network",
                name,
                "--network-alias",
                "api",
                "--network-alias",
                "web",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "-v",
                f"{work / 'upstream.py'}:/upstream.py:ro",
                "--entrypoint",
                "python",
                args.application_image,
                "/upstream.py",
            )
            owned.append(upstream)
            command(
                "docker",
                "run",
                "-d",
                "--name",
                edge,
                "--network",
                name,
                "--user",
                "101:101",
                "--group-add",
                str(os.getgid()),
                "--read-only",
                "--tmpfs",
                "/tmp",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "-p",
                "127.0.0.1::8443",
                "-v",
                f"{work / 'nginx.conf'}:/etc/club/nginx.conf:ro",
                "-v",
                f"{work / 'fullchain.pem'}:/etc/club/tls/fullchain.pem:ro",
                "-v",
                f"{work / 'privkey.pem'}:/etc/club/tls/privkey.pem:ro",
                "--entrypoint",
                "nginx",
                args.edge_image,
                "-c",
                "/etc/club/nginx.conf",
                "-g",
                "daemon off;",
            )
            owned.append(edge)
            port = command("docker", "port", edge, "8443/tcp").rsplit(":", 1)[1]
            tls = ssl.create_default_context(cafile=str(work / "fullchain.pem"))

            def request(path, data=None, headers=None):
                connection = http.client.HTTPSConnection("localhost", int(port), context=tls, timeout=5)
                try:
                    connection.request("POST" if data is not None else "GET", path, body=data, headers=headers or {})
                    response = connection.getresponse()
                    return response.status, response.headers, response.read()
                finally:
                    connection.close()

            for _attempt in range(30):
                try:
                    if request("/api/ready")[0] == 200:
                        break
                except OSError:
                    pass
                time.sleep(1)
            else:
                raise RuntimeError("nginx не готов")
            status, headers, body = request(
                "/api/probe",
                headers={"X-Forwarded-For": "185.71.76.1", "X-Real-IP": "185.71.76.1", "Forwarded": "for=185.71.76.1"},
            )
            payload = json.loads(body)
            assert status == 200 and payload["path"] == "/probe"
            assert payload["ip"] != "185.71.76.1" and not payload["forwarded"]
            assert headers["Strict-Transport-Security"] and headers["Content-Security-Policy"]
            assert headers["X-Content-Type-Options"] == "nosniff"
            for path in ("/.env", "/.git/config", "/wp-login.php"):
                assert request(path)[0] == 404, path
            assert request("/api/probe", b"x" * 524289)[0] == 413
            assert request("/api/me/avatar", b"x" * (4 * 1024 * 1024 + 1))[0] == 413
            assert request("/api/admin/media", b"x" * (5 * 1024 * 1024))[0] == 200
            compressed = {"Accept-Encoding": "gzip"}
            assert request("/assets/site.css?v=1", headers=compressed)[1].get("Content-Encoding") == "gzip"
            for path in ("/news", "/api/probe"):
                assert request(path, headers=compressed)[1].get("Content-Encoding") is None, path
            statuses = [request("/api/auth/admin-login", b"{}")[0] for _ in range(15)]
            assert 200 in statuses and 429 in statuses
            print("nginx: TLS, прокси, защита заголовков IP, CSP, сжатие статики, лимиты тела и входа проверены")
    finally:
        for container in reversed(owned):
            command("docker", "rm", "-f", container)
        if network_created:
            command("docker", "network", "rm", name)


if __name__ == "__main__":
    main()
