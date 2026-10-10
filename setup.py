import os

from setuptools import setup

APP = ["mac_heater.py"]
OPTIONS = {
    "argv_emulation": False,
    "iconfile": "icon.icns",
    "plist": {
        "CFBundleName": "MacHeater",
        "CFBundleDisplayName": "Mac Heater",
        "CFBundleIdentifier": "io.github.userrand6.macheater",  # change to your own
        "CFBundleShortVersionString": os.environ.get("APP_VERSION", "0.0.0"),
        "LSUIElement": True,  # menu bar only, no Dock icon
    },
    "packages": ["rumps", "objc", "Metal"],
}

setup(
    name="MacHeater",
    app=APP,
    options={"py2app": OPTIONS},
)