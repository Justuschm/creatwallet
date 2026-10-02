"""Validation of pass.json content and the files of a pass bundle.

Every issue has a level:

* ``error``   - Wallet will reject the pass (or the build cannot work).
* ``warning`` - the pass installs, but something will not look/work as intended
                (e.g. a poster ticket silently falls back to the legacy style).
* ``info``    - hints.
"""

import re
from dataclasses import dataclass

from . import spec
from .png import png_size

ERROR, WARNING, INFO = "error", "warning", "info"

_COLOR_RE = re.compile(r"^rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)$")
# W3C / ISO 8601 date with hours and minutes and a time zone designator.
_DATE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{1,2}:?\d{2})$")
_IMAGE_RE = re.compile(r"^(?:[^/]+\.lproj/)?([A-Za-z]+)(@[23]x)?\.png$")


@dataclass
class Issue:
    level: str
    path: str
    message: str

    def __str__(self):
        return f"[{self.level}] {self.path}: {self.message}"


class _Collector:
    def __init__(self):
        self.issues = []

    def add(self, level, path, message):
        self.issues.append(Issue(level, path, message))

    def error(self, path, message):
        self.add(ERROR, path, message)

    def warning(self, path, message):
        self.add(WARNING, path, message)

    def info(self, path, message):
        self.add(INFO, path, message)


def is_color(value):
    m = _COLOR_RE.match(value) if isinstance(value, str) else None
    return bool(m) and all(0 <= int(c) <= 255 for c in m.groups())


def is_date(value):
    return isinstance(value, str) and bool(_DATE_RE.match(value))


def validate(pass_data, files=None):
    """Validate a pass.

    ``pass_data`` is the parsed pass.json. ``files`` maps bundle-relative
    file names (e.g. ``"icon@2x.png"``, ``"de.lproj/pass.strings"``) to
    their bytes. If ``files`` is None, image checks are skipped.
    Returns a list of :class:`Issue`.
    """
    c = _Collector()
    if not isinstance(pass_data, dict):
        c.error("pass.json", "Die Wurzel muss ein JSON-Objekt sein.")
        return c.issues

    _check_top_level(c, pass_data)
    styles = _check_styles(c, pass_data)
    _check_fields(c, pass_data, styles)
    _check_barcodes(c, pass_data)
    _check_relevance(c, pass_data)
    _check_nfc(c, pass_data)
    semantics = pass_data.get("semantics")
    if semantics is not None:
        _check_semantics(c, semantics, "semantics")
    _check_schemes(c, pass_data, styles)
    _check_upcoming(c, pass_data, styles)
    if files is not None:
        _check_images(c, pass_data, files)
    return c.issues


def has_errors(issues):
    return any(i.level == ERROR for i in issues)


# --------------------------------------------------------------------------
# top level

def _check_top_level(c, p):
    for key in spec.REQUIRED_TOP_LEVEL:
        if key not in p or p[key] in ("", None):
            c.error(key, "Pflichtfeld fehlt.")
    if "formatVersion" in p and p["formatVersion"] != 1:
        c.error("formatVersion", "Muss die Zahl 1 sein.")
    pti = p.get("passTypeIdentifier")
    if isinstance(pti, str) and pti and not pti.startswith("pass."):
        c.error("passTypeIdentifier", "Muss mit 'pass.' beginnen (z. B. pass.com.example.ticket).")
    sn = p.get("serialNumber")
    if sn is not None and (not isinstance(sn, str) or len(sn) < 1):
        c.error("serialNumber", "Muss ein nicht-leerer String sein.")

    for key in spec.STRING_KEYS:
        if key in p and not isinstance(p[key], str):
            c.error(key, "Muss ein String sein.")
    for key in spec.BOOL_KEYS:
        if key in p and not isinstance(p[key], bool):
            c.error(key, "Muss true oder false sein.")
    for key in spec.COLOR_KEYS:
        if key in p and not is_color(p[key]):
            c.error(key, "Farbe muss im Format rgb(R, G, B) mit Werten 0-255 angegeben werden.")
    for key in ("expirationDate", "relevantDate"):
        if key in p and not is_date(p[key]):
            c.error(key, "Datum muss ISO 8601 mit Uhrzeit und Zeitzone sein, z. B. 2026-10-02T20:00+02:00.")
    if "relevantDate" in p:
        c.info("relevantDate", "Veraltet seit iOS 18 - besser 'relevantDates' verwenden.")
    for key in ("associatedStoreIdentifiers", "auxiliaryStoreIdentifiers"):
        if key in p and not (isinstance(p[key], list) and all(isinstance(i, int) for i in p[key])):
            c.error(key, "Muss eine Liste von App-Store-IDs (Zahlen) sein.")
    ws = p.get("webServiceURL")
    if ws is not None:
        if isinstance(ws, str) and not ws.startswith("https://"):
            c.warning("webServiceURL", "Muss in Produktion HTTPS verwenden.")
        token = p.get("authenticationToken")
        if not isinstance(token, str) or len(token) < 16:
            c.error("authenticationToken", "Bei webServiceURL Pflicht, mindestens 16 Zeichen.")
    if "logoText" in p and "eventLogoText" not in p and "posterEventTicket" in p.get("preferredStyleSchemes", []):
        c.info("logoText", "Wird bei Poster-Event-Tickets ignoriert - dort 'eventLogoText' verwenden.")

    for key in p:
        if key not in spec.KNOWN_TOP_LEVEL:
            c.warning(key, "Unbekannter Schlüssel - Tippfehler? Wallet ignoriert ihn.")


def _check_styles(c, p):
    classic = [s for s in spec.CLASSIC_STYLES if s in p]
    styles = classic + [s for s in spec.POSTER_STYLES if s in p]
    if not classic:
        if "posterGeneric" in p:
            c.warning("posterGeneric", "Ohne zusätzlichen klassischen Stil (z. B. 'generic') sieht man den "
                      "Pass auf Geräten vor iOS 27 nicht.")
        else:
            c.error("pass.json", "Es fehlt ein Pass-Stil: " + ", ".join(spec.ALL_STYLES) + ".")
    if len(classic) > 1:
        c.error("pass.json", "Nur ein klassischer Pass-Stil erlaubt, gefunden: " + ", ".join(classic) + ".")
    for style in styles:
        if not isinstance(p[style], dict):
            c.error(style, "Muss ein Objekt sein.")
    bp = p.get("boardingPass")
    if isinstance(bp, dict):
        tt = bp.get("transitType")
        if tt is None:
            c.error("boardingPass.transitType", "Pflichtfeld bei Boarding-Pässen.")
        elif tt not in spec.TRANSIT_TYPES:
            c.error("boardingPass.transitType", "Ungültig. Erlaubt: " + ", ".join(spec.TRANSIT_TYPES))
    return [s for s in styles if isinstance(p[s], dict)]


# --------------------------------------------------------------------------
# fields

def _check_fields(c, p, styles):
    for style in styles:
        keys = set()  # keys must be unique within a style dictionary
        for group in spec.FIELD_GROUPS:
            fields = p[style].get(group)
            if fields is None:
                continue
            path = f"{style}.{group}"
            if not isinstance(fields, list):
                c.error(path, "Muss eine Liste sein.")
                continue
            if group == "footerFields" and style != "posterGeneric":
                c.warning(path, "footerFields gibt es nur bei posterGeneric.")
            limit = spec.FIELD_LIMITS.get(style, {}).get(group)
            if limit and len(fields) > limit:
                c.warning(path, f"{len(fields)} Felder - Wallet zeigt in diesem Stil höchstens {limit} an.")
            for i, field in enumerate(fields):
                _check_field(c, field, f"{path}[{i}]", group, keys)


def _check_field(c, f, path, group, keys):
    if not isinstance(f, dict):
        c.error(path, "Feld muss ein Objekt sein.")
        return
    key = f.get("key")
    if not isinstance(key, str) or not key:
        c.error(path + ".key", "Pflichtfeld (eindeutiger String).")
    elif key in keys:
        c.error(path + ".key", f"Schlüssel '{key}' ist doppelt - Feldschlüssel müssen eindeutig sein.")
    else:
        keys.add(key)
    if "value" not in f:
        c.error(path + ".value", "Pflichtfeld.")
    elif not isinstance(f["value"], (str, int, float)) or isinstance(f["value"], bool):
        c.error(path + ".value", "Muss String, Zahl oder Datum (ISO 8601) sein.")
    for name, allowed in (("dateStyle", spec.DATE_STYLES), ("timeStyle", spec.DATE_STYLES),
                          ("numberStyle", spec.NUMBER_STYLES), ("textAlignment", spec.TEXT_ALIGNMENTS)):
        if name in f and f[name] not in allowed:
            c.error(f"{path}.{name}", "Ungültig. Erlaubt: " + ", ".join(allowed))
    if ("dateStyle" in f or "timeStyle" in f) and not is_date(f.get("value")):
        c.error(path + ".value", "Bei dateStyle/timeStyle muss value ein ISO-8601-Datum mit Zeitzone sein.")
    if "textAlignment" in f and group in ("primaryFields", "backFields"):
        c.warning(path + ".textAlignment", "Wird bei primary- und backFields nicht unterstützt.")
    if "currencyCode" in f:
        if not (isinstance(f["currencyCode"], str) and re.fullmatch(r"[A-Z]{3}", f["currencyCode"])):
            c.error(path + ".currencyCode", "ISO-4217-Code wie EUR erwartet.")
        if not isinstance(f.get("value"), (int, float)):
            c.error(path + ".value", "Bei currencyCode muss value eine Zahl sein.")
    if "changeMessage" in f and "%@" not in str(f["changeMessage"]):
        c.error(path + ".changeMessage", "Muss den Platzhalter %@ enthalten.")
    if "dataDetectorTypes" in f:
        dd = f["dataDetectorTypes"]
        if not isinstance(dd, list) or any(d not in spec.DATA_DETECTORS for d in dd):
            c.error(path + ".dataDetectorTypes", "Erlaubt: " + ", ".join(spec.DATA_DETECTORS))


# --------------------------------------------------------------------------
# barcodes, relevance, nfc

def _check_barcode(c, b, path):
    if not isinstance(b, dict):
        c.error(path, "Muss ein Objekt sein.")
        return None
    for key in ("format", "message", "messageEncoding"):
        if key not in b:
            c.error(f"{path}.{key}", "Pflichtfeld.")
    fmt = b.get("format")
    if fmt is not None and fmt not in spec.BARCODE_FORMATS:
        c.error(path + ".format", "Ungültig. Erlaubt: " + ", ".join(spec.BARCODE_FORMATS))
    if fmt == "PKBarcodeFormatEAN13" and not re.fullmatch(r"\d{12,13}", str(b.get("message", ""))):
        c.error(path + ".message", "EAN-13 braucht 12 bzw. 13 Ziffern.")
    if fmt == "PKBarcodeFormatI2of5" and not re.fullmatch(r"(\d\d)+", str(b.get("message", ""))):
        c.error(path + ".message", "Interleaved 2 of 5 braucht eine gerade Anzahl Ziffern.")
    return fmt


def _check_barcodes(c, p):
    if "barcode" in p:
        c.info("barcode", "Veraltet - 'barcodes' (Liste) verwenden.")
        _check_barcode(c, p["barcode"], "barcode")
    barcodes = p.get("barcodes")
    if barcodes is None:
        return
    if isinstance(barcodes, dict):  # Apple's own sample does this; Wallet expects a list.
        c.error("barcodes", "Muss eine Liste von Barcode-Objekten sein, nicht ein einzelnes Objekt.")
        return
    if not isinstance(barcodes, list):
        c.error("barcodes", "Muss eine Liste sein.")
        return
    formats = [_check_barcode(c, b, f"barcodes[{i}]") for i, b in enumerate(barcodes)]
    if formats and formats[0] not in spec.LEGACY_BARCODE_FORMATS and formats[0] in spec.BARCODE_FORMATS:
        if not any(f in spec.LEGACY_BARCODE_FORMATS for f in formats[1:]):
            c.warning("barcodes", f"{formats[0]} gibt es erst ab iOS 27. Füge danach einen QR-, PDF417-, "
                      "Aztec- oder Code128-Barcode als Fallback für ältere Geräte hinzu.")


def _check_relevance(c, p):
    rd = p.get("relevantDates")
    if rd is not None:
        if not isinstance(rd, list):
            c.error("relevantDates", "Muss eine Liste sein.")
        else:
            for i, entry in enumerate(rd):
                path = f"relevantDates[{i}]"
                if not isinstance(entry, dict):
                    c.error(path, "Muss ein Objekt sein.")
                    continue
                for key in ("date", "startDate", "endDate"):
                    if key in entry and not is_date(entry[key]):
                        c.error(f"{path}.{key}", "ISO-8601-Datum mit Uhrzeit und Zeitzone erwartet.")
                if "startDate" in entry and "endDate" not in entry:
                    c.error(path + ".endDate", "Pflicht, wenn startDate gesetzt ist.")
                if not any(k in entry for k in ("date", "startDate")):
                    c.error(path, "Braucht 'date' oder 'startDate'/'endDate'.")
    for name, limit in (("locations", 10), ("beacons", 10)):
        items = p.get(name)
        if items is None:
            continue
        if not isinstance(items, list):
            c.error(name, "Muss eine Liste sein.")
            continue
        if len(items) > limit:
            c.warning(name, f"Wallet verwendet nur die ersten {limit} Einträge.")
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                c.error(f"{name}[{i}]", "Muss ein Objekt sein.")
            elif name == "locations":
                _check_location(c, item, f"{name}[{i}]")
            elif "proximityUUID" not in item:
                c.error(f"{name}[{i}].proximityUUID", "Pflichtfeld.")


def _check_location(c, loc, path):
    if not isinstance(loc, dict):
        c.error(path, "Muss ein Objekt mit latitude/longitude sein.")
        return
    for key, bound in (("latitude", 90), ("longitude", 180)):
        v = loc.get(key)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            c.error(f"{path}.{key}", "Pflichtfeld (Zahl).")
        elif abs(v) > bound:
            c.error(f"{path}.{key}", f"Muss zwischen -{bound} und {bound} liegen.")


def _check_nfc(c, p):
    nfc = p.get("nfc")
    if nfc is None:
        return
    if not isinstance(nfc, dict):
        c.error("nfc", "Muss ein Objekt sein.")
        return
    if "message" not in nfc:
        c.error("nfc.message", "Pflichtfeld.")
    elif len(str(nfc["message"]).encode()) > 64:
        c.warning("nfc.message", "Länger als 64 Bytes - wird abgeschnitten.")
    if "encryptionPublicKey" not in nfc:
        c.error("nfc.encryptionPublicKey", "Pflichtfeld.")
    c.info("nfc", "NFC-Pässe brauchen ein spezielles Zertifikat/Entitlement von Apple.")


# --------------------------------------------------------------------------
# semantic tags

def _check_semantics(c, s, path):
    if not isinstance(s, dict):
        c.error(path, "Muss ein Objekt sein.")
        return
    for key, value in s.items():
        p = f"{path}.{key}"
        if key not in spec.KNOWN_SEMANTIC_TAGS:
            c.warning(p, "Unbekannter semantischer Tag.")
        elif key in spec.SEMANTIC_DATE_KEYS and not is_date(value):
            c.error(p, "ISO-8601-Datum mit Uhrzeit und Zeitzone erwartet.")
    if "eventType" in s and s["eventType"] not in spec.EVENT_TYPES:
        c.error(path + ".eventType", "Erlaubt: " + ", ".join(spec.EVENT_TYPES))
    for key, allowed in (("passengerCapabilities", spec.PASSENGER_CAPABILITIES),
                         ("passengerEligibleSecurityPrograms", spec.SECURITY_PROGRAMS),
                         ("departureLocationSecurityPrograms", spec.SECURITY_PROGRAMS),
                         ("destinationLocationSecurityPrograms", spec.SECURITY_PROGRAMS),
                         ("passengerServiceSSRs", spec.SERVICE_SSRS),
                         ("passengerInformationSSRs", spec.INFORMATION_SSRS)):
        if key in s:
            if not isinstance(s[key], list):
                c.error(f"{path}.{key}", "Muss eine Liste sein.")
            else:
                bad = [v for v in s[key] if v not in allowed]
                if bad:
                    c.warning(f"{path}.{key}", f"Von Wallet nicht unterstützt: {', '.join(map(str, bad))}. "
                              "Erlaubt: " + ", ".join(allowed))
    for key in ("performerNames", "artistIDs", "albumIDs", "playlistIDs", "loungePlaceIDs",
                "passengerAirlineSSRs"):
        if key in s and not (isinstance(s[key], list) and all(isinstance(v, str) for v in s[key])):
            c.error(f"{path}.{key}", "Muss eine Liste von Strings sein.")
    if "flightNumber" in s and (not isinstance(s["flightNumber"], int) or isinstance(s["flightNumber"], bool)):
        c.error(path + ".flightNumber", "Muss eine Zahl sein (nur der numerische Teil, z. B. 123).")
    if "duration" in s and not isinstance(s["duration"], (int, float)):
        c.error(path + ".duration", "Dauer in Sekunden (Zahl).")
    for key in ("venueLocation", "departureLocation", "destinationLocation"):
        if key in s:
            _check_location(c, s[key], f"{path}.{key}")
    for key in ("balance", "totalPrice"):
        if key in s:
            amt = s[key]
            if not isinstance(amt, dict) or not isinstance(amt.get("amount"), str) or "currencyCode" not in amt:
                c.error(f"{path}.{key}", 'Objekt {"amount": "12.50", "currencyCode": "EUR"} erwartet.')
    if "passengerName" in s and not isinstance(s["passengerName"], dict):
        c.error(path + ".passengerName", 'Objekt wie {"givenName": "...", "familyName": "..."} erwartet.')
    if "eventStartDateInfo" in s:
        info = s["eventStartDateInfo"]
        if not isinstance(info, dict):
            c.error(path + ".eventStartDateInfo", "Muss ein Objekt sein.")
        elif "date" in info and not is_date(info["date"]):
            c.error(path + ".eventStartDateInfo.date", "ISO-8601-Datum mit Zeitzone erwartet.")
    if "seats" in s:
        seats = s["seats"]
        if not isinstance(seats, list):
            c.error(path + ".seats", "Muss eine Liste von Sitz-Objekten sein.")
        else:
            for i, seat in enumerate(seats):
                if not isinstance(seat, dict):
                    c.error(f"{path}.seats[{i}]", "Muss ein Objekt sein.")
                elif "seatSectionColor" in seat and not is_color(seat["seatSectionColor"]):
                    c.error(f"{path}.seats[{i}].seatSectionColor", "Format rgb(R, G, B) erwartet.")
    if "wifiAccess" in s:
        wifi = s["wifiAccess"]
        if not isinstance(wifi, list) or any(not isinstance(w, dict) or "ssid" not in w or "password" not in w
                                             for w in wifi):
            c.error(path + ".wifiAccess", 'Liste von {"ssid": ..., "password": ...} erwartet.')


# --------------------------------------------------------------------------
# new styles (preferredStyleSchemes)

def _check_schemes(c, p, styles):
    schemes = p.get("preferredStyleSchemes")
    sem = p.get("semantics") if isinstance(p.get("semantics"), dict) else {}
    if schemes is None:
        schemes = []
    elif not isinstance(schemes, list) or not all(isinstance(s, str) for s in schemes):
        c.error("preferredStyleSchemes", "Muss eine Liste von Strings sein.")
        schemes = []
    for s in schemes:
        if s not in spec.STYLE_SCHEMES:
            c.warning("preferredStyleSchemes", f"Unbekanntes Schema '{s}'. Bekannt: " + ", ".join(spec.STYLE_SCHEMES))
        elif spec.STYLE_SCHEMES[s] not in styles:
            c.error("preferredStyleSchemes", f"Schema '{s}' braucht den Stil '{spec.STYLE_SCHEMES[s]}'.")

    if "posterEventTicket" in schemes and "eventTicket" in styles:
        required = list(spec.POSTER_EVENT_REQUIRED)
        event_type = sem.get("eventType")
        if event_type == "PKEventTypeSports":
            required += spec.POSTER_EVENT_SPORTS_REQUIRED
        elif event_type == "PKEventTypeLivePerformance":
            required += spec.POSTER_EVENT_LIVE_REQUIRED
        elif event_type is None:
            c.warning("semantics.eventType", "Für Poster-Tickets empfohlen: PKEventTypeSports oder "
                      "PKEventTypeLivePerformance (nur dafür gibt es das Poster-Layout).")
        missing = [k for k in required if k not in sem]
        if missing:
            c.warning("semantics", "Poster-Event-Ticket fällt auf klassisches Ticket zurück - es fehlen: "
                      + ", ".join(missing))
        for k in ("eventStartDate", "eventStartDateInfo"):
            if k not in sem:
                c.info("semantics." + k, "Für Poster-Event-Tickets empfohlen.")
        if "barcodes" in p or "barcode" in p:
            c.info("barcodes", "Laut Apple sind Poster-Event-Tickets nicht für Tickets gedacht, die zwingend "
                   "einen Barcode zum Einlass brauchen - auf dem Gerät testen.")

    if "semanticBoardingPass" in schemes and "boardingPass" in styles:
        if p["boardingPass"].get("transitType") != "PKTransitTypeAir":
            c.error("boardingPass.transitType", "Semantische Boarding-Pässe brauchen PKTransitTypeAir.")
        missing = [k for k in spec.SEMANTIC_BOARDING_REQUIRED if k not in sem]
        if missing:
            c.warning("semantics", "Semantischer Boarding-Pass fällt auf klassischen zurück - es fehlen: "
                      + ", ".join(missing))

    if "posterEventTicket" not in schemes:
        used = [k for k in spec.POSTER_EVENT_ONLY_KEYS if k in p]
        if used:
            c.info(", ".join(used), "Wirkt nur bei Poster-Event-Tickets (preferredStyleSchemes: posterEventTicket).")
    if "boardingPass" not in styles:
        used = [k for k in spec.BOARDING_SERVICE_KEYS if k in p]
        if used:
            c.info(", ".join(used), "Wirkt nur bei Boarding-Pässen.")


def _check_upcoming(c, p, styles):
    entries = p.get("upcomingPassInformation")
    if entries is None:
        return
    if "eventTicket" not in styles:
        c.warning("upcomingPassInformation", "Wird nur bei Event-Tickets angezeigt.")
    if not isinstance(entries, list):
        c.error("upcomingPassInformation", "Muss eine Liste sein.")
        return
    ids = set()
    for i, e in enumerate(entries):
        path = f"upcomingPassInformation[{i}]"
        if not isinstance(e, dict):
            c.error(path, "Muss ein Objekt sein.")
            continue
        for key in ("identifier", "name", "type"):
            if not e.get(key):
                c.error(f"{path}.{key}", "Pflichtfeld.")
        if e.get("identifier") in ids:
            c.error(path + ".identifier", "Muss eindeutig sein.")
        ids.add(e.get("identifier"))
        if "type" in e and e["type"] not in spec.UPCOMING_ENTRY_TYPES:
            c.error(path + ".type", "Erlaubt: " + ", ".join(spec.UPCOMING_ENTRY_TYPES))
        if "isActive" in e and not isinstance(e["isActive"], bool):
            c.error(path + ".isActive", "Muss true oder false sein.")
        di = e.get("dateInformation")
        if di is not None:
            if not isinstance(di, dict):
                c.error(path + ".dateInformation", "Muss ein Objekt sein.")
            elif "date" in di and not is_date(di["date"]):
                c.error(path + ".dateInformation.date", "ISO-8601-Datum mit Zeitzone erwartet.")
        urls = e.get("URLs")
        if isinstance(urls, dict):
            for key in urls:
                if key not in spec.UPCOMING_URL_KEYS:
                    c.warning(f"{path}.URLs.{key}", "Unbekannter URL-Schlüssel.")
        if "semantics" in e:
            _check_semantics(c, e["semantics"], path + ".semantics")
        images = e.get("images")
        if isinstance(images, dict):
            for name, img in images.items():
                ipath = f"{path}.images.{name}"
                if name not in ("headerImage", "venueMap"):
                    c.warning(ipath, "Unbekanntes Bild. Erlaubt: headerImage, venueMap.")
                if not isinstance(img, dict):
                    c.error(ipath, "Muss ein Objekt sein.")
                    continue
                for j, u in enumerate(img.get("URLs", [])):
                    upath = f"{ipath}.URLs[{j}]"
                    if not str(u.get("URL", "")).startswith("https://"):
                        c.error(upath + ".URL", "Pflichtfeld, muss https:// sein.")
                    if not re.fullmatch(r"[0-9a-fA-F]{64}", str(u.get("SHA256", ""))):
                        c.error(upath + ".SHA256", "SHA-256-Hash (64 Hex-Zeichen) des Bildes erforderlich.")
                    if isinstance(u.get("size"), int) and u["size"] > 2 * 1024 * 1024:
                        c.error(upath + ".size", "Maximal 2 MB.")


# --------------------------------------------------------------------------
# images

def _check_images(c, p, files):
    present = {}
    for name, data in files.items():
        m = _IMAGE_RE.match(name)
        if not m:
            continue
        base, scale = m.group(1), m.group(2) or ""
        present.setdefault(base, set()).add(scale)
        if base not in spec.IMAGES:
            c.info(name, "Kein von Wallet verwendeter Bildname.")
            continue
        size = png_size(data)
        if size is None:
            c.error(name, "Ist keine gültige PNG-Datei.")
            continue
        factor = {"": 1, "@2x": 2, "@3x": 3}[scale]
        (wmin, wmax), h = spec.IMAGES[base]["size"]
        w_px, h_px = size
        if w_px > wmax * factor or h_px > h * factor or w_px < wmin * factor * 0.5:
            c.warning(name, f"{w_px}x{h_px} px - empfohlen für {scale or '@1x'}: "
                      f"Breite {wmin * factor}-{wmax * factor}, Höhe {h * factor} px.")
    if "icon" not in present:
        c.error("icon.png", "Jeder Pass braucht ein Icon (icon.png, icon@2x.png, icon@3x.png).")
    schemes = p.get("preferredStyleSchemes") or []
    poster = "posterGeneric" in p or "posterEventTicket" in schemes
    if poster and "artwork" not in present:
        c.warning("artwork.png", "Poster-Pässe brauchen ein Hintergrundbild artwork.png (358x448 pt).")
    if (poster or "semanticBoardingPass" in schemes) and "primaryLogo" not in present:
        c.info("primaryLogo.png", "Für Poster- und semantische Pässe wird primaryLogo.png angezeigt.")
    if not poster and "logo" not in present:
        c.info("logo.png", "Kein Logo vorhanden.")
    for base, scales in present.items():
        if base in spec.IMAGES and "@2x" not in scales and "@3x" not in scales:
            c.info(base, "Für scharfe Darstellung @2x- und @3x-Varianten mitliefern.")
