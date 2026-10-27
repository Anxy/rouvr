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

export async function listIdempotencyKeys(
  args: Prisma.IdempotencyKeyFindManyArgs = {}
) {
  return getPrismaClient().idempotencyKey.findMany(args);
}

export async function getIdempotencyKey(
  where: Prisma.IdempotencyKeyWhereUniqueInput
) {
  return getPrismaClient().idempotencyKey.findUnique({ where });
}

export async function createIdempotencyKey(
  data: Prisma.IdempotencyKeyCreateInput
) {
  return getPrismaClient().idempotencyKey.create({ data });
}

export async function updateIdempotencyKey(
  where: Prisma.IdempotencyKeyWhereUniqueInput,
  data: Prisma.IdempotencyKeyUpdateInput
) {
  return getPrismaClient().idempotencyKey.update({ where, data });
}

export async function deleteIdempotencyKey(
  where: Prisma.IdempotencyKeyWhereUniqueInput
) {
  return getPrismaClient().idempotencyKey.delete({ where });
}

export async function upsertIdempotencyKey(
  where: Prisma.IdempotencyKeyWhereUniqueInput,
  create: Prisma.IdempotencyKeyCreateInput,
  update: Prisma.IdempotencyKeyUpdateInput
) {
  return getPrismaClient().idempotencyKey.upsert({ where, create, update });
}

export type { Prisma };
export type { PrismaConfig, PrismaLifecycleHook };
