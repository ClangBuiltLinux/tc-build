#!/usr/bin/env python3

import hashlib
import re
import subprocess

import tc_build.utils

# When doing verification, read 128MiB at a time
BYTES_TO_READ = 131072


class Tarball:

    def __init__(self):
        self.base_download_url = None
        self.local_location = None
        self.remote_tarball_name = None
        self.remote_checksum_name = ''

    def download(self):
        if not self.local_location:
            raise RuntimeError('No local tarball location specified?')
        if self.local_location.exists():
            return  # Already downloaded

        if not self.base_download_url:
            raise RuntimeError('No tarball download URL specified?')
        if not self.remote_tarball_name:
            self.remote_tarball_name = self.local_location.name

        full_url = f"{self.base_download_url}/{self.remote_tarball_name}"
        tc_build.utils.print_info(f"Downloading {full_url} to {self.local_location}...")
        tc_build.utils.curl(full_url, destination=self.local_location)

        # If there is a remote checksum file, download it, find the checksum
        # for the particular tarball, compute the downloaded file's checksum,
        # and finally compare the two.
        if self.remote_checksum_name:
            checksums = tc_build.utils.curl(f"{self.base_download_url}/{self.remote_checksum_name}")
            if not (match := re.search(
                    fr"([0-9a-f]+)\s+{self.remote_tarball_name}$", checksums, flags=re.M)):
                raise RuntimeError(f"Could not find checksum for {self.remote_tarball_name}?")

            if 'sha256' in self.remote_checksum_name:
                file_hash = hashlib.sha256()
            elif 'sha512' in self.remote_checksum_name:
                file_hash = hashlib.sha512()
            else:
                raise RuntimeError(
                    f"No supported hashlib for {self.remote_checksum_name}, add support for it?")
            with self.local_location.open('rb') as file:
                while (data := file.read(BYTES_TO_READ)):
                    file_hash.update(data)

            computed_checksum = file_hash.hexdigest()
            expected_checksum = match.groups()[0]
            if computed_checksum != expected_checksum:
                raise RuntimeError(
                    f"Computed checksum of {self.local_destination} ('{computed_checksum}') differs from expected checksum ('{expected_checksum}'), remove it and try again?"
                )

    def extract(self, extraction_location):
        if not self.local_location:
            raise RuntimeError('No local tarball location specified?')
        if not self.local_location.exists():
            raise RuntimeError(
                f"Local tarball ('{self.local_location}') could not be found, download it first?")

        extraction_location.mkdir(exist_ok=True, parents=True)
        tar_cmd = [
            'tar',
            '--auto-compress',
            f"--directory={extraction_location}",
            '--extract',
            f"--file={self.local_location}",
            '--strip-components=1',
        ]

        tc_build.utils.print_info(f"Extracting {self.local_location} into {extraction_location}...")
        subprocess.run(tar_cmd, check=True)


class SourceManager:

    def __init__(self, location=None):
        self.location = location
        self.tarball = Tarball()
