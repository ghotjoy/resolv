# Resolv diagrams

[Back to the README](../README.md) · [Final project report](../Resolv_Project_Report.pdf)

## Application architecture

```mermaid
flowchart TD
    Browser["Browser workspaces"] --> API["HTTP server: sessions, validation, authorization"]
    API --> Facade["Resolv facade"]
    Facade --> Services["IncidentService, UserService, ConfigService, ReportService"]
    Services --> Domain["User, Incident, State, assignment and priority strategies"]
    Services --> Repo["SQLiteRepository and schema migrations"]
    Services --> Bus["EventBus"]
    Bus --> Audit["AuditObserver"]
    Bus --> Notify["NotificationObserver"]
    Audit --> Repo
    Notify --> Repo
    Notify --> Mail["In-app feed and optional SMTP"]
    API --> Evidence["Private attachment storage"]
```

## Services and strategy contracts

```mermaid
classDiagram
    class Resolv
    class IncidentService
    class UserService
    class ConfigService
    class ReportService
    class IncidentAnalyzer
    class SLAEngine
    class AssignmentStrategy {
        <<abstract>>
        +pick(candidates, incident, load)
    }
    class PriorityStrategy {
        <<abstract>>
        +compute(impact, urgency, criticality, users)
    }
    Resolv *-- IncidentService
    Resolv *-- UserService
    Resolv *-- ConfigService
    Resolv *-- ReportService
    Resolv *-- SLAEngine
    IncidentService --> IncidentAnalyzer
    IncidentService --> AssignmentStrategy
    IncidentService --> PriorityStrategy
    SkillBased --|> AssignmentStrategy
    LoadBased --|> AssignmentStrategy
    ExperienceBased --|> AssignmentStrategy
    RoundRobin --|> AssignmentStrategy
    RuleBased --|> PriorityStrategy
    WeightedScore --|> PriorityStrategy
    CategoryWeighted --|> PriorityStrategy
```

## Role use cases

```mermaid
flowchart LR
    E["Employee"] --> Submit["Report and track own issues"]
    E --> Reply["Reply, verify and leave feedback"]
    E --> Personal["View personal analytics"]
    T["Technician"] --> Assigned["Acknowledge, investigate and resolve assigned work"]
    A["Administrator"] --> Manage["Configure services, accounts and SLA policies"]
    A --> Close["Review audit history and close verified work"]
    L["Team lead / IT manager"] --> Tier["View eligible escalations"]
    T --> CSV["Export visible incidents"]
    A --> CSV
    L --> CSV
```

The [domain class diagram](class_a.png), [observer and repository diagram](class_c.png), [incident lifecycle](state.png), and [ER diagram](er.png) provide more detail.
