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

export async function listAixModelGatewayPolicies(
  args: Prisma.AixModelGatewayPolicyFindManyArgs = {}
) {
  return getPrismaClient().aixModelGatewayPolicy.findMany(args);
}

export async function getAixModelGatewayPolicy(
  where: Prisma.AixModelGatewayPolicyWhereUniqueInput
) {
  return getPrismaClient().aixModelGatewayPolicy.findUnique({ where });
}

export async function createAixModelGatewayPolicy(
  data: Prisma.AixModelGatewayPolicyCreateInput
) {
  return getPrismaClient().aixModelGatewayPolicy.create({ data });
}

export async function updateAixModelGatewayPolicy(
  where: Prisma.AixModelGatewayPolicyWhereUniqueInput,
  data: Prisma.AixModelGatewayPolicyUpdateInput
) {
  return getPrismaClient().aixModelGatewayPolicy.update({ where, data });
}

export async function deleteAixModelGatewayPolicy(
  where: Prisma.AixModelGatewayPolicyWhereUniqueInput
) {
  return getPrismaClient().aixModelGatewayPolicy.delete({ where });
}

export async function upsertAixModelGatewayPolicy(
  where: Prisma.AixModelGatewayPolicyWhereUniqueInput,
  create: Prisma.AixModelGatewayPolicyCreateInput,
  update: Prisma.AixModelGatewayPolicyUpdateInput
) {
  return getPrismaClient().aixModelGatewayPolicy.upsert({ where, create, update });
}

export type { Prisma };
export type { PrismaConfig, PrismaLifecycleHook };
