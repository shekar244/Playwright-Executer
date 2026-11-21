"""Target environments. The grid sets TEST_ENV per chunk (the run's chosen environment)."""
import os

# Replace with the real application URLs. Any entry can also be overridden at deploy time
# with BASE_URL_<ENV> (e.g. BASE_URL_QA), or for everything at once with BASE_URL.
ENVIRONMENTS = {
    "dev": "https://example.com",
    "qa": "https://example.com",
    "perf": "https://example.com",
    "prod": "https://example.com",
}


def current_environment() -> str:
    name = os.getenv("TEST_ENV", "qa").lower()
    if name not in ENVIRONMENTS:
        raise ValueError(f"Unknown TEST_ENV '{name}'. Choose one of: {', '.join(ENVIRONMENTS)}")
    return name


def base_url(name: str = "") -> str:
    name = name or current_environment()
    return os.getenv("BASE_URL") or os.getenv(f"BASE_URL_{name.upper()}") or ENVIRONMENTS[name]
