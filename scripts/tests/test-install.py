import importlib.util
import contextlib
import io
import json
import os
import pathlib
import tempfile
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('installer', pathlib.Path(__file__).resolve().parents[1] / 'lib/install-config.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallationGuards(unittest.TestCase):
    def test_local_has_independent_secrets_and_no_external_channels(self):
        values = installer.configuration(True)
        keys = ('POSTGRES_PASSWORD', 'CHECKOUT_DB_PASSWORD', 'AUTH_SECRET',
                'ADMIN_AUTH_SECRET', 'ADMIN_PASSWORD', 'BACKUP_ENCRYPTION_KEY')
        self.assertEqual(len({values[key] for key in keys}), len(keys))
        self.assertTrue(all(len(values[key]) == 64 for key in keys))
        self.assertEqual(values['APP_ENV'], 'production')
        self.assertEqual(values['SEED_DEMO'], 'false')
        self.assertEqual(values['SMTP_HOST'], 'mailpit')
        for key in ('JOBS_ENABLED', 'DPO_SYNC_ENABLED', 'NEWS_SYNC_ENABLED'):
            self.assertEqual(values[key], 'false')
        for key in ('YOOKASSA_SECRET_KEY', 'TELEGRAM_BOT_TOKEN', 'TEST_EDITOR_PASSWORD', 'TEST_ALUMNI_PASSWORD'):
            self.assertEqual(values[key], '')

    def test_reject_untrusted_public_domain_and_email(self):
        for value in ('https://club.ru', 'club.ru:443', 'localhost', '127.0.0.1', 'a.local', 'a..ru', 'a.ru/path', 'a.ru\nEVIL=true'):
            with self.subTest(value=value), self.assertRaises(ValueError): installer.hostname(value)
        with self.assertRaises(ValueError): installer.email('a@b.ru\nEVIL=true')

    def test_existing_files_and_symlinks_are_not_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp).resolve()
            config = directory / 'runtime.env'
            config.write_text('existing configuration')
            with self.assertRaisesRegex(ValueError, 'не перезаписаны'):
                installer.install(directory, True)
            self.assertEqual(config.read_text(), 'existing configuration')
            link = directory / 'link'
            link.symlink_to(config)
            with self.assertRaises(ValueError): installer.private_file(link)
            with self.assertRaises(FileExistsError): installer.write_new(config, 'replacement')

    def test_refuse_existing_project_or_volumes(self):
        with patch.object(installer.subprocess, 'check_output', return_value='container\n'):
            with self.assertRaisesRegex(ValueError, 'контейнеры'): installer.fresh_project('club-local')
        with patch.object(installer.subprocess, 'check_output', side_effect=['', 'club-local_pgdata\n']):
            with self.assertRaisesRegex(ValueError, 'тома'): installer.fresh_project('club-local')

    def test_retry_preserves_files_and_uses_sanitized_deployment_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp).resolve()
            calls = []
            def run(args, **kwargs): calls.append((args, kwargs))
            with patch.object(installer.subprocess, 'run', side_effect=run), \
                    patch.object(installer, 'fresh_project'), \
                    patch.object(installer.subprocess, 'check_output', return_value='https://localhost:9443\n'), \
                    contextlib.redirect_stdout(io.StringIO()):
                installer.install(directory, True)
                before = {name: (directory / name).read_bytes() for name in installer.FILES}
                self.assertTrue(all(os.stat(directory / name).st_mode & 0o777 == 0o600 for name in installer.FILES))
                installer.install(directory, True)
                self.assertEqual(before, {name: (directory / name).read_bytes() for name in installer.FILES})
            self.assertEqual(len(calls), 4)
            deploy_env = calls[-1][1]['env']
            self.assertNotIn('ADMIN_PASSWORD', deploy_env)
            self.assertNotIn('COMPOSE_PROJECT_NAME', deploy_env)

    def test_failure_message_names_cause_without_command_arguments(self):
        error = subprocess.CalledProcessError(17, ['docker', 'compose', '--env-file', '/etc/club/compose.env', 'run'])
        message = installer.failure_message(error)
        self.assertIn('docker compose --env-file', message)
        self.assertIn('17', message)
        self.assertNotIn('/etc/club', message)
        self.assertEqual(installer.failure_message(ValueError('Порт занят')), 'Порт занят')
        self.assertIn('повторный запуск', installer.failure_message(KeyboardInterrupt()))

    def test_compose_preserves_smtp_password_and_local_isolation(self):
        with tempfile.TemporaryDirectory() as temp:
            config = pathlib.Path(temp) / 'runtime.env'
            values = installer.configuration(True)
            values['SMTP_PASS'] = 'synthetic $password "quote" \\slash apostrophe\x27'
            config.write_text(installer.dotenv(values))
            command = ['docker', 'compose', '--env-file', str(config), '-f', str(installer.REPO / 'docker-compose.yml'),
                       '-f', str(installer.REPO / 'deploy/compose.local.yml'), 'config']
            resolved = subprocess.check_output([*command, '--environment'], text=True)
            password = next(line.split('=', 1)[1] for line in resolved.splitlines() if line.startswith('SMTP_PASS='))
            self.assertEqual(password, values['SMTP_PASS'])
            effective = json.loads(subprocess.check_output([*command, '--format', 'json'], text=True))
            for name in ('api', 'postgres'):
                self.assertFalse(effective['services'][name].get('ports'))
            for name in ('caddy', 'mailpit'):
                self.assertTrue(all(binding['host_ip'] == '127.0.0.1' for binding in effective['services'][name]['ports']))
            self.assertTrue(effective['networks']['default']['internal'])


if __name__ == '__main__': unittest.main()
