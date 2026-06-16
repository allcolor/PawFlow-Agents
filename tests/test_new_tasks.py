"""Tests unitaires pour les nouvelles tasks (Phase 4)."""

import unittest
import json
import hashlib
import base64
import gzip
import zlib
import os
import tempfile

import tasks  # noqa: F401 - trigger auto-registration
from core import FlowFile

from tasks.system.generate_flowfile import GenerateFlowFileTask
from tasks.system.hash_content import HashContentTask
from tasks.system.list_files import ListFilesTask
from tasks.data.evaluate_jsonpath import EvaluateJSONPathTask
from tasks.data.extract_text import ExtractTextTask
from tasks.data.compress_content import CompressContentTask
from tasks.data.validate_json import ValidateJSONTask
from tasks.data.convert_charset import ConvertCharsetTask


class TestStartupTriggerTask(unittest.TestCase):

    def _task(self, config=None):
        from tasks.system.startup_trigger import StartupTriggerTask
        return StartupTriggerTask(config or {})

    def test_registered(self):
        from tasks import register_all_tasks
        register_all_tasks()
        from core import TaskFactory
        self.assertIsNotNone(TaskFactory.get("startupTrigger"))

    def test_fires_once_then_quiet(self):
        task = self._task({'content': 'init'})
        self.assertTrue(task.has_pending_input())
        results = task.execute(None)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].get_content(), b"init")
        self.assertEqual(results[0].get_attribute('startup.trigger'), 'true')
        self.assertTrue(results[0].get_attribute('startup.fired_at'))
        # One-shot: no longer pending, and re-executing yields nothing.
        self.assertFalse(task.has_pending_input())
        self.assertEqual(task.execute(None), [])

    def test_reset_does_not_rearm(self):
        task = self._task()
        task.execute(None)
        task.reset()
        self.assertFalse(task.has_pending_input())


class TestGenerateFlowFileTask(unittest.TestCase):

    def test_generate_single(self):
        task = GenerateFlowFileTask({'content': 'hello', 'count': 1})
        ff = FlowFile(content=b"dummy")
        results = task.execute(ff)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].get_content(), b"hello")
        self.assertEqual(results[0].get_attribute('mime.type'), 'text/plain')
        self.assertEqual(results[0].get_attribute('filename'), 'generated_0.dat')

    def test_generate_multiple(self):
        task = GenerateFlowFileTask({'content': 'data', 'count': 3})
        ff = FlowFile(content=b"dummy")
        results = task.execute(ff)
        self.assertEqual(len(results), 3)
        for i, r in enumerate(results):
            self.assertEqual(r.get_content(), b"data")
            self.assertEqual(r.get_attribute('filename'), f'generated_{i}.dat')

    def test_custom_attributes(self):
        task = GenerateFlowFileTask({
            'content': 'x',
            'custom_attributes': {'env': 'prod', 'version': '2'}
        })
        ff = FlowFile(content=b"dummy")
        results = task.execute(ff)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].get_attribute('env'), 'prod')
        self.assertEqual(results[0].get_attribute('version'), '2')

    def test_content_file_loads_flow_asset(self):
        with tempfile.TemporaryDirectory() as tmp:
            assets = os.path.join(tmp, 'assets')
            os.makedirs(assets, exist_ok=True)
            with open(os.path.join(assets, 'install.html'), 'w', encoding='utf-8') as f:
                f.write('<html>Installer</html>')

            task = GenerateFlowFileTask({
                'content_file': 'install.html',
                'content_type': 'text/html; charset=utf-8',
            })
            task.set_flow_source_dir(tmp)
            results = task.execute(FlowFile(content=b"dummy"))

            self.assertEqual(results[0].get_content(), b'<html>Installer</html>')
            self.assertEqual(results[0].get_attribute('mime.type'), 'text/html; charset=utf-8')


class TestHashContentTask(unittest.TestCase):

    def test_sha256(self):
        task = HashContentTask({'algorithm': 'sha256'})
        ff = FlowFile(content=b"test data")
        results = task.execute(ff)
        expected = hashlib.sha256(b"test data").hexdigest()
        self.assertEqual(results[0].get_attribute('content.hash'), expected)

    def test_md5_custom_attribute(self):
        task = HashContentTask({'algorithm': 'md5', 'attribute_name': 'md5.hash'})
        ff = FlowFile(content=b"test data")
        results = task.execute(ff)
        expected = hashlib.md5(b"test data").hexdigest()
        self.assertEqual(results[0].get_attribute('md5.hash'), expected)

    def test_sha512(self):
        task = HashContentTask({'algorithm': 'sha512'})
        ff = FlowFile(content=b"abc")
        results = task.execute(ff)
        expected = hashlib.sha512(b"abc").hexdigest()
        self.assertEqual(results[0].get_attribute('content.hash'), expected)


class TestEvaluateJSONPathTask(unittest.TestCase):

    def test_simple_path(self):
        task = EvaluateJSONPathTask({
            'expressions': {'user_name': 'user.name'}
        })
        data = {"user": {"name": "Alice"}}
        ff = FlowFile(content=json.dumps(data).encode())
        results = task.execute(ff)
        self.assertEqual(results[0].get_attribute('user_name'), 'Alice')

    def test_array_index(self):
        task = EvaluateJSONPathTask({
            'expressions': {'second_id': 'items.1.id'}
        })
        data = {"items": [{"id": 1}, {"id": 2}]}
        ff = FlowFile(content=json.dumps(data).encode())
        results = task.execute(ff)
        self.assertEqual(results[0].get_attribute('second_id'), '2')

    def test_nested_object_as_attribute(self):
        task = EvaluateJSONPathTask({
            'expressions': {'addr': 'user.address'}
        })
        data = {"user": {"address": {"city": "Paris", "zip": "75001"}}}
        ff = FlowFile(content=json.dumps(data).encode())
        results = task.execute(ff)
        attr_val = json.loads(results[0].get_attribute('addr'))
        self.assertEqual(attr_val['city'], 'Paris')

    def test_content_destination(self):
        task = EvaluateJSONPathTask({
            'expressions': {'name': 'user.name', 'age': 'user.age'},
            'destination': 'content'
        })
        data = {"user": {"name": "Bob", "age": 30}}
        ff = FlowFile(content=json.dumps(data).encode())
        results = task.execute(ff)
        output = json.loads(results[0].get_content())
        self.assertEqual(output['name'], 'Bob')
        self.assertEqual(output['age'], 30)

    def test_missing_path(self):
        task = EvaluateJSONPathTask({
            'expressions': {'missing': 'does.not.exist'}
        })
        ff = FlowFile(content=b'{"a": 1}')
        results = task.execute(ff)
        self.assertIsNone(results[0].get_attribute('missing'))


class TestExtractTextTask(unittest.TestCase):

    def test_extract_email(self):
        task = ExtractTextTask({'pattern': r'[\w.]+@[\w.]+'})
        ff = FlowFile(content=b"contact: user@example.com please")
        results = task.execute(ff)
        self.assertEqual(results[0].get_attribute('extracted.text'), 'user@example.com')

    def test_no_match(self):
        task = ExtractTextTask({'pattern': r'[\w.]+@[\w.]+'})
        ff = FlowFile(content=b"no email here")
        results = task.execute(ff)
        self.assertEqual(results[0].get_attribute('extracted.text'), '')

    def test_capture_group(self):
        task = ExtractTextTask({
            'pattern': r'version[:\s]+(\d+\.\d+\.\d+)',
            'group': 1,
            'attribute_name': 'version'
        })
        ff = FlowFile(content=b"app version: 2.1.0 released")
        results = task.execute(ff)
        self.assertEqual(results[0].get_attribute('version'), '2.1.0')


class TestCompressContentTask(unittest.TestCase):

    def test_gzip_roundtrip(self):
        original = b"Hello World! " * 100
        # Compress
        task_c = CompressContentTask({'algorithm': 'gzip', 'mode': 'compress'})
        ff = FlowFile(content=original)
        compressed = task_c.execute(ff)[0]
        self.assertLess(len(compressed.get_content()), len(original))
        self.assertEqual(compressed.get_attribute('mime.type'), 'application/gzip')
        # Decompress
        task_d = CompressContentTask({'algorithm': 'gzip', 'mode': 'decompress'})
        decompressed = task_d.execute(compressed)[0]
        self.assertEqual(decompressed.get_content(), original)

    def test_zlib_roundtrip(self):
        original = b"Data data data " * 50
        task_c = CompressContentTask({'algorithm': 'zlib', 'mode': 'compress'})
        ff = FlowFile(content=original)
        compressed = task_c.execute(ff)[0]
        self.assertLess(len(compressed.get_content()), len(original))
        task_d = CompressContentTask({'algorithm': 'zlib', 'mode': 'decompress'})
        decompressed = task_d.execute(compressed)[0]
        self.assertEqual(decompressed.get_content(), original)


class TestValidateJSONTask(unittest.TestCase):

    def test_valid_json(self):
        task = ValidateJSONTask({})
        ff = FlowFile(content=b'{"key": "value", "num": 42}')
        results = task.execute(ff)
        self.assertEqual(results[0].get_attribute('json.valid'), 'true')

    def test_invalid_json(self):
        task = ValidateJSONTask({})
        ff = FlowFile(content=b'not json {invalid')
        results = task.execute(ff)
        self.assertEqual(results[0].get_attribute('json.valid'), 'false')

    def test_route_attribute(self):
        task = ValidateJSONTask({'route_to': 'valid'})
        ff_valid = FlowFile(content=b'{"ok": true}')
        ff_invalid = FlowFile(content=b'nope')
        r_valid = task.execute(ff_valid)[0]
        r_invalid = task.execute(ff_invalid)[0]
        self.assertEqual(r_valid.get_attribute('route'), 'valid')
        self.assertEqual(r_invalid.get_attribute('route'), 'invalid')


class TestListFilesTask(unittest.TestCase):

    def test_list_directory(self):
        # Use the repository tasks directory which has .json files
        task = ListFilesTask({'directory': str(__import__('core.paths', fromlist=['REPOSITORY_DIR']).REPOSITORY_DIR / 'tasks' / 'global'), 'pattern': '*.json'})
        ff = FlowFile(content=b"")
        results = task.execute(ff)
        self.assertGreater(len(results), 0)
        for r in results:
            self.assertTrue(r.get_attribute('filename').endswith('.json'))
            self.assertIsNotNone(r.get_attribute('absolute.path'))
            self.assertIsNotNone(r.get_attribute('fileSize'))
            self.assertIsNotNone(r.get_attribute('file.lastModified'))

    def test_nonexistent_directory(self):
        task = ListFilesTask({'directory': 'nonexistent_dir_xyz'})
        ff = FlowFile(content=b"")
        with self.assertRaises(ValueError):
            task.execute(ff)

    def test_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            task = ListFilesTask({'directory': tmpdir, 'pattern': '*.txt'})
            ff = FlowFile(content=b"")
            results = task.execute(ff)
            self.assertEqual(len(results), 0)


class TestConvertCharsetTask(unittest.TestCase):

    def test_utf8_to_latin1(self):
        task = ConvertCharsetTask({
            'source_encoding': 'utf-8',
            'target_encoding': 'latin-1'
        })
        ff = FlowFile(content="Hello World".encode('utf-8'))
        results = task.execute(ff)
        self.assertEqual(results[0].get_content(), b"Hello World")
        self.assertEqual(results[0].get_attribute('charset'), 'latin-1')

    def test_roundtrip(self):
        original = "Cafe avec des accents".encode('utf-8')
        # UTF-8 -> Latin-1
        task1 = ConvertCharsetTask({
            'source_encoding': 'utf-8',
            'target_encoding': 'latin-1'
        })
        ff = FlowFile(content=original)
        result1 = task1.execute(ff)[0]
        # Latin-1 -> UTF-8
        task2 = ConvertCharsetTask({
            'source_encoding': 'latin-1',
            'target_encoding': 'utf-8'
        })
        result2 = task2.execute(result1)[0]
        self.assertEqual(result2.get_content(), original)
        self.assertEqual(result2.get_attribute('charset'), 'utf-8')


class TestExecuteScriptTask(unittest.TestCase):

    def test_simple_script(self):
        from tasks.system.execute_script import ExecuteScriptTask
        task = ExecuteScriptTask({'script': 'result = content.upper()'})
        ff = FlowFile(content=b"hello world")
        results = task.execute(ff)
        self.assertEqual(results[0].get_content(), b"HELLO WORLD")

    def test_script_with_attributes(self):
        from tasks.system.execute_script import ExecuteScriptTask
        task = ExecuteScriptTask({'script': 'result = content + " by " + attributes.get("author", "unknown")'})
        ff = FlowFile(content=b"article", attributes={"author": "Alice"})
        results = task.execute(ff)
        self.assertEqual(results[0].get_content(), b"article by Alice")

    def test_invalid_script(self):
        from tasks.system.execute_script import ExecuteScriptTask
        from core import TaskError
        task = ExecuteScriptTask({'script': 'result = 1/0'})
        ff = FlowFile(content=b"test")
        with self.assertRaises(TaskError):
            task.execute(ff)

    def test_helper_function_sees_injected_names(self):
        # Regression: a function defined at the script's top level must be able
        # to resolve the injected names (flowfile, content, attributes). With a
        # two-dict exec they live only in locals and the function's __globals__
        # can't see them -> "name 'flowfile' is not defined" (the web_help_bot
        # _respond() 500). A single shared namespace keeps them visible.
        from tasks.system.execute_script import ExecuteScriptTask
        script = (
            "def _respond():\n"
            "    flowfile.set_attribute('done', attributes.get('author', '?'))\n"
            "    return content.upper()\n"
            "result = _respond()\n"
        )
        task = ExecuteScriptTask({'script': script})
        ff = FlowFile(content=b"hello", attributes={"author": "Alice"})
        results = task.execute(ff)
        self.assertEqual(results[0].get_content(), b"HELLO")
        self.assertEqual(results[0].get_attribute('done'), "Alice")

    def test_get_service_returns_declared_service(self):
        from tasks.system.execute_script import ExecuteScriptTask

        class _FakeSvc:
            def call_api(self, method, params=None):
                return {"method": method, "params": params}

        task = ExecuteScriptTask({
            'script': "result = get_service('tg').call_api('getMe')['method']",
        })
        task.set_services({'tg': _FakeSvc()})
        results = task.execute(FlowFile(content=b""))
        self.assertEqual(results[0].get_content(), b"getMe")

    def test_get_service_undeclared_raises(self):
        from tasks.system.execute_script import ExecuteScriptTask
        from core import TaskError
        task = ExecuteScriptTask({
            'script': "result = get_service('nope')",
        })
        task.set_services({'tg': object()})
        with self.assertRaises(TaskError):
            task.execute(FlowFile(content=b""))


class TestFilterContentTask(unittest.TestCase):

    def test_include_mode(self):
        from tasks.data.filter_content import FilterContentTask
        task = FilterContentTask({'pattern': r'ERROR'})
        content = "INFO: ok\nERROR: bad\nINFO: fine\nERROR: worse"
        ff = FlowFile(content=content.encode())
        results = task.execute(ff)
        lines = results[0].get_content().decode().split('\n')
        self.assertEqual(len(lines), 2)
        self.assertTrue(all('ERROR' in l for l in lines))

    def test_exclude_mode(self):
        from tasks.data.filter_content import FilterContentTask
        task = FilterContentTask({'pattern': r'DEBUG', 'mode': 'exclude'})
        content = "DEBUG: verbose\nINFO: ok\nDEBUG: more"
        ff = FlowFile(content=content.encode())
        results = task.execute(ff)
        self.assertEqual(results[0].get_content().decode(), "INFO: ok")


class TestBase64EncodeTask(unittest.TestCase):

    def test_encode(self):
        from tasks.data.base64_encode import Base64EncodeTask
        task = Base64EncodeTask({'mode': 'encode'})
        ff = FlowFile(content=b"Hello World")
        results = task.execute(ff)
        self.assertEqual(results[0].get_content(), base64.b64encode(b"Hello World"))

    def test_roundtrip(self):
        from tasks.data.base64_encode import Base64EncodeTask
        original = b"Binary data: \x00\x01\x02\xff"
        task_enc = Base64EncodeTask({'mode': 'encode'})
        ff = FlowFile(content=original)
        encoded = task_enc.execute(ff)[0]
        task_dec = Base64EncodeTask({'mode': 'decode'})
        decoded = task_dec.execute(encoded)[0]
        self.assertEqual(decoded.get_content(), original)


class TestCountTextTask(unittest.TestCase):

    def test_count(self):
        from tasks.data.count_text import CountTextTask
        task = CountTextTask({})
        content = "line one\nline two\nline three"
        ff = FlowFile(content=content.encode())
        results = task.execute(ff)
        self.assertEqual(results[0].get_attribute('text.line.count'), '3')
        self.assertEqual(results[0].get_attribute('text.word.count'), '6')
        self.assertEqual(results[0].get_attribute('text.character.count'), str(len(content)))


class TestDuplicateContentTask(unittest.TestCase):

    def test_duplicate(self):
        from tasks.control.duplicate_content import DuplicateContentTask
        task = DuplicateContentTask({'copies': 3})
        ff = FlowFile(content=b"original", attributes={"key": "value"})
        results = task.execute(ff)
        self.assertEqual(len(results), 3)
        for i, r in enumerate(results):
            self.assertEqual(r.get_content(), b"original")
            self.assertEqual(r.get_attribute('key'), 'value')
            self.assertEqual(r.get_attribute('copy.index'), str(i))


if __name__ == '__main__':
    unittest.main()
