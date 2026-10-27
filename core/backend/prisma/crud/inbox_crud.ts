import { PrismaClient } from '@prisma/client';
import type { Prisma } from '@prisma/client';

type PrismaConfig = { datasources?: Prisma.Datasources; log?: Prisma.LogDefinition[] };
type PrismaLifecycleHook = () => Promise<void> | void;

let prisma: PrismaClient | null = null;
const shutdownHooks = new Set<PrismaLifecycleHook>();

export function getPrismaClient(
  config: PrismaConfig = {},
  lifecycle: { onShutdown?: PrismaLifecycleHook } = {}
) {
  if (!prisma) {
    prisma = new PrismaClient({
      datasources: config.datasources,
      log: config.log,
    });
  }
  if (lifecycle.onShutdown) {
    shutdownHooks.add(lifecycle.onShutdown);
  }
  return prisma;
}

export async function shutdownPrismaClient() {
  if (!prisma) {
    return;
  }
  const client = prisma;
  prisma = null;
  await client.$disconnect();
  for (const hook of shutdownHooks) {
    await hook();
  }
  shutdownHooks.clear();
}

export async function listInboxes(
  args: Prisma.InboxFindManyArgs = {}
) {
  return getPrismaClient().inbox.findMany(args);
}

export async function getInbox(
  where: Prisma.InboxWhereUniqueInput
) {
  return getPrismaClient().inbox.findUnique({ where });
}

export async function createInbox(
  data: Prisma.InboxCreateInput
) {
  return getPrismaClient().inbox.create({ data });
}

export async function updateInbox(
  where: Prisma.InboxWhereUniqueInput,
  data: Prisma.InboxUpdateInput
) {
  return getPrismaClient().inbox.update({ where, data });
}

export async function deleteInbox(
  where: Prisma.InboxWhereUniqueInput
) {
  return getPrismaClient().inbox.delete({ where });
}

export async function upsertInbox(
  where: Prisma.InboxWhereUniqueInput,
  create: Prisma.InboxCreateInput,
  update: Prisma.InboxUpdateInput
) {
  return getPrismaClient().inbox.upsert({ where, create, update });
}

export type { Prisma };
export type { PrismaConfig, PrismaLifecycleHook };
