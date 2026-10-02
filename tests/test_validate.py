import copy
import unittest

from creatwallet import new_pass, placeholder_images, validate, TEMPLATES
from creatwallet.png import make_png


def levels(issues, level):
    return [i for i in issues if i.level == level]


def paths(issues, level="error"):
    return {i.path for i in issues if i.level == level}


class TemplateTests(unittest.TestCase):
    def test_all_templates_are_valid(self):
        for name in TEMPLATES:
            with self.subTest(template=name):
                issues = validate(new_pass(name), placeholder_images(name))
                self.assertEqual(levels(issues, "error"), [])
                self.assertEqual(levels(issues, "warning"), [])


class ValidateTests(unittest.TestCase):
    def setUp(self):
        self.p = new_pass("generic")

    def test_missing_required(self):
        del self.p["serialNumber"]
        del self.p["description"]
        self.assertTrue({"serialNumber", "description"} <= paths(validate(self.p)))

    def test_no_style(self):
        del self.p["generic"]
        self.assertIn("pass.json", paths(validate(self.p)))

    def test_two_classic_styles(self):
        self.p["coupon"] = {}
        self.assertIn("pass.json", paths(validate(self.p)))

    def test_bad_color_and_date(self):
        self.p["backgroundColor"] = "#ff0000"
        self.p["expirationDate"] = "2026-12-31"
        self.assertTrue({"backgroundColor", "expirationDate"} <= paths(validate(self.p)))

    def test_duplicate_field_keys(self):
        self.p["generic"]["secondaryFields"].append({"key": "member", "value": "x"})
        self.assertIn("generic.secondaryFields[1].key", paths(validate(self.p)))

    def test_change_message_needs_placeholder(self):
        self.p["generic"]["primaryFields"][0]["changeMessage"] = "Geändert"
        self.assertIn("generic.primaryFields[0].changeMessage", paths(validate(self.p)))

    def test_barcodes_as_object_is_error(self):
        self.p["barcodes"] = {"format": "PKBarcodeFormatQR", "message": "x", "messageEncoding": "utf-8"}
        self.assertIn("barcodes", paths(validate(self.p)))

    def test_new_barcode_format_needs_fallback(self):
        self.p["barcodes"] = [{"format": "PKBarcodeFormatEAN13", "message": "4006381333931",
                               "messageEncoding": "iso-8859-1"}]
        self.assertIn("barcodes", paths(validate(self.p), "warning"))
        self.p["barcodes"].append({"format": "PKBarcodeFormatQR", "message": "x", "messageEncoding": "iso-8859-1"})
        self.assertNotIn("barcodes", paths(validate(self.p), "warning"))

    def test_web_service_requires_token(self):
        self.p["webServiceURL"] = "https://example.com/"
        self.assertIn("authenticationToken", paths(validate(self.p)))

    def test_missing_icon(self):
        files = placeholder_images("generic")
        files = {k: b for k, b in files.items() if not k.startswith("icon")}
        self.assertIn("icon.png", paths(validate(self.p, files)))

    def test_image_size_warning(self):
        files = placeholder_images("generic")
        files["logo@2x.png"] = make_png(1000, 1000)
        self.assertIn("logo@2x.png", paths(validate(self.p, files), "warning"))

    def test_invalid_png(self):
        files = placeholder_images("generic")
        files["logo.png"] = b"not a png"
        self.assertIn("logo.png", paths(validate(self.p, files)))

    def test_unknown_key_warning(self):
        self.p["backgroundcolour"] = "rgb(0,0,0)"
        self.assertIn("backgroundcolour", paths(validate(self.p), "warning"))


class NewStyleTests(unittest.TestCase):
    def test_poster_event_missing_tags_falls_back(self):
        p = new_pass("posterEventTicket")
        del p["semantics"]["venueRoom"]
        del p["semantics"]["performerNames"]
        warnings = [i for i in validate(p) if i.level == "warning" and i.path == "semantics"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("venueRoom", warnings[0].message)
        self.assertIn("performerNames", warnings[0].message)

    def test_poster_sports_requires_team_abbreviations(self):
        p = new_pass("posterSportsTicket")
        del p["semantics"]["awayTeamAbbreviation"]
        self.assertIn("semantics", paths(validate(p), "warning"))

    def test_scheme_requires_style(self):
        p = new_pass("generic")
        p["preferredStyleSchemes"] = ["posterEventTicket", "eventTicket"]
        self.assertIn("preferredStyleSchemes", paths(validate(p)))

    def test_semantic_boarding_requires_air(self):
        p = new_pass("semanticBoardingPass")
        p["boardingPass"]["transitType"] = "PKTransitTypeTrain"
        self.assertIn("boardingPass.transitType", paths(validate(p)))

    def test_semantic_boarding_missing_tags(self):
        p = new_pass("semanticBoardingPass")
        del p["semantics"]["passengerName"]
        self.assertIn("semantics", paths(validate(p), "warning"))

    def test_semantic_enum_values(self):
        p = new_pass("semanticBoardingPass")
        p["semantics"]["passengerCapabilities"] = ["Fly"]
        p["semantics"]["flightNumber"] = "123"
        issues = validate(p)
        self.assertIn("semantics.passengerCapabilities", paths(issues, "warning"))
        self.assertIn("semantics.flightNumber", paths(issues))

    def test_poster_generic_without_fallback(self):
        p = new_pass("posterGeneric")
        del p["generic"]
        issues = validate(p)
        self.assertEqual(levels(issues, "error"), [])
        self.assertIn("posterGeneric", paths(issues, "warning"))

    def test_poster_needs_artwork(self):
        p = new_pass("posterGeneric")
        files = {k: b for k, b in placeholder_images("posterGeneric").items() if not k.startswith("artwork")}
        self.assertIn("artwork.png", paths(validate(p, files), "warning"))

    def test_upcoming_entries(self):
        p = new_pass("seasonTicket")
        entries = p["upcomingPassInformation"]
        entries.append(copy.deepcopy(entries[0]))  # duplicate identifier
        entries[1]["type"] = "concert"
        entries[2]["dateInformation"]["date"] = "morgen"
        issues = paths(validate(p))
        self.assertIn("upcomingPassInformation[3].identifier", issues)
        self.assertIn("upcomingPassInformation[1].type", issues)
        self.assertIn("upcomingPassInformation[2].dateInformation.date", issues)

    def test_upcoming_remote_images(self):
        p = new_pass("seasonTicket")
        p["upcomingPassInformation"][0]["images"] = {
            "headerImage": {"URLs": [{"URL": "http://x/y.png", "SHA256": "abc"}]}}
        issues = paths(validate(p))
        self.assertIn("upcomingPassInformation[0].images.headerImage.URLs[0].URL", issues)
        self.assertIn("upcomingPassInformation[0].images.headerImage.URLs[0].SHA256", issues)


if __name__ == "__main__":
    unittest.main()
