from __future__ import annotations

import re
import subprocess
import sys
import time
from collections.abc import Sequence
from os import PathLike
from pathlib import Path
from typing import Union

UNINIT_PATH = Path('/uninitialized')
ValidCmdItem = Union[bytes, PathLike, str]
ValidCmd = Sequence[ValidCmdItem]
CmdList = list[ValidCmdItem]


def cpu_is_apple_silicon() -> bool:
    cpuinfo = Path('/proc/cpuinfo').read_text(encoding='utf-8')
    if match := re.search(r"implementer\s+:\s+(\w+)", cpuinfo):
        # https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/arch/arm64/include/asm/cputype.h?h=v6.17-rc4#n62
        return match.groups()[0] == '0x61'
    # If we cannot prove that it is Apple Silicon, we assume it is not
    return False


def create_gitignore(folder: Path) -> None:
    folder.joinpath('.gitignore').write_text('*\n', encoding='utf-8')


def curl(
    url: str,
    capture_output: bool = True,
    destination: str | Path | None = None,
    text: bool | None = True,
) -> str:
    curl_cmd: list[Path | str] = ['curl', '-fLSs']
    if destination:
        curl_cmd += ['-o', destination]
    curl_cmd.append(url)
    return subprocess.run(curl_cmd, capture_output=capture_output, check=True, text=text).stdout


def flush_std_err_out() -> None:
    sys.stderr.flush()
    sys.stdout.flush()


def get_duration(start_seconds: float, end_seconds: float | None = None) -> str:
    if not end_seconds:
        end_seconds = time.time()
    seconds = int(end_seconds - start_seconds)
    days, seconds = divmod(seconds, 60 * 60 * 24)
    hours, seconds = divmod(seconds, 60 * 60)
    minutes, seconds = divmod(seconds, 60)

    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")

    return ' '.join(parts)


def libc_is_musl() -> bool:
    # musl's ldd does not appear to support '--version' directly, as its return
    # code is 1 and it prints all text to stderr. However, it does print the
    # version information so it is good enough. Just 'check=False' it and move
    # on.
    ldd_out = subprocess.run(['ldd', '--version'], capture_output=True, check=False, text=True)
    return 'musl' in (ldd_out.stderr or ldd_out.stdout)


def path_is_set(path: Path) -> bool:
    return path != UNINIT_PATH


def print_color(color: str, string: str) -> None:
    print(f"{color}{string}\033[0m", flush=True)


def print_cyan(msg: str) -> None:
    print_color('\033[01;36m', msg)


def print_header(string: str) -> None:
    border = ''.join(["=" for _ in range(len(string) + 6)])
    print_cyan(f"\n{border}\n== {string} ==\n{border}\n")


def print_info(msg: str) -> None:
    print(f"I: {msg}", flush=True)


def print_warning(msg: str) -> None:
    print_color('\033[01;33m', f"W: {msg}")
