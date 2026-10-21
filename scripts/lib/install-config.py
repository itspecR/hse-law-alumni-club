import argparse
import getpass
import json
import os
import pathlib
import re
import secrets
import shlex
import stat
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
FILES = ('runtime.env', 'operations.env', 'initial-admin.txt', 'install.json')


def email(value):
    if not re.fullmatch(r"[A-Za-z0-9.!#%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", value):
        raise ValueError('Укажите корректный email')
    return value


def hostname(value):
    value = value.lower()
    labels = value.split('.')
    if len(value) > 253 or len(labels) < 2 or any(
            not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels):
        raise ValueError('Нужно доменное имя без схемы, порта и пути')
    if labels[-1] in ('localhost', 'local', 'test', 'invalid') or labels[-1].isdigit():
        raise ValueError('Для публичного HTTPS нужен публичный домен')
    return value


def prompt(label, default='', check=None, hidden=False):
    while True:
        value = (getpass.getpass if hidden else input)(label + (f' [{default}]' if default else '') + ': ') or default
        try:
            if '\n' in value or '\r' in value or '\0' in value:
                raise ValueError('Значение должно занимать одну строку')
            return check(value) if check else value
        except ValueError as error:
            print(error, file=sys.stderr)


def configuration(local):
    values = {}
    for line in (REPO / '.env.example').read_text().splitlines():
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, raw = line.split('=', 1)
        values[key] = ' '.join(shlex.split(raw, comments=True))
    for key in ('POSTGRES_PASSWORD', 'CHECKOUT_DB_PASSWORD', 'AUTH_SECRET',
                'ADMIN_AUTH_SECRET', 'ADMIN_PASSWORD', 'BACKUP_ENCRYPTION_KEY'):
        values[key] = secrets.token_hex(32)
    values.update(APP_ENV='production', SEED_DEMO='false', CHECKOUT_DATABASE_URL='',
                  OFFICE_NOTIFY_CHANNEL='email', TELEGRAM_POLLING='false', SUPPORT_ENABLED='false')
    for key in ('TEST_EDITOR_EMAIL', 'TEST_EDITOR_PASSWORD', 'TEST_ALUMNI_EMAIL',
                'TEST_ALUMNI_PASSWORD', 'POINTS_SERVICE_TOKEN', 'TELEGRAM_BOT_TOKEN',
                'TELEGRAM_WEBHOOK_SECRET', 'OFFICE_TG_BOT_TOKEN', 'OFFICE_TG_CHAT_ID',
                'YOOKASSA_SHOP_ID', 'YOOKASSA_SECRET_KEY', 'VAPID_PUBLIC_KEY',
                'VAPID_PRIVATE_KEY', 'SENTRY_DSN', 'BACKUP_OFFSITE_REMOTE', 'CORS_ORIGINS'):
        values[key] = ''
    if local:
        values.update(ADMIN_EMAIL='admin@club.test', PUBLIC_URL='https://localhost:9443',
                      WEB_DOMAIN='localhost', ADMIN_DOMAIN='admin.localhost', ACME_EMAIL='qa@club.test',
                      SMTP_HOST='mailpit', SMTP_PORT='1025', SMTP_FROM='club@club.test',
                      SMTP_USER='', SMTP_PASS='', OFFICE_EMAIL='office@club.test',
                      JOBS_ENABLED='false', DPO_SYNC_ENABLED='false', NEWS_SYNC_ENABLED='false')
    else:
        print('Для публичного HTTPS оба домена должны указывать на сервер; нужны входящие 80/443.')
        values['WEB_DOMAIN'] = prompt('Домен сайта', check=hostname)
        values['ADMIN_DOMAIN'] = prompt('Домен прежних файлов/офиса', 'office.' + values['WEB_DOMAIN'], hostname)
        if values['ADMIN_DOMAIN'] == values['WEB_DOMAIN']:
            raise ValueError('Домены сайта и прежних файлов должны отличаться')
        values['PUBLIC_URL'] = 'https://' + values['WEB_DOMAIN']
        values['ADMIN_EMAIL'] = prompt('Email первого администратора', check=email)
        values['ACME_EMAIL'] = prompt('Email для центра сертификации ACME', check=email)
        values['SMTP_HOST'] = prompt('Сервер SMTP', check=lambda x: hostname(x))
        values['SMTP_PORT'] = prompt('Порт SMTP', '587', lambda x: str(port(x)))
        values['SMTP_USER'] = prompt('Логин SMTP (пусто для сервера без авторизации)')
        values['SMTP_PASS'] = prompt('Пароль SMTP', hidden=True)
        values['SMTP_FROM'] = prompt('Разрешённый SMTP-отправитель', check=email)
        values['OFFICE_EMAIL'] = prompt('Email получателя заявок офиса', check=email)
    return values


def port(value):
    if not value.isdigit() or not 1 <= int(value) <= 65535:
        raise ValueError('Порт должен быть числом от 1 до 65535')
    return int(value)


def mode(value):
    if value not in ('1', '2'):
        raise ValueError('Выберите 1 или 2')
    return value


def dotenv(values):
    def quoted(value):
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('$', '$$') + '"'
    return ''.join(key + '=' + quoted(value) + '\n' for key, value in values.items())


def private_file(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError(f'{path}: требуется обычный файл оператора с правами 0600')


def config_directory(path):
    if not path.is_absolute() or path == pathlib.Path('/') or path.is_symlink() or path.resolve() != path:
        raise ValueError('Каталог конфигурации должен быть абсолютным путём без симлинков')
    if path == REPO or REPO in path.parents:
        raise ValueError('Конфигурация должна находиться вне checkout')
    if path.exists():
        info = path.stat()
        if not path.is_dir() or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('Существующий каталог конфигурации должен принадлежать оператору и иметь права 0700')


def write_new(path, text):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as output:
        output.write(text)


def fresh_project(project):
    ids = subprocess.check_output(['docker', 'ps', '-aq', '--filter', f'label=com.docker.compose.project={project}'], text=True)
    if ids.strip():
        raise ValueError(f'В {project} уже есть контейнеры. Используйте runbook для обновления или переноса данных')
    volumes = set(subprocess.check_output(['docker', 'volume', 'ls', '-q'], text=True).split())
    if any(f'{project}_{name}' in volumes for name in ('pgdata', 'directus_uploads', 'caddy_data', 'caddy_config')):
        raise ValueError(f'В {project} уже есть тома. Установщик не присоединяет прежние данные автоматически')


def operations(directory, local):
    values = {'ENV_FILE': str(directory / 'runtime.env'),
              'STATE_DIR': str(directory / 'state'), 'BACKUP_DIR': str(directory / 'backups')}
    if directory == pathlib.Path('/etc/club'):
        values.update(STATE_DIR='/var/lib/club-ops', BACKUP_DIR='/var/backups/club')
    if local:
        values.update(DEPLOY_COMPOSE_OVERRIDE=str(REPO / 'deploy/compose.local.yml'),
                      HTTPS_CA_FILE=str(directory / 'local-ca.crt'), MONITOR_CERT_MIN_DAYS='0.25')
    return values


def install(directory, local):
    config_directory(directory)
    previous = directory / 'install.json'
    if previous.exists():
        for name in FILES:
            private_file(directory / name)
        metadata = json.loads(previous.read_text())
        if metadata.get('version') != 1 or metadata.get('mode') not in ('local', 'public'):
            raise ValueError('Неизвестная конфигурация установщика')
        if local and metadata['mode'] != 'local':
            raise ValueError('--local не меняет контур существующей установки')
        local = metadata['mode'] == 'local'
        print('Конфигурация уже существует. Секреты и пароль администратора сохраняются.')
    else:
        if directory.exists() and any(directory.iterdir()):
            raise ValueError('Каталог уже содержит конфигурацию. Используйте runbook; файлы не перезаписаны')
        if not local:
            if not sys.stdin.isatty():
                raise ValueError('Для мастера нужен терминал; для локального стенда укажите --local')
            choice = prompt('Контур: 1 – локальная репетиция, 2 – публичный HTTPS', '1', mode)
            local = choice == '1'
        values = configuration(local)
    subprocess.run(['bash', str(REPO / 'scripts/setup-ubuntu.sh')], check=True)
    if not previous.exists():
        fresh_project('club-local' if local else 'club-pravo-hse')
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_new(directory / 'runtime.env', dotenv(values))
        write_new(directory / 'operations.env', dotenv(operations(directory, local)))
        write_new(directory / 'initial-admin.txt', 'Email: ' + values['ADMIN_EMAIL'] + '\nPassword: ' + values['ADMIN_PASSWORD'] + '\n')
        metadata = {'version': 1, 'mode': 'local' if local else 'public'}
        write_new(previous, json.dumps(metadata) + '\n')
    environment = {'PATH': os.environ['PATH'], **operations(directory, local)}
    subprocess.run(['bash', str(REPO / 'scripts/deploy.sh')], env=environment, check=True)
    url = subprocess.check_output(['bash', '-c', 'source "$1/scripts/lib/ops-common.sh"; ops_value PUBLIC_URL',
                                   'install', str(REPO)], env=environment, text=True).strip()
    print('\nСайт и /api/ready проверены: ' + url)
    print('Офис: ' + url + '/admin')
    print('Первичные данные администратора: sudo cat ' + shlex.quote(str(directory / 'initial-admin.txt')))
    print('Пароль не выводился в журнал. После смены пароля этот файл содержит прежние данные.')
    if local:
        print('Локальные письма: http://localhost:8025; отправки наружу нет.')
        print('Для браузера подтвердите доверие локальной CA: ' + str(directory / 'local-ca.crt'))
        print('Доступ с другого компьютера – через SSH-туннель; инструкция в README, раздел установки.')


def failure_message(error):
    if isinstance(error, ValueError):
        return str(error)
    if isinstance(error, KeyboardInterrupt):
        return 'прервано вручную; повторный запуск продолжит с сохранённой конфигурацией'
    if isinstance(error, subprocess.CalledProcessError):
        command = error.cmd if isinstance(error.cmd, (list, tuple)) else [str(error.cmd)]
        return 'команда «' + ' '.join(map(str, command[:3])) + '» завершилась с кодом ' + str(error.returncode)
    return type(error).__name__ + ' ' + str(error)


def main():
    parser = argparse.ArgumentParser(description='Настройка и запуск Клуба на Ubuntu')
    parser.add_argument('--local', action='store_true', help='локальная репетиция без интерактивных вопросов')
    parser.add_argument('--config-dir', type=pathlib.Path, default=pathlib.Path('/etc/club'))
    parser.add_argument('--public-url', help='новый адрес сайта контура 3, например https://203.0.113.10:9443')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        previous = args.config_dir / 'install.json'
        if previous.is_file() and json.loads(previous.read_text()).get('version') == 1:
            if args.public_url:
                raise ValueError('--public-url поддерживается только установками MariaDB')
            install(args.config_dir, args.local)
        else:
            from mariadb_native import install as install_mariadb
            install_mariadb(args.config_dir, args.local, args.public_url)
    except (ValueError, OSError, KeyError, EOFError, KeyboardInterrupt, subprocess.CalledProcessError) as error:
        print('Установка прервана: ' + failure_message(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
