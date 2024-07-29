from sys import platform


if platform == "win32":
    import shutil
    from ctypes.util import find_library

    shutil.copyfile(find_library("libsodium"), "libsodium.dll")
