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


LEGACY_RECIPE = {
    "version": "v1.003",
    "description": "A pre-2026 recipe",
    "inputs": ["artist_name"],
    "strategy_notes": {"approach": "play count"},
    "prompt_template": "Build a playlist for {artist_name} with {num_tracks} tracks.",
    "llm_params": {"temperature": 0.7, "max_tokens": 1000},
}


class ValidationTests(RecipeDirMixin, unittest.TestCase):
    """The validator has to recognise both recipe formats.

    The current seven-key format is what every live recipe uses; the legacy
    format survives in recipes/archive/ and apply_recipe still runs it. Checking
    only one shape means reporting every file in the other as broken — which is
    exactly what this validator used to do to all six live recipes.
    """

    def errors_for(self, recipe):
        return self.make_manager(recipes={"test.json": recipe}).validate_recipe("test.json")

    def test_a_valid_current_recipe_has_no_errors(self):
        self.assertEqual(self.errors_for(RECIPE), [])

    def test_a_valid_legacy_recipe_has_no_errors(self):
        self.assertEqual(self.errors_for(LEGACY_RECIPE), [])

    def test_a_missing_current_field_is_reported(self):
        errors = self.errors_for({k: v for k, v in RECIPE.items() if k != "processing_steps"})
        self.assertIn("Missing required field: processing_steps", errors)

    def test_a_missing_legacy_field_is_reported(self):
        errors = self.errors_for({k: v for k, v in LEGACY_RECIPE.items() if k != "strategy_notes"})
        self.assertIn("Missing required field: strategy_notes", errors)

    def test_a_legacy_recipe_is_not_judged_by_current_rules(self):
        # The regression: every legacy field name is absent from a current recipe
        # and vice versa, so cross-checking produces four bogus errors.
        self.assertNotIn("Missing required field: recipe_id", self.errors_for(LEGACY_RECIPE))
        self.assertNotIn("Missing required field: version", self.errors_for(RECIPE))

    def test_an_out_of_range_temperature_is_reported(self):
        errors = self.errors_for({**RECIPE, "llm_config": {"temperature": 3.5}})
        self.assertIn("'temperature' must be a number between 0 and 2", errors)

    def test_a_nonsense_token_budget_is_reported(self):
        errors = self.errors_for({**RECIPE, "llm_config": {"max_output_tokens": 0}})
        self.assertIn("'max_output_tokens' must be a positive integer", errors)

    def test_wrongly_typed_sections_are_reported(self):
        errors = self.errors_for({**RECIPE, "processing_steps": {"not": "a list"}})
        self.assertIn("'processing_steps' must be a list", errors)

    def test_an_unknown_placeholder_is_reported(self):
        # Nothing substitutes {{MOOD}}, so the model would receive it literally —
        # invisible at build time, visible only as a bad playlist.
        errors = self.errors_for({**RECIPE, "model_instructions": "Build for {{MOOD}}"})
        self.assertEqual(len(errors), 1)
        self.assertIn("{{MOOD}}", errors[0])

    def test_known_placeholders_and_math_are_accepted(self):
        instructions = ("{{RADIO_SEED}} {{TARGET_ARTIST}} {{TARGET_GENRE}} "
                        "{{DESIRED_TRACK_COUNT}} {{MATH:ceil(DESIRED_TRACK_COUNT*0.4)}}")
        self.assertEqual(self.errors_for({**RECIPE, "model_instructions": instructions}), [])

    def test_an_unparseable_file_reports_a_load_failure(self):
        errors = self.make_manager(recipes={"test.json": "{ not json"}).validate_recipe("test.json")
        self.assertEqual(len(errors), 1)
        self.assertIn("Failed to load recipe", errors[0])


class RecipeListingTests(RecipeDirMixin, unittest.TestCase):
    def test_current_recipe_metadata_is_reported(self):
        info = self.make_manager().list_available_recipes()["radio"]
        self.assertEqual(info["format"], "current")
        self.assertEqual(info["recipe_id"], "Test_v1_001")
        self.assertEqual(info["name"], "Test recipe")
        self.assertTrue(info["uses_llm"])

    def test_legacy_recipe_metadata_is_reported(self):
        manager = self.make_manager(recipes={"test.json": LEGACY_RECIPE})
        info = manager.list_available_recipes()["radio"]
        self.assertEqual(info["format"], "legacy")
        self.assertEqual(info["recipe_id"], "v1.003")
        self.assertTrue(info["uses_llm"])

    def test_a_broken_recipe_is_reported_without_taking_the_listing_down(self):
        manager = self.make_manager(registry={"radio": "gone.json"}, recipes={"test.json": RECIPE})
        self.assertIn("error", manager.list_available_recipes()["radio"])


if __name__ == "__main__":
    unittest.main()
