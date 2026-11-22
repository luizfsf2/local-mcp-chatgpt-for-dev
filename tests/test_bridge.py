"""Unit tests with a simulated Herdr CLI; no hc/MCP/Windows required."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from herdr_bridge import Bridge, BridgeError, run_hc


class FakeHC:
    def __init__(self, entries):
        self.entries = entries
        self.calls = []
        self.read_output = 'screen line\n' * 4

    def __call__(self, executable, args, timeout):
        self.calls.append((executable, args, timeout))
        op = args[0]
        if op == 'ls':
            return json.dumps({'terminals': self.entries})
        if op == 'get':
            wanted = args[-1]
            rec = next((v for v in self.entries if v['address'] == wanted), None)
            return json.dumps({'terminal': rec})
        if op == 'read':
            return self.read_output
        if op in ('send', 'interrupt', 'wait', 'run'):
            return json.dumps({'ok': True, 'address': 't10'} if op == 'run' else {'ok': True})
        raise AssertionError(f'Unexpected command: {args}')


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / 'repo'
        self.project.mkdir()
        self.elsewhere = self.root / 'elsewhere'
        self.elsewhere.mkdir()
        self.cfg_path = self.root / 'config.json'
        self.cfg = {
            'projects': {'demo': {'path': str(self.project), 'agents': ['claude', 'codex']}},
            'attached_sessions': {},
            'allow_launch': True, 'allow_send': True, 'allow_interrupt': False,
        }
        self.good = {
            'address': 't1', 'name': 'ab-demo-claude-12345',
            'cwd': str(self.project), 'agent': 'claude',
            'state': 'live', 'status': 'idle',
            'labels': {'agent_bridge': 'yes', 'project': 'demo', 'agent': 'claude'},
        }
        self.fake = FakeHC([self.good])
        self.reload()

    def reload(self):
        self.cfg_path.write_text(json.dumps(self.cfg), encoding='utf-8')
        self.bridge = Bridge(self.cfg_path, self.fake)

    def test_projects_are_allowlisted(self):
        projects = self.bridge.list_projects()
        self.assertEqual([p['project'] for p in projects], ['demo'])
        self.assertTrue(projects[0]['available'])

    def test_labeled_session_is_visible(self):
        rows = self.bridge.list_sessions()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['address'], 't1')
        self.assertEqual(rows[0]['reported_agent'], 'claude')

    def test_foreign_session_does_not_appear_even_when_labeled(self):
        self.fake.entries[0] = dict(self.good, cwd=str(self.elsewhere))
        self.assertEqual(self.bridge.list_sessions(), [])
        with self.assertRaises(BridgeError):
            self.bridge.read_session('t1')

    def test_unlabeled_session_is_not_discovered(self):
        self.fake.entries[0] = dict(self.good, labels={})
        self.assertEqual(self.bridge.list_sessions(), [])

    def test_explicit_attachment_admits_existing_session(self):
        self.fake.entries[0] = dict(self.good, labels={})
        self.cfg['attached_sessions'] = {
            'claude-antigo': {'project': 'demo', 'terminal': 't1', 'agent': 'claude'}
        }
        self.reload()
        self.assertEqual(self.bridge.get_session('claude-antigo')['address'], 't1')

    def test_nested_project_path_is_valid(self):
        nested = self.project / 'subdir'
        nested.mkdir()
        self.fake.entries[0] = dict(self.good, cwd=str(nested))
        self.assertEqual(len(self.bridge.list_sessions()), 1)

    def test_unexpected_agent_identity_denies_send(self):
        self.fake.entries[0] = dict(self.good, agent='powershell')
        with self.assertRaisesRegex(BridgeError, 'nao confirmado'):
            self.bridge.send_message('t1', 'Olhe os testes')
        self.assertFalse(any(args[0] == 'send' for _, args, _ in self.fake.calls))

    def test_blocked_status_denies_send_even_with_valid_identity(self):
        self.fake.entries[0] = dict(self.good, status='blocked')
        with self.assertRaisesRegex(BridgeError, 'blocked'):
            self.bridge.send_message('t1', 'Pode aprovar?')
        self.assertFalse(any(args[0] == 'send' for _, args, _ in self.fake.calls))

    def test_send_is_single_argument_never_force(self):
        payload = 'Explique o teste; rm -rf /\nnao execute nada'
        result = self.bridge.send_message('t1', payload)
        self.assertTrue(result['accepted_by_hc'])
        _, args, _ = self.fake.calls[-1]
        self.assertEqual(args, ['send', '--json', 't1', payload])
        self.assertNotIn('--force', args)

    def test_stale_agent_status_does_not_write_to_powershell(self):
        self.fake.entries[0] = dict(self.good, foreground={'name': 'powershell.exe'})
        self.assertFalse(self.bridge.get_session('t1')['can_send'])
        with self.assertRaisesRegex(BridgeError, 'nao confirmado'):
            self.bridge.send_message('t1', 'Oi')

    def test_send_requires_flag(self):
        self.cfg['allow_send'] = False
        self.reload()
        with self.assertRaisesRegex(BridgeError, 'desabilitado'):
            self.bridge.send_message('t1', 'Oi')

    def test_read_strips_ansi_and_bounds_length(self):
        self.fake.read_output = '\x1b[32m' + 'A' * 30000 + '\x1b[0m'
        result = self.bridge.read_session('t1', 50)
        self.assertTrue(result['truncated'])
        self.assertEqual(len(result['screen']), 24000)
        self.assertNotIn('\x1b', result['screen'])

    def test_finished_session_can_be_read_but_not_sent(self):
        self.fake.entries[0] = dict(self.good, state='finished')
        self.assertIn('screen', self.bridge.read_session('t1'))
        with self.assertRaisesRegex(BridgeError, 'nao esta ativa'):
            self.bridge.send_message('t1', 'Oi')

    def test_launch_uses_fixed_program_argv_and_root(self):
        response = self.bridge.start_agent('demo', 'codex')
        self.assertTrue(response['started_by_hc'])
        _, args, _ = self.fake.calls[-1]
        self.assertEqual(args[0:4], ['run', '--json', '--bg', '--no-parent'])
        self.assertEqual(args[-1], 'codex')
        self.assertIn(str(self.project), args)
        self.assertIn('agent_bridge=yes', args)
        self.assertNotIn('--force', args)

    def test_start_denies_unknown_agent_or_project(self):
        for name, agent in [('demo', 'bash'), ('other', 'claude')]:
            with self.assertRaises(BridgeError):
                self.bridge.start_agent(name, agent)

    def test_interrupt_disabled_by_default(self):
        with self.assertRaisesRegex(BridgeError, 'desabilitada'):
            self.bridge.interrupt_session('t1')

    def test_short_wait_uses_bounded_timeout(self):
        self.assertEqual(self.bridge.wait_session('t1', 2)['session'], 't1')
        _, args, timeout = self.fake.calls[-1]
        self.assertEqual(args, ['wait', '--json', '--timeout', '2', 't1'])
        self.assertLessEqual(timeout, 15)
        with self.assertRaises(BridgeError):
            self.bridge.wait_session('t1', 100)

    def test_get_mismatch_fails_closed(self):
        def bad_get(exe, args, timeout):
            if args[0] == 'get':
                return json.dumps({'address': 't99', 'cwd': str(self.project)})
            return self.fake(exe, args, timeout)
        self.bridge.runner = bad_get
        with self.assertRaisesRegex(BridgeError, 'outra sessao'):
            self.bridge.get_session('t1')

    def test_malformed_ls_fails_closed(self):
        self.bridge.runner = lambda exe, args, timeout: '{"unexpected": true}'
        with self.assertRaisesRegex(BridgeError, 'inesperado'):
            self.bridge.list_sessions()

    def test_subprocess_is_never_shell(self):
        proc = subprocess.CompletedProcess(args=[], returncode=0, stdout='{}', stderr='')
        with patch('herdr_bridge.core.subprocess.run', return_value=proc) as mocked:
            out = run_hc('hc', ['send', 't1', 'echo $HOME'])
            self.assertEqual(out, '{}')
            self.assertEqual(mocked.call_args.args[0], ['hc', 'send', 't1', 'echo $HOME'])
            self.assertIs(mocked.call_args.kwargs['shell'], False)


if __name__ == '__main__':
    unittest.main()
