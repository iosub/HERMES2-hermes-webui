from __future__ import annotations

import copy
import shutil
from datetime import datetime

import yaml


class ConfigManager:
    """Manages reading, writing, and merging the Hermes YAML config."""

    def __init__(self, *, config_path_getter, backup_dir_getter, secret_patterns):
        object.__setattr__(self, "_config", {})
        object.__setattr__(self, "_config_mtime_ns", None)
        object.__setattr__(self, "_manual_override", False)
        object.__setattr__(self, "_setting_from_disk", False)
        object.__setattr__(self, "_config_path_getter", config_path_getter)
        object.__setattr__(self, "_backup_dir_getter", backup_dir_getter)
        object.__setattr__(self, "_secret_patterns", secret_patterns)
        self.load()

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)
        if name == "_config" and not getattr(self, "_setting_from_disk", False):
            object.__setattr__(self, "_manual_override", True)
            object.__setattr__(self, "_config_mtime_ns", self._config_file_mtime_ns())

    def _config_file_mtime_ns(self):
        config_path = self._config_path_getter()
        try:
            return config_path.stat().st_mtime_ns
        except FileNotFoundError:
            return None
        except OSError:
            return None

    def _replace_config_from_disk(self, data):
        object.__setattr__(self, "_setting_from_disk", True)
        try:
            object.__setattr__(self, "_config", data)
        finally:
            object.__setattr__(self, "_setting_from_disk", False)
        object.__setattr__(self, "_manual_override", False)
        object.__setattr__(self, "_config_mtime_ns", self._config_file_mtime_ns())

    def load_if_changed(self):
        if self._manual_override:
            return
        current_mtime_ns = self._config_file_mtime_ns()
        if current_mtime_ns != self._config_mtime_ns:
            self.load()

    def load(self):
        config_path = self._config_path_getter()
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as fh:
                    self._replace_config_from_disk(yaml.safe_load(fh) or {})
            except Exception as exc:
                self._replace_config_from_disk({})
                print(f"[ConfigManager] Failed to load config: {exc}")
        else:
            self._replace_config_from_disk({})

    def save(self):
        config_path = self._config_path_getter()
        backup_dir = self._backup_dir_getter()
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = backup_dir / f"config_{ts}.yaml"
        if config_path.exists():
            shutil.copy2(config_path, backup)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as fh:
            yaml.dump(
                self._config,
                fh,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )
        object.__setattr__(self, "_manual_override", False)
        object.__setattr__(self, "_config_mtime_ns", self._config_file_mtime_ns())

    def get(self, section=None):
        self.load_if_changed()
        if section is None:
            return self.mask_secrets(copy.deepcopy(self._config))
        data = copy.deepcopy(self._config.get(section, {}))
        if isinstance(data, dict):
            return self.mask_secrets(data)
        return data

    def get_raw(self, section=None):
        self.load_if_changed()
        if section is None:
            return copy.deepcopy(self._config)
        return copy.deepcopy(self._config.get(section, {}))

    def set(self, section, data):
        self._config[section] = data
        self.save()

    def update(self, section, data):
        current = self._config.get(section, {})
        if isinstance(current, dict) and isinstance(data, dict):
            self._config[section] = self.deep_merge(current, data)
        else:
            self._config[section] = data
        self.save()

    def delete_section(self, section):
        self._config.pop(section, None)
        self.save()

    @staticmethod
    def deep_merge(base: dict, update: dict) -> dict:
        result = copy.deepcopy(base)
        for key, value in update.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = ConfigManager.deep_merge(result[key], value)
            else:
                result[key] = copy.deepcopy(value)
        return result

    def mask_secrets(self, data, parent_key=""):
        if isinstance(data, dict):
            return {key: self.mask_secrets(value, key) for key, value in data.items()}
        if isinstance(data, list):
            return [self.mask_secrets(value, parent_key) for value in data]
        if isinstance(data, str) and self._secret_patterns.search(parent_key) and len(data) > 4:
            return "\u2022" * (len(data) - 4) + data[-4:]
        return data