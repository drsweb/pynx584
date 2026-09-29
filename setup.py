from setuptools import setup

setup(name='pynx584',
      version='0.8.3',
      description='NX584/NX8E Interface Library and Server',
      author='Dan Smith',
      author_email='dsmith+nx584@danplanet.com',
      url='http://github.com/kk7ds/pynx584',
      packages=['nx584'],
      install_requires=['requests', 'stevedore', 'prettytable', 'pyserial', 'flask'],
      extras_require={
          'mqtt': ['paho-mqtt'],
          'http': ['flask'],
          'client': ['prettytable'],
          'full': ['paho-mqtt', 'flask', 'prettytable'],
      },
      scripts=['nx584_server', 'nx584_client'],
      classifiers=[
          "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
      ],
  )
