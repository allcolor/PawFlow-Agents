"""Tests for AAAK Dialect — compressed symbolic memory language.

Tests cover:
- compress (basic text, with metadata wing/room)
- Entity detection (known entities, auto-coded from text)
- Topic extraction (stop words filtering)
- Key sentence extraction (decision keywords boost)
- Emotion detection (keyword -> code mapping)
- Flag detection (DECISION, TECHNICAL, ORIGIN, etc.)
- Output format verification (ZID:entities|topics|"key_quote"|emotions|flags)
"""

import unittest

from core.aaak_dialect import Dialect, EMOTION_CODES, _EMOTION_SIGNALS, _FLAG_SIGNALS


class TestCompress(unittest.TestCase):

    def test_basic_text(self):
        d = Dialect()
        result = d.compress("We decided to use GraphQL instead of REST for the API.")
        assert isinstance(result, str)
        assert len(result) > 0
        # Should contain a zettel line with 0: prefix
        assert "0:" in result

    def test_with_metadata(self):
        d = Dialect()
        result = d.compress(
            "Important architecture decision about databases",
            metadata={"wing": "tech", "room": "arch", "date": "2024-01-15",
                      "source_file": "notes.txt"},
        )
        lines = result.split("\n")
        # First line should be header with wing|room|date|source
        assert lines[0].startswith("tech|arch|2024-01-15|")
        # Second line is the content
        assert "0:" in lines[1]

    def test_no_metadata_no_header(self):
        d = Dialect()
        result = d.compress("Simple text without metadata")
        lines = result.strip().split("\n")
        # Should be single line (no header)
        assert len(lines) == 1
        assert "0:" in lines[0]

    def test_compression_ratio(self):
        d = Dialect()
        long_text = ("We had a long meeting about the architecture of the new system. "
                     "The team decided to use GraphQL instead of REST for better flexibility. "
                     "Alice suggested we deploy on Kubernetes. Bob agreed but raised concerns "
                     "about the learning curve. We also discussed database options.")
        compressed = d.compress(long_text)
        assert len(compressed) < len(long_text)


class TestEntityDetection(unittest.TestCase):

    def test_known_entities(self):
        d = Dialect(entities={"Alice": "ALC", "Bob": "BOB"})
        found = d._detect_entities_in_text("Alice and Bob discussed the plan.")
        assert "ALC" in found
        assert "BOB" in found

    def test_auto_code_unknown(self):
        d = Dialect()
        found = d._detect_entities_in_text("The project was led by Marcus in the team.")
        # Marcus should be auto-coded from its first 3 chars
        assert "MAR" in found

    def test_no_entities(self):
        d = Dialect()
        found = d._detect_entities_in_text("no proper nouns here at all")
        assert found == []

    def test_encode_entity_known(self):
        d = Dialect(entities={"Alice": "ALC"})
        assert d.encode_entity("Alice") == "ALC"

    def test_encode_entity_case_insensitive(self):
        d = Dialect(entities={"Alice": "ALC"})
        assert d.encode_entity("alice") == "ALC"

    def test_encode_entity_auto_code(self):
        d = Dialect()
        assert d.encode_entity("Marcus") == "MAR"

    def test_skip_names(self):
        d = Dialect(skip_names=["Gandalf"])
        result = d.encode_entity("Gandalf")
        assert result is None

    def test_max_three_entities(self):
        d = Dialect()
        text = "We discussed with Alice and Bob and Charlie and Dave and Eve."
        found = d._detect_entities_in_text(text)
        assert len(found) <= 3


class TestTopicExtraction(unittest.TestCase):

    def test_basic_topics(self):
        d = Dialect()
        topics = d._extract_topics("GraphQL provides better flexibility than REST APIs")
        assert len(topics) > 0
        # Should contain meaningful words, not stop words
        for t in topics:
            assert t not in {"the", "a", "is", "and", "for", "with"}

    def test_stop_words_filtered(self):
        d = Dialect()
        topics = d._extract_topics("The quick brown fox jumps over the lazy dog")
        # "the" and "over" should be filtered
        for t in topics:
            assert t != "the"
            assert t != "over"

    def test_proper_nouns_boosted(self):
        d = Dialect()
        topics = d._extract_topics(
            "Python is great. python is flexible. python works well.")
        # "python" appears as both capitalized and lowercase, should rank high
        assert "python" in topics

    def test_max_topics(self):
        d = Dialect()
        topics = d._extract_topics("alpha beta gamma delta epsilon zeta", max_topics=2)
        assert len(topics) <= 2

    def test_empty_text(self):
        d = Dialect()
        topics = d._extract_topics("")
        assert topics == []


class TestKeySentenceExtraction(unittest.TestCase):

    def test_decision_keywords_boost(self):
        d = Dialect()
        text = ("The weather was nice today. "
                "We decided to switch from REST to GraphQL because of flexibility. "
                "The office has good coffee.")
        sentence = d._extract_key_sentence(text)
        # The sentence with "decided" and "because" should win
        assert "decided" in sentence.lower() or "switch" in sentence.lower()

    def test_short_sentences_preferred(self):
        d = Dialect()
        text = ("This is key. "
                "This is a very long sentence that goes on and on and on "
                "about nothing in particular and contains no decision words whatsoever "
                "and just keeps going without any real substance or meaning.")
        sentence = d._extract_key_sentence(text)
        # Short sentence should score higher (all else equal)
        assert len(sentence) < 100

    def test_truncation_at_55(self):
        d = Dialect()
        text = ("We discovered that the implementation required significantly more "
                "complex infrastructure than originally anticipated in the plan")
        sentence = d._extract_key_sentence(text)
        assert len(sentence) <= 55

    def test_empty_text(self):
        d = Dialect()
        assert d._extract_key_sentence("") == ""

    def test_short_sentences_skipped(self):
        d = Dialect()
        # Sentences <= 10 chars are skipped
        assert d._extract_key_sentence("Hi. Ok. No.") == ""


class TestEmotionDetection(unittest.TestCase):

    def test_basic_emotions(self):
        d = Dialect()
        emotions = d._detect_emotions("I'm excited about this and also a bit worried")
        assert "excite" in emotions
        assert "anx" in emotions

    def test_multiple_emotions_max_three(self):
        d = Dialect()
        emotions = d._detect_emotions(
            "I'm excited and worried and happy and sad and confused")
        assert len(emotions) <= 3

    def test_no_emotions(self):
        d = Dialect()
        emotions = d._detect_emotions("The database schema has three tables")
        # Might detect nothing if no signal words present
        assert isinstance(emotions, list)

    def test_known_mappings(self):
        d = Dialect()
        # Test specific keyword -> code
        assert "love" in d._detect_emotions("I love this project")
        assert "fear" in d._detect_emotions("I fear the worst")
        assert "joy" in d._detect_emotions("I'm so happy about this")
        assert "grief" in d._detect_emotions("This makes me sad")

    def test_encode_emotions_from_list(self):
        d = Dialect()
        encoded = d.encode_emotions(["joy", "fear", "trust"])
        assert encoded == "joy+fear+trust"

    def test_encode_emotions_unknown_truncated(self):
        d = Dialect()
        encoded = d.encode_emotions(["nonexistent_emotion"])
        # Unknown emotions get first 4 chars
        assert encoded == "none"

    def test_encode_emotions_dedup(self):
        d = Dialect()
        encoded = d.encode_emotions(["joy", "joy", "joy"])
        assert encoded == "joy"


class TestFlagDetection(unittest.TestCase):

    def test_decision_flag(self):
        d = Dialect()
        flags = d._detect_flags("We decided to use the new framework")
        assert "DECISION" in flags

    def test_technical_flag(self):
        d = Dialect()
        flags = d._detect_flags("The API uses a new database architecture")
        assert "TECHNICAL" in flags

    def test_origin_flag(self):
        d = Dialect()
        flags = d._detect_flags("The company was founded in 2020")
        assert "ORIGIN" in flags

    def test_core_flag(self):
        d = Dialect()
        flags = d._detect_flags("This is a fundamental principle of our approach")
        assert "CORE" in flags

    def test_pivot_flag(self):
        d = Dialect()
        flags = d._detect_flags("That was a turning point in my career")
        assert "PIVOT" in flags

    def test_max_three_flags(self):
        d = Dialect()
        # Text with many flag keywords
        flags = d._detect_flags(
            "We decided to create a new core API architecture "
            "that was a turning point founded on fundamental principles")
        assert len(flags) <= 3

    def test_no_flags(self):
        d = Dialect()
        flags = d._detect_flags("The cat sat on the mat")
        assert flags == []


class TestOutputFormat(unittest.TestCase):

    def test_zettel_line_format(self):
        d = Dialect(entities={"Alice": "ALC"})
        result = d.compress(
            "Alice decided to switch from REST to GraphQL because of performance")
        # Content line should have ZID:entities|topics format
        lines = result.strip().split("\n")
        content_line = lines[-1]  # Last line is always the content
        assert content_line.startswith("0:")
        parts = content_line.split("|")
        assert len(parts) >= 2  # At minimum: ZID:entities|topics

    def test_entities_in_output(self):
        d = Dialect(entities={"Alice": "ALC"})
        result = d.compress("Alice worked on the project")
        assert "ALC" in result

    def test_quote_in_output(self):
        d = Dialect()
        result = d.compress(
            "We decided to switch because the old system was too slow and unreliable")
        # Should contain a quoted key sentence
        assert '"' in result

    def test_emotions_in_output(self):
        d = Dialect()
        result = d.compress("I'm so excited and happy about this new approach")
        # Should contain emotion codes
        assert "excite" in result or "joy" in result

    def test_flags_in_output(self):
        d = Dialect()
        result = d.compress("We decided to migrate the API to a new architecture")
        assert "DECISION" in result or "TECHNICAL" in result

    def test_header_with_metadata(self):
        d = Dialect()
        result = d.compress("test text about the architecture",
                            metadata={"wing": "W1", "room": "R1",
                                      "date": "2024-03-15",
                                      "source_file": "notes.txt"})
        lines = result.split("\n")
        assert "W1" in lines[0]
        assert "R1" in lines[0]
        assert "2024-03-15" in lines[0]
        assert "notes" in lines[0]


class TestGetFlags(unittest.TestCase):
    """Test the zettel-based get_flags method (separate from text _detect_flags)."""

    def test_origin_moment(self):
        d = Dialect()
        flags = d.get_flags({"origin_moment": True})
        assert "ORIGIN" in flags

    def test_sensitivity(self):
        d = Dialect()
        flags = d.get_flags({"sensitivity": "MAXIMUM"})
        assert "SENSITIVE" in flags

    def test_core_in_notes(self):
        d = Dialect()
        flags = d.get_flags({"notes": "This is a foundational pillar"})
        assert "CORE" in flags

    def test_genesis_in_notes(self):
        d = Dialect()
        flags = d.get_flags({"notes": "genesis of the project"})
        assert "GENESIS" in flags

    def test_no_flags(self):
        d = Dialect()
        flags = d.get_flags({})
        assert flags == ""


if __name__ == "__main__":
    unittest.main()
