#!/usr/bin/env python
# -*- coding: utf-8 -*-
from pathlib import Path
from setuptools import setup


requirements = Path(__file__).parent / 'requirements.txt'
with requirements.open() as fp:
    install_requires = fp.read()


setup(
    install_requires=install_requires,
)