#!/usr/bin/env python3

import subprocess
import sys
import time


def create_gitignore(folder):
    folder.joinpath('.gitignore').write_text('*\n', encoding='utf-8')


def curl(url, capture_output=True, destination=None, text=True):
    curl_cmd = ['curl', '-fLSs']
    if destination:
        curl_cmd += ['-o', destination]
    curl_cmd.append(url)
    return subprocess.run(curl_cmd, capture_output=capture_output, check=True, text=text).stdout


def flush_std_err_out():
    sys.stderr.flush()
    sys.stdout.flush()


def get_duration(start_seconds, end_seconds=None):
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


def libc_is_musl():
    # musl's ldd does not appear to support '--version' directly, as its return
    # code is 1 and it prints all text to stderr. However, it does print the
    # version information so it is good enough. Just 'check=False' it and move
    # on.
    ldd_out = subprocess.run(['ldd', '--version'], capture_output=True, check=False, text=True)
    return 'musl' in (ldd_out.stderr if ldd_out.stderr else ldd_out.stdout)


def print_color(color, string):
    print(f"{color}{string}\033[0m", flush=True)


def print_cyan(msg):
    print_color('\033[01;36m', msg)


def print_header(string):
    border = ''.join(["=" for _ in range(len(string) + 6)])
    print_cyan(f"\n{border}\n== {string} ==\n{border}\n")


def print_info(msg):
    print(f"I: {msg}", flush=True)


def print_warning(msg):
    print_color('\033[01;33m', f"W: {msg}")
