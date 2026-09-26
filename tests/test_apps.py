"""Discovery must include sleeping apps and use only Fly management reads."""
import copy
import json
import subprocess
import unittest
from unittest.mock import patch

from fly_apps import DiscoveryError, discover, fly_json, web_destination

PRIVATE = [{'Type': 'private_v6', 'Address': 'fdaa:0:1234:0:1::2'}]
PUBLIC = [{'Type': 'v4', 'Address': '66.241.124.10'}]
HTTP = [{'id': 'machine-one', 'state': 'stopped', 'config': {'services': [
    {'protocol': 'tcp', 'ports': [{'port': 80, 'handlers': ['http']},
                                {'port': 443, 'handlers': ['tls', 'http']}]}]}}]
MONGO = [{'id': 'machine-two', 'state': 'started', 'config': {'services': [
    {'protocol': 'tcp', 'ports': [{'port': 27017, 'handlers': []}]}]}}]


class Apps(unittest.TestCase):
    def test_sleeping_apps_use_management_reads_and_do_not_probe_service(self):
        apps = [{'Name': 'my-web', 'Organization': {'Slug': 'personal'}},
                {'Name': 'my-db', 'Organization': {'Slug': 'another-org'}}]
        calls = []
        def run(argv, **kwargs):
            calls.append(argv[1:-1])
            self.assertEqual(argv[-1], '--json')
            self.assertEqual(kwargs['stdin'], subprocess.DEVNULL)
            command = argv[1:-1]
            if command == ['apps', 'list']:
                data = apps
            elif command[:2] == ['machine', 'list']:
                data = HTTP if command[-1] == 'my-web' else MONGO
            elif command[:2] == ['ips', 'list']:
                data = PRIVATE
            else:
                self.fail(f'Unexpected command: {command}')
            return subprocess.CompletedProcess(argv, 0, json.dumps(data), '')
        with patch('fly_apps.subprocess.run', side_effect=run), \
             patch('socket.create_connection', side_effect=AssertionError('App connection forbidden')), \
             patch('socket.getaddrinfo', side_effect=AssertionError('App DNS probe forbidden')):
            result = discover('/fake/fly')
        self.assertEqual(len(calls), 5)
        self.assertEqual({tuple(c[:2]) for c in calls}, {('apps', 'list'), ('machine', 'list'), ('ips', 'list')})
        by_name = {a['name']: a for a in result['apps']}
        self.assertEqual(by_name['my-web']['status'], 'sleeping')
        self.assertEqual(by_name['my-web']['url'], 'http://my-web.flycast')
        self.assertEqual(by_name['my-db']['url'], 'https://fly.io/apps/my-db')
        self.assertEqual(by_name['my-db']['organization'], 'another-org')

    def test_public_https_private_http_and_nonstandard_port(self):
        self.assertEqual(web_destination('web', HTTP, PUBLIC), ('https://web.fly.dev', 'app', False))
        self.assertEqual(web_destination('web', HTTP, PRIVATE + PUBLIC), ('http://web.flycast', 'app', True))
        custom = copy.deepcopy(HTTP)
        custom[0]['config']['services'][0]['ports'] = [{'port': 8088, 'handlers': ['http']}]
        self.assertEqual(web_destination('web', custom, PRIVATE)[0], 'http://web.flycast:8088')

    def test_force_https_does_not_make_broken_flycast_url(self):
        forced = copy.deepcopy(HTTP)
        forced[0]['config']['services'][0]['ports'][0]['force_https'] = True
        self.assertEqual(web_destination('web', forced, PRIVATE)[1], 'dashboard')
        self.assertEqual(web_destination('web', forced, PRIVATE + PUBLIC)[0], 'https://web.fly.dev')

    def test_missing_metadata_keeps_app_with_dashboard_fallback(self):
        def query(_exe, *args):
            if args == ('apps', 'list'):
                return [{'Name': 'new-app'}]
            raise DiscoveryError('Permission denied')
        result = discover('/fake/fly', query)
        self.assertEqual(result['partial'], 1)
        self.assertEqual(result['apps'][0]['url'], 'https://fly.io/apps/new-app')

    def test_additions_deletions_and_undeployed_apps_are_discovered_each_time(self):
        names = ['old-app']
        def query(_exe, *args):
            return [{'Name': n} for n in names] if args == ('apps', 'list') else []
        first = discover('/fake/fly', query)
        self.assertEqual(first['apps'][0]['status'], 'empty')
        names[:] = ['new-app', 'second-app']
        self.assertEqual([a['name'] for a in discover('/fake/fly', query)['apps']], names)
        names.clear()
        self.assertEqual(discover('/fake/fly', query)['apps'], [])

    def test_unknown_protocol_and_no_ingress_use_dashboard(self):
        self.assertEqual(web_destination('db', MONGO, PRIVATE + PUBLIC)[1], 'dashboard')
        self.assertEqual(web_destination('web', HTTP, [])[1], 'dashboard')

    def test_subprocess_failures_never_echo_secrets_or_trigger_login(self):
        with patch('fly_apps.subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', 'secret-token')) as run:
            with self.assertRaises(DiscoveryError) as error:
                fly_json('/fake/fly', 'apps', 'list')
            self.assertNotIn('secret-token', str(error.exception))
            run.assert_called_once()
        with patch('fly_apps.subprocess.run') as run:
            for args in [('machine', 'start'), ('apps', 'open'), ('auth', 'login')]:
                with self.assertRaises(DiscoveryError):
                    fly_json('/fake/fly', *args)
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
