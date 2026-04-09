#!/usr/bin/env python3

from pathlib import Path
import shlex
import shutil
import subprocess
from typing import Optional

import tc_build.utils


class Folders:
    def __init__(self) -> None:
        self.build: Path = tc_build.utils.UNINIT_PATH
        self.install: Path = tc_build.utils.UNINIT_PATH
        self.source: Path = tc_build.utils.UNINIT_PATH


class Builder:
    def __init__(self) -> None:
        self.folders: Folders = Folders()
        self.show_commands: bool = False

    def build(self) -> None:
        raise NotImplementedError

    def clean_build_folder(self) -> None:
        if not tc_build.utils.path_is_set(self.folders.build):
            raise RuntimeError('No build folder set?')

        if self.folders.build.exists():
            if self.folders.build.is_dir():
                shutil.rmtree(self.folders.build)
            else:
                self.folders.build.unlink()

    def make_build_folder(self) -> None:
        if not tc_build.utils.path_is_set(self.folders.build):
            raise RuntimeError('No build folder set?')

        self.folders.build.mkdir(parents=True)

    def run_cmd(
        self, cmd: tc_build.utils.ValidCmd, capture_output: bool = False, cwd: Optional[Path] = None
    ) -> subprocess.CompletedProcess:
        if self.show_commands:
            # Acts sort of like 'set -x' in bash
            print(f"$ {' '.join([shlex.quote(str(elem)) for elem in cmd])}", flush=True)
        try:
            return subprocess.run(
                cmd, capture_output=capture_output, check=True, cwd=cwd, text=True
            )
        except subprocess.CalledProcessError as err:
            if capture_output:
                if err.stdout:
                    print(err.stdout)
                if err.stderr:
                    print(err.stderr)
            raise err
