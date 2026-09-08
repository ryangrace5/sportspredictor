import unittest

import espn_scraper


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params or {}, "timeout": timeout})
        if not self.responses:
            raise AssertionError("Unexpected extra HTTP request")
        return self.responses.pop(0)


def _event(team_a, score_a, team_b, score_b, completed=True):
    return {
        "status": {
            "type": {
                "completed": completed,
                "name": "STATUS_FINAL" if completed else "STATUS_SCHEDULED",
            }
        },
        "competitions": [
            {
                "competitors": [
                    {
                        "team": {"displayName": team_a},
                        "score": str(score_a) if score_a is not None else None,
                    },
                    {
                        "team": {"displayName": team_b},
                        "score": str(score_b) if score_b is not None else None,
                    },
                ]
            }
        ],
    }


class EspnScraperTests(unittest.TestCase):
    def test_aggregate_scoreboard_events_counts_completed_only(self):
        rows = {
            "Alpha": ["Alpha", 0, 0, 0],
            "Beta": ["Beta", 0, 0, 0],
        }
        events = [
            _event("Alpha", 31, "Beta", 20, completed=True),
            _event("Alpha", None, "Gamma", None, completed=False),
        ]

        completed = espn_scraper._aggregate_scoreboard_events(events, rows)

        self.assertEqual(completed, 1)
        self.assertEqual(rows["Alpha"], ["Alpha", 1, 31, 20])
        self.assertEqual(rows["Beta"], ["Beta", 1, 20, 31])
        self.assertEqual(rows["Gamma"], ["Gamma", 0, 0, 0])

    def test_validate_rows_rejects_zero_football(self):
        with self.assertRaisesRegex(ValueError, "all football stats are zero"):
            espn_scraper._validate_rows_for_sheet(
                "NCAAF",
                [["Alpha", 0, 0, 0], ["Beta", 0, 0, 0]],
            )

        rows = espn_scraper._validate_rows_for_sheet(
            "NCAAF",
            [["Alpha", 1, 31, 20], ["Beta", 1, 20, 31]],
        )
        self.assertEqual(len(rows), 2)

    def test_local_ncaaf_catalog_contains_texas_tech(self):
        names = espn_scraper._local_ncaaf_team_catalog()
        self.assertIn("Texas Tech Red Raiders", names)

    def test_fetch_football_scoreboard_stats_fetches_current_week_explicitly(self):
        catalog = {
            "sports": [
                {
                    "leagues": [
                        {
                            "teams": [
                                {"team": {"displayName": "Alpha"}},
                                {"team": {"displayName": "Beta"}},
                                {"team": {"displayName": "Gamma"}},
                                {"team": {"displayName": "Delta"}},
                            ]
                        }
                    ]
                }
            ]
        }
        current_metadata = {
            "week": {"number": 2},
            "events": [],
        }
        week_one = {
            "events": [
                _event("Alpha", 24, "Beta", 17, completed=True),
            ]
        }
        week_two = {
            "events": [
                _event("Gamma", 14, "Delta", 21, completed=True),
            ]
        }

        session = FakeSession(
            [
                FakeResponse(catalog),
                FakeResponse(current_metadata),
                FakeResponse(week_one),
                FakeResponse(week_two),
            ]
        )

        rows = espn_scraper.fetch_football_scoreboard_stats(
            "NFL", session=session
        )
        by_team = {row[0]: row for row in rows}

        self.assertEqual(by_team["Alpha"], ["Alpha", 1, 24, 17])
        self.assertEqual(by_team["Beta"], ["Beta", 1, 17, 24])
        self.assertEqual(by_team["Gamma"], ["Gamma", 1, 14, 21])
        self.assertEqual(by_team["Delta"], ["Delta", 1, 21, 14])

        self.assertEqual(len(session.calls), 4)
        self.assertEqual(session.calls[2]["params"]["week"], 1)
        self.assertEqual(session.calls[2]["params"]["seasontype"], 2)
        self.assertEqual(session.calls[3]["params"]["week"], 2)
        self.assertEqual(session.calls[3]["params"]["seasontype"], 2)


if __name__ == "__main__":
    unittest.main()
