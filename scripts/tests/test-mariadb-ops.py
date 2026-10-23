import importlib
import io
import json
import pathlib
import os
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
            with patch.object(installer, 'Context', return_value=context), patch.object(maintenance, 'deploy') as deploy, \
                    patch.object(installer, 'refresh_self_signed') as refresh, patch('builtins.print'):
                installer.install(config, True)
                deploy.assert_called_once_with(context)
                refresh.assert_called_once_with(config)
            self.assertEqual(before, {name: (config / name).read_bytes() for name in installer.FILES})


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


if __name__ == '__main__':
    unittest.main()
