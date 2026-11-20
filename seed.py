"""Reset the dedicated course schema to a deterministic dataset."""
import argparse

import psycopg

from app import DSN, OPTIONS, ROOT

VOLUMES = {"small": (10, 10, 500, 300), "work": (200, 30, 200000, 60000)}


def seed(volume):
    specialists, services, slots, appointments = VOLUMES[volume]
    with psycopg.connect(DSN, options=OPTIONS) as c:
        c.execute((ROOT / "schema.sql").read_text(encoding="utf-8"))
        c.execute("TRUNCATE appointments, slots, services, specialists RESTART IDENTITY")
        c.execute("INSERT INTO specialists SELECT i, 'Специалист ' || i FROM generate_series(1,%s) i", (specialists,))
        c.execute("INSERT INTO services SELECT i, 'Услуга ' || i, 30 + 15*(i%%3) FROM generate_series(1,%s) i", (services,))
        c.execute("""
            INSERT INTO slots
            SELECT i, 1+(i-1)%%%(s)s,
              timestamp '2026-11-18 09:00' + ((i-1)/%(s)s)*interval '90 minutes',
              timestamp '2026-11-18 09:00' + ((i-1)/%(s)s)*interval '90 minutes'
                + (30 + 15*(i%%3))*interval '1 minute'
            FROM generate_series(1,%(n)s) i
        """, {"s": specialists, "n": slots})
        c.execute("""
            INSERT INTO appointments(slot_id, service_id, status)
            SELECT i, 1+((i+2)%%3)+3*((i/3)%%(%s/3)),
                   CASE WHEN i%%5=0 THEN 'cancelled' ELSE 'booked' END
            FROM generate_series(1,%s) i
        """, (services, appointments))
        c.execute("ANALYZE")
        print(f"stanislav_tedety | {volume}")
        for table in ("specialists", "services", "slots", "appointments"):
            print(f"{table:15} {c.execute('SELECT count(*) FROM ' + table).fetchone()[0]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("volume", choices=VOLUMES)
    seed(parser.parse_args().volume)
