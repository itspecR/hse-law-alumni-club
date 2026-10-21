import importlib
import io
import json
import pathlib
import os
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

    def test_repeat_install_preserves_closed_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            config = pathlib.Path(temp).resolve()
            for name in installer.FILES:
                (config / name).write_text(json.dumps({'version': 2, 'stack': 'mariadb', 'mode': 'local'}) if name == 'install.json' else 'preserved')
                (config / name).chmod(0o600)
            before = {name: (config / name).read_bytes() for name in installer.FILES}
            context = type('Stand', (), {'runtime': {'PUBLIC_URL': 'https://localhost:9443'}, 'lock': lambda self: __import__('contextlib').nullcontext()})()
            with patch.object(installer, 'Context', return_value=context), patch.object(maintenance, 'deploy') as deploy, patch('builtins.print'):
                installer.install(config, True)
                deploy.assert_called_once_with(context)
            self.assertEqual(before, {name: (config / name).read_bytes() for name in installer.FILES})


class RestoreResourceGuards(unittest.TestCase):
    def test_no_space_rejects_restore_before_context_sql_and_decryption(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            snapshot = directory / 'snapshot.enc'
            snapshot.write_bytes(b'x' * 32)
            snapshot.chmod(0o600)
            usage = type('Usage', (), {'free': backup.RESTORE_RESERVE_BYTES + 31})()
            with patch.object(backup.shutil, 'disk_usage', return_value=usage), patch.object(backup, 'Context') as target, patch.object(backup, 'sql') as query, patch.object(backup.subprocess, 'Popen') as decrypt:
                with self.assertRaisesRegex(ValueError, 'Недостаточно места'):
                    backup.restore(Mock(backups=directory), snapshot, directory / 'target.env')
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
            with patch.object(backup, 'MAX_SNAPSHOT_BYTES', 31), patch.object(backup.subprocess, 'Popen') as decrypt:
                with self.assertRaisesRegex(ValueError, 'Размер снимка'):
                    backup.decrypt_snapshot(snapshot, destination, 'a' * 64)
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
            with patch.object(backup.subprocess, 'Popen', return_value=manager):
                with self.assertRaisesRegex(ValueError, 'превысил исходный размер'):
                    backup.decrypt_snapshot(snapshot, destination, 'a' * 64)
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
            backup.decrypt_snapshot(snapshot, destination, key)
            self.assertEqual(destination.read_bytes(), original.read_bytes())
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            snapshot.write_bytes(snapshot.read_bytes()[:-1])
            rejected = directory / 'rejected'
            with self.assertRaisesRegex(ValueError, 'расшифровать'):
                backup.decrypt_snapshot(snapshot, rejected, key)
            self.assertFalse(rejected.exists())


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
            return ''
        return output

    def test_address_and_certificate_subject(self):
        self.assertEqual(installer.server_address(self.ADDRESS), self.ADDRESS)
        self.assertEqual(installer.server_address('club.example.ru'), 'club.example.ru')
        for value in ('127.0.0.1', '0.0.0.0', '224.0.0.1', 'localhost', 'a.ru:9443', 'https://a.ru'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                installer.server_address(value)
        self.assertEqual(installer.subject(self.ADDRESS), 'IP:' + self.ADDRESS)
        self.assertEqual(installer.subject('localhost'), 'DNS:localhost')
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
            signed.assert_called_once_with(config / 'data' / 'tls', self.ADDRESS, config / 'local-ca.crt')
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


if __name__ == '__main__':
    unittest.main()
