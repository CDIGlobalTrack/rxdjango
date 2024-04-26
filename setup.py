from setuptools import setup, find_packages
from os import path

cur_dir = path.abspath(path.dirname(__file__))

VERSION = '0.0.9'

setup(
    name="rxdjango",
    author="Luis Fagundes",
    author_email="lhfagundes@gmail.com",
    version=VERSION,
    packages=find_packages(),
    license="LICENSE.md",
    install_requires=[
        'Django>=4.2',
        'motor>=3.3',
        'channels>=4',
        'channels-redis>=4.1',
        'djangorestframework>=3',
        'bson>=0.5',
        'daphne>=4.1.0'
    ],
    url="https://github.com/CDIGlobalTrack/rxdjango",
    include_package_data=True,
    python_requires=">=3.10"
)
