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

export async function listOutboxes(
  args: Prisma.OutboxFindManyArgs = {}
) {
  return getPrismaClient().outbox.findMany(args);
}

export async function getOutbox(
  where: Prisma.OutboxWhereUniqueInput
) {
  return getPrismaClient().outbox.findUnique({ where });
}

export async function createOutbox(
  data: Prisma.OutboxCreateInput
) {
  return getPrismaClient().outbox.create({ data });
}

export async function updateOutbox(
  where: Prisma.OutboxWhereUniqueInput,
  data: Prisma.OutboxUpdateInput
) {
  return getPrismaClient().outbox.update({ where, data });
}

export async function deleteOutbox(
  where: Prisma.OutboxWhereUniqueInput
) {
  return getPrismaClient().outbox.delete({ where });
}

export async function upsertOutbox(
  where: Prisma.OutboxWhereUniqueInput,
  create: Prisma.OutboxCreateInput,
  update: Prisma.OutboxUpdateInput
) {
  return getPrismaClient().outbox.upsert({ where, create, update });
}

export type { Prisma };
export type { PrismaConfig, PrismaLifecycleHook };
