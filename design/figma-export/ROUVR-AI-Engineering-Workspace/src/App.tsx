import { useMemo, useState } from "react"

type Page = "Dashboard" | "AI Chat" | "Projects" | "Project Details" | "Tasks" | "Task Details" | "AI Agents" | "Live Execution" | "Verification" | "Knowledge" | "Activity" | "Integrations" | "Settings" | "Login"

type IconName = "grid" | "chat" | "folder" | "check" | "bot" | "pulse" | "shield" | "book" | "clock" | "plug" | "settings" | "search" | "bell" | "chevron" | "plus" | "more" | "arrow" | "play" | "pause" | "filter" | "git" | "x" | "menu" | "spark" | "paperclip" | "send" | "code" | "users" | "sun" | "lock"

type WorkspaceRuntime = {
  paused: boolean
  running: boolean
  approved: boolean
  completedTasks: number
}

type WorkspaceEvent = {
  time: string
  actor: string
  action: string
  target: string
  icon: IconName
}

const paths: Record<IconName, React.ReactNode> = {
  grid: (
    <>
      <rect x="3" y="3" width="7" height="7" rx="2" />
      <rect x="14" y="3" width="7" height="7" rx="2" />
      <rect x="3" y="14" width="7" height="7" rx="2" />
      <rect x="14" y="14" width="7" height="7" rx="2" />
    </>
  ),
  chat: (
    <>
      <path d="M21 14a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z" />
      <path d="M8 9h8M8 13h5" />
    </>
  ),
  folder: (
    <path d="M3 6a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v9a3 3 0 0 1-3 3H6a3 3 0 0 1-3-3z" />
  ),
  check: (
    <>
      <path d="M9 11l3 3L22 4" />
      <path d="M21 12a9 9 0 1 1-5-8" />
    </>
  ),
  bot: (
    <>
      <rect x="4" y="7" width="16" height="13" rx="4" />
      <path d="M9 12h.01M15 12h.01M9 16h6M12 7V3M9 3h6" />
    </>
  ),
  pulse: <path d="M3 12h4l2-7 4 14 2-7h6" />,
  shield: (
    <>
      <path d="M12 22s8-3.8 8-10V5l-8-3-8 3v7c0 6.2 8 10 8 10z" />
      <path d="m9 12 2 2 4-5" />
    </>
  ),
  book: (
    <>
      <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5z" />
      <path d="M8 7h8M8 11h6" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
  plug: (
    <>
      <path d="M8 12V7M16 12V7M6 12h12v2a6 6 0 0 1-12 0zM12 20v2" />
    </>
  ),
  settings: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3A1.7 1.7 0 0 0 10 3V2.8h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1z" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-4-4" />
    </>
  ),
  bell: (
    <>
      <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9" />
      <path d="M10 21h4" />
    </>
  ),
  chevron: <path d="m9 18 6-6-6-6" />,
  plus: <path d="M12 5v14M5 12h14" />,
  more: <path d="M5 12h.01M12 12h.01M19 12h.01" />,
  arrow: <path d="M5 12h14M14 7l5 5-5 5" />,
  play: <path d="m8 5 11 7-11 7z" />,
  pause: <path d="M9 5v14M15 5v14" />,
  filter: <path d="M4 5h16l-6 7v5l-4 2v-7z" />,
  git: (
    <>
      <circle cx="6" cy="5" r="2" />
      <circle cx="18" cy="6" r="2" />
      <circle cx="6" cy="19" r="2" />
      <path d="M6 7v10M8 6h5a5 5 0 0 1 5 5v-3" />
    </>
  ),
  x: <path d="M6 6l12 12M18 6 6 18" />,
  menu: <path d="M4 7h16M4 12h16M4 17h16" />,
  spark: (
    <path d="m12 2 1.5 5.5L19 9l-5.5 1.5L12 16l-1.5-5.5L5 9l5.5-1.5zM19 16l.7 2.3L22 19l-2.3.7L19 22l-.7-2.3L16 19l2.3-.7z" />
  ),
  paperclip: (
    <path d="m20 12-8.5 8.5a5 5 0 0 1-7-7L14 4a3 3 0 0 1 4 4l-9.5 9.5a1 1 0 0 1-1.5-1.5L16 7" />
  ),
  send: <path d="m22 2-7 20-4-9-9-4zM22 2 11 13" />,
  code: <path d="m8 9-4 3 4 3M16 9l4 3-4 3M14 5l-4 14" />,
  users: (
    <>
      <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
      <circle cx="9" cy="7" r="4" />
      <path d="M22 21v-2a4 4 0 0 0-3-3.8M16 3.2a4 4 0 0 1 0 7.6" />
    </>
  ),
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </>
  ),
  lock: (
    <>
      <rect x="4" y="10" width="16" height="11" rx="2" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3" />
    </>
  ),
}

function Icon({ name, size = 18 }: { name: IconName size?: number }) {
  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {paths[name]}
    </svg>
  )
}

function RouvrMark({ size = 32 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 40 40"
      fill="none"
      aria-label="Rouvr blue dog mascot"
    >
      <rect width="40" height="40" rx="12" fill="#08A9F0" />
      <path
        d="M10 13.5 14.5 8l3 5h5l3-5 4.5 5.5-2 4.2v7.1c0 4-3.6 7.2-8 7.2s-8-3.2-8-7.2v-7.1z"
        fill="#061522"
      />
      <path
        d="M15.5 20.5c1.3-1.2 7.7-1.2 9 0v4c-1 2.4-2.5 3.5-4.5 3.5s-3.5-1.1-4.5-3.5z"
        fill="#EAF9FF"
      />
      <circle cx="16.5" cy="18" r="1.2" fill="#EAF9FF" />
      <circle cx="23.5" cy="18" r="1.2" fill="#EAF9FF" />
      <path
        d="m18 23 2 1.4 2-1.4"
        stroke="#061522"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

const nav: { section: string items: { label: Page icon: IconName }[] }[] = [
  {
    section: "Workspace",
    items: [
      { label: "Dashboard", icon: "grid" },
      { label: "AI Chat", icon: "chat" },
      { label: "Projects", icon: "folder" },
      { label: "Tasks", icon: "check" },
    ],
  },
  {
    section: "Intelligence",
    items: [
      { label: "AI Agents", icon: "bot" },
      { label: "Live Execution", icon: "pulse" },
      { label: "Verification", icon: "shield" },
      { label: "Knowledge", icon: "book" },
    ],
  },
  {
    section: "Manage",
    items: [
      { label: "Activity", icon: "clock" },
      { label: "Integrations", icon: "plug" },
      { label: "Settings", icon: "settings" },
    ],
  },
]

const agents = [
  {
    name: "Atlas",
    role: "Orchestrator",
    color: "#08A9F0",
    status: "Running",
    task: "Planning data migration",
  },
  {
    name: "Nova",
    role: "Frontend",
    color: "#A78BFA",
    status: "Running",
    task: "Building invoice table",
  },
  {
    name: "Forge",
    role: "Backend",
    color: "#F59E0B",
    status: "Running",
    task: "Implementing API routes",
  },
  {
    name: "Sentry",
    role: "QA & Security",
    color: "#38D9A9",
    status: "Verifying",
    task: "Running regression suite",
  },
  {
    name: "Query",
    role: "Data Engineer",
    color: "#FB7185",
    status: "Idle",
    task: "Awaiting assignment",
  },
  {
    name: "Scribe",
    role: "Documentation",
    color: "#60A5FA",
    status: "Idle",
    task: "Awaiting assignment",
  },
]

const tasks = [
  {
    id: "ERP-128",
    title: "Implement multi-currency invoice totals",
    status: "In progress",
    priority: "Urgent",
    agent: "Nova",
    project: "ERP Core",
    progress: 68,
  },
  {
    id: "ERP-127",
    title: "Create vendor onboarding workflow",
    status: "Verification",
    priority: "High",
    agent: "Sentry",
    project: "ERP Core",
    progress: 92,
  },
  {
    id: "ERP-126",
    title: "Migrate customer ledger schema",
    status: "In progress",
    priority: "High",
    agent: "Forge",
    project: "Data Platform",
    progress: 44,
  },
  {
    id: "ERP-125",
    title: "Add payment reconciliation tests",
    status: "Todo",
    priority: "Medium",
    agent: "Atlas",
    project: "ERP Core",
    progress: 0,
  },
  {
    id: "ERP-124",
    title: "Document inventory sync API",
    status: "Done",
    priority: "Low",
    agent: "Scribe",
    project: "Integrations",
    progress: 100,
  },
  {
    id: "ERP-123",
    title: "Optimize product catalog queries",
    status: "Blocked",
    priority: "High",
    agent: "Query",
    project: "Data Platform",
    progress: 31,
  },
]

function Avatar({
  name,
  color = "#08A9F0",
  size = 32,
}: {
  name: string
  color?: string
  size?: number
}) {
  return (
    <span
      className="avatar"
      style={
        { "--avatar": color, width: size, height: size } as React.CSSProperties
      }
    >
      {name[0]}
    </span>
  )
}

function Badge({
  children,
  tone = "neutral",
}: {
  children: React.ReactNode
  tone?: string
}) {
  return <span className={`badge ${tone}`}>{children}</span>
}

function Progress({
  value,
  color = "#08A9F0",
}: {
  value: number
  color?: string
}) {
  return (
    <div className="progress">
      <span style={{ width: `${value}%`, background: color }} />
    </div>
  )
}

function Button({
  children,
  variant = "secondary",
  icon,
  onClick,
}: {
  children: React.ReactNode
  variant?: string
  icon?: IconName
  onClick?: () => void
}) {
  return (
    <button className={`button ${variant}`} onClick={onClick}>
      {icon && <Icon name={icon} size={16} />}
      <span>{children}</span>
    </button>
  )
}

function PageHead({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string
  title: string
  description?: string
  actions?: React.ReactNode
}) {
  return (
    <header className="page-head">
      <div>
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="head-actions">{actions}</div>}
    </header>
  )
}

function Stat({
  label,
  value,
  delta,
  icon,
}: {
  label: string
  value: string
  delta: string
  icon: IconName
}) {
  return (
    <div className="panel stat">
      <div className="stat-icon">
        <Icon name={icon} />
      </div>
      <div className="muted">{label}</div>
      <div className="stat-row">
        <strong>{value}</strong>
        <span>{delta}</span>
      </div>
    </div>
  )
}

function Panel({
  title,
  action,
  children,
  className = "",
}: {
  title?: string
  action?: React.ReactNode
  children: React.ReactNode
  className?: string
}) {
  return (
    <section className={`panel ${className}`}>
      {title && (
        <div className="panel-head">
          <h3>{title}</h3>
          {action}
        </div>
      )}
      {children}
    </section>
  )
}

function Sparkline() {
  return (
    <svg className="sparkline" viewBox="0 0 600 180" preserveAspectRatio="none">
      <defs>
        <linearGradient id="area" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#08A9F0" stopOpacity=".28" />
          <stop offset="1" stopColor="#08A9F0" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path className="gridline" d="M0 30H600M0 80H600M0 130H600" />
      <path
        className="area"
        d="M0 150 C45 145 58 106 102 116 S170 130 205 90 S260 110 302 70 S365 85 400 48 S455 62 490 34 S550 56 600 18 L600 180H0Z"
      />
      <path
        className="line"
        d="M0 150 C45 145 58 106 102 116 S170 130 205 90 S260 110 302 70 S365 85 400 48 S455 62 490 34 S550 56 600 18"
      />
    </svg>
  )
}

function Dashboard({ go }: { go: (p: Page) => void }) {
  return (
    <>
      <PageHead
        eyebrow="MONDAY, MAY 26"
        title="Good morning, Alex"
        description="Here’s what your autonomous engineering team is working on."
        actions={
          <>
            <Button icon="chat" onClick={() => go("AI Chat")}>
              Ask Rouvr
            </Button>
            <Button
              variant="primary"
              icon="plus"
              onClick={() => go("Projects")}
            >
              New project
            </Button>
          </>
        }
      />
      <div className="stats-grid">
        <Stat
          label="Active projects"
          value="8"
          delta="+2 this month"
          icon="folder"
        />
        <Stat label="Tasks completed" value="394" delta="+12.4%" icon="check" />
        <Stat
          label="Agent hours saved"
          value="186h"
          delta="+23.1%"
          icon="bot"
        />
        <Stat
          label="Verification rate"
          value="96.8%"
          delta="+1.2%"
          icon="shield"
        />
      </div>
      <div className="dashboard-grid">
        <Panel
          title="Delivery velocity"
          className="chart-panel"
          action={
            <div className="segmented">
              <button className="active">30 days</button>
              <button>90 days</button>
            </div>
          }
        >
          <div className="chart-kpi">
            <strong>128</strong>
            <span>tasks completed</span>
            <Badge tone="success">+18.2%</Badge>
          </div>
          <Sparkline />
          <div className="axis">
            <span>May 1</span>
            <span>May 8</span>
            <span>May 15</span>
            <span>May 22</span>
            <span>May 26</span>
          </div>
        </Panel>
        <Panel
          title="AI team"
          action={
            <button className="text-button" onClick={() => go("AI Agents")}>
              View all <Icon name="arrow" size={14} />
            </button>
          }
        >
          <div className="agent-list">
            {agents.slice(0, 4).map((a) => (
              <div className="agent-row" key={a.name}>
                <Avatar name={a.name} color={a.color} />
                <div>
                  <strong>{a.name}</strong>
                  <small>{a.task}</small>
                </div>
                <span className={`status-dot ${a.status.toLowerCase()}`} />
              </div>
            ))}
          </div>
          <div className="capacity">
            <span>Team capacity</span>
            <strong>72%</strong>
            <Progress value={72} />
          </div>
        </Panel>
        <Panel
          title="Active tasks"
          className="wide"
          action={
            <button className="text-button" onClick={() => go("Tasks")}>
              View board <Icon name="arrow" size={14} />
            </button>
          }
        >
          <TaskTable
            rows={tasks.slice(0, 4)}
            onSelect={() => go("Task Details")}
          />
        </Panel>
        <Panel title="Requires attention" className="attention">
          <div className="alert-item">
            <span className="alert-icon warning">!</span>
            <div>
              <strong>Schema migration blocked</strong>
              <p>Query needs access to production replica.</p>
              <small>12 minutes ago</small>
            </div>
          </div>
          <div className="alert-item">
            <span className="alert-icon danger">×</span>
            <div>
              <strong>2 verification failures</strong>
              <p>Invoice rounding tests require review.</p>
              <small>34 minutes ago</small>
            </div>
          </div>
          <Button onClick={() => go("Verification")}>Review alerts</Button>
        </Panel>
      </div>
    </>
  )
}

function TaskTable({
  rows,
  onSelect,
}: {
  rows: typeof tasks
  onSelect: () => void
}) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Task</th>
            <th>Status</th>
            <th>Priority</th>
            <th>Assignee</th>
            <th>Progress</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((t) => (
            <tr key={t.id} onClick={onSelect}>
              <td>
                <small>{t.id}</small>
                <strong>{t.title}</strong>
              </td>
              <td>
                <Badge
                  tone={
                    t.status === "Done"
                      ? "success"
                      : t.status === "Blocked"
                        ? "danger"
                        : t.status === "Verification"
                          ? "purple"
                          : "cyan"
                  }
                >
                  {t.status}
                </Badge>
              </td>
              <td>
                <span className={`priority ${t.priority.toLowerCase()}`}>
                  {t.priority}
                </span>
              </td>
              <td>
                <span className="assignee">
                  <Avatar name={t.agent} size={25} />
                  {t.agent}
                </span>
              </td>
              <td>
                <div className="table-progress">
                  <Progress value={t.progress} />
                  <span>{t.progress}%</span>
                </div>
              </td>
              <td>
                <Icon name="more" />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function AIChat({
  go,
  runtime,
  events,
  onToggleRun,
  onStartRun,
  onApprove,
}: {
  go: (p: Page) => void
  runtime: WorkspaceRuntime
  events: WorkspaceEvent[]
  onToggleRun: () => void
  onStartRun: () => void
  onApprove: () => void
}) {
  const [sent, setSent] = useState(false)
  const [autonomy, setAutonomy] = useState("Supervised")
  const [contextOpen, setContextOpen] = useState(false)
  const [selectedTask, setSelectedTask] = useState("ERP-128")
  return (
    <div className={`chat-layout ${contextOpen ? "context-open" : ""}`}>
      <aside className="chat-history">
        <Button variant="primary" icon="plus">
          New conversation
        </Button>
        <div className="history-label">Today</div>
        {[
          "ERP payment workflows",
          "Vendor portal scope",
          "Review migration plan",
        ].map((x, i) => (
          <button className={i === 0 ? "selected" : ""} key={x}>
            <Icon name="chat" size={15} />
            <span>{x}</span>
            <Icon name="more" size={15} />
          </button>
        ))}
        <div className="history-label">Previous 7 days</div>
        {["Q2 platform roadmap", "Auth architecture", "Performance audit"].map(
          (x) => (
            <button key={x}>
              <Icon name="chat" size={15} />
              <span>{x}</span>
            </button>
          ),
        )}
      </aside>
      <main className="chat-main">
        <div className="chat-top">
          <div>
            <strong>ERP payment workflows</strong>
            <span>
              <i /> Atlas is online
            </span>
          </div>
          <div className="autonomy">
            <span>Autonomy</span>
            {["Manual", "Supervised", "Full"].map((x) => (
              <button
                className={autonomy === x ? "active" : ""}
                onClick={() => setAutonomy(x)}
                key={x}
              >
                {x}
              </button>
            ))}
            <button
              className="context-toggle"
              onClick={() => setContextOpen(!contextOpen)}
              aria-label="Toggle workspace context"
            >
              <Icon name="grid" size={14} />
              Context
            </button>
          </div>
        </div>
        <div className="messages">
          <div className="message user">
            <div className="bubble">
              Build the payment reconciliation workflow for the ERP. Include
              multi-currency matching, exception handling, and an approval step
              for differences over $500.
            </div>
            <Avatar name="Alex" />
          </div>
          <div className="message ai">
            <RouvrMark size={34} />
            <div className="ai-content">
              <div className="message-meta">
                ROUVR <Badge tone="cyan">ATLAS</Badge>
                <span>9:42 AM</span>
              </div>
              <div className="linked-context">
                <button onClick={() => go("Project Details")}>
                  <Icon name="folder" size={13} />
                  ERP Core
                </button>
                <button
                  onClick={() => {
                    setSelectedTask("ERP-128")
                    setContextOpen(true)
                  }}
                >
                  <Icon name="check" size={13} />8 linked tasks
                </button>
                <button onClick={() => go("AI Agents")}>
                  <Icon name="bot" size={13} />3 agents
                </button>
              </div>
              <p>
                I’ve analyzed the ERP architecture and your existing ledger
                service. I can deliver this as a coordinated workflow across
                three agents.
              </p>
              <Panel className="plan-card">
                <div className="plan-head">
                  <div>
                    <span className="plan-icon">
                      <Icon name="spark" />
                    </span>
                    <div>
                      <strong>Execution plan</strong>
                      <small>8 tasks · 3 agents · ~42 minutes</small>
                    </div>
                  </div>
                  <div className="plan-readiness">
                    <strong>100%</strong>
                    <small>Context ready</small>
                  </div>
                </div>
                {[
                  "Extend reconciliation schema and currency rates",
                  "Build matching engine with tolerance rules",
                  "Create exception review and approval flow",
                  "Run unit, integration and security verification",
                ].map((x, i) => (
                  <div className="plan-step" key={x}>
                    <span className="plan-step-index">{i + 1}</span>
                    <div className="plan-step-copy">
                      <strong>{x}</strong>
                      <small>
                        {
                          [
                            "Forge · 12 min",
                            "Forge · 14 min",
                            "Nova · 10 min",
                            "Sentry · 6 min",
                          ][i]
                        }
                      </small>
                    </div>
                    <div className="plan-step-status">
                      <div className="plan-step-progress">
                        <span style={{ width: `${[100, 100, 86, 72][i]}%` }} />
                      </div>
                      <small>
                        {["Scoped", "Scoped", "Mapped", "Ready to run"][i]}
                      </small>
                    </div>
                  </div>
                ))}
                <div className="plan-actions">
                  <Button
                    onClick={() => {
                      setSelectedTask("ERP-128")
                      setContextOpen(true)
                    }}
                  >
                    Review plan
                  </Button>
                  <Button
                    variant="primary"
                    icon={runtime.running ? "pulse" : "play"}
                    onClick={onStartRun}
                  >
                    {runtime.running ? "Execution active" : "Approve & execute"}
                  </Button>
                </div>
              </Panel>
              {runtime.running && (
                <div className="inline-execution">
                  <div className="inline-execution-top">
                    <span
                      className={`run-indicator ${
                        runtime.paused ? "paused" : ""
                      }`}
                    >
                      <Icon
                        name={runtime.paused ? "pause" : "pulse"}
                        size={15}
                      />
                    </span>
                    <div>
                      <strong>
                        {runtime.paused
                          ? "Execution paused"
                          : "Payment workflow is running"}
                      </strong>
                      <small>
                        {runtime.completedTasks} of 8 tasks complete · 3 agents
                        assigned
                      </small>
                    </div>
                    <Badge tone={runtime.paused ? "warning" : "success"}>
                      {runtime.paused ? "Paused" : "Live"}
                    </Badge>
                  </div>
                  <div className="inline-execution-progress">
                    <Progress value={(runtime.completedTasks / 8) * 100} />
                    <span>
                      {Math.round((runtime.completedTasks / 8) * 100)}%
                    </span>
                  </div>
                  <div className="inline-execution-actions">
                    <button onClick={onToggleRun}>
                      <Icon
                        name={runtime.paused ? "play" : "pause"}
                        size={14}
                      />
                      {runtime.paused ? "Resume agents" : "Pause agents"}
                    </button>
                    <button onClick={() => go("Live Execution")}>
                      Open live view <Icon name="arrow" size={13} />
                    </button>
                  </div>
                </div>
              )}
              <p>
                I’ll pause before any database migration or production-facing
                change. Would you like me to proceed?
              </p>
              {sent && (
                <div className="typing">
                  <i />
                  <i />
                  <i />
                  <span>Atlas is preparing execution context</span>
                </div>
              )}
            </div>
          </div>
        </div>
        <div className="composer-dock">
          <div className="composer">
            <div className="composer-tools">
              <button aria-label="Attach file">
                <Icon name="paperclip" />
              </button>
              <button aria-label="Add code context">
                <Icon name="code" />
              </button>
              <span>
                Use <b>@</b> to add context
              </span>
            </div>
            <textarea
              defaultValue={sent ? "" : ""}
              placeholder="Ask Rouvr to plan, build, or investigate…"
            />
            <button
              className="send"
              onClick={() => setSent(true)}
              aria-label="Send message"
            >
              <Icon name="arrow" />
            </button>
            <div className="composer-foot">
              <span>
                <Icon name="spark" size={14} /> ERP Core · 24 sources
              </span>
              <span>Rouvr can make mistakes · ⌘ ↵ to send</span>
            </div>
          </div>
        </div>
      </main>
      <aside className="chat-context">
        <div className="context-head">
          <div>
            <span>Workspace context</span>
            <strong>ERP Core Platform</strong>
          </div>
          <button
            onClick={() => setContextOpen(false)}
            aria-label="Close panel"
          >
            <Icon name="x" size={16} />
          </button>
        </div>
        <button
          className="context-project"
          onClick={() => go("Project Details")}
        >
          <div>
            <span className="context-project-icon">
              <Icon name="folder" />
            </span>
            <div>
              <strong>Payment operations</strong>
              <small>Milestone 3 of 5</small>
            </div>
            <Icon name="chevron" size={14} />
          </div>
          <Progress value={74} />
          <span>
            <small>94 of 128 tasks</small>
            <strong>74%</strong>
          </span>
        </button>
        <div className="context-section">
          <div className="context-section-head">
            <span>Running tasks</span>
            <button onClick={() => go("Tasks")}>View all</button>
          </div>
          {tasks.slice(0, 3).map((task, index) => (
            <button
              className={`context-task ${
                selectedTask === task.id ? "active" : ""
              }`}
              onClick={() => setSelectedTask(task.id)}
              key={task.id}
            >
              <span
                className={`context-task-state ${index === 1 ? "verify" : ""}`}
              />
              <div>
                <small>{task.id}</small>
                <strong>{task.title}</strong>
                <Progress
                  value={
                    task.id === "ERP-128" && runtime.approved
                      ? 100
                      : task.progress
                  }
                />
              </div>
              <span>
                {task.id === "ERP-128" && runtime.approved
                  ? "100%"
                  : `${task.progress}%`}
              </span>
            </button>
          ))}
          {selectedTask === "ERP-128" && (
            <div className="context-task-detail">
              <div>
                <span>Verification</span>
                <Badge tone={runtime.approved ? "success" : "warning"}>
                  {runtime.approved ? "Approved" : "18 / 21 passed"}
                </Badge>
              </div>
              <p>2 rounding cases need review. Security checks passed.</p>
              <div>
                <Button onClick={() => go("Task Details")}>Open task</Button>
                <Button variant="primary" icon="check" onClick={onApprove}>
                  {runtime.approved ? "Approved" : "Approve"}
                </Button>
              </div>
            </div>
          )}
        </div>
        <div className="context-section">
          <div className="context-section-head">
            <span>Active agents</span>
            <button onClick={() => go("AI Agents")}>Manage</button>
          </div>
          <div className="context-agents">
            {agents.slice(0, 3).map((agent) => (
              <button key={agent.name} onClick={() => go("AI Agents")}>
                <Avatar name={agent.name} color={agent.color} size={28} />
                <div>
                  <strong>{agent.name}</strong>
                  <small>{runtime.paused ? "Paused" : agent.task}</small>
                </div>
                <i className={runtime.paused ? "paused" : ""} />
              </button>
            ))}
          </div>
        </div>
        <div className="context-section context-activity">
          <div className="context-section-head">
            <span>Recent activity</span>
            <button onClick={() => go("Activity")}>History</button>
          </div>
          {events.slice(0, 3).map((event, index) => (
            <div key={`${event.action}-${index}`}>
              <span>
                <Icon name={event.icon} size={12} />
              </span>
              <p>
                <strong>{event.actor}</strong> {event.action}
                <small>{event.time}</small>
              </p>
            </div>
          ))}
        </div>
      </aside>
      {contextOpen && (
        <button
          className="context-scrim"
          onClick={() => setContextOpen(false)}
          aria-label="Close context panel"
        />
      )}
    </div>
  )
}

const projects = [
  {
    name: "ERP Core Platform",
    desc: "Modernize finance, inventory and procurement operations.",
    progress: 74,
    tasks: "94 / 128",
    color: "#08A9F0",
    agents: 4,
    status: "On track",
  },
  {
    name: "Vendor Portal",
    desc: "Self-service onboarding, documents and account management.",
    progress: 41,
    tasks: "18 / 44",
    color: "#A78BFA",
    agents: 2,
    status: "At risk",
  },
  {
    name: "Data Platform",
    desc: "Unified operational warehouse and reporting layer.",
    progress: 62,
    tasks: "31 / 50",
    color: "#38D9A9",
    agents: 3,
    status: "On track",
  },
  {
    name: "Mobile Operations",
    desc: "Field inventory and fulfillment applications.",
    progress: 18,
    tasks: "8 / 46",
    color: "#F59E0B",
    agents: 2,
    status: "Planning",
  },
  {
    name: "Customer Identity",
    desc: "Consolidated authentication and enterprise SSO.",
    progress: 89,
    tasks: "40 / 45",
    color: "#FB7185",
    agents: 2,
    status: "On track",
  },
  {
    name: "Integration Hub",
    desc: "Partner APIs, webhooks and observability tooling.",
    progress: 53,
    tasks: "29 / 55",
    color: "#60A5FA",
    agents: 3,
    status: "On track",
  },
]

function Projects({ go }: { go: (p: Page) => void }) {
  const [modal, setModal] = useState(false)
  const [query, setQuery] = useState("")
  return (
    <>
      <PageHead
        title="Projects"
        description="Plan, execute and deliver work with your autonomous team."
        actions={
          <Button variant="primary" icon="plus" onClick={() => setModal(true)}>
            New project
          </Button>
        }
      />
      <div className="toolbar">
        <div className="search-field">
          <Icon name="search" />
          <input
            placeholder="Search projects…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <div className="segmented">
          <button className="active">
            All projects <b>8</b>
          </button>
          <button>
            Active <b>6</b>
          </button>
          <button>
            Archived <b>2</b>
          </button>
        </div>
        <Button icon="filter">Filter</Button>
      </div>
      <div className="project-grid">
        {projects
          .filter((p) => p.name.toLowerCase().includes(query.toLowerCase()))
          .map((p) => (
            <button
              className="project-card panel"
              key={p.name}
              onClick={() => go("Project Details")}
            >
              <div className="project-top">
                <span className="project-icon" style={{ background: p.color }}>
                  <Icon name="folder" />
                </span>
                <Icon name="more" />
              </div>
              <div>
                <Badge
                  tone={
                    p.status === "At risk"
                      ? "warning"
                      : p.status === "Planning"
                        ? "neutral"
                        : "success"
                  }
                >
                  {p.status}
                </Badge>
                <h3>{p.name}</h3>
                <p>{p.desc}</p>
              </div>
              <div className="project-progress">
                <div>
                  <span>Progress</span>
                  <strong>{p.progress}%</strong>
                </div>
                <Progress value={p.progress} color={p.color} />
              </div>
              <div className="project-meta">
                <span>
                  <Icon name="check" size={15} />
                  {p.tasks} tasks
                </span>
                <span>
                  <Icon name="bot" size={15} />
                  {p.agents} agents
                </span>
                <span>Updated 2h ago</span>
              </div>
            </button>
          ))}
      </div>
      {modal && (
        <div className="modal-backdrop" onMouseDown={() => setModal(false)}>
          <div className="modal" onMouseDown={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <div>
                <h2>Create a project</h2>
                <p>Describe the outcome. Rouvr will create the plan.</p>
              </div>
              <button onClick={() => setModal(false)}>
                <Icon name="x" />
              </button>
            </div>
            <label>
              Project name
              <input placeholder="e.g. Supplier analytics portal" autoFocus />
            </label>
            <label>
              What do you want to build?
              <textarea placeholder="Describe your goals, users, constraints and desired outcome…" />
            </label>
            <div className="form-row">
              <label>
                Template
                <select>
                  <option>Start from scratch</option>
                  <option>Web application</option>
                  <option>Data pipeline</option>
                </select>
              </label>
              <label>
                Autonomy
                <select>
                  <option>Supervised</option>
                  <option>Full autonomy</option>
                </select>
              </label>
            </div>
            <div className="modal-actions">
              <Button onClick={() => setModal(false)}>Cancel</Button>
              <Button
                variant="primary"
                icon="spark"
                onClick={() => {
                  setModal(false)
                  go("AI Chat")
                }}
              >
                Plan with Rouvr
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}

function ProjectDetails({ go }: { go: (p: Page) => void }) {
  return (
    <>
      <PageHead
        eyebrow="PROJECT / ERP CORE PLATFORM"
        title="ERP Core Platform"
        description="Modernize finance, inventory and procurement operations."
        actions={
          <>
            <Button icon="chat" onClick={() => go("AI Chat")}>
              Open in chat
            </Button>
            <Button
              variant="primary"
              icon="play"
              onClick={() => go("Live Execution")}
            >
              Run next tasks
            </Button>
          </>
        }
      />
      <div className="project-summary panel">
        <div>
          <span>Overall progress</span>
          <strong>74%</strong>
          <Progress value={74} />
        </div>
        <div>
          <span>Tasks</span>
          <strong>
            94 <small>/ 128</small>
          </strong>
        </div>
        <div>
          <span>Current milestone</span>
          <strong>Payment operations</strong>
        </div>
        <div>
          <span>Target delivery</span>
          <strong>June 14, 2025</strong>
        </div>
        <div className="stacked-avatars">
          {agents.slice(0, 4).map((a) => (
            <Avatar key={a.name} name={a.name} color={a.color} />
          ))}
        </div>
      </div>
      <div className="tabs">
        <button className="active">Overview</button>
        <button>
          Tasks <b>128</b>
        </button>
        <button>Milestones</button>
        <button>Dependencies</button>
        <button>Files</button>
      </div>
      <div className="detail-grid">
        <Panel title="Project goal">
          <p className="large-copy">
            Deliver a production-ready ERP foundation that unifies invoicing,
            vendor operations, inventory, and reporting while preserving all
            historical financial records.
          </p>
          <div className="goal-tags">
            <Badge tone="cyan">SOC 2 compliant</Badge>
            <Badge tone="cyan">99.9% uptime</Badge>
            <Badge tone="cyan">Zero data loss</Badge>
          </div>
        </Panel>
        <Panel title="Health">
          <div className="health-score">
            <div>
              <strong>86</strong>
              <span>/100</span>
            </div>
            <Badge tone="success">Healthy</Badge>
          </div>
          {[
            ["Scope", "Stable", "success"],
            ["Velocity", "+12%", "success"],
            ["Risk", "2 open", "warning"],
            ["Quality", "96.8%", "success"],
          ].map((x) => (
            <div className="health-row" key={x[0]}>
              <span>{x[0]}</span>
              <Badge tone={x[2]}>{x[1]}</Badge>
            </div>
          ))}
        </Panel>
        <Panel title="Milestones" className="wide">
          <div className="milestones">
            {[
              ["Foundation & architecture", 100, "Apr 18", "done"],
              ["Core finance operations", 100, "May 09", "done"],
              ["Payment operations", 67, "May 31", "active"],
              ["Inventory & procurement", 21, "Jun 10", ""],
              ["Production hardening", 0, "Jun 14", ""],
            ].map((m, i) => (
              <div className={`milestone ${m[3]}`} key={m[0]}>
                <div className="milestone-marker">
                  {m[3] === "done" ? "✓" : i + 1}
                </div>
                <div className="milestone-body">
                  <div>
                    <strong>{m[0]}</strong>
                    <span>{m[2]}</span>
                  </div>
                  <Progress value={Number(m[1])} />
                  <small>{m[1]}% complete</small>
                </div>
              </div>
            ))}
          </div>
        </Panel>
        <Panel title="Dependencies">
          <div className="dependency">
            <span className="dep-icon">
              <Icon name="git" />
            </span>
            <div>
              <strong>Payment reconciliation</strong>
              <p>Waiting on currency rate service</p>
            </div>
            <Badge tone="warning">Blocked</Badge>
          </div>
          <div className="dependency">
            <span className="dep-icon">
              <Icon name="git" />
            </span>
            <div>
              <strong>Invoice PDF generation</strong>
              <p>Requires tax calculation module</p>
            </div>
            <Badge tone="cyan">Linked</Badge>
          </div>
        </Panel>
      </div>
    </>
  )
}

function Tasks({ go, approved }: { go: (p: Page) => void approved: boolean }) {
  const [status, setStatus] = useState("All")
  const syncedTasks = tasks.map((task) =>
    task.id === "ERP-128" && approved
      ? { ...task, status: "Done", progress: 100 }
      : task,
  )
  const shown =
    status === "All"
      ? syncedTasks
      : syncedTasks.filter((t) => t.status === status)
  return (
    <>
      <PageHead
        title="Tasks"
        description="128 tasks across 6 active projects."
        actions={
          <Button variant="primary" icon="plus">
            New task
          </Button>
        }
      />
      <div className="toolbar">
        <div className="search-field">
          <Icon name="search" />
          <input placeholder="Search tasks…" />
        </div>
        <div className="segmented">
          {["All", "Todo", "In progress", "Verification", "Done"].map((x) => (
            <button
              className={status === x ? "active" : ""}
              onClick={() => setStatus(x)}
              key={x}
            >
              {x}
            </button>
          ))}
        </div>
        <Button icon="filter">Filter</Button>
      </div>
      <Panel className="task-panel">
        <TaskTable rows={shown} onSelect={() => go("Task Details")} />
      </Panel>
    </>
  )
}

function TaskDetails({
  go,
  runtime,
  onApprove,
  onToggleRun,
}: {
  go: (p: Page) => void
  runtime: WorkspaceRuntime
  onApprove: () => void
  onToggleRun: () => void
}) {
  const [tab, setTab] = useState("Overview")
  return (
    <>
      <PageHead
        eyebrow="ERP CORE / ERP-128"
        title="Implement multi-currency invoice totals"
        description="Ensure invoice totals and line items are converted using the effective exchange rate at issue time."
        actions={
          <>
            <Button icon="more">Actions</Button>
            <Button variant="primary" icon="check" onClick={onApprove}>
              {runtime.approved ? "Approved" : "Approve task"}
            </Button>
          </>
        }
      />
      <div className="task-detail-meta panel">
        <Badge tone={runtime.approved ? "success" : "cyan"}>
          {runtime.approved ? "Approved" : "In progress"}
        </Badge>
        <span className="priority urgent">Urgent</span>
        <span>
          <Avatar name="Nova" color="#A78BFA" size={25} /> Nova
        </span>
        <span>
          <Icon name="clock" size={15} /> Updated 3 min ago
        </span>
        <div className="task-completion">
          <Progress value={runtime.approved ? 100 : 68} />
          <strong>{runtime.approved ? 100 : 68}%</strong>
        </div>
      </div>
      <div className="tabs">
        {["Overview", "Execution logs", "Evidence", "Activity"].map((x) => (
          <button
            className={tab === x ? "active" : ""}
            onClick={() => setTab(x)}
            key={x}
          >
            {x}
            {x === "Evidence" && <b>6</b>}
          </button>
        ))}
      </div>
      <div className="detail-grid task-detail">
        <div className="content-stack">
          {tab === "Overview" ? (
            <>
              <Panel title="Requirements">
                <ul className="check-list">
                  <li>
                    <span>✓</span>Support USD, EUR, GBP, CAD, JPY and AUD.
                  </li>
                  <li>
                    <span>✓</span>Persist the exchange rate used at invoice
                    issue time.
                  </li>
                  <li>
                    <span>✓</span>Round line items using each currency’s
                    precision.
                  </li>
                  <li>
                    <span>·</span>Show converted base total on the invoice
                    detail view.
                  </li>
                  <li>
                    <span>·</span>Add regression tests for rounding edge cases.
                  </li>
                </ul>
              </Panel>
              <Panel title="Implementation notes">
                <div className="code-block">
                  <div>
                    <span>invoice-service.ts</span>
                    <Badge tone="success">+84 −12</Badge>
                  </div>
                  <code>
                    const convertedTotal = money.convert({"\n"} invoice.total,
                    exchangeRate, targetCurrency{"\n"})
                  </code>
                </div>
              </Panel>
            </>
          ) : tab === "Execution logs" ? (
            <LogList />
          ) : (
            <Panel title={tab}>
              <div className="empty-state">
                <Icon
                  name={tab === "Evidence" ? "shield" : "clock"}
                  size={28}
                />
                <strong>
                  {tab === "Evidence"
                    ? "6 verification artifacts attached"
                    : "Activity is up to date"}
                </strong>
                <p>
                  Open the verification workspace to inspect detailed results.
                </p>
                <Button onClick={() => go("Verification")}>
                  Open verification
                </Button>
              </div>
            </Panel>
          )}
        </div>
        <div className="side-stack">
          <Panel title="Assigned agent">
            <div className="agent-profile">
              <Avatar name="Nova" color="#A78BFA" size={44} />
              <div>
                <strong>Nova</strong>
                <span>Frontend engineer</span>
              </div>
              <Badge tone="success">Active</Badge>
            </div>
            <div className="agent-metrics">
              <span>
                Session<strong>24m 18s</strong>
              </span>
              <span>
                Tokens<strong>42.8k</strong>
              </span>
              <span>
                Cost<strong>$1.84</strong>
              </span>
            </div>
            <Button
              icon={runtime.paused ? "play" : "pause"}
              onClick={onToggleRun}
            >
              {runtime.paused ? "Resume execution" : "Pause execution"}
            </Button>
          </Panel>
          <Panel title="Dependencies">
            <div className="mini-task">
              <span>✓</span>
              <div>
                <small>ERP-119</small>
                <strong>Add exchange rate provider</strong>
              </div>
            </div>
            <div className="mini-task">
              <span className="running" />
              <div>
                <small>ERP-122</small>
                <strong>Update money value type</strong>
              </div>
            </div>
          </Panel>
          <Panel title="Verification">
            <div className="verify-score">
              <strong>18 / 21</strong>
              <span>checks passing</span>
              <Progress value={86} color="#38D9A9" />
            </div>
          </Panel>
        </div>
      </div>
    </>
  )
}

function LogList() {
  return (
    <Panel
      title="Live execution log"
      action={<Badge tone="success">Streaming</Badge>}
    >
      <div className="logs">
        {[
          ["10:42:18", "Nova", "Reading invoice domain models…"],
          [
            "10:42:24",
            "Nova",
            "Updated MoneyValue type with currency precision",
          ],
          ["10:43:01", "Forge", "Running typecheck and unit test suite"],
          ["10:43:18", "System", "✓ 42 unit tests passed in 8.2s"],
          ["10:44:03", "Nova", "Implementing converted total component"],
          ["10:44:21", "System", "Building production bundle…"],
        ].map((l, i) => (
          <div key={i}>
            <time>{l[0]}</time>
            <Badge tone={l[1] === "System" ? "neutral" : "cyan"}>{l[1]}</Badge>
            <span>{l[2]}</span>
          </div>
        ))}
      </div>
    </Panel>
  )
}

function Agents({ paused }: { paused: boolean }) {
  return (
    <>
      <PageHead
        title="AI Agents"
        description="Your specialized autonomous engineering team."
        actions={
          <Button variant="primary" icon="plus">
            Configure agent
          </Button>
        }
      />
      <div className="agent-hero panel">
        <div>
          <div className="live-orb">
            <Icon name="bot" size={30} />
          </div>
          <div>
            <span>Team status</span>
            <strong>
              {paused ? "Execution is paused" : "4 agents are working"}
            </strong>
            <p>
              {paused
                ? "Agent context is retained and ready to resume"
                : "18 tasks completed today · 96.8% verification rate"}
            </p>
          </div>
        </div>
        <div className="agent-stats">
          <span>
            <strong>6</strong> Total
          </span>
          <span>
            <strong>4</strong> Active
          </span>
          <span>
            <strong>72%</strong> Capacity
          </span>
        </div>
      </div>
      <div className="agent-grid">
        {agents.map((a) => (
          <div className="agent-card panel" key={a.name}>
            <div className="agent-card-top">
              <Avatar name={a.name} color={a.color} size={48} />
              <Badge
                tone={
                  a.status === "Idle"
                    ? "neutral"
                    : paused
                      ? "warning"
                      : a.status === "Verifying"
                        ? "purple"
                        : "success"
                }
              >
                <i />
                {paused && a.status !== "Idle" ? "Paused" : a.status}
              </Badge>
            </div>
            <h3>{a.name}</h3>
            <p>{a.role}</p>
            <div className="skill-row">
              {(a.name === "Atlas"
                ? ["Planning", "Delegation", "Risk"]
                : a.name === "Nova"
                  ? ["React", "TypeScript", "UI"]
                  : a.name === "Forge"
                    ? ["APIs", "Node.js", "SQL"]
                    : ["Testing", "Review", "Security"]
              ).map((x) => (
                <Badge key={x}>{x}</Badge>
              ))}
            </div>
            <div className="current-task">
              <span>Current task</span>
              <strong>{a.task}</strong>
            </div>
            <div className="agent-footer">
              <span>
                Today <strong>{a.status === "Idle" ? 0 : 6} tasks</strong>
              </span>
              <button>
                <Icon name="more" />
              </button>
            </div>
          </div>
        ))}
      </div>
    </>
  )
}

function LiveExecution({
  runtime,
  onToggleRun,
}: {
  runtime: WorkspaceRuntime
  onToggleRun: () => void
}) {
  const paused = runtime.paused
  return (
    <>
      <PageHead
        eyebrow="ERP CORE / RUN #0842"
        title="Payment reconciliation workflow"
        description="Started 24 minutes ago by Alex Morgan"
        actions={
          <>
            <Button icon={paused ? "play" : "pause"} onClick={onToggleRun}>
              {paused ? "Resume" : "Pause all"}
            </Button>
            <Button variant="danger" icon="x">
              Stop run
            </Button>
          </>
        }
      />
      <div className="execution-banner panel">
        <div className={`execution-ring ${paused ? "paused" : ""}`}>
          <Icon name={paused ? "pause" : "pulse"} size={26} />
        </div>
        <div>
          <span>
            {paused ? "Execution paused" : "Autonomous execution in progress"}
          </span>
          <strong>{runtime.completedTasks} of 8 tasks complete</strong>
          <p>
            {paused
              ? "Agents will retain their current context."
              : "Estimated completion in 18 minutes"}
          </p>
        </div>
        <div className="execution-progress">
          <div>
            <span>{Math.round((runtime.completedTasks / 8) * 100)}%</span>
            <strong>05:42</strong>
          </div>
          <Progress value={(runtime.completedTasks / 8) * 100} />
        </div>
      </div>
      <div className="workflow panel">
        {[
          "Plan",
          "Schema",
          "Matching engine",
          "Approval UI",
          "Integration",
          "Verify",
          "Deliver",
        ].map((x, i) => (
          <div
            className={`workflow-node ${
              i < 4 ? "done" : i === 4 ? "active" : ""
            }`}
            key={x}
          >
            <span>{i < 4 ? "✓" : i + 1}</span>
            <strong>{x}</strong>
            <small>{i < 4 ? "Complete" : i === 4 ? "Running" : "Queued"}</small>
            {i < 6 && <i />}
          </div>
        ))}
      </div>
      <div className="execution-grid">
        <Panel title="Agent activity">
          <div className="active-agents">
            {agents.slice(0, 4).map((a, i) => (
              <div className="active-agent" key={a.name}>
                <Avatar name={a.name} color={a.color} size={38} />
                <div>
                  <div>
                    <strong>{a.name}</strong>
                    <Badge tone={i === 3 ? "purple" : "success"}>
                      {paused ? "Paused" : i === 3 ? "Waiting" : "Working"}
                    </Badge>
                  </div>
                  <p>{a.task}</p>
                  <Progress value={[78, 64, 42, 0][i]} color={a.color} />
                </div>
                <span>{[78, 64, 42, 0][i]}%</span>
              </div>
            ))}
          </div>
        </Panel>
        <LogList />
      </div>
    </>
  )
}

function Verification({
  approved,
  onApprove,
}: {
  approved: boolean
  onApprove: () => void
}) {
  const [filter, setFilter] = useState("Needs review")
  const reviews = [
    ["ERP-127", "Vendor onboarding workflow", "21 / 21", "Ready", "Sentry"],
    approved
      ? [
          "ERP-128",
          "Multi-currency invoice totals",
          "21 / 21",
          "Approved",
          "Nova",
        ]
      : [
          "ERP-128",
          "Multi-currency invoice totals",
          "18 / 21",
          "Needs review",
          "Nova",
        ],
    [
      "ERP-121",
      "Payment webhook retry policy",
      "14 / 16",
      "Repairing",
      "Forge",
    ],
    ["ERP-118", "Tax region configuration", "24 / 24", "Approved", "Sentry"],
  ]
  return (
    <>
      <PageHead
        title="Verification"
        description="Review evidence, tests and AI-produced changes before delivery."
        actions={<Button icon="shield">Verification policy</Button>}
      />
      <div className="verification-stats">
        <Stat label="Pass rate" value="96.8%" delta="+1.2%" icon="shield" />
        <Stat
          label="Awaiting approval"
          value="7"
          delta="3 high priority"
          icon="clock"
        />
        <Stat
          label="Auto-repaired"
          value="24"
          delta="This month"
          icon="spark"
        />
      </div>
      <div className="tabs">
        {["Needs review", "In repair", "Approved", "All"].map((x) => (
          <button
            className={filter === x ? "active" : ""}
            onClick={() => setFilter(x)}
            key={x}
          >
            {x}
          </button>
        ))}
      </div>
      <div className="review-grid">
        {reviews.map((r, i) => (
          <Panel key={r[0]} className="review-card">
            <div className="review-top">
              <div>
                <small>{r[0]}</small>
                <h3>{r[1]}</h3>
              </div>
              <Badge
                tone={
                  r[3] === "Approved" || r[3] === "Ready"
                    ? "success"
                    : r[3] === "Repairing"
                      ? "purple"
                      : "warning"
                }
              >
                {r[3]}
              </Badge>
            </div>
            <div className="review-score">
              <div
                className="score-ring"
                style={
                  {
                    "--score": `${(parseInt(r[2]) / parseInt(r[2].split("/")[1])) * 100}%`,
                  } as React.CSSProperties
                }
              >
                <span>
                  {Math.round(
                    (parseInt(r[2]) / parseInt(r[2].split("/")[1])) * 100,
                  )}
                </span>
              </div>
              <div>
                <strong>{r[2]} checks passed</strong>
                <span>
                  {i === 1
                    ? "2 test failures · 1 warning"
                    : "No critical issues found"}
                </span>
              </div>
            </div>
            <div className="evidence-row">
              <span>
                <Icon name="check" /> Unit tests
              </span>
              <Badge tone={i === 1 ? "warning" : "success"}>
                {i === 1 ? "42 / 44" : "Passed"}
              </Badge>
            </div>
            <div className="evidence-row">
              <span>
                <Icon name="shield" /> Security scan
              </span>
              <Badge tone="success">Passed</Badge>
            </div>
            <div className="evidence-row">
              <span>
                <Icon name="code" /> Code review
              </span>
              <Badge tone={i === 2 ? "purple" : "success"}>
                {i === 2 ? "Repairing" : "Passed"}
              </Badge>
            </div>
            <div className="review-actions">
              <Button>View evidence</Button>
              <Button
                variant="primary"
                icon="check"
                onClick={r[0] === "ERP-128" ? onApprove : undefined}
              >
                {r[0] === "ERP-128" && approved ? "Approved" : "Approve"}
              </Button>
            </div>
          </Panel>
        ))}
      </div>
    </>
  )
}

function Knowledge() {
  const docs = [
    ["Payment domain architecture", "Architecture", "Updated 2h ago", "Forge"],
    ["ERP data migration decisions", "Decision", "Updated yesterday", "Atlas"],
    ["Multi-currency handling", "Technical spec", "Updated 3d ago", "Nova"],
    ["Security and permissions model", "Policy", "Updated 5d ago", "Sentry"],
    ["Vendor onboarding research", "Research", "Updated May 18", "Scribe"],
    ["API conventions", "Engineering guide", "Updated May 12", "Forge"],
  ]
  return (
    <>
      <PageHead
        title="Knowledge"
        description="Shared context, decisions and long-term memory for your team."
        actions={
          <Button variant="primary" icon="plus">
            Add document
          </Button>
        }
      />
      <div className="knowledge-search panel">
        <Icon name="search" size={22} />
        <input placeholder="Ask your workspace knowledge…" />
        <kbd>⌘ K</kbd>
      </div>
      <div className="knowledge-grid">
        <aside className="knowledge-folders panel">
          <h3>Library</h3>
          {[
            "All documents",
            "Architecture",
            "Decisions",
            "Technical specs",
            "Research",
            "Meeting notes",
          ].map((x, i) => (
            <button className={i === 0 ? "active" : ""} key={x}>
              <Icon name={i === 0 ? "book" : "folder"} />
              <span>{x}</span>
              <b>{[24, 6, 8, 4, 3, 3][i]}</b>
            </button>
          ))}
          <h3>Memory</h3>
          <button>
            <Icon name="spark" />
            <span>Agent memory</span>
            <b>142</b>
          </button>
        </aside>
        <div className="documents">
          <div className="section-title">
            <h2>Recently updated</h2>
            <div className="segmented">
              <button className="active">Grid</button>
              <button>List</button>
            </div>
          </div>
          <div className="doc-grid">
            {docs.map((d) => (
              <button className="doc-card panel" key={d[0]}>
                <span className="doc-icon">
                  <Icon name={d[1] === "Decision" ? "spark" : "book"} />
                </span>
                <Badge>{d[1]}</Badge>
                <h3>{d[0]}</h3>
                <p>
                  Context and implementation guidance shared across the
                  autonomous team.
                </p>
                <div>
                  <Avatar name={d[3]} size={24} />
                  <span>{d[2]}</span>
                  <Icon name="more" />
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>
    </>
  )
}

function Activity({ workspaceEvents }: { workspaceEvents: WorkspaceEvent[] }) {
  const events = [
    ...workspaceEvents.map((event) => [
      event.time,
      event.actor,
      event.action,
      event.target,
      event.icon,
    ]),
    [
      "10:44 AM",
      "Nova",
      "Updated 3 files",
      "Multi-currency invoice totals",
      "code",
    ],
    [
      "10:43 AM",
      "Sentry",
      "Completed verification",
      "Vendor onboarding workflow",
      "shield",
    ],
    [
      "10:41 AM",
      "Forge",
      "Created pull request #482",
      "Payment reconciliation service",
      "git",
    ],
    [
      "10:32 AM",
      "Atlas",
      "Replanned 3 tasks",
      "Payment operations milestone",
      "spark",
    ],
    [
      "10:18 AM",
      "Alex Morgan",
      "Approved deployment",
      "Customer identity release",
      "check",
    ],
    [
      "9:54 AM",
      "Query",
      "Reported a blocker",
      "Production replica access",
      "x",
    ],
  ]
  return (
    <>
      <PageHead
        title="Activity"
        description="A complete audit trail of human and AI actions."
      />
      <div className="toolbar">
        <div className="search-field">
          <Icon name="search" />
          <input placeholder="Search activity…" />
        </div>
        <Button icon="filter">All events</Button>
        <Button icon="bot">All agents</Button>
      </div>
      <Panel>
        <div className="timeline">
          {events.map((e, i) => (
            <div className="timeline-event" key={i}>
              <time>{e[0]}</time>
              <span className={`timeline-icon ${i === 5 ? "danger" : ""}`}>
                <Icon name={e[4] as IconName} />
              </span>
              <div>
                <p>
                  <strong>{e[1]}</strong> {e[2]}
                </p>
                <span>{e[3]}</span>
              </div>
              <button>
                <Icon name="chevron" />
              </button>
            </div>
          ))}
        </div>
      </Panel>
    </>
  )
}

function Integrations() {
  const items = [
    ["GitHub", "Repositories, pull requests and checks", "git", "Connected"],
    ["Linear", "Import projects, issues and cycles", "check", "Connected"],
    ["Slack", "Notifications and approval requests", "chat", "Connected"],
    ["OpenAI", "GPT-4.1 and o3 reasoning models", "spark", "Connected"],
    ["Anthropic", "Claude 4 Sonnet and Opus models", "bot", "Connected"],
    ["Sentry", "Errors, traces and performance context", "pulse", "Available"],
  ]
  return (
    <>
      <PageHead
        title="Integrations"
        description="Connect Rouvr to your engineering stack."
        actions={<Button icon="plus">Request integration</Button>}
      />
      <div className="toolbar">
        <div className="search-field">
          <Icon name="search" />
          <input placeholder="Search integrations…" />
        </div>
        <div className="segmented">
          <button className="active">All</button>
          <button>Developer tools</button>
          <button>AI providers</button>
        </div>
      </div>
      <div className="integration-grid">
        {items.map((x, i) => (
          <Panel className="integration-card" key={x[0]}>
            <div className={`integration-logo logo-${i}`}>
              <Icon name={x[2] as IconName} size={25} />
            </div>
            <div>
              <h3>{x[0]}</h3>
              <p>{x[1]}</p>
            </div>
            <div className="integration-foot">
              {x[3] === "Connected" ? (
                <Badge tone="success">
                  <i /> Connected
                </Badge>
              ) : (
                <Badge>Available</Badge>
              )}
              <Button>{x[3] === "Connected" ? "Manage" : "Connect"}</Button>
            </div>
          </Panel>
        ))}
      </div>
    </>
  )
}

function Settings() {
  const [section, setSection] = useState("Profile")
  return (
    <>
      <PageHead
        title="Settings"
        description="Manage your account and workspace preferences."
      />
      <div className="settings-layout">
        <aside className="settings-nav panel">
          {[
            ["Personal", ["Profile", "Notifications", "Appearance"]],
            ["Workspace", ["General", "Team", "Permissions"]],
            ["Engineering", ["Autonomy", "Models", "Verification"]],
            ["Billing", ["Plan & usage"]],
          ].map(([head, its]) => (
            <div key={head as string}>
              <span>{head}</span>
              {(its as string[]).map((x) => (
                <button
                  className={section === x ? "active" : ""}
                  onClick={() => setSection(x)}
                  key={x}
                >
                  {x}
                </button>
              ))}
            </div>
          ))}
        </aside>
        <div className="settings-content">
          {section === "Profile" ? (
            <>
              <Panel title="Profile">
                <div className="profile-edit">
                  <Avatar name="Alex" size={64} />
                  <div>
                    <Button>Change photo</Button>
                    <p>JPG, PNG or GIF. 2MB maximum.</p>
                  </div>
                </div>
                <div className="form-row">
                  <label>
                    First name
                    <input defaultValue="Alex" />
                  </label>
                  <label>
                    Last name
                    <input defaultValue="Morgan" />
                  </label>
                </div>
                <label>
                  Email address
                  <input defaultValue="alex@northstar.dev" />
                </label>
                <label>
                  Role
                  <input defaultValue="VP of Engineering" />
                </label>
                <div className="save-row">
                  <Button variant="primary">Save changes</Button>
                </div>
              </Panel>
              <Panel title="Connected accounts">
                <div className="connected-account">
                  <span className="integration-logo">
                    <Icon name="git" />
                  </span>
                  <div>
                    <strong>GitHub</strong>
                    <p>Connected as alexmorgan</p>
                  </div>
                  <Badge tone="success">Connected</Badge>
                </div>
              </Panel>
            </>
          ) : (
            <Panel title={section}>
              <p className="large-copy">
                Configure {section.toLowerCase()} preferences for Northstar
                Labs. Changes apply across your workspace and autonomous team.
              </p>
              {[
                "Enable workspace defaults",
                "Require approval for critical actions",
                "Send weekly activity summary",
              ].map((x, i) => (
                <div className="setting-row" key={x}>
                  <div>
                    <strong>{x}</strong>
                    <p>Keep your workspace secure and predictable.</p>
                  </div>
                  <button className={`toggle ${i < 2 ? "on" : ""}`}>
                    <span />
                  </button>
                </div>
              ))}
              <div className="save-row">
                <Button variant="primary">Save preferences</Button>
              </div>
            </Panel>
          )}
        </div>
      </div>
    </>
  )
}

function Auth({ onEnter }: { onEnter: () => void }) {
  const [signup, setSignup] = useState(false)
  return (
    <div className="auth-page">
      <div className="auth-brand">
        <div className="brand-lockup">
          <RouvrMark size={40} />
          <strong>rouvr</strong>
        </div>
        <div className="auth-copy">
          <Badge tone="cyan">
            <Icon name="spark" size={13} /> AUTONOMOUS ENGINEERING
          </Badge>
          <h1>
            From conversation
            <br />
            to <em>production.</em>
          </h1>
          <p>
            Plan, build, verify and deliver software with an autonomous AI
            engineering team that understands your entire workspace.
          </p>
          <div className="flow-line">
            {["Chat", "Plan", "Execute", "Verify", "Deliver"].map((x, i) => (
              <div key={x}>
                <span>{i + 1}</span>
                <strong>{x}</strong>
              </div>
            ))}
          </div>
        </div>
        <div className="auth-dog">
          <RouvrMark size={88} />
          <span>Ready when you are.</span>
        </div>
      </div>
      <div className="auth-form-wrap">
        <div className="auth-form">
          <div className="mobile-brand">
            <RouvrMark />
            <strong>rouvr</strong>
          </div>
          <h2>{signup ? "Create your workspace" : "Welcome back"}</h2>
          <p>
            {signup
              ? "Start building with your AI engineering team."
              : "Sign in to continue to Northstar Labs."}
          </p>
          <button className="oauth">
            <Icon name="git" /> Continue with GitHub
          </button>
          <div className="divider">
            <span>or continue with email</span>
          </div>
          {signup && (
            <label>
              Full name
              <input placeholder="Alex Morgan" />
            </label>
          )}
          <label>
            Work email
            <input type="email" placeholder="you@company.com" />
          </label>
          <label>
            Password
            <div className="password">
              <input type="password" placeholder="••••••••••" />
              <button>Show</button>
            </div>
          </label>
          {!signup && (
            <div className="forgot">
              <label>
                <input type="checkbox" /> Remember me
              </label>
              <button>Forgot password?</button>
            </div>
          )}
          <Button variant="primary" onClick={onEnter}>
            {signup ? "Create workspace" : "Sign in to Rouvr"}
            <Icon name="arrow" />
          </Button>
          <p className="switch-auth">
            {signup ? "Already have an account?" : "New to Rouvr?"}{" "}
            <button onClick={() => setSignup(!signup)}>
              {signup ? "Sign in" : "Create an account"}
            </button>
          </p>
          <small>
            By continuing, you agree to Rouvr’s Terms and Privacy Policy.
          </small>
        </div>
      </div>
    </div>
  )
}

function AppShell() {
  const [page, setPage] = useState<Page>("Dashboard")
  const [collapsed, setCollapsed] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)
  const [profile, setProfile] = useState(false)
  const [notifications, setNotifications] = useState(false)
  const [search, setSearch] = useState(false)
  const [runtime, setRuntime] = useState<WorkspaceRuntime>({
    paused: false,
    running: true,
    approved: false,
    completedTasks: 5,
  })
  const [workspaceEvents, setWorkspaceEvents] = useState<WorkspaceEvent[]>([
    {
      time: "Just now",
      actor: "Sentry",
      action: "completed verification",
      target: "ERP-128 · 18 of 21 checks passed",
      icon: "shield",
    },
    {
      time: "3 min ago",
      actor: "Nova",
      action: "updated invoice totals",
      target: "ERP-128 · 3 files changed",
      icon: "code",
    },
    {
      time: "8 min ago",
      actor: "Atlas",
      action: "replanned the workflow",
      target: "Payment operations",
      icon: "spark",
    },
  ])
  const addWorkspaceEvent = (
    action: string,
    target: string,
    icon: IconName,
  ) => {
    setWorkspaceEvents((current) => [
      { time: "Just now", actor: "Alex", action, target, icon },
      ...current.map((event, index) =>
        index === 0 && event.time === "Just now"
          ? { ...event, time: "A moment ago" }
          : event,
      ),
    ])
  }
  const toggleRun = () => {
    setRuntime((current) => ({ ...current, paused: !current.paused }))
    addWorkspaceEvent(
      runtime.paused ? "resumed agent execution" : "paused agent execution",
      "Payment reconciliation workflow",
      runtime.paused ? "play" : "pause",
    )
  }
  const startRun = () => {
    if (runtime.running && !runtime.paused) return
    setRuntime((current) => ({
      ...current,
      running: true,
      paused: false,
    }))
    addWorkspaceEvent(
      "approved autonomous execution",
      "Payment reconciliation workflow",
      "play",
    )
  }
  const approveTask = () => {
    if (runtime.approved) return
    setRuntime((current) => ({
      ...current,
      approved: true,
      completedTasks: Math.max(current.completedTasks, 6),
    }))
    addWorkspaceEvent(
      "approved verification results",
      "ERP-128 · Multi-currency invoice totals",
      "check",
    )
  }
  const content = useMemo(() => {
    const props = { go: setPage }
    switch (page) {
      case "Dashboard":
        return <Dashboard {...props} />
      case "AI Chat":
        return (
          <AIChat
            {...props}
            runtime={runtime}
            events={workspaceEvents}
            onToggleRun={toggleRun}
            onStartRun={startRun}
            onApprove={approveTask}
          />
        )
      case "Projects":
        return <Projects {...props} />
      case "Project Details":
        return <ProjectDetails {...props} />
      case "Tasks":
        return <Tasks {...props} approved={runtime.approved} />
      case "Task Details":
        return (
          <TaskDetails
            {...props}
            runtime={runtime}
            onApprove={approveTask}
            onToggleRun={toggleRun}
          />
        )
      case "AI Agents":
        return <Agents paused={runtime.paused} />
      case "Live Execution":
        return <LiveExecution runtime={runtime} onToggleRun={toggleRun} />
      case "Verification":
        return (
          <Verification approved={runtime.approved} onApprove={approveTask} />
        )
      case "Knowledge":
        return <Knowledge />
      case "Activity":
        return <Activity workspaceEvents={workspaceEvents} />
      case "Integrations":
        return <Integrations />
      case "Settings":
        return <Settings />
      default:
        return <Dashboard {...props} />
    }
  }, [page, runtime, workspaceEvents])
  if (page === "Login") return <Auth onEnter={() => setPage("Dashboard")} />
  const navigate = (p: Page) => {
    setPage(p)
    setMobileOpen(false)
  }
  return (
    <div className={`app-shell ${collapsed ? "sidebar-collapsed" : ""}`}>
      {mobileOpen && (
        <div className="mobile-scrim" onClick={() => setMobileOpen(false)} />
      )}
      <aside className={`sidebar ${mobileOpen ? "mobile-open" : ""}`}>
        <div className="brand">
          <RouvrMark />
          <strong>rouvr</strong>
          <button onClick={() => setCollapsed(!collapsed)}>
            <Icon name="chevron" />
          </button>
        </div>
        <button className="workspace-switcher">
          <span className="workspace-logo">N</span>
          <div>
            <small>WORKSPACE</small>
            <strong>Northstar Labs</strong>
          </div>
          <Icon name="chevron" size={14} />
        </button>
        <nav>
          {nav.map((group) => (
            <div className="nav-group" key={group.section}>
              <span>{group.section}</span>
              {group.items.map((item) => (
                <button
                  className={page === item.label ? "active" : ""}
                  onClick={() => navigate(item.label)}
                  key={item.label}
                >
                  <Icon name={item.icon} />
                  <b>{item.label}</b>
                  {item.label === "Tasks" && <em>7</em>}
                  {item.label === "Live Execution" && <i />}
                </button>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="usage">
            <div>
              <span>Monthly usage</span>
              <strong>68%</strong>
            </div>
            <Progress value={68} />
            <small>136k / 200k agent credits</small>
          </div>
          <button
            className="sidebar-profile"
            onClick={() => setProfile(!profile)}
          >
            <Avatar name="Alex" size={34} />
            <div>
              <strong>Alex Morgan</strong>
              <span>alex@northstar.dev</span>
            </div>
            <Icon name="more" />
          </button>
        </div>
      </aside>
      <div className="main-area">
        <header className="topbar">
          <button className="mobile-menu" onClick={() => setMobileOpen(true)}>
            <Icon name="menu" />
          </button>
          <button className="global-search" onClick={() => setSearch(true)}>
            <Icon name="search" />
            <span>Search projects, tasks, or ask Rouvr…</span>
            <kbd>⌘ K</kbd>
          </button>
          <div className="top-actions">
            <button
              className="icon-button"
              onClick={() => setNotifications(!notifications)}
            >
              <Icon name="bell" />
              <i />
            </button>
            <button className="help">?</button>
            <span className="top-divider" />
            <button
              className="top-profile"
              onClick={() => setProfile(!profile)}
            >
              <Avatar name="Alex" size={30} />
              <span>Alex Morgan</span>
              <Icon name="chevron" size={13} />
            </button>
          </div>
          {notifications && (
            <div className="popover notifications">
              <div>
                <strong>Notifications</strong>
                <button>Mark all read</button>
              </div>
              <div className="notice">
                <span className="notice-icon">
                  <Icon name="check" />
                </span>
                <div>
                  <strong>Task ready for review</strong>
                  <p>Vendor onboarding workflow passed 21 checks.</p>
                  <small>4 min ago</small>
                </div>
              </div>
              <div className="notice">
                <span className="notice-icon warning">
                  <Icon name="bell" />
                </span>
                <div>
                  <strong>Agent needs access</strong>
                  <p>Query requested production replica access.</p>
                  <small>12 min ago</small>
                </div>
              </div>
            </div>
          )}
          {profile && (
            <div className="popover profile-menu">
              <div>
                <Avatar name="Alex" />
                <div>
                  <strong>Alex Morgan</strong>
                  <span>alex@northstar.dev</span>
                </div>
              </div>
              <button
                onClick={() => {
                  setPage("Settings")
                  setProfile(false)
                }}
              >
                <Icon name="settings" />
                Account settings
              </button>
              <button>
                <Icon name="users" />
                Switch workspace
              </button>
              <hr />
              <button onClick={() => setPage("Login")}>
                <Icon name="lock" />
                Sign out
              </button>
            </div>
          )}
        </header>
        <main
          className={`page-content ${page === "AI Chat" ? "chat-page" : ""}`}
        >
          {content}
        </main>
      </div>
      {search && (
        <div
          className="modal-backdrop search-backdrop"
          onMouseDown={() => setSearch(false)}
        >
          <div
            className="command-menu"
            onMouseDown={(e) => e.stopPropagation()}
          >
            <div>
              <Icon name="search" />
              <input placeholder="Search or ask Rouvr…" autoFocus />
              <kbd>ESC</kbd>
            </div>
            <small>QUICK NAVIGATION</small>
            {nav
              .slice(0, 2)
              .flatMap((x) => x.items)
              .slice(0, 6)
              .map((x) => (
                <button
                  key={x.label}
                  onClick={() => {
                    navigate(x.label)
                    setSearch(false)
                  }}
                >
                  <span>
                    <Icon name={x.icon} />
                    {x.label}
                  </span>
                  <kbd>↵</kbd>
                </button>
              ))}
          </div>
        </div>
      )}
    </div>
  )
}

export default function App() {
  return <AppShell />
}
