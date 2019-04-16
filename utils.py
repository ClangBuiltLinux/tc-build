#!/usr/bin/env python3

import colorama

def header(string):
    print(colorama.Fore.RED + colorama.Style.BRIGHT)
    for x in range(0, len(string) + 6):
        print("=", end="")
    print()
    print("== " + string + " ==")
    for x in range(0, len(string) + 6):
        print("=", end="")
    print()
    print(colorama.Style.RESET_ALL)
