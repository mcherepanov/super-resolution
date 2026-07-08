from setuptools import setup, find_packages

setup(
    name="FlashSRInfer",
    version="0.1",
    packages=find_packages(),
    install_requires=[
        "numpy",
        "tqdm",
        "psutil",
        "pyyaml",
        "matplotlib",
        "librosa",
        "einops",
        "soundfile",
        "scipy",
    ],
    description="Redistribution of FlashSR audio super-resolution inference "
                "(original authors: Jaekwon Im and Juhan Nam, KAIST)",
    keywords="audio super-resolution speech enhancement",
)
