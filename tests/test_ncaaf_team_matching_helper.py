import unittest

from ncaaf_team_matching_helper import resolve_team


class NcaafTeamMatchingTests(unittest.TestCase):
    def test_non_miami_team_does_not_use_unbound_prefer(self):
        mapping = {
            "TCU": {"aliases": ["TCU Horned Frogs"], "stats_key": "TCU"}
        }
        self.assertEqual(
            resolve_team("TCU Horned Frogs", mapping, ["TCU"]),
            ("TCU", "TCU"),
        )

    def test_texas_tech_prefers_sheet_name_over_truncated_legacy_mapping(self):
        mapping = {
            "Texas Tech Red": {
                "aliases": ["Texas Tech Red Raiders"],
                "stats_key": "Texas Tech Red",
            }
        }
        self.assertEqual(
            resolve_team("Texas Tech Red Raiders", mapping, ["Texas Tech", "TCU"]),
            ("Texas Tech", "Texas Tech"),
        )

    def test_miami_ohio_stays_separate_from_miami_florida(self):
        sheet_names = ["Miami (OH)", "Miami (FL)"]
        self.assertEqual(
            resolve_team("Miami RedHawks", {}, sheet_names),
            ("Miami (OH)", "Miami (OH)"),
        )
        self.assertEqual(
            resolve_team("Miami Hurricanes", {}, sheet_names),
            ("Miami (FL)", "Miami (FL)"),
        )


if __name__ == "__main__":
    unittest.main()
