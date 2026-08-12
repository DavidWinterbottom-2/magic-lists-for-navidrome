"""Tests for recipe loading and placeholder substitution.

A recipe is the versioned prompt behind a playlist type, so a substitution bug
is invisible until a playlist comes back wrong. These cover the two passes
(`{{MATH:...}}` evaluation, then placeholder replacement), the registry
indirection that makes a version swap a one-line change, and the failure modes.

Recipes are written to a temp directory so the tests don't couple to the
committed recipe content.

Run from the repo root:
    python -m unittest tests.test_recipe_manager
    python -m pytest tests/test_recipe_manager.py
"""

import json
import tempfile
import unittest
from pathlib import Path

from backend.recipe_manager import RecipeManager

RECIPE = {
    "recipe_id": "Test_v1_001",
    "name": "Test recipe",
    "user_parameters": {"seed_name": "{{RADIO_SEED}}"},
    "llm_config": {"temperature": 0.7, "max_output_tokens": 16000},
    "model_instructions": (
        "Build a station from {{RADIO_SEED}} with {{DESIRED_TRACK_COUNT}} tracks. "
        "No more than {{MATH:max(2,ceil(DESIRED_TRACK_COUNT*0.2))}} per artist."
    ),
    "global_strategy": {"final_track_count": "{{DESIRED_TRACK_COUNT}}"},
    "processing_steps": [
        {"step_id": 1, "target_track_count": "{{MATH:ceil(DESIRED_TRACK_COUNT*0.4)}}"}
    ],
}


class RecipeDirMixin:
    """Writes a registry + recipe into a temp dir for each test."""

    def make_manager(self, registry=None, recipes=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "registry.json").write_text(json.dumps(registry if registry is not None else {"radio": "test.json"}))
        for filename, body in (recipes or {"test.json": RECIPE}).items():
            (root / filename).write_text(body if isinstance(body, str) else json.dumps(body))
        return RecipeManager(str(root))


class MathEvaluationTests(unittest.TestCase):
    """Proportional rules are expressed once and scale with playlist length."""

    def setUp(self):
        self.manager = RecipeManager("recipes")

    def _eval(self, expression, num_tracks=25):
        return self.manager._evaluate_math_expressions(expression, {"num_tracks": num_tracks})

    def test_the_track_count_is_substituted_into_the_expression(self):
        self.assertEqual(self._eval("{{MATH:DESIRED_TRACK_COUNT*2}}", 25), "50")

    def test_ceil_and_floor_are_available(self):
        self.assertEqual(self._eval("{{MATH:ceil(DESIRED_TRACK_COUNT*0.2)}}", 25), "5")
        self.assertEqual(self._eval("{{MATH:floor(DESIRED_TRACK_COUNT*0.2)}}", 24), "4")

    def test_min_and_max_clamp_small_playlists(self):
        # The real per-artist rule: never fewer than 2, even on a tiny station.
        self.assertEqual(self._eval("{{MATH:max(2,ceil(DESIRED_TRACK_COUNT*0.2))}}", 5), "2")
        self.assertEqual(self._eval("{{MATH:max(2,ceil(DESIRED_TRACK_COUNT*0.2))}}", 50), "10")

    def test_whole_floats_are_rendered_as_integers(self):
        # "12.0 tracks" in a prompt invites the model to hedge.
        self.assertEqual(self._eval("{{MATH:DESIRED_TRACK_COUNT*0.5}}", 24), "12")

    def test_several_expressions_in_one_string_are_all_evaluated(self):
        result = self._eval("{{MATH:DESIRED_TRACK_COUNT*2}} and {{MATH:DESIRED_TRACK_COUNT+1}}", 10)
        self.assertEqual(result, "20 and 11")

    def test_the_default_track_count_is_used_when_none_is_supplied(self):
        self.assertEqual(
            self.manager._evaluate_math_expressions("{{MATH:DESIRED_TRACK_COUNT}}", {}), "25"
        )

    def test_a_broken_expression_is_left_intact_rather_than_crashing(self):
        # A malformed recipe should degrade to a slightly odd prompt, not a 500.
        self.assertEqual(self._eval("{{MATH:this is not maths}}"), "{{MATH:this is not maths}}")

    def test_expressions_are_evaluated_inside_nested_structures(self):
        recipe = {"steps": [{"target": "{{MATH:DESIRED_TRACK_COUNT*0.4}}"}], "n": 5, "on": True}
        result = self.manager._evaluate_math_expressions(recipe, {"num_tracks": 10})
        self.assertEqual(result["steps"][0]["target"], "4")
        # Non-strings pass through untouched.
        self.assertEqual(result["n"], 5)
        self.assertIs(result["on"], True)


class RecursiveReplaceTests(unittest.TestCase):
    def setUp(self):
        self.manager = RecipeManager("recipes")

    def test_placeholders_are_replaced_at_every_depth(self):
        obj = {"a": "{{X}}", "b": ["{{X}}", {"c": "{{X}}"}]}
        result = self.manager._recursive_replace(obj, {"{{X}}": "done"})
        self.assertEqual(result, {"a": "done", "b": ["done", {"c": "done"}]})

    def test_non_string_values_are_preserved(self):
        obj = {"n": 3, "f": 0.5, "flag": False, "nothing": None}
        self.assertEqual(self.manager._recursive_replace(obj, {"{{X}}": "y"}), obj)

    def test_an_unknown_placeholder_is_left_alone(self):
        self.assertEqual(self.manager._recursive_replace("{{Y}}", {"{{X}}": "y"}), "{{Y}}")


class RegistryTests(RecipeDirMixin, unittest.TestCase):
    def test_a_playlist_type_resolves_through_the_registry(self):
        manager = self.make_manager()
        self.assertEqual(manager.get_recipe("radio")["recipe_id"], "Test_v1_001")

    def test_swapping_the_registry_entry_swaps_the_recipe(self):
        # This indirection is the rollback mechanism: one line, no prompt edits.
        manager = self.make_manager(
            registry={"radio": "v2.json"},
            recipes={"test.json": RECIPE, "v2.json": {**RECIPE, "recipe_id": "Test_v1_002"}},
        )
        self.assertEqual(manager.get_recipe("radio")["recipe_id"], "Test_v1_002")

    def test_an_unregistered_playlist_type_is_an_error(self):
        with self.assertRaises(Exception) as ctx:
            self.make_manager().get_recipe("nonexistent")
        self.assertIn("No recipe registered", str(ctx.exception))

    def test_a_registry_pointing_at_a_missing_file_is_an_error(self):
        with self.assertRaises(Exception) as ctx:
            self.make_manager(registry={"radio": "gone.json"}).get_recipe("radio")
        self.assertIn("Recipe file not found", str(ctx.exception))

    def test_malformed_recipe_json_is_an_error(self):
        manager = self.make_manager(recipes={"test.json": "{ not json"})
        with self.assertRaises(Exception) as ctx:
            manager.get_recipe("radio")
        self.assertIn("Invalid JSON", str(ctx.exception))

    def test_a_missing_registry_is_an_error(self):
        with self.assertRaises(Exception) as ctx:
            RecipeManager("/nonexistent/recipes")._load_registry()
        self.assertIn("registry not found", str(ctx.exception))

    def test_recipes_are_cached_after_first_read(self):
        manager = self.make_manager()
        manager.get_recipe("radio")
        self.assertIn("test.json", manager._recipe_cache)

    def test_clearing_the_cache_forces_a_reread(self):
        manager = self.make_manager()
        manager.get_recipe("radio")
        manager.clear_cache()
        self.assertEqual(manager._recipe_cache, {})
        self.assertIsNone(manager._registry_cache)


class ApplyRecipeTests(RecipeDirMixin, unittest.TestCase):
    def test_both_passes_run_over_the_prompt(self):
        manager = self.make_manager()
        applied = manager.apply_recipe("radio", {"radio_seed": "Alpha", "num_tracks": 25})
        instructions = applied["model_instructions"]

        self.assertIn("Build a station from Alpha with 25 tracks", instructions)
        self.assertIn("No more than 5 per artist", instructions)
        self.assertNotIn("{{", instructions)

    def test_substitution_reaches_nested_strategy_and_steps(self):
        manager = self.make_manager()
        applied = manager.apply_recipe("radio", {"radio_seed": "Alpha", "num_tracks": 50})
        self.assertEqual(applied["global_strategy"]["final_track_count"], "50")
        self.assertEqual(applied["processing_steps"][0]["target_track_count"], "20")

    def test_track_data_is_attached_for_the_model(self):
        manager = self.make_manager()
        applied = manager.apply_recipe("radio", {"num_tracks": 25, "tracks_data": [{"id": "t1"}]})
        self.assertEqual(applied["tracks_data"], [{"id": "t1"}])

    def test_the_other_seed_types_have_their_own_placeholders(self):
        recipe = {**RECIPE, "model_instructions": "{{TARGET_ARTIST}} / {{TARGET_GENRE}}"}
        manager = self.make_manager(recipes={"test.json": recipe})
        applied = manager.apply_recipe("radio", {"artists": "Alpha", "genre": "Shoegaze"})
        self.assertEqual(applied["model_instructions"], "Alpha / Shoegaze")

    def test_the_stored_recipe_is_not_mutated_by_substitution(self):
        # apply_recipe runs per request off a cached recipe; mutating it would
        # leak one listener's seed into the next build.
        manager = self.make_manager()
        manager.apply_recipe("radio", {"radio_seed": "Alpha", "num_tracks": 25})
        self.assertIn("{{RADIO_SEED}}", manager.get_recipe("radio")["model_instructions"])


if __name__ == "__main__":
    unittest.main()
