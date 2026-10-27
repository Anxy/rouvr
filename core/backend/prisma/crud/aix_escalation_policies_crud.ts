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

export async function listAixEscalationPolicies(
  args: Prisma.AixEscalationPolicyFindManyArgs = {}
) {
  return getPrismaClient().aixEscalationPolicy.findMany(args);
}

export async function getAixEscalationPolicy(
  where: Prisma.AixEscalationPolicyWhereUniqueInput
) {
  return getPrismaClient().aixEscalationPolicy.findUnique({ where });
}

export async function createAixEscalationPolicy(
  data: Prisma.AixEscalationPolicyCreateInput
) {
  return getPrismaClient().aixEscalationPolicy.create({ data });
}

export async function updateAixEscalationPolicy(
  where: Prisma.AixEscalationPolicyWhereUniqueInput,
  data: Prisma.AixEscalationPolicyUpdateInput
) {
  return getPrismaClient().aixEscalationPolicy.update({ where, data });
}

export async function deleteAixEscalationPolicy(
  where: Prisma.AixEscalationPolicyWhereUniqueInput
) {
  return getPrismaClient().aixEscalationPolicy.delete({ where });
}

export async function upsertAixEscalationPolicy(
  where: Prisma.AixEscalationPolicyWhereUniqueInput,
  create: Prisma.AixEscalationPolicyCreateInput,
  update: Prisma.AixEscalationPolicyUpdateInput
) {
  return getPrismaClient().aixEscalationPolicy.upsert({ where, create, update });
}

export type { Prisma };
export type { PrismaConfig, PrismaLifecycleHook };
