#!/usr/bin/env python3
from setuptools import setup, find_packages

setup(
    name="iwa-cli",
    version="1.0.0",
    author="Autoppia Team",
    description="IWAP - Simplified Interactive Wrapper for Autoppia",
    packages=find_packages(exclude=["tests", "examples"]),
    install_requires=[
        "python-dotenv",
        "SQLAlchemy>=2.0",
        "asyncpg",
        "boto3",
    ],
    entry_points={
        "console_scripts": [
            "iwa = scripts.iwap:main",
        ],
    },
    python_requires=">=3.9",
)
