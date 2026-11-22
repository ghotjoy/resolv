"""Configured service dependencies and explainable correlation."""
from collections import Counter, deque
from resolv import ValidationError, require_role


class ServiceTopology:
    def __init__(self, system):
        self.system = system
        self.edges = {key: set(values) for key, values in (system.repo.cfg("dependencies") or {}).items()}

    def ancestors(self, service):
        levels = {service: 0}
        pending = deque([service])
        while pending:
            node = pending.popleft()
            for upstream in sorted(self.edges.get(node, ())):
                if upstream not in levels:
                    levels[upstream] = levels[node] + 1
                    pending.append(upstream)
        return levels

    def configure(self, actor, service, upstream, remove=False):
        require_role(actor, "ADMIN")
        if service not in self.system.services or upstream not in self.system.services:
            raise ValidationError("select two configured services")
        if not remove and service in self.ancestors(upstream):
            raise ValidationError("a service cannot depend on itself or form a dependency cycle")
        updated = {key: set(values) for key, values in self.edges.items()}
        links = updated.setdefault(service, set())
        (links.discard if remove else links.add)(upstream)
        with self.system.repo.transaction():
            self.system.repo.save_cfg("dependencies", {key: sorted(values) for key, values in updated.items()})
            self.system.repo.audit(self.system.now().isoformat(timespec="seconds"), actor.id,
                                   "DEPENDENCY_UPDATED", "-", None, None,
                                   f"{'Removed' if remove else 'Added'} {service} → {upstream}")
        self.edges = updated

    def visible(self, actor):
        allowed = self.system.incident_service.can_view
        return [i for i in self.system.incidents.values() if allowed(actor, i)]

    def view(self, actor):
        counts = Counter(i.service for i in self.visible(actor) if i.sla_tracked)
        return [dict(name=name, open=counts[name], upstream=sorted(self.edges.get(name, ())),
                     downstream=sorted(key for key, dependencies in self.edges.items() if name in dependencies))
                for name in sorted(self.system.services)]

    def insights(self, actor, incident):
        visible = {i.id: i for i in self.visible(actor)}
        details = {row["id"]:row for row in self.system.incident_service.relationships(incident)
                   if row["id"] in visible}
        related = {key for key,row in details.items() if row["decision"] != "DISMISSED"}
        related.update(i.id for i in visible.values() if i.parent == incident.id)
        if incident.parent in visible:
            related.add(incident.parent)
            related.update(i.id for i in visible.values() if i.parent == incident.parent and i.id != incident.id)
        cluster = [incident, *(visible[key] for key in sorted(related) if key in visible)]
        paths = [self.ancestors(i.service) for i in cluster]
        active = Counter(i.service for i in visible.values() if i.sla_tracked)
        shared = set.intersection(*(set(path) for path in paths)) if len(cluster) > 1 else set()
        candidates = sorted((key for key in shared if active[key] and any(path[key] for path in paths)),
                            key=lambda key: (sum(path[key] for path in paths), -active[key], key))
        return dict(parent=incident.parent if incident.parent in visible else None,
                    related=[dict(id=i.id,title=i.title,service=i.service,kind=details.get(i.id,{}).get("kind")) for i in cluster[1:]],
                    relationships=[dict(row,title=visible[key].title) for key,row in details.items() if
                                   actor.role == "ADMIN" or row["decision"] != "DISMISSED"],
                    root_cause=dict(service=candidates[0], active=active[candidates[0]], matched=len(cluster)) if candidates else None)
