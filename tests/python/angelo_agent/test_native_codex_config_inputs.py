# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The sign-in inputs are derived from the fork's auth code, and the derivation fails loudly when that code changes
shape (re-read of 0b6b952594, SHOULD-2: a renamed builder or an unparsed FeatureSpec gave a smaller set, and a new
callee's key was missed). ``scripts/angelo/codex_config_inputs.derive`` on small Rust fixtures. No fork, no git.

The script was codex_config_keys.py, the same module name as the recorded lists beside the tests: in one process the
first import won and this test read the lists (VM CI on batch 10). No module name is shared between the two folders."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/angelo"))
import codex_config_inputs as script  # noqa: E402

AUTH = '''
impl Config {
    pub fn auth_config(&self) -> AuthConfig {
        AuthConfig { backend: self.auth_keyring_backend_kind(), route: self.auth_route_config() }
    }
    fn auth_keyring_backend_kind(&self) -> Kind { kind(self.features.enabled(Feature::SecretAuthStorage)) }
    pub fn auth_route_config(&self) -> Route {
        if self.features.enabled(Feature::RespectSystemProxy) { a() } else { b() }
    }
}
pub fn bootstrap_auth_config(codex_home: &Path, bootstrap_config: &Loaded) -> Result<AuthConfig> {
    let config = &bootstrap_config.config_toml;
    Ok(AuthConfig { store: config.cli_auth_credentials_store.unwrap_or_default(),
                    backend: resolve_kind(bootstrap_config)? })
}
fn resolve_kind(bootstrap_config: &Loaded) -> Result<Kind> { Ok(kind(Feature::SecretAuthStorage)) }
fn unrelated(config: &ConfigToml) -> bool { config.model.is_some() }
'''
SPECS = '''
    FeatureSpec { id: Feature::SecretAuthStorage, key: "secret_auth_storage", stage: Stage::Stable },
    FeatureSpec { id: Feature::RespectSystemProxy, key: "respect_system_proxy", stage: Stage::Stable },
'''


class ModuleNameTests(unittest.TestCase):
    def test_no_module_name_is_both_a_tool_and_a_test_helper(self):
        """Both folders go on sys.path in one process (run_native_suites, host tests): one name, one module."""
        tools = {path.stem for path in (ROOT / "scripts/angelo").glob("*.py")}
        helpers = {path.stem for path in Path(__file__).resolve().parent.glob("*.py")}
        self.assertEqual(sorted(tools & helpers), [])
        self.assertEqual(Path(script.__file__).resolve(), ROOT / "scripts/angelo/codex_config_inputs.py")


class DerivationTests(unittest.TestCase):
    def test_both_builders_and_their_callees_are_read(self):
        inputs, visited = script.derive(AUTH, SPECS)
        self.assertEqual(inputs, ("cli_auth_credentials_store", "features.respect_system_proxy",
                                  "features.secret_auth_storage"))
        self.assertIn("resolve_kind", visited)
        self.assertNotIn("unrelated", visited, "only what the builders reach")

    def test_a_renamed_builder_is_an_error_not_a_smaller_set(self):
        for root in script.ROOTS:
            with self.subTest(root), self.assertRaises(script.DerivationError):
                script.derive(AUTH.replace("fn %s(" % root, "fn %s_v2(" % root), SPECS)

    def test_a_feature_without_a_spec_is_an_error(self):
        with self.assertRaises(script.DerivationError):
            script.derive(AUTH.replace("Feature::RespectSystemProxy", "Feature::NewRoute"), SPECS)
        with self.assertRaises(script.DerivationError):
            script.derive(AUTH, SPECS.replace("id:", "identity:"))   # No spec parses at all.

    def test_a_new_callee_reading_a_new_key_is_found(self):
        grown = AUTH.replace("backend: resolve_kind(bootstrap_config)?",
                             "backend: resolve_kind(bootstrap_config)?, extra: resolve_extra(bootstrap_config)") + (
            "fn resolve_extra(bootstrap_config: &Loaded) -> Extra { let config = &bootstrap_config.config_toml; "
            "config.new_sign_in_key.clone() }\n")
        inputs, _visited = script.derive(grown, SPECS)
        self.assertIn("new_sign_in_key", inputs)


if __name__ == "__main__":
    unittest.main()
