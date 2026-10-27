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

export async function listFeatureFlags(
  args: Prisma.FeatureFlagFindManyArgs = {}
) {
  return getPrismaClient().featureFlag.findMany(args);
}

export async function getFeatureFlag(
  where: Prisma.FeatureFlagWhereUniqueInput
) {
  return getPrismaClient().featureFlag.findUnique({ where });
}

export async function createFeatureFlag(
  data: Prisma.FeatureFlagCreateInput
) {
  return getPrismaClient().featureFlag.create({ data });
}

export async function updateFeatureFlag(
  where: Prisma.FeatureFlagWhereUniqueInput,
  data: Prisma.FeatureFlagUpdateInput
) {
  return getPrismaClient().featureFlag.update({ where, data });
}

export async function deleteFeatureFlag(
  where: Prisma.FeatureFlagWhereUniqueInput
) {
  return getPrismaClient().featureFlag.delete({ where });
}

export async function upsertFeatureFlag(
  where: Prisma.FeatureFlagWhereUniqueInput,
  create: Prisma.FeatureFlagCreateInput,
  update: Prisma.FeatureFlagUpdateInput
) {
  return getPrismaClient().featureFlag.upsert({ where, create, update });
}

export type { Prisma };
export type { PrismaConfig, PrismaLifecycleHook };
