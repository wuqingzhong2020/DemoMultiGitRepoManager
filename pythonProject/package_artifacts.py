"""按版本覆盖的历史安装包、固定最新副本及安装前的完整性校验。"""
from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .common import OperationError


def validate_package(package: object, label: str) -> dict:
    if (not isinstance(package, dict) or not isinstance(package.get("name"), str)
            or not re.fullmatch(r"(?:@[a-z0-9._-]+/)?[a-z0-9._-]+", package["name"])
            or not isinstance(package.get("version"), str)
            or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.+-]+)?", package["version"])):
        raise OperationError(f"{label} 缺少合法 package name/version")
    for field in ("scripts", "peerDependencies"):
        values = package.get(field, {})
        if not isinstance(values, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in values.items()):
            raise OperationError(f"{label} 的 {field} 必须是字符串映射")
    return package


def package_stem(name: str) -> str:
    if not re.fullmatch(r"(?:@[a-z0-9._-]+/)?[a-z0-9._-]+", name):
        raise OperationError(f"非法插件包名: {name}")
    return name.lstrip("@").replace("/", "-")


def dist_directory(root: Path) -> Path:
    root = root.resolve()
    directory = (root / "dist").resolve()
    if directory == root or not directory.is_relative_to(root):
        raise OperationError(f"安装包输出目录逃出工程: {directory}")
    return directory


def artifact_directory(root: Path, child: str) -> Path:
    dist = dist_directory(root)
    directory = (dist / child).resolve()
    if directory == dist or not directory.is_relative_to(dist):
        raise OperationError(f"安装包子目录逃出 dist: {directory}")
    return directory


def latest_path(root: Path, name: str) -> Path:
    return artifact_directory(root, "latest") / f"{package_stem(name)}.tgz"


def regular_bytes(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise OperationError(f"文件缺失或不是普通文件，请先 pack: {path}")
    return path.read_bytes()


def packed_metadata(data: bytes, label: str) -> dict:
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as source:
            member = source.getmember("package/package.json")
            if not member.isfile() or member.size > 1024 * 1024:
                raise ValueError("package.json 不是普通小文件")
            stream = source.extractfile(member)
            if stream is None:
                raise ValueError("无法读取 package.json")
            package = json.load(stream)
        return validate_package(package, label)
    except (OSError, tarfile.TarError, KeyError, ValueError, UnicodeError) as error:
        raise OperationError(f"安装包内容无效: {label}: {error}") from error


@dataclass(frozen=True)
class PackageArtifact:
    history: Path
    digest: str
    package: dict
    latest: Path | None = None
    build_time: str | None = None


def load_history(root: Path, filename: str | Path) -> PackageArtifact:
    dist = dist_directory(root)
    candidate = Path(filename)
    if not candidate.is_absolute():
        candidate = dist / candidate
    if candidate.is_symlink() or candidate.resolve().parent != dist or candidate.suffix != ".tgz":
        raise OperationError(f"历史安装包必须是 dist 直属的普通 tgz 文件: {filename}")
    return _load_archive(candidate.resolve())


def _load_archive(path: Path) -> PackageArtifact:
    try:
        data = regular_bytes(path)
        checksum = regular_bytes(Path(str(path) + ".sha256")).decode("utf-8").split()
        if len(checksum) != 2 or not re.fullmatch(r"[0-9a-fA-F]{64}", checksum[0]) or checksum[1] != path.name:
            raise OperationError(f"包校验摘要格式无效: {path}")
        digest = hashlib.sha256(data).hexdigest()
        if checksum[0].lower() != digest:
            raise OperationError(f"安装包 SHA256 不匹配: {path}")
        return PackageArtifact(path, digest, packed_metadata(data, str(path)))
    except (OSError, UnicodeError) as error:
        raise OperationError(f"无法校验安装包: {path}: {error}") from error


def load_latest(root: Path, name: str, expected_version: str | None = None) -> PackageArtifact:
    path = latest_path(root, name)
    index_path = path.with_suffix(".json")
    try:
        revision = regular_bytes(index_path)
        index = json.loads(revision)
        if not isinstance(index, dict) or set(index) != {"name", "version", "sha256", "history", "buildTime"}:
            raise ValueError("最新包索引字段无效")
        build_time = index["buildTime"]
        if not isinstance(build_time, str) or datetime.fromisoformat(build_time.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("编译时间必须是带时区的 ISO 8601 时间")
        history_name = index["history"]
        if not isinstance(history_name, str) or Path(history_name).name != history_name:
            raise ValueError("历史包索引必须是直属文件名")
        latest = _load_archive(path)
        history = load_history(root, history_name)
        if (latest.package["name"] != name or history.package["name"] != name
                or latest.digest != history.digest or latest.digest != index["sha256"]
                or index["name"] != name or index["version"] != latest.package["version"]
                or (expected_version is not None and latest.package["version"] != expected_version)):
            raise ValueError("最新包、历史包、索引或源码版本不一致")
        if regular_bytes(index_path) != revision:
            raise ValueError("最新包在校验期间变化，请重试")
        return PackageArtifact(history.history, history.digest, history.package, path, build_time)
    except (OSError, ValueError, UnicodeError, KeyError) as error:
        raise OperationError(f"最新安装包无效，请先 pack: {path}: {error}") from error


def _atomic_write(path: Path, data: bytes) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise OperationError(f"拒绝覆盖非普通安装包文件: {path}")
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".artifact-", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(data)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def publish_archive(root: Path, source: Path, expected_package: dict | None = None,
                    build_time: str | None = None) -> PackageArtifact:
    dist = dist_directory(root)
    if not source.resolve().is_relative_to(dist):
        raise OperationError(f"待发布安装包必须位于 dist: {source}")
    data = regular_bytes(source)
    package = packed_metadata(data, str(source))
    if expected_package is not None and any(package[field] != expected_package[field] for field in ("name", "version")):
        raise OperationError("安装包 name/version 与源码不一致，未更新 latest")
    build_time = build_time or datetime.fromtimestamp(source.stat().st_mtime, timezone.utc).isoformat(timespec="milliseconds")
    try:
        if not isinstance(build_time, str) or datetime.fromisoformat(build_time.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("缺少时区")
    except ValueError as error:
        raise OperationError("编译时间必须是带时区的 ISO 8601 时间，未更新安装包") from error
    digest = hashlib.sha256(data).hexdigest()
    name = package_stem(package["name"])
    history = dist / f"{name}-{package['version']}.tgz"
    latest = latest_path(root, package["name"])
    latest.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(history, data)
    _atomic_write(Path(str(history) + ".sha256"), f"{digest}  {history.name}\n".encode())
    _atomic_write(latest, data)
    _atomic_write(Path(str(latest) + ".sha256"), f"{digest}  {latest.name}\n".encode())
    index = {"name": package["name"], "version": package["version"], "sha256": digest,
             "history": history.name, "buildTime": build_time}
    # 索引最后提交；安装检查两份摘要并复核索引，拒绝读取未完整发布的包。
    _atomic_write(latest.with_suffix(".json"), (json.dumps(index, ensure_ascii=False, indent=2) + "\n").encode())
    return load_latest(root, package["name"], package["version"])
