"""Platzhalter in Vorlagen: ``{{name}}`` in beliebigen Textwerten der pass.json.

Steht ein Platzhalter allein in einem Wert (``"value": "{{preis}}"``), wird der Wert
aus den Daten unverändert übernommen - so gehen auch Zahlen und Listen. Sonst wird
er als Text eingesetzt (``"Hallo {{vorname}}!"``).
"""

import re

NAME = r"[A-Za-z_][A-Za-z0-9_]*"
PATTERN = re.compile(r"\{\{\s*(" + NAME + r")\s*\}\}")
WHOLE = re.compile(r"^\{\{\s*(" + NAME + r")\s*\}\}$")


def find(obj):
    """Alle Platzhalter-Namen in ``obj`` (sortiert, ohne Doppelte)."""
    names = set()

    def walk(o):
        if isinstance(o, str):
            names.update(PATTERN.findall(o))
        elif isinstance(o, dict):
            for k, v in o.items():
                walk(k)
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(obj)
    return sorted(names)


def check(placeholders, data):
    """Fehlermeldungen für fehlende oder unbekannte Felder."""
    problems = []
    missing = [n for n in placeholders if n not in data]
    unknown = sorted(k for k in data if k not in placeholders)
    if missing:
        problems.append("Fehlende Felder: " + ", ".join(missing))
    if unknown:
        problems.append("Unbekannte Felder (nicht in der Vorlage): " + ", ".join(unknown))
    for k, v in data.items():
        if isinstance(v, (dict,)) or (isinstance(v, list) and any(isinstance(i, (dict, list)) for i in v)):
            problems.append(f"Feld {k}: nur Text, Zahl, Wahrheitswert oder Liste von Texten erlaubt.")
    return problems


def render(obj, data):
    """Kopie von ``obj`` mit eingesetzten Werten."""
    if isinstance(obj, str):
        m = WHOLE.match(obj)
        if m:
            return data[m.group(1)]
        return PATTERN.sub(lambda m: _text(data[m.group(1)]), obj)
    if isinstance(obj, dict):
        return {render(k, data): render(v, data) for k, v in obj.items()}
    if isinstance(obj, list):
        return [render(v, data) for v in obj]
    return obj


def sample_data(placeholders):
    """Beispielwerte, um eine Vorlage vor der Freigabe zu prüfen."""
    return {n: f"Beispiel {n}" for n in placeholders}


def _text(value):
    if isinstance(value, bool):
        return "ja" if value else "nein"
    if isinstance(value, list):
        return ", ".join(_text(v) for v in value)
    return str(value)
