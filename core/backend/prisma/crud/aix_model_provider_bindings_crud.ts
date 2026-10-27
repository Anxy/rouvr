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

export async function listAixModelProviderBindings(
  args: Prisma.AixModelProviderBindingFindManyArgs = {}
) {
  return getPrismaClient().aixModelProviderBinding.findMany(args);
}

export async function getAixModelProviderBinding(
  where: Prisma.AixModelProviderBindingWhereUniqueInput
) {
  return getPrismaClient().aixModelProviderBinding.findUnique({ where });
}

export async function createAixModelProviderBinding(
  data: Prisma.AixModelProviderBindingCreateInput
) {
  return getPrismaClient().aixModelProviderBinding.create({ data });
}

export async function updateAixModelProviderBinding(
  where: Prisma.AixModelProviderBindingWhereUniqueInput,
  data: Prisma.AixModelProviderBindingUpdateInput
) {
  return getPrismaClient().aixModelProviderBinding.update({ where, data });
}

export async function deleteAixModelProviderBinding(
  where: Prisma.AixModelProviderBindingWhereUniqueInput
) {
  return getPrismaClient().aixModelProviderBinding.delete({ where });
}

export async function upsertAixModelProviderBinding(
  where: Prisma.AixModelProviderBindingWhereUniqueInput,
  create: Prisma.AixModelProviderBindingCreateInput,
  update: Prisma.AixModelProviderBindingUpdateInput
) {
  return getPrismaClient().aixModelProviderBinding.upsert({ where, create, update });
}

export type { Prisma };
export type { PrismaConfig, PrismaLifecycleHook };
