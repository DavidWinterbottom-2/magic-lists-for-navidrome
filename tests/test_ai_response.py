"""Tests for salvaging JSON out of a model reply.

The regression that prompted this: a reply containing a nested object was
truncated at the first closing brace by a non-greedy regex, so it failed to
parse and the caller silently fell back to play-count ordering — reporting "AI
service was unavailable", which is not what happened.

Run from the repo root:
    python -m unittest tests.test_ai_response
    python -m pytest tests/test_ai_response.py
"""

import json
import unittest

from backend.ai_response import extract_json_payload, find_json_object


def parsed(content):
    return json.loads(extract_json_payload(content))


class NestedStructureTests(unittest.TestCase):
    """The bug: nesting used to truncate the payload."""

    NESTED = json.dumps({
        "track_ids": [0, 1, 2],
        "reasoning": "A quiet run.",
        "album_suggestions": [
            {"artist": "Delta Static", "album": "Reverb Country", "year": 2011},
            {"artist": "Epsilon Drift", "album": "Long Way Down", "year": 2016},
        ],
    })

    def test_a_reply_with_nested_objects_survives(self):
        result = parsed(self.NESTED)
        self.assertEqual(result["track_ids"], [0, 1, 2])
        self.assertEqual(len(result["album_suggestions"]), 2)

    def test_deeply_nested_values_survive(self):
        content = json.dumps({
            "track_ids": [1],
            "reasoning": "x",
            "meta": {"scores": {"style": {"weight": 0.5}}},
        })
        self.assertEqual(parsed(content)["meta"]["scores"]["style"]["weight"], 0.5)


class ExtractionTests(unittest.TestCase):
    def test_a_bare_object_is_returned_whole(self):
        self.assertEqual(parsed('{"track_ids": [1, 2], "reasoning": "ok"}')["track_ids"], [1, 2])

    def test_json_code_fences_are_stripped(self):
        content = '```json\n{"track_ids": [3], "reasoning": "fenced"}\n```'
        self.assertEqual(parsed(content)["track_ids"], [3])

    def test_plain_code_fences_are_stripped(self):
        self.assertEqual(parsed('```\n{"track_ids": [4], "reasoning": "x"}\n```')["track_ids"], [4])

    def test_preamble_and_trailing_prose_are_discarded(self):
        content = ('Sure! Here is the playlist you asked for:\n'
                   '{"track_ids": [5, 6], "reasoning": "picked"}\n'
                   'Let me know if you want changes.')
        self.assertEqual(parsed(content)["track_ids"], [5, 6])

    def test_the_curation_object_wins_over_an_earlier_unrelated_one(self):
        content = '{"note": "thinking"} {"track_ids": [7], "reasoning": "real"}'
        self.assertEqual(parsed(content)["track_ids"], [7])

    def test_a_brace_inside_a_string_does_not_end_the_object(self):
        content = '{"track_ids": [8], "reasoning": "ends with a brace } here"}'
        result = parsed(content)
        self.assertEqual(result["track_ids"], [8])
        self.assertIn("}", result["reasoning"])

    def test_an_escaped_quote_inside_a_string_is_handled(self):
        content = r'{"track_ids": [9], "reasoning": "a \"quoted\" phrase"}'
        self.assertEqual(parsed(content)["track_ids"], [9])

    def test_the_legacy_bare_array_format_still_parses(self):
        self.assertEqual(parsed("Here you go: [1, 2, 3]"), [1, 2, 3])

    def test_trailing_commas_are_tolerated(self):
        self.assertEqual(parsed('{"track_ids": [1, 2,], "reasoning": "x",}')["track_ids"], [1, 2])

    def test_line_comments_are_dropped(self):
        content = '{\n"track_ids": [1], // the best one\n"reasoning": "x"\n}'
        self.assertEqual(parsed(content)["track_ids"], [1])

    def test_urls_are_not_mistaken_for_comments(self):
        content = '{"track_ids": [1], "reasoning": "see https://example.com/a"}'
        self.assertIn("https://example.com/a", parsed(content)["reasoning"])


class FailureModeTests(unittest.TestCase):
    def test_an_unclosed_object_is_not_mistaken_for_a_complete_one(self):
        self.assertIsNone(find_json_object('{"track_ids": [1], "reasoning": "cut off'))

    def test_content_with_no_json_is_passed_through_to_fail_loudly(self):
        # Better a json.loads error naming the real content than a silent guess.
        with self.assertRaises(json.JSONDecodeError):
            parsed("I'm afraid I can't do that.")


if __name__ == "__main__":
    unittest.main()
