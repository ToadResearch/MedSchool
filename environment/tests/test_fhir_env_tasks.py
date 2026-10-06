"""Offline import and structured-answer checks; no FHIR or model calls."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from main import answer_matches, load_environment, to_vf_format
from src.tasks.task_manager import TaskManager


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('fhir_env_import', ROOT / 'scripts/import_fhir_env_tasks.py')
importer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(importer)


class FhirEnvTaskTests(unittest.TestCase):
    def test_all_imported_splits_load_and_keep_answers_out_of_prompts(self):
        for split, count in (('train', 19530), ('dev', 2186)):
            path = ROOT / 'tasks' / f'fhir_env_{split}.json'
            with self.subTest(split=split), tempfile.TemporaryDirectory() as cache, \
                 patch('datasets.config.HF_DATASETS_CACHE', Path(cache)), \
                 patch('main.MedSchoolEnv') as env:
                load_environment(task_filepath=str(path))
                dataset = env.call_args.kwargs['dataset']
                self.assertEqual(len(dataset), count)
                tasks = TaskManager(task_filepath=str(path)).tasks
                self.assertEqual(len({task.id for task in tasks}), count)
                self.assertEqual(len({task.meta['family'] for task in tasks}), 37)
                for task, example in zip(tasks, dataset):
                    self.assertEqual(example['prompt'][1]['content'], task.input['task'])
                    self.assertEqual(json.loads(example['answer']), json.loads(task.output['upstream_gold'])['answer'])
                    metadata = json.loads(task.meta['upstream_metadata'])
                    row = dict(metadata, id=task.id, prompt=task.input['task'],
                               gold=json.loads(task.output['upstream_gold']),
                               reference_steps=json.loads(task.output['reference_steps']))
                    self.assertEqual(importer.convert_task(row, task.meta['source_revision'])['type'],
                                     [operation.value for operation in task.type])

    def test_structured_answers_ignore_key_order_and_evidence(self):
        expected = '{"unit": "g/dL", "value": 11.1}'
        for response in ('{"answer":{"value":11.1,"unit":"g/dL"},"evidence":["Observation/example"]}',
                         '```json\n{"unit":"g/dL","value":11.1}\n```'):
            self.assertTrue(answer_matches(response, expected))
        for response in ('{"answer":{"value":11.2,"unit":"g/dL"}}',
                         '{"unit":"g/dL"}', 'not JSON',
                         '{"value":11.1,"unit":"g/dL","extra":true}'):
            self.assertFalse(answer_matches(response, expected))

    def test_booleans_lists_and_legacy_scalars(self):
        self.assertFalse(answer_matches('{"updated":1}', '{"updated":true}'))
        self.assertTrue(answer_matches('{"answer":{"items":["a","b"]}}', '{"items":["a","b"]}'))
        self.assertFalse(answer_matches('{"items":["b","a"]}', '{"items":["a","b"]}'))
        self.assertTrue(answer_matches('There are 800 patients.', '800'))
        self.assertTrue(answer_matches('CAFÉ', 'café'))
        example = to_vf_format({'input': {'task': 'question'}, 'output': {'answer': {'updated': True}}}, 'system')
        self.assertEqual(json.loads(example['answer']), {'updated': True})


if __name__ == '__main__':
    unittest.main()
