"""Safe CSV views of an already authorized incident selection."""
import csv
from io import StringIO


def csv_cell(value):
    if not isinstance(value, str):
        return value
    # Quoting does not prevent spreadsheet formula evaluation.
    return "'" + value if value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")) else value


def incident_csv(system, incidents):
    out = StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(("ID", "Title", "Service", "Category", "Priority", "Status", "Reporter", "Technician",
                     "Created", "Response due", "Resolution due", "SLA", "Escalation"))
    for incident in sorted(incidents, key=lambda item: (item.created, item.id), reverse=True):
        response, resolution = system.deadlines(incident)
        writer.writerow(map(csv_cell, (incident.id, incident.title, incident.service, incident.category,
                        incident.priority, incident.state, incident.reporter, incident.tech, incident.created.isoformat(),
                        response.isoformat(), resolution.isoformat(), system.sla_status(incident), incident.escalation_level)))
    return out.getvalue().encode("utf-8")
