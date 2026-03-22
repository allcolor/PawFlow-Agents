"""Tests for Expression Language pipeline operations."""

import unittest
from core.expression import resolve_expression
from core.expression_pipeline import parse_pipeline, evaluate_pipeline


class TestParsePipeline(unittest.TestCase):
    """Test pipeline parser."""

    def test_no_pipeline(self):
        key, ops = parse_pipeline("global.mavar")
        self.assertEqual(key, "global.mavar")
        self.assertEqual(ops, [])

    def test_single_op(self):
        key, ops = parse_pipeline("global.x:upper")
        self.assertEqual(key, "global.x")
        self.assertEqual(ops, [("upper", [])])

    def test_op_with_args(self):
        key, ops = parse_pipeline('global.x:replace("a","b")')
        self.assertEqual(key, "global.x")
        self.assertEqual(ops, [("replace", ["a", "b"])])

    def test_chained_ops(self):
        key, ops = parse_pipeline("global.x:upper:trim:length")
        self.assertEqual(key, "global.x")
        self.assertEqual(len(ops), 3)

    def test_important_not_pipeline(self):
        key, ops = parse_pipeline("global.x:!important")
        self.assertEqual(key, "global.x:!important")
        self.assertEqual(ops, [])

    def test_generator(self):
        key, ops = parse_pipeline(":uuid")
        self.assertEqual(key, "")
        self.assertEqual(ops, [("uuid", [])])

    def test_nested_expr_in_args(self):
        key, ops = parse_pipeline('global.x:else(${global.y:upper})')
        self.assertEqual(key, "global.x")
        self.assertEqual(len(ops), 1)
        self.assertIn("${global.y:upper}", ops[0][1][0])


class TestEvaluatePipeline(unittest.TestCase):
    """Test pipeline evaluator."""

    def test_upper(self):
        self.assertEqual(evaluate_pipeline("hello", [("upper", [])]), "HELLO")

    def test_lower(self):
        self.assertEqual(evaluate_pipeline("HELLO", [("lower", [])]), "hello")

    def test_trim(self):
        self.assertEqual(evaluate_pipeline("  hi  ", [("trim", [])]), "hi")

    def test_capitalize(self):
        self.assertEqual(evaluate_pipeline("hello world", [("capitalize", [])]), "Hello world")

    def test_title(self):
        self.assertEqual(evaluate_pipeline("hello world", [("title", [])]), "Hello World")

    def test_reverse(self):
        self.assertEqual(evaluate_pipeline("abc", [("reverse", [])]), "cba")

    def test_length(self):
        self.assertEqual(evaluate_pipeline("hello", [("length", [])]), "5")

    def test_substr(self):
        self.assertEqual(evaluate_pipeline("hello", [("substr", ["1", "3"])]), "el")

    def test_replace(self):
        self.assertEqual(evaluate_pipeline("hello", [("replace", ["l", "r"])]), "herro")

    def test_append(self):
        self.assertEqual(evaluate_pipeline("hello", [("append", [" world"])]), "hello world")

    def test_prepend(self):
        self.assertEqual(evaluate_pipeline("world", [("prepend", ["hello "])]), "hello world")

    def test_split_index(self):
        r = evaluate_pipeline("a,b,c", [("split", [","]), ("index", ["1"])])
        self.assertEqual(r, "b")

    def test_split_first_last(self):
        self.assertEqual(evaluate_pipeline("a,b,c", [("split", [","]), ("first", [])]), "a")
        self.assertEqual(evaluate_pipeline("a,b,c", [("split", [","]), ("last", [])]), "c")

    def test_split_join(self):
        r = evaluate_pipeline("a,b,c", [("split", [","]), ("join", ["-"])])
        self.assertEqual(r, "a-b-c")

    def test_default_empty(self):
        self.assertEqual(evaluate_pipeline("", [("default", ["fallback"])]), "fallback")

    def test_default_nonempty(self):
        self.assertEqual(evaluate_pipeline("value", [("default", ["fallback"])]), "value")

    def test_equals_then_else(self):
        ops = [("equals", ["yes"]), ("then", ["OUI"]), ("else", ["NON"])]
        self.assertEqual(evaluate_pipeline("yes", ops), "OUI")
        self.assertEqual(evaluate_pipeline("no", ops), "NON")

    def test_contains(self):
        self.assertEqual(evaluate_pipeline("hello world", [("contains", ["world"])]), "true")
        self.assertEqual(evaluate_pipeline("hello", [("contains", ["xyz"])]), "false")

    def test_starts_with(self):
        self.assertEqual(evaluate_pipeline("http://x", [("starts_with", ["http"])]), "true")

    def test_matches(self):
        self.assertEqual(evaluate_pipeline("user@test.com", [("matches", ["@.*\\.com$"])]), "true")

    def test_is_empty(self):
        self.assertEqual(evaluate_pipeline("", [("is_empty", [])]), "true")
        self.assertEqual(evaluate_pipeline("x", [("is_empty", [])]), "false")

    def test_to_int(self):
        self.assertEqual(evaluate_pipeline("42.7", [("to_int", [])]), "42")

    def test_base64(self):
        r = evaluate_pipeline("hello", [("base64_encode", [])])
        self.assertEqual(evaluate_pipeline(r, [("base64_decode", [])]), "hello")

    def test_url_encode(self):
        self.assertEqual(evaluate_pipeline("a b&c", [("url_encode", [])]), "a%20b%26c")

    def test_hash_md5(self):
        r = evaluate_pipeline("test", [("hash_md5", [])])
        self.assertEqual(len(r), 32)

    def test_json_get(self):
        j = '{"data":{"name":"Bob"}}'
        self.assertEqual(evaluate_pipeline(j, [("json_get", ["data.name"])]), "Bob")

    def test_uuid(self):
        r = evaluate_pipeline("", [("uuid", [])])
        self.assertEqual(len(r), 36)  # UUID format

    def test_uuid_short(self):
        r = evaluate_pipeline("", [("uuid_short", [])])
        self.assertEqual(len(r), 12)

    def test_now(self):
        r = evaluate_pipeline("", [("now", ["%Y"])])
        self.assertEqual(r, "2026")

    def test_timestamp(self):
        r = evaluate_pipeline("", [("timestamp", [])])
        self.assertTrue(r.isdigit())

    def test_pad_left(self):
        self.assertEqual(evaluate_pipeline("42", [("pad_left", ["5", "0"])]), "00042")

    def test_chain_multiple(self):
        r = evaluate_pipeline("  Hello World  ", [("trim", []), ("upper", []), ("substr", ["0", "5"])])
        self.assertEqual(r, "HELLO")


class TestResolveExpressionPipeline(unittest.TestCase):
    """Test full integration with resolve_expression."""

    def test_simple_pipeline(self):
        r = resolve_expression("${flow.x:upper}", parameters={"x": "hello"})
        self.assertEqual(r, "HELLO")

    def test_conditional(self):
        r = resolve_expression(
            '${flow.v:equals("yes"):then("OUI"):else("NON")}',
            parameters={"v": "yes"})
        self.assertEqual(r, "OUI")

    def test_conditional_false(self):
        r = resolve_expression(
            '${flow.v:equals("yes"):then("OUI"):else("NON")}',
            parameters={"v": "no"})
        self.assertEqual(r, "NON")

    def test_nested_expr_in_else(self):
        r = resolve_expression(
            '${flow.x:equals("A"):then("matched"):else(${flow.x:upper})}',
            parameters={"x": "hello"})
        self.assertEqual(r, "HELLO")

    def test_default_missing(self):
        r = resolve_expression('${flow.missing:default("none")}', parameters={})
        self.assertEqual(r, "none")

    def test_generator_uuid(self):
        r = resolve_expression("${:uuid_short}")
        self.assertEqual(len(r), 12)

    def test_mixed_text(self):
        r = resolve_expression("Hi ${flow.name:upper}!", parameters={"name": "bob"})
        self.assertEqual(r, "Hi BOB!")

    def test_multiple_expressions(self):
        r = resolve_expression(
            "${flow.a:upper} and ${flow.b:lower}",
            parameters={"a": "hello", "b": "WORLD"})
        self.assertEqual(r, "HELLO and world")

    def test_replace_in_context(self):
        r = resolve_expression(
            '${flow.greeting:replace("hello","bonjour")}',
            parameters={"greeting": "hello world"})
        self.assertEqual(r, "bonjour world")

    def test_split_index(self):
        r = resolve_expression(
            '${flow.csv:split(","):index(1)}',
            parameters={"csv": "a,b,c"})
        self.assertEqual(r, "b")

    def test_user_example_case1(self):
        """User's exact test case 1: mavar=mavar, plop=plip → OUI Et plop: plip"""
        r = resolve_expression(
            '${flow.mavar:upper:equals("MAVAR"):then("OUI"):else(${flow.mavar:upper})} Et plop: ${flow.plop}',
            parameters={"mavar": "mavar", "plop": "plip"})
        self.assertEqual(r, "OUI Et plop: plip")

    def test_user_example_case2(self):
        """User's exact test case 2: mavar=zob, plop=tutu → ZOB Et plop: tutu"""
        r = resolve_expression(
            '${flow.mavar:upper:equals("MAVAR"):then("OUI"):else(${flow.mavar:upper})} Et plop: ${flow.plop}',
            parameters={"mavar": "zob", "plop": "tutu"})
        self.assertEqual(r, "ZOB Et plop: tutu")


if __name__ == "__main__":
    unittest.main()
