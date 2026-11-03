import Link from "next/link";

export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6">
      <nav className="mb-8 flex items-center gap-2 border-b border-steel/40 pb-4">
        <span className="mr-4 font-mono text-xs uppercase tracking-widest text-muted">
          Rouvr workspace
        </span>
        <Link
          href="/workspace"
          className="rounded-full px-4 py-1.5 text-sm text-cloud transition-colors hover:bg-steel/30 hover:text-white"
        >
          Board
        </Link>
        <Link
          href="/workspace/chat"
          className="rounded-full px-4 py-1.5 text-sm text-cloud transition-colors hover:bg-steel/30 hover:text-white"
        >
          Chat
        </Link>
        <Link
          href="/workspace/login"
          className="ml-auto rounded-full px-4 py-1.5 text-sm text-muted transition-colors hover:bg-steel/30 hover:text-white"
        >
          Login
        </Link>
      </nav>
      {children}
    </div>
  );
}
