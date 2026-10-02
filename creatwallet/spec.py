"""Knowledge base for the Apple Wallet pass format (pass.json).

Based on Apple's "Wallet Passes" documentation, including the newer styles:

* iOS 18/26  - poster event tickets   (preferredStyleSchemes: posterEventTicket)
* iOS 26     - semantic boarding passes (preferredStyleSchemes: semanticBoardingPass)
* iOS 26     - upcomingPassInformation (multi-event tickets, e.g. season tickets)
* iOS 27     - poster generic passes    (top-level style key: posterGeneric)
* iOS 27     - multiple barcodes as fallback chain in ``barcodes``
"""

# Top-level style keys. Exactly one "classic" style is required; posterGeneric
# is an additional style that newer devices prefer when present.
CLASSIC_STYLES = ("boardingPass", "coupon", "eventTicket", "generic", "storeCard")
POSTER_STYLES = ("posterGeneric",)
ALL_STYLES = CLASSIC_STYLES + POSTER_STYLES

FIELD_GROUPS = (
    "headerFields",
    "primaryFields",
    "secondaryFields",
    "auxiliaryFields",
    "backFields",
    "additionalInfoFields",
    "footerFields",
)

# Rough upper limits for the fields that Wallet shows on the front of a pass.
# More fields are not an error, but Wallet will not display them.
FIELD_LIMITS = {
    "boardingPass": {"headerFields": 3, "primaryFields": 2, "secondaryFields": 5, "auxiliaryFields": 5},
    "coupon": {"headerFields": 3, "primaryFields": 1, "secondaryFields": 4, "auxiliaryFields": 4},
    "eventTicket": {"headerFields": 3, "primaryFields": 1, "secondaryFields": 4, "auxiliaryFields": 4},
    "generic": {"headerFields": 3, "primaryFields": 1, "secondaryFields": 4, "auxiliaryFields": 4},
    "storeCard": {"headerFields": 3, "primaryFields": 1, "secondaryFields": 4, "auxiliaryFields": 4},
    "posterGeneric": {"headerFields": 1, "primaryFields": 4, "footerFields": 2},
}

REQUIRED_TOP_LEVEL = (
    "description",
    "formatVersion",
    "organizationName",
    "passTypeIdentifier",
    "serialNumber",
    "teamIdentifier",
)

STRING_KEYS = {
    "appLaunchURL", "authenticationToken", "backgroundColor", "description",
    "expirationDate", "foregroundColor", "footerBackgroundColor", "groupingIdentifier",
    "labelColor", "logoText", "eventLogoText", "organizationName", "passTypeIdentifier",
    "relevantDate", "serialNumber", "teamIdentifier", "webServiceURL",
}

BOOL_KEYS = {
    "sharingProhibited", "suppressStripShine", "suppressHeaderDarkening",
    "useAutomaticColors", "voided",
}

COLOR_KEYS = ("backgroundColor", "foregroundColor", "labelColor", "footerBackgroundColor")

# Keys that only have an effect on poster event tickets (iOS 18+/26+).
POSTER_EVENT_ONLY_KEYS = (
    "accessibilityURL", "addOnURL", "bagPolicyURL", "contactVenueEmail",
    "contactVenuePhoneNumber", "contactVenueWebsite", "directionsInformationURL",
    "eventLogoText", "footerBackgroundColor", "merchandiseURL", "orderFoodURL",
    "parkingInformationURL", "purchaseParkingURL", "sellURL", "suppressHeaderDarkening",
    "transferURL", "transitInformationURL", "useAutomaticColors",
)

# Airline / semantic boarding pass service keys (iOS 26+).
BOARDING_SERVICE_KEYS = (
    "changeSeatURL", "entertainmentURL", "managementURL", "purchaseAdditionalBaggageURL",
    "purchaseLoungeAccessURL", "purchaseWifiURL", "registerServiceAnimalURL",
    "reportLostBagURL", "requestWheelchairURL", "trackBagsURL", "transitProviderEmail",
    "transitProviderPhoneNumber", "transitProviderWebsiteURL", "upgradeURL",
)

KNOWN_TOP_LEVEL = set(REQUIRED_TOP_LEVEL) | STRING_KEYS | BOOL_KEYS | set(ALL_STYLES) | set(
    POSTER_EVENT_ONLY_KEYS) | set(BOARDING_SERVICE_KEYS) | {
    "associatedStoreIdentifiers", "auxiliaryStoreIdentifiers", "barcode", "barcodes",
    "beacons", "locations", "maxDistance", "nfc", "preferredStyleSchemes", "relevantDates",
    "semantics", "userInfo", "upcomingPassInformation",
}

STYLE_SCHEMES = {
    "posterEventTicket": "eventTicket",
    "eventTicket": "eventTicket",
    "semanticBoardingPass": "boardingPass",
    "boardingPass": "boardingPass",
}

TRANSIT_TYPES = (
    "PKTransitTypeAir", "PKTransitTypeBoat", "PKTransitTypeBus",
    "PKTransitTypeGeneric", "PKTransitTypeTrain",
)

BARCODE_FORMATS = (
    "PKBarcodeFormatQR", "PKBarcodeFormatPDF417", "PKBarcodeFormatAztec",
    "PKBarcodeFormatCode128",
    # iOS 27+ (older devices fall back to the next entry in ``barcodes``)
    "PKBarcodeFormatCode39", "PKBarcodeFormatCodabar", "PKBarcodeFormatEAN13",
    "PKBarcodeFormatI2of5",
)
LEGACY_BARCODE_FORMATS = BARCODE_FORMATS[:4]

DATE_STYLES = ("PKDateStyleNone", "PKDateStyleShort", "PKDateStyleMedium",
               "PKDateStyleLong", "PKDateStyleFull")
NUMBER_STYLES = ("PKNumberStyleDecimal", "PKNumberStylePercent",
                 "PKNumberStyleScientific", "PKNumberStyleSpellOut")
TEXT_ALIGNMENTS = ("PKTextAlignmentLeft", "PKTextAlignmentCenter",
                   "PKTextAlignmentRight", "PKTextAlignmentNatural")
DATA_DETECTORS = ("PKDataDetectorTypePhoneNumber", "PKDataDetectorTypeLink",
                  "PKDataDetectorTypeAddress", "PKDataDetectorTypeCalendarEvent")

EVENT_TYPES = (
    "PKEventTypeGeneric", "PKEventTypeLivePerformance", "PKEventTypeMovie",
    "PKEventTypeSports", "PKEventTypeConference", "PKEventTypeConvention",
    "PKEventTypeWorkshop", "PKEventTypeSocialGathering",
)
PASSENGER_CAPABILITIES = (
    "PKPassengerCapabilityPreboarding", "PKPassengerCapabilityPriorityBoarding",
    "PKPassengerCapabilityCarryon", "PKPassengerCapabilityPersonalItem",
    "PKPassengerCapabilityLapInfant",
)
SECURITY_PROGRAMS = (
    "PKTransitSecurityProgramTSAPreCheck", "PKTransitSecurityProgramTSAPreCheckTouchlessID",
    "PKTransitSecurityProgramOSS", "PKTransitSecurityProgramITI",
    "PKTransitSecurityProgramITD", "PKTransitSecurityProgramGlobalEntry",
    "PKTransitSecurityProgramCLEAR",
)
SERVICE_SSRS = ("PETC", "SVAN", "UMNR", "WCBD", "WCBW", "WCHC", "WCHR", "WCHS",
                "WCLB", "WCMP", "WCOB")
INFORMATION_SSRS = ("INFT",)

# Semantic tags required for the new styles. If any is missing, Wallet
# silently falls back to the legacy style.
POSTER_EVENT_REQUIRED = ("eventName", "venueName", "venueRegionName", "venueRoom")
POSTER_EVENT_SPORTS_REQUIRED = ("awayTeamAbbreviation", "homeTeamAbbreviation")
POSTER_EVENT_LIVE_REQUIRED = ("performerNames",)
SEMANTIC_BOARDING_REQUIRED = (
    "airlineCode", "flightNumber", "departureAirportCode", "departureCityName",
    "departureLocationTimeZone", "destinationAirportCode", "destinationCityName",
    "destinationLocationTimeZone", "originalArrivalDate", "originalBoardingDate",
    "originalDepartureDate", "passengerName",
)

SEMANTIC_DATE_KEYS = (
    "currentArrivalDate", "currentBoardingDate", "currentDepartureDate",
    "eventEndDate", "eventStartDate", "originalArrivalDate", "originalBoardingDate",
    "originalDepartureDate", "venueBoxOfficeOpenDate", "venueCloseDate",
    "venueDoorsOpenDate", "venueFanZoneOpenDate", "venueGatesOpenDate",
    "venueOpenDate", "venueParkingLotsOpenDate",
)

KNOWN_SEMANTIC_TAGS = set(SEMANTIC_DATE_KEYS) | {
    "additionalTicketAttributes", "admissionLevel", "admissionLevelAbbreviation",
    "airlineCode", "albumIDs", "artistIDs", "attendeeName", "awayTeamAbbreviation",
    "awayTeamLocation", "awayTeamName", "balance", "boardingGroup",
    "boardingSequenceNumber", "boardingZone", "carNumber", "confirmationNumber",
    "departureAirportCode", "departureAirportName", "departureCityName", "departureGate",
    "departureLocation", "departureLocationDescription", "departureLocationSecurityPrograms",
    "departureLocationTimeZone", "departurePlatform", "departureStationName",
    "departureTerminal", "destinationAirportCode", "destinationAirportName",
    "destinationCityName", "destinationGate", "destinationLocation",
    "destinationLocationDescription", "destinationLocationSecurityPrograms",
    "destinationLocationTimeZone", "destinationPlatform", "destinationStationName",
    "destinationTerminal", "duration", "entranceDescription", "eventName",
    "eventStartDateInfo", "eventType", "flightCode", "flightNumber", "genre",
    "homeTeamAbbreviation", "homeTeamLocation", "homeTeamName",
    "internationalDocumentsAreVerified", "internationalDocumentsVerifiedDeclarationName",
    "leagueAbbreviation", "leagueName", "loungePlaceIDs", "membershipProgramName",
    "membershipProgramNumber", "membershipProgramStatus", "passengerAirlineSSRs",
    "passengerCapabilities", "passengerEligibleSecurityPrograms",
    "passengerInformationSSRs", "passengerName", "passengerServiceSSRs",
    "performerNames", "playlistIDs", "priorityStatus", "seats", "securityScreening",
    "silenceRequested", "sportName", "tailgatingAllowed", "ticketFareClass", "totalPrice",
    "transitProvider", "transitStatus", "transitStatusReason", "vehicleName",
    "vehicleNumber", "vehicleType", "venueEntrance", "venueEntranceDoor",
    "venueEntranceGate", "venueEntrancePortal", "venueLocation", "venueName",
    "venuePhoneNumber", "venueRegionName", "venueRoom", "wifiAccess",
}

UPCOMING_ENTRY_TYPES = ("event",)
UPCOMING_URL_KEYS = (
    "accessibilityURL", "addOnURL", "bagPolicyURL", "contactVenueEmail",
    "contactVenuePhoneNumber", "contactVenueWebsite", "directionsInformationURL",
    "merchandiseURL", "orderFoodURL", "parkingInformationURL", "purchaseParkingURL",
    "sellURL", "transferURL", "transitInformationURL",
)

# Image files a pass bundle may contain, with their size in points
# (@1x). Width ranges are (min, max). Source: Apple HIG "Wallet".
IMAGES = {
    "icon": {"size": ((38, 38), 38), "styles": "all", "required": True},
    "logo": {"size": ((50, 160), 50), "styles": "legacy styles"},
    "primaryLogo": {"size": ((30, 126), 30), "styles": "semantic boarding pass, poster event ticket, poster generic"},
    "secondaryLogo": {"size": ((12, 135), 12), "styles": "poster event ticket"},
    "strip": {"size": ((375, 375), 144), "styles": "coupon, storeCard (eventTicket without background)"},
    "thumbnail": {"size": ((60, 90), 90), "styles": "eventTicket, generic"},
    "background": {"size": ((343, 343), 503), "styles": "eventTicket (non-poster)"},
    "artwork": {"size": ((358, 358), 448), "styles": "poster event ticket, poster generic"},
    "footer": {"size": ((268, 268), 15), "styles": "boardingPass"},
}
IMAGE_SCALES = ("", "@2x", "@3x")
