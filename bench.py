"""Measure successful authenticated requests on the current dataset."""
import argparse
import base64
import json
import os
import statistics
from math import ceil
from pathlib import Path
from urllib.request import Request, urlopen

from app import USER, PASSWORD
from seed import VOLUMES

BASE = "http://127.0.0.1:" + os.getenv("APP_PORT", "8765")
TOKEN = base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()


def request(path, data=None):
    headers = {"Authorization": "Basic " + TOKEN, "Content-Type": "application/json"}
    body = json.dumps(data).encode() if data is not None else None
    with urlopen(Request(BASE + path, data=body, headers=headers), timeout=60) as response:
        return (float(response.headers["X-Response-Time-Ms"]),
                float(response.headers["X-DB-Time-Ms"]),
                int(response.headers["X-DB-Queries"]),
                json.load(response))


def measure(volume, repeats):
    _, _, slots, _ = VOLUMES[volume]
    operations = [
        ("GET /api/services", lambda i: request("/api/services")),
        ("GET /api/appointments", lambda i: request("/api/appointments?page=1&size=20&status=booked")),
        ("GET /api/appointments/{id}", lambda i: request("/api/appointments/42")),
        ("GET /api/slots", lambda i: request("/api/slots?service_id=3")),
        ("GET /api/summary", lambda i: request("/api/summary?from=2026-11-18&to=2027-01-01")),
        ("POST /api/appointments", lambda i: request("/api/appointments", {"slot_id": slots-i, "service_id": 3})),
    ]
    rows = []
    created = []
    print(f"stanislav_tedety | {volume} | {repeats} повторов + 1 прогрев")
    print(f"{'Операция':40} {'p50':>8} {'p95':>8} {'max':>8} {'БД':>8} {'Код':>8} {'SQL':>4}")

    def series(name, action):
        action(-1)
        samples = [action(i) for i in range(repeats)]
        total = sorted(sample[0] for sample in samples)
        representative = sorted(samples, key=lambda x: x[0])[len(samples) // 2]
        row = dict(operation=name, p50=statistics.median(total), p95=total[ceil(repeats*.95)-1],
                   maximum=max(total), layer_total=representative[0], db=representative[1],
                   code=representative[0]-representative[1], queries=representative[2])
        rows.append(row)
        print(f"{name:40} {row['p50']:8.2f} {row['p95']:8.2f} {row['maximum']:8.2f} "
              f"{row['db']:8.2f} {row['code']:8.2f} {row['queries']:4}")
        return samples

    for name, action in operations[:-1]:
        series(name, action)

    def booking(i):
        response = operations[-1][1](i+1)
        created.append(response[3]["id"])
        return response

    series(operations[-1][0], booking)
    series("POST /api/appointments/{id}/cancel",
           lambda i: request(f"/api/appointments/{created[i+1]}/cancel", {}))
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("volume", choices=VOLUMES)
    parser.add_argument("--repeats", type=int, choices=range(20, 101), default=25, metavar="20–100")
    args = parser.parse_args()
    Path(f"bench-{args.volume}.json").write_text(
        json.dumps(measure(args.volume, args.repeats), indent=2), encoding="utf-8")
