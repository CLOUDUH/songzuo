"""Back up and reclassify one confirmed, closed abnormal session.

Run inside the container: python -m app.repair_interruption --help
No repair runs automatically at startup.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .database import Database


def repair(db: Database, session_id: int, expected_start: str) -> str:
    key = f"interruption-repair-{session_id}-{expected_start}"
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        if connection.execute('SELECT 1 FROM repair_backups WHERE repair_key = ?', (key,)).fetchone():
            return key
        row = connection.execute('SELECT * FROM sessions WHERE id = ?', (session_id,)).fetchone()
        if row is None or row['started_at'] != expected_start:
            raise ValueError('Session identity does not match; nothing changed')
        if row['ended_at'] is None:
            raise ValueError('Session is still active; stop monitoring before repair')
        breaks = [dict(r) for r in connection.execute('SELECT * FROM session_breaks WHERE session_id = ?', (session_id,))]
        logs = [dict(r) for r in connection.execute("SELECT * FROM notification_log WHERE kind = 'sedentary' AND (period_key = ? OR period_key LIKE ?)", (str(session_id), f'{session_id}:%'))]
        cursor = connection.execute("INSERT INTO monitoring_interruptions(started_at, ended_at, reason) VALUES (?, ?, 'historical_unknown')", (row['started_at'], row['ended_at']))
        payload = {'sessions': [dict(row)], 'breaks': breaks, 'notifications': logs, 'interruption_id': cursor.lastrowid}
        connection.execute('INSERT INTO repair_backups(repair_key,created_at,payload) VALUES (?,?,?)', (key, datetime.now(timezone.utc).isoformat(), json.dumps(payload)))
        connection.execute('DELETE FROM sessions WHERE id = ?', (session_id,))
        for item in logs:
            connection.execute('DELETE FROM notification_log WHERE id = ?', (item['id'],))
    return key


def restore(db: Database, key: str) -> None:
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute('SELECT payload FROM repair_backups WHERE repair_key = ?', (key,)).fetchone()
        if row is None:
            raise ValueError('Backup not found')
        payload = json.loads(row[0])
        for table, items in [('sessions', payload['sessions']), ('session_breaks', payload['breaks']), ('notification_log', payload['notifications'])]:
            for item in items:
                columns = ','.join(item)
                placeholders = ','.join('?' for _ in item)
                connection.execute(f'INSERT INTO {table} ({columns}) VALUES ({placeholders})', list(item.values()))
        connection.execute('DELETE FROM monitoring_interruptions WHERE id = ?', (payload['interruption_id'],))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=Path('/app/data/songzuo.db'))
    parser.add_argument('--session-id', type=int)
    parser.add_argument('--expected-start')
    parser.add_argument('--restore', metavar='BACKUP_KEY')
    args = parser.parse_args()
    db = Database(args.database)
    db.initialize()
    if args.restore:
        restore(db, args.restore)
        print('Restored')
    elif args.session_id and args.expected_start:
        print('Backup key:', repair(db, args.session_id, args.expected_start))
    else:
        parser.error('Supply --session-id and --expected-start, or --restore')
