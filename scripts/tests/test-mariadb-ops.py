import importlib
import io
import json
import pathlib
import os
import re
import ssl
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'lib'))
common = importlib.import_module('mariadb_common')
installer = importlib.import_module('mariadb_install')
maintenance = importlib.import_module('mariadb_native')
backup = importlib.import_module('mariadb_backup')
restoring = importlib.import_module('mariadb_restore')
certificate = importlib.import_module('certificate')


class NativeDatabaseGuards(unittest.TestCase):
    def test_low_docker_disk_space_stops_before_build_backup_or_writes(self):
        from unittest.mock import Mock

        context = Mock()
        context.runtime = {'APP_ENV': 'production', 'SEED_DEMO': 'false'}
        context.capture.return_value = json.dumps({'services': {'api': {}, 'web': {}, 'operator': {}}})
        with tempfile.TemporaryDirectory() as storage:
            usage = type('Usage', (), {'free': maintenance.MIN_BUILD_FREE_BYTES - 1})()
            with patch.object(maintenance.os, 'geteuid', return_value=0), patch.object(maintenance, 'revision'), patch.object(maintenance, 'output', return_value=storage), patch.object(maintenance.shutil, 'disk_usage', return_value=usage), patch.object(maintenance, 'sql') as query, patch.object(maintenance, 'backup') as backup:
                with self.assertRaisesRegex(ValueError, '5 GiB'):
                    maintenance.deploy(context)
                context.execute.assert_not_called()
                query.assert_not_called()
                backup.assert_not_called()

    def test_build_space_accepts_exact_threshold(self):
        with tempfile.TemporaryDirectory() as storage:
            usage = type('Usage', (), {'free': maintenance.MIN_BUILD_FREE_BYTES})()
            with patch.object(maintenance, 'output', return_value=storage), patch.object(maintenance.shutil, 'disk_usage', return_value=usage) as measured:
                maintenance.verify_build_space()
                measured.assert_called_once_with(pathlib.Path(storage).resolve())

    def test_resume_waits_for_existing_container_without_compose_version_specific_flags(self):
        from unittest.mock import Mock

        context = object.__new__(common.Context)
        context.execute = Mock()
        context.capture = Mock(return_value='original-container-id')
        with patch.object(common, 'output', side_effect=['starting', 'healthy']) as inspect, patch.object(common.time, 'sleep'):
            context.resume()
        context.execute.assert_called_once_with('start', 'api')
        self.assertEqual(inspect.call_count, 2)
        for call in inspect.call_args_list:
            self.assertEqual(call.args[0][-1], 'original-container-id')

    def test_database_identifiers_and_credentials_are_not_sql(self):
        for value in ('club;DROP', 'postgres', 'club_`', 'club_a\n', 'club_' + 'a' * 64):
            with self.subTest(value=value), self.assertRaises(ValueError):
                common.database_name(value)
        with patch.object(common, 'sql') as query:
            with self.assertRaises(ValueError):
                common.create_database('club_test', 'club_ops', 'bad\x27password')
            query.assert_not_called()

    def test_existing_database_is_never_replaced(self):
        with patch.object(common, 'sql', return_value='1') as query:
            with self.assertRaises(ValueError):
                common.create_database('club_test', 'club_ops', 'a' * 64)
            self.assertEqual(query.call_count, 1)
            self.assertTrue(query.call_args.args[0].startswith('SELECT'))

    def test_archives_reject_traversal_links_duplicates_and_missing_files(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            for mode in ('traversal', 'link', 'duplicate', 'missing'):
                archive = directory / (mode + '.tar.gz')
                with tarfile.open(archive, 'w:gz') as destination:
                    member = tarfile.TarInfo('../escape' if mode == 'traversal' else 'data')
                    member.size = 1
                    if mode == 'link':
                        member.type = tarfile.SYMTYPE
                        member.linkname = '/etc/passwd'
                    destination.addfile(member, io.BytesIO(b'x') if mode != 'link' else None)
                    if mode == 'duplicate':
                        destination.addfile(member, io.BytesIO(b'x'))
                with self.subTest(mode=mode), self.assertRaises(ValueError):
                    common.extract_archive(archive, directory / 'out', {'data', 'required'} if mode == 'missing' else None)
            self.assertFalse((directory.parent / 'escape').exists())

    def test_extract_size_is_bounded_before_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            archive = directory / 'oversized.tar.gz'
            with tarfile.open(archive, 'w:gz') as destination:
                member = tarfile.TarInfo('data')
                member.size = 2
                destination.addfile(member, io.BytesIO(b'xx'))
            with self.assertRaises(ValueError):
                common.extract_archive(archive, directory / 'out', maximum=1)
            self.assertFalse((directory / 'out').exists())

    def test_restore_preparation_rejects_shared_ports_before_database_creation(self):
        context = type('Source', (), {'runtime': {'PUBLIC_URL': 'https://localhost:9443'}})()
        with tempfile.TemporaryDirectory() as temp, patch.object(installer, 'create_database') as create:
            for port, mail_port in ((9443, 8125), (9543, 9543), (9543, 9544), (65535, 8125)):
                with self.subTest(port=port, mail=mail_port), self.assertRaises(ValueError):
                    installer.prepare_restore(context, pathlib.Path(temp) / 'new', port, mail_port)
            create.assert_not_called()

    def test_restore_disables_external_integrations_and_transfer_requisites(self):
        production = {name: 'production-value' for name in installer.RESTORE_DISABLED}
        context = type('Source', (), {
            'runtime': {**production, 'PUBLIC_URL': 'https://club.example'},
            'operator': {},
            'values': {},
        })()

        def directory(path, *args):
            pathlib.Path(path).mkdir(mode=0o700, parents=True, exist_ok=True)
            return pathlib.Path(path)

        with tempfile.TemporaryDirectory() as temp, \
                patch.object(installer, 'create_database'), patch.object(installer, 'directory', directory), \
                patch.object(installer, 'self_signed'), patch.object(installer, 'protect_tls'), \
                patch('sys.stdout', io.StringIO()):
            config = pathlib.Path(temp).resolve() / 'new'
            installer.prepare_restore(context, config, 9543, 8125)
            runtime = common.read_env(config / 'runtime.env')
        self.assertTrue({'PAYEE_ACCOUNT', 'PAYEE_BIC', 'BIFIT_CONNECTOR_TOKEN', 'YOOKASSA_SECRET_KEY'} <= set(production))
        self.assertEqual({name: runtime[name] for name in production}, dict.fromkeys(production, ''))

    def test_repeat_install_preserves_closed_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            config = pathlib.Path(temp).resolve()
            for name in installer.FILES:
                (config / name).write_text(json.dumps({'version': 2, 'stack': 'mariadb', 'mode': 'local'}) if name == 'install.json' else 'preserved')
                (config / name).chmod(0o600)
            before = {name: (config / name).read_bytes() for name in installer.FILES}
            context = type('Stand', (), {'runtime': {'PUBLIC_URL': 'https://localhost:9443'}, 'lock': lambda self: __import__('contextlib').nullcontext()})()
            with patch.object(installer, 'Context', return_value=context), patch.object(maintenance, 'deploy') as deploy, \
                    patch.object(installer, 'refresh_self_signed') as refresh, patch('builtins.print'):
                installer.install(config, True)
                deploy.assert_called_once_with(context)
                refresh.assert_called_once_with(config)
            self.assertEqual(before, {name: (config / name).read_bytes() for name in installer.FILES})

    def test_default_configuration_has_no_removed_stack_keys(self):
        values = installer.HELPERS['configuration']()
        removed = [name for name in values if name.startswith('POSTGRES_') or name in ('WEB_DOMAIN', 'ADMIN_DOMAIN', 'ACME_EMAIL')]
        self.assertEqual(removed, [])
        self.assertEqual(len(values['CHECKOUT_DB_PASSWORD']), 64)


class BackupRevision(unittest.TestCase):
    API = "a" * 40

    def revision_of(self, web):
        context = Mock()
        context.capture.side_effect = ["api-container", "web-container"]
        with patch.object(common, "output", side_effect=[self.API, web]), patch("sys.stdout", new_callable=io.StringIO) as printed:
            return common.image_revision(context), printed.getvalue()

    def test_web_built_by_hand_does_not_block_backup(self):
        for web in ("", "b" * 40):
            revision, printed = self.revision_of(web)
            self.assertEqual(revision, self.API)
            self.assertIn("Web собран не из версии API", printed)

    def test_matching_labels_pass_silently(self):
        self.assertEqual(self.revision_of(self.API), (self.API, ""))

    def test_api_without_label_is_rejected(self):
        context = Mock()
        context.capture.side_effect = ["api-container", "web-container"]
        with patch.object(common, "output", side_effect=["", ""]):
            with self.assertRaisesRegex(ValueError, "У API нет revision label"):
                common.image_revision(context)


class RestoreResourceGuards(unittest.TestCase):
    def test_no_space_rejects_restore_before_context_sql_and_decryption(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            snapshot = directory / 'snapshot.enc'
            snapshot.write_bytes(b'x' * 32)
            snapshot.chmod(0o600)
            usage = type('Usage', (), {'free': restoring.RESTORE_RESERVE_BYTES + 31})()
            with patch.object(restoring.shutil, 'disk_usage', return_value=usage), patch.object(restoring, 'Context') as target, patch.object(restoring, 'sql') as query, patch.object(restoring.subprocess, 'Popen') as decrypt:
                with self.assertRaisesRegex(ValueError, 'Недостаточно места'):
                    restoring.restore(Mock(backups=directory), snapshot, directory / 'target.env')
                target.assert_not_called()
                query.assert_not_called()
                decrypt.assert_not_called()

    def test_oversized_ciphertext_rejected_without_starting_process(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            snapshot = directory / 'snapshot.enc'
            snapshot.write_bytes(b'x' * 32)
            snapshot.chmod(0o600)
            destination = directory / 'plaintext'
            with patch.object(restoring, 'MAX_SNAPSHOT_BYTES', 31), patch.object(restoring.subprocess, 'Popen') as decrypt:
                with self.assertRaisesRegex(ValueError, 'Размер снимка'):
                    restoring.decrypt_snapshot(snapshot, destination, 'a' * 64)
                decrypt.assert_not_called()
            self.assertFalse(destination.exists())

    def test_ciphertext_growth_cannot_write_beyond_initial_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            snapshot = directory / 'snapshot.enc'
            snapshot.write_bytes(b'x' * 32)
            snapshot.chmod(0o600)
            destination = directory / 'plaintext'
            process = Mock(stdout=io.BytesIO(b'p' * 33))
            manager = Mock()
            manager.__enter__ = Mock(return_value=process)
            manager.__exit__ = Mock(return_value=False)
            with patch.object(restoring.subprocess, 'Popen', return_value=manager):
                with self.assertRaisesRegex(ValueError, 'превысил исходный размер'):
                    restoring.decrypt_snapshot(snapshot, destination, 'a' * 64)
            process.kill.assert_called_once()
            self.assertFalse(destination.exists())

    def test_real_decryption_roundtrip_and_bad_padding_remove_plaintext(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            original, snapshot = directory / 'source', directory / 'snapshot.enc'
            original.write_bytes(b'synthetic backup payload' * 100)
            key = 'a' * 64
            subprocess.run(['openssl', 'enc', '-aes-256-cbc', '-pbkdf2', '-iter', '200000', '-pass', 'env:BACKUP_ENCRYPTION_KEY', '-in', str(original), '-out', str(snapshot)], env={'PATH': os.environ['PATH'], 'BACKUP_ENCRYPTION_KEY': key}, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            snapshot.chmod(0o600)
            destination = directory / 'plaintext'
            restoring.decrypt_snapshot(snapshot, destination, key)
            self.assertEqual(destination.read_bytes(), original.read_bytes())
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            snapshot.write_bytes(snapshot.read_bytes()[:-1])
            rejected = directory / 'rejected'
            with self.assertRaisesRegex(ValueError, 'расшифровать'):
                restoring.decrypt_snapshot(snapshot, rejected, key)
            self.assertFalse(rejected.exists())


class FakeServer:
    def __init__(self, tables):
        self.databases = {'club_site': set(tables)}

    def __call__(self, query, database=None):
        names = re.findall(r'`(\w+)`', query)
        if query.startswith('SELECT TABLE_NAME'):
            schema = re.search(r"TABLE_SCHEMA='(\w+)'", query)[1]
            return '\n'.join(name + '\tBASE TABLE' for name in sorted(self.databases[schema]))
        if query.startswith('CREATE DATABASE'):
            self.databases[names[0]] = set()
        elif query.startswith('DROP DATABASE'):
            if self.databases.pop(names[0]):
                raise AssertionError('Удалена база с таблицами')
        elif query.startswith('RENAME TABLE'):
            for source, table, destination, renamed in zip(*[iter(names)] * 4):
                self.databases[source].remove(table)
                self.databases[destination].add(renamed)
        else:
            raise AssertionError(query)
        return ''

    def named(self, prefix):
        return [name for name in self.databases if name.startswith(prefix)]


class SiteRestore(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name).resolve()
        self.uploads = self.root / 'data' / 'uploads'
        self.uploads.mkdir(parents=True)
        (self.uploads / 'before.png').write_text('before')
        for name in ('backups', 'state'):
            (self.root / name).mkdir()
        self.snapshot = self.root / 'snapshot.tar.gz.enc'
        self.snapshot.write_bytes(b'x' * 32)
        self.snapshot.chmod(0o600)
        self.context = Mock(
            values={'CLUB_UPLOADS_DIR': str(self.uploads), 'BACKUP_ENCRYPTION_KEY': 'a' * 64},
            runtime={'PUBLIC_URL': 'https://club.example.org'}, database='club_site',
            backups=self.root / 'backups', state=self.root / 'state')
        self.context.stop_writers.return_value = True
        self.server = FakeServer({'club_members', 'club_news'})
        self.keys = []

    def folder(self, path):
        path.mkdir()
        return path

    def opened(self, snapshot, work, key):
        self.keys.append(key)
        return {'revision': 'c' * 40, 'database_bytes': 1, 'uploads_bytes': 1}

    def loaded(self, work, metadata, contour, uploads):
        self.assertEqual(self.server.databases['club_site'], set())
        self.assertEqual(list(uploads.iterdir()), [])
        self.context.stop_writers.assert_called_once()
        self.server.databases['club_site'].add('club_campaigns')
        (uploads / 'after.png').write_text('after')

    def broken(self, work, metadata, contour, uploads):
        self.loaded(work, metadata, contour, uploads)
        raise ValueError('Содержимое таблиц не совпало со снимком')

    def run_restore(self, fill, site='club.example.org', source=None, opened=None):
        with patch.object(restoring, 'sql', self.server), patch.object(restoring, 'directory', self.folder), \
                patch.object(restoring, 'open_snapshot', opened or self.opened), patch.object(restoring, 'require_space'), \
                patch.object(restoring, 'fill', fill), patch('builtins.print'):
            restoring.restore_site(self.context, self.snapshot, site, source)

    def files(self, folder):
        return sorted(path.name for path in folder.iterdir())

    def test_previous_database_and_files_are_set_aside_before_snapshot_is_loaded(self):
        self.run_restore(self.loaded)
        result = json.loads((self.root / 'state' / 'last-restore.json').read_text())
        self.assertEqual(self.server.databases['club_site'], {'club_campaigns'})
        self.assertEqual(self.server.databases[result['kept_database']], {'club_members', 'club_news'})
        self.assertRegex(result['kept_database'], r'^club_kept_\d{8}t\d{6}_[a-f0-9]{4}$')
        self.assertEqual(self.files(self.uploads), ['after.png'])
        self.assertEqual(self.files(pathlib.Path(result['kept_uploads'])), ['before.png'])
        self.assertEqual(pathlib.Path(result['kept_uploads']).parent, self.uploads.parent)
        self.assertEqual((result['snapshot'], result['revision']), ('snapshot.tar.gz.enc', 'c' * 40))
        self.assertEqual(self.keys, ['a' * 64])
        self.context.resume.assert_not_called()

    def test_failed_load_returns_previous_data_and_running_site(self):
        with self.assertRaisesRegex(ValueError, 'не совпало'):
            self.run_restore(self.broken)
        self.assertEqual(self.server.databases['club_site'], {'club_members', 'club_news'})
        self.assertEqual(self.files(self.uploads), ['before.png'])
        failed = self.server.named('club_failed_')
        self.assertEqual([self.server.databases[name] for name in failed], [{'club_campaigns'}])
        self.assertEqual(self.server.named('club_kept_'), [])
        folders = self.files(self.uploads.parent)
        self.assertEqual([name.split('-')[1] for name in folders if name != 'uploads'], ['failed'])
        self.assertEqual(self.files(self.uploads.parent / folders[1]), ['after.png'])
        self.context.resume.assert_called_once()
        self.assertFalse((self.root / 'state' / 'last-restore.json').exists())

    def test_stopped_site_is_not_started_after_failed_load(self):
        self.context.stop_writers.return_value = False
        with self.assertRaisesRegex(ValueError, 'не совпало'):
            self.run_restore(self.broken)
        self.assertEqual(self.server.databases['club_site'], {'club_members', 'club_news'})
        self.context.resume.assert_not_called()

    def test_failure_while_setting_aside_returns_moved_part_without_failed_copy(self):
        (self.uploads / 'second.png').write_text('second')
        rename = os.rename

        def stuck(source, destination):
            if pathlib.Path(source).name == 'second.png' and 'kept' in str(destination):
                raise OSError('Invalid cross-device link')
            rename(source, destination)

        fill = Mock()
        with patch.object(restoring.os, 'rename', stuck), self.assertRaises(OSError):
            self.run_restore(fill)
        fill.assert_not_called()
        self.assertEqual(self.server.databases, {'club_site': {'club_members', 'club_news'}})
        self.assertEqual(self.files(self.uploads), ['before.png', 'second.png'])
        self.assertEqual(self.files(self.uploads.parent), ['uploads'])
        self.context.resume.assert_called_once()

    def test_other_site_address_and_bad_snapshot_leave_site_running(self):
        def rejected(snapshot, work, key):
            raise ValueError('Контрольная сумма снимка не совпала')

        for options, message in (({'site': 'other.example.org'}, 'не совпадает'), ({'site': ''}, 'Нужны'),
                                 ({'opened': rejected}, 'Контрольная сумма')):
            with self.subTest(options), self.assertRaisesRegex(ValueError, message):
                self.run_restore(Mock(), **options)
        self.context.stop_writers.assert_not_called()
        self.assertEqual(self.server.databases, {'club_site': {'club_members', 'club_news'}})
        self.assertEqual(self.files(self.uploads), ['before.png'])

    def test_snapshot_of_another_server_is_opened_with_its_key(self):
        source = self.root / 'source.env'
        source.write_text('BACKUP_ENCRYPTION_KEY="' + 'b' * 64 + '"\n')
        source.chmod(0o600)
        self.run_restore(self.loaded, source=source)
        self.assertEqual(self.keys, ['b' * 64])

    def test_source_env_without_key_or_with_open_access_is_refused(self):
        source = self.root / 'source.env'
        for content, mode, message in (('BACKUP_ENCRYPTION_KEY="short"\n', 0o600, '64 hex'), ('BACKUP_ENCRYPTION_KEY="' + 'b' * 64 + '"\n', 0o644, '0600')):
            source.write_text(content)
            source.chmod(mode)
            with self.subTest(mode), self.assertRaisesRegex(ValueError, message):
                self.run_restore(Mock(), source=source)
        self.context.stop_writers.assert_not_called()

    def test_views_and_unexpected_table_names_stop_the_move(self):
        for rows in ('club_news\tBASE TABLE\nclub_report\tVIEW', 'club`news\tBASE TABLE'):
            with self.subTest(rows), patch.object(restoring, 'sql', return_value=rows) as query:
                with self.assertRaisesRegex(ValueError, 'перенос остановлен'):
                    restoring.move_tables('club_site', 'club_kept_1')
                query.assert_called_once()


class ServerContour(unittest.TestCase):
    ADDRESS = '203.0.113.10'

    def answers(self, label, default='', check=None, hidden=False):
        if label.startswith('Контур'):
            return 'server'
        if label.startswith('IP-адрес'):
            self.assertEqual(default, self.ADDRESS)
            return self.ADDRESS
        return default

    def system(self, busy):
        def output(arguments, **options):
            if arguments[0] == 'hostname':
                return self.ADDRESS + ' 172.17.0.1'
            if arguments[0] == 'ss' and arguments[-1] in busy:
                return 'LISTEN 0 4096 0.0.0.0:' + arguments[-1].rsplit(':', 1)[1] + ' 0.0.0.0:*'
            if arguments[0] == 'ss' and arguments[-1] == 'sport = :3306':
                return 'LISTEN 0 80 127.0.0.1:3306 0.0.0.0:*'
            return ''
        return output

    def test_address_and_certificate_subject(self):
        self.assertEqual(installer.server_address(self.ADDRESS), self.ADDRESS)
        self.assertEqual(installer.server_address('club.example.ru'), 'club.example.ru')
        for value in ('127.0.0.1', '0.0.0.0', '224.0.0.1', 'localhost', 'a.ru:9443', 'https://a.ru'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                installer.server_address(value)
        self.assertEqual(installer.subjects([self.ADDRESS, 'localhost']), 'IP:' + self.ADDRESS + ', DNS:localhost')
        self.assertEqual(installer.public_address('https://203.0.113.7:9443/'), 'https://203.0.113.7:9443')
        self.assertEqual(installer.public_address('https://club.example.ru'), 'https://club.example.ru')
        for value in ('http://203.0.113.7:9443', 'https://127.0.0.1:9443', 'https://a.ru/path', 'https://u:p@a.ru', '203.0.113.7'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                installer.public_address(value)
        self.assertEqual(installer.public_url(self.ADDRESS, 9443), 'https://' + self.ADDRESS + ':9443')
        self.assertEqual(installer.public_url('club.example.ru', 443), 'https://club.example.ru')
        with self.assertRaises(ValueError):
            installer.contour('4')

    def test_busy_port_stops_before_system_changes(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.dict(installer.HELPERS, {'prompt': self.answers}), \
                patch.object(installer, 'output', side_effect=self.system({'sport = :9443'})), \
                patch.object(installer, 'run') as run, \
                patch.object(installer, 'create_database') as create:
            with self.assertRaisesRegex(ValueError, '9443'):
                installer.install(pathlib.Path(temp).resolve() / 'club', False)
            run.assert_not_called()
            create.assert_not_called()

    def test_server_contour_publishes_chosen_ports_with_own_certificate(self):
        def certificate(tls, address, authority):
            for name in ('privkey.pem', 'fullchain.pem'):
                (tls / name).write_text('synthetic')
            authority.write_text('synthetic')

        def folder(path, *arguments):
            path = pathlib.Path(path)
            path.mkdir(parents=True, exist_ok=True)
            return path

        with tempfile.TemporaryDirectory() as temp, \
                patch.dict(installer.HELPERS, {'prompt': self.answers}), \
                patch.object(installer, 'output', side_effect=self.system(set())), \
                patch.object(installer, 'run'), \
                patch.object(installer, 'sql', return_value='0'), \
                patch.object(installer, 'create_database'), \
                patch.object(installer, 'directory', side_effect=folder), \
                patch.object(installer.os, 'chown'), \
                patch.object(installer, 'self_signed', side_effect=certificate) as signed, \
                patch.object(installer, 'wait_for_apt'), \
                patch.object(installer, 'Context'), \
                patch.object(maintenance, 'deploy'), \
                patch('builtins.print'):
            config = pathlib.Path(temp).resolve() / 'club'
            installer.install(config, False)
            signed.assert_called_once_with(config / 'data' / 'tls', [self.ADDRESS], config / 'local-ca.crt')
            compose = common.read_env(config / 'compose.env')
            runtime = common.read_env(config / 'runtime.env')
            metadata = json.loads((config / 'install.json').read_text())
        self.assertEqual(metadata['mode'], 'server')
        self.assertEqual(runtime['PUBLIC_URL'], 'https://' + self.ADDRESS + ':9443')
        self.assertEqual(compose['PUBLIC_URL'], runtime['PUBLIC_URL'])
        self.assertEqual(compose['CLUB_BIND_ADDRESS'], '0.0.0.0')
        self.assertEqual((compose['CLUB_HTTPS_PORT'], compose['CLUB_HTTP_PORT']), ('9443', '9080'))
        self.assertEqual(compose['HTTPS_CA_FILE'], str(config / 'local-ca.crt'))
        self.assertEqual(compose['COMPOSE_PROFILES'], 'local')
        self.assertEqual(runtime['APP_ENV'], 'production')
        self.assertEqual(runtime['SEED_DEMO'], 'false')


class PackageLockWait(unittest.TestCase):
    HOLD = 'import fcntl,sys,time;h=open(sys.argv[1],"a");fcntl.lockf(h,fcntl.LOCK_EX);print("held",flush=True);time.sleep(float(sys.argv[2]))'

    def hold(self, path, seconds):
        process = subprocess.Popen([sys.executable, '-c', self.HOLD, str(path), str(seconds)], stdout=subprocess.PIPE, text=True)
        self.assertEqual(process.stdout.readline().strip(), 'held')
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        return process

    def test_lock_held_by_another_process_is_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            lock = pathlib.Path(temp) / 'lock-frontend'
            lock.touch()
            with patch.object(installer, 'APT_LOCKS', (lock, pathlib.Path(temp) / 'missing')):
                self.assertFalse(installer.apt_busy())
                self.hold(lock, 30)
                self.assertTrue(installer.apt_busy())
            self.assertFalse((pathlib.Path(temp) / 'missing').exists())

    def test_wait_returns_after_release_and_stops_after_deadline(self):
        with tempfile.TemporaryDirectory() as temp:
            lock = pathlib.Path(temp) / 'lock'
            lock.touch()
            with patch.object(installer, 'APT_LOCKS', (lock,)), patch.object(installer, 'APT_POLL_SECONDS', 0.05), \
                    patch('builtins.print'):
                self.hold(lock, 0.5)
                installer.wait_for_apt()
                self.assertFalse(installer.apt_busy())
                self.hold(lock, 30)
                with patch.object(installer, 'APT_WAIT_SECONDS', 0.2), self.assertRaisesRegex(ValueError, '10 минут'):
                    installer.wait_for_apt()


class SelfSignedCertificate(unittest.TestCase):
    def issue(self, directory, address):
        tls = pathlib.Path(directory) / 'tls'
        tls.mkdir(exist_ok=True)
        installer.self_signed(tls, [address] if isinstance(address, str) else address, pathlib.Path(directory) / 'local-ca.crt')
        return tls

    def test_certificate_matches_only_its_address_and_validity(self):
        with tempfile.TemporaryDirectory() as temp:
            certificate = self.issue(temp, '192.0.2.10') / 'fullchain.pem'
            renew = installer.CERTIFICATE_RENEW_SECONDS
            text = subprocess.check_output(['openssl', 'x509', '-in', str(certificate), '-noout', '-text'], text=True)
            self.assertIn('CA:FALSE', text)
            self.assertIn('TLS Web Server Authentication', text)
            self.assertTrue(common.certificate_current(certificate, '192.0.2.10', renew))
            self.assertFalse(common.certificate_current(certificate, '127.0.0.1', renew))
            self.assertFalse(common.certificate_current(certificate, 'club.example.ru', renew))
            self.assertFalse(common.certificate_current(certificate, '192.0.2.10', 400 * 24 * 3600))
            localhost = self.issue(temp, 'localhost') / 'fullchain.pem'
            self.assertTrue(common.certificate_current(localhost, 'localhost', renew))
            self.assertFalse(common.certificate_current(pathlib.Path(temp) / 'missing.pem', 'localhost', renew))

    def test_repeat_install_reissues_certificate_for_public_url(self):
        with tempfile.TemporaryDirectory() as temp:
            config = pathlib.Path(temp).resolve()
            tls = self.issue(config, '127.0.0.1')
            (config / 'compose.env').write_text('PUBLIC_URL="https://192.0.2.10:9443"\nCLUB_TLS_DIR="' + str(tls) + '"\n')
            (config / 'compose.env').chmod(0o600)
            with patch.object(installer, 'protect_tls') as protect, patch('builtins.print'):
                installer.refresh_self_signed(config)
                protect.assert_called_once_with(tls)
                before = (tls / 'fullchain.pem').read_bytes()
                installer.refresh_self_signed(config)
                self.assertEqual((tls / 'fullchain.pem').read_bytes(), before)
            self.assertTrue(common.certificate_current(tls / 'fullchain.pem', '192.0.2.10', installer.CERTIFICATE_RENEW_SECONDS))
            self.assertEqual((config / 'local-ca.crt').read_bytes(), before)

    def test_certificate_error_names_the_reason_and_is_not_retried(self):
        error = ssl.SSLCertVerificationError()
        error.verify_message = "IP address mismatch, certificate is not valid for '192.0.2.10'."
        self.assertEqual(common.failure_detail(error, None), 'сертификат: ' + error.verify_message)
        self.assertEqual(common.failure_detail(OSError(), 502), 'OSError (HTTP 502)')
        self.assertEqual(common.failure_detail(ValueError('API не готов'), 200), 'API не готов (HTTP 200)')
        context = Mock(values={'HTTPS_CA_FILE': '', 'CLUB_BIND_ADDRESS': '0.0.0.0', 'CLUB_HTTPS_PORT': '9443'},
                       runtime={'PUBLIC_URL': 'https://192.0.2.10:9443'})
        with patch.object(common, 'https_connection', side_effect=error) as connect, \
                patch.object(common.time, 'sleep') as sleep:
            with self.assertRaisesRegex(ValueError, 'IP address mismatch'):
                common.Context.verify_http(context)
        sleep.assert_not_called()
        target, base, _ = connect.call_args.args
        self.assertEqual(target, ('127.0.0.1', 9443))
        self.assertEqual(base.hostname, '192.0.2.10')


class DeployOrder(unittest.TestCase):
    def test_images_are_fetched_before_backup_and_api_stays_stopped_until_migrations(self):
        calls = []
        context = Mock(values={'COMPOSE_PROFILES': 'local', 'CLUB_OPERATOR_IMAGE': 'operator'}, project='club-mariadb-test')
        context.capture.side_effect = lambda *args: 'container' if args[:2] == ('ps', '--status') else ''
        context.execute.side_effect = lambda *args, **options: calls.append(('execute',) + args[:2])
        context.stop_writers.side_effect = lambda: calls.append(('stop',))
        context.operator_command.side_effect = lambda command: calls.append(('operator', command))
        context.verify_http.side_effect = lambda: calls.append(('verify',))
        with tempfile.TemporaryDirectory() as temp:
            context.state = pathlib.Path(temp)
            with patch.object(maintenance, 'revision', return_value='a' * 40), \
                    patch.object(maintenance, 'preflight', side_effect=lambda c: calls.append(('preflight',))), \
                    patch.object(maintenance, 'prefetch_images', side_effect=lambda c: calls.append(('prefetch',))), \
                    patch.object(maintenance, 'backup', side_effect=lambda c, resume: calls.append(('backup', resume))), \
                    patch.object(maintenance, 'output', return_value='sha256:operator'), \
                    patch.object(maintenance, 'record'), patch.object(maintenance.os, 'geteuid', return_value=0), \
                    patch('builtins.print'):
                maintenance.deploy(context)
        kinds = [call[0] for call in calls]
        self.assertLess(kinds.index('preflight'), kinds.index('prefetch'))
        self.assertLess(kinds.index('prefetch'), kinds.index('backup'))
        self.assertLess(kinds.index('backup'), kinds.index('operator'))
        self.assertIn(('backup', False), calls)
        self.assertEqual(kinds[-1], 'verify')

    def test_missing_runtime_image_is_pulled_with_retries_before_changes(self):
        configuration = {'services': {'api': {'build': {}, 'image': 'api'}, 'mailpit': {'image': 'mailpit:1@sha256:abc'}}}
        context = Mock()
        context.capture.return_value = json.dumps(configuration)
        context.execute.side_effect = [subprocess.CalledProcessError(1, ['docker']), None]
        with patch.object(maintenance, 'image_present', return_value=False) as present, \
                patch.object(common.time, 'sleep') as sleep, patch('builtins.print'):
            maintenance.prefetch_images(context)
        present.assert_called_once_with('mailpit:1@sha256:abc')
        self.assertEqual(context.execute.call_count, 2)
        context.execute.assert_called_with('pull', '--quiet', 'mailpit')
        sleep.assert_called_once_with(common.NETWORK_PAUSE_SECONDS)

    def test_retries_stop_after_limit(self):
        action = Mock(side_effect=subprocess.CalledProcessError(1, ['docker']))
        with patch.object(common.time, 'sleep') as sleep, patch('builtins.print'):
            with self.assertRaises(subprocess.CalledProcessError):
                common.with_retries(action, 'Сборка')
        self.assertEqual(action.call_count, common.NETWORK_ATTEMPTS)
        self.assertEqual(sleep.call_count, common.NETWORK_ATTEMPTS - 1)

    def test_preflight_certificate_must_match_public_url(self):
        with tempfile.TemporaryDirectory() as temp:
            tls = pathlib.Path(temp) / 'tls'
            tls.mkdir()
            installer.self_signed(tls, ['192.0.2.10'], pathlib.Path(temp) / 'local-ca.crt')
            context = Mock(values={'CLUB_TLS_DIR': str(tls)}, runtime={'PUBLIC_URL': 'https://192.0.2.10:9443'})
            maintenance.verify_certificate(context, 0)
            context.runtime = {'PUBLIC_URL': 'https://192.0.2.11:9443'}
            with self.assertRaisesRegex(ValueError, '192.0.2.11'):
                maintenance.verify_certificate(context, 0)


class SharedMariaDB(unittest.TestCase):
    def packages(self, installed):
        def output(arguments, **options):
            if arguments[0] == 'dpkg-query':
                if arguments[-1] in installed:
                    return 'install ok installed'
                raise subprocess.CalledProcessError(1, arguments)
            return ''
        return output

    def test_running_mariadb_is_not_reinstalled_or_upgraded(self):
        with patch.object(installer, 'output', side_effect=self.packages({'mariadb-server'})), \
                patch.object(installer, 'run') as run, patch.object(installer, 'wait_for_apt') as wait:
            installer.ensure_mariadb()
        run.assert_called_once_with(['systemctl', 'enable', '--now', 'mariadb'])
        wait.assert_not_called()

    def test_missing_mariadb_is_installed_without_service_restarts(self):
        with patch.object(installer, 'output', side_effect=self.packages(set())), \
                patch.object(installer, 'run') as run, patch.object(installer, 'wait_for_apt'):
            installer.ensure_mariadb()
        arguments, options = run.call_args_list[0]
        self.assertIn('mariadb-server', arguments[0])
        self.assertEqual(options['env']['NEEDRESTART_MODE'], 'l')

    def test_existing_mysql_is_not_replaced(self):
        with patch.object(installer, 'output', side_effect=self.packages({'mysql-server-8.0'})), \
                patch.object(installer, 'run') as run, patch.object(installer, 'wait_for_apt'):
            with self.assertRaisesRegex(ValueError, 'MySQL'):
                installer.ensure_mariadb()
        run.assert_not_called()


class PublicAddressChange(unittest.TestCase):
    def config(self, temp):
        config = pathlib.Path(temp).resolve()
        tls = config / 'tls'
        tls.mkdir()
        installer.self_signed(tls, ['192.168.3.155'], config / 'local-ca.crt')
        files = {
            'runtime.env': {'PUBLIC_URL': 'https://192.168.3.155:9443', 'SMTP_PASS': 'synthetic $pass "q"'},
            'operator.env': {'PUBLIC_URL': 'https://192.168.3.155:9443', 'DATABASE_URL': 'mariadb://x'},
            'compose.env': {'PUBLIC_URL': 'https://192.168.3.155:9443', 'CLUB_TLS_DIR': str(tls), 'CLUB_HTTPS_PORT': '9443'},
        }
        for name, values in files.items():
            (config / name).write_text(installer.HELPERS['dotenv'](values))
            (config / name).chmod(0o600)
        return config, tls

    def test_external_address_keeps_lan_name_and_other_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            config, tls = self.config(temp)
            with patch('builtins.print'), patch.object(installer, 'protect_tls'):
                installer.change_public_url(config, installer.public_address('https://62.112.123.10:9443'))
                installer.refresh_self_signed(config)
            runtime = common.read_env(config / 'runtime.env')
            compose = common.read_env(config / 'compose.env')
            for name in ('runtime.env', 'operator.env', 'compose.env'):
                self.assertEqual(common.read_env(config / name)['PUBLIC_URL'], 'https://62.112.123.10:9443')
                self.assertEqual(os.stat(config / name).st_mode & 0o777, 0o600)
            self.assertEqual(runtime['SMTP_PASS'], 'synthetic $pass "q"')
            self.assertEqual(common.read_env(config / 'operator.env')['DATABASE_URL'], 'mariadb://x')
            self.assertEqual(compose['CLUB_TLS_NAMES'], '192.168.3.155')
            for name in ('62.112.123.10', '192.168.3.155'):
                self.assertTrue(common.certificate_current(tls / 'fullchain.pem', name, installer.CERTIFICATE_RENEW_SECONDS))
            self.assertFalse(list(config.glob('*.partial')))

    def test_public_url_is_refused_for_other_contours_and_new_installs(self):
        with tempfile.TemporaryDirectory() as temp:
            config = pathlib.Path(temp).resolve()
            for name in installer.FILES:
                (config / name).write_text(json.dumps({'version': 2, 'stack': 'mariadb', 'mode': 'local'}) if name == 'install.json' else '')
                (config / name).chmod(0o600)
            with patch('builtins.print'), self.assertRaisesRegex(ValueError, 'контуре'):
                installer.install(config, False, 'https://62.112.123.10:9443')
        with tempfile.TemporaryDirectory() as temp, self.assertRaisesRegex(ValueError, 'сначала'):
            installer.install(pathlib.Path(temp).resolve() / 'club', False, 'https://62.112.123.10:9443')

    def test_new_server_install_adds_lan_address_when_external_is_chosen(self):
        answers = {'IP-адрес': '62.112.123.10'}
        def prompt(label, default='', check=None, hidden=False):
            return next((value for key, value in answers.items() if label.startswith(key)), default)
        values = {}
        with patch.dict(installer.HELPERS, {'prompt': prompt}), \
                patch.object(installer, 'output', return_value='192.168.3.155 172.17.0.1 fe80::1'):
            names, https, http = installer.ask_server(values)
        self.assertEqual(names, ['62.112.123.10', '192.168.3.155'])
        self.assertEqual(values['PUBLIC_URL'], 'https://62.112.123.10:9443')


    def public_answers(self, answers):
        with tempfile.TemporaryDirectory() as temp:
            files = {name: pathlib.Path(temp, name) for name in ('fullchain.pem', 'privkey.pem')}
            for path in files.values():
                path.write_text('')
            answers = {'Абсолютный путь к fullchain': str(files['fullchain.pem']),
                       'Абсолютный путь к privkey': str(files['privkey.pem']), **answers}
            def prompt(label, default='', check=None, hidden=False):
                value = next((value for key, value in answers.items() if label.startswith(key)), '') or default
                return check(value) if check else value
            values = {}
            with patch.dict(installer.HELPERS, {'prompt': prompt}):
                installer.ask_public(values)
            return values

    def test_public_install_uses_one_mailbox_unless_told_otherwise(self):
        base = {'Домен': 'club.example.org', 'Email первого': 'admin@club.example.org',
                'Сервер SMTP': 'smtp.example.org', 'Логин SMTP': 'club@club.example.org', 'Пароль': 'synthetic'}
        one = self.public_answers(base)
        two = self.public_answers({**base, 'Разрешённый': 'noreply@club.example.org'})
        self.assertEqual((one['SMTP_FROM'], one['OFFICE_EMAIL']), ('club@club.example.org', 'club@club.example.org'))
        self.assertEqual((two['SMTP_FROM'], two['OFFICE_EMAIL']), ('noreply@club.example.org', 'noreply@club.example.org'))
        self.assertEqual(one['PUBLIC_URL'], 'https://club.example.org')


class SettingsAdoption(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = pathlib.Path(self.temp.name).resolve()
        self.current = {'PUBLIC_URL': 'https://club.example.org', 'SMTP_HOST': 'smtp.new.example', 'SMTP_PASS': 'new $pass "q"',
                        'AUTH_SECRET': 'new-secret', 'YOOKASSA_SHOP_ID': '', 'PAYEE_NAME': '', 'JOBS_ENABLED': 'false', 'RATE_LIMIT_MAX': '1000'}
        self.write('runtime.env', self.current)
        self.write('operator.env', {**self.current, 'DATABASE_URL': 'mariadb://operator'})
        self.source = self.config / 'previous-runtime.env'
        self.previous = {'PUBLIC_URL': 'https://old.example.org', 'SMTP_HOST': 'smtp.old.example', 'SMTP_PASS': 'old-pass',
                         'AUTH_SECRET': 'old-secret', 'YOOKASSA_SHOP_ID': '123456', 'YOOKASSA_SECRET_KEY': 'synthetic $key "k"',
                         'PAYEE_NAME': 'АНО «Клуб»', 'JOBS_ENABLED': 'true', 'RATE_LIMIT_MAX': '1000', 'OFFICE_EMAIL': ''}
        self.write('previous-runtime.env', self.previous)
        self.printed = []

    def write(self, name, values):
        (self.config / name).write_text(installer.HELPERS['dotenv'](values))
        (self.config / name).chmod(0o600)

    def adopt(self):
        context = Mock(values={'CLUB_RUNTIME_ENV': str(self.config / 'runtime.env'), 'CLUB_OPERATOR_ENV': str(self.config / 'operator.env')},
                       runtime=common.read_env(self.config / 'runtime.env'))
        with patch('builtins.print', side_effect=lambda text: self.printed.append(text)):
            installer.adopt_settings(context, self.source)

    def test_integrations_move_and_server_bound_settings_stay(self):
        self.adopt()
        runtime, operator = (common.read_env(self.config / name) for name in ('runtime.env', 'operator.env'))
        for values in (runtime, operator):
            self.assertEqual((values['YOOKASSA_SHOP_ID'], values['YOOKASSA_SECRET_KEY']), ('123456', 'synthetic $key "k"'))
            self.assertEqual((values['PAYEE_NAME'], values['JOBS_ENABLED']), ('АНО «Клуб»', 'true'))
            self.assertEqual((values['PUBLIC_URL'], values['SMTP_HOST'], values['SMTP_PASS'], values['AUTH_SECRET']),
                             ('https://club.example.org', 'smtp.new.example', 'new $pass "q"', 'new-secret'))
        self.assertEqual(operator['DATABASE_URL'], 'mariadb://operator')
        copies = sorted(self.config.glob('*.before-*'))
        self.assertEqual([path.name.split('.before-')[0] for path in copies], ['operator.env', 'runtime.env'])
        self.assertEqual(common.read_env(copies[1]), self.current)
        for path in (*copies, self.config / 'runtime.env', self.config / 'operator.env'):
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertFalse(list(self.config.glob('*.partial')))

    def test_report_names_settings_without_values(self):
        self.adopt()
        report = '\n'.join(self.printed)
        self.assertIn('Перенесены настройки: YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY, PAYEE_NAME, JOBS_ENABLED', report)
        self.assertIn('Не переносятся и отличаются от прежнего сервера: SMTP_HOST;', report)
        for value in ('123456', 'synthetic', 'АНО', 'old-pass', 'old-secret', 'smtp.old.example'):
            self.assertNotIn(value, report)

    def test_matching_settings_change_nothing(self):
        self.write('previous-runtime.env', {'JOBS_ENABLED': 'false', 'RATE_LIMIT_MAX': '1000', 'SMTP_HOST': 'smtp.new.example'})
        before = (self.config / 'runtime.env').read_bytes()
        self.adopt()
        self.assertEqual((self.config / 'runtime.env').read_bytes(), before)
        self.assertFalse(list(self.config.glob('*.before-*')))
        self.assertEqual(self.printed, ['Переносимые настройки уже совпадают с прежним сервером'])

    def test_open_or_missing_source_is_refused_before_changes(self):
        before = (self.config / 'runtime.env').read_bytes()
        self.source.chmod(0o644)
        with self.assertRaisesRegex(ValueError, '0600'):
            self.adopt()
        self.source = None
        with self.assertRaisesRegex(ValueError, 'source-env'):
            self.adopt()
        self.assertEqual((self.config / 'runtime.env').read_bytes(), before)
        self.assertFalse(list(self.config.glob('*.before-*')))

    def test_adopted_and_manual_lists_cover_only_known_settings(self):
        known = set(installer.HELPERS['configuration']())
        self.assertFalse(set(installer.ADOPTED_SETTINGS) - known)
        self.assertFalse(set(installer.MANUAL_SETTINGS) - known)
        self.assertFalse(set(installer.ADOPTED_SETTINGS) & set(installer.MANUAL_SETTINGS))
        bound = {'PUBLIC_URL', 'AUTH_SECRET', 'ADMIN_AUTH_SECRET', 'CHECKOUT_DATABASE_URL', 'CHECKOUT_DB_PASSWORD', 'ADMIN_EMAIL',
                 'ADMIN_PASSWORD', 'SMTP_PASS', 'BACKUP_ENCRYPTION_KEY', 'APP_ENV', 'SEED_DEMO'}
        self.assertFalse(set(installer.ADOPTED_SETTINGS) & bound)


def openssl(*arguments):
    subprocess.run(['openssl', *arguments], check=True, capture_output=True)


def extensions(folder, name, text):
    path = pathlib.Path(folder, name + '.ext')
    path.write_text(text)
    return str(path)


class CertificateCommands(unittest.TestCase):
    DOMAIN = 'club.example.org'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        authority = extensions(self.root, 'ca', 'basicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,cRLSign\n')
        openssl('req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '30', '-subj', '/CN=Test Root',
                '-keyout', str(self.root / 'root.key'), '-out', str(self.root / 'root.pem'),
                '-addext', 'basicConstraints=critical,CA:TRUE', '-addext', 'keyUsage=critical,keyCertSign,cRLSign')
        openssl('req', '-new', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=Test Intermediate',
                '-keyout', str(self.root / 'middle.key'), '-out', str(self.root / 'middle.csr'))
        openssl('x509', '-req', '-in', str(self.root / 'middle.csr'), '-days', '30', '-CA', str(self.root / 'root.pem'),
                '-CAkey', str(self.root / 'root.key'), '-CAcreateserial', '-extfile', authority,
                '-out', str(self.root / 'middle.pem'))
        with patch.object(certificate.time, 'strftime', return_value='request'):
            self.folder = certificate.request(self.DOMAIN, self.root / 'home')

    def tearDown(self):
        self.temp.cleanup()

    def issue(self, names=None, days=30, csr=None):
        names = names or [self.DOMAIN, 'www.' + self.DOMAIN]
        site = extensions(self.root, 'site', 'subjectAltName=' + ','.join('DNS:' + name for name in names)
                          + '\nextendedKeyUsage=serverAuth\nbasicConstraints=CA:FALSE\n')
        target = self.root / ('site-' + str(len(list(self.root.glob('site-*')))) + '.pem')
        openssl('x509', '-req', '-in', str(csr or self.folder / certificate.REQUEST_NAME), '-days', str(days),
                '-CA', str(self.root / 'middle.pem'), '-CAkey', str(self.root / 'middle.key'), '-CAcreateserial',
                '-extfile', site, '-out', str(target))
        return target

    def prepare(self, *files):
        return certificate.prepare(self.folder, [str(path) for path in files], self.root / 'root.pem')

    def test_request_keeps_private_key_and_asks_for_domain_and_www(self):
        text = subprocess.run(['openssl', 'req', '-noout', '-text', '-in', str(self.folder / certificate.REQUEST_NAME)],
                              check=True, capture_output=True, text=True).stdout
        self.assertIn('DNS:club.example.org, DNS:www.club.example.org', text)
        self.assertEqual(oct((self.folder / certificate.KEY_NAME).stat().st_mode & 0o777), '0o600')
        self.assertEqual(certificate.request_names(self.folder), ['club.example.org', 'www.club.example.org'])

    def test_prepare_builds_full_chain_with_site_first(self):
        site = self.issue()
        ends = self.prepare(self.root / 'middle.pem', site)
        chain = (self.folder / certificate.CHAIN_NAME).read_text()
        self.assertTrue(chain.startswith(site.read_text().strip()[:80]))
        self.assertIn(chain.split(certificate.PEM_END)[1].strip()[:80], (self.root / 'middle.pem').read_text())
        self.assertTrue(ends.endswith('GMT'))

    def test_prepare_refuses_broken_certificates(self):
        other = self.root / 'other'
        (self.root / 'site.der').write_bytes(bytes([0x30, 0x82, 0xFF, 0xFE]))
        openssl('req', '-new', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=' + self.DOMAIN,
                '-keyout', str(other.with_suffix('.key')), '-out', str(other.with_suffix('.csr')))
        cases = [
            ((self.issue(),), 'Цепочка.*unable to get local issuer certificate\\)$'),
            ((self.issue([self.DOMAIN]), self.root / 'middle.pem'), 'www.club.example.org'),
            ((self.issue(days=2), self.root / 'middle.pem'), 'истекает'),
            ((self.issue(csr=other.with_suffix('.csr')), self.root / 'middle.pem'), 'ключу'),
            ((self.root / 'root.key',), 'PEM'),
            ((self.root / 'site.der',), 'PEM'),
        ]
        for files, message in cases:
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                self.prepare(*files)
        self.assertFalse((self.folder / certificate.CHAIN_NAME).exists())

    def site(self, address='club.example.org'):
        tls = self.root / 'tls'
        tls.mkdir(exist_ok=True)
        for name in (certificate.CHAIN_NAME, certificate.KEY_NAME):
            (tls / name).write_text('old ' + name)
        context = Mock(runtime={'PUBLIC_URL': 'https://' + address}, values={'CLUB_TLS_DIR': str(tls)})
        return context, tls

    def test_apply_replaces_certificate_and_checks_site(self):
        self.prepare(self.root / 'middle.pem', self.issue())
        context, tls = self.site()
        with patch.object(certificate, 'protect_tls'):
            certificate.apply(self.folder, context, self.root / 'root.pem')
        self.assertEqual((tls / certificate.CHAIN_NAME).read_text(), (self.folder / certificate.CHAIN_NAME).read_text())
        context.execute.assert_called_once_with('up', '-d', '--wait', '--force-recreate', 'nginx')
        context.verify_http.assert_called_once_with()
        self.assertEqual(list(self.folder.glob(certificate.PREVIOUS_SUFFIX + '*')), [])

    def test_apply_returns_previous_certificate_when_site_check_fails(self):
        self.prepare(self.root / 'middle.pem', self.issue())
        context, tls = self.site()
        context.verify_http.side_effect = [ValueError('сертификат: unable to verify'), None]
        with patch.object(certificate, 'protect_tls'), self.assertRaisesRegex(ValueError, 'возвращён прежний'):
            certificate.apply(self.folder, context, self.root / 'root.pem')
        self.assertEqual((tls / certificate.CHAIN_NAME).read_text(), 'old fullchain.pem')
        self.assertEqual((tls / certificate.KEY_NAME).read_text(), 'old privkey.pem')
        self.assertEqual(context.execute.call_count, 2)

    def test_apply_refuses_other_domain_and_unprepared_folder(self):
        context, tls = self.site('other.example.org')
        with self.assertRaisesRegex(ValueError, 'other.example.org'):
            certificate.apply(self.folder, context, self.root / 'root.pem')
        context, tls = self.site()
        with self.assertRaisesRegex(ValueError, 'prepare'):
            certificate.apply(self.folder, context, self.root / 'root.pem')
        self.assertEqual((tls / certificate.CHAIN_NAME).read_text(), 'old fullchain.pem')
        context.execute.assert_not_called()

if __name__ == '__main__':
    unittest.main()
