import os
from setuptools import setup


# pip workaround
os.chdir(os.path.abspath(os.path.dirname(__file__)))


with open('README.rst') as fp:
    description = fp.read()
req = [
    'Twisted~=16.3.0',
    'pyOpenSSL',
    'service-identity',
    'certifi',
    'markdown2']
setup(name='matrix-gitter-twisted',
      version='0.1',
      packages=['matrix_gitter'],
      entry_points={
          'console_scripts': [
              'matrix_gitter = matrix_gitter.main:main']},
      install_requires=req,
      description="Matrix-Gitter bridge using Twisted",
      author="Remi Rampin",
      author_email='remirampin@gmail.com',
      maintainer="Remi Rampin",
      maintainer_email='remirampin@gmail.com',
      long_description=description,
      license='BSD',
      keywords=['matrix', 'twisted'],
      classifiers=[
          'Development Status :: 4 - Beta',
          'License :: OSI Approved :: BSD License',
          'Programming Language :: Python :: 2.7'])
