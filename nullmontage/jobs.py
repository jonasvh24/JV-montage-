"""Job state: one directory per job under data/jobs/<id>, with a JSON meta file and a render thread."""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "jobs"


@dataclass
class Job:
    id: str
    dir: Path
    status: str = "new"          # new | analyzing | planned | rendering | done | error
    progress: float = 0.0
    message: str = ""
    photos: list[str] = field(default_factory=list)   # ordered filenames within dir/photos
    audio: str | None = None
    output: str | None = None
    error: str | None = None
    created: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    @property
    def photo_dir(self) -> Path:
        return self.dir / "photos"

    def photo_paths(self) -> list[Path]:
        return [self.photo_dir / p for p in self.photos]

    def to_dict(self) -> dict:
        return {
            "id": self.id, "status": self.status, "progress": round(self.progress, 4), "message": self.message,
            "photos": self.photos, "audio": self.audio, "output": self.output, "error": self.error, "created": self.created,
        }

    def save(self) -> None:
        (self.dir / "job.json").write_text(json.dumps(self.to_dict()))

    def set(self, **kw) -> None:
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, v)
            self.save()


class JobStore:
    def __init__(self, root: Path = DATA_DIR):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, Job] = {}
        self._load()

    def _load(self) -> None:
        for d in self.root.iterdir():
            meta = d / "job.json"
            if d.is_dir() and meta.exists():
                try:
                    m = json.loads(meta.read_text())
                    job = Job(id=m["id"], dir=d, status=m.get("status", "new"), progress=m.get("progress", 0.0),
                              message=m.get("message", ""), photos=m.get("photos", []), audio=m.get("audio"),
                              output=m.get("output"), error=m.get("error"), created=m.get("created", 0.0))
                    if job.status == "rendering":  # interrupted by a restart
                        job.status = "error"
                        job.error = "render interrupted by server restart"
                    self.jobs[job.id] = job
                except Exception:
                    continue

    def create(self) -> Job:
        jid = uuid.uuid4().hex[:10]
        d = self.root / jid
        (d / "photos").mkdir(parents=True, exist_ok=True)
        job = Job(id=jid, dir=d)
        job.save()
        self.jobs[jid] = job
        return job

    def get(self, jid: str) -> Job | None:
        return self.jobs.get(jid)

    def list(self) -> list[dict]:
        return [j.to_dict() for j in sorted(self.jobs.values(), key=lambda j: -j.created)]


def run_in_thread(fn, *args, **kwargs) -> threading.Thread:
    t = threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True)
    t.start()
    return t
