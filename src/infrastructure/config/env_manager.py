"""
Environment variable manager.
Responsible for reading and updating the .env file, with fallback to runtime environment variables.
"""
import os
import re
from typing import Dict, List, Optional
from pathlib import Path

from dotenv import dotenv_values


_PLAIN_ENV_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9_./:-]+$")


class EnvManager:
    """Environment variable manager."""

    def __init__(self, env_file: str = ".env"):
        self.env_file = Path(env_file)
        self._ensure_env_file_exists()

    def _ensure_env_file_exists(self):
        """Ensure the .env file exists."""
        if not self.env_file.exists():
            self.env_file.touch()

    def read_env(self) -> Dict[str, str]:
        """Read all environment variables from the .env file."""
        if not self.env_file.exists():
            return {}

        loaded = dotenv_values(self.env_file, encoding="utf-8")
        return {
            key: value
            for key, value in loaded.items()
            if key and value is not None
        }

    def get_value(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Get a single environment variable value, preferring the runtime environment."""
        runtime_value = os.getenv(key)
        if runtime_value is not None:
            return runtime_value

        env_vars = self.read_env()
        return env_vars.get(key, default)

    def update_values(self, updates: Dict[str, str]) -> bool:
        """Batch-update environment variables."""
        return self.apply_changes(updates=updates)

    def apply_changes(
        self,
        updates: Dict[str, str],
        deletions: List[str] | None = None,
    ) -> bool:
        """Batch-update and delete environment variables."""
        try:
            existing_vars = self.read_env()
            existing_vars.update(updates)
            for key in deletions or []:
                existing_vars.pop(key, None)
            return self._write_env(existing_vars)
        except Exception as e:
            print(f"Failed to update environment variables: {e}")
            return False

    def set_value(self, key: str, value: str) -> bool:
        """Set a single environment variable."""
        return self.update_values({key: value})

    def delete_keys(self, keys: List[str]) -> bool:
        """Delete the specified environment variables."""
        try:
            existing_vars = self.read_env()
            for key in keys:
                existing_vars.pop(key, None)
            return self._write_env(existing_vars)
        except Exception as e:
            print(f"Failed to delete environment variables: {e}")
            return False

    def _write_env(self, env_vars: Dict[str, str]) -> bool:
        """Write environment variables to the .env file."""
        try:
            with open(self.env_file, 'w', encoding='utf-8') as f:
                for key, value in env_vars.items():
                    f.write(f"{key}={self._serialize_value(value)}\n")
            return True
        except Exception as e:
            print(f"Failed to write .env file: {e}")
            return False

    def _serialize_value(self, value: str) -> str:
        text = str(value)
        if text == "":
            return ""
        if _PLAIN_ENV_VALUE_PATTERN.fullmatch(text):
            return text
        escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'


# Global singleton instance
env_manager = EnvManager()
