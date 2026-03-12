"""Tests unitaires pour les tasks individuelles."""

import unittest
import json
from core import FlowFile
from tasks.data.transform_json import TransformJSONTask
from tasks.control.split_content import SplitContentTask
from tasks.control.merge_content import MergeContentTask
from tasks.system.update_attribute import UpdateAttributeTask


class TestTransformJSON(unittest.TestCase):

    def test_extract_root(self):
        task = TransformJSONTask({'operation': 'extract', 'json_path': '$'})
        data = {'name': 'test', 'value': 42}
        ff = FlowFile(content=json.dumps(data).encode())
        results = task.execute(ff)
        self.assertEqual(json.loads(results[0].get_content()), data)

    def test_extract_nested(self):
        task = TransformJSONTask({'operation': 'extract', 'json_path': '$.data.name'})
        data = {'data': {'name': 'hello', 'id': 1}}
        ff = FlowFile(content=json.dumps(data).encode())
        results = task.execute(ff)
        self.assertEqual(json.loads(results[0].get_content()), 'hello')

    def test_set_values(self):
        task = TransformJSONTask({'operation': 'set', 'set_values': {'new_key': 'new_val'}})
        ff = FlowFile(content=b'{"existing": "data"}')
        results = task.execute(ff)
        output = json.loads(results[0].get_content())
        self.assertEqual(output['existing'], 'data')
        self.assertEqual(output['new_key'], 'new_val')

    def test_delete_keys(self):
        task = TransformJSONTask({'operation': 'delete', 'delete_keys': ['remove_me']})
        ff = FlowFile(content=b'{"keep": "yes", "remove_me": "bye"}')
        results = task.execute(ff)
        output = json.loads(results[0].get_content())
        self.assertIn('keep', output)
        self.assertNotIn('remove_me', output)

    def test_flatten(self):
        task = TransformJSONTask({'operation': 'flatten'})
        data = {'a': {'b': {'c': 1}}, 'd': [10, 20]}
        ff = FlowFile(content=json.dumps(data).encode())
        results = task.execute(ff)
        output = json.loads(results[0].get_content())
        self.assertEqual(output['a.b.c'], 1)
        self.assertEqual(output['d[0]'], 10)

    def test_invalid_json(self):
        from core import TaskError
        task = TransformJSONTask({'operation': 'extract'})
        ff = FlowFile(content=b'not json')
        with self.assertRaises(TaskError):
            task.execute(ff)


class TestSplitContent(unittest.TestCase):

    def test_split_by_newline(self):
        task = SplitContentTask({'separator': '\n'})
        ff = FlowFile(content=b'line1\nline2\nline3')
        results = task.execute(ff)
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0].get_content(), b'line1')
        self.assertEqual(results[2].get_content(), b'line3')

    def test_split_with_index_attributes(self):
        task = SplitContentTask({'separator': ','})
        ff = FlowFile(content=b'a,b,c')
        results = task.execute(ff)
        self.assertEqual(results[0].get_attribute('fragment.index'), '0')
        self.assertEqual(results[0].get_attribute('fragment.count'), '3')

    def test_split_max_splits(self):
        task = SplitContentTask({'separator': ',', 'max_splits': 1})
        ff = FlowFile(content=b'a,b,c')
        results = task.execute(ff)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[1].get_content(), b'b,c')

    def test_split_empty_content(self):
        task = SplitContentTask({'separator': '\n'})
        ff = FlowFile(content=b'')
        results = task.execute(ff)
        self.assertEqual(len(results), 1)


class TestMergeContent(unittest.TestCase):

    def test_merge_two_flowfiles(self):
        task = MergeContentTask({'separator': '|', 'min_entries': 2})
        ff1 = FlowFile(content=b'hello')
        ff2 = FlowFile(content=b'world')

        results1 = task.execute(ff1)
        self.assertEqual(len(results1), 0)

        results2 = task.execute(ff2)
        self.assertEqual(len(results2), 1)
        self.assertEqual(results2[0].get_content(), b'hello|world')
        self.assertEqual(results2[0].get_attribute('merge.count'), '2')

    def test_merge_with_header_footer(self):
        task = MergeContentTask({
            'separator': ',', 'min_entries': 2,
            'header': 'START', 'footer': 'END'
        })
        task.execute(FlowFile(content=b'A'))
        results = task.execute(FlowFile(content=b'B'))
        content = results[0].get_content()
        self.assertTrue(content.startswith(b'START'))
        self.assertTrue(content.endswith(b'END'))


class TestUpdateAttribute(unittest.TestCase):

    def test_set_attributes(self):
        task = UpdateAttributeTask({'set': {'env': 'prod', 'version': '2.0'}})
        ff = FlowFile(content=b'data')
        results = task.execute(ff)
        self.assertEqual(results[0].get_attribute('env'), 'prod')
        self.assertEqual(results[0].get_attribute('version'), '2.0')

    def test_delete_attributes(self):
        task = UpdateAttributeTask({'delete': ['temp']})
        ff = FlowFile(content=b'data', attributes={'temp': 'val', 'keep': 'yes'})
        results = task.execute(ff)
        self.assertIsNone(results[0].get_attribute('temp'))
        self.assertEqual(results[0].get_attribute('keep'), 'yes')

    def test_resolve_references(self):
        task = UpdateAttributeTask({'set': {'full_path': '${path}/${filename}'}})
        ff = FlowFile(content=b'', attributes={'path': '/data', 'filename': 'test.csv'})
        results = task.execute(ff)
        self.assertEqual(results[0].get_attribute('full_path'), '/data/test.csv')


if __name__ == '__main__':
    unittest.main()