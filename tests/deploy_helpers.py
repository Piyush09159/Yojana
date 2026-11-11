"""Load deploy/*.py scripts (not a package) for tests."""
import importlib.util
from pathlib import Path

DEPLOY = Path(__file__).resolve().parent.parent / 'deploy'


def _load(name):
    spec = importlib.util.spec_from_file_location('deploy_' + name, DEPLOY / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


healthcheck = _load('healthcheck')


def smoke_modules():
    return _load('smoke_imports').MODULES
