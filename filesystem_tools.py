import ctypes
import json
import os
import re
import shutil
import tempfile
import threading
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path


_KNOWN_FOLDER_IDS = {
    "desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
    "music": "4BD8D97D-6D19-48D3-BE97-422220080E43",
}
_FOLDER_ALIASES = {
    "desktop": "desktop",
    "documents": "documents",
    "downloads": "downloads",
    "pictures": "pictures",
    "videos": "videos",
    "music": "music",
    "my documents": "documents",
    "my downloads": "downloads",
    "my pictures": "pictures",
    "my videos": "videos",
    "my music": "music",
}
_PROTECTED_SYSTEM_DIRS = {
    "desktop": "USERPROFILE",
    "documents": "USERPROFILE",
}
_INVALID_NAME_CHARS = set('<>:"|?*')
_PROJECT_ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class FileSystemIntent:
    intent: str
    source: str = ""
    destination: str = ""
    new_name: str = ""
    item_type: str = ""
    sources: tuple = ()
    requires_confirmation: bool = False

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, payload: str):
        data = json.loads(payload)
        data["sources"] = tuple(data.get("sources") or ())
        return cls(**data)


class FileSystemContext:
    def __init__(self):
        self._lock = threading.RLock()
        self._items = deque(maxlen=30)
        self._last_created = ""
        self._last_copied = ""
        self._last_item = ""
        self._pending_copy_source = ""

    def clear(self):
        with self._lock:
            self._items.clear()
            self._last_created = ""
            self._last_copied = ""
            self._last_item = ""
            self._pending_copy_source = ""

    def record(self, path, operation, item_type):
        path = str(Path(path))
        with self._lock:
            self._items.append({"path": path, "operation": operation, "type": item_type})
            if operation != "copy":
                self._last_item = path
            if operation == "create":
                self._last_created = path
            elif operation == "copy":
                self._last_copied = path

    def select_for_copy(self, path):
        with self._lock:
            self._pending_copy_source = str(Path(path))
            self._last_item = self._pending_copy_source

    def copy_source(self):
        with self._lock:
            return self._pending_copy_source or self._last_item

    def resolve_latest_named(self, phrase):
        normalized = _clean_phrase(phrase).lower()
        with self._lock:
            for item in reversed(self._items):
                if Path(item["path"]).name.lower() == normalized:
                    return item["path"]
        return ""

    def resolve_parent_location(self, phrase):
        parts = [
            part.casefold()
            for part in re.split(r"[\\/]+", _clean_phrase(phrase))
            if part and part != "."
        ]
        if not parts:
            return ""
        with self._lock:
            recent_paths = [item["path"] for item in reversed(self._items)]
        for path in recent_paths:
            parent = Path(path).parent
            parent_parts = parent.parts
            if len(parent_parts) >= len(parts) and [
                part.casefold() for part in parent_parts[-len(parts):]
            ] == parts:
                if parent.is_dir():
                    return str(parent)
        return ""

    def forget(self, path):
        canonical = str(resolve_path(path))
        with self._lock:
            self._items = deque(
                (item for item in self._items if str(resolve_path(item["path"])) != canonical),
                maxlen=30,
            )
            if self._last_item and str(resolve_path(self._last_item)) == canonical:
                self._last_item = self._items[-1]["path"] if self._items else ""
            if self._last_created and str(resolve_path(self._last_created)) == canonical:
                self._last_created = ""
            if self._last_copied and str(resolve_path(self._last_copied)) == canonical:
                self._last_copied = ""
            if self._pending_copy_source and str(resolve_path(self._pending_copy_source)) == canonical:
                self._pending_copy_source = ""

    def resolve_reference(self, phrase):
        normalized = _clean_phrase(phrase).lower()
        with self._lock:
            if normalized in {"the copy", "copy", "copied folder", "copied file"}:
                return self._last_copied or ""
            if normalized in {
                "it", "that", "this", "that folder", "this folder",
                "that file", "this file", "the previous folder",
                "the previous file", "the new folder", "the file i created",
                "the folder we just created", "the folder i just created",
            }:
                return self._last_item or self._last_created
            if normalized in {"the copied folder", "the copied file"}:
                return self._last_copied
            for item in reversed(self._items):
                if Path(item["path"]).name.lower() == normalized:
                    return item["path"]
            return ""

    def context_lines(self):
        with self._lock:
            lines = []
            if self._last_item:
                lines.append(f"Last filesystem item: {self._last_item}")
            if self._last_created:
                lines.append(f"Last created filesystem item: {self._last_created}")
            if self._last_copied:
                lines.append(f"Last copied filesystem item: {self._last_copied}")
            recent = list(self._items)[-8:]
            if recent:
                lines.append(
                    "Recent filesystem items: "
                    + "; ".join(f"{item['operation']}|{item['type']}|{item['path']}" for item in recent)
                )
            return lines


filesystem_context = FileSystemContext()


def _clean_phrase(value):
    return (value or "").strip().strip("\"'“”‘’").strip().rstrip(".,!?")


def _known_folder_path(name):
    folder_id = _KNOWN_FOLDER_IDS[name]
    if os.name == "nt":
        try:
            class GUID(ctypes.Structure):
                _fields_ = [
                    ("Data1", ctypes.c_uint32),
                    ("Data2", ctypes.c_uint16),
                    ("Data3", ctypes.c_uint16),
                    ("Data4", ctypes.c_ubyte * 8),
                ]

            parsed = uuid.UUID(folder_id)
            data = parsed.bytes_le
            guid = GUID(
                int.from_bytes(data[0:4], "little"),
                int.from_bytes(data[4:6], "little"),
                int.from_bytes(data[6:8], "little"),
                (ctypes.c_ubyte * 8).from_buffer_copy(data[8:16]),
            )
            path_pointer = ctypes.c_wchar_p()
            result = ctypes.windll.shell32.SHGetKnownFolderPath(
                ctypes.byref(guid), 0, None, ctypes.byref(path_pointer)
            )
            if result == 0 and path_pointer.value:
                path = path_pointer.value
                ctypes.windll.ole32.CoTaskMemFree(path_pointer)
                return Path(path)
        except (AttributeError, OSError, ValueError, ctypes.ArgumentError):
            pass

    home = Path.home()
    candidates = []
    one_drive = os.environ.get("OneDrive") or os.environ.get("OneDriveCommercial")
    if one_drive:
        candidates.append(Path(one_drive) / name.title())
    candidates.extend((home / name.title(), home / "OneDrive" / name.title()))
    return next((candidate for candidate in candidates if candidate.exists()), candidates[0])


def resolve_path(value, base=None) -> Path:
    """Resolve Windows known-folder aliases, environment variables and relative paths."""
    raw = _clean_phrase(os.fspath(value) if isinstance(value, os.PathLike) else value)
    if not raw:
        raise ValueError("A path is required.")
    raw = re.sub(
        r"%([^%]+)%",
        lambda match: os.environ.get(match.group(1), match.group(0)),
        raw,
    )
    raw = os.path.expandvars(os.path.expanduser(raw))
    raw = raw.replace("/", os.sep) if os.name == "nt" else raw
    raw = re.sub(r"^the\s+", "", raw, flags=re.I)
    project_root = _PROJECT_ROOT

    project_match = re.match(r"^(?:my\s+)?(?:jarvis|ultron)(?:\s+project)?\s+folder(?:[\\/](.*))?$", raw, re.I)
    if project_match:
        path = project_root
        if project_match.group(1):
            path /= project_match.group(1)
        return path.resolve(strict=False)
    if raw.lower() in {"jarvis", "ultron", "ultron project"}:
        return project_root

    for alias, folder_key in sorted(_FOLDER_ALIASES.items(), key=lambda entry: -len(entry[0])):
        match = re.match(rf"^{re.escape(alias)}(?:\s+folder)?(?:[\\/](.*))?$", raw, re.I)
        if match:
            path = _known_folder_path(folder_key)
            if match.group(1):
                path /= match.group(1)
            return path.resolve(strict=False)

    path = Path(raw)
    if not path.is_absolute():
        path = Path(base) / path if base else Path.home() / path
    return path.resolve(strict=False)


def _validate_leaf_name(name):
    name = _clean_phrase(name)
    if not name or name in {".", ".."} or any(char in name for char in _INVALID_NAME_CHARS):
        raise ValueError(f"Invalid file or folder name: {name or '(empty)'}")
    if name.endswith((" ", ".")):
        raise ValueError(f"Windows names cannot end with a space or period: {name}")
    return name


def create_folder(path):
    target = resolve_path(path)
    if target.exists():
        raise FileExistsError(f"The folder already exists: {target}")
    target.mkdir(parents=False, exist_ok=False)
    filesystem_context.record(target, "create", "folder")
    return f"Folder created successfully:\n{target}"


def create_file(path):
    target = resolve_path(path)
    if not target.parent.is_dir():
        raise FileNotFoundError(f"The destination folder does not exist: {target.parent}")
    with target.open("x", encoding="utf-8"):
        pass
    filesystem_context.record(target, "create", "file")
    return f"File created successfully:\n{target}"


def delete_file(path):
    target = resolve_path(path)
    if target.is_symlink():
        target.unlink()
    elif target.is_file():
        target.unlink()
    else:
        raise FileNotFoundError(f"The source file does not exist: {target}")
    return f"File deleted successfully:\n{target}"


def delete_folder(path):
    target = resolve_path(path)
    _guard_delete_target(target)
    if not target.is_dir():
        raise FileNotFoundError(f"The source folder does not exist: {target}")
    if target.is_symlink():
        target.unlink()
    else:
        shutil.rmtree(target)
    return f"Folder deleted successfully:\n{target}"


def _guard_delete_target(target):
    target = target.resolve(strict=False)
    home = Path.home().resolve(strict=False)
    project = _PROJECT_ROOT
    if target == home or target == Path(target.anchor).resolve(strict=False):
        raise PermissionError(f"Refusing to delete a protected root folder: {target}")
    if target == project or target == Path(os.path.abspath(os.getcwd())).resolve(strict=False):
        raise PermissionError(f"Refusing to delete the active ULTRON project folder: {target}")


def rename_file(source, new_name, overwrite=False):
    return _rename(source, new_name, "file", overwrite)


def rename_folder(source, new_name, overwrite=False):
    return _rename(source, new_name, "folder", overwrite)


def _rename(source, new_name, item_type, overwrite):
    src = resolve_path(source)
    name = _validate_leaf_name(new_name)
    if item_type == "file" and src.is_file():
        if Path(name).suffix == "" and src.suffix:
            name += src.suffix
    if not src.exists():
        raise FileNotFoundError(f"The source {item_type} does not exist: {src}")
    if src.is_dir() != (item_type == "folder"):
        raise ValueError(f"The source is not a {item_type}: {src}")
    dst = src.with_name(name)
    if src == dst:
        raise ValueError("The new name is the same as the current name.")
    if dst.exists() and not overwrite:
        raise FileExistsError(f"The destination already exists: {dst}")
    _replace_with_backup(src, dst, move=True, overwrite=overwrite)
    filesystem_context.record(dst, "rename", item_type)
    return f"{item_type.title()} renamed successfully:\nFrom: {src}\nTo: {dst}"


def move_file(source, destination, overwrite=False):
    return _transfer(source, destination, "file", copy=False, overwrite=overwrite)


def move_folder(source, destination, overwrite=False):
    return _transfer(source, destination, "folder", copy=False, overwrite=overwrite)


def copy_file(source, destination, overwrite=False):
    return _transfer(source, destination, "file", copy=True, overwrite=overwrite)


def copy_folder(source, destination, overwrite=False):
    return _transfer(source, destination, "folder", copy=True, overwrite=overwrite)


def _transfer(source, destination, item_type, copy, overwrite):
    src = resolve_path(source)
    dst_dir = resolve_path(destination)
    if not src.exists():
        raise FileNotFoundError(f"The source {item_type} does not exist: {src}")
    if src.is_dir() != (item_type == "folder"):
        raise ValueError(f"The source is not a {item_type}: {src}")
    if not dst_dir.is_dir():
        raise FileNotFoundError(f"The destination folder does not exist: {dst_dir}")
    final = dst_dir / src.name
    if final == src:
        raise ValueError("The source and destination are the same location.")
    if item_type == "folder" and _is_child_path(dst_dir, src):
        raise ValueError("A folder cannot be copied or moved into itself.")
    if final.exists() and not overwrite:
        raise FileExistsError(f"The destination already exists: {final}")
    _replace_with_backup(src, final, move=not copy, overwrite=overwrite)
    if copy:
        filesystem_context.select_for_copy(src)
    filesystem_context.record(final, "copy" if copy else "move", item_type)
    verb = "copied" if copy else "moved"
    return f"{item_type.title()} {verb} successfully:\nFrom: {src}\nTo: {final}"


def _is_child_path(path, parent):
    try:
        Path(path).resolve(strict=False).relative_to(Path(parent).resolve(strict=False))
        return True
    except ValueError:
        return False


def _replace_with_backup(source, destination, move, overwrite):
    destination.parent.mkdir(parents=False, exist_ok=True)
    if not destination.exists():
        if move:
            shutil.move(str(source), str(destination))
        elif source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)
        return
    if not overwrite:
        raise FileExistsError(f"The destination already exists: {destination}")

    staging = Path(tempfile.mkdtemp(prefix=".ultron-stage-", dir=str(destination.parent)))
    staged_item = staging / destination.name
    backup = destination.with_name(f".{destination.name}.ultron-backup-{uuid.uuid4().hex}")
    try:
        if move:
            shutil.move(str(source), str(staged_item))
        elif source.is_dir():
            shutil.copytree(source, staged_item)
        else:
            shutil.copy2(source, staged_item)
        destination.rename(backup)
        try:
            staged_item.rename(destination)
        except OSError:
            backup.rename(destination)
            raise
        if backup.is_dir() and not backup.is_symlink():
            shutil.rmtree(backup)
        else:
            backup.unlink()
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def list_directory(path):
    target = resolve_path(path)
    if not target.is_dir():
        raise FileNotFoundError(f"The folder does not exist: {target}")
    entries = sorted(target.iterdir(), key=lambda entry: (not entry.is_dir(), entry.name.lower()))
    if not entries:
        return f"Folder is empty:\n{target}"
    lines = [f"Contents of {target}:"]
    lines.extend(f"{'[Folder]' if entry.is_dir() else '[File]'} {entry.name}" for entry in entries[:100])
    if len(entries) > 100:
        lines.append(f"... and {len(entries) - 100} more item(s).")
    return "\n".join(lines)


def delete_payload(payload):
    data = json.loads(payload)
    paths = data.get("paths") or [data.get("path", "")]
    item_type = data.get("item_type", "item")
    results = []
    for path in paths:
        target = resolve_path(path)
        if target.is_dir():
            results.append(delete_folder(target))
        else:
            results.append(delete_file(target))
    return "\n".join(results)


def overwrite_payload(payload):
    intent = FileSystemIntent.from_json(payload)
    if intent.intent == "rename":
        operation = rename_folder if intent.item_type == "folder" else rename_file
        return operation(intent.source, intent.new_name, overwrite=True)
    if intent.intent == "move":
        operation = move_folder if intent.item_type == "folder" else move_file
        return operation(intent.source, intent.destination, overwrite=True)
    if intent.intent == "copy":
        operation = copy_folder if intent.item_type == "folder" else copy_file
        return operation(intent.source, intent.destination, overwrite=True)
    raise ValueError("This filesystem operation cannot replace an existing item.")


def parse_filesystem_command(text):
    """Return a structured filesystem intent for clear natural-language commands."""
    command = _clean_phrase(text)
    command = re.sub(r"^(?:hey\s+)?(?:ultron|jarvis)[, ]*", "", command, flags=re.I)
    command = command.strip()

    match = re.match(
        r"^(?:create|make)\s+(?:a\s+|new\s+)?folder\s+(?:(?:named|called)\s+)?(.+)$",
        command,
        re.I,
    )
    if match:
        name, location = _split_name_location(match.group(1))
        if not location:
            return FileSystemIntent("create_folder", new_name=_clean_phrase(name), item_type="folder")
        try:
            parent = _resolve_location(location)
        except ValueError as error:
            return FileSystemIntent(
                "unresolved_destination",
                destination=str(error),
                item_type="folder",
            )
        if not parent.is_dir():
            return FileSystemIntent(
                "unresolved_destination",
                destination=str(parent),
                item_type="folder",
            )
        return FileSystemIntent(
            "create_folder",
            destination=str(parent),
            new_name=_clean_phrase(name),
            item_type="folder",
        )

    paste_match = re.match(
        r"^paste(?:\s+(?:it|the\s+copy|that))?\s+(?:in|into|to|on)\s+(.+)$",
        command,
        re.I,
    )
    if paste_match:
        source = filesystem_context.copy_source()
        if not source:
            return FileSystemIntent("paste_unresolved", destination=_clean_phrase(paste_match.group(1)))
        item_type = _infer_type(source)
        destination_path = _resolve_location(paste_match.group(1))
        return FileSystemIntent(
            "copy",
            source=source,
            destination=str(destination_path),
            item_type=item_type,
            requires_confirmation=(destination_path / Path(source).name).exists(),
        )

    match = re.match(
        r"^(?:create|make)\s+(?:a\s+)?file\s+(?:named|called)\s+(.+)$",
        command,
        re.I,
    )
    if match:
        name, location = _split_name_location(match.group(1))
        if not location:
            return FileSystemIntent("create_file", new_name=_clean_phrase(name), item_type="file")
        target = _resolve_location(location) / _validate_leaf_name(name)
        return FileSystemIntent(
            "create_file",
            destination=str(target.parent),
            new_name=target.name,
            item_type="file",
            source=str(target),
        )

    match = re.match(r"^(?:list|show)\s+(?:the\s+)?(?:contents\s+of\s+)?(.+?)(?:\s+folder)?$", command, re.I)
    # "show me <name>" asks to open a folder elsewhere; "show <name>" lists it.
    if match and not re.match(r"^show\s+me\b", command, re.I):
        target = _clean_phrase(match.group(1))
        if target.lower() not in {"me", "that", "this", "files", "folders", "directory"}:
            return FileSystemIntent("list_directory", source=str(resolve_path(target)), item_type="folder")

    match = re.match(
        r"^(?:delete|remove)\s+(?:(?:the\s+)?(file|folder|directory)\s+)?(.+?)(?:\s+from\s+(.+))?$",
        command,
        re.I,
    )
    if match:
        item_type = (match.group(1) or "").lower()
        reference = _clean_phrase(match.group(2))
        location = _clean_phrase(match.group(3) or "")
        if not location:
            reference, location = _split_name_location(reference)
        if reference.lower() in {"everything", "all files", "all folders", "all"}:
            return FileSystemIntent("delete_invalid", source=reference)
        sources = _resolve_sources(reference, location, item_type)
        if not sources:
            return FileSystemIntent("delete_unresolved", source=reference, item_type=item_type)
        actual_type = "folder" if Path(sources[0]).is_dir() else "file"
        return FileSystemIntent(
            "delete",
            source=sources[0],
            item_type=actual_type,
            sources=tuple(sources),
            requires_confirmation=True,
        )

    match = re.match(
        r"^rename\s+(?:(?:the\s+)?(file|folder|directory)\s+)?(.+?)\s+to\s+(.+)$",
        command,
        re.I,
    )
    if match:
        item_type = (match.group(1) or "").lower()
        raw_source = _strip_item_type(match.group(2))
        new_part = _clean_phrase(match.group(3))
        new_name, location = _split_name_location(new_part)
        src = _resolve_source(raw_source, location, item_type)
        if not src:
            return FileSystemIntent("unresolved", source=raw_source, item_type=item_type)
        actual_type = "folder" if Path(src).is_dir() else "file"
        dest = Path(src).with_name(_validate_leaf_name(new_name))
        if actual_type == "file" and not dest.suffix and Path(src).suffix:
            dest = dest.with_suffix(Path(src).suffix)
        return FileSystemIntent(
            "rename",
            source=src,
            destination=str(dest),
            new_name=dest.name,
            item_type=actual_type,
            requires_confirmation=dest.exists(),
        )

    transfer_match = re.match(
        r"^(copy|move)\s+(?:(?:the\s+)?(file|folder|directory)\s+)?(.+)$",
        command,
        re.I,
    )
    if transfer_match:
        operation = transfer_match.group(1).lower()
        item_type = (transfer_match.group(2) or "").lower()
        remainder = _clean_phrase(transfer_match.group(3))
        paste_match = re.match(
            r"^(.+?)(?:\s+from\s+(.+?))?\s+and\s+paste\s+(?:it\s+)?"
            r"(?:in|into|to|on)\s+(.+)$",
            remainder,
            re.I,
        )
        if paste_match:
            raw_source = _strip_item_type(paste_match.group(1))
            source_location = _clean_phrase(paste_match.group(2) or "")
            destination = _clean_phrase(paste_match.group(3))
        else:
            transfer = re.match(
                r"^(.+?)(?:\s+from\s+(.+?))?\s+(?:to|into)\s+(.+)$",
                remainder,
                re.I,
            )
            if not transfer:
                if operation == "copy":
                    source_path = _resolve_source(remainder, "", item_type)
                    if source_path:
                        filesystem_context.select_for_copy(source_path)
                        return FileSystemIntent(
                            "copy_pending", source=source_path,
                            item_type=_infer_type(source_path, item_type),
                        )
                if operation == "move":
                    source_path = _resolve_source(_strip_item_type(remainder), "", item_type)
                    if source_path:
                        return FileSystemIntent(
                            "move_pending", source=source_path,
                            item_type=_infer_type(source_path, item_type),
                        )
                return None
            raw_source = _strip_item_type(transfer.group(1))
            source_location = _clean_phrase(transfer.group(2) or "")
            destination = _clean_phrase(transfer.group(3))

        src = _resolve_source(raw_source, source_location, item_type)
        if not src:
            return FileSystemIntent("unresolved", source=raw_source, item_type=item_type)
        actual_type = _infer_type(src, item_type)
        try:
            dest_dir = _resolve_location(destination)
        except ValueError:
            return FileSystemIntent("unresolved_destination", source=src, destination=destination, item_type=actual_type)
        final = dest_dir / Path(src).name
        return FileSystemIntent(
            operation,
            source=src,
            destination=str(dest_dir),
            item_type=actual_type,
            requires_confirmation=final.exists(),
        )

    return None


def _split_name_location(value):
    value = _clean_phrase(value)
    match = re.match(r"^(.+?)\s+(?:inside|in|on|at)\s+(.+)$", value, re.I)
    if not match:
        return value, ""
    name = _clean_phrase(match.group(1))
    location = _clean_phrase(match.group(2))
    if name.startswith(("\"", "'")) and name.endswith(("\"", "'")):
        name = _clean_phrase(name)
    return name, location


def _resolve_location(value):
    location = _clean_phrase(value)
    location = re.sub(r"^(?:my|the)\s+", "", location, flags=re.I)
    location = re.sub(r"\s+(?:folder|directory)$", "", location, flags=re.I)
    remembered = filesystem_context.resolve_reference(location)
    if remembered and Path(remembered).is_dir():
        return Path(remembered)
    if location.casefold() in _FOLDER_ALIASES:
        return resolve_path(location)
    if location.casefold() in {"jarvis", "ultron", "ultron project"}:
        return _PROJECT_ROOT
    requested = Path(location)
    if not requested.is_absolute():
        candidates = [
            (_PROJECT_ROOT / requested).resolve(strict=False),
            (Path.home() / requested).resolve(strict=False),
        ]
        existing = list(dict.fromkeys(path for path in candidates if path.is_dir()))
        if len(existing) == 1:
            return existing[0]
        if len(existing) > 1:
            raise ValueError(
                f"More than one folder is named {location}: "
                + "; ".join(str(path) for path in existing)
                + ". Specify its full path."
            )
        recent_parent = filesystem_context.resolve_parent_location(location)
        if recent_parent:
            return Path(recent_parent)
        indexed = _find_indexed_folders(location)
        if len(indexed) == 1:
            return indexed[0]
        if len(indexed) > 1:
            raise ValueError(
                f"More than one folder matches {location}: "
                + "; ".join(str(path) for path in indexed[:5])
                + ". Specify its full path."
            )
    return resolve_path(location)


def _find_indexed_folders(query):
    parts = [
        part for part in re.split(r"[\\/]+", _clean_phrase(query))
        if part and part != "."
    ]
    if not parts:
        return []

    quick_matches = _find_configured_folders(parts)
    if quick_matches:
        return quick_matches

    from commands import _find_folder_in_index, _norm_name
    from commands import IndexBuildingError, ensure_folder_index_ready

    if not ensure_folder_index_ready():
        raise IndexBuildingError("The whole-computer folder index is still building.")

    matches = []
    for path in _find_folder_in_index(parts[-1]):
        candidate = Path(path)
        candidate_parts = candidate.parts
        if len(candidate_parts) < len(parts):
            continue
        if all(
            _norm_name(actual) == _norm_name(expected)
            for actual, expected in zip(candidate_parts[-len(parts):], parts)
        ):
            matches.append(candidate.resolve(strict=False))
    return list(dict.fromkeys(matches))


def _find_configured_folders(parts):
    try:
        import config
        roots = config.FILE_SEARCH_ROOTS
    except (ImportError, AttributeError):
        roots = []

    roots = list(dict.fromkeys(
        Path(root).resolve(strict=False)
        for root in roots
        if root and Path(root).is_dir()
    ))
    matches = []
    skipped = {
        "$recycle.bin", "windows", "system volume information",
        "programdata", "appdata", "node_modules", ".git",
        "venv", ".venv", "__pycache__", ".cache", "temp", "tmp",
        "site-packages", "installer", "assembly", "recovery",
    }
    max_depth = 12
    max_directories = 100000
    visited = 0

    for root in roots:
        root_depth = len(root.parts)
        for current, directories, _files in os.walk(root, topdown=True):
            current_path = Path(current)
            depth = len(current_path.parts) - root_depth
            directories[:] = [
                name for name in directories
                if name.casefold() not in skipped
                and not name.startswith(".")
                and not _is_reparse_path(current_path / name)
            ]
            if depth >= max_depth:
                directories[:] = []

            current_parts = current_path.parts
            if len(current_parts) >= len(parts) and all(
                actual.casefold() == expected.casefold()
                for actual, expected in zip(current_parts[-len(parts):], parts)
            ):
                matches.append(current_path.resolve(strict=False))

            visited += len(directories) + 1
            if visited >= max_directories:
                break
        if visited >= max_directories:
            break

    return list(dict.fromkeys(matches))


def _is_reparse_path(path):
    try:
        attributes = os.stat(path, follow_symlinks=False).st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & 0x400)


def _strip_item_type(value):
    return re.sub(r"\s+(?:file|folder|directory)$", "", _clean_phrase(value), flags=re.I).strip()


def _infer_type(source, explicit=""):
    if explicit in {"file", "folder", "directory"}:
        return "folder" if explicit in {"folder", "directory"} else "file"
    return "folder" if Path(source).is_dir() else "file"


def _resolve_source(raw_source, location="", item_type=""):
    raw_source = _clean_phrase(raw_source)
    if raw_source.lower() in {
        "it", "that", "this", "that folder", "this folder", "that file", "this file",
        "the previous folder", "the previous file", "the new folder", "the file i created",
        "the folder we just created", "the folder i just created", "the copy",
        "copy", "copied folder", "copied file",
    }:
        return filesystem_context.resolve_reference(raw_source)

    active = filesystem_context.resolve_reference("it")
    if active and Path(active).name.lower() == raw_source.lower() and not location:
        return active

    if location:
        parent = _resolve_location(location)
        candidate = parent / raw_source
        if candidate.exists():
            return str(candidate.resolve(strict=False))

    candidate = resolve_path(raw_source)
    if candidate.exists():
        return str(candidate)

    context_path = filesystem_context.resolve_reference(raw_source)
    if context_path:
        candidate = Path(context_path)
        if location and candidate.parent != _resolve_location(location):
            return str(_resolve_location(location) / candidate.name)
        return context_path
    if not location:
        matches = []
        for folder_name in _FOLDER_ALIASES.values():
            folder = _known_folder_path(folder_name)
            candidate = folder / raw_source
            if candidate.exists():
                matches.append(candidate.resolve(strict=False))
        unique = list(dict.fromkeys(matches))
        if len(unique) == 1:
            return str(unique[0])
        if len(unique) > 1:
            raise ValueError(
                f"More than one item is named {raw_source}: "
                + "; ".join(str(path) for path in unique[:5])
                + ". Specify its source folder."
            )
        searched = _search_known_locations(raw_source, item_type)
        if len(searched) == 1:
            return str(searched[0])
        if len(searched) > 1:
            raise ValueError(
                f"More than one item is named {raw_source}: "
                + "; ".join(str(path) for path in searched[:5])
                + ". Specify its source folder."
            )
        indexed = _find_indexed_items(raw_source, item_type)
        if len(indexed) == 1:
            return str(indexed[0])
        if len(indexed) > 1:
            raise ValueError(
                f"More than one item is named {raw_source}: "
                + "; ".join(str(path) for path in indexed[:5])
                + ". Specify its source folder."
            )
    return ""


def _find_indexed_items(name, item_type=""):
    from commands import _find_folder_in_index, _find_in_index
    from commands import (
        IndexBuildingError,
        ensure_file_index_ready,
        ensure_folder_index_ready,
    )

    cleaned = _strip_item_type(name)
    wanted = cleaned.casefold()
    matches = []
    if item_type not in {"file"}:
        if not ensure_folder_index_ready():
            raise IndexBuildingError("The whole-computer folder index is still building.")
        matches.extend(
            Path(path) for path in _find_folder_in_index(cleaned)
            if Path(path).name.casefold() == wanted
        )
    if item_type not in {"folder", "directory"}:
        if not ensure_file_index_ready():
            raise IndexBuildingError("The whole-computer file index is still building.")
        file_query = Path(cleaned).stem if Path(cleaned).suffix else cleaned
        matches.extend(
            Path(path) for path in _find_in_index(cleaned)
            if Path(path).name.casefold() == wanted
            or (not Path(cleaned).suffix and Path(path).stem.casefold() == file_query.casefold())
        )
    return list(dict.fromkeys(path.resolve(strict=False) for path in matches))


def _search_known_locations(name, item_type=""):
    wanted = _strip_item_type(name).casefold()
    matches = []
    roots = list(dict.fromkeys(_known_folder_path(folder) for folder in _FOLDER_ALIASES.values()))
    skip = {
        ".git", "node_modules", "venv", ".venv", "__pycache__",
        "appdata", "windows", "program files", "program files (x86)",
        ".cache", "dist", "build",
    }
    for root in roots:
        if not root.is_dir():
            continue
        base_depth = len(root.parts)
        for current, dirs, files in os.walk(root):
            depth = len(Path(current).parts) - base_depth
            dirs[:] = [
                directory for directory in dirs
                if directory.casefold() not in skip and not directory.startswith(".")
            ]
            if depth >= 6:
                dirs[:] = []
            if item_type not in {"file"}:
                matches.extend(
                    Path(current) / directory
                    for directory in dirs if directory.casefold() == wanted
                )
            if item_type not in {"folder", "directory"}:
                matches.extend(
                    Path(current) / filename
                    for filename in files if filename.casefold() == wanted
                )
            if len(matches) > 5:
                break
        if len(matches) > 5:
            break
    return list(dict.fromkeys(path.resolve(strict=False) for path in matches))


def _resolve_sources(raw_source, location="", item_type=""):
    normalized_source = _strip_item_type(raw_source).lower()
    if not location and normalized_source not in {
        "it", "that", "this", "that folder", "this folder", "that file", "this file",
        "the previous folder", "the previous file", "the new folder", "the file i created",
        "the folder we just created", "the folder i just created", "the copy",
        "copy", "copied folder", "copied file",
    }:
        recent = filesystem_context.resolve_latest_named(normalized_source)
        if recent and Path(recent).exists():
            return [recent]
    source = _resolve_source(raw_source, location, item_type)
    if source:
        return [source]
    if "," in raw_source or re.search(r"\band\b", raw_source, re.I):
        names = [part.strip() for part in re.split(r",|\s+and\s+", raw_source, flags=re.I) if part.strip()]
        resolved = [_resolve_source(name, location, item_type) for name in names]
        if resolved and all(resolved):
            return resolved
    return []


def describe_confirmation(intent):
    if intent.intent == "delete":
        if len(intent.sources) > 1:
            return "Are you sure you want to permanently delete:\n" + "\n".join(intent.sources) + "?"
        label = "folder" if intent.item_type == "folder" else "file"
        return f"Are you sure you want to permanently delete the {label}:\n{intent.source}?"
    destination = intent.destination
    if intent.intent in {"copy", "move"}:
        destination = str(resolve_path(destination) / Path(intent.source).name)
    return (
        f"{intent.intent.title()} would replace an existing destination:\n"
        f"{destination}\nProceed?"
    )


def execute_filesystem_intent(intent, overwrite=False):
    print(f"[INTENT] {intent.intent.upper()}")
    print(f"[SOURCE] {intent.source or None}")
    print(f"[DESTINATION] {intent.destination or None}")
    print(f"[NAME] {intent.new_name or None}")
    if intent.requires_confirmation and not overwrite:
        print("[CONFIRMATION] REQUIRED")
        return None
    if intent.intent == "create_folder":
        if not intent.destination:
            return "Where should I create the folder? Specify Desktop, Documents, Downloads, or a full path."
        return create_folder(resolve_path(intent.destination) / _validate_leaf_name(intent.new_name))
    if intent.intent == "create_file":
        if not intent.destination:
            return "Where should I create the file? Specify a folder or full path."
        return create_file(resolve_path(intent.destination) / _validate_leaf_name(intent.new_name))
    if intent.intent == "delete":
        results = []
        for source in intent.sources or (intent.source,):
            target = resolve_path(source)
            results.append(delete_folder(target) if target.is_dir() else delete_file(target))
            filesystem_context.forget(target)
        return "\n".join(results)
    if intent.intent == "rename":
        operation = rename_folder if intent.item_type == "folder" else rename_file
        return operation(intent.source, intent.new_name, overwrite)
    if intent.intent == "move":
        operation = move_folder if intent.item_type == "folder" else move_file
        return operation(intent.source, intent.destination, overwrite)
    if intent.intent == "copy":
        operation = copy_folder if intent.item_type == "folder" else copy_file
        return operation(intent.source, intent.destination, overwrite)
    if intent.intent == "copy_pending":
        return "Where should I copy it? Specify Desktop, Documents, Downloads, or a full path."
    if intent.intent == "move_pending":
        return "Where should I move it? Specify Desktop, Documents, Downloads, or a full path."
    if intent.intent == "paste_unresolved":
        return "I don't have a selected item to paste. Tell me which file or folder to copy first."
    if intent.intent == "list_directory":
        return list_directory(intent.source)
    if intent.intent == "unresolved":
        return f"I couldn't locate the source: {intent.source}. Specify its folder or full path."
    if intent.intent == "unresolved_destination":
        return f"I couldn't resolve the destination folder: {intent.destination}."
    if intent.intent == "delete_unresolved":
        return f"I couldn't locate {intent.source}. Nothing was deleted."
    if intent.intent == "delete_invalid":
        return "I won't delete a broad group of files. Specify each exact file or folder."
    raise ValueError(f"Unsupported filesystem intent: {intent.intent}")


def run_filesystem_intent(intent, overwrite=False):
    try:
        result = execute_filesystem_intent(intent, overwrite=overwrite)
    except FileExistsError as error:
        print(f"[RESULT] CONFLICT {error}")
        raise
    except FileNotFoundError as error:
        print(f"[RESULT] FAILURE {error}")
        return f"I couldn't complete the filesystem operation. {error}"
    except (PermissionError, ValueError, OSError) as error:
        print(f"[RESULT] FAILURE {type(error).__name__}: {error}")
        return f"I couldn't complete the filesystem operation: {error}"
    tool_name = {
        "create_folder": "create_folder",
        "create_file": "create_file",
        "delete": "delete_" + (intent.item_type or "file"),
        "rename": "rename_" + (intent.item_type or "file"),
        "move": "move_" + (intent.item_type or "file"),
        "copy": "copy_" + (intent.item_type or "file"),
        "list_directory": "list_directory",
    }.get(intent.intent, intent.intent)
    print(f"[TOOL] {tool_name}")
    completed_operations = {
        "create_folder", "create_file", "delete", "rename",
        "move", "copy", "list_directory",
    }
    missing_destination = (
        intent.intent in {"create_folder", "create_file"}
        and not intent.destination
    )
    if intent.intent in completed_operations and not missing_destination:
        print("[RESULT] SUCCESS")
    else:
        print("[RESULT] NEEDS_INFORMATION")
    return result
