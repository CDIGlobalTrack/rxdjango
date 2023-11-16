from setuptools import setup, find_packages
from os import path

cur_dir = path.abspath(path.dirname(__file__))

VERSION = '0.0.8'

# parse requirements
with open(path.join(cur_dir, "requirements.txt"), "r") as f:
    requirements = f.read().split('\n')

requirements = [ x for x in requirements if x and not x.startswith('#') ]

setup(
    name="rxdjango",
    author="Luis Fagundes",
    author_email="lhfagundes@gmail.com",
    version=VERSION,
    packages=find_packages(),
    license="LICENSE.md",
    install_requires=requirements,
    url="https://github.com/CDIGlobalTrack/rxdjango",
    include_package_data=True,
    python_requires=">=3.10"
)
