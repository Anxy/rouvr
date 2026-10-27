"""Setup script for hyeve_core package."""

from setuptools import find_packages, setup  # type: ignore[import-untyped]

setup(
    name="hyeve-core",
    version="0.1.0",
    description="Core components and utilities for the Hyeve multi-tenant SaaS ERP platform",
    packages=find_packages(exclude=["tests", "tests.*", "dist", "*.egg-info"]),
    python_requires=">=3.9",
    install_requires=[
        # Core dependencies that hyeve_core needs
        "pydantic>=2.0.0",
    ],
)
