from setuptools import setup

APP = ["mac_heater.py"]
OPTIONS = {
    "argv_emulation": False,
    "plist": {
        "CFBundleName": "MacHeater",
        "CFBundleIdentifier": "com.example.macheater",  # change to your own
        "LSUIElement": True,  # menu bar only, no Dock icon
    },
    "packages": ["rumps", "objc", "Metal"],
    "iconfile": "icon.icns",
}

setup(
    app=APP,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)