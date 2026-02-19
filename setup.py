# Minimal setup.py for C extension support
# All metadata is in pyproject.toml
from setuptools import Extension, setup

delta_utils = Extension(
    'rxdjango.utils.delta_utils_c',
    sources=['rxdjango/utils/delta_utils.c'],
)

setup(ext_modules=[delta_utils])
