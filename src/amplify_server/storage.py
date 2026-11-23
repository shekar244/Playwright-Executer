"""User-scoped storage and workspace management."""
from pathlib import Path
from amplify_server.config import settings

def get_user_workspace_dir(user_id: int) -> Path:
    """Get the workspace directory for a user."""
    ws_dir = settings.WORKSPACES_ROOT / str(user_id)
    ws_dir.mkdir(parents=True, exist_ok=True)
    return ws_dir

def get_user_config_path(user_id: int) -> Path:
    """Get the config.json path for a user."""
    ws_dir = get_user_workspace_dir(user_id)
    config_dir = ws_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / "config.json"

def get_user_history_path(user_id: int) -> Path:
    """Get the report_history.json path for a user."""
    ws_dir = get_user_workspace_dir(user_id)
    history_dir = ws_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    return history_dir / "report_history.json"

def initialize_user_workspace(user_id: int) -> None:
    """Initialize workspace directories and default config for a new user."""
    ws_dir = get_user_workspace_dir(user_id)
    config_path = get_user_config_path(user_id)
    history_path = get_user_history_path(user_id)

    if not config_path.exists():
        from amplify_server.config_reader import fresh_defaults
        import json
        config_path.write_text(json.dumps(fresh_defaults(), indent=2), encoding="utf-8")

    if not history_path.exists():
        history_path.write_text("[]", encoding="utf-8")
