[app]
# Numele aplicației
title = WiiController

# Nume pachet (fără spații, lowercase)
package.name = wiicontroller
package.domain = org.wiimote

# Fișiere sursă
source.dir = .
source.include_exts = py,png,jpg,kv,json,txt,atlas
source.include_patterns = assets/**

# Entry point
source.main = main.py

# Icon aplicație
icon.filename = assets/app_icon/icon.png

# Versiune
version = 0.1

# Cerințe Python / Kivy
requirements = python3,kivy,plyer

# Orientare
orientation = portrait

# Fullscreen (IMPORTANT: boolean, nu "auto")
fullscreen = 1

# =========================
# ANDROID
# =========================

# Permisiuni necesare
android.permissions = INTERNET, ACCESS_NETWORK_STATE, BODY_SENSORS

# API levels
android.api = 33
android.minapi = 27

# NDK (exact ce folosești deja)
android.ndk = 25b

# Arhitecturi suportate
android.archs = arm64-v8a, armeabi-v7a

# Stocare privată (recomandat)
android.private_storage = True

# =========================
# BUILD
# =========================

# Debug log level
log_level = 2
warn_on_root = 0


[buildozer]
# Folder build
build_dir = .buildozer
