import os
import json
from pathlib import Path
from typing import Dict
from PyQt5.QtCore import QLocale

LOCALES_DIR = Path(__file__).resolve().parent / "locales"

TRANSLATIONS: Dict[str, Dict[str, str]] = {}

def load_translations():
    """Dynamically loads all JSON translation files from the locales directory."""
    global TRANSLATIONS
    if LOCALES_DIR.exists():
        for json_file in LOCALES_DIR.glob("*.json"):
            lang_code = json_file.stem.lower()
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    TRANSLATIONS[lang_code] = json.load(f)
            except Exception as e:
                print(f"Error loading translation file {json_file}: {e}")

load_translations()

def get_current_language() -> str:
    """Detects system language from environment or QLocale ('pl' or 'en')."""
    lang_env = os.environ.get("APP_LANG") or os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG", "")
    lang_env = lang_env.lower()
    if lang_env.startswith("pl"):
        return "pl"
    if lang_env.startswith("en"):
        return "en"

    try:
        loc = QLocale.system().name().lower()
        if loc.startswith("pl"):
            return "pl"
    except Exception:
        pass

    return "en"

_CURRENT_LANG = get_current_language()

def set_language(lang: str):
    """Overrides the active language ('en' or 'pl')."""
    global _CURRENT_LANG
    _CURRENT_LANG = "pl" if lang.lower().startswith("pl") else "en"

def tr(key: str, **kwargs) -> str:
    """Translates a message key into the active language with optional parameter formatting."""
    lang_dict = TRANSLATIONS.get(_CURRENT_LANG, TRANSLATIONS.get("en", {}))
    template = lang_dict.get(key, TRANSLATIONS.get("en", {}).get(key, key))
    if kwargs:
        return template.format(**kwargs)
    return template
