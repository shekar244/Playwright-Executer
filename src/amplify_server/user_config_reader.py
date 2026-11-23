"""User-scoped configuration reader for multi-tenant support."""
import json
from pathlib import Path
from flask import g
from amplify_server.config_reader import ConfigReader, fresh_defaults, _read_json
from amplify_server.storage import get_user_config_path

class UserConfigReader(ConfigReader):
    """ConfigReader that uses user-scoped workspace paths."""

    def __init__(self, user_id: int = None):
        self.user_id = user_id
        if not user_id and hasattr(g, "user"):
            self.user_id = g.user.id

    def load(self) -> dict:
        """Load config from user's workspace."""
        config = fresh_defaults()

        if not self.user_id:
            return config

        user_config_path = get_user_config_path(self.user_id)

        if user_config_path.exists():
            config.update(_read_json(user_config_path))
            config["_active_config_path"] = str(user_config_path)
            config["_active_config_source"] = "user"
        else:
            config["_active_config_path"] = str(user_config_path)
            config["_active_config_source"] = "user"

        return config

    def save(self, config: dict) -> None:
        """Save config to user's workspace."""
        if not self.user_id:
            raise ValueError("User ID not set")

        user_config_path = get_user_config_path(self.user_id)
        to_save = {k: v for k, v in config.items() if not k.startswith("_")}
        user_config_path.write_text(json.dumps(to_save, indent=2), encoding="utf-8")
