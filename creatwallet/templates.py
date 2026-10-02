"""Starter templates for every pass style, including the new ones."""

import copy
import json
import uuid
from pathlib import Path

from .png import make_png

PLACEHOLDER_TEAM = "ABCDE12345"
PLACEHOLDER_PTI = "pass.com.example.wallet"

_BASE = {
    "formatVersion": 1,
    "passTypeIdentifier": PLACEHOLDER_PTI,
    "teamIdentifier": PLACEHOLDER_TEAM,
    "serialNumber": "",
    "organizationName": "Beispiel GmbH",
}

TEMPLATES = {}


def _template(name, title, min_ios, images, data):
    TEMPLATES[name] = {"title": title, "min_ios": min_ios, "images": images, "pass": data}


_template("generic", "Generischer Pass (z. B. Mitgliedsausweis)", "6", ["icon", "logo", "thumbnail"], {
    "description": "Mitgliedsausweis",
    "logoText": "Beispiel Club",
    "foregroundColor": "rgb(255, 255, 255)",
    "backgroundColor": "rgb(32, 54, 92)",
    "labelColor": "rgb(180, 200, 230)",
    "generic": {
        "headerFields": [{"key": "level", "label": "STATUS", "value": "Gold"}],
        "primaryFields": [{"key": "member", "label": "MITGLIED", "value": "Max Mustermann"}],
        "secondaryFields": [{"key": "number", "label": "NUMMER", "value": "102035"}],
        "auxiliaryFields": [{"key": "since", "label": "SEIT", "value": "2024-01-15T00:00+01:00",
                             "dateStyle": "PKDateStyleMedium", "timeStyle": "PKDateStyleNone"}],
        "backFields": [{"key": "terms", "label": "Bedingungen", "value": "Nicht übertragbar."}],
    },
    "barcodes": [{"format": "PKBarcodeFormatQR", "message": "MEMBER-102035",
                  "messageEncoding": "iso-8859-1", "altText": "102035"}],
})

_template("storeCard", "Kundenkarte / Gutschein mit Guthaben", "6", ["icon", "logo", "strip"], {
    "description": "Kundenkarte",
    "logoText": "Beispiel Café",
    "foregroundColor": "rgb(255, 255, 255)",
    "backgroundColor": "rgb(120, 60, 30)",
    "labelColor": "rgb(255, 220, 180)",
    "storeCard": {
        "primaryFields": [{"key": "balance", "label": "GUTHABEN", "value": 25.5, "currencyCode": "EUR",
                           "changeMessage": "Neues Guthaben: %@"}],
        "secondaryFields": [{"key": "name", "label": "KUNDE", "value": "Max Mustermann"}],
        "backFields": [{"key": "info", "label": "Info", "value": "Gültig in allen Filialen."}],
    },
    "semantics": {"balance": {"amount": "25.50", "currencyCode": "EUR"}},
    "barcodes": [{"format": "PKBarcodeFormatCode128", "message": "4006381333931",
                  "messageEncoding": "iso-8859-1"}],
})

_template("coupon", "Coupon", "6", ["icon", "logo", "strip"], {
    "description": "Rabatt-Coupon",
    "logoText": "Beispiel Shop",
    "foregroundColor": "rgb(0, 0, 0)",
    "backgroundColor": "rgb(245, 197, 67)",
    "labelColor": "rgb(90, 60, 0)",
    "expirationDate": "2026-12-31T23:59+01:00",
    "coupon": {
        "primaryFields": [{"key": "offer", "label": "AUF ALLES", "value": "20 %"}],
        "secondaryFields": [{"key": "expires", "label": "GÜLTIG BIS", "value": "2026-12-31T23:59+01:00",
                             "dateStyle": "PKDateStyleMedium", "timeStyle": "PKDateStyleNone"}],
        "backFields": [{"key": "terms", "label": "Bedingungen", "value": "Einmal einlösbar."}],
    },
    "barcodes": [{"format": "PKBarcodeFormatQR", "message": "COUPON-20", "messageEncoding": "iso-8859-1"}],
})

_template("eventTicket", "Klassisches Event-Ticket (Kino, Konferenz, ...)", "6",
          ["icon", "logo", "thumbnail"], {
    "description": "Konferenz-Ticket",
    "logoText": "DevConf 2026",
    "foregroundColor": "rgb(255, 255, 255)",
    "backgroundColor": "rgb(60, 20, 90)",
    "labelColor": "rgb(210, 180, 255)",
    "relevantDates": [{"startDate": "2026-11-12T08:00+01:00", "endDate": "2026-11-12T18:00+01:00"}],
    "eventTicket": {
        "primaryFields": [{"key": "event", "label": "EVENT", "value": "DevConf 2026"}],
        "secondaryFields": [{"key": "loc", "label": "ORT", "value": "Messe Berlin"}],
        "auxiliaryFields": [{"key": "date", "label": "DATUM", "value": "2026-11-12T09:00+01:00",
                             "dateStyle": "PKDateStyleMedium", "timeStyle": "PKDateStyleShort"}],
        "backFields": [{"key": "info", "label": "Info", "value": "Einlass ab 8 Uhr."}],
    },
    "semantics": {"eventType": "PKEventTypeConference", "eventName": "DevConf 2026",
                  "venueName": "Messe Berlin", "eventStartDate": "2026-11-12T09:00+01:00"},
    "barcodes": [{"format": "PKBarcodeFormatQR", "message": "TICKET-0001", "messageEncoding": "iso-8859-1"}],
})

_template("posterEventTicket", "NEU: Poster-Event-Ticket Konzert (iOS 26+, mit Fallback)", "26",
          ["icon", "logo", "primaryLogo", "secondaryLogo", "artwork", "thumbnail"], {
    "description": "Konzertticket",
    "preferredStyleSchemes": ["posterEventTicket", "eventTicket"],
    "logoText": "Live Tour 2026",
    "eventLogoText": "Live Tour 2026",
    "foregroundColor": "rgb(255, 255, 255)",
    "backgroundColor": "rgb(20, 20, 30)",
    "labelColor": "rgb(200, 200, 220)",
    "footerBackgroundColor": "rgb(200, 30, 60)",
    "useAutomaticColors": True,
    "relevantDates": [{"startDate": "2026-11-20T18:00+01:00", "endDate": "2026-11-20T23:30+01:00"}],
    "bagPolicyURL": "https://example.com/taschen",
    "orderFoodURL": "https://example.com/essen",
    "directionsInformationURL": "https://example.com/anfahrt",
    "merchandiseURL": "https://example.com/merch",
    "transferURL": "https://example.com/weitergeben",
    "contactVenueWebsite": "https://example.com",
    "eventTicket": {
        "primaryFields": [{"key": "event", "label": "KONZERT", "value": "Die Beispiele"}],
        "secondaryFields": [{"key": "venue", "label": "ORT", "value": "Arena Hamburg"},
                            {"key": "date", "label": "DATUM", "value": "2026-11-20T20:00+01:00",
                             "dateStyle": "PKDateStyleMedium", "timeStyle": "PKDateStyleShort"}],
        "auxiliaryFields": [{"key": "block", "label": "BLOCK", "value": "A"},
                            {"key": "row", "label": "REIHE", "value": "12"},
                            {"key": "seat", "label": "PLATZ", "value": "7"}],
        "additionalInfoFields": [{"key": "doors", "label": "Einlass", "value": "18:30 Uhr"}],
        "backFields": [{"key": "terms", "label": "AGB", "value": "Es gelten die AGB des Veranstalters."}],
    },
    "semantics": {
        "eventType": "PKEventTypeLivePerformance",
        "eventName": "Die Beispiele - Live Tour 2026",
        "performerNames": ["Die Beispiele"],
        "venueName": "Arena Hamburg",
        "venueRegionName": "Hamburg",
        "venueRoom": "Halle 1",
        "venueLocation": {"latitude": 53.5888, "longitude": 9.8988},
        "eventStartDate": "2026-11-20T20:00+01:00",
        "eventEndDate": "2026-11-20T23:00+01:00",
        "eventStartDateInfo": {"date": "2026-11-20T20:00+01:00", "timeZone": "Europe/Berlin"},
        "venueDoorsOpenDate": "2026-11-20T18:30+01:00",
        "admissionLevel": "Sitzplatz",
        "attendeeName": "Max Mustermann",
        "seats": [{"seatSection": "A", "seatRow": "12", "seatNumber": "7",
                   "seatSectionColor": "rgb(200, 30, 60)"}],
    },
    "barcodes": [{"format": "PKBarcodeFormatQR", "message": "TICKET-4711", "messageEncoding": "iso-8859-1"}],
})

_sports = copy.deepcopy(TEMPLATES["posterEventTicket"]["pass"])
_sports.update({"description": "Fußballticket", "logoText": "Beispiel Liga", "eventLogoText": "Beispiel Liga"})
_sports["eventTicket"]["primaryFields"] = [{"key": "match", "label": "SPIEL", "value": "FCB - SVW"}]
_sports["semantics"] = {
    "eventType": "PKEventTypeSports",
    "eventName": "FC Beispiel vs. SV Werte",
    "homeTeamName": "FC Beispiel", "homeTeamAbbreviation": "FCB", "homeTeamLocation": "Hamburg",
    "awayTeamName": "SV Werte", "awayTeamAbbreviation": "SVW", "awayTeamLocation": "Bremen",
    "leagueName": "Beispiel Liga", "leagueAbbreviation": "BL", "sportName": "Fußball",
    "venueName": "Volksparkstadion", "venueRegionName": "Hamburg", "venueRoom": "Nordtribüne",
    "eventStartDate": "2026-11-20T20:30+01:00",
    "seats": [{"seatSection": "22C", "seatRow": "8", "seatNumber": "14",
               "seatSectionColor": "rgb(0, 90, 160)"}],
}
_template("posterSportsTicket", "NEU: Poster-Event-Ticket Sport (iOS 26+, mit Fallback)", "26",
          TEMPLATES["posterEventTicket"]["images"], _sports)

_template("seasonTicket", "NEU: Dauerkarte mit mehreren Terminen (upcomingPassInformation, iOS 26+)", "26",
          ["icon", "logo", "primaryLogo", "artwork"], {
    "description": "Dauerkarte",
    "preferredStyleSchemes": ["posterEventTicket", "eventTicket"],
    "logoText": "Saison 26/27",
    "eventLogoText": "Saison 26/27",
    "foregroundColor": "rgb(255, 255, 255)",
    "backgroundColor": "rgb(0, 60, 120)",
    "labelColor": "rgb(180, 210, 255)",
    "relevantDates": [
        {"startDate": "2026-10-10T13:00+02:00", "endDate": "2026-10-10T19:00+02:00"},
        {"startDate": "2026-10-24T13:00+02:00", "endDate": "2026-10-24T19:00+02:00"},
    ],
    "eventTicket": {
        "primaryFields": [{"key": "pass", "label": "DAUERKARTE", "value": "Saison 26/27"}],
        "secondaryFields": [{"key": "holder", "label": "INHABER", "value": "Max Mustermann"}],
        "auxiliaryFields": [{"key": "seat", "label": "PLATZ", "value": "Block 22C, Reihe 8, Platz 14"}],
    },
    "semantics": {
        "eventType": "PKEventTypeSports", "eventName": "Heimspiele Saison 26/27",
        "homeTeamAbbreviation": "FCB", "awayTeamAbbreviation": "TBA",
        "venueName": "Volksparkstadion", "venueRegionName": "Hamburg", "venueRoom": "Nordtribüne",
    },
    "upcomingPassInformation": [
        {"identifier": "match-01", "type": "event", "name": "FC Beispiel vs. SV Werte", "isActive": True,
         "dateInformation": {"date": "2026-10-10T15:30+02:00", "timeZone": "Europe/Berlin"},
         "semantics": {"homeTeamAbbreviation": "FCB", "awayTeamAbbreviation": "SVW",
                       "venueName": "Volksparkstadion"},
         "URLs": {"directionsInformationURL": "https://example.com/anfahrt"}},
        {"identifier": "match-02", "type": "event", "name": "FC Beispiel vs. TSV Muster",
         "dateInformation": {"date": "2026-10-24T15:30+02:00", "timeZone": "Europe/Berlin"},
         "semantics": {"homeTeamAbbreviation": "FCB", "awayTeamAbbreviation": "TSV"}},
        {"identifier": "match-03", "type": "event", "name": "FC Beispiel vs. Musterstadt",
         "dateInformation": {"isUndetermined": True}},
    ],
    "barcodes": [{"format": "PKBarcodeFormatQR", "message": "SEASON-0815", "messageEncoding": "iso-8859-1"}],
})

_template("semanticBoardingPass", "NEU: Semantischer Flug-Boarding-Pass (iOS 26+, mit Fallback)", "26",
          ["icon", "logo", "primaryLogo", "footer"], {
    "description": "Bordkarte EX123 HAM-MUC",
    "preferredStyleSchemes": ["semanticBoardingPass", "boardingPass"],
    "logoText": "Example Air",
    "foregroundColor": "rgb(255, 255, 255)",
    "backgroundColor": "rgb(10, 60, 130)",
    "labelColor": "rgb(170, 200, 240)",
    "groupingIdentifier": "booking-ABC123",
    "relevantDates": [{"startDate": "2026-11-05T06:00+01:00", "endDate": "2026-11-05T09:30+01:00"}],
    "changeSeatURL": "https://example.com/sitzplatz",
    "purchaseAdditionalBaggageURL": "https://example.com/gepaeck",
    "managementURL": "https://example.com/buchung",
    "transitProviderWebsiteURL": "https://example.com",
    "transitProviderPhoneNumber": "+49 40 123456",
    "boardingPass": {
        "transitType": "PKTransitTypeAir",
        "headerFields": [{"key": "gate", "label": "GATE", "value": "B12", "changeMessage": "Neues Gate: %@"}],
        "primaryFields": [{"key": "from", "label": "Hamburg", "value": "HAM"},
                          {"key": "to", "label": "München", "value": "MUC"}],
        "auxiliaryFields": [{"key": "boarding", "label": "BOARDING", "value": "2026-11-05T07:25+01:00",
                             "timeStyle": "PKDateStyleShort", "dateStyle": "PKDateStyleNone"},
                            {"key": "flight", "label": "FLUG", "value": "EX123"},
                            {"key": "seat", "label": "SITZ", "value": "14C"}],
        "secondaryFields": [{"key": "passenger", "label": "PASSAGIER", "value": "Max Mustermann"},
                            {"key": "group", "label": "GRUPPE", "value": "2"}],
        "backFields": [{"key": "pnr", "label": "Buchungscode", "value": "ABC123"}],
    },
    "semantics": {
        "airlineCode": "EX", "flightNumber": 123, "flightCode": "EX123",
        "transitProvider": "Example Air",
        "departureAirportCode": "HAM", "departureAirportName": "Flughafen Hamburg",
        "departureCityName": "Hamburg", "departureLocationTimeZone": "Europe/Berlin",
        "departureLocation": {"latitude": 53.6304, "longitude": 9.9882},
        "departureGate": "B12", "departureTerminal": "1",
        "destinationAirportCode": "MUC", "destinationAirportName": "Flughafen München",
        "destinationCityName": "München", "destinationLocationTimeZone": "Europe/Berlin",
        "destinationLocation": {"latitude": 48.3538, "longitude": 11.7861},
        "originalBoardingDate": "2026-11-05T07:25+01:00",
        "originalDepartureDate": "2026-11-05T07:55+01:00",
        "originalArrivalDate": "2026-11-05T09:05+01:00",
        "passengerName": {"givenName": "Max", "familyName": "Mustermann"},
        "confirmationNumber": "ABC123",
        "boardingGroup": "2", "boardingSequenceNumber": "045",
        "ticketFareClass": "Economy",
        "membershipProgramName": "Example Miles", "membershipProgramNumber": "EM 1234 5678",
        "membershipProgramStatus": "Gold",
        "passengerCapabilities": ["PKPassengerCapabilityPriorityBoarding", "PKPassengerCapabilityCarryon"],
        "seats": [{"seatNumber": "14C", "seatRow": "14", "seatType": "Gang"}],
    },
    "barcodes": [{"format": "PKBarcodeFormatPDF417",
                  "message": "M1MUSTERMANN/MAX      EABC123 HAMMUCEX 0123 309Y014C0045 100",
                  "messageEncoding": "iso-8859-1"}],
})

_template("trainTicket", "Bahn-/Bus-Ticket (Boarding-Pass)", "6", ["icon", "logo", "footer"], {
    "description": "Bahnticket Hamburg - Berlin",
    "logoText": "Beispiel Bahn",
    "foregroundColor": "rgb(255, 255, 255)",
    "backgroundColor": "rgb(200, 20, 30)",
    "labelColor": "rgb(255, 200, 200)",
    "boardingPass": {
        "transitType": "PKTransitTypeTrain",
        "headerFields": [{"key": "car", "label": "WAGEN", "value": "7"}],
        "primaryFields": [{"key": "from", "label": "Hamburg Hbf", "value": "HH"},
                          {"key": "to", "label": "Berlin Hbf", "value": "B"}],
        "auxiliaryFields": [{"key": "dep", "label": "ABFAHRT", "value": "2026-11-05T08:00+01:00",
                             "timeStyle": "PKDateStyleShort", "dateStyle": "PKDateStyleShort"},
                            {"key": "seat", "label": "PLATZ", "value": "45"}],
        "secondaryFields": [{"key": "passenger", "label": "REISENDER", "value": "Max Mustermann"}],
    },
    "semantics": {"departureStationName": "Hamburg Hbf", "destinationStationName": "Berlin Hbf",
                  "departurePlatform": "13", "carNumber": "7",
                  "originalDepartureDate": "2026-11-05T08:00+01:00"},
    "barcodes": [{"format": "PKBarcodeFormatAztec", "message": "TRAIN-123456", "messageEncoding": "iso-8859-1"}],
})

_template("posterGeneric", "NEU: Poster-Generic-Pass (iOS 27+, mit generic-Fallback)", "27",
          ["icon", "logo", "primaryLogo", "artwork", "thumbnail"], {
    "description": "Museumspass",
    "logoText": "Beispiel Museum",
    "foregroundColor": "rgb(0, 0, 0)",
    "backgroundColor": "rgb(245, 197, 67)",
    "labelColor": "rgb(90, 60, 0)",
    "posterGeneric": {
        "headerFields": [{"key": "memberNumber", "label": "MITGLIEDS-NR.", "value": "102035"}],
        "primaryFields": [{"key": "memberName", "label": "NAME", "value": "Max Mustermann"},
                          {"key": "memberType", "label": "TARIF", "value": "Familie"}],
        "footerFields": [{"key": "validUntil", "label": "GÜLTIG BIS", "value": "2027-12-31T23:59+01:00",
                          "dateStyle": "PKDateStyleMedium", "timeStyle": "PKDateStyleNone"}],
        "additionalInfoFields": [{"key": "hours", "label": "Öffnungszeiten", "value": "Di-So 10-18 Uhr"}],
        "backFields": [{"key": "service", "label": "Kundenservice", "value": "+49 40 123456"},
                       {"key": "terms", "label": "Bedingungen", "value": "Nicht übertragbar."}],
    },
    "generic": {
        "primaryFields": [{"key": "memberName", "label": "NAME", "value": "Max Mustermann"}],
        "secondaryFields": [{"key": "memberNumber", "label": "MITGLIEDS-NR.", "value": "102035"}],
        "auxiliaryFields": [{"key": "memberType", "label": "TARIF", "value": "Familie"}],
        "backFields": [{"key": "service", "label": "Kundenservice", "value": "+49 40 123456"},
                       {"key": "terms", "label": "Bedingungen", "value": "Nicht übertragbar."}],
    },
    "barcodes": [
        {"format": "PKBarcodeFormatEAN13", "message": "4006381333931", "messageEncoding": "iso-8859-1"},
        {"format": "PKBarcodeFormatQR", "message": "4006381333931", "messageEncoding": "iso-8859-1"},
    ],
})

# Placeholder image sizes in points (@1x) and colors.
_PLACEHOLDER = {
    "icon": ((38, 38), (0, 122, 255)),
    "logo": ((120, 50), (255, 255, 255)),
    "primaryLogo": ((100, 30), (255, 255, 255)),
    "secondaryLogo": ((80, 12), (255, 255, 255)),
    "strip": ((375, 144), (230, 140, 60)),
    "thumbnail": ((90, 90), (120, 120, 140)),
    "background": ((343, 503), (40, 40, 60)),
    "artwork": ((358, 448), (70, 40, 120)),
    "footer": ((268, 15), (255, 255, 255)),
}


def new_pass(name):
    """A fresh copy of a template's pass.json with a random serial number."""
    if name not in TEMPLATES:
        raise KeyError(name)
    data = copy.deepcopy(_BASE)
    data.update(copy.deepcopy(TEMPLATES[name]["pass"]))
    data["serialNumber"] = uuid.uuid4().hex
    # Put the required keys first for readability.
    order = list(_BASE) + ["description"]
    return {k: data[k] for k in order} | {k: val for k, val in data.items() if k not in order}


def placeholder_images(name, scales=(1, 2, 3)):
    """Placeholder PNGs for the images a template uses."""
    files = {}
    for image in TEMPLATES[name]["images"]:
        (w, h), color = _PLACEHOLDER[image]
        accent = (255, 255, 255) if image in ("icon", "artwork", "thumbnail", "strip") else None
        for s in scales:
            suffix = "" if s == 1 else f"@{s}x"
            files[f"{image}{suffix}.png"] = make_png(w * s, h * s, color, accent)
    return files


def write_project(name, directory, pass_type_identifier=None, team_identifier=None):
    """Create a pass source directory from a template."""
    target = Path(directory)
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(f"{target} existiert bereits und ist nicht leer.")
    target.mkdir(parents=True, exist_ok=True)
    data = new_pass(name)
    if pass_type_identifier:
        data["passTypeIdentifier"] = pass_type_identifier
    if team_identifier:
        data["teamIdentifier"] = team_identifier
    (target / "pass.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for filename, png in placeholder_images(name).items():
        (target / filename).write_bytes(png)
    return target
