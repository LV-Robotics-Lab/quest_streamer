from setuptools import find_packages, setup

setup(
    name="quest_streamer",
    version="0.1.0",
    description="Stream pose and button data from a Meta Quest / Oculus controller into Python.",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    author="Jingxiang",
    packages=find_packages(exclude=("examples", "examples.*")),
    python_requires=">=3.8",
    install_requires=[
        "numpy",
        "scipy",
    ],
    extras_require={
        "viser": ["viser"],
    },
)
