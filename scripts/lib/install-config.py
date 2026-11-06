import argparse
import getpass
import os
import pathlib
import re
import secrets
import shlex
import stat
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]


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


def configuration():
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
                'TEST_ALUMNI_PASSWORD', 'TELEGRAM_BOT_TOKEN',
                'TELEGRAM_WEBHOOK_SECRET', 'OFFICE_TG_BOT_TOKEN', 'OFFICE_TG_CHAT_ID',
                'YOOKASSA_SHOP_ID', 'YOOKASSA_SECRET_KEY', 'VAPID_PUBLIC_KEY',
                'VAPID_PRIVATE_KEY', 'SENTRY_DSN', 'BACKUP_OFFSITE_REMOTE', 'CORS_ORIGINS'):
        values[key] = ''
    values.update(ADMIN_EMAIL='admin@club.test', PUBLIC_URL='https://localhost:9443',
                  WEB_DOMAIN='localhost', ADMIN_DOMAIN='admin.localhost', ACME_EMAIL='qa@club.test',
                  SMTP_HOST='mailpit', SMTP_PORT='1025', SMTP_FROM='club@club.test',
                  SMTP_USER='', SMTP_PASS='', OFFICE_EMAIL='office@club.test',
                  JOBS_ENABLED='false', DPO_SYNC_ENABLED='false', NEWS_SYNC_ENABLED='false')
    return values


def port(value):
    if not value.isdigit() or not 1 <= int(value) <= 65535:
        raise ValueError('Порт должен быть числом от 1 до 65535')
    return int(value)




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
        from mariadb_native import install

        install(args.config_dir, args.local, args.public_url)
    except (ValueError, OSError, KeyError, EOFError, KeyboardInterrupt, subprocess.CalledProcessError) as error:
        print('Установка прервана: ' + failure_message(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
