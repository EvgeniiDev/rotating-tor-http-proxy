from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

install_requires = [
    "mitmproxy==12.1.2",
    "aiohttp==3.12.15",
    "aiohttp-socks==0.10.1",
]

setup(
    name="rotating-tor-http-proxy",
    version="0.1.0",
    author="GitHub Copilot",
    author_email="copilot@example.com",
    description="Rotating Tor HTTP proxy with mitmproxy integration",
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.12",
    ],
    python_requires=">=3.12",
    install_requires=install_requires,
    extras_require={
        "dev": [
            "pytest>=6.0",
            "ruff>=0.1.0",
        ],
    },
    license="MIT",
    entry_points={
        "console_scripts": [
            "rotating-tor-proxy = main:main",
        ],
    },
)