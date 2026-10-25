"""Debian 13/systemd integration. Only public CA operations are replaced.

Runs only in the disposable CI container. Downloads/installs the real Xray core;
the local TLS certificate is self-signed and NEVER claims ACME validation.
"""
import hashlib
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit, parse_qs

import pexpect

ROOT = Path('/workspace')
CONFIG = Path('/usr/local/etc/xray/config.json')
CERT = Path('/etc/xray-cert/server.crt')
KEY = Path('/etc/xray-cert/server.key')
FIXTURE = Path('/etc/xray-cert/acme/ci.example.test_ecc')
RAW = 'https://raw.githubusercontent.com/brian952700/xray-xhttp-manager/main/'
MENU = '请选择 [1-8]：'
PAUSE = '按 Enter 返回菜单…'


def run(*args, check=True, **kwargs):
    return subprocess.run(args, check=check, text=True, **kwargs)


def check(name, predicate):
    if not predicate:
        raise AssertionError(name)
    print(f'PASS: {name}', flush=True)


def content():
    return json.loads(CONFIG.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def active():
    return run('systemctl', 'is-active', '--quiet', 'xray', check=False).returncode == 0


def spawn(command, args=()):
    child = pexpect.spawn(command, list(args), encoding='utf-8', timeout=360)
    child.logfile_read = sys.stdout
    return child


def close(child):
    child.expect(pexpect.EOF)
    child.close()
    check('interactive command exit status', child.exitstatus == 0)


def resume(child):
    child.expect_exact(PAUSE)
    child.sendline('')
    child.expect_exact(MENU)


def certificate(domain='ci.example.test'):
    fixture = Path('/etc/xray-cert/acme') / (domain + '_ecc')
    fixture.mkdir(parents=True, exist_ok=True)
    run('openssl', 'req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256',
        '-nodes', '-days', '7', '-subj', '/CN=' + domain,
        '-addext', 'subjectAltName=DNS:' + domain,
        '-addext', 'basicConstraints=critical,CA:FALSE',
        '-keyout', str(fixture / 'key.pem'), '-out', str(fixture / 'fullchain.cer'))


def manager(code, check_result=True):
    return run('bash', '-c', 'source /usr/local/bin/xy\n' + code, check=check_result)


def proxy_test(uuid):
    cert_der = subprocess.check_output(['openssl', 'x509', '-in', str(CERT), '-outform', 'DER'])
    cert_pin = hashlib.sha256(cert_der).hexdigest()
    client_config = {
        'log': {'loglevel': 'warning'},
        'inbounds': [{'listen': '127.0.0.1', 'port': 10808, 'protocol': 'socks',
                      'settings': {'auth': 'noauth', 'udp': False}}],
        'outbounds': [{'protocol': 'vless', 'settings': {'vnext': [{
            'address': '127.0.0.1', 'port': 8443,
            'users': [{'id': uuid, 'encryption': content()['_xy']['vlessEncryption']}]}]},
            'streamSettings': {'network': 'xhttp', 'security': 'tls',
                'xhttpSettings': {'path': '/ci'},
                # Self-signed certificate is only used in this local CI test.
                'tlsSettings': {'serverName': Path('/etc/xray-cert/domain.txt').read_text().strip(), 'pinnedPeerCertSha256': cert_pin}}}]
    }
    client_file = Path('/tmp/ci-client.json')
    client_file.write_text(json.dumps(client_config))
    with open('/tmp/ci-client.log', 'w') as log:
        client = subprocess.Popen(['xray', 'run', '-config', str(client_file)], stdout=log, stderr=log)
        try:
            for _ in range(3):
                response = run('curl', '-fsS', '--max-time', '10', '--noproxy', '',
                    '--socks5-hostname', '127.0.0.1:10808', 'http://127.0.0.1:19080/',
                    check=False, capture_output=True)
                if response.returncode == 0:
                    check('VLESS+xHTTP+TLS transfers HTTP via SOCKS', response.stdout == 'xy-ci-ok\n')
                    check('client has no deprecated VLESS warning', 'VLESS (with no Flow' not in Path('/tmp/ci-client.log').read_text())
                    return
                time.sleep(1)
            raise AssertionError('Local proxy failed: ' + response.stderr + Path('/tmp/ci-client.log').read_text())
        finally:
            client.terminate()
            client.wait(timeout=10)


def main():
    check('Debian 13', 'VERSION_ID="13"' in Path('/etc/os-release').read_text())
    check('real systemd PID 1', Path('/proc/1/comm').read_text().strip() == 'systemd')
    for script in ('xy.sh', 'install.sh', 'tests/acme-fixture.sh'):
        run('bash', '-n', str(ROOT / script))
    run('shellcheck', '--severity=error', str(ROOT / 'xy.sh'), str(ROOT / 'install.sh'))
    check('Bash syntax and ShellCheck error-level checks', True)

    FIXTURE.mkdir(parents=True)
    (FIXTURE / 'ci.example.test.conf').touch()
    certificate()
    shutil.copy(ROOT / 'tests/acme-fixture.sh', FIXTURE.parent / 'acme.sh')
    (FIXTURE.parent / 'acme.sh').chmod(0o755)

    # Verify anonymous public URLs and ensure this run tests this checkout.
    for name in ('install.sh', 'xy.sh'):
        downloaded = Path('/tmp') / ('public-' + name)
        run('curl', '-fsSL', '--retry', '3', RAW + name, '-o', str(downloaded))
        check('public URL exact checkout: ' + name, downloaded.read_bytes() == (ROOT / name).read_bytes())

    # Execute exactly the README one-command pipeline inside a controlling PTY.
    child = spawn('bash', ['-o', 'pipefail', '-c', f'curl -fsSL {RAW}install.sh | bash'])
    child.expect_exact('请输入域名（已解析到本服务器）：')
    child.sendline('ci.example.test')
    child.expect_exact('监听端口 [443]：')
    child.sendline('8443')
    child.expect_exact('xHTTP 路径 [/]：')
    child.sendline('/ci')
    child.expect_exact('安装/更新完成；证书续期回调已注册。')
    resume(child)
    check('downloaded manager matches checkout', Path('/usr/local/bin/xy').read_bytes() == (ROOT / 'xy.sh').read_bytes())
    check('real Xray service active after first install', active())
    run('systemctl', 'is-enabled', 'xray')
    run('systemctl', 'is-active', 'cron')
    check('independent renewal cron installed', Path('/etc/cron.d/xy-acme').is_file())
    check('fresh install enables VLESS Encryption', content()['inbounds'][0]['settings']['decryption'].startswith('mlkem768x25519plus.'))
    check('Flow remains empty', not content()['inbounds'][0]['settings'].get('flow'))
    links = subprocess.check_output(['bash', '-c', 'source /usr/local/bin/xy; list_users'], text=True)
    link = next(line.removeprefix('链接：') for line in links.splitlines() if line.startswith('链接：'))
    check('share link carries matching encryption', parse_qs(urlsplit(link).query)['encryption'][0] == content()['_xy']['vlessEncryption'])
    check('share link never exposes server private parameter', content()['inbounds'][0]['settings']['decryption'] not in links)
    admin = content()['inbounds'][0]['settings']['clients'][0]['id']

    username = 'ci "quoted" \\ user #&中文'
    child.sendline('2')
    child.expect_exact('请输入新用户名/邮箱：')
    child.sendline(username)
    child.expect_exact('已添加用户：' + username)
    resume(child)
    check('special-character user safely added', any(c['email'] == username for c in content()['inbounds'][0]['settings']['clients']))
    before = content()
    child.sendline('1')
    child.expect_exact('安装/更新完成；证书续期回调已注册。')
    resume(child)
    check('update preserves every existing user and setting', content() == before)

    child.sendline('3')
    child.expect_exact('请输入要删除的用户名/邮箱：')
    child.sendline(username)
    child.expect_exact('[y/N]：')
    child.sendline('y')
    child.expect_exact('已删除用户：' + username)
    resume(child)
    check('delete user preserves original UUID', content()['inbounds'][0]['settings']['clients'] == [{'id': admin, 'email': 'admin'}])
    child.sendline('3')
    child.expect_exact('请输入要删除的用户名/邮箱：')
    child.sendline('admin')
    child.expect_exact('[y/N]：')
    child.sendline('y')
    child.expect_exact('至少需要保留一个用户。')
    resume(child)
    child.sendline('6')
    close(child)

    # Disposable CI fixture only: current Xray blocks private destinations by default.
    # Permit exactly the local HTTP test target; production config keeps core defaults.
    manager("jq '.outbounds[0].settings.finalRules=[{\"action\":\"allow\",\"network\":\"tcp\",\"ip\":[\"127.0.0.1\"],\"port\":19080}]' /usr/local/etc/xray/config.json > /tmp/fixture.json; lock; apply_config /tmp/fixture.json")

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'xy-ci-ok\n')
        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 19080), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    proxy_test(admin)

    before = CONFIG.read_bytes()
    Path('/tmp/invalid.json').write_text('{invalid')
    result = manager('lock\napply_config /tmp/invalid.json', check_result=False)
    check('invalid JSON rejected without changing live config', result.returncode != 0 and CONFIG.read_bytes() == before and active())

    busy = content()
    busy['inbounds'][0]['port'] = 19080  # occupied by the real HTTP server
    Path('/tmp/busy.json').write_text(json.dumps(busy))
    result = manager('lock\napply_config /tmp/busy.json', check_result=False)
    check('failed real service restart rolls back config and service', result.returncode != 0 and CONFIG.read_bytes() == before and active())

    old_cert = digest(CERT)
    certificate()
    shutil.copy(FIXTURE / 'fullchain.cer', '/etc/xray-cert/staging/server.crt')
    shutil.copy(FIXTURE / 'key.pem', '/etc/xray-cert/staging/server.key')
    run('/usr/local/bin/xy', '--renew-reload')
    check('renewal callback deploys matching new certificate', digest(CERT) != old_cert and active())
    good_cert, good_key = digest(CERT), digest(KEY)
    certificate()  # use only the new key, yielding a mismatched pair
    shutil.copy(FIXTURE / 'key.pem', '/etc/xray-cert/staging/server.key')
    result = run('/usr/local/bin/xy', '--renew-reload', check=False)
    check('mismatched certificate rejected without replacing active pair', result.returncode != 0 and digest(CERT) == good_cert and digest(KEY) == good_key and active())
    proxy_test(admin)

    # New menu commands run against the real service and staged certificate adapter.
    domain_file = Path('/etc/xray-cert/domain.txt')
    before_config = CONFIG.read_bytes()
    before_domain = domain_file.read_bytes()
    before_cert, before_key = digest(CERT), digest(KEY)
    old_pid = run('systemctl', 'show', 'xray', '-p', 'MainPID', '--value', capture_output=True).stdout
    child = spawn('/usr/local/bin/xy')
    child.expect_exact(MENU)
    child.sendline('7')
    child.expect_exact('Xray 服务已重启并确认运行。')
    resume(child)
    new_pid = run('systemctl', 'show', 'xray', '-p', 'MainPID', '--value', capture_output=True).stdout
    check('menu restart replaces running process without changing config',
          old_pid != new_pid and active() and CONFIG.read_bytes() == before_config)

    child.sendline('8')
    child.expect_exact('请输入新域名 [ci.example.test]：')
    child.sendline('https://invalid.example')
    child.expect_exact('域名格式错误')
    resume(child)
    check('invalid domain preserves node and certificates',
          CONFIG.read_bytes() == before_config and domain_file.read_bytes() == before_domain
          and digest(CERT) == before_cert and digest(KEY) == before_key and active())

    # Request a domain whose fixture deliberately holds a different domain's certificate.
    bad_fixture = Path('/etc/xray-cert/acme/wrong.example.test_ecc')
    bad_fixture.mkdir()
    shutil.copy(CERT, bad_fixture / 'fullchain.cer')
    shutil.copy(KEY, bad_fixture / 'key.pem')
    child.sendline('8')
    child.expect_exact('请输入新域名 [ci.example.test]：')
    child.sendline('wrong.example.test')
    child.expect_exact('操作未完成')
    resume(child)
    check('wrong-domain certificate leaves active domain and TLS untouched',
          CONFIG.read_bytes() == before_config and domain_file.read_bytes() == before_domain
          and digest(CERT) == before_cert and digest(KEY) == before_key and active())

    certificate('new.example.test')
    # Fail exactly the first start: rollback must restore domain, TLS and running service.
    dropin = Path('/etc/systemd/system/xray.service.d/xy-ci-fail.conf')
    dropin.parent.mkdir(parents=True, exist_ok=True)
    fail_script = Path('/tmp/xy-fail-once.sh')
    fail_script.write_text('#!/bin/sh\nif [ -f /tmp/xy-fail-once ]; then rm /tmp/xy-fail-once; exit 1; fi\n')
    fail_script.chmod(0o755)
    dropin.write_text('[Service]\nExecStartPre=/tmp/xy-fail-once.sh\n')
    Path('/tmp/xy-fail-once').touch()
    run('systemctl', 'daemon-reload')
    child.sendline('8')
    child.expect_exact('请输入新域名 [ci.example.test]：')
    child.sendline('new.example.test')
    child.expect_exact('操作失败，已恢复操作前的配置/证书及服务状态。')
    resume(child)
    check('domain change restart failure rolls back domain, config, TLS and service',
          CONFIG.read_bytes() == before_config and domain_file.read_bytes() == before_domain
          and digest(CERT) == before_cert and digest(KEY) == before_key and active())
    dropin.unlink()
    run('systemctl', 'daemon-reload')

    child.sendline('8')
    child.expect_exact('请输入新域名 [ci.example.test]：')
    child.sendline('new.example.test')
    child.expect_exact('域名和证书更新完成')
    resume(child)
    check('domain update preserves every node setting and encryption key', CONFIG.read_bytes() == before_config)
    check('domain update deploys new identity', domain_file.read_text().strip() == 'new.example.test'
          and digest(CERT) != before_cert and active())
    run('openssl', 'x509', '-in', str(CERT), '-noout', '-checkhost', 'new.example.test')
    check('old renewal disabled after successful domain update',
          '--remove ci.example.test' in Path('/tmp/xy-acme-fixture.log').read_text())
    check('renewal callback bound to new domain',
          Path('/tmp/xy-reload-new.example.test').read_text().strip().endswith('--renew-reload new.example.test'))
    new_cert, new_key = digest(CERT), digest(KEY)
    run('/usr/local/bin/xy', '--renew-reload', 'ci.example.test')
    check('stale domain callback cannot replace active identity',
          digest(CERT) == new_cert and digest(KEY) == new_key and active())
    links = subprocess.check_output(['bash', '-c', 'source /usr/local/bin/xy; list_users'], text=True)
    link = next(line.removeprefix('链接：') for line in links.splitlines() if line.startswith('链接：'))
    check('new links use updated address and SNI', urlsplit(link).hostname == 'new.example.test'
          and parse_qs(urlsplit(link).query)['sni'] == ['new.example.test'])

    certificate('new.example.test')
    child.sendline('8')
    child.expect_exact('请输入新域名 [new.example.test]：')
    child.sendline('')
    child.expect_exact('域名和证书更新完成')
    resume(child)
    check('blank domain renews current identity without changing users or keys',
          CONFIG.read_bytes() == before_config and domain_file.read_text().strip() == 'new.example.test'
          and digest(CERT) != new_cert and active())
    child.sendline('6')
    close(child)
    proxy_test(admin)

    # Bootstrap under sudo must still read the menu from /dev/tty.
    run('useradd', '-m', 'ciuser')
    sudoers = Path('/etc/sudoers.d/xy-ci')
    sudoers.write_text('ciuser ALL=(ALL) NOPASSWD: ALL\n')
    sudoers.chmod(0o440)
    before = CONFIG.read_bytes()
    child = spawn('runuser', ['-u', 'ciuser', '--', 'bash', '-o', 'pipefail', '-c', f'curl -fsSL {RAW}install.sh | bash'])
    child.expect_exact(MENU)
    child.sendline('6')
    close(child)
    check('non-root bootstrap preserves existing configuration', CONFIG.read_bytes() == before)

    shared = Path('/root/.acme.sh/unrelated-site.keep')
    shared.parent.mkdir(exist_ok=True)
    shared.write_text('must survive')
    child = spawn('/usr/local/bin/xy')
    child.expect_exact(MENU)
    child.sendline('5')
    child.expect_exact('确定彻底卸载？[y/N]：')
    child.sendline('y')
    child.expect_exact('卸载完成。')
    close(child)
    check('uninstall removes manager/config/cert/cron', all(not p.exists() for p in (CONFIG, CERT, KEY, Path('/usr/local/bin/xy'), Path('/etc/cron.d/xy-acme'))))
    check('uninstall preserves shared ACME directory', shared.read_text() == 'must survive')
    print('ALL INTEGRATION CHECKS PASSED. Public ACME issuance was NOT tested.', flush=True)


if __name__ == '__main__':
    main()
