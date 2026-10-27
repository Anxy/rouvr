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

export async function listAixModelHealthStatuses(
  args: Prisma.AixModelHealthStatusFindManyArgs = {}
) {
  return getPrismaClient().aixModelHealthStatus.findMany(args);
}

export async function getAixModelHealthStatus(
  where: Prisma.AixModelHealthStatusWhereUniqueInput
) {
  return getPrismaClient().aixModelHealthStatus.findUnique({ where });
}

export async function createAixModelHealthStatus(
  data: Prisma.AixModelHealthStatusCreateInput
) {
  return getPrismaClient().aixModelHealthStatus.create({ data });
}

export async function updateAixModelHealthStatus(
  where: Prisma.AixModelHealthStatusWhereUniqueInput,
  data: Prisma.AixModelHealthStatusUpdateInput
) {
  return getPrismaClient().aixModelHealthStatus.update({ where, data });
}

export async function deleteAixModelHealthStatus(
  where: Prisma.AixModelHealthStatusWhereUniqueInput
) {
  return getPrismaClient().aixModelHealthStatus.delete({ where });
}

export async function upsertAixModelHealthStatus(
  where: Prisma.AixModelHealthStatusWhereUniqueInput,
  create: Prisma.AixModelHealthStatusCreateInput,
  update: Prisma.AixModelHealthStatusUpdateInput
) {
  return getPrismaClient().aixModelHealthStatus.upsert({ where, create, update });
}

export type { Prisma };
export type { PrismaConfig, PrismaLifecycleHook };
