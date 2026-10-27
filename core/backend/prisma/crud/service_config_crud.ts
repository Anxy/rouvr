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

export async function listServiceConfigs(
  args: Prisma.ServiceConfigFindManyArgs = {}
) {
  return getPrismaClient().serviceConfig.findMany(args);
}

export async function getServiceConfig(
  where: Prisma.ServiceConfigWhereUniqueInput
) {
  return getPrismaClient().serviceConfig.findUnique({ where });
}

export async function createServiceConfig(
  data: Prisma.ServiceConfigCreateInput
) {
  return getPrismaClient().serviceConfig.create({ data });
}

export async function updateServiceConfig(
  where: Prisma.ServiceConfigWhereUniqueInput,
  data: Prisma.ServiceConfigUpdateInput
) {
  return getPrismaClient().serviceConfig.update({ where, data });
}

export async function deleteServiceConfig(
  where: Prisma.ServiceConfigWhereUniqueInput
) {
  return getPrismaClient().serviceConfig.delete({ where });
}

export async function upsertServiceConfig(
  where: Prisma.ServiceConfigWhereUniqueInput,
  create: Prisma.ServiceConfigCreateInput,
  update: Prisma.ServiceConfigUpdateInput
) {
  return getPrismaClient().serviceConfig.upsert({ where, create, update });
}

export type { Prisma };
export type { PrismaConfig, PrismaLifecycleHook };
