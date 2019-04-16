#!/usr/bin/env python

import os


def main():
    root = os.path.dirname(os.path.realpath(__file__))
    os.chdir(root)


if __name__ == '__main__':
    main()
