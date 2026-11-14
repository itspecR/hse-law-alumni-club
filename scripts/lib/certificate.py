import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from mariadb_common import HELPERS, Context, output, run
from mariadb_install import protect_tls

CERTIFICATE_HOME = pathlib.Path("/etc/club-certificate")
INSTALLED_ENV_FILE = pathlib.Path("/etc/club/compose.env")
SYSTEM_TRUST = pathlib.Path("/etc/ssl/certs/ca-certificates.crt")
KEY_SPEC = "rsa:2048"
WWW = "www."
MIN_VALID_DAYS = 7
SECONDS_PER_DAY = 86400
PEM_BEGIN = "-----BEGIN CERTIFICATE-----"
PEM_END = "-----END CERTIFICATE-----"
KEY_NAME = "privkey.pem"
REQUEST_NAME = "request.csr"
DOMAIN_NAME = "domain.txt"
CHAIN_NAME = "fullchain.pem"
PREVIOUS_SUFFIX = ".previous"
SCRIPT = "sudo ./scripts/certificate.sh"


def site_names(domain):
    host = HELPERS["hostname"](domain.removeprefix(WWW))
    return [host, WWW + host]


def request(domain, home=CERTIFICATE_HOME):
    names = site_names(domain)
    target = home / time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    target.mkdir(mode=0o700, parents=True)
    run(
        [
            "openssl",
            "req",
            "-new",
            "-newkey",
            KEY_SPEC,
            "-nodes",
            "-keyout",
            str(target / KEY_NAME),
            "-out",
            str(target / REQUEST_NAME),
            "-subj",
            "/CN=" + names[1],
        ],
        capture_output=True,
    )
    (target / KEY_NAME).chmod(0o600)
    (target / DOMAIN_NAME).write_text(names[0] + "\n")
    return target


def pem_blocks(paths):
    text = "".join(pathlib.Path(path).read_text(errors="replace") for path in paths)
    blocks = [PEM_BEGIN + part.split(PEM_END)[0] + PEM_END + "\n" for part in text.split(PEM_BEGIN)[1:]]
    if not blocks:
        raise ValueError("Нужны файлы сертификата в формате PEM: текст со строкой BEGIN CERTIFICATE")
    return list(dict.fromkeys(blocks))


def public_key(arguments, text):
    with tempfile.NamedTemporaryFile("w", suffix=".pem") as source:
        source.write(text)
        source.flush()
        return output(["openssl", *arguments, source.name])


def certificate_key(block):
    return public_key(["x509", "-noout", "-pubkey", "-in"], block)


def leaf_first(blocks, key):
    expected = output(["openssl", "pkey", "-pubout", "-in", str(key)])
    leaves = [block for block in blocks if certificate_key(block) == expected]
    if not leaves:
        raise ValueError("Ни один из файлов не подходит к ключу " + str(key) + "; нужен сертификат по этому запросу")
    return [leaves[0], *(block for block in blocks if block != leaves[0])]


def openssl_check(arguments):
    try:
        result = run(["openssl", *arguments], capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        return False, (error.stdout + error.stderr).strip()
    return True, (result.stdout + result.stderr).strip()


def openssl_reason(answer):
    return next((line.rsplit(": ", 1)[-1] for line in answer.splitlines() if " lookup: " in line), answer)


def check_names(leaf, names):
    for name in names:
        _, answer = openssl_check(["x509", "-noout", "-in", str(leaf), "-checkhost", name])
        if "does match" not in answer:
            raise ValueError("Сертификат не выпущен для " + name + "; закажите сертификат на основной домен и www")


def check_term(leaf, days):
    valid, _ = openssl_check(["x509", "-noout", "-in", str(leaf), "-checkend", str(days * SECONDS_PER_DAY)])
    if not valid:
        raise ValueError("Сертификат истекает раньше чем через " + str(days) + " дней или уже истёк")


def check_trust(leaf, intermediates, trust):
    arguments = ["verify", "-purpose", "sslserver", "-CAfile", str(trust)]
    if intermediates.stat().st_size:
        arguments += ["-untrusted", str(intermediates)]
    valid, answer = openssl_check([*arguments, str(leaf)])
    if not valid:
        raise ValueError(
            "Цепочка сертификата неполная или не доверена; добавьте файл цепочки регистратора ("
            + openssl_reason(answer)
            + ")"
        )


def verify(blocks, names, trust):
    with tempfile.TemporaryDirectory() as temporary:
        leaf, intermediates = pathlib.Path(temporary, "leaf.pem"), pathlib.Path(temporary, "chain.pem")
        leaf.write_text(blocks[0])
        intermediates.write_text("".join(blocks[1:]))
        check_names(leaf, names)
        check_term(leaf, MIN_VALID_DAYS)
        check_trust(leaf, intermediates, trust)
        return output(["openssl", "x509", "-noout", "-enddate", "-in", str(leaf)]).split("=", 1)[1]


def request_names(folder):
    return site_names((folder / DOMAIN_NAME).read_text().strip())


def prepare(folder, files, trust=SYSTEM_TRUST):
    blocks = leaf_first(pem_blocks(files), folder / KEY_NAME)
    ends = verify(blocks, request_names(folder), trust)
    chain = folder / CHAIN_NAME
    chain.write_text("".join(blocks))
    chain.chmod(0o644)
    return ends


def checked_chain(folder, trust):
    if not (folder / CHAIN_NAME).is_file():
        raise ValueError("Сначала выполните " + SCRIPT + " prepare " + str(folder) + " с файлами регистратора")
    blocks = leaf_first(pem_blocks([folder / CHAIN_NAME]), folder / KEY_NAME)
    return verify(blocks, request_names(folder), trust)


def swap(source, tls):
    for name in (CHAIN_NAME, KEY_NAME):
        shutil.copyfile(source / name, tls / name)
    protect_tls(tls)


def keep_previous(tls, folder):
    previous = pathlib.Path(tempfile.mkdtemp(dir=folder, prefix=PREVIOUS_SUFFIX))
    for name in (CHAIN_NAME, KEY_NAME):
        shutil.copyfile(tls / name, previous / name)
    return previous


def restart(context):
    context.execute("up", "-d", "--wait", "--force-recreate", "nginx")
    context.verify_http()


def apply(folder, context, trust=SYSTEM_TRUST):
    site = urlsplit(context.runtime["PUBLIC_URL"]).hostname
    if request_names(folder)[0] != site_names(site)[0]:
        raise ValueError("Сертификат выпущен для " + request_names(folder)[0] + ", а сайт работает на " + site)
    ends = checked_chain(folder, trust)
    tls = pathlib.Path(context.values["CLUB_TLS_DIR"])
    previous = keep_previous(tls, folder)
    try:
        swap(folder, tls)
        restart(context)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        swap(previous, tls)
        restart(context)
        shutil.rmtree(previous)
        raise ValueError("Новый сертификат не прошёл проверку сайта, возвращён прежний: " + str(error)) from None
    shutil.rmtree(previous)
    return ends


def show_request(folder):
    print("Запрос для регистратора (вставьте целиком): " + str(folder / REQUEST_NAME) + "\n")
    print((folder / REQUEST_NAME).read_text())
    print("Ключ остаётся на сервере: " + str(folder / KEY_NAME) + ". Никому его не передавайте.")
    print("Когда регистратор выдаст сертификат:")
    print(SCRIPT + " prepare " + str(folder) + " <файл сертификата> [<файл цепочки>]")


def show_prepared(folder, ends):
    names = ", ".join(request_names(folder))
    print("Сертификат проверен: " + names + "; действует до " + ends + "; цепочка доверена")
    print("Для мастера установки: " + str(folder / CHAIN_NAME) + " и " + str(folder / KEY_NAME))
    print("Для замены на работающем сайте: " + SCRIPT + " apply " + str(folder))


def installed_env():
    path = pathlib.Path(os.environ.get("ENV_FILE") or INSTALLED_ENV_FILE)
    if not path.is_file():
        raise ValueError("Установка сайта не найдена (" + str(path) + "); при первой установке укажите файлы мастеру")
    return path


def run_command(arguments):
    if arguments.command == "request":
        show_request(request(arguments.target))
    elif arguments.command == "prepare":
        folder = pathlib.Path(arguments.target).resolve(strict=True)
        show_prepared(folder, prepare(folder, arguments.files))
    else:
        folder = pathlib.Path(arguments.target).resolve(strict=True)
        context = Context(installed_env())
        with context.lock():
            ends = apply(folder, context)
        print("Новый сертификат установлен и проверен; действует до " + ends)


def main():
    parser = argparse.ArgumentParser(description="Купленный сертификат: запрос, проверка и замена")
    parser.add_argument("command", choices=("request", "prepare", "apply"))
    parser.add_argument("target", help="домен для request, папка запроса для prepare и apply")
    parser.add_argument("files", nargs="*", help="файлы сертификата и цепочки от регистратора для prepare")
    arguments = parser.parse_args()
    os.umask(0o077)
    try:
        if os.geteuid() != 0:
            raise ValueError("Запустите через sudo")
        if arguments.command == "prepare" and not arguments.files:
            raise ValueError("Укажите файл сертификата и, если есть, файл цепочки")
        run_command(arguments)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print("Операция не завершена: " + HELPERS["failure_message"](error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
