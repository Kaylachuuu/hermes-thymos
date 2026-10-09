"""Make this folder importable as the package `thymos`, whatever the folder is called.
Used by the tests; Hermes loads the package itself.

Development scripts live in scripts/ and tests/, never in the plugin's top folder:
Hermes executes every .py file it finds there when it loads the plugin."""
import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The Hermes source, for the tests that load the plugin the way Hermes does.
for candidate in (os.environ.get("HERMES_SRC"), ROOT.parent / "hermes-agent"):
    if candidate and (Path(candidate) / "hermes_cli" / "plugins.py").exists():
        if str(candidate) not in sys.path:
            sys.path.append(str(candidate))
        break

if "thymos" not in sys.modules:
    spec = importlib.util.spec_from_file_location("thymos", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["thymos"] = module
    spec.loader.exec_module(module)
