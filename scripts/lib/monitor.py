import datetime
import json
import os
import pathlib
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlsplit

issues = []
metrics = {}
now = time.time()
state = pathlib.Path(os.environ['STATE_DIR'])
containers = json.loads(subprocess.check_output(['docker', 'inspect', *sys.argv[1:]]))
expected_services = {'postgres', 'api', 'web', 'caddy'}
actual_services = {container['Config']['Labels']['com.docker.compose.service'] for container in containers}
for missing in sorted(expected_services - actual_services): issues.append(f'{missing}: контейнер отсутствует')
for container in containers:
    name = container['Config']['Labels']['com.docker.compose.service']
    status = container['State']
    metrics[name] = {'running': status['Running'], 'health': status.get('Health', {}).get('Status'), 'restarts': container['RestartCount']}
    if not status['Running'] or status.get('Health', {}).get('Status') != 'healthy': issues.append(f'{name}: не готов')
    if status.get('OOMKilled'): issues.append(f'{name}: OOM')
    if container['RestartCount'] >= 3: issues.append(f'{name}: не менее трёх перезапусков')
    if name == 'api':
        logs = subprocess.run(['docker','logs','--since','26h',container['Id']], capture_output=True, text=True, check=True)
        try:
            last_jobs = json.loads((state/'last-monitor.json').read_text()).get('metrics', {}).get('last_jobs', {})
        except (OSError, ValueError): last_jobs = {}
        recent_errors = 0
        for line in (logs.stdout+'\n'+logs.stderr).splitlines():
            try: event = json.loads(line)
            except ValueError: continue
            if not isinstance(event, dict): continue
            timestamp = event.get('time', 0)/1000
            if event.get('level', 0) >= 50 and timestamp > now-300: recent_errors += 1
            if event.get('job') and event.get('status') in ('ok','failed'):
                last_jobs[event['job']] = (event['status'], timestamp)
        metrics['api_errors_5m'] = recent_errors
        if recent_errors >= 5: issues.append('API: не менее пяти ошибок за пять минут')
        metrics['last_jobs'] = last_jobs
        if os.environ.get('MONITOR_JOBS') == 'true':
            expected = {'mail-outbox':900, 'reserve-expiry':2700, 'retention':26*3600, 'event-reminders':26*3600, 'podcast-reminders':26*3600, 'points-decay':35*86400}
            if os.environ.get('MONITOR_DPO') == 'true': expected['dpo-sync'] = 2*3600
            if os.environ.get('MONITOR_NEWS') == 'true': expected['news-sync'] = 2*3600
            uptime = now-datetime.datetime.fromisoformat(status['StartedAt'].replace('Z','+00:00')).timestamp()
            for job, age in expected.items():
                result, finished = last_jobs.get(job, ('missing', 0))
                if result == 'failed' or (uptime > age and now-finished > age): issues.append(f'Задача {job}: ошибка или просрочен результат')
        metrics['jobs_enabled'] = os.environ.get('MONITOR_JOBS') == 'true'

disk = shutil.disk_usage(state)
metrics['disk_used_percent'] = round((disk.total-disk.free)/disk.total*100, 1)
if metrics['disk_used_percent'] >= float(os.getenv('MONITOR_DISK_PERCENT',85)): issues.append('Диск: превышен порог заполнения')
memory = {line.split(':')[0]: int(line.split()[1]) for line in pathlib.Path('/proc/meminfo').read_text().splitlines()}
metrics['memory_used_percent'] = round((1-memory['MemAvailable']/memory['MemTotal'])*100,1)
if metrics['memory_used_percent'] >= float(os.getenv('MONITOR_MEMORY_PERCENT',90)): issues.append('Память: превышен порог использования')
try:
    backup = json.loads((state/'last-backup.json').read_text())
    metrics['backup_age_hours'] = round((now-backup['completed_at'])/3600,2)
    metrics['offsite_verified'] = backup['offsite_verified']
    if metrics['backup_age_hours'] > float(os.getenv('MONITOR_BACKUP_MAX_HOURS',36)): issues.append('Копия старше допустимого срока')
except (OSError, ValueError, KeyError): issues.append('Нет записи об успешной копии')
url = os.environ['MONITOR_URL'].rstrip('/')
context = ssl.create_default_context(cafile=os.getenv('HTTPS_CA_FILE') or None)
try:
    with urllib.request.urlopen(url+'/api/ready', context=context, timeout=10) as response:
        if json.load(response).get('status') != 'ok': issues.append('Readiness не подтверждён')
    with urllib.request.urlopen(url+'/', context=context, timeout=10) as response:
        if b'<html' not in response.read().lower(): issues.append('Сайт не вернул HTML')
    target = urlsplit(url)
    if target.scheme != 'https': issues.append('TLS: используется HTTP')
    else:
        with socket.create_connection((target.hostname, target.port or 443), timeout=10) as connection:
            with context.wrap_socket(connection, server_hostname=target.hostname) as tls:
                days = (ssl.cert_time_to_seconds(tls.getpeercert()['notAfter'])-now)/86400
                metrics['certificate_days_remaining'] = round(days,2)
                if days < float(os.getenv('MONITOR_CERT_MIN_DAYS',14)): issues.append('TLS: сертификат скоро истекает')
except Exception: issues.append('HTTPS/readiness: соединение или проверка сертификата не прошли')
report = {'checked_at':int(now),'status':'error' if issues else 'ok','issues':issues,'metrics':metrics,'notification_channel':'journald only'}
temporary = state/'last-monitor.json.partial'
temporary.write_text(json.dumps(report,ensure_ascii=False)+'\n')
os.replace(temporary,state/'last-monitor.json')
print(json.dumps(report,ensure_ascii=False))
sys.exit(1 if issues else 0)
