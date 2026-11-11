"""Update policy tests without accessing panel hardware or starting service threads."""
import ast
import json
from pathlib import Path
import re
import threading
import time
import types
import unittest
from unittest.mock import Mock

SOURCE = Path(__file__).resolve().parents[1] / 'apps/wallpanel-api/rootfs/usr/lib/wallpanel/wallpanel_api.py'


def policy():
    tree = ast.parse(SOURCE.read_text())
    names = {'auto_kernel_update', 'kernel_update_time', 'set_kernel_update_time',
             'install_updates', '_install_updates', 'install_scheduled_kernel', 'install_kernel',
             'command', 'Mqtt', 'UpdatePage', 'reboot_scheduler'}
    tree.body = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    ns = dict(json=json, re=re, time=Mock(wraps=time), threading=threading, SETTINGS={},
              REBOOT_TIMES=[f'{h:02d}:{m:02d}' for h in range(24) for m in (0, 30)],
              UPDATE={'running': False, 'log': []}, UPDATE_LOCK=threading.Lock(),
              KERNEL={'info': None, 'error': None, 'process': None},
              save_settings=Mock(), kernel_installing=Mock(return_value=False),
              running_slot=Mock(return_value='A'), check_kernel=Mock(),
              rw_cmd=lambda *args: list(args), apk_update=Mock(return_value=0),
              now_iso=lambda: '2026-11-11T04:30:00+0100', check_updates=Mock(),
              restart_kiosk=Mock(), reboot=Mock(), MQ=None,
              sh=Mock(return_value=types.SimpleNamespace(returncode=0)),
              subprocess=types.SimpleNamespace(Popen=Mock(), PIPE=-1, STDOUT=-2, DEVNULL=-3),
              BASE='wallpanel/test', NODE='test', NAME='Test panel', CONF={},
              ENTITY_IDS={}, SCALES=(100,),
              Display=types.SimpleNamespace(CT_SCALE=(50, 150), CT_GAIN=(50, 100), KELVIN=(2202, 6500)),
              display=types.SimpleNamespace(max=255))
    exec(compile(tree, str(SOURCE), 'exec'), ns)
    return ns


class UpdatePolicyTests(unittest.TestCase):
    def test_all_package_updates_use_upgrade_and_restart_updated_services(self):
        ns = policy()
        proc = Mock(stdout=iter(['(1/2) Upgrading chromium (1 -> 2)\n',
                               '(2/2) Upgrading shairport-sync (1 -> 2)\n']))
        proc.wait.return_value = 0
        ns['subprocess'].Popen.return_value = proc
        self.assertTrue(ns['install_updates']())
        self.assertEqual(ns['subprocess'].Popen.call_args.args[0], ['apk', 'upgrade', '--no-interactive'])
        ns['restart_kiosk'].assert_called_once()
        ns['sh'].assert_any_call('rc-service', 'wallpanel-airplay', 'restart')
        self.assertFalse(ns['UPDATE']['running'])
        self.assertTrue(ns['UPDATE_LOCK'].acquire(blocking=False))

    def test_failed_package_index_does_not_start_upgrade_or_reboot(self):
        ns = policy()
        ns['apk_update'].return_value = 1
        self.assertFalse(ns['install_updates'](then_reboot=True))
        ns['subprocess'].Popen.assert_not_called()
        ns['reboot'].assert_not_called()
        self.assertFalse(ns['UPDATE']['running'])

    def test_package_and_kernel_start_are_mutually_exclusive(self):
        ns = policy()
        ns['UPDATE_LOCK'].acquire()
        self.assertFalse(ns['install_updates']())
        self.assertFalse(ns['install_kernel']())
        ns['subprocess'].Popen.assert_not_called()

    def test_kernel_automation_is_opt_in(self):
        ns = policy()
        ns['install_kernel'] = Mock(return_value=True)
        self.assertFalse(ns['auto_kernel_update']())
        self.assertEqual(ns['kernel_update_time'](), '04:30')
        self.assertFalse(ns['install_scheduled_kernel']())
        ns['check_kernel'].assert_not_called()
        ns['install_kernel'].assert_not_called()

    def test_scheduled_kernel_checks_fresh_release_and_uses_existing_installer(self):
        ns = policy()
        ns['SETTINGS']['auto_kernel_update'] = True
        ns['KERNEL']['info'] = {'update_available': True}
        ns['install_kernel'] = Mock(return_value=True)
        self.assertTrue(ns['install_scheduled_kernel']())
        ns['check_kernel'].assert_called_once_with(refresh=True)
        ns['install_kernel'].assert_called_once_with(source='automatic schedule')

    def test_kernel_skips_missing_release_pending_test_and_errors(self):
        for info in ({}, {'update_available': False}, {'update_available': True, 'pending': {'to': 'test'}},
                     {'update_available': True, 'error': 'network failed'}):
            with self.subTest(info=info):
                ns = policy()
                ns['SETTINGS']['auto_kernel_update'] = True
                ns['KERNEL']['info'] = info
                ns['install_kernel'] = Mock()
                self.assertFalse(ns['install_scheduled_kernel']())
                ns['install_kernel'].assert_not_called()

    def test_kernel_skips_slot_b_and_busy_packages(self):
        for slot, busy in [('B', False), ('A', True)]:
            ns = policy()
            ns['SETTINGS']['auto_kernel_update'] = True
            ns['running_slot'].return_value = slot
            ns['UPDATE']['running'] = busy
            self.assertFalse(ns['install_scheduled_kernel']())
            ns['check_kernel'].assert_not_called()

    def test_kernel_setting_commands_persist_and_validate(self):
        ns = policy()
        ns['command']('wallpanel/test/auto_kernel_update/set', 'ON')
        self.assertTrue(ns['auto_kernel_update']())
        ns['command']('wallpanel/test/kernel_update_time/set', '05:00')
        self.assertEqual(ns['kernel_update_time'](), '05:00')
        ns['command']('wallpanel/test/kernel_update_time/set', '25:00')
        self.assertEqual(ns['kernel_update_time'](), '05:00')
        self.assertEqual(ns['save_settings'].call_count, 2)

    def test_discovery_and_admin_expose_same_kernel_settings(self):
        ns = policy()
        mqtt = ns['Mqtt'].__new__(ns['Mqtt'])
        mqtt.device = lambda: {}
        mqtt.c = Mock()
        mqtt.discovery()
        messages = {c.args[0]: json.loads(c.args[1]) for c in mqtt.c.publish.call_args_list if c.args[1]}
        switch = messages['homeassistant/switch/test/auto_kernel_update/config']
        select = messages['homeassistant/select/test/kernel_update_time/config']
        self.assertEqual(switch['command_topic'], 'wallpanel/test/auto_kernel_update/set')
        self.assertIn('value_json.auto_kernel_update', switch['value_template'])
        self.assertEqual(select['options'], ns['REBOOT_TIMES'])
        self.assertIn('auto_kernel_update', ns['UpdatePage'].SETTABLE)
        self.assertIn('kernel_update_time', ns['UpdatePage'].SETTABLE)
        packages = messages['homeassistant/switch/test/auto_update/config']
        self.assertEqual(packages['name'], 'Packages update daily')
        self.assertEqual(packages['unique_id'], 'wallpanel_test_auto_update')

    def scheduled_once(self, packages_ok):
        ns = policy()
        ns.update(update_time=lambda: '04:30', kernel_update_time=lambda: '04:30',
                  reboot_time=lambda: '04:30', uptime=lambda: 1000,
                  auto_update=lambda: True, auto_kernel_update=lambda: True,
                  reboot_enabled=lambda: True, install_updates=Mock(return_value=packages_ok),
                  install_scheduled_kernel=Mock(return_value=True))
        ns['time'].strftime.return_value = '04:30'
        ns['time'].time.return_value = 2e9
        ns['time'].sleep.side_effect = [None, StopIteration]
        with self.assertRaises(StopIteration):
            ns['reboot_scheduler']()
        return ns

    def test_coincident_schedules_run_packages_then_kernel_without_extra_reboot(self):
        ns = self.scheduled_once(True)
        ns['install_updates'].assert_called_once()
        ns['install_scheduled_kernel'].assert_called_once()
        ns['reboot'].assert_not_called()

    def test_failed_packages_skip_coincident_kernel_schedule(self):
        ns = self.scheduled_once(False)
        ns['install_updates'].assert_called_once()
        ns['install_scheduled_kernel'].assert_not_called()


if __name__ == '__main__':
    unittest.main()
