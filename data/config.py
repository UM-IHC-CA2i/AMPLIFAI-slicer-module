import json
import os


def config_path():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "config.json"))


def load_config():
    path = config_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def resolve_config_path(value, config_dir=None):
    if not value:
        return None
    path = os.path.expanduser(str(value))
    if not os.path.isabs(path):
        base = config_dir or os.path.dirname(config_path())
        path = os.path.abspath(os.path.join(base, path))
    return path


def get_config_value(key, default=None, resolve_path=False):
    config = load_config()
    value = config.get(key, default)
    if resolve_path:
        return resolve_config_path(value)
    return value
