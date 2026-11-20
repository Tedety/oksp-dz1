"""Business rules and HTTP contract checks against a seeded small database."""
import unittest
from datetime import datetime, timedelta
from urllib.error import HTTPError
from urllib.request import urlopen

from app import fits
from bench import BASE, request


class DurationTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 11, 18, 9)

    def test_exact_duration_fits(self):
        self.assertTrue(fits({"starts_at": self.start, "ends_at": self.start + timedelta(minutes=45)},
                             {"minutes": 45}))

    def test_short_slot_rejected(self):
        self.assertFalse(fits({"starts_at": self.start, "ends_at": self.start + timedelta(minutes=30)},
                              {"minutes": 45}))


class ContractTests(unittest.TestCase):
    def test_requires_authorization(self):
        with self.assertRaises(HTTPError) as err:
            urlopen(BASE + "/api/appointments")
        self.assertEqual(err.exception.code, 401)
        err.exception.close()

    def test_list_filtered_and_paged(self):
        data = request("/api/appointments?page=2&size=3&status=booked")[3]
        self.assertEqual((data["page"], data["size"], len(data["items"])), (2, 3, 3))
        self.assertTrue(all(row["status"] == "booked" for row in data["items"]))

    def test_invalid_parameter(self):
        with self.assertRaises(HTTPError) as err:
            request("/api/appointments?size=101")
        self.assertEqual(err.exception.code, 422)
        err.exception.close()

    def test_detail_includes_linked_entities(self):
        row = request("/api/appointments/42")[3]
        self.assertEqual(row["id"], 42)
        self.assertTrue({"specialist_id", "service_id", "slot_id"} <= row.keys())

    def test_services(self):
        self.assertTrue(all({"id", "name", "minutes"} <= item.keys() for item in request("/api/services")[3]))

    def test_summary_totals(self):
        result = request("/api/summary")[3]
        self.assertEqual(result["booked"], sum(row["booked"] for row in result["specialists"]))
        self.assertEqual(result["cancelled"], sum(row["cancelled"] for row in result["specialists"]))

    def test_short_slot_over_http(self):
        with self.assertRaises(HTTPError) as err:
            request("/api/appointments", {"slot_id": 498, "service_id": 2})
        self.assertEqual(err.exception.code, 409)
        err.exception.close()

    def test_book_overlap_cancel_and_reopen(self):
        slots = request("/api/slots?service_id=3")[3]
        self.assertTrue(slots)
        slot = slots[0]["id"]
        payload = {"slot_id": slot, "service_id": 3}
        booked = request("/api/appointments", payload)[3]
        try:
            with self.assertRaises(HTTPError) as err:
                request("/api/appointments", payload)
            self.assertEqual(err.exception.code, 409)
            err.exception.close()
            self.assertNotIn(slot, [row["id"] for row in request("/api/slots?service_id=3")[3]])
        finally:
            cancelled = request(f"/api/appointments/{booked['id']}/cancel", {})[3]
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertIn(slot, [row["id"] for row in request("/api/slots?service_id=3")[3]])
