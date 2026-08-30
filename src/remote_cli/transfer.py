"""File transfer engine for uploading and downloading files over remote-cli sessions."""

import base64
import hashlib
import io
import os
import shlex
import tarfile
from collections.abc import Callable
from pathlib import Path

from .client import Client


def calculate_md5(data: bytes) -> str:
    """Calculates MD5 hex digest for given bytes."""
    return hashlib.md5(data).hexdigest()


def create_tar_gz_bytes(local_path: Path) -> tuple[bytes, bool]:
    """
    Creates a tar.gz archive in memory from a local file or directory.
    Returns (tar_gz_bytes, is_directory).
    """
    local_path = local_path.resolve()
    if not local_path.exists():
        raise FileNotFoundError(f"Local path does not exist: {local_path}")

    is_dir = local_path.is_dir()
    bio = io.BytesIO()

    with tarfile.open(fileobj=bio, mode="w:gz") as tar:
        if is_dir:
            for root, _, files in os.walk(local_path):
                for f in files:
                    full_p = Path(root) / f
                    rel_p = full_p.relative_to(local_path)
                    tar.add(str(full_p), arcname=str(rel_p))
        else:
            tar.add(str(local_path), arcname=local_path.name)

    return bio.getvalue(), is_dir


def extract_tar_gz_bytes(
    archive_bytes: bytes, dest_path: Path, single_file_name: str | None = None
) -> None:
    """Extracts a tar.gz archive to destination path.

    If destination is a file path and archive contains a single file without directory structure, writes directly to dest_path.
    """
    dest_path = dest_path.resolve()
    bio = io.BytesIO(archive_bytes)

    with tarfile.open(fileobj=bio, mode="r:*") as tar:
        members = tar.getmembers()
        has_dirs = any(m.isdir() for m in members)

        # Filter regular files (excluding directory entries, PAX headers, and AppleDouble ._ files)
        regular_members = [
            m
            for m in members
            if not m.isdir()
            and not m.name.startswith("PaxHeaders")
            and not Path(m.name).name.startswith("._")
        ]

        if len(regular_members) == 1 and not has_dirs and not dest_path.is_dir():
            # True single file target
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            extracted_f = tar.extractfile(regular_members[0])
            if extracted_f:
                dest_path.write_bytes(extracted_f.read())
        else:
            dest_path.mkdir(parents=True, exist_ok=True)
            try:
                tar.extractall(path=str(dest_path), filter="data")
            except TypeError:
                tar.extractall(path=str(dest_path))


def is_control_master_active(session_id: str) -> bool:
    """Checks if an SSH ControlMaster socket is active and responsive for the session."""
    import subprocess

    from .utils import get_cm_socket_path

    sock = get_cm_socket_path(session_id)
    if not sock.exists():
        return False

    try:
        res = subprocess.run(
            ["ssh", "-O", "check", "-o", f"ControlPath={sock}", "dummy_host"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        return res.returncode == 0 or "Master running" in (res.stdout + res.stderr)
    except Exception:
        return False


class TransferManager:
    """Manages file transfer over active remote-cli sessions using chunked streams or SSH ControlMaster."""

    def __init__(self, client: Client):
        self.client = client

    def upload(
        self,
        session_id: str,
        local_path: str | Path,
        remote_path: str,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> None:
        """Uploads a local file or directory to remote_path in the session."""
        loc_p = Path(local_path).resolve()
        if not loc_p.exists():
            raise FileNotFoundError(f"Local file or directory not found: {loc_p}")

        # Plan B Fast-Path: Use SSH ControlMaster via native SCP if available
        if is_control_master_active(session_id):
            try:
                import subprocess

                from .utils import get_cm_socket_path

                sock = get_cm_socket_path(session_id)
                scp_cmd = [
                    "scp",
                    "-o",
                    f"ControlPath={sock}",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "StrictHostKeyChecking=no",
                ]
                if loc_p.is_dir():
                    scp_cmd.append("-r")
                scp_cmd.extend([str(loc_p), f"dummy_host:{remote_path}"])

                res = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=120.0)
                if res.returncode == 0:
                    if progress_callback:
                        progress_callback(100, 100)
                    return
            except Exception:
                pass  # Fallback to Tar + Base64 stream

        # Fallback Engine: In-band Tar + Base64 single-line stream
        tar_bytes, is_dir = create_tar_gz_bytes(loc_p)
        b64_data = base64.b64encode(tar_bytes).decode("ascii")
        total_len = len(b64_data)

        # Dynamic timeout based on archive size (at least 30s)
        timeout = max(30.0, float(len(tar_bytes)) / (50 * 1024) + 15.0)

        remote_dest_quoted = shlex.quote(remote_path)
        is_remote_dir = is_dir or remote_path.endswith("/")

        if is_remote_dir:
            unpack_cmd = (
                f'mkdir -p {remote_dest_quoted} && tar -xzf "$__RCLI_TAR" -C {remote_dest_quoted}'
            )
        else:
            unpack_cmd = (
                f'mkdir -p "$(dirname {remote_dest_quoted})" && '
                f"__RCLI_EXTRACT_DIR=$(mktemp -d /tmp/rcli_ext_XXXXXX) && "
                f'tar -xzf "$__RCLI_TAR" -C "$__RCLI_EXTRACT_DIR" && '
                f'mv "$__RCLI_EXTRACT_DIR/{loc_p.name}" {remote_dest_quoted} && '
                f'rm -rf "$__RCLI_EXTRACT_DIR"'
            )

        # Step 1: Create remote staging temp file
        init_cmd = '__RCLI_B64=$(mktemp /tmp/rcli_up_XXXXXX) && echo "$__RCLI_B64"'
        init_res = self.client.exec_command(session_id, init_cmd, timeout=15.0)
        if init_res.exit_code != 0 or not init_res.output.strip():
            raise RuntimeError(f"Failed to initialize remote upload staging: {init_res.output}")

        remote_b64_file = init_res.output.strip().splitlines()[-1]

        # Step 2: Stream single-line chunks of 512 chars (safe across all PTY & SSH connections)
        chunk_size = 512
        sent = 0
        for i in range(0, total_len, chunk_size):
            chunk = b64_data[i : i + chunk_size]
            append_cmd = f"printf '%s' '{chunk}' >> {shlex.quote(remote_b64_file)}"
            chunk_res = self.client.exec_command(session_id, append_cmd, timeout=timeout)
            if chunk_res.exit_code != 0:
                self.client.exec_command(
                    session_id, f"rm -f {shlex.quote(remote_b64_file)}", timeout=5.0
                )
                raise RuntimeError(f"Failed uploading chunk: {chunk_res.output}")
            sent += len(chunk)
            if progress_callback:
                progress_callback(sent, total_len)

        # Step 3: Unpack remotely
        unpack_script = (
            f"__RCLI_TAR=$(mktemp /tmp/rcli_pkg_XXXXXX); "
            f'base64 -d < {shlex.quote(remote_b64_file)} > "$__RCLI_TAR" && '
            f"rm -f {shlex.quote(remote_b64_file)} && "
            f"{unpack_cmd} && "
            f'rm -f "$__RCLI_TAR"'
        )
        unpack_res = self.client.exec_command(session_id, unpack_script, timeout=timeout)
        if unpack_res.exit_code != 0:
            raise RuntimeError(f"Failed to unpack uploaded file remotely: {unpack_res.output}")

    def download(
        self,
        session_id: str,
        remote_path: str,
        local_path: str | Path,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> None:
        """Downloads a remote file or directory from the session to local_path."""
        loc_p = Path(local_path).resolve()

        # Plan B Fast-Path: Use SSH ControlMaster via native SCP if available
        if is_control_master_active(session_id):
            try:
                import subprocess

                from .utils import get_cm_socket_path

                sock = get_cm_socket_path(session_id)
                loc_p.parent.mkdir(parents=True, exist_ok=True)
                scp_cmd = [
                    "scp",
                    "-r",
                    "-o",
                    f"ControlPath={sock}",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "StrictHostKeyChecking=no",
                    f"dummy_host:{remote_path}",
                    str(loc_p),
                ]
                res = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=120.0)
                if res.returncode == 0:
                    if progress_callback:
                        progress_callback(100, 100)
                    return
            except Exception:
                pass  # Fallback to Tar + Base64 stream

        # Fallback Engine: In-band Tar + Base64 stream
        remote_quoted = shlex.quote(remote_path)

        # Pack to tar.gz + base64 with clear sentinel markers
        pack_script = (
            f"if [ ! -e {remote_quoted} ]; then echo '__RCLI_NOT_FOUND__'; exit 44; fi; "
            f"__RCLI_T=$(mktemp /tmp/rcli_dl_XXXXXX); "
            f"if [ -d {remote_quoted} ]; then "
            f'tar -czf "$__RCLI_T" -C {remote_quoted} .; '
            f"else "
            f"__RCLI_P=$(dirname {remote_quoted}); "
            f"__RCLI_B=$(basename {remote_quoted}); "
            f'tar -czf "$__RCLI_T" -C "$__RCLI_P" "$__RCLI_B"; '
            f"fi && "
            f"printf '__B64_START__\\n' && "
            f'base64 < "$__RCLI_T" && '
            f"printf '\\n__B64_END__\\n'; "
            f'rm -f "$__RCLI_T"'
        )

        res = self.client.exec_command(session_id, pack_script, timeout=60.0)
        if res.exit_code == 44 or "__RCLI_NOT_FOUND__" in res.output:
            raise FileNotFoundError(f"Remote path not found: {remote_path}")
        if res.exit_code != 0:
            raise RuntimeError(f"Failed to package remote file: {res.output}")

        # Extract base64 between markers
        if "__B64_START__" not in res.output:
            raise RuntimeError(f"Invalid remote download response: {res.output}")

        _, _, after = res.output.partition("__B64_START__")
        b64_content, _, _ = after.partition("__B64_END__")
        import re

        b64_clean = re.sub(r"[^A-Za-z0-9+/=]", "", b64_content)
        if not b64_clean:
            raise RuntimeError("Received empty payload from remote server")

        try:
            tar_bytes = base64.b64decode(b64_clean)
        except Exception as e:
            raise RuntimeError(f"Failed to decode base64 download payload: {e}") from e

        if progress_callback:
            progress_callback(len(tar_bytes), len(tar_bytes))

        # Extract locally
        single_name = Path(remote_path).name
        extract_tar_gz_bytes(tar_bytes, loc_p, single_file_name=single_name)
