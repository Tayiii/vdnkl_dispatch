from datetime import date, datetime, time, timedelta
from pathlib import Path
import uuid

WINDOWS = [(time(8, 0), time(12, 0)), (time(13, 0), time(16, 0))]


def day_slots(day: date, slot_minutes: int):
    result = []
    for start_t, end_t in WINDOWS:
        cur = datetime.combine(day, start_t)
        end = datetime.combine(day, end_t)
        while cur < end:
            result.append(cur)
            cur += timedelta(minutes=slot_minutes)
    return result


def save_upload(base_dir: Path, appointment_id: int, filename: str, content: bytes) -> str:
    ext = Path(filename).suffix or ".bin"
    rel = Path("uploads") / str(appointment_id)
    full = base_dir / rel
    full.mkdir(parents=True, exist_ok=True)
    unique = f"{uuid.uuid4().hex}{ext}"
    target = full / unique
    target.write_bytes(content)
    return str(rel / unique)
