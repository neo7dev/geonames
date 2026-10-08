from pathlib import Path


def create_directory(path: str | Path) -> Path:
    """Create a directory if it does not exist."""
    path = Path(path)
    if check_read_permissions(path) and check_write_permissions(path):
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
    return path


def check_read_permissions(path: str | Path) -> bool:
    """Check if the directory is readable."""
    path = Path(path)
    return path.is_dir() and path.exists() and path.stat().st_mode & 0o400 != 0


def check_write_permissions(path: str | Path) -> bool:
    """Check if the directory is writable."""
    path = Path(path)
    return path.is_dir() and path.exists() and path.stat().st_mode & 0o200 != 0


def find_project_root(start: Path) -> Path:
    for parent in start.resolve().parents:
        if (parent / "requirements.txt").exists():
            return parent
    raise RuntimeError("Project root not found")
