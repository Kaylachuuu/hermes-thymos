"""Makes the repository root importable as the package ``thymos``, the name Hermes gives it.
(The folder is called hermes-thymos, which Python cannot import by name.)"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

if "thymos" not in sys.modules:
    spec = importlib.util.spec_from_file_location("thymos", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["thymos"] = module
    spec.loader.exec_module(module)
