from pathlib import Path


def normalize(repo_id: str) -> str:
    """Normalizes a model repository ID to be something suitable for a folder or image tag."""
    return repo_id.replace("/", "--").replace(".", "_").lower()


class Truthy(str):
    """Casting to boolean returns True when set to "true", "yes", "1", or any case of the above."""

    def __bool__(self) -> bool:
        match self.lower():
            case "true":
                return True
            case "yes":
                return True
            case "1":
                return True
            case _:
                return False


def walk(path: Path) -> list[Path]:
    """Walks a provided path to enumerate all child files, ignoring empty directories"""
    if path.is_dir():
        ret = list()
        for subpath in path.iterdir():
            ret.extend(walk(subpath))
        return ret
    else:
        return [path]


def cleanup(path: Path, skip: list[str] = []) -> bool:
    """Remove all children of the provided path, except for top-level files provided in skip."""
    changed = False
    for subpath in path.iterdir():
        if subpath.name in skip:
            continue
        if subpath.is_dir():
            _ = cleanup(subpath, skip=[])
            subpath.rmdir()
            changed = True
        else:
            subpath.unlink()
            changed = True
    return changed
