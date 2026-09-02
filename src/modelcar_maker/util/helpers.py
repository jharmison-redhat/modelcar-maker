import os
import shutil
import uuid
from pathlib import Path
from typing import Iterable


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
    if path.is_symlink():
        return [path]
    if path.is_dir():
        ret = list()
        for subpath in path.iterdir():
            ret.extend(walk(subpath))
        return ret
    return [path]


def cleanup(path: Path, skip: Iterable[str] | None = None) -> bool:
    """Remove all children of the provided path, except for top-level files provided in skip."""
    changed = False
    if not path.exists():
        return changed
    if path.is_symlink():
        return changed
    skipped = set(skip or ())
    for subpath in path.iterdir():
        if subpath.name in skipped:
            continue
        if subpath.is_symlink():
            subpath.unlink()
            changed = True
        elif subpath.is_dir():
            _ = cleanup(subpath)
            subpath.rmdir()
            changed = True
        else:
            subpath.unlink()
            changed = True
    return changed


def remove_path(path: Path) -> None:
    """Remove a path without following a directory symlink."""
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path)


def seed_directory(source: Path, destination: Path) -> None:
    """Seed a staging directory, hardlinking regular files and ignoring symlinks."""
    if not source.is_dir() or source.is_symlink():
        return
    for root, directories, files in os.walk(source, followlinks=False):
        root_path = Path(root)
        relative_root = root_path.relative_to(source)
        stage_root = destination / relative_root
        stage_root.mkdir(parents=True, exist_ok=True)
        directories[:] = [name for name in directories if not (root_path / name).is_symlink()]
        for name in files:
            source_file = root_path / name
            if source_file.is_symlink():
                continue
            destination_file = stage_root / name
            in_local_cache = bool(relative_root.parts and relative_root.parts[0] == ".cache")
            if in_local_cache and source_file.suffix != ".metadata":
                continue
            destination_file.parent.mkdir(parents=True, exist_ok=True)
            try:
                # Hugging Face replaces changed payload files rather than writing them in place.
                # Copy its mutable metadata and omit transient locks/incomplete downloads.
                if in_local_cache:
                    shutil.copy2(source_file, destination_file)
                else:
                    os.link(source_file, destination_file)
            except OSError:
                shutil.copy2(source_file, destination_file)


def atomic_replace_directory(
    staged: Path,
    destination: Path,
    companion_replacements: Iterable[tuple[Path, Path]] = (),
) -> None:
    """Promote same-parent staged paths, restoring all live paths on failure."""
    replacements = [(staged, destination), *companion_replacements]
    for staged_path, destination_path in replacements:
        if staged_path.parent.resolve() != destination_path.parent.resolve():
            raise ValueError("Staged and destination paths must have the same parent")
        if not staged_path.exists() and not staged_path.is_symlink():
            raise FileNotFoundError(staged_path)

    backups: list[tuple[Path, Path]] = []
    promoted: list[Path] = []
    try:
        for _, destination_path in replacements:
            if destination_path.exists() or destination_path.is_symlink():
                backup = destination_path.with_name(f".{destination_path.name}.backup-{uuid.uuid4().hex}")
                destination_path.rename(backup)
                backups.append((backup, destination_path))
        for staged_path, destination_path in replacements:
            staged_path.rename(destination_path)
            promoted.append(destination_path)
    except BaseException:
        displaced: list[Path] = []
        rollback_errors: list[BaseException] = []
        for promoted_path in reversed(promoted):
            if not promoted_path.exists() and not promoted_path.is_symlink():
                continue
            try:
                rollback_path = promoted_path.with_name(f".{promoted_path.name}.rollback-{uuid.uuid4().hex}")
                promoted_path.rename(rollback_path)
                displaced.append(rollback_path)
            except BaseException as error:
                rollback_errors.append(error)
        for backup, destination_path in reversed(backups):
            if destination_path.exists() or destination_path.is_symlink():
                rollback_errors.append(RuntimeError(f"Unable to clear promoted destination {destination_path}"))
                continue
            try:
                backup.rename(destination_path)
            except BaseException as error:
                rollback_errors.append(error)
        for rollback_path in displaced:
            try:
                remove_path(rollback_path)
            except BaseException:
                pass
        if rollback_errors:
            raise RuntimeError("Failed to restore live paths after promotion failure") from rollback_errors[0]
        raise
    for backup, _ in backups:
        try:
            remove_path(backup)
        except BaseException:
            pass
