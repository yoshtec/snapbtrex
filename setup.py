import setuptools

VERSION = "2.0.2"

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setuptools.setup(
    name="snapbtrex",
    version=VERSION,
    author="Yoshtec",
    author_email="yoshtec@gmail.com",
    description="snapbtrex is a small utility that keeps snapshots of btrfs filesystems "
    "and optionally send them to a remote system or syncs them locally.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/yoshtec/snapbtrex",
    entry_points={
        "console_scripts": [
            "snapbtrex = snapbtrex:main",
        ]
    },
    packages=setuptools.find_packages(),
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: BSD License",
        "Environment :: Console",
        "Operating System :: POSIX :: Linux",
    ],
    keywords=[
        "btrfs",
        "snapshot",
        "backups",
        "backup",
        "btrfs incremental-backups",
        "btrfs-filesystem",
        "transferring-snapshots",
        "btrfs send-receive",
    ],
    python_requires=">=3.5",
)
