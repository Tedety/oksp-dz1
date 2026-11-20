"""HTTP service for specialist appointments."""
import base64
import hmac
import json
import os
import re
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import perf_counter
from urllib.parse import parse_qs, urlsplit

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parent
DSN = os.getenv("DATABASE_URL", "postgresql://postgres:course@127.0.0.1:55433/stanislav_tedety")
OPTIONS = "-c search_path=stanislav_tedety"
USER = os.getenv("APP_USER", "demo")
PASSWORD = os.getenv("APP_PASSWORD", "demo")


class InvalidRequest(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


class Database:
    def __init__(self, connection):
        self.connection = connection
        self.milliseconds = 0
        self.queries = 0

    def query(self, sql, *parameters):
        started = perf_counter()
        try:
            return self.connection.execute(sql, parameters).fetchall()
        finally:
            self.milliseconds += (perf_counter() - started) * 1000
            self.queries += 1


def number(value, label, minimum=1, maximum=2147483647):
    try:
        result = int(value)
    except (ValueError, TypeError, OverflowError):
        raise InvalidRequest(422, f"Проверьте параметр {label}") from None
    if isinstance(value, (bool, float)) or not minimum <= result <= maximum:
        raise InvalidRequest(422, f"Проверьте параметр {label}")
    return result


DETAIL = """
SELECT a.id, a.status, t.id AS slot_id, t.starts_at, t.ends_at,
       p.id AS specialist_id, p.name AS specialist, s.id AS service_id, s.name AS service
FROM appointments a JOIN slots t ON t.id=a.slot_id
JOIN specialists p ON p.id=t.specialist_id JOIN services s ON s.id=a.service_id
"""


def detail(db, appointment_id):
    rows = db.query(DETAIL + " WHERE a.id=%s", appointment_id)
    if not rows:
        raise InvalidRequest(404, "Запись не найдена")
    return rows[0]


def fits(slot, service):
    return (slot["ends_at"] - slot["starts_at"]).total_seconds() >= service["minutes"] * 60


def book(db, data):
    slot_id = number(data.get("slot_id"), "slot_id")
    service_id = number(data.get("service_id"), "service_id")
    slots = db.query("SELECT * FROM slots WHERE id=%s", slot_id)
    services = db.query("SELECT * FROM services WHERE id=%s", service_id)
    if not slots or not services:
        raise InvalidRequest(404, "Слот или услуга не найдены")
    slot, service = slots[0], services[0]
    if not fits(slot, service):
        raise InvalidRequest(409, "Слот слишком короткий")
    # Serialize all bookings for one specialist, including overlapping slots.
    db.query("SELECT id FROM specialists WHERE id=%s FOR UPDATE", slot["specialist_id"])
    busy = db.query("""
        SELECT 1 FROM appointments a JOIN slots t ON t.id=a.slot_id
        WHERE a.status='booked' AND t.specialist_id=%s
          AND t.starts_at < %s AND t.ends_at > %s LIMIT 1
    """, slot["specialist_id"], slot["ends_at"], slot["starts_at"])
    if busy:
        raise InvalidRequest(409, "Слот пересекается с существующей записью")
    row = db.query("""
        INSERT INTO appointments (slot_id, service_id, status)
        VALUES (%s,%s,'booked') RETURNING id
    """, slot_id, service_id)[0]
    return 201, detail(db, row["id"])


def dispatch(db, method, path, parameters, body):
    if method == "GET" and path == "/api/services":
        return 200, db.query("SELECT * FROM services ORDER BY id")
    if method == "GET" and path == "/api/appointments":
        page = number(parameters.get("page", "1"), "page")
        size = number(parameters.get("size", "20"), "size", maximum=100)
        status = parameters.get("status", "")
        if status not in ("", "booked", "cancelled"):
            raise InvalidRequest(422, "Неизвестный статус")
        where = " WHERE a.status=%s" if status else ""
        args = (status,) if status else ()
        total = db.query("SELECT count(*) AS total FROM appointments a" + where, *args)[0]["total"]
        rows = db.query(DETAIL + where + " ORDER BY a.id DESC LIMIT %s OFFSET %s",
                        *args, size, (page - 1) * size)
        return 200, {"items": rows, "total": total, "page": page, "size": size}
    if method == "GET" and path == "/api/slots":
        service_id = number(parameters.get("service_id"), "service_id")
        if not db.query("SELECT id FROM services WHERE id=%s", service_id):
            raise InvalidRequest(404, "Услуга не найдена")
        return 200, db.query("""
            SELECT t.id, p.name AS specialist, t.starts_at, t.ends_at
            FROM slots t JOIN specialists p ON p.id=t.specialist_id
            JOIN services s ON s.id=%s
            WHERE t.ends_at-t.starts_at >= make_interval(mins => s.minutes)
              AND NOT EXISTS (
                 SELECT 1 FROM appointments a JOIN slots b ON b.id=a.slot_id
                 WHERE a.status='booked' AND b.specialist_id=t.specialist_id
                   AND b.starts_at<t.ends_at AND b.ends_at>t.starts_at
              )
            ORDER BY t.starts_at, t.id LIMIT 50
        """, service_id)
    if method == "GET" and path == "/api/summary":
        try:
            start = datetime.fromisoformat(parameters.get("from", "2026-01-01"))
            end = datetime.fromisoformat(parameters.get("to", "2027-01-01"))
            if start.tzinfo or end.tzinfo or start >= end:
                raise ValueError
        except ValueError:
            raise InvalidRequest(422, "Укажите корректный период") from None
        rows = db.query("""
            WITH slot_load AS (
                SELECT t.specialist_id, count(*) AS available_slots
                FROM slots t WHERE t.starts_at >= %s AND t.starts_at < %s
                GROUP BY t.specialist_id
            ), activity AS (
                SELECT t.specialist_id,
                  count(*) FILTER (WHERE a.status='booked') AS booked,
                  count(*) FILTER (WHERE a.status='cancelled') AS cancelled
                FROM appointments a JOIN slots t ON t.id=a.slot_id
                WHERE t.starts_at >= %s AND t.starts_at < %s GROUP BY t.specialist_id
            )
            SELECT p.id, p.name, coalesce(l.available_slots,0) AS slots,
                   coalesce(a.booked,0) AS booked, coalesce(a.cancelled,0) AS cancelled
            FROM specialists p LEFT JOIN slot_load l ON l.specialist_id=p.id
            LEFT JOIN activity a ON a.specialist_id=p.id ORDER BY p.id
        """, start, end, start, end)
        booked = sum(row["booked"] for row in rows)
        cancelled = sum(row["cancelled"] for row in rows)
        for row in rows:
            row["utilization"] = row["booked"] / row["slots"] if row["slots"] else 0
        return 200, {"from": start, "to": end, "booked": booked, "cancelled": cancelled,
                     "cancelled_share": cancelled / (booked + cancelled) if booked + cancelled else 0,
                     "specialists": rows}
    if method == "POST" and path == "/api/appointments":
        return book(db, body)
    match = re.fullmatch(r"/api/appointments/([0-9]+)(/cancel)?", path)
    if match:
        appointment_id = number(match[1], "id")
        if method == "GET" and not match[2]:
            return 200, detail(db, appointment_id)
        if method == "POST" and match[2]:
            if not db.query("UPDATE appointments SET status='cancelled' WHERE id=%s RETURNING id",
                            appointment_id):
                raise InvalidRequest(404, "Запись не найдена")
            return 200, detail(db, appointment_id)
    raise InvalidRequest(404, "Операция не найдена")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def handle_request(self):
        started = perf_counter()
        db = None
        url = urlsplit(self.path)
        credential = base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        if not hmac.compare_digest(self.headers.get("Authorization", "").encode(), f"Basic {credential}".encode()):
            self.respond(401, b'{"error":"Unauthorized"}', "application/json", started, db)
            return
        if self.command == "GET" and url.path == "/":
            self.respond(200, (ROOT / "index.html").read_bytes(), "text/html; charset=utf-8", started, db)
            return
        try:
            body = {}
            if self.command == "POST":
                length = number(self.headers.get("Content-Length", "0"), "Content-Length", 0, 65536)
                body = json.loads(self.rfile.read(length)) if length else {}
                if not isinstance(body, dict):
                    raise InvalidRequest(422, "Ожидается JSON-объект")
            parameters = {key: values[-1] for key, values in parse_qs(url.query).items()}
            with psycopg.connect(DSN, options=OPTIONS, row_factory=dict_row) as connection:
                db = Database(connection)
                status, result = dispatch(db, self.command, url.path, parameters, body)
        except (ValueError, UnicodeError):
            status, result = 422, {"error": "Некорректные данные"}
        except InvalidRequest as exc:
            status, result = exc.status, {"error": str(exc)}
        except psycopg.Error:
            self.log_error("Database request failed")
            status, result = 503, {"error": "База данных недоступна"}
        payload = json.dumps(result, ensure_ascii=False, default=str).encode()
        self.respond(status, payload, "application/json; charset=utf-8", started, db)

    def respond(self, status, payload, content_type, started, db):
        total = (perf_counter() - started) * 1000
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        if status == 401:
            self.send_header("WWW-Authenticate", 'Basic realm="Appointment service"')
        self.send_header("X-Response-Time-Ms", f"{total:.3f}")
        self.send_header("X-DB-Time-Ms", f"{db.milliseconds if db else 0:.3f}")
        self.send_header("X-DB-Queries", str(db.queries if db else 0))
        self.end_headers()
        self.wfile.write(payload)


if __name__ == "__main__":
    port = int(os.getenv("APP_PORT", "8765"))
    print(f"stanislav_tedety | http://127.0.0.1:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
