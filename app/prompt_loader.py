from pathlib import Path

try:
    from .config import AppConfig
except ImportError:  # Allows `python app/main.py`.
    from config import AppConfig


def load_prompt(file_name: str) -> str:
    """Load one markdown prompt from the prompts directory."""
    safe_name = Path(file_name).name
    if safe_name != file_name:
        raise ValueError("file_name must be a file from the prompts directory")

    prompt_path = AppConfig.prompts_dir / safe_name
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt not found: {safe_name}")

    return prompt_path.read_text(encoding="utf-8").strip()


def load_all_prompts() -> dict[str, str]:
    """Load all markdown prompts keyed by file stem."""
    prompts: dict[str, str] = {}
    for prompt_path in sorted(AppConfig.prompts_dir.glob("*.md")):
        prompts[prompt_path.stem] = prompt_path.read_text(encoding="utf-8").strip()
    return prompts
