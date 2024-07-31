from __future__ import annotations

import re
import sys
from itertools import chain
from pathlib import Path

from cx_Freeze import Executable, setup
from packaging.version import VERSION_PATTERN
from pip._internal.network.session import PipSession
from pip._internal.req import parse_requirements
from setuptools import find_packages
from setuptools_scm import Version, get_version

sys.path.insert(0, 'src')
sys.path.insert(0, 'pyipv8')


class TriblerVersion(Version):
    """
    Use "exp" instead of "dev" for the dev tag identifier.
    """

    _regex = re.compile(r"^\s*" + VERSION_PATTERN.replace("(?P<dev_l>dev)", "(?P<dev_l>exp)") + r"\s*$",
                        re.VERBOSE | re.IGNORECASE)


setup(
    name="tribler",
    version=get_version(version_cls=TriblerVersion, normalize=True),
    description="Privacy enhanced BitTorrent client with P2P content discovery",
    long_description=Path("README.rst").read_text(encoding="utf-8"),
    long_description_content_type="text/x-rst",
    author="Tribler Team",
    author_email="info@tribler.org",
    url="https://github.com/Tribler/tribler",
    keywords="BitTorrent client, file sharing, peer-to-peer, P2P, TOR-like network",
    python_requires=">=3.10",
    packages=find_packages(where="src"),
    include_package_data=True,
    install_requires=[str(r.requirement) for r in
                      chain(parse_requirements("requirements.txt", session=PipSession()),
                            parse_requirements("pyipv8/requirements.txt", session=PipSession()))
                      if r is not None],
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Developers",
        "Intended Audience :: End Users/Desktop",
        "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Topic :: Communications :: File Sharing",
        "Topic :: Security :: Cryptography",
        "Operating System :: OS Independent",
    ],
    options={
        "build_exe": {
            "packages": ["aiohttp_apispec", "pkg_resources", "requests", "libtorrent", "ssl"],
            "includes": ["tribler.core", "tribler.ui"],
            "excludes": [".git", "tribler.test_unit", "tribler.test_integration", "tribler.run_unit_tests", "venv"],
            "include_files": ["src/run_tribler.py"],
            "include_msvcr": True,
            "build_exe": "dist/tribler"
        }
    },
    executables=[Executable("src/run_tribler.py", base="gui", icon="build/icons/tribler",
                            target_name="Tribler" if sys.platform != "linux" else "tribler")]
)
