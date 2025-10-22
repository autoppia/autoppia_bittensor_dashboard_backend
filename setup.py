# setup.py
#!/usr/bin/env python3
from setuptools import setup, find_packages

setup(
    name="leaderboard-api",
    version="0.1.1",  # bump
    packages=find_packages(exclude=["tests", "examples"]),
    install_requires=[
        "python-dotenv",
        "SQLAlchemy>=2.0",
        "asyncpg",
        "boto3",
    ],
    entry_points={
        "console_scripts": [
            "iwap = scripts.iwap:main",
            "iwa = scripts.iwap:main",
            "iwaseed = scripts.seed_round:main",
        ],
    },
    python_requires=">=3.9",
)
