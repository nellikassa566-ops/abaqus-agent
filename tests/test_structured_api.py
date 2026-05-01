import os
import tempfile
import unittest

from abaqus_mcp_tools import (
    build_command_payload,
    instantiate_template,
    list_template_metadata,
    parse_job_diagnostics_text,
    validate_model_spec,
)


class StructuredApiTests(unittest.TestCase):
    def test_validate_model_spec_accepts_minimal_block_model(self):
        spec = {
            "model_name": "SpecDemo",
            "parts": [
                {
                    "name": "Block",
                    "type": "block",
                    "dimensions": [1.0, 2.0, 3.0],
                    "origin": [0.0, 0.0, 0.0],
                }
            ],
            "materials": [
                {"name": "Steel", "elastic": {"youngs_modulus": 210000.0, "poisson_ratio": 0.3}}
            ],
            "sections": [{"name": "BlockSection", "material": "Steel", "parts": ["Block"]}],
            "steps": [{"name": "Load", "type": "static", "previous": "Initial"}],
            "mesh": {"global_size": 0.5},
            "jobs": [{"name": "Job-SpecDemo", "model": "SpecDemo"}],
        }

        result = validate_model_spec(spec)

        self.assertTrue(result["valid"], result)
        self.assertEqual([], result["errors"])
        self.assertEqual("SpecDemo", result["normalized"]["model_name"])
        self.assertEqual("C3D8R", result["normalized"]["mesh"]["element_type"])

    def test_validate_model_spec_rejects_missing_material_references(self):
        spec = {
            "model_name": "BadSpec",
            "parts": [{"name": "Block", "type": "block", "dimensions": [1, 1, 1]}],
            "sections": [{"name": "BadSection", "material": "Missing", "parts": ["Block"]}],
        }

        result = validate_model_spec(spec)

        self.assertFalse(result["valid"])
        self.assertIn("section material not found: BadSection -> Missing", result["errors"])

    def test_build_command_payload_defaults_to_dry_run(self):
        spec = {
            "model_name": "PayloadDemo",
            "parts": [{"name": "Block", "type": "block", "dimensions": [1, 1, 1]}],
        }

        payload = build_command_payload("build_model_from_spec", spec=spec)

        self.assertEqual("build_model_from_spec", payload["type"])
        self.assertTrue(payload["dry_run"])
        self.assertTrue(payload["validation"]["valid"])

    def test_templates_are_discoverable_and_instantiate_to_valid_specs(self):
        templates = list_template_metadata()
        template_ids = {item["id"] for item in templates}

        self.assertTrue({"tsv_thermal_cycle", "bga_thermal_cycle"}.issubset(template_ids))

        tsv = instantiate_template("tsv_thermal_cycle", {"model_name": "TSVTest"})
        bga = instantiate_template("bga_thermal_cycle", {"model_name": "BGATest"})

        self.assertTrue(validate_model_spec(tsv)["valid"])
        self.assertTrue(validate_model_spec(bga)["valid"])
        self.assertEqual("TSVTest", tsv["model_name"])
        self.assertEqual("BGATest", bga["model_name"])

    def test_parse_job_diagnostics_classifies_common_solver_messages(self):
        msg_text = """
        ***ERROR: TOO MANY ATTEMPTS MADE FOR THIS INCREMENT
        ***WARNING: THE SYSTEM MATRIX HAS 3 NEGATIVE EIGENVALUES.
        ***WARNING: 2 elements are distorted.
        THE ANALYSIS HAS BEEN COMPLETED
        """

        diagnostics = parse_job_diagnostics_text(msg_text)
        codes = {item["code"] for item in diagnostics["issues"]}

        self.assertFalse(diagnostics["ok"])
        self.assertIn("increment_cutback_failure", codes)
        self.assertIn("negative_eigenvalues", codes)
        self.assertIn("distorted_elements", codes)


if __name__ == "__main__":
    unittest.main()
