"""Offline regression checks for the benchmark workflow (no model or Docker calls)."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from datasets import Dataset

import eval as benchmark
from main import load_environment


class BenchmarkTests(unittest.TestCase):
    def test_retained_task_suites_load_into_environment(self):
        for name in ('synthetic_hospital_counts', 'toy_tasks'):
            with self.subTest(suite=name), tempfile.TemporaryDirectory() as cache, \
                 patch('datasets.config.HF_DATASETS_CACHE', Path(cache)), \
                 patch('main.MedSchoolEnv') as env_class:
                load_environment(task_filepath=f'tasks/{name}.json')
                args = env_class.call_args.kwargs
                dataset = args['dataset']
                self.assertGreater(len(dataset), 0)
                self.assertEqual(dataset[0]['prompt'][0]['role'], 'system')
                self.assertEqual(dataset[0]['prompt'][1]['role'], 'user')
                self.assertIsInstance(dataset[0]['answer'], str)
                self.assertIsNotNone(args['rubric'])

    def test_evaluation_caps_concurrency_and_exports_rewards(self):
        env = Mock()
        env.dataset = Dataset.from_dict({'id': ['a', 'b']})
        env.evaluate.return_value = object()
        env.make_dataset.return_value = Dataset.from_dict({
            'reward': [1.0, 0.0],
            'completion': ['800', 'wrong'],
            'info': [{'session': 'a'}, {'session': 'b'}],
        })
        settings = Mock()
        settings.sandbox.max_concurrent_sessions = 5
        args = ['eval.py', '-m', 'test/model', '-k', 'BENCHMARK_TEST_KEY',
                '-t', 'synthetic_hospital_counts', '--requested', '1000']
        console = io.StringIO()
        with tempfile.TemporaryDirectory() as temp:
            cwd = os.getcwd()
            try:
                os.chdir(temp)
                with patch('sys.argv', args), \
                     patch.dict(os.environ, {'BENCHMARK_TEST_KEY': 'offline-placeholder'}), \
                     patch.object(benchmark, 'load_dotenv'), \
                     patch.object(benchmark, 'OpenAI') as client, \
                     patch.object(benchmark, 'load_environment', return_value=env) as load, \
                     patch.object(benchmark, 'get_settings', return_value=settings), \
                     contextlib.redirect_stdout(console):
                    benchmark.main()
                load.assert_called_once_with(task_filepath='tasks/synthetic_hospital_counts.json')
                call = env.evaluate.call_args.kwargs
                self.assertEqual(call['num_examples'], 2)
                self.assertEqual(call['max_concurrent'], 2)
                self.assertIs(call['client'], client.return_value)
                files = list(Path('outputs/test_model').glob('*/results.json'))
                self.assertEqual(len(files), 1)
                rows = json.loads(files[0].read_text())
                self.assertEqual([row['reward'] for row in rows], [1.0, 0.0])
                self.assertNotIn('info', rows[0])
                self.assertIn('Average reward: 0.5000', console.getvalue())
            finally:
                os.chdir(cwd)


if __name__ == '__main__':
    unittest.main()
