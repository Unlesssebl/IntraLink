"""Historical baseline checks; no app imports, database or network required."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/ticket_quality_baseline.py'
SPEC = importlib.util.spec_from_file_location('ticket_quality_baseline', SCRIPT)
baseline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(baseline)


class BaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = json.loads((baseline.FIXTURES / 'decisions.json').read_text(encoding='utf-8'))
        cls.labels = json.loads((baseline.FIXTURES / 'annotations.json').read_text(encoding='utf-8'))

    def test_original_counts_and_detected_regressions(self):
        result = baseline.evaluate(self.rows, self.labels)
        self.assertEqual((result['total'], result['confirmed'], result['needs_review']), (99, 4, 95))
        self.assertEqual((result['template'], result['standard'], result['executable']), (99, 83, 3))
        self.assertEqual(result['confidence'], {'0.91': 95, '0.81': 4})
        found = {f['task_id']: f['errors'] for f in result['findings']}
        for tid in (140408, 140511):
            self.assertIn('wrong_device_clarification', found[tid])
        for tid in (139762, 140686):
            self.assertIn('forbidden_redirect', found[tid])

    def test_corrected_outcome_clears_wrong_device_error(self):
        rows = copy.deepcopy(self.rows)
        row = next(r for r in rows if r['task_id'] == 140408)
        row['envelope_json']['scenario_key'] = 'peripheral_setup'
        row['envelope_json']['outcome'] = {'kind': 'resolution', 'outcome_key': 'in_work_standard'}
        found = next(f for f in baseline.evaluate(rows, self.labels)['findings'] if f['task_id'] == 140408)
        self.assertEqual(found['errors'], [])

    def test_action_gate_and_missing_labels(self):
        rows = copy.deepcopy(self.rows)
        row = next(r for r in rows if r['task_id'] == 140511)
        row['envelope_json']['gates']['can_execute_action'] = True
        row['envelope_json']['outcome']['action'] = 'install_printer'
        found = next(f for f in baseline.evaluate(rows, self.labels)['findings'] if f['task_id'] == 140511)
        self.assertIn('forbidden_action_gate', found['errors'])
        with self.assertRaises(ValueError):
            baseline.evaluate(rows, self.labels[:-1])

    def test_privacy_preserves_negations_models_and_host_links(self):
        sanitizer = baseline.Sanitizer([{'CreatorPhone': '207', 'Creator': 'Нет'}])
        value = sanitizer.text('нет сети, Samsung M2070; NTEMW 0771 / NTEMW0771; ТКТ0151 / TKT0151')
        self.assertIn('нет сети, Samsung M2070', value)
        self.assertIn('NTEMW 1000 / NTEMW1000', value)
        self.assertIn('ТКТ1001 / TKT1001', value)
        self.assertEqual(sanitizer.walk({'password': 'test-secret'}), {'password': '<redacted>'})

    def test_fixture_keeps_key_semantics(self):
        rows = {r['task_id']: r for r in self.rows}
        self.assertRegex(rows[140408]['context_json']['task']['Description'], r'NTEMW \d{4}')
        suffix = rows[140408]['context_json']['task']['Description'].split()[-1]
        self.assertEqual(rows[140408]['envelope_json']['facts_summary']['pc_name']['value'], suffix)
        self.assertIn('не подключаются', rows[140511]['context_json']['task']['Name'].lower())
        self.assertIn('140500', rows[140686]['context_json']['task']['Description'])
        targets = rows[139241]['envelope_json']['outcome']['parameters']['printer_targets']
        self.assertEqual(len(targets), 3)
        self.assertEqual(targets[2]['printer_name'], 'Samsung Xpress M2070')
        self.assertEqual(targets[2]['connection_type'], 'usb')


    def test_check_fixtures_and_checksum_verification(self):
        manifest = baseline.check_fixtures()
        self.assertEqual(manifest['actual_count'], 99)
        # Test tampered fixture detection
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            (tmp_path / 'manifest.json').write_text(json.dumps({'sha256': {'file.txt': 'dummy'}}), encoding='utf-8')
            (tmp_path / 'file.txt').write_text('bad content', encoding='utf-8')
            with self.assertRaises(ValueError):
                baseline.check_fixtures(tmp_path)

    def test_prevent_network_blocks_socket(self):
        import socket
        with baseline.prevent_network():
            with self.assertRaises(baseline.BlockedNetworkCallError):
                socket.create_connection(('1.1.1.1', 80))

    def test_replay_and_compare_lifecycle(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            # Replay a subset of 2 tickets to keep unit test fast
            sub_dec = tmp_path / 'sub_decisions.json'
            sub_dec.write_bytes(baseline.encoded(self.rows[:2]))
            
            # Replay into tmp_path
            results = baseline.replay(
                output_dir=tmp_path / 'replay',
                decisions_path=sub_dec,
            )
            self.assertEqual(len(results), 2)
            self.assertTrue((tmp_path / 'replay/decisions-after.json').exists())
            self.assertTrue((tmp_path / 'replay/manifest.json').exists())

            # Compare against the subset
            cmp_res = baseline.compare(
                candidate_path=tmp_path / 'replay/decisions-after.json',
                baseline_path=sub_dec,
                output_path=tmp_path / 'replay/comparison.md',
            )
            self.assertEqual(cmp_res['total'], 2)
            self.assertTrue((tmp_path / 'replay/comparison.md').exists())


if __name__ == '__main__':
    unittest.main()
