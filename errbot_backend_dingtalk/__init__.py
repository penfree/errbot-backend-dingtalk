from pathlib import Path


__version__ = '0.1.0'


def get_plugin_dir() -> str:
    module_dir = Path(__file__).parent
    return str(module_dir)