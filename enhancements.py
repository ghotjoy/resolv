"""Campus reference data, evidence storage, transcription and filtered reporting."""
import base64
import binascii
from collections import Counter
from contextlib import contextmanager
from datetime import date, timedelta
from calendar import monthrange
import json
import math
import os
from pathlib import Path
import re
import subprocess
import uuid
from zipfile import BadZipFile, ZipFile
from io import BytesIO

from resolv import InvalidTransition, NotFound, ValidationError, require_role

ASSETS = Path(__file__).resolve().parent / "assets"


class CampusLocations:
    def __init__(self, repo, now):
        self.repo, self.now = repo, now
        inventory = json.loads((ASSETS / "campus.json").read_text())
        self.version, self.bounds = inventory["version"], inventory["bounds"]
        self.areas = {area["id"]: area for area in inventory["areas"]}
        self.areas.update(repo.cfg("campus_areas") or {})

    def public(self):
        return dict(version=self.version, bounds=self.bounds, areas=list(self.areas.values()))

    def validate(self, value):
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValidationError("invalid location")
        area, pin = value.get("area"), value.get("pin")
        if area and (area not in self.areas or not self.areas[area]["active"]):
            raise ValidationError("choose an active campus area")
        if pin is not None:
            if not isinstance(pin, dict) or pin.get("version") != self.version:
                raise ValidationError("map version changed, select the pin again")
            lat, lon = pin.get("lat"), pin.get("lon")
            if not self.contains(lat, lon):
                raise ValidationError("pin is outside the campus map")
            pin = dict(lat=round(lat, 7), lon=round(lon, 7), version=self.version)
        return dict(area=area or None, pin=pin) if area or pin else None

    def contains(self, lat, lon):
        west, south, east, north = self.bounds
        return (type(lat) in (float, int) and type(lon) in (float, int)
                and south <= lat <= north and west <= lon <= east)

    def update(self, admin, fields):
        require_role(admin, "ADMIN")
        key = (fields.get("id") or "").strip()
        existing = self.areas.get(key, {})
        if not re.fullmatch(r"[a-z][a-z0-9-]{1,79}", key):
            raise ValidationError("use a stable lowercase area ID")
        area = {**existing, **fields, "id": key}
        name = (area.get("name") or "").strip()
        group = area.get("group")
        if not 1 <= len(name) <= 100 or group not in {"Hostels", "Academic", "Administration", "Amenities", "Sports", "Residential"}:
            raise ValidationError("enter an area name and a valid group")
        if not self.contains(area.get("lat"), area.get("lon")) or type(area.get("active")) is not bool:
            raise ValidationError("enter map coordinates and an active status")
        area = {k: area[k] for k in ("id", "name", "group", "lat", "lon", "active")}
        updates = dict(self.repo.cfg("campus_areas") or {}, **{key: area})
        with self.repo.transaction():
            self.repo.save_cfg("campus_areas", updates)
            self.repo.audit(self.now().isoformat(timespec="seconds"), admin.id, "CAMPUS_AREA_UPDATED", "-",
                            None, None, json.dumps({"previous": existing, "current": area}))
        self.areas[key] = area
        return area


class LocalAttachmentStorage:
    def __init__(self, path):
        self.path = Path(path)

    def write(self, key, data):
        self.path.mkdir(mode=0o700, parents=True, exist_ok=True)
        stored = self.path / key
        handle = stored.open("xb")
        try:
            with handle:
                handle.write(data)
        except BaseException:
            stored.unlink(missing_ok=True)
            raise

    def read(self, key):
        try:
            return (self.path / key).read_bytes()
        except FileNotFoundError as ex:
            raise NotFound("attachment file unavailable") from ex

    def discard(self, key):
        (self.path / key).unlink(missing_ok=True)


class Attachments:
    types = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
        ".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".txt": "text/plain",
    }

    def __init__(self, storage, service, repo, now, max_count=5, max_bytes=10 * 1024 * 1024):
        self.storage, self.service, self.repo, self.now = storage, service, repo, now
        self.max_count, self.max_bytes = max_count, max_bytes

    def recognized(self, mime, data):
        if mime == "image/jpeg":
            return data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9")
        if mime == "image/png":
            return data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR" and b"IEND" in data[-12:]
        if mime == "image/webp":
            return (data.startswith(b"RIFF") and data[8:12] == b"WEBP"
                    and int.from_bytes(data[4:8], "little") + 8 == len(data))
        if mime == "application/pdf":
            return data.startswith(b"%PDF-") and data.rstrip().endswith(b"%%EOF")
        if mime == "text/plain":
            text = data.decode("utf-8-sig")
            return all(c.isprintable() or c in "\t\r\n" for c in text)
        with ZipFile(BytesIO(data)) as archive:
            return {"[Content_Types].xml", "word/document.xml"} <= set(archive.namelist())

    def validate(self, file):
        filename = file.get("name", "").replace("\\", "/").split("/")[-1]
        mime = self.types.get(Path(filename.lower()).suffix)
        content = file.get("data", "")
        if not mime or not filename or len(filename) > 200 or any(ord(c) < 32 for c in filename):
            raise ValidationError("unsupported attachment filename")
        if file.get("type") not in (None, "", "application/octet-stream", mime):
            raise ValidationError(f"attachment format mismatch for {filename}")
        if not isinstance(content, str) or len(content) > math.ceil(self.max_bytes / 3) * 4:
            raise ValidationError(f"{filename} exceeds the file-size limit")
        try:
            raw = base64.b64decode(content, validate=True)
            valid = 0 < len(raw) <= self.max_bytes and self.recognized(mime, raw)
        except (ValueError, UnicodeError, binascii.Error, BadZipFile):
            valid = False
        if not valid:
            raise ValidationError(f"invalid file contents or size for {filename}")
        return filename, mime, raw

    @contextmanager
    def stage(self, actor, files, existing=0):
        if not isinstance(files, (list, tuple)) or len(files) + existing > self.max_count:
            raise ValidationError(f"at most {self.max_count} files per incident")
        validated = [self.validate(file) for file in files]
        metadata = []
        try:
            for name, mime, data in validated:
                key = uuid.uuid4().hex
                self.storage.write(key, data)
                metadata.append(dict(id=key, name=name, type=mime, size=len(data), uploader=actor.id,
                                     uploaded=self.now().isoformat(timespec="seconds")))
            yield metadata
        except BaseException:
            for file in metadata:
                self.storage.discard(file["id"])
            raise

    def add(self, actor, iid, files):
        incident = self.service.accessible(actor, iid)
        if incident.state == "CLOSED":
            raise InvalidTransition("reopen the incident before adding evidence")
        with self.stage(actor, files, len(self.repo.attachments(iid))) as metadata:
            with self.repo.transaction():
                self.repo.add_attachments(iid, metadata)
                if metadata:
                    self.service.emit("ATTACHMENTS_ADDED", incident, actor.id, incident.state,
                                      f"{len(metadata)} attachment(s) added")
        return self.repo.attachments(iid)

    def download(self, actor, iid, key):
        self.service.accessible(actor, iid)
        for file in self.repo.attachments(iid):
            if file["id"] == key:
                return file, self.storage.read(key)
        raise NotFound("attachment")


class SpeechToText:
    def transcribe(self, audio):
        raise NotImplementedError


class LocalVosk(SpeechToText):
    """Transcribe a short English recording in memory using a local model."""
    def __init__(self, model_path):
        self.model_path, self.model = model_path, None

    def transcribe(self, audio):
        try:
            from vosk import KaldiRecognizer, Model, SetLogLevel
            if self.model is None:
                SetLogLevel(-1)
                self.model = Model(self.model_path)
            result = subprocess.run(
                ["ffmpeg", "-v", "error", "-nostdin", "-threads", "1",
                 "-protocol_whitelist", "pipe", "-i", "pipe:0", "-vn", "-t", "60",
                 "-f", "s16le", "-ar", "16000", "-ac", "1", "pipe:1"],
                input=audio, capture_output=True, check=True, timeout=15,
            )
            recognizer = KaldiRecognizer(self.model, 16000)
            chunks = []
            for offset in range(0, len(result.stdout), 4000):
                if recognizer.AcceptWaveform(result.stdout[offset:offset + 4000]):
                    chunks.append(json.loads(recognizer.Result())["text"])
            chunks.append(json.loads(recognizer.FinalResult())["text"])
        except (ImportError, OSError, RuntimeError, subprocess.SubprocessError) as ex:
            raise ValidationError("transcription failed. You can still report the issue manually.") from ex
        text = " ".join(filter(None, chunks))
        if not text:
            raise ValidationError("no speech was recognized. Try again or report manually.")
        return text


class IncidentDraftBuilder:
    def __init__(self, system, campus):
        self.system, self.campus = system, campus

    def build(self, transcript):
        transcript = transcript.strip()[:5000]
        if not transcript:
            raise ValidationError("enter a transcript")
        text = transcript.casefold()
        service = next((name for name in sorted(self.system.services, key=len, reverse=True)
                        if re.search(r"\b" + re.escape(name.casefold()) + r"\b", text)), None)
        area = next((item["id"] for item in sorted(self.campus.areas.values(), key=lambda item: -len(item["name"]))
                     if item["active"] and re.search(r"\b" + re.escape(item["name"].casefold()) + r"\b", text)), None)
        title = re.split(r"[.!?\n]", transcript, maxsplit=1)[0]
        title = " ".join(title.split()[:12])[:200] or transcript[:100]
        return dict(title=title[:1].upper() + title[1:], description=transcript, service=service,
                    category=self.system.suggest_category(transcript), area=area)


def analytics(system, actor, filters, period, personal=False):
    if not personal:
        require_role(actor,"ADMIN")
    if period not in ("daily", "weekly", "monthly"):
        raise ValidationError("unknown trend interval")
    for edge in ("start", "end"):
        if edge in filters:
            date.fromisoformat(filters[edge])
    if filters.get("start", "") > filters.get("end", "9999"):
        raise ValidationError("start date must precede the end date")
    incidents = system.report_service.search(actor=actor if personal else None, **filters)
    status, priority, service, category, sla, area, workload = (Counter() for _ in range(7))
    trend = {}

    def bucket_for(day):
        start, end = day, day
        if period == "weekly":
            start = day - timedelta(days=day.weekday())
            end = start + timedelta(days=min(6, (date.max - start).days))
        if period == "monthly":
            start = day.replace(day=1)
            end = day.replace(day=monthrange(day.year, day.month)[1])
        return start, end

    for incident in incidents:
        status[incident.state] += 1
        priority[incident.priority] += 1
        service[incident.service] += 1
        category[incident.category] += 1
        sla[system.sla_status(incident)] += 1
        location = incident.location or {}
        area[location.get("area") or ("pin_only" if location.get("pin") else "none")] += 1
        if incident.in_workload and incident.tech:
            workload[incident.tech] += 1
        start, end = bucket_for(incident.created.date())
        key = start.isoformat()
        bucket = trend.setdefault(key, dict(start=key, end=end.isoformat(), count=0))
        bucket["count"] += 1
    if trend:
        first = date.fromisoformat(filters.get("start", min(trend)))
        last = date.fromisoformat(filters.get("end", max(trend)))
        # Bound the number of empty buckets so broad date ranges stay readable.
        if 0 <= (last - first).days <= {"daily": 92, "weekly": 730, "monthly": 3650}[period]:
            current = first
            while current <= last:
                start, end = bucket_for(current)
                key = start.isoformat()
                trend.setdefault(key, dict(start=key, end=end.isoformat(), count=0))
                if end >= last:
                    break
                current = end + timedelta(days=1)
    metrics = dict(total=len(incidents), open=sum(i.sla_tracked for i in incidents),
                   urgent=priority["P1"] + priority["P2"], at_risk=sla["AT_RISK"], breached=sla["BREACHED"])
    overview = dict(total=metrics["total"], open=metrics["open"], critical=priority["P1"],
                    resolved=metrics["total"] - metrics["open"], sla_violations=sla["BREACHED"])
    metrics.update(critical_open=sum(i.sla_tracked and i.priority == "P1" for i in incidents),
                   resolved=overview["resolved"])
    ordered = sorted(incidents, key=lambda i: (i.created, i.id), reverse=True)
    service_health = {}
    hourly = {}
    deadlines = []
    for incident in ordered:
        current = service_health.setdefault(incident.service, dict(total=0, resolved=0, breached=0, critical=0))
        current["total"] += 1
        current["resolved"] += not incident.sla_tracked
        current["breached"] += system.sla_status(incident) == "BREACHED"
        current["critical"] += incident.priority == "P1"
        if incident.tech:
            hourly.setdefault(incident.tech, [0] * 24)[incident.created.hour] += 1
        if incident.sla_tracked and incident.priority in ("P1","P2"):
            response, resolution = system.deadlines(incident)
            deadline = resolution if incident.responded else response
            elapsed = incident.sla_fraction(system.now(),deadline)
            deadlines.append(dict(id=incident.id, title=incident.title, priority=incident.priority,
                                  deadline=deadline.isoformat(), sla=system.sla_status(incident),
                                  progress=max(0, min(100, round(elapsed * 100)))))
    visible = {i.id for i in ordered}
    activities = ([activity for activity in system.report_service.audit_records(actor,sort="newest")
                   if activity["inc"] in visible][:8] if actor.role == "ADMIN" else [])
    visible_techs = {i.tech for i in ordered}
    capacity = {u.id:u.capacity for u in system.users.values() if u.role == "TECHNICIAN"
                and (actor.role == "ADMIN" or u.id == actor.id or u.id in visible_techs)}
    return dict(metrics=metrics, overview=overview, status=status, priority=priority, services=service, categories=category,
                sla=sla, areas=area, workload=workload, trend=[trend[key] for key in sorted(trend)],
                recent=[dict(id=i.id,title=i.title,priority=i.priority,service=i.service,state=i.state,created=i.created.isoformat()) for i in ordered[:6]],
                deadlines=sorted(deadlines,key=lambda entry:entry["deadline"])[:6], service_health=service_health,
                hourly=hourly, capacity=capacity,
                activity=[dict(id=a["inc"],action=a["action"],ts=a["ts"]) for a in activities])
