import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

MANAGER = Path(__file__).with_name("butterproxy_config_manager.py")
spec = importlib.util.spec_from_file_location("butter_manager", MANAGER)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

class ModelLimitsTests(unittest.TestCase):
    def config(self):
        return {"server": {"address": ":8088"}, "providers": {"venice": {"base_url": "https://api.venice.ai/api/v1", "keys": [{"key": "none", "weight": 1}]}},
                "routing": {"default_provider": "venice", "models": {"venice/raw": {"provider": "venice", "model": "raw", "context_length": 128000, "max_output_tokens": 8192, "extra_body": {"venice_parameters": {"include_venice_system_prompt": False}}}}}}

    def test_validation(self):
        for value in [0, -1, 1.5, True, None, "128000"]:
            cfg = self.config()
            cfg["routing"]["models"]["venice/raw"]["context_length"] = value
            with self.assertRaises(module.ButterConfigError):
                module.validate_butter_config(cfg)
        cfg = self.config()
        cfg["routing"]["models"]["venice/raw"]["max_output_tokens"] = 999999
        with self.assertRaises(module.ButterConfigError):
            module.validate_butter_config(cfg)

    def test_route_edits_and_clear(self):
        cfg = self.config()
        original = copy.deepcopy(cfg["routing"]["models"]["venice/raw"])
        module.apply_route(cfg, provider="venice", model="raw", label="New")
        route = cfg["routing"]["models"]["venice/raw"]
        self.assertEqual(route["context_length"], 128000)
        self.assertEqual(route["max_output_tokens"], 8192)
        self.assertEqual(route["extra_body"], original["extra_body"])
        module.apply_route(cfg, provider="venice", model="raw", clear_context_length=True)
        self.assertNotIn("context_length", cfg["routing"]["models"]["venice/raw"])

    def test_provider_edit_sync_and_serialization(self):
        cfg = self.config()
        original = copy.deepcopy(cfg["routing"]["models"]["venice/raw"])
        module.apply_provider(cfg, name="venice", base_url="https://api.venice.ai/api/v1", models=["raw"])
        route = cfg["routing"]["models"]["venice/raw"]
        for field in ["context_length", "max_output_tokens", "extra_body"]:
            self.assertEqual(route[field], original[field])
        module.sync_catalogs(cfg, {}, fetcher=lambda _url, _key: ["raw", "new"])
        restored = module.load_config_text(module.dump_config(cfg))
        self.assertEqual(restored["routing"]["models"]["venice/raw"], route)
        rows = module.list_routes(restored)
        self.assertEqual(next(row for row in rows if row["model"] == "raw")["context_length"], 128000)

    def test_gui_limit_helper_is_atomic(self):
        cfg = self.config()
        before = copy.deepcopy(cfg)
        with self.assertRaises(module.ButterConfigError):
            module.set_route_limits(cfg, "venice/raw", 100, 200)
        self.assertEqual(cfg, before)
        module.set_route_limits(cfg, "venice/raw", None, None)
        route = cfg["routing"]["models"]["venice/raw"]
        self.assertNotIn("context_length", route)
        self.assertIn("extra_body", route)

    def test_cli_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.yaml"
            path.write_text(module.dump_config(self.config()))
            subprocess.run([sys.executable, str(MANAGER), "add-route", "--config", str(path), "--provider", "venice", "--model", "raw", "--context-length", "1048576", "--max-output-tokens", "65536"], check=True, capture_output=True)
            output = subprocess.check_output([sys.executable, str(MANAGER), "list-routes", "--config", str(path)], text=True)
            self.assertIn("1048576", output)
            cfg = module.load_config_file(path)
            self.assertEqual(cfg["routing"]["models"]["venice/raw"]["max_output_tokens"], 65536)

if __name__ == "__main__":
    unittest.main()
