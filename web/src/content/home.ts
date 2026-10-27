import type { LucideIcon } from "lucide-react";
import {
  Box,
  Building2,
  Eye,
  FlaskConical,
  ListChecks,
  Network,
  Newspaper,
  Rocket,
  ScanSearch,
  ShieldCheck,
  Users,
  Workflow,
} from "lucide-react";
import type { MascotState } from "@/components/brand/Mascot";

export const hero = {
  title: "Rouvr.\nMake it happen.",
  strap: "Your pack, your ambition.",
  description:
    "Rouvr puts a pack of AI specialists to work on what you want to achieve. Think it through together, then let your pack research, create, build and deliver across your tools, with you in control.",
  paragraphs: [
    "A business to grow. A week to get on top of. An idea that deserves a proper go.",
    "Research the opportunity. Write the campaign. Build, test and launch the website. Get the report submitted and the follow-ups done. You set the direction. Your pack gets to work.",
  ],
  primaryCta: { label: "Tell us what you'd hand over first", href: "/contact" },
  secondaryCta: { label: "See how it works", href: "/#how-it-works" },
  objective:
    "Get today's report, check the figures, submit it, follow up on anything missing and let the team know.",
  doneLabel: "Result, exceptions and receipt",
  waitingLabel: "Waiting for the brief",
  steps: [
    {
      id: "01",
      title: "Think together",
      detail: "Explore the idea and work out a way forward.",
      mascot: "analyzing",
    },
    {
      id: "02",
      title: "Agree what happens",
      detail: "Set the outcome, the remit and what comes back to you.",
      mascot: "analyzing",
    },
    {
      id: "03",
      title: "Put your pack to work",
      detail: "Research, create, build, check and deliver.",
      mascot: "executing",
    },
  ] satisfies { id: string; title: string; detail: string; mascot: MascotState }[],
};

export const marqueeItems = [
  "Your pack, your ambition",
  "Think it through",
  "Agree the plan",
  "Get it moving",
  "You in control",
  "Research, create, build, deliver",
];

export const problem = {
  eyebrow: "Possibility",
  title: "What could you do with someone",
  highlight: "keeping things moving?",
  paragraphs: [
    "The idea is in one conversation. The plan is in a document. The latest update is somewhere in your inbox. And somehow, you're the one holding it all together.",
    "Briefing. Reminding. Checking. Starting over again.",
    "Imagine putting that energy into the work you actually want to do. Growing the business. Teaching the class. Starting the thing you keep putting off.",
  ],
  bullets: [
    "Growing the business.",
    "Teaching the class.",
    "Starting the thing you keep putting off.",
    "Go on. Plot bigger.",
  ],
};

export const pack = {
  title: "One ambition. A pack equipped to deliver.",
  head: "You + rouvr",
  headBody: "Think it through. Set the direction. Agree what gets done.",
  roles: [
    { title: "Research", body: "Investigate the question. Check sources. Produce findings." },
    { title: "Create", body: "Write the copy. Design the assets. Make the materials." },
    { title: "Build", body: "Write the code. Connect the systems. Implement the solution." },
    { title: "Verify", body: "Test the work. Review independently. Return defects for repair." },
    { title: "Deliver", body: "Deploy the release. Send the campaign. Submit the report." },
    { title: "Operate", body: "Monitor the service. Process recurring work. Respond to exceptions." },
  ],
  base: [
    {
      label: "Powered by Hyeve",
      body: "specialist agents, AI models, services, adapters and connectors.",
    },
    {
      label: "Connected by shared knowledge",
      body: "the brief, decisions, permissions and evidence stay with rouvr throughout.",
    },
  ],
  caption:
    "Specialists work in parallel where dependencies allow, with one supervising colleague bringing their work together.",
};

export const approach = {
  eyebrow: "A typical delivery journey",
  title: "From an agreed goal to work delivered and maintained.",
  caption: [
    "Your knowledge, decisions and permissions stay connected throughout.",
    "A typical delivery journey. Actions, checks, release approvals and ongoing responsibilities depend on the work you agree.",
  ],
  stages: [
    {
      title: "Agree the goal",
      icon: ScanSearch,
      body: "Outcome and authority.",
    },
    {
      title: "Do the work",
      icon: Box,
      body: "Research, write and build.",
    },
    {
      title: "Check and repair",
      icon: FlaskConical,
      body: "Test, review and fix.",
    },
    {
      title: "Deliver",
      icon: Rocket,
      body: "Deploy, send and submit.",
    },
    {
      title: "Keep it running",
      icon: Eye,
      body: "Monitor and follow up.",
    },
  ] satisfies { title: string; icon: LucideIcon; body: string }[],
};

export const capabilities = {
  eyebrow: "Capabilities",
  title: "The understanding to help. The tools to act.",
  description: "",
  items: [
    {
      title: "A colleague who understands your world",
      icon: Users,
      body: "Your role, ambitions, decisions and way of working stay in one shared understanding that you can inspect and revise. It grows through your conversations and feedback, and it stays with rouvr as individual agents and models change.",
      visual: "graph",
    },
    {
      title: "A pack of specialists, chosen for the job",
      icon: Network,
      body: "Researchers investigate. Writers create. Developers build. Reviewers test and challenge the result. Rouvr draws them from Hyeve, the ecosystem of agents, models, services and connectors beneath it, and picks the right intelligence for each task. Because rouvr is model agnostic, your pack improves as better capabilities arrive, while your knowledge and ongoing work stay in place.",
      visual: "agents",
    },
    {
      title: "Your tools, put to work",
      icon: Workflow,
      body: "Update the customer record. Send the approved campaign. Publish the lesson materials. Submit the report. Rouvr takes authorised action through your connected systems and checks that it went through.",
      visual: "approval",
    },
    {
      title: "Responsibilities that keep running",
      icon: ListChecks,
      body: "Monitor the launch. Process the next batch. Send the weekly update. Once you authorise recurring work, rouvr runs it without a fresh instruction each time, recovers interrupted work and brings you the exceptions that need a decision.",
      visual: "recovery",
    },
    {
      title: "A clear view of what needs you",
      icon: Eye,
      body: "See what needs your judgement, what's progressing, what's waiting and what's ready for review. Follow the detail when you want it, with one supervising colleague keeping the whole picture.",
      visual: "progress",
    },
  ] as const,
};

export const howItWorks = {
  eyebrow: "How it works",
  title: "Think it through. Agree the plan. Get it moving.",
  quote:
    "Get today's report, check the figures, submit it, follow up on anything missing and let the team know.",
  followUp:
    "Rouvr retrieves the report, checks the figures against the source data, submits it through the agreed system and notifies the team. Missing information triggers a follow-up; a failed submission triggers recovery or escalation. You get the result, the exceptions and the submission receipt.",
  steps: [
    {
      id: "01",
      title: "Think together",
      body: "Bring a clear objective, a messy instruction or the beginnings of an idea. Rouvr helps you explore it, ask better questions and work out a way forward.",
      mascot: "analyzing",
    },
    {
      id: "02",
      title: "Agree what happens",
      body: "Decide what you want to achieve, what rouvr can take on and which decisions should come back to you.",
      mascot: "analyzing",
    },
    {
      id: "03",
      title: "Put your pack to work",
      body: "Specialists research, write, design, code, test and act through your connected systems. Rouvr chooses the models and tools for the job, brings the work together and checks the result.",
      mascot: "executing",
    },
  ] satisfies { id: string; title: string; body: string; mascot: MascotState }[],
};

export const useCases = {
  eyebrow: "Your pack at work",
  title: "Your pack at work.",
  label:
    "Imagine rouvr at work. These illustrative stories show what you and your pack could make happen.",
  outcomeLabel: "Illustrative delivery",
  link: { label: "Explore the engineering behind rouvr", href: "/#evidence" },
  bridge: {
    title: "Same pack, different worlds.",
    body: "Software is where we started. The ambition goes further: work through the idea together, put the pack to work and see it delivered.",
  },
  items: [
    {
      tab: "A running service",
      kicker: "Your pack. Your kind of impossible.",
      title: "From a client brief to a running service.",
      quote: "I run an IT company. I want to deliver more than my team has time for.",
      icon: Building2,
      paragraphs: [
        "A client wants a new booking portal, and they want it this quarter. My team is already committed. I work through the brief with rouvr, agree the scope, and set who approves the reviews and the release.",
        "The pack gets to work. One agent builds the interface, another implements the backend and integrations, and a testing agent writes and runs the checks. An independent reviewer examines every change; defects go back for repair and are reviewed again. Rouvr selects the models, agents and tools for each part of the job, and keeps the evidence tied to the exact version under review.",
        "I try the working portal in staging and approve the release. Rouvr runs the CI/CD pipeline, deploys, and checks the live service. Monitoring continues under the agreed remit, with issues investigated, repaired or brought back to me.",
        "The client gets their portal on time. My team stays on the work only they can do. And the next brief doesn't have to be turned down.",
      ],
      outcomes: [
        { title: "A working portal", body: "Interface, backend and integrations implemented." },
        { title: "A checked release", body: "Tests run, defects repaired, every change independently reviewed." },
        { title: "A running service", body: "Approved build deployed through CI/CD, with live checks and monitoring." },
      ],
      result: "Built, tested, launched and being watched. On to the next opportunity.",
    },
    {
      tab: "More time to teach",
      kicker: "Big ideas welcome.",
      title: "I'm a teacher. I want more time for teaching.",
      icon: Users,
      paragraphs: [
        "Part of my class is stuck on fractions. I talk through a different approach with rouvr, and the pack creates a fresh explanation, visual examples, practice at three levels and a short assessment.",
        "Once I've reviewed them, rouvr shares the right activities with the right groups through our learning platform, checks the objective answers and sends the follow-up practice I approve.",
      ],
      result: "Materials made. Work delivered. Feedback on time. More time to teach.",
    },
    {
      tab: "The whole picture",
      kicker: "Whatever you're plotting.",
      title: "I run several ventures. I need the whole picture.",
      icon: Network,
      paragraphs: [
        "An admissions campaign, a property listing and a monthly operating report, all waiting for time I don't have. I agree priorities, budgets and approvals with rouvr once.",
        "The pack builds the campaign, prepares the listing and checks the figures. After my approvals, rouvr publishes, sends, follows up and submits. Each venture keeps its own information and permissions; I see across all three.",
      ],
      result: "Three ventures moving. One set of instructions.",
    },
    {
      tab: "Issue one",
      kicker: "A little more audacity.",
      title: "The newsletter I've been meaning to start.",
      icon: Newspaper,
      paragraphs: [
        "I've talked about a community newsletter for months. This time I agree with rouvr what the first issue and website should include.",
        "Specialists research local stories, check sources, write, design, build the site and test it. A few days later I review a finished issue and a working preview, edit, and approve. Rouvr publishes, sends it to subscribers and starts the next edition.",
      ],
      result: "\"One day\" is now issue one.",
    },
  ] satisfies {
    tab: string;
    kicker: string;
    title: string;
    quote?: string;
    icon: LucideIcon;
    paragraphs: string[];
    outcomes?: { title: string; body: string }[];
    result: string;
  }[],
};

export const audience = {
  eyebrow: "Continuity",
  title: "Pick up where you left off.",
  description:
    "Come back tomorrow and ask, \"Where are we?\" Rouvr shows you what it did, what passed its checks and what needs your judgement. Revise the plan and it works out what else needs to change. Move between projects with the right context, and each project's information kept within its own boundaries.",
  visualTitle: "What happened while I was away?",
  visualLabel: "Illustrative return view",
  caption:
    "The work produced, its actual state, the evidence and the next action. Open the detail when you need it.",
  note: "Less reconstructing. More getting on with it.",
  items: [
    {
      title: "Booking portal",
      detail: "Release deployed. Live health checks passed.",
      status: "Monitoring active",
      next: "Next: continue the agreed service checks.",
    },
    {
      title: "Admissions campaign",
      detail: "Landing page and campaign assets built and checked.",
      status: "Your decision",
      next: "Approve the campaign before it is sent.",
    },
    {
      title: "Operating report",
      detail: "Figures reconciled. Report submitted.",
      status: "Delivered",
      next: "Report and submission receipt available.",
    },
  ],
};

export const control = {
  eyebrow: "Trust and control",
  title: "More in motion. You in control.",
  description: "Give your pack room to act, with the checks and boundaries the job needs.",
  caption:
    "Every outcome comes with something to inspect: a deployment record, a submission receipt, a delivery confirmation or a test result.",
  recovery: [
    { title: "Take action", detail: "Within agreed authority" },
    { title: "Check the outcome", detail: "Did it actually happen?" },
    { title: "Record the result", detail: "With checkable evidence", note: "Verified" },
    { title: "Investigate", detail: "Establish the actual state", note: "Failed or uncertain" },
    { title: "Bring you a decision", detail: "Evidence + recommendation", note: "New authority needed" },
    { title: "Repair or recover", detail: "Within the existing remit", note: "Check again after repair" },
  ],
  recoveryNotes: [
    "Act within the remit. Check what happened.",
    "A verified result is recorded with evidence. A failed or uncertain result is investigated.",
    "Authorised repairs return for another check. A decision needing new authority comes back to you with the evidence and a recommendation.",
  ],
  items: [
    {
      title: "Agree the remit",
      icon: ShieldCheck,
      body: "Thinking aloud stays exploratory until you authorise the work. Set what rouvr can build, change, send or publish, where, and which approvals it needs. Revise, pause or withdraw it at any time.",
    },
    {
      title: "Check every result",
      icon: FlaskConical,
      body: "Code is tested and independently reviewed. Figures are checked against their sources, materials against the brief. Failed checks go back for repair. Approval is tied to the exact version reviewed, so a later change can't inherit it.",
    },
    {
      title: "Recover, don't repeat",
      icon: ListChecks,
      body: "When something fails, rouvr first establishes what actually happened. Valid work is kept, an action that already went through isn't repeated, and anything needing new authority comes back to you with the evidence and a proposed next step.",
    },
  ] satisfies { title: string; icon: LucideIcon; body: string }[],
};

export const evidence = {
  eyebrow: "Evidence",
  title: "Built on real delivery.",
  paragraphs: [
    "We started with software development, where work can be assigned, tested, independently reviewed and checked against an exact result. That foundation is already supporting real software delivery.",
    "Across more than 100 autonomous task sequences, the same foundation brings together persistent work records, durable orchestration, separate build and review roles, test gates and revision-bound approval.",
  ],
  tagline:
    "We're now carrying that discipline into every responsibility rouvr takes on: understand the intent, act within authority and establish what actually happened.",
  caseLabel: "Engineering delivery · client anonymised",
  caseTitle: "A borrower self-service feature for a lending platform",
  caseBody:
    "Rouvr's engineering foundation took a case-lifecycle feature through build, independent review and repair, including a configuration defect fixed without disturbing existing client settings, to a reviewed and merged change, with its review evidence bound to the precise revision approved.",
  stats: [
    { value: "3", label: "rounds of independent review" },
    { value: "1,490", label: "tests passed across operations and gateway suites" },
    { value: "Revision-bound", label: "approval and evidence tied to the reviewed code" },
  ],
};

export const finalCta = {
  title: "What would you do today with rouvr?",
  body: "The business you're building. The work that keeps spilling into your evening. That gloriously unreasonable idea.",
  detail:
    "Tell us what you want to make happen, and what keeps getting in the way. Let's explore where your pack could help.",
  primary: { label: "Get in touch", href: "/contact" },
  campaign: "Your pack. Go on, plot bigger.",
  tagline: "Rouvr. Make it happen.",
  strap: "Your pack, your ambition.",
};
