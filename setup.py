from setuptools import setup, find_packages
from os import path

cur_dir = path.abspath(path.dirname(__file__))

VERSION = '0.1'

# parse requirements
with open(path.join(cur_dir, "requirements.txt"), "r") as f:
    requirements = f.read().split()

setup(
    name="django-react-framework",
    author="Luis Fagundes",
    author_email="lhfagundes@gmail",
    version=VERSION,
    packages=find_packages(),
    license="LICENSE.md",
    install_requires=requirements,
    url="https://github.com/CDIGlobalTrack/django-react-framework",
    include_package_data=True,
    python_requires=">=3.8"
)
